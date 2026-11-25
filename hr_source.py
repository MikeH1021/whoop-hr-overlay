"""Heart-rate data sources for the WHOOP HR overlay.

Two sources share one small interface:

  * ``BleHeartRateSource`` reads the **standard** Bluetooth LE Heart Rate
    Profile -- service ``0x180D``, measurement characteristic ``0x2A37`` --
    over ``bleak``. A WHOOP 5.0 / MG that is broadcasting its heart rate shows
    up here exactly like a Polar / Garmin / Coospo strap, so the overlay needs
    no reverse-engineered WHOOP protocol to run. Any standard BLE HR strap
    works too.

  * ``SimulatedHeartRateSource`` produces a believable fake signal so the
    overlay can be built, styled and demoed with no hardware connected.

Each source runs its own asyncio loop on a daemon thread and calls the
``on_bpm(int)`` / ``on_status(str)`` callbacks **from that thread**. The GUI
marshals those onto the Qt thread with signals, so the callbacks must stay
cheap and must not touch widgets directly.
"""
from __future__ import annotations

import asyncio
import datetime
import math
import os
import random
import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

HR_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

WHOOP_COMPANY_ID = 816  # manufacturer-data id seen on WHOOP 5/MG adverts


def looks_like_whoop(name: str, uuids, mfg_ids) -> bool:
    """A WHOOP can be renamed (e.g. "W MGB0663331"), so never rely on the word
    "whoop": check the WHOOP service, the manufacturer id, or a serial-shaped
    last word (MGB..., 5AM..., 5B0...)."""
    if "fd4b0001-cce1-4033-93ce-002d5875f58a" in uuids or WHOOP_COMPANY_ID in mfg_ids:
        return True
    n = (name or "").strip()
    last = n.split()[-1] if n else ""
    return "whoop" in n.lower() or (
        len(last) == 10 and last[:3].isalnum() and last[-6:].isdigit()
        and last[0] in "M5"
    )


# Every scan is published here as a list of dicts (addr, name, rssi, uuids,
# mfg). main.py hooks it for the tray band-picker and the LAN diagnostics
# beacon. Names are cached per address because Windows often delivers the
# name in a later packet than the first one it reports.
SCAN_LISTENERS: list = []
LOG_LISTENERS: list = []   # every source log line (main.py forwards to the LAN beacon)
NAME_CACHE: dict = {}


async def scan_devices(timeout: float):
    """Active BLE scan. Returns [(dev, adv, name, uuids, rssi)], strongest first."""
    from bleak import BleakScanner

    found = await BleakScanner.discover(
        timeout=timeout, return_adv=True, scanning_mode="active"
    )
    rows = []
    for addr, (dev, adv) in found.items():
        nm = adv.local_name or dev.name or ""
        if nm and nm != str(addr):
            NAME_CACHE[str(addr)] = nm
        nm = NAME_CACHE.get(str(addr), "")
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        rssi = adv.rssi if adv.rssi is not None else -999
        rows.append((dev, adv, nm, uuids, rssi))
    rows.sort(key=lambda r: r[4], reverse=True)
    report = [
        {
            "addr": str(getattr(d, "address", "")),
            "name": nm,
            "rssi": rssi,
            "uuids": uuids,
            "mfg": sorted((adv.manufacturer_data or {}).keys()),
        }
        for d, adv, nm, uuids, rssi in rows
    ]
    for fn in list(SCAN_LISTENERS):
        try:
            fn(report)
        except Exception:
            pass
    return rows


def default_log_path() -> Optional[str]:
    """A log file next to the exe (frozen) or this script (source)."""
    try:
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, "whoop-overlay.log")
    except Exception:
        return None


def parse_hr_measurement(data: bytes) -> Optional[int]:
    """Decode a ``0x2A37`` Heart Rate Measurement value into bpm.

    Per the BLE spec the first byte is a flags field; bit 0 selects an 8-bit
    (0) or 16-bit (1) heart-rate value that follows. Returns ``None`` if the
    packet is too short to contain a value.
    """
    if not data:
        return None
    flags = data[0]
    if flags & 0x01:  # 16-bit heart-rate value
        if len(data) < 3:
            return None
        return int.from_bytes(data[1:3], "little")
    if len(data) < 2:
        return None
    return data[1]


