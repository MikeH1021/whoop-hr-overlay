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
    """'ready' (plugged in and usable), 'busy' (plugged in, but another process
    has it open), 'needs_driver' (plugged in, but Windows hasn't got WinUSB on
    it so libusb can't open it), or 'absent'."""
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
                    except Exception as exc:
                        return "busy" if dongle_busy(exc) else "needs_driver"
    except Exception:
        return "absent"
    return "absent"


BUSY_STATUS = ("USB dongle is busy\nAnother program (often an old overlay still running) "
               "has it. Unplug the dongle and plug it back in, or end extra "
               "whoop-hr-overlay.exe in Task Manager.")


def dongle_busy(exc: BaseException) -> bool:
    """libusb ACCESS/BUSY: the dongle is open in another process."""
    t = repr(exc).upper()
    return "ERROR_ACCESS" in t or "ERRORACCESS" in t or "ERROR_BUSY" in t or "ERRORBUSY" in t


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


class DongleHub:
    """One USB dongle, opened once for the whole app.

    Opening the dongle (USB claim + HCI reset + power on) takes seconds, so it
    must not happen on every mode switch. The hub owns a single asyncio loop
    thread and the Bumble Device; sources run on that loop one after another,
    and switching only drops the band connection, never the dongle."""

    _inst: Optional["DongleHub"] = None

    @classmethod
    def get(cls) -> "DongleHub":
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def __init__(self, transport: str = DONGLE_TRANSPORT) -> None:
        import threading

        self.transport_name = transport
        self.transport = None
        self.dev = None
        self.keystore_path: Optional[str] = None
        self._current: Optional[asyncio.Task] = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, name="dongle-hub", daemon=True).start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    # -- device ------------------------------------------------------------
    def alive(self) -> bool:
        if self.dev is None or self.transport is None:
            return False
        term = getattr(self.transport.source, "terminated", None)
        return not (term is not None and term.done())

    async def device(self, keystore_path: Optional[str] = None):
        """The powered-on Bumble Device, opening (or re-opening after an
        unplug) only when needed."""
        if keystore_path and keystore_path != self.keystore_path:
            self.keystore_path = keystore_path
            if self.dev is not None:
                self._set_keystore()
        if self.alive():
            return self.dev
        await self.reset()
        from bumble.device import Device
        from bumble.pairing import PairingConfig, PairingDelegate
        from bumble.transport import open_transport

        self.transport = await open_transport(self.transport_name)
        try:
            dev = Device.with_hci("WHOOP HR Overlay", OWN_ADDRESS,
                                  self.transport.source, self.transport.sink)
            dev.pairing_config_factory = lambda _c: PairingConfig(
                sc=True, mitm=False, bonding=True,
                delegate=PairingDelegate(
                    io_capability=PairingDelegate.IoCapability.NO_OUTPUT_NO_INPUT))
            await dev.power_on()
        except BaseException:
            await self.reset()
            raise
        self.dev = dev
        self._set_keystore()
        return dev

    def _set_keystore(self) -> None:
        if self.dev is not None and self.keystore_path:
            from bumble.keys import JsonKeyStore

            self.dev.keystore = JsonKeyStore(namespace=OWN_ADDRESS,
                                             filename=self.keystore_path)

    async def reset(self) -> None:
        self.dev = None
        t, self.transport = self.transport, None
        if t is not None:
            try:
                await asyncio.wait_for(t.close(), 3)
            except BaseException:
                pass

    async def release(self) -> None:
        """Drop whatever the previous source left: scanning and connections.
        The dongle itself stays open."""
        dev = self.dev
        if dev is None or not self.alive():
            return
        try:
            if getattr(dev, "is_scanning", False):
                await asyncio.wait_for(dev.stop_scanning(), 2)
        except BaseException:
            pass
        for conn in list(getattr(dev, "connections", {}).values()):
            try:
                await asyncio.wait_for(conn.disconnect(), 3)
            except BaseException:
                pass

    # -- running sources -----------------------------------------------------
    def run(self, coro_fn):
        """Run a source's coroutine after the previous one has fully finished
        (so its cleanup can't race the new source). Returns (future, task-box)."""
        box: dict = {}

        async def runner():
            prev, self._current = self._current, asyncio.current_task()
            box["task"] = self._current
            if prev is not None and not prev.done():
                await asyncio.wait([prev])
            await self.release()
            await coro_fn()

        return asyncio.run_coroutine_threadsafe(runner(), self.loop), box

    def shutdown(self, timeout: float = 4.0) -> None:
        """App exit: let the running source finish its cleanup, then close USB."""
        async def fin():
            cur = self._current
            if cur is not None and not cur.done():
                await asyncio.wait([cur], timeout=timeout)
            await self.release()
            await self.reset()

        try:
            asyncio.run_coroutine_threadsafe(fin(), self.loop).result(timeout + 4)
        except BaseException:
            pass


class HubSource:
    """Mixin (put before HeartRateSource): run ``_main`` on the shared dongle
    hub instead of a private thread + transport."""

    STOP_GRACE_S = 3.0   # time to restore the band before we cancel

    def start(self) -> None:
        hub = DongleHub.get()
        self._loop = hub.loop

        async def body():
            try:
                await self._main()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self._on_status(f"error: {exc}")
                self._log(f"FATAL {exc!r}")

        self._hub_future, self._hub_box = hub.run(body)

    def stop(self) -> None:
        """Non-blocking: ask the loop to wind down; cancel it if it dawdles."""
        self._stop.set()
        loop, box = self._loop, getattr(self, "_hub_box", None)
        if loop is None or box is None:
            return

        def cancel():
            t = box.get("task")
            if t is not None and not t.done():
                t.cancel()

        loop.call_soon_threadsafe(lambda: loop.call_later(self.STOP_GRACE_S, cancel))

    def wait(self, timeout: float = 5.0) -> None:
        f = getattr(self, "_hub_future", None)
        if f is not None:
            try:
                f.result(timeout)
            except BaseException:
                pass


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


class WhoopDongleSource(HubSource, HeartRateSource):
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
        hub = DongleHub.get()
        while not self._stop.is_set():
            try:
                if not hub.alive():
                    self._on_status("Opening USB Bluetooth dongle...")
                device = await hub.device()
                while not self._stop.is_set():
                    target = await self._scan(device)
                    if target is None:
                        continue
                    await self._stream(device, target)
            except Exception as exc:
                self._on_status(BUSY_STATUS if dongle_busy(exc)
                                else f"Dongle error: {exc}\nRetrying...")
                self._log(f"dongle error: {exc!r}")
                await asyncio.sleep(3)

    async def _scan(self, device):
        cache = _AdvCache()
        device.on("advertisement", cache.add)
        try:
            await device.start_scanning(active=True, filter_duplicates=False)
            end = time.monotonic() + self._scan_seconds
            while time.monotonic() < end and not self._stop.is_set():
                await asyncio.sleep(0.25)
            await device.stop_scanning()
        finally:
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
            self._on_status("Choose your band\nHover here and click the gear, then Source.\n"
                            + (f"In range:\n{listing}" if listing
                               else f"No WHOOP in range yet ({len(rows)} devices seen)."))
            return None
        for r in whoops:
            if r["name"] and self._serial.lower() in r["name"].lower():
                self._log(f"[dongle] found {r['name']} at {r['rssi']} dBm")
                return r
        self._on_status(f"Looking for {self._serial}\nNot heard yet ({len(rows)} devices "
                        "seen). Is it charged and nearby?")
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
