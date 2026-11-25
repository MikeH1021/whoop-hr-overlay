"""Headless tests: framing, decoders, the Active-mode pipeline (with genuine,
CRC-correct WHOOP frames split into BLE-sized fragments), config safety, and
rendering every theme x size x state. Run: QT_QPA_PLATFORM=offscreen pytest"""
import json
import math
import os
import struct
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import whoop_framing as wf  # noqa: E402
from active_source import (ActiveDecoder, PpgProcessor, motion_from_frame,  # noqa: E402
                           ppg_from_frame, rmssd, rr_from_frame)
from hr_source import looks_like_whoop, parse_hr_measurement  # noqa: E402


# ------------------------------------------------------------- helpers --
def data_frame(ptype: int, total_len: int, fill: dict) -> bytes:
    """A CRC-correct WHOOP frame of exactly total_len bytes whose absolute
    byte offsets (as the decoders index them) are set from `fill`."""
    body_len = total_len - 8 - 4 - 3          # header+crc16, crc32, [type,seq,cmd]
    body = bytearray(body_len)
    for off, val in fill.items():
        body[off - 11] = val                  # body starts at absolute offset 11
    f = wf.build_command_frame(0, 0, bytes(body), ptype=ptype)
    assert len(f) == total_len and wf.verify_frame(f)
    return f


def type40(bpm: int, rr_ms: list) -> bytes:
    fill = {16: bpm, 17: len(rr_ms)}
    for i, ms in enumerate(rr_ms):
        ticks = round(ms * 1024 / 1000)
        fill[18 + 2 * i], fill[19 + 2 * i] = ticks & 0xFF, ticks >> 8
    return data_frame(40, 32, fill)


def optical(samples: list) -> bytes:
    fill = {26: len(samples)}
    for i, v in enumerate(samples):
        for j, b in enumerate(struct.pack("<i", v)):
            fill[47 + 4 * i + j] = b
    return data_frame(43, 2140, fill)


def imu(accel_g: list) -> bytes:
    fill = {}
    for axis, base in ((0, 28), (1, 228), (2, 428)):
        for i in range(100):
            v = int(accel_g[i][axis] * 4096)
            fill[base + 2 * i], fill[base + 2 * i + 1] = struct.pack("<h", v)
    return data_frame(43, 1244, fill)


def fragments(frame: bytes, mtu: int = 20):
    return [frame[i:i + mtu] for i in range(0, len(frame), mtu)]


# ------------------------------------------------------------ framing --
def test_client_hello_matches_known_bytes():
    assert wf.build_command_frame(145, 1, b"\x01") == wf.CLIENT_HELLO


def test_corrupt_frame_rejected():
    f = bytearray(type40(70, []))
    f[16] ^= 0xFF
    assert not wf.verify_frame(bytes(f))


def test_reassembler_handles_fragments_and_garbage():
    f1, f2 = type40(71, [850]), type40(72, [840])
    r = wf.Reassembler()
    out = []
    for frag in fragments(b"\x00\x13garbage" + f1 + f2, 17):
        out += r.feed(frag)
    assert out == [f1, f2]


# ----------------------------------------------------------- decoders --
@pytest.mark.parametrize("payload,bpm", [(b"\x00\x48", 72), (b"\x01\x2c\x01", 300),
                                         (b"\x00", None), (b"", None)])
def test_standard_hr(payload, bpm):
    assert parse_hr_measurement(payload) == bpm


def test_whoop_detection():
    assert looks_like_whoop("W MGB0663331", [], [])
    assert looks_like_whoop("", ["fd4b0001-cce1-4033-93ce-002d5875f58a"], [])
    assert looks_like_whoop("", [], [816])
    assert not looks_like_whoop("Govee_H6076_4C10", [], [])
    assert not looks_like_whoop("iPhone", [], [])


def test_type40_decodes_bpm_and_rr():
    bpm, rr = rr_from_frame(type40(64, [940, 925, 960]))
    assert bpm == 64 and rr == [940, 925, 960]


