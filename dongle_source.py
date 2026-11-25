"""WHOOP heart rate through a USB Bluetooth dongle driven directly by Bumble.

This bypasses the operating system's Bluetooth stack entirely: the overlay
talks HCI to a dongle running Zephyr's ``hci_usb`` firmware (USB 2FE3:000B,
e.g. a Nordic nRF52840 dongle). Use it when the PC's built-in Bluetooth is too
weak or flaky to hear the band.

Safety rules match ``WhoopDirectSource``: it only ever connects to the band
whose serial the user configured, and it never pairs/bonds on its own (a WHOOP
keeps a small bond table; a new bond could push the user's phone out).
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Dict, Optional

from hr_source import (
    HR_MEASUREMENT_UUID,
    SCAN_LISTENERS,
    SHARING_SETTINGS,
    WHOOP_CMD,
    WHOOP_NOTIFY,
    HeartRateSource,
    hr_from_whoop_frame,
    looks_like_whoop,
    parse_hr_measurement,
)

DONGLE_TRANSPORT = "usb:2FE3:000B"  # Zephyr hci_usb default VID:PID
OWN_ADDRESS = "C0:FF:EE:48:52:01"   # random static address for the dongle


def dongle_state() -> str:
    """'ready' (plugged in and usable), 'needs_driver' (plugged in, but Windows
    hasn't got WinUSB on it so libusb can't open it), or 'absent'."""
    try:
        import usb1  # from the libusb1 package (a Bumble dependency)

        try:  # exactly Bumble's loader (bundled libusb DLL); usb1 guards re-loads
            from bumble.transport.usb import load_libusb

            load_libusb()
        except Exception:
            pass
        with usb1.USBContext() as ctx:
            for dev in ctx.getDeviceIterator(skip_on_error=True):
                if dev.getVendorID() == 0x2FE3 and dev.getProductID() == 0x000B:
                    try:
                        dev.open().close()
                        return "ready"
                    except Exception:
                        return "needs_driver"
    except Exception:
        return "absent"
    return "absent"


def dongle_present() -> bool:
    return dongle_state() == "ready"


def launch_driver_installer() -> bool:
    """Run the Zadig copy bundled inside the exe (Windows), with our preset."""
    import os
    import shutil
    import sys
    import tempfile

    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(base, "drivers")
    exe = os.path.join(src, "zadig-2.9.exe")
    if sys.platform != "win32" or not os.path.exists(exe):
        return False
    dst = os.path.join(tempfile.gettempdir(), "whoop-hr-overlay-driver")
    os.makedirs(dst, exist_ok=True)
    for name in ("zadig-2.9.exe", "zadig.ini"):
        if os.path.exists(os.path.join(src, name)):
            shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
    os.startfile(os.path.join(dst, "zadig-2.9.exe"))  # Zadig asks for admin itself
    return True


class _AdvCache:
    """Merge advertising + scan-response data per address."""

    def __init__(self) -> None:
        self.rows: Dict[str, dict] = {}

    def add(self, adv) -> dict:
        from bumble.core import AdvertisingData as AD

        key = str(adv.address)
        row = self.rows.setdefault(
            key, {"addr": key, "address": adv.address, "name": "", "uuids": set(),
                  "mfg": set(), "rssi": -999})
        row["address"] = adv.address
        row["rssi"] = adv.rssi
        d = adv.data
        nm = d.get(AD.COMPLETE_LOCAL_NAME) or d.get(AD.SHORTENED_LOCAL_NAME)
        if nm:
            nm = nm if isinstance(nm, str) else bytes(nm).decode(errors="replace")
            row["name"] = nm.replace("\x00", "").strip()  # WHOOP pads its name with NULs
        for t in (AD.COMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS,
                  AD.INCOMPLETE_LIST_OF_16_BIT_SERVICE_CLASS_UUIDS,
                  AD.COMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS,
                  AD.INCOMPLETE_LIST_OF_128_BIT_SERVICE_CLASS_UUIDS):
            for u in d.get(t) or []:
                row["uuids"].add(_full_uuid(u))
        md = d.get(AD.MANUFACTURER_SPECIFIC_DATA)
        if md:
            row["mfg"].add(md[0])
        return row


def _full_uuid(u) -> str:
    s = str(u).lower()
    if len(s) == 4:  # 16-bit -> Bluetooth base UUID
        return f"0000{s}-0000-1000-8000-00805f9b34fb"
    return s


class WhoopDongleSource(HeartRateSource):
    def __init__(
        self,
        on_bpm: Callable[[int], None],
        on_status: Optional[Callable[[str], None]] = None,
        serial: Optional[str] = None,
        transport: str = DONGLE_TRANSPORT,
        configure_band: bool = True,
        scan_seconds: float = 6.0,
        log_path: Optional[str] = None,
    ) -> None:
        super().__init__(on_bpm, on_status, log_path=log_path)
        self._serial = (serial or "").strip()
        self._transport = transport
        self._configure = configure_band
        self._scan_seconds = scan_seconds
        self._seq = 0

    # ------------------------------------------------------------------
    async def _main(self) -> None:
        from bumble.device import Device
        from bumble.transport import open_transport

        while not self._stop.is_set():
            try:
                self._on_status("Opening USB Bluetooth dongle...")
                async with await open_transport(self._transport) as (src, sink):
                    device = Device.with_hci("WHOOP HR Overlay", OWN_ADDRESS, src, sink)
                    await device.power_on()
                    self._log("dongle powered on")
                    while not self._stop.is_set():
                        target = await self._scan(device)
                        if target is None:
                            continue
                        await self._stream(device, target)
            except Exception as exc:
                self._on_status(f"Dongle error: {exc}\nRetrying...")
                self._log(f"dongle error: {exc!r}")
                await asyncio.sleep(3)

    def stop(self) -> None:
        # Let the loop close the USB transport cleanly before the process exits
        # (libusb asserts if its context is torn down mid-transfer).
        super().stop()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)

    async def _scan(self, device):
        cache = _AdvCache()
        device.on("advertisement", cache.add)
        await device.start_scanning(active=True, filter_duplicates=False)
        end = time.monotonic() + self._scan_seconds
        while time.monotonic() < end and not self._stop.is_set():
            await asyncio.sleep(0.25)
        await device.stop_scanning()
        device.remove_listener("advertisement", cache.add)

        rows = sorted(cache.rows.values(), key=lambda r: r["rssi"], reverse=True)
        report = [{"addr": r["addr"], "name": r["name"], "rssi": r["rssi"],
                   "uuids": sorted(r["uuids"]), "mfg": sorted(r["mfg"])} for r in rows]
        for fn in list(SCAN_LISTENERS):
            try:
                fn(report)
            except Exception:
                pass

        whoops = [r for r in rows if looks_like_whoop(r["name"], r["uuids"], r["mfg"])]
        if not self._serial:
            listing = "\n".join(f"- {r['name'] or '(unnamed)'}  ({r['rssi']}dBm)"
                                for r in whoops[:4])
            self._on_status("Hover the overlay, click the gear > Source to pick your band.\n"
                            + (f"WHOOPs in range:\n{listing}" if listing
                               else f"No WHOOP in range ({len(rows)} devices seen)."))
            return None
        for r in whoops:
            if r["name"] and self._serial.lower() in r["name"].lower():
                self._log(f"[dongle] found {r['name']} at {r['rssi']} dBm")
                return r
        self._on_status(f"[dongle] {self._serial} not in range "
                        f"({len(rows)} devices seen). Is the band broadcasting?")
        return None

    # ------------------------------------------------------------------
    async def _stream(self, device, target) -> None:
        import whoop_framing as wf
        from bumble.core import UUID
        from bumble.device import Peer

        label = target["name"] or self._serial
        self._on_status(f"[dongle] Connecting to {label} ({target['rssi']} dBm)...")
        conn = await device.connect(target["address"], timeout=15)
        done = asyncio.Event()
        conn.on("disconnection", lambda *_: done.set())
        peer = Peer(conn)
        await peer.discover_services()
        await peer.discover_characteristics()

        def char(uuid: str):
            found = peer.get_characteristics_by_uuid(UUID(uuid))
            return found[0] if found else None

        pending: dict = {}
        reasm: Dict[str, object] = {}
        got = {"n": 0}

        def emit(bpm: int, src: str) -> None:
            if got["n"] == 0:
                self._log(f"[dongle] first HR {bpm} bpm via {src}")
                self._on_status(f"[dongle] Connected to {label}")
            got["n"] += 1
            self._emit(bpm)

        def on_whoop(uuid: str):
            def handler(value: bytes) -> None:
                r = reasm.setdefault(uuid, wf.Reassembler())
                for frame in r.feed(bytes(value)):
                    if not wf.verify_frame(frame):
                        continue
                    if frame[8] == 36:
                        resp = wf.Response(frame)
                        fut = pending.get((resp.cmd, resp.origin_seq))
                        if fut and not fut.done() and resp.result != 2:
                            fut.set_result(resp)
                        continue
                    bpm = hr_from_whoop_frame(frame)
                    if bpm:
                        emit(bpm, f"WHOOP type {frame[8]}")
            return handler

        # Standard HR (0x2A37) works without pairing -- that's the main path.
        hr = char(HR_MEASUREMENT_UUID)
        if hr is not None:
            await peer.subscribe(
                hr, lambda v: (lambda b: b and emit(b, "0x2A37"))(parse_hr_measurement(bytes(v))))
            self._log("[dongle] subscribed to standard HR 0x2A37")
        # WHOOP's own channels need a bond with the band. Never pair on our own
        # (it could push the user's phone off the band); just skip them.
        cmd = None
        whoop_ok = False
        for u in WHOOP_NOTIFY:
            c = char(u)
            if c is None:
                continue
            try:
                await peer.subscribe(c, on_whoop(u))
                whoop_ok = True
            except Exception as exc:
                self._log(f"[dongle] WHOOP channel {u[:8]} unavailable ({type(exc).__name__}); "
                          "using standard HR only")
                break
        if whoop_ok:
            cmd = char(WHOOP_CMD)
        if hr is None and not whoop_ok:
            self._on_status(f"[dongle] {label} exposes no heart-rate channel\n"
                            "without pairing. Turn on HR broadcast for the band.")
            await conn.disconnect()
            await asyncio.sleep(10)
            return

        async def command(cmd_id: int, body: bytes = b"", timeout: float = 5.0):
            self._seq = (self._seq + 1) & 0xFF
            seq = self._seq
            fut = asyncio.get_running_loop().create_future()
            pending[(cmd_id, seq)] = fut
            try:
                await peer.write_value(cmd, wf.build_command_frame(cmd_id, seq, body),
                                       with_response=True)
                return await asyncio.wait_for(fut, timeout)
            except Exception:
                return None
            finally:
                pending.pop((cmd_id, seq), None)

        def field(text: str) -> bytes:
            return text.encode()[:31].ljust(32, b"\x00")

        if cmd is not None:
            await peer.write_value(cmd, wf.CLIENT_HELLO, with_response=True)
            await asyncio.sleep(0.5)
            if self._configure:
                for set_cmd, read_cmd, key in SHARING_SETTINGS:
                    r = await command(read_cmd, b"\x01" + field(key))
                    cur = None
                    if r is not None and r.result == 1 and len(r.body) >= 65:
                        cur = r.body[33:65].split(b"\x00", 1)[0].decode(errors="replace")
                    self._log(f"[dongle] setting {key} = {cur!r}")
                    if cur != "1":
                        w = await command(set_cmd, b"\x01" + field(key) + field("1"))
                        self._log(f"[dongle] set {key}=1 -> {w.result_name if w else 'timeout'}")
            r = await command(3, b"\x01")
            self._log(f"[dongle] realtime HR on -> {r.result_name if r else 'timeout'}")

        self._on_status(f"[dongle] Connected to {label}. Waiting for heart rate\n"
                        "(band must be on your wrist)")
        last_kick = time.monotonic()
        while not self._stop.is_set() and not done.is_set():
            await asyncio.sleep(0.5)
            if cmd is not None and time.monotonic() - last_kick > 60:
                await command(3, b"\x01")
                last_kick = time.monotonic()
        if not done.is_set():
            await conn.disconnect()
        self._on_status("[dongle] Disconnected, retrying...")
        self._log("[dongle] disconnected")
