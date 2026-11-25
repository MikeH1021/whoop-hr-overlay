"""Active mode: a dedicated WHOOP 5/MG paired to this PC, streaming everything.

Unlike Passive mode (standard HR only, no pairing), Active mode bonds with a
spare band so WHOOP's own data channels open up, then turns on the band's live
sensor streams (the same unlock sequence proven in the RE toolkit):

  * realtime HR (cmd 3)            -> packet type 40: bpm + beat-to-beat R-R
  * raw optical (cmd 81 + 108)     -> type 43, 2140 B: PPG, 25 samples/frame
  * raw IMU     (cmd 81 + 106)     -> type 43, 1244 B: 100 Hz accel/gyro

and emits bpm, a detrended PPG waveform, and extras (HRV, motion) to the
overlay. On disconnect/quit it stops the raw streams again.

Works over the USB dongle (Bumble; bond keys kept in a file next to the exe)
or the computer's own Bluetooth (bleak; the OS handles pairing).
"""
from __future__ import annotations

import asyncio
import collections
import math
import os
import struct
import time
from typing import Callable, Optional

from hr_source import (
    HR_MEASUREMENT_UUID,
    WHOOP_CMD,
    WHOOP_NOTIFY,
    HeartRateSource,
    looks_like_whoop,
    parse_hr_measurement,
    scan_devices,
)

RAIL = 524287  # +-2^19: optical ADC saturated (band off the wrist)

UNLOCK_FF = (("enable_r22_packets", "1"), ("enable_r22_v9_packets", "1"),
             ("enable_r22_v3_packets", "1"), ("disable_pip_r26_packets", "0"))
UNLOCK_CFG = (("cont_collection_mode", "1"), ("enable_raw_data_w_ecg", "1"))
LIVE_ON = ((81, "START_RAW_DATA"), (108, "optical live"), (106, "IMU live"))


# ---------------------------------------------------------------- decoders --
def pairing_refused(exc: BaseException) -> bool:
    """True when the band itself said no to pairing (SMP 0x05 PAIRING_NOT_SUPPORTED,
    or a Windows/bleak equivalent): it still holds a bond with another device, so
    retrying is pointless until that bond is cleared."""
    text = f"{exc!r} {exc}".upper().replace(" ", "_")
    return any(k in text for k in ("PAIRING_NOT_SUPPORTED", "PAIRING_NOT_ALLOWED",
                                   "REJECTEDBYHANDLER", "PAIRINGNOTSUPPORTED"))


def ppg_from_frame(f: bytes) -> Optional[list]:
    """R20 optical frame (type 43, 2140 B) -> block-0 slot-A samples (int)."""
    if len(f) != 2140 or f[8] != 43:
        return None
    base = 26
    n = min(f[base], 50)
    return [struct.unpack_from("<i", f, base + 21 + 4 * i)[0] for i in range(n)]


def motion_from_frame(f: bytes) -> Optional[float]:
    """IMU frame (type 43, 1244 B) -> std-dev of |accel| in g over 1 s."""
    if len(f) != 1244 or f[8] != 43:
        return None
    acc = 1.0 / 4096.0
    mags = []
    for i in range(100):
        o = 2 * i
        ax = struct.unpack_from("<h", f, 28 + o)[0] * acc
        ay = struct.unpack_from("<h", f, 228 + o)[0] * acc
        az = struct.unpack_from("<h", f, 428 + o)[0] * acc
        mags.append(math.sqrt(ax * ax + ay * ay + az * az))
    m = sum(mags) / len(mags)
    return math.sqrt(sum((x - m) ** 2 for x in mags) / len(mags))