class HeartRateSource:
    """Base class: owns a background asyncio loop and the lifecycle."""

    def __init__(
        self,
        on_bpm: Callable[[int], None],
        on_status: Optional[Callable[[str], None]] = None,
        log_path: Optional[str] = None,
    ) -> None:
        self._on_bpm = on_bpm
        self._on_status = on_status or (lambda _s: None)
        self._log_path = log_path
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = threading.Event()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="hr-source", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(lambda: None)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            self._on_status(f"error: {exc}")
            self._log(f"FATAL {exc!r}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------
    def _emit(self, bpm: Optional[int]) -> None:
        if bpm and bpm > 0:
            self._on_bpm(int(bpm))

    def _log(self, msg: str) -> None:
        for fn in list(LOG_LISTENERS):
            try:
                fn(msg)
            except Exception:
                pass
        if not self._log_path:
            return
        try:
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
        except Exception:
            pass


class SimulatedHeartRateSource(HeartRateSource):
    """A believable fake HR signal: a slow wander with occasional surges."""

    def __init__(
        self,
        on_bpm: Callable[[int], None],
        on_status: Optional[Callable[[str], None]] = None,
        base: int = 72,
        interval: float = 1.0,
        on_pulse: Optional[Callable[[list, float], None]] = None,
        on_extras: Optional[Callable[[dict], None]] = None,
    ) -> None:
        super().__init__(on_bpm, on_status)
        self._base = base
        self._interval = interval
        self._on_pulse = on_pulse      # set => preview Active mode (fake PPG + HRV)
        self._on_extras = on_extras

    async def _main(self) -> None:
        self._on_status("simulated")
        drift = 0.0
        t = 0.0
        phase = 0.0
        rate = 25.0
        while not self._stop.is_set():
            drift += random.uniform(-0.6, 0.6)
            drift = max(-10.0, min(28.0, drift))
            surge = 14.0 * (math.sin(t / 41.0) ** 2)
            noise = random.uniform(-1.5, 1.5)
            hr = self._base + drift + surge + noise
            hr = max(45.0, min(195.0, hr))
            self._emit(round(hr))
            if self._on_pulse:
                from overlay import pulse_template

                samples = []
                for _ in range(int(rate * self._interval)):
                    phase += hr / 60.0 / rate
                    samples.append(pulse_template(phase) + random.uniform(-0.03, 0.03))
                self._on_pulse(samples, rate)
            if self._on_extras:
                self._on_extras({"HRV": f"{random.randint(38, 62)} ms", "motion": "still"})
            t += self._interval
            await asyncio.sleep(self._interval)


class BleHeartRateSource(HeartRateSource):
    """Read live bpm from a standard BLE Heart Rate device via ``bleak``.

    Discovers by explicit ``address``, by ``name`` substring, or -- if neither
    is given -- by picking the strongest advertiser that exposes the Heart Rate
    service. Reconnects automatically if the link drops, and (when a log path is
    set) records every scan and connection event for troubleshooting.
    """

    def __init__(
        self,
        on_bpm: Callable[[int], None],
        on_status: Optional[Callable[[str], None]] = None,
        address: Optional[str] = None,
        name: Optional[str] = None,
        scan_timeout: float = 8.0,
        log_path: Optional[str] = None,
    ) -> None:
        super().__init__(on_bpm, on_status, log_path=log_path)
        self._address = address
        self._name = name
        self._scan_timeout = scan_timeout

    async def _discover(self):
        from bleak import BleakScanner

        target = self._address or self._name or "(any HR device)"
        self._on_status("scanning for heart-rate device...")
        self._log(f"--- scan start (target: {target}) ---")
        found = await BleakScanner.discover(
            timeout=self._scan_timeout, return_adv=True
        )

        hr_candidates = []       # devices advertising the HR service (preferred)
        whoop_candidates = []    # devices merely named "WHOOP*" (fallback)
        seen: List[Tuple[int, str, str, bool]] = []
        for addr, (dev, adv) in found.items():
            nm = adv.local_name or dev.name or ""
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            has_hr = HR_SERVICE_UUID in uuids
            rssi = adv.rssi if adv.rssi is not None else -999
            seen.append((rssi, nm or "(unnamed)", str(addr), has_hr))
            if self._address and str(addr).lower() == self._address.lower():
                self._log(f"matched by address: {addr}")
                return dev
            if self._name and nm and self._name.lower() in nm.lower():
                self._log(f"matched by name: {nm} ({addr})")
                return dev
            if not self._address and not self._name:
                if has_hr:
                    hr_candidates.append((rssi, dev))
                elif looks_like_whoop(nm, uuids, list((adv.manufacturer_data or {}).keys())):
                    whoop_candidates.append((rssi, dev))

        # log everything we saw this pass, strongest signal first
        for rssi, nm, addr, has_hr in sorted(seen, reverse=True):
            self._log(f"  {rssi:>4} dBm  {addr}  {nm}"
                      f"{'  [Heart Rate service]' if has_hr else ''}")
        self._log(f"--- scan end: {len(seen)} device(s) ---")

        # prefer a real HR broadcaster; otherwise fall back to the nearest
        # WHOOP by name (it may only expose HR after you connect + wear it)
        if hr_candidates:
            hr_candidates.sort(key=lambda c: c[0], reverse=True)
            return hr_candidates[0][1]
        if whoop_candidates:
            whoop_candidates.sort(key=lambda c: c[0], reverse=True)
            self._log("no HR advertiser; trying nearest WHOOP by name")
            return whoop_candidates[0][1]

        # nothing matched -> show the nearest devices right on the overlay so
        # the user can see whether the WHOOP is even broadcasting (no log file)
        top = sorted(seen, reverse=True)[:4]
        lines = [
            f"- {nm}  ({rssi}dBm){'  HR' if hr else ''}"
            for rssi, nm, addr, hr in top
        ]
        listing = "\n".join(lines) if lines else "(no BLE devices seen)"
        if self._name:
            header = f"No '{self._name}' nearby. Seeing:"
        else:
            header = f"No WHOOP broadcast found. {len(seen)} devices near:"
        self._on_status(header + "\n" + listing)
        return None

    async def _main(self) -> None:
        while not self._stop.is_set():
            try:
                device = await self._discover()
                if device is None:
                    await asyncio.sleep(2)
                    continue
                await self._stream(device)
            except Exception as exc:
                self._on_status(f"reconnecting ({exc})")
                self._log(f"discover/stream error: {exc!r}")
                await asyncio.sleep(2)

    async def _stream(self, device) -> None:
        from bleak import BleakClient

        label = getattr(device, "name", None) or getattr(device, "address", "device")
        self._on_status(f"connecting to {label}...")
        self._log(f"connecting to {label} ({getattr(device, 'address', '?')})")

        first = {"ok": False}

        def handler(_char, data: bytearray) -> None:
            raw = bytes(data)
            bpm = parse_hr_measurement(raw)
            if bpm:
                if not first["ok"]:
                    self._log(f"first HR notify: {raw.hex()} -> {bpm} bpm")
                    first["ok"] = True
                self._emit(bpm)
            else:
                self._log(f"HR notify could not be parsed: {raw.hex()}")

        async with BleakClient(device) as client:
            self._on_status(f"connected: {label}")
            self._log(f"connected: {label}")
            try:
                has_char = any(
                    c.uuid.lower() == HR_MEASUREMENT_UUID
                    for s in client.services
                    for c in s.characteristics
                )
                self._log(f"HR measurement characteristic present: {has_char}")
                if not has_char:
                    self._on_status("connected, but no HR data channel")
            except Exception as exc:
                self._log(f"service enumeration error: {exc!r}")

            await client.start_notify(HR_MEASUREMENT_UUID, handler)
            while not self._stop.is_set() and client.is_connected:
                await asyncio.sleep(0.5)
            try:
                await client.stop_notify(HR_MEASUREMENT_UUID)
            except Exception:
                pass
        self._on_status("disconnected")
        self._log("disconnected")


WHOOP_SVC = "fd4b0001-cce1-4033-93ce-002d5875f58a"
WHOOP_CMD = "fd4b0002-cce1-4033-93ce-002d5875f58a"
WHOOP_NOTIFY = [f"fd4b000{n}-cce1-4033-93ce-002d5875f58a" for n in (3, 4, 5, 7)]

# Band settings that make it share HR with a second device while it stays
# connected to its phone: (set_cmd, read_cmd, key)
SHARING_SETTINGS = (
    (120, 128, "make_hrfm_visible"),         # feature flag: expose standard HR service
    (119, 121, "whoop_live_2_hrm_devices"),  # device config: allow a 2nd HR connection
)


def hr_from_whoop_frame(frame: bytes) -> Optional[int]:
    """Live bpm from a verified WHOOP 5/MG frame, or None.

    * packet type 40 (live HR + R-R): bpm at byte 16
    * R18 biometric summary (layout byte 9 == 18, 124 bytes): bpm at byte 22
    """
    if len(frame) < 20:
        return None
    if frame[8] == 40:
        bpm = frame[16]
    elif frame[9] == 18 and len(frame) >= 124 and frame[8] != 36:
        bpm = frame[22]
    else:
        return None
    return bpm if 25 <= bpm <= 230 else None


class WhoopDirectSource(HeartRateSource):
    """Talk to a WHOOP 5/MG band directly over its own protocol.

    On connect it: subscribes to the standard 0x2A37 channel (if present) and the
    WHOOP notify channels, sends the client hello, optionally turns on the band's
    HR-sharing settings (so it keeps sharing HR alongside its phone afterwards),
    and switches on realtime HR (command 3). HR is taken from whichever channel
    produces it.
    """

    def __init__(
        self,
        on_bpm: Callable[[int], None],
        on_status: Optional[Callable[[str], None]] = None,
        serial: Optional[str] = None,
        scan_timeout: float = 6.0,
        configure_band: bool = True,
        log_path: Optional[str] = None,
    ) -> None:
        super().__init__(on_bpm, on_status, log_path=log_path)
        self._serial = (serial or "").strip()
        self._scan_timeout = scan_timeout
        self._configure = configure_band
        self._seq = 0
        self._announced = False

    # -- discovery -----------------------------------------------------------
    async def _discover(self):
        want = self._serial
        if not self._announced:
            self._on_status(f"Looking for WHOOP {want}..." if want
                            else "Scanning for WHOOPs...")
            self._announced = True
        rows = await scan_devices(self._scan_timeout)
        if not want:
            # No band chosen yet: list the WHOOPs in range. Never auto-connect.
            whoops = [(r, n or "(unnamed WHOOP)") for _d, a, n, u, r in rows
                      if looks_like_whoop(n, u, list((a.manufacturer_data or {}).keys()))]
            listing = "\n".join(f"- {n}  ({r}dBm)" for r, n in whoops[:4])
            self._on_status(
                "Hover the overlay, click the gear > Source to pick your band.\n"
                + (f"WHOOPs in range:\n{listing}" if listing else
                   f"No WHOOP broadcasting near this PC ({len(rows)} devices seen).")
            )
            return None
        cands, seen = [], []
        for dev, adv, nm, uuids, rssi in rows:
            addr = getattr(dev, "address", "")
            seen.append((rssi, nm or "(unnamed)", str(addr), HR_SERVICE_UUID in uuids))
            is_whoop = looks_like_whoop(nm, uuids, list((adv.manufacturer_data or {}).keys()))
            # Only ever connect to the band the user configured -- never a
            # neighbour's WHOOP that happens to be in range.
            if not is_whoop or not self._serial or self._serial.lower() not in nm.lower():
                continue
            cands.append((rssi, nm, dev))
        for rssi, nm, addr, hr in sorted(seen, reverse=True):
            self._log(f"  {rssi:>4} dBm  {addr}  {nm}{'  [HR]' if hr else ''}")
        if cands:
            cands.sort(key=lambda c: c[0], reverse=True)
            rssi = cands[0][0]
            self._log(f"picked {cands[0][1]} ({rssi} dBm)")
            if rssi < -88:
                self._on_status(
                    f"Found {cands[0][1]} but signal is very weak ({rssi} dBm).\n"
                    "PC Bluetooth reception problem: check the antenna\n"
                    "or use a USB Bluetooth adapter. Trying anyway...")
            return cands[0][2]
        top = sorted(seen, reverse=True)[:4]
        listing = "\n".join(f"- {n}  ({r}dBm)" for r, n, _a, _h in top) or "(nothing)"
        self._on_status(
            f"{want} not broadcasting (connected to your phone?). Seeing:\n{listing}"
        )
        return None

    # -- protocol helpers ----------------------------------------------------
    async def _command(self, client, pending, cmd: int, body: bytes = b"",
                       timeout: float = 5.0):
        import whoop_framing as wf

        self._seq = (self._seq + 1) & 0xFF
        seq = self._seq
        fut = asyncio.get_running_loop().create_future()
        pending[(cmd, seq)] = fut
        try:
            await client.write_gatt_char(
                WHOOP_CMD, wf.build_command_frame(cmd, seq, body), response=True
            )
            return await asyncio.wait_for(fut, timeout)
        except Exception:
            return None
        finally:
            pending.pop((cmd, seq), None)

    @staticmethod
    def _field(text: str) -> bytes:
        return text.encode()[:31].ljust(32, b"\x00")

    async def _configure_sharing(self, client, pending) -> None:
        for set_cmd, read_cmd, key in SHARING_SETTINGS:
            r = await self._command(client, pending, read_cmd, b"\x01" + self._field(key))
            cur = None
            if r is not None and r.result == 1 and len(r.body) >= 65:
                cur = r.body[33:65].split(b"\x00", 1)[0].decode(errors="replace")
            self._log(f"setting {key} = {cur!r}")
            if cur == "1":
                continue
            w = await self._command(
                client, pending, set_cmd, b"\x01" + self._field(key) + self._field("1")
            )
            self._log(f"set {key}=1 -> {w.result_name if w else 'timeout'}")

    # -- main loop -----------------------------------------------------------
    async def _main(self) -> None:
        while not self._stop.is_set():
            try:
                device = await self._discover()
                if device is None:
                    await asyncio.sleep(2)
                    continue
                await self._stream(device)
            except Exception as exc:
                self._on_status(f"reconnecting ({exc})")
                self._log(f"error: {exc!r}")
                await asyncio.sleep(2)

    async def _stream(self, device) -> None:
        from bleak import BleakClient
        import whoop_framing as wf

        label = getattr(device, "name", None) or "WHOOP"
        self._on_status(f"Connecting to {label}...")
        self._log(f"connecting to {label}")
        pending: dict = {}
        reasm = {u: wf.Reassembler() for u in WHOOP_NOTIFY}
        got = {"n": 0}

        def emit(bpm: int, src: str) -> None:
            if got["n"] == 0:
                self._log(f"first HR {bpm} bpm via {src}")
            got["n"] += 1
            self._emit(bpm)

        def on_std(_c, data: bytearray) -> None:
            bpm = parse_hr_measurement(bytes(data))
            if bpm:
                emit(bpm, "0x2A37")

        def on_whoop(char, data: bytearray) -> None:
            uuid = getattr(char, "uuid", str(char)).lower()
            r = reasm.setdefault(uuid, wf.Reassembler())
            for frame in r.feed(bytes(data)):
                if not wf.verify_frame(frame):
                    continue
                if frame[8] == 36:  # command response
                    resp = wf.Response(frame)
                    fut = pending.get((resp.cmd, resp.origin_seq))
                    if fut and not fut.done() and resp.result != 2:
                        fut.set_result(resp)
                    continue
                bpm = hr_from_whoop_frame(frame)
                if bpm:
                    emit(bpm, f"WHOOP type {frame[8]}")

        async with BleakClient(device, timeout=20.0) as client:
            uuids = {c.uuid.lower() for s in client.services for c in s.characteristics}
            if HR_MEASUREMENT_UUID in uuids:
                await client.start_notify(HR_MEASUREMENT_UUID, on_std)
            # WHOOP's own channels need a bond; standard HR doesn't. Skip them
            # if the band refuses rather than failing the whole connection.
            whoop_ok = False
            for u in WHOOP_NOTIFY:
                if u in uuids:
                    try:
                        await client.start_notify(u, on_whoop)
                        whoop_ok = True
                    except Exception as exc:
                        self._log(f"WHOOP channel {u[:8]} unavailable ({exc!r}); "
                                  "using standard HR only")
                        break
            if whoop_ok and WHOOP_CMD in uuids:
                await client.write_gatt_char(WHOOP_CMD, wf.CLIENT_HELLO, response=True)
                await asyncio.sleep(0.5)
                if self._configure:
                    await self._configure_sharing(client, pending)
                r = await self._command(client, pending, 3, b"\x01")
                self._log(f"realtime HR on -> {r.result_name if r else 'timeout'}")
            self._on_status(f"Connected to {label}. Waiting for heart rate\n"
                            "(band must be on your wrist)")
            last_kick = time.monotonic()
            while not self._stop.is_set() and client.is_connected:
                await asyncio.sleep(0.5)
                if whoop_ok and WHOOP_CMD in uuids and time.monotonic() - last_kick > 60:
                    await self._command(client, pending, 3, b"\x01")
                    last_kick = time.monotonic()
        self._on_status("Disconnected, retrying...")
        self._log("disconnected")


async def _scan_list(timeout: float) -> List[Tuple[int, str, str, bool]]:
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    rows: List[Tuple[int, str, str, bool]] = []
    for addr, (dev, adv) in found.items():
        nm = adv.local_name or dev.name or "?"
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        rssi = adv.rssi if adv.rssi is not None else -999
        rows.append((rssi, str(addr), nm, HR_SERVICE_UUID in uuids))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


def list_devices(timeout: float = 8.0) -> List[Tuple[int, str, str, bool]]:
    """Blocking scan used by ``main.py --list``."""
    return asyncio.run(_scan_list(timeout))