def test_optical_and_imu_decoders():
    s = [100000 + i for i in range(25)]
    assert ppg_from_frame(optical(s)) == s
    still = [(0.0, 0.0, 1.0)] * 100
    assert motion_from_frame(imu(still)) < 0.01
    shaky = [(0.0, 0.0, 1.0 + 0.4 * math.sin(i)) for i in range(100)]
    assert motion_from_frame(imu(shaky)) > 0.12


def test_rmssd_known_value():
    # successive diffs 20, -20, 20, -20, 20 -> RMSSD = 20
    assert rmssd([800, 820, 800, 820, 800, 820]) == pytest.approx(20.0)
    assert rmssd([800, 810]) is None


def test_ppg_processor_finds_beats_at_the_right_rate():
    proc, beats = PpgProcessor(25.0), []
    bpm = 75
    for i in range(25 * 20):  # 20 s of a 75 bpm PPG (inverted, as the sensor reports)
        ph = (i / 25.0) * bpm / 60.0 % 1.0
        x = -int(300000 + 20000 * math.exp(-((ph - 0.16) / 0.065) ** 2))
        _, b = proc.feed([x])
        beats += b
    assert len(beats) >= 15
    mean = sum(beats[3:]) / len(beats[3:])
    assert abs(mean - 60000 / bpm) < 40   # ~800 ms


# --------------------------------------------- active-mode pipeline --
def test_active_decoder_end_to_end():
    now = [1000.0]
    bpms, pulses, logs = [], [], []
    dec = ActiveDecoder(bpms.append, lambda w, r: pulses.append((w, r)), logs.append,
                        clock=lambda: now[0])
    uuid = "fd4b0003-cce1-4033-93ce-002d5875f58a"
    stream = type40(66, [910, 905]) + optical([300000 + (i % 5) * 3000 for i in range(25)])
    stream += imu([(0, 0, 1.0)] * 100) + type40(67, [895])
    for frag in fragments(stream, 20):          # BLE-sized notifications
        dec.feed(uuid, frag)
    assert bpms == [66, 67]
    assert len(pulses) == 1 and len(pulses[0][0]) == 25 and pulses[0][1] == 25.0
    assert list(dec.rr) == [910, 905, 895]
    assert dec.motion is not None and dec.extras()["motion"] == "still"
    assert dec.frames == 4 and dec.bad_frames == 0
    # standard HR is ignored while type-40 frames flow, used once they stop
    dec.on_std(b"\x00\x50")
    assert bpms[-1] == 67
    now[0] += 5
    dec.on_std(b"\x00\x50")
    assert bpms[-1] == 80


def test_active_decoder_flags_railed_sensor():
    dec = ActiveDecoder(lambda b: None, lambda w, r: None)
    dec.feed("u", optical([524287] * 25))
    assert dec.railed and dec.extras()["PPG"] == "off wrist?"


def test_pairing_refusal_is_recognised():
    from active_source import pairing_refused

    class ProtocolError(Exception):
        pass

    assert pairing_refused(ProtocolError("smp/PAIRING_NOT_SUPPORTED [0x5]"))
    assert not pairing_refused(TimeoutError("connection timed out"))


def test_command_response_resolves_pending_future():
    import asyncio

    async def go():
        dec = ActiveDecoder(lambda b: None, lambda w, r: None)
        fut = asyncio.get_running_loop().create_future()
        dec.pending[(3, 7)] = fut
        resp = wf.build_command_frame(3, 1, b"\x07\x01", ptype=36)  # cmd 3, origin seq 7, SUCCESS
        dec.feed("u", resp)
        return await asyncio.wait_for(fut, 1)

    r = asyncio.run(go())
    assert r.cmd == 3 and r.origin_seq == 7 and r.result_name == "SUCCESS"


# ------------------------------------------------------------ config --
def test_corrupt_config_is_moved_aside(tmp_path):
    import main

    p = tmp_path / "config.json"
    p.write_text("{not json")
    cfg = main.load_config(str(p))
    assert cfg == {"max_hr": 190}          # fresh defaults, no crash
    assert (tmp_path / "config.bad.json").exists() and not p.exists()


def test_config_migration():
    import main

    cfg = main.migrate_config({"name": "WHOOP MGB0680949", "hr_mode": "weird",
                               "geometry": "nope", "max_hr": "abc", "style": 5})
    assert cfg["serial"] == "MGB0680949" and cfg["hr_mode"] == "passive"
    assert "geometry" not in cfg and cfg["max_hr"] == 190 and cfg["style"] == {}