def rr_from_frame(f: bytes) -> Optional[tuple]:
    """Packet type 40 -> (bpm, [rr_ms, ...])."""
    if len(f) < 20 or f[8] != 40:
        return None
    bpm, count = f[16], f[17]
    rr = []
    for i in range(count):
        o = 18 + 2 * i
        if o + 2 > len(f) - 4:
            break
        ticks = f[o] | (f[o + 1] << 8)
        if ticks:
            rr.append((ticks * 1000 + 512) // 1024)
    return bpm, rr


class PpgProcessor:
    """Invert (more blood = less light), remove the slow baseline, smooth
    lightly, and spot beats (for HRV when the band sends no R-R)."""

    def __init__(self, rate: float = 25.0) -> None:
        self.rate = rate
        self.base = None
        self.y = None
        self.prev = [0.0, 0.0]
        self.peak_level = 0.0
        self.last_beat = None
        self.t = 0.0

    def feed(self, raw: list) -> tuple:
        out, beats = [], []
        for x in raw:
            v = -float(x)
            self.base = v if self.base is None else self.base + 0.05 * (v - self.base)
            d = v - self.base
            self.y = d if self.y is None else 0.55 * self.y + 0.45 * d
            out.append(self.y)
            # local max in the previous sample, above an adaptive threshold
            a, b = self.prev
            self.peak_level = max(abs(self.y), self.peak_level * 0.995)
            if b > a and b > self.y and b > 0.35 * self.peak_level:
                if self.last_beat is None or self.t - self.last_beat > 0.33:
                    if self.last_beat is not None:
                        beats.append(int((self.t - self.last_beat) * 1000))
                    self.last_beat = self.t
            self.prev = [b, self.y]
            self.t += 1.0 / self.rate
        return out, beats


def rmssd(rr: list) -> Optional[float]:
    good = [r for r in rr if 300 <= r <= 2000]
    diffs = [b - a for a, b in zip(good, good[1:]) if abs(b - a) < 300]
    if len(diffs) < 5:
        return None
    return math.sqrt(sum(d * d for d in diffs) / len(diffs))


def motion_label(sd: float) -> str:
    return "still" if sd < 0.02 else "light" if sd < 0.12 else "active"


class ActiveDecoder:
    """Turns raw BLE notifications from a WHOOP into overlay data: reassembles
    and CRC-checks frames, resolves command responses, and decodes live HR /
    R-R (type 40), optical PPG and IMU motion (type 43). Transport-free, so it
    can be fed synthetic frames in tests."""

    def __init__(self, emit_bpm: Callable[[int], None], emit_pulse: Callable[[list, float], None],
                 log: Callable[[str], None] = lambda _m: None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.emit_bpm = emit_bpm
        self.emit_pulse = emit_pulse
        self.log = log
        self.clock = clock
        self.pending: dict = {}            # (cmd, seq) -> future
        self.reasm: dict = {}
        self.ppg = PpgProcessor()
        self.rr: collections.deque = collections.deque(maxlen=120)
        self.t40 = -1e9                    # last type-40 frame
        self.rr_from_band = -1e9           # last R-R from the band itself
        self.motion: Optional[float] = None
        self.railed = False
        self.ppg_frames = 0
        self.frames = 0
        self.bad_frames = 0
        self._first_hr = True

    def _bpm(self, b: int, src: str) -> None:
        if self._first_hr:
            self.log(f"[active] first HR {b} via {src}")
            self._first_hr = False
        self.emit_bpm(b)

    def on_std(self, data: bytes) -> None:
        """Standard 0x2A37 notification; ignored while type-40 frames flow."""
        bpm = parse_hr_measurement(data)
        if bpm and self.clock() - self.t40 > 3:
            self._bpm(bpm, "0x2A37")

    def feed(self, uuid: str, value: bytes) -> None:
        """One BLE notification fragment from a WHOOP notify characteristic."""
        import whoop_framing as wf

        r = self.reasm.setdefault(uuid, wf.Reassembler())
        for frame in r.feed(bytes(value)):
            if wf.verify_frame(frame):
                self.frames += 1
                self.on_frame(frame)
            else:
                self.bad_frames += 1

    def on_frame(self, f: bytes) -> None:
        import whoop_framing as wf

        if f[8] == 36:
            resp = wf.Response(f)
            fut = self.pending.get((resp.cmd, resp.origin_seq))
            if fut and not fut.done() and resp.result != 2:
                fut.set_result(resp)
            return
        r = rr_from_frame(f)
        if r:
            bpm, rr = r
            self.t40 = self.clock()
            if 25 <= bpm <= 230:
                self._bpm(bpm, "type 40")
            if rr:
                self.rr_from_band = self.clock()
                self.rr.extend(rr)
            return
        raw = ppg_from_frame(f)
        if raw is not None:
            self.ppg_frames += 1
            self.railed = sum(1 for x in raw if abs(x) >= RAIL) > len(raw) * 0.5
            if not self.railed:
                wave, beats = self.ppg.feed(raw)
                self.emit_pulse(wave, self.ppg.rate)
                if self.clock() - self.rr_from_band > 10:
                    self.rr.extend(beats)          # PPG-derived fallback
            return
        m = motion_from_frame(f)
        if m is not None:
            self.motion = m

    def extras(self) -> dict:
        out = {}
        hrv = rmssd(list(self.rr)[-60:])
        if hrv is not None:
            out["HRV"] = f"{hrv:.0f} ms"
        if self.motion is not None:
            out["motion"] = motion_label(self.motion)
        if self.railed:
            out["PPG"] = "off wrist?"
        return out


# -------------------------------------------------------------- transports --
class _BleakLink:
    """The computer's own Bluetooth (Windows WinRT / macOS CoreBluetooth)."""

    def __init__(self, log) -> None:
        self.log = log
        self.client = None
        self.disconnected = asyncio.Event()

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def scan(self, seconds: float):
        rows = await scan_devices(seconds)
        return [{"name": n, "rssi": r, "uuids": u, "mfg": list((a.manufacturer_data or {}).keys()),
                 "dev": d} for d, a, n, u, r in rows]

    async def connect(self, row) -> None:
        from bleak import BleakClient

        self.disconnected = asyncio.Event()
        self.client = BleakClient(row["dev"], timeout=20.0,
                                  disconnected_callback=lambda _c: self.disconnected.set())
        await self.client.connect()

    def has(self, uuid: str) -> bool:
        return any(c.uuid.lower() == uuid for s in self.client.services for c in s.characteristics)

    async def secure(self) -> None:
        try:
            await self.client.pair()
        except Exception as exc:  # macOS pairs implicitly; others may refuse
            self.log(f"[active] pair(): {exc!r}")

    async def subscribe(self, uuid: str, cb) -> None:
        await self.client.start_notify(uuid, lambda _c, d: cb(bytes(d)))

    async def write(self, uuid: str, data: bytes) -> None:
        await self.client.write_gatt_char(uuid, data, response=True)

    async def disconnect(self) -> None:
        if self.client is not None and self.client.is_connected:
            await self.client.disconnect()


def _hub_alive() -> bool:
    from dongle_source import DongleHub

    return DongleHub._inst is not None and DongleHub._inst.alive()


class _DongleLink:
    """The USB dongle via Bumble. Bond keys persist in a JSON keystore."""

    def __init__(self, log, keystore_path: str) -> None:
        self.log = log
        self.keystore_path = keystore_path
        self.transport = None
        self.device = None
        self.conn = None
        self.peer = None
        self.disconnected = asyncio.Event()

    async def open(self) -> None:
        from dongle_source import DongleHub

        # The shared hub keeps the dongle open across mode switches.
        self.device = await DongleHub.get().device(self.keystore_path)

    async def close(self) -> None:
        """Drop our connection only; the dongle stays open for the next source."""
        await self.disconnect()

    async def scan(self, seconds: float):
        from dongle_source import _AdvCache

        from hr_source import SCAN_LISTENERS

        cache = _AdvCache()
        self.device.on("advertisement", cache.add)
        try:
            await self.device.start_scanning(active=True, filter_duplicates=False)
            await asyncio.sleep(seconds)
            await self.device.stop_scanning()
        finally:
            self.device.remove_listener("advertisement", cache.add)
        rows = sorted(cache.rows.values(), key=lambda r: r["rssi"], reverse=True)
        report = [{"addr": r["addr"], "name": r["name"], "rssi": r["rssi"],
                   "uuids": sorted(r["uuids"]), "mfg": sorted(r["mfg"])} for r in rows]
        for fn in list(SCAN_LISTENERS):
            try:
                fn(report)
            except Exception:
                pass
        return [dict(r, uuids=sorted(r["uuids"]), mfg=sorted(r["mfg"])) for r in rows]

    async def connect(self, row) -> None:
        from bumble.device import Peer

        self.disconnected = asyncio.Event()
        try:
            if self.device.is_le_connecting:      # leftover from a cancelled source
                await asyncio.wait_for(self.device.cancel_connection(), 3)
        except Exception:
            pass
        self.log(f"[active] link: connecting to {row['addr']}")
        self.conn = await asyncio.wait_for(
            self.device.connect(row["address"], timeout=15), 20)
        self.conn.on("disconnection", lambda *_: self.disconnected.set())
        self.log("[active] link: connected; discovering services")
        self.peer = Peer(self.conn)
        await asyncio.wait_for(self.peer.discover_services(), 15)
        await asyncio.wait_for(self.peer.discover_characteristics(), 20)
        self.log(f"[active] link: {len(self.peer.services)} services discovered")

    def _char(self, uuid: str):
        from bumble.core import UUID

        found = self.peer.get_characteristics_by_uuid(UUID(uuid))
        return found[0] if found else None

    def has(self, uuid: str) -> bool:
        return self._char(uuid) is not None

    async def secure(self) -> None:
        """Encrypt with stored bond keys, or pair + bond the first time."""
        keys = await self.device.keystore.get(str(self.conn.peer_address))
        if keys is not None:
            self.log("[active] re-using bond keys")
            await self.conn.encrypt()
        else:
            self.log("[active] pairing (first time with this band)")
            await self.conn.pair()

    async def subscribe(self, uuid: str, cb) -> None:
        await self.peer.subscribe(self._char(uuid), lambda v: cb(bytes(v)))

    async def write(self, uuid: str, data: bytes) -> None:
        await self.peer.write_value(self._char(uuid), data, with_response=True)

    async def disconnect(self) -> None:
        if self.conn is not None and not self.disconnected.is_set():
            await self.conn.disconnect()


# ------------------------------------------------------------------ source --
class WhoopActiveSource(HeartRateSource):
    STOP_GRACE_S = 3.0   # long enough to restore the band's settings
    def __init__(
        self,
        on_bpm: Callable[[int], None],
        on_status: Optional[Callable[[str], None]],
        on_pulse: Callable[[list, float], None],
        on_extras: Callable[[dict], None],
        serial: Optional[str] = None,
        use_dongle: bool = True,
        scan_seconds: float = 6.0,
        log_path: Optional[str] = None,
    ) -> None:
        super().__init__(on_bpm, on_status, log_path=log_path)
        self._on_pulse = on_pulse
        self._on_extras = on_extras
        self._serial = (serial or "").strip()
        self._use_dongle = use_dongle
        self._scan_seconds = scan_seconds
        self._seq = 0

    # Over the dongle, run on the shared hub (no USB re-open on mode switch).
    def start(self) -> None:
        from dongle_source import HubSource

        (HubSource.start if self._use_dongle else HeartRateSource.start)(self)

    def stop(self) -> None:
        from dongle_source import HubSource

        if self._use_dongle:
            HubSource.stop(self)
            return
        super().stop()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=6)

    def wait(self, timeout: float = 5.0) -> None:
        from dongle_source import HubSource

        if self._use_dongle:
            HubSource.wait(self, timeout)

    def _keystore_path(self) -> str:
        base = os.path.dirname(self._log_path) if self._log_path else os.getcwd()
        return os.path.join(base, "whoop-overlay-bonds.json")

    async def _main(self) -> None:
        tag = "[active, dongle]" if self._use_dongle else "[active]"
        while not self._stop.is_set():
            link = _DongleLink(self._log, self._keystore_path()) if self._use_dongle \
                else _BleakLink(self._log)
            try:
                if not self._use_dongle or not _hub_alive():
                    self._on_status(f"{tag} Starting Bluetooth...")
                await link.open()
                while not self._stop.is_set():
                    rows = await link.scan(self._scan_seconds)
                    whoops = [r for r in rows if looks_like_whoop(r["name"], r["uuids"], r["mfg"])]
                    if not self._serial:
                        names = "\n".join(f"- {r['name'] or '(unnamed)'}  ({r['rssi']}dBm)"
                                          for r in whoops[:4]) or "(none in range)"
                        self._on_status("Choose your dedicated band\nHover here and click the "
                                        f"gear, then Source > Dedicated band.\n{names}")
                        continue
                    target = next((r for r in whoops
                                   if r["name"] and self._serial.lower() in r["name"].lower()), None)
                    if target is None:
                        self._on_status(f"Looking for {self._serial}\nNot heard yet. Is it "
                                        "charged, nearby, and off your phone?")
                        continue
                    await self._session(link, target, tag)
            except Exception as exc:
                from dongle_source import BUSY_STATUS, DongleHub, dongle_busy

                if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or \
                        "Timeout" in type(exc).__name__:
                    fails = getattr(self, "_timeouts", 0) + 1
                    self._timeouts = fails
                    self._log(f"[active] timed out ({fails} in a row)")
                    if self._use_dongle and fails >= 2:
                        self._log("[active] resetting the dongle after repeated timeouts")
                        self._timeouts = 0
                        await DongleHub.get().reset()

                if dongle_busy(exc):
                    self._on_status(BUSY_STATUS)
                elif "BluetoothNotAvailable" in type(exc).__name__ or "No Bluetooth" in str(exc):
                    self._on_status("No Bluetooth found\nPlug in the USB dongle: the overlay "
                                    "switches to it automatically within a few seconds.")
                else:
                    self._on_status(f"{tag} {type(exc).__name__}: {exc}\nRetrying...")
                self._log(f"[active] error: {exc!r}")
                await asyncio.sleep(3)
            finally:
                try:
                    await link.close()
                except Exception:
                    pass

    async def _hr_only(self, link, dec, label: str, exc: Exception) -> None:
        """The band refused to pair (it still holds a bond with another phone or
        computer). Keep the link up on standard HR instead of looping, and say
        exactly what to do."""
        self._log(f"[active] pairing refused by {label}: {exc!r}; staying on standard HR")
        self._on_status(f"{label} won't pair\nIt's still paired to another phone or computer. "
                        "Remove it there (or factory-reset the band), then reopen Settings. "
                        "Showing basic heart rate meanwhile.")
        while not self._stop.is_set() and not link.disconnected.is_set():
            await asyncio.sleep(1.0)
            self._on_extras({"Active": "band won't pair"})

    async def _session(self, link, target, tag: str) -> None:
        import whoop_framing as wf

        label = target["name"] or self._serial
        self._on_status(f"{tag} Connecting to {label} ({target['rssi']} dBm)...")
        await link.connect(target)
        self._timeouts = 0
        dec = ActiveDecoder(self._emit, self._on_pulse, self._log)
        pending = dec.pending

        def on_whoop(uuid: str):
            return lambda value: dec.feed(uuid, value)

        async def command(cid: int, body: bytes = b"", timeout: float = 5.0):
            self._seq = (self._seq + 1) & 0xFF
            seq = self._seq
            fut = asyncio.get_running_loop().create_future()
            pending[(cid, seq)] = fut
            try:
                await link.write(WHOOP_CMD, wf.build_command_frame(cid, seq, body))
                return await asyncio.wait_for(fut, timeout)
            except Exception:
                return None
            finally:
                pending.pop((cid, seq), None)

        def field(text: str) -> bytes:
            return text.encode()[:31].ljust(32, b"\x00")

        try:
            if link.has(HR_MEASUREMENT_UUID):
                await link.subscribe(HR_MEASUREMENT_UUID, dec.on_std)
            # WHOOP channels need a bond: pair once (dedicated band only).
            try:
                await link.subscribe(WHOOP_NOTIFY[0], on_whoop(WHOOP_NOTIFY[0]))
            except Exception as exc:
                self._log(f"[active] WHOOP channel locked ({type(exc).__name__}); securing link")
                self._on_status(f"{tag} Pairing with {label}...")
                try:
                    await link.secure()
                except Exception as pexc:
                    if not pairing_refused(pexc):
                        raise
                    await self._hr_only(link, dec, label, pexc)
                    return
                await link.subscribe(WHOOP_NOTIFY[0], on_whoop(WHOOP_NOTIFY[0]))
            for u in WHOOP_NOTIFY[1:]:
                if link.has(u):
                    try:
                        await link.subscribe(u, on_whoop(u))
                    except Exception as exc:
                        self._log(f"[active] notify {u[:8]}: {exc!r}")

            await link.write(WHOOP_CMD, wf.CLIENT_HELLO)
            await asyncio.sleep(0.4)
            self._on_status(f"{tag} Unlocking live sensors on {label}...")
            for name, val in UNLOCK_FF:
                r = await command(120, b"\x01" + field(name) + field(val))
                self._log(f"[active] ff {name}={val} -> {r.result_name if r else 'timeout'}")
            for name, val in UNLOCK_CFG:
                r = await command(119, b"\x01" + field(name) + field(val))
                self._log(f"[active] cfg {name}={val} -> {r.result_name if r else 'timeout'}")
            for cid, note in LIVE_ON:
                r = await command(cid, b"\x01\x01")
                self._log(f"[active] cmd {cid} {note} -> {r.result_name if r else 'timeout'}")
            r = await command(3, b"\x01")
            self._log(f"[active] realtime HR -> {r.result_name if r else 'timeout'}")
            self._on_status(f"{tag} Connected to {label}. Waiting for data\n"
                            "(band must be on your wrist)")

            last_kick = time.monotonic()
            while not self._stop.is_set() and not link.disconnected.is_set():
                await asyncio.sleep(1.0)
                self._on_extras(dec.extras())
                if time.monotonic() - last_kick > 60:
                    await command(3, b"\x01")
                    last_kick = time.monotonic()
        finally:
            if not link.disconnected.is_set():
                for cid, body in ((82, b"\x01\x01"), (108, b"\x01\x00"), (106, b"\x01\x00"),
                                  (3, b"\x00")):
                    await command(cid, body, timeout=1)
                for name, _ in UNLOCK_CFG:
                    await command(119, b"\x01" + field(name) + field("0"), timeout=1)
                try:
                    await link.disconnect()
                except Exception:
                    pass
            self._on_status(f"{tag} Disconnected, retrying...")
            self._log(f"[active] session ended ({dec.frames} frames, {dec.ppg_frames} PPG, "
                      f"{dec.bad_frames} bad CRC)")
