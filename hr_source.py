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
from typing import Callable, List, Optional, Tuple

HR_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"


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
    ) -> None:
        super().__init__(on_bpm, on_status)
        self._base = base
        self._interval = interval

    async def _main(self) -> None:
        self._on_status("simulated")
        drift = 0.0
        t = 0.0
        while not self._stop.is_set():
            drift += random.uniform(-0.6, 0.6)
            drift = max(-10.0, min(28.0, drift))
            surge = 14.0 * (math.sin(t / 41.0) ** 2)
            noise = random.uniform(-1.5, 1.5)
            hr = self._base + drift + surge + noise
            hr = max(45.0, min(195.0, hr))
            self._emit(round(hr))
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
                elif "whoop" in nm.lower():
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

        # nothing matched -> surface a hint on the overlay itself
        named = sorted([s for s in seen if s[1] != "(unnamed)"], reverse=True)
        if self._name:
            self._on_status(f"no device named '{self._name}' found")
        elif named:
            self._on_status(f"no HR match. Nearest: {named[0][1]}")
        else:
            self._on_status(f"no HR device found ({len(seen)} nearby)")
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