def test_sanitize_style_repairs_bad_values():
    from overlay import DEFAULT_STYLE, sanitize_style

    st = sanitize_style({"font_scale": "big", "line_width": "3", "bg_opacity": 7,
                         "glow": "false", "accent_color": "notacolor", "bogus": 1,
                         "hr_window_s": -4, "color_mode": "rainbow"})
    assert st["font_scale"] == DEFAULT_STYLE["font_scale"]
    assert st["line_width"] == 3.0 and st["bg_opacity"] == 1.0 and st["glow"] is False
    assert st["accent_color"] == DEFAULT_STYLE["accent_color"] and "bogus" not in st
    assert st["hr_window_s"] == 5 and st["color_mode"] == "zones"


# ----------------------------------------------------------- rendering --
@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _render(qapp, theme, size, state):
    from PySide6 import QtGui

    import overlay

    w = overlay.OverlayWindow({"max_hr": 190, "geometry": [0, 0, *size]},
                              dict(overlay.THEMES[theme], theme=theme))
    if state != "status":
        now = time.monotonic()
        for i in range(40):
            w.hr.append((now - 40 + i, 70 + (i % 7)))
        w.current = 75
        if state == "active":
            w.add_pulse([overlay.pulse_template(i / 25) for i in range(150)], 25.0)
            w.set_extras({"HRV": "50 ms", "motion": "still"})
    else:
        w.set_status("Choose your band\nHover here and click the gear, then Source.")
    img = QtGui.QImage(w.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
    for _ in range(3):
        w._tick()
        w._hover = True
        w._paint(None)  # direct, so exceptions surface instead of being logged
        w.render(img)
    w.close()
    return img


@pytest.mark.parametrize("size", [(120, 54), (230, 74), (300, 170), (170, 300), (900, 480)])
@pytest.mark.parametrize("state", ["status", "passive", "active"])
def test_every_theme_renders(qapp, size, state):
    import overlay

    for theme in overlay.THEMES:
        img = _render(qapp, theme, size, state)
        assert not img.isNull()


def test_settings_dialog_builds_and_applies_themes(qapp):
    import overlay
    import settings_dialog

    cfg, changes = {"style": dict(overlay.DEFAULT_STYLE)}, []
    d = settings_dialog.SettingsDialog(cfg, lambda: ["W MGB0663331"], changes.append,
                                       lambda: None, start_tab="look")
    for name in overlay.THEMES:
        d._apply_theme(name)
        assert cfg["style"]["theme"] == name
    d._set_style("accent_color", "#123456")
    assert cfg["style"]["theme"] == "Custom" and "style" in changes
    json.dumps(cfg)  # whole config stays serialisable


def test_stream_effects(qapp):
    from PySide6 import QtGui

    import overlay

    w = overlay.OverlayWindow({"max_hr": 190, "geometry": [0, 0, 300, 170]}, {})
    now = time.monotonic()
    for i in range(30):
        w.hr.append((now - 30 + i, 62 + (i % 2)))
    w.set_bpm(62)
    assert w._mood() == "ICE COLD" and w._spike is None
    w.set_bpm(80)                                   # +18 within 8 s
    assert w._spike and w._spike[1] == 18 and w._mood() == "CLUTCH!"
    w.set_bpm(95)                                   # cooldown: no second spike
    assert w._spike[1] == 18
    img = QtGui.QImage(w.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
    for _ in range(5):
        w._tick()
        w._paint(None)
        w.render(img)
    off = overlay.OverlayWindow({"max_hr": 190}, {"fx_spikes": False})
    off.set_bpm(60)
    off.set_bpm(90)
    assert off._spike is None


def test_dongle_busy_detection():
    from dongle_source import dongle_busy

    class USBErrorAccess(Exception):
        def __repr__(self):
            return "USBErrorAccess(LIBUSB_ERROR_ACCESS [-3])"

    assert dongle_busy(USBErrorAccess())
    assert not dongle_busy(RuntimeError("timeout"))
