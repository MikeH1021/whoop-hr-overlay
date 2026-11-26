"""The on-screen overlay: live BPM + pulsing heart, a rolling heart-rate graph,
and a pulse-waveform graph (real PPG in Active mode, a beat-synced animation
in Passive mode).

Everything is animated per frame (number count-up, color cross-fades, eased
autoscale, continuously scrolling smooth curves), themeable (see THEMES), and
the layout adapts to whatever size the user drags it to.

Interaction: hover to reveal a gear (settings) and a resize grip; drag to move
(snaps to screen edges). While a game hides the cursor, clicks pass through.
"""
from __future__ import annotations

import collections
import math
import platform
import time

from PySide6 import QtCore, QtGui, QtWidgets

DEFAULT_FONT = "Segoe UI" if platform.system() == "Windows" else "Helvetica Neue"

DEFAULT_STYLE = {
    "theme": "Midnight",
    "font_family": DEFAULT_FONT,
    "font_scale": 1.0,
    "text_color": "#f6f8fb",
    "muted_color": "#9aa4b2",
    "bg_color": "#0c0e14",
    "bg_opacity": 0.74,
    "bg_gradient": True,
    "border_alpha": 0.10,
    "color_mode": "zones",        # "zones" (by % of max HR) or "fixed"
    "accent_color": "#ff4d5e",
    "corner_radius": 16,
    "line_width": 2.0,
    "glow": True,
    "text_shadow": False,
    "show_heart": True,
    "show_hr_graph": True,
    "show_pulse_graph": True,
    "show_stats": True,
    "show_captions": True,
    "show_grid": True,
    "show_zone": True,
    "hr_window_s": 30,
    "pulse_window_s": 6,
    # stream effects
    "fx_beat_rings": True,     # ring ripples out of the heart on every beat
    "fx_spikes": True,         # shake + flash + callout on a fast heart-rate jump
    "fx_mood": True,           # ICE COLD / LOCKED IN / HEART RACING / CLUTCH!
    "show_peak": True,         # session-peak marker on the graph
    "spike_bpm": 12,           # jump within 8 s that counts as a spike
}

MOODS = {"CLUTCH!": "#ff3b5c", "HEART RACING": "#ff9f45", "LOCKED IN": "#3ddc97",
         "ICE COLD": "#7cd8ff"}
SPIKE_COLOR = "#ff3b5c"

# Built-in looks. Applying one overwrites only these appearance keys.
THEMES = {
    "Midnight": dict(bg_color="#0c0e14", bg_opacity=0.74, bg_gradient=True, border_alpha=0.10,
                     text_color="#f6f8fb", muted_color="#9aa4b2", color_mode="zones",
                     accent_color="#ff4d5e", corner_radius=16, line_width=2.0, glow=True,
                     text_shadow=False, font_family=DEFAULT_FONT),
    "Glass": dict(bg_color="#24324a", bg_opacity=0.34, bg_gradient=True, border_alpha=0.28,
                  text_color="#ffffff", muted_color="#d3dcec", color_mode="zones",
                  accent_color="#7cc4ff", corner_radius=22, line_width=2.2, glow=True,
                  text_shadow=True, font_family=DEFAULT_FONT),
    "Neon": dict(bg_color="#08040f", bg_opacity=0.86, bg_gradient=True, border_alpha=0.22,
                 text_color="#f6ecff", muted_color="#a98be0", color_mode="fixed",
                 accent_color="#ff2bd6", corner_radius=12, line_width=2.4, glow=True,
                 text_shadow=False, font_family=DEFAULT_FONT),
    "Esports": dict(bg_color="#101010", bg_opacity=0.92, bg_gradient=False, border_alpha=0.0,
                    text_color="#ffffff", muted_color="#a3a3a3", color_mode="fixed",
                    accent_color="#ff3b3b", corner_radius=3, line_width=2.6, glow=False,
                    text_shadow=False, font_family=DEFAULT_FONT),
    "Minimal": dict(bg_color="#000000", bg_opacity=0.0, bg_gradient=False, border_alpha=0.0,
                    text_color="#ffffff", muted_color="#e2e2e2", color_mode="zones",
                    accent_color="#ff4d5e", corner_radius=0, line_width=2.2, glow=True,
                    text_shadow=True, font_family=DEFAULT_FONT),
    "Daylight": dict(bg_color="#f5f7fb", bg_opacity=0.94, bg_gradient=True, border_alpha=0.14,
                     text_color="#12151b", muted_color="#5b6474", color_mode="zones",
                     accent_color="#e0344a", corner_radius=16, line_width=2.0, glow=False,
                     text_shadow=False, font_family=DEFAULT_FONT),
    "Terminal": dict(bg_color="#020a04", bg_opacity=0.9, bg_gradient=False, border_alpha=0.3,
                     text_color="#b8ffc2", muted_color="#4fae63", color_mode="fixed",
                     accent_color="#39ff6a", corner_radius=2, line_width=1.6, glow=True,
                     text_shadow=False, font_family="Consolas"),
}

def sanitize_style(style: dict | None) -> dict:
    """Merge onto DEFAULT_STYLE, coercing each value to the default's type and
    dropping anything unusable, so a hand-edited/corrupt config can't break
    painting (which would otherwise raise on every frame)."""
    out = dict(DEFAULT_STYLE)
    for key, val in (style or {}).items():
        if key not in DEFAULT_STYLE:
            continue
        ref = DEFAULT_STYLE[key]
        try:
            if isinstance(ref, bool):
                val = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes", "on")
            elif isinstance(ref, int):
                val = int(float(val))
            elif isinstance(ref, float):
                val = float(val)
                if val != val or val in (float("inf"), float("-inf")):
                    continue
            elif key.endswith("_color"):
                if not QtGui.QColor(str(val)).isValid():
                    continue
                val = str(val)
            else:
                val = str(val)
        except (TypeError, ValueError):
            continue
        out[key] = val
    out["bg_opacity"] = max(0.0, min(1.0, out["bg_opacity"]))
    out["border_alpha"] = max(0.0, min(1.0, out["border_alpha"]))
    out["font_scale"] = max(0.3, min(4.0, out["font_scale"]))
    out["line_width"] = max(0.3, min(12.0, out["line_width"]))
    out["corner_radius"] = max(0, min(60, out["corner_radius"]))
    out["hr_window_s"] = max(5, min(3600, out["hr_window_s"]))
    out["pulse_window_s"] = max(1, min(60, out["pulse_window_s"]))
    out["spike_bpm"] = max(4, min(40, out["spike_bpm"]))
    if out["color_mode"] not in ("zones", "fixed"):
        out["color_mode"] = "zones"
    return out


DEFAULT_SIZE = (300, 170)
MIN_SIZE = (120, 54)
SNAP_PX = 18

ZONES = (  # (upper bound as fraction of max HR, color, name)
    (0.50, "#6ea8fe", "RECOVERY"), (0.60, "#48cae4", "WARM-UP"), (0.70, "#3ddc97", "AEROBIC"),
    (0.80, "#ffd166", "TEMPO"), (0.90, "#ff9f45", "THRESHOLD"), (9.99, "#ff5d6c", "MAX"),
)


def zone_of(bpm, max_hr):
    if not bpm or not max_hr:
        return None, QtGui.QColor("#9aa4b2"), ""
    pct = bpm / float(max_hr)
    for i, (thr, col, name) in enumerate(ZONES):
        if pct < thr:
            return i + 1, QtGui.QColor(col), name
    return len(ZONES), QtGui.QColor(ZONES[-1][1]), ZONES[-1][2]


def zone_color(bpm, max_hr) -> QtGui.QColor:  # kept for compatibility
    return zone_of(bpm, max_hr)[1]


def pulse_template(phase: float) -> float:
    """One PPG-shaped beat over phase 0..1: sharp systolic peak, dicrotic notch,
    smaller diastolic bump, slow decay."""
    p = phase % 1.0
    return (math.exp(-((p - 0.16) / 0.065) ** 2)
            + 0.38 * math.exp(-((p - 0.44) / 0.085) ** 2)
            + 0.08 * math.exp(-((p - 0.75) / 0.2) ** 2))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_color(a: QtGui.QColor, b: QtGui.QColor, t: float) -> QtGui.QColor:
    return QtGui.QColor.fromRgbF(_lerp(a.redF(), b.redF(), t), _lerp(a.greenF(), b.greenF(), t),
                                 _lerp(a.blueF(), b.blueF(), t), _lerp(a.alphaF(), b.alphaF(), t))


def smooth_path(pts) -> QtGui.QPainterPath:
    """Catmull-Rom spline through the points, as cubic Beziers."""
    path = QtGui.QPainterPath(pts[0])
    n = len(pts)
    if n < 3:
        for q in pts[1:]:
            path.lineTo(q)
        return path
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else p2
        path.cubicTo(p1 + (p2 - p0) / 6.0, p2 - (p3 - p1) / 6.0, p2)
    return path


def bucket_average(data, n):
    """Average (t, v) pairs into at most n buckets. Smooths jitter and bounds
    drawing cost; the last sample is kept exact so the live edge doesn't lag."""
    if len(data) <= n or n < 2:
        return data
    size = len(data) / float(n - 1)
    out = []
    i = 0.0
    last = len(data) - 1
    while int(i) < last:
        a, b = int(i), min(last, int(i + size))
        chunk = data[a:b] or [data[a]]
        out.append((sum(t for t, _ in chunk) / len(chunk), sum(v for _, v in chunk) / len(chunk)))
        i += size
    out.append(data[-1])
    return out


def bucket_extrema(data, n):
    """Min/max decimation into at most n buckets (2 points each, in time
    order). Unlike averaging, it keeps every waveform peak at full height."""
    if len(data) <= 2 * n or n < 2:
        return data
    size = len(data) / float(n)
    out = []
    for b in range(n):
        chunk = data[int(b * size):int((b + 1) * size)] or [data[min(len(data) - 1, int(b * size))]]
        lo = min(chunk, key=lambda tv: tv[1])
        hi = max(chunk, key=lambda tv: tv[1])
        out.extend((lo, hi) if lo[0] <= hi[0] else (hi, lo))
    out.append(data[-1])
    return out


class _Ease:
    """Exponential ease toward a target value (frame-rate independent)."""

    def __init__(self, speed: float) -> None:
        self.v = None
        self.speed = speed

    def step(self, target: float, dt: float) -> float:
        if self.v is None:
            self.v = target
        else:
            self.v += (target - self.v) * (1.0 - math.exp(-self.speed * dt))
        return self.v


def cursor_hidden_from(flags: int, hcursor) -> bool:
    """GetCursorInfo result -> is the mouse cursor hidden (a game has it)?"""
    CURSOR_SHOWING = 0x1
    return not (flags & CURSOR_SHOWING) or not hcursor


def windows_cursor_hidden():
    """True while a game hides/captures the cursor; None if unknown / not Windows."""
    if platform.system() != "Windows":
        return None
    import ctypes
    from ctypes import wintypes

    class CURSORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]

    try:
        ci = CURSORINFO()
        ci.cbSize = ctypes.sizeof(CURSORINFO)
        if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(ci)):
            return None
        return cursor_hidden_from(ci.flags, ci.hCursor)
    except Exception:
        return None


class OverlayWindow(QtWidgets.QWidget):
    settings_requested = QtCore.Signal()
    geometry_changed = QtCore.Signal(int, int, int, int)
    pin_changed = QtCore.Signal(bool)          # position lock toggled on the overlay

    def __init__(self, opts: dict, style: dict | None = None) -> None:
        super().__init__()
        self.opts = opts
        self.style = sanitize_style(style)
        self.max_hr = int(opts.get("max_hr", 190))
        self.locked = bool(opts.get("locked", False))
        self.snap = bool(opts.get("snap", True))
        self.pinned = bool(opts.get("pinned", False))   # position locked (still hoverable)
        self._pin_flash = -1e9                 # when a blocked drag was attempted
        # In-game (cursor hidden/captured) every click passes through to the
        # game, so stray in-game clicks can never move the overlay or open settings.
        self._passthrough = False

        self.hr = collections.deque()          # (t, bpm)
        self.pulse = collections.deque()       # (t, value)
        self.current = None
        self.status = "Starting..."
        self.extras: dict = {}
        self.pulse_real_until = 0.0
        self._ppg_t = 0.0
        self._ppg_arrival = None
        self._ppg_gaps = collections.deque(maxlen=20)   # recent batch inter-arrival times
        self._phase = 0.0
        self._last = time.monotonic()
        self._last_gen = self._last
        self._beat_env = 0.0
        # animated state
        self._shown = _Ease(5.0)               # displayed BPM (counts toward target)
        self._col = None                       # displayed accent color
        self._hover_a = _Ease(10.0)
        self._live_a = _Ease(4.0)              # cross-fade status view <-> live view
        self._hr_lo, self._hr_hi = _Ease(2.5), _Ease(2.5)
        self._pu_lo, self._pu_hi = _Ease(1.5), _Ease(1.5)
        self._hover = False
        self._drag = None
        self._resize = None
        self._rings: list = []                 # beat shockwave start times
        self._spike = None                     # (time, +bpm) of the last spike
        self._last_spike = -1e9
        self._peak = None                      # (time, bpm) session peak

        flags = (QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
                 | QtCore.Qt.Tool | QtCore.Qt.WindowDoesNotAcceptFocus)
        if self.locked:
            flags |= QtCore.Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setWindowTitle("WHOOP HR Overlay")
        self.setWindowOpacity(float(opts.get("opacity", 1.0)))
        self._place(opts.get("geometry"))

        self._fps = max(10, int(opts.get("fps", 60)))
        self._paint_ms = 0.0           # smoothed paint cost, drives adaptive fps
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(1000 / self._fps))
        if platform.system() == "Windows":
            self._cursor_timer = QtCore.QTimer(self)
            self._cursor_timer.timeout.connect(self._check_cursor)
            self._cursor_timer.start(100)

    def _check_cursor(self) -> None:
        hidden = windows_cursor_hidden()
        if hidden is None or hidden == self._passthrough:
            return
        self._passthrough = hidden
        if hidden:
            self._drag = self._resize = None
            self._hover = False
        self._apply_win32_styles()

    # ------------------------------------------------------------ placement --
    def _place(self, geometry) -> None:
        if geometry and len(geometry) == 4:
            x, y, w, h = (int(v) for v in geometry)
            self.setGeometry(x, y, max(MIN_SIZE[0], w), max(MIN_SIZE[1], h))
            if QtGui.QGuiApplication.screenAt(self.geometry().center()) is not None:
                return
        w, h = DEFAULT_SIZE
        geo = QtWidgets.QApplication.primaryScreen().availableGeometry()
        m = int(self.opts.get("margin", 24))
        pos = str(self.opts.get("position", "top-left"))
        x = geo.right() - w - m if "right" in pos else geo.left() + m
        y = geo.bottom() - h - m if "bottom" in pos else geo.top() + m
        self.setGeometry(x, y, w, h)

    def reset_geometry(self) -> None:
        self._place(None)
        self._emit_geometry()

    def set_locked(self, locked: bool) -> None:
        self.locked = bool(locked)
        self.setWindowFlag(QtCore.Qt.WindowTransparentForInput, self.locked)
        self.show()
        self._apply_win32_styles()

    def set_pinned(self, on: bool) -> None:
        self.pinned = bool(on)
        self._drag = self._resize = None

    def set_snap(self, on: bool) -> None:
        self.snap = bool(on)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_win32_styles()

    def _apply_win32_styles(self) -> None:
        if platform.system() != "Windows":
            return
        import ctypes

        GWL_EXSTYLE = -20
        LAYERED, TRANSPARENT = 0x00080000, 0x00000020
        TOOLWINDOW, NOACTIVATE = 0x00000080, 0x08000000
        try:
            hwnd = int(self.winId())
            u = ctypes.windll.user32
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE) | LAYERED | TOOLWINDOW | NOACTIVATE
            through = self.locked or self._passthrough
            ex = ex | TRANSPARENT if through else ex & ~TRANSPARENT
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        except Exception:
            pass

    # ---------------------------------------------------------------- style --
    def apply_style(self, style: dict) -> None:
        self.style = sanitize_style(style)
        self.update()

    def set_max_hr(self, v: int) -> None:
        self.max_hr = int(v)

    # ----------------------------------------------- data in (GUI thread) --
    @QtCore.Slot(int)
    def set_bpm(self, bpm: int) -> None:
        now = time.monotonic()
        self.current = int(bpm)
        self.hr.append((now, int(bpm)))
        if self._peak is None or bpm >= self._peak[1]:
            self._peak = (now, int(bpm))
        recent = [v for t, v in self.hr if t >= now - 8]
        jump = int(bpm) - min(recent) if recent else 0
        if (self.style["fx_spikes"] and jump >= int(self.style["spike_bpm"])
                and now - self._last_spike > 12):
            self._spike = (now, jump)
            self._last_spike = now
        cutoff = now - max(10, int(self.style["hr_window_s"])) - 3
        while self.hr and self.hr[0][0] < cutoff:
            self.hr.popleft()

    @QtCore.Slot(str)
    def set_status(self, text: str) -> None:
        self.status = text

    @QtCore.Slot(dict)
    def set_extras(self, extras: dict) -> None:
        self.extras = dict(extras)

    @QtCore.Slot(list, float)
    def add_pulse(self, samples: list, rate: float) -> None:
        """Real PPG samples (already detrended), spread over the batch so bursty
        BLE delivery still scrolls smoothly."""
        if not samples or rate <= 0:
            return
        now = time.monotonic()
        n = len(samples)
        # Jitter buffer: track how late batches can arrive, and play back that
        # far behind real time so the next batch is always in hand before the
        # graph needs it (no stall-then-jump).
        if self._ppg_arrival is not None:
            gap = now - self._ppg_arrival
            self._ppg_gaps.append(gap)
        self._ppg_arrival = now
        if self._ppg_t < now - 4.0 or self._ppg_t > now + 2.0:
            self._ppg_t = now - n / rate          # first batch, or after a real outage
            start, step = self._ppg_t, 1.0 / rate
        else:
            # Spread the batch so its end drifts gently toward "now": absorbs a
            # band clock that isn't exactly 25 Hz without snapping or overlap.
            start = self._ppg_t
            nominal = start + n / rate
            end = nominal + 0.25 * (now - nominal)
            step = max(0.6 / rate, min(1.5 / rate, (end - start) / n))
        t = start
        for v in samples:
            self.pulse.append((t, float(v)))
            t += step
        self._ppg_t = t
        self.pulse_real_until = now + 3.0
        self._trim_pulse(now)

    def _ppg_delay(self) -> float:
        """Playback delay for the real PPG: a little more than the worst recent
        gap between batches (eased, so the graph doesn't lurch)."""
        worst = max(self._ppg_gaps) if self._ppg_gaps else 1.0
        target = max(0.8, min(3.0, worst + 0.35))
        self._ppg_delay_v = getattr(self, "_ppg_delay_v", target)
        self._ppg_delay_v += (target - self._ppg_delay_v) * (0.02 if target < self._ppg_delay_v else 0.2)
        return self._ppg_delay_v

    def _trim_pulse(self, now: float) -> None:
        cutoff = now - max(2, int(self.style["pulse_window_s"])) - 4
        while self.pulse and self.pulse[0][0] < cutoff:
            self.pulse.popleft()

    @property
    def pulse_is_real(self) -> bool:
        return time.monotonic() < self.pulse_real_until

    # ------------------------------------------------------------ animation --
    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, max(0.0, now - self._last))
        self._last = now
        bpm = self.current
        if bpm:
            before = int(self._phase)
            self._phase += dt * bpm / 60.0
            if int(self._phase) != before and self.style["fx_beat_rings"]:
                self._rings.append(now)
            self._rings = [t for t in self._rings if now - t < 0.9]
            self._beat_env = max(0.0, 1.0 - (self._phase % 1.0) / 0.35) ** 1.6
            self._shown.step(float(bpm), dt)
        else:
            self._beat_env = 0.0
        # accent color cross-fade
        target = self._target_accent()
        self._col = target if self._col is None else _lerp_color(
            self._col, target, 1.0 - math.exp(-4.0 * dt))
        self._hover_a.step(1.0 if (self._hover and not self.locked) else 0.0, dt)
        self._live_a.step(1.0 if bpm else 0.0, dt)
        # beat-synced pulse animation when no real PPG is arriving
        if bpm and not self.pulse_is_real:
            t = self._last_gen
            step = 1.0 / 60.0
            ph = self._phase - (now - t) * bpm / 60.0
            while t + step <= now:
                t += step
                ph += step * bpm / 60.0
                self.pulse.append((t, pulse_template(ph)))
            self._last_gen = t
        else:
            self._last_gen = now
        self._trim_pulse(now)
        self.update()

    def _target_accent(self) -> QtGui.QColor:
        if self.style["color_mode"] == "fixed":
            return QtGui.QColor(self.style["accent_color"])
        return zone_of(self.current, self.max_hr)[1]

    # -------------------------------------------------------- interactions --
    def _k(self) -> float:
        return max(0.55, min(self.width() / DEFAULT_SIZE[0], self.height() / DEFAULT_SIZE[1]))

    def _gear_rect(self) -> QtCore.QRectF:
        g = max(14.0, 17 * self._k())
        return QtCore.QRectF(self.width() - g - 7, 7, g, g)

    def _lock_rect(self) -> QtCore.QRectF:
        g = self._gear_rect()
        return g.translated(-(g.width() + 9), 0)

    def _grip_rect(self) -> QtCore.QRectF:
        g = max(12.0, 15 * self._k())
        return QtCore.QRectF(self.width() - g - 4, self.height() - g - 4, g, g)

    def enterEvent(self, event) -> None:
        self._hover = True

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.unsetCursor()

    def mousePressEvent(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        p = event.position()
        if self._gear_rect().adjusted(-4, -4, 4, 4).contains(p):
            self.settings_requested.emit()
        elif self._lock_rect().adjusted(-4, -4, 4, 4).contains(p):
            self.set_pinned(not self.pinned)
            self.pin_changed.emit(self.pinned)
        elif self.pinned:
            self._pin_flash = time.monotonic()   # nudge: "it's locked"
        elif self._grip_rect().adjusted(-4, -4, 4, 4).contains(p):
            self._resize = (event.globalPosition().toPoint(), self.size())
        else:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        # Settings open only from the gear: a double-click on the body did it by
        # accident far too easily during games.
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        gp = event.globalPosition().toPoint()
        if self._drag is not None:
            self.move(self._snapped(gp - self._drag))
        elif self._resize is not None:
            start, size = self._resize
            d = gp - start
            self.resize(max(MIN_SIZE[0], size.width() + d.x()),
                        max(MIN_SIZE[1], size.height() + d.y()))
        else:
            p = event.position()
            if self._gear_rect().adjusted(-4, -4, 4, 4).contains(p):
                self.setCursor(QtCore.Qt.PointingHandCursor)
                QtWidgets.QToolTip.showText(gp, "Settings", self)
            elif self._lock_rect().adjusted(-4, -4, 4, 4).contains(p):
                self.setCursor(QtCore.Qt.PointingHandCursor)
                QtWidgets.QToolTip.showText(
                    gp, "Unlock position" if self.pinned else "Lock position", self)
            elif self.pinned:
                self.setCursor(QtCore.Qt.ArrowCursor)
            elif self._grip_rect().adjusted(-4, -4, 4, 4).contains(p):
                self.setCursor(QtCore.Qt.SizeFDiagCursor)
            else:
                self.setCursor(QtCore.Qt.OpenHandCursor)

    def _snapped(self, pos: QtCore.QPoint) -> QtCore.QPoint:
        if not self.snap:
            return pos
        screen = QtGui.QGuiApplication.screenAt(pos + QtCore.QPoint(self.width() // 2, 10))
        if screen is None:
            return pos
        g = screen.availableGeometry()
        m = 12
        x, y = pos.x(), pos.y()
        if abs(x - (g.left() + m)) < SNAP_PX:
            x = g.left() + m
        if abs((x + self.width()) - (g.right() - m)) < SNAP_PX:
            x = g.right() - m - self.width()
        if abs(y - (g.top() + m)) < SNAP_PX:
            y = g.top() + m
        if abs((y + self.height()) - (g.bottom() - m)) < SNAP_PX:
            y = g.bottom() - m - self.height()
        return QtCore.QPoint(x, y)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None or self._resize is not None:
            self._drag = self._resize = None
            self.setCursor(QtCore.Qt.OpenHandCursor)
            self._emit_geometry()

    def _emit_geometry(self) -> None:
        g = self.geometry()
        self.geometry_changed.emit(g.x(), g.y(), g.width(), g.height())

    def keyPressEvent(self, event) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            QtWidgets.QApplication.quit()

    # -------------------------------------------------------------- drawing --
    def _font(self, px: float, weight=QtGui.QFont.DemiBold, tabular=False) -> QtGui.QFont:
        fam = self.style["font_family"]
        f = QtGui.QFont(fam)
        if any(s in fam.lower() for s in ("mono", "consolas", "courier", "menlo")):
            f.setStyleHint(QtGui.QFont.TypeWriter)
        f.setPixelSize(max(6, int(px * float(self.style["font_scale"]))))
        f.setWeight(weight)
        if tabular:
            try:
                f.setFeature(QtGui.QFont.Tag("tnum"), 1)
            except Exception:
                pass
        return f

    def _text(self, p, rect, flags, s, color) -> None:
        if self.style["text_shadow"]:
            p.setPen(QtGui.QColor(0, 0, 0, 150))
            p.drawText(rect.translated(1.2, 1.4), flags, s)
        p.setPen(color)
        p.drawText(rect, flags, s)

    def paintEvent(self, _event) -> None:
        t0 = time.perf_counter()
        try:
            self._paint(_event)
        except Exception as exc:  # never let a paint error take the overlay down
            if not getattr(self, "_paint_err_logged", False):
                self._paint_err_logged = True
                import logging
                logging.getLogger("whoop-hr-overlay").exception("paint failed: %r", exc)
        ms = (time.perf_counter() - t0) * 1000.0
        self._paint_ms += (ms - self._paint_ms) * 0.1
        # Adaptive frame rate: if a (very large) overlay can't hold 60 fps,
        # drop to 30 so it stays even instead of stuttering; recover when cheap.
        want = self._fps
        if self._paint_ms > 11.0:
            want = min(self._fps, 30)
        interval = int(1000 / want)
        if self._timer.interval() != interval and (self._paint_ms > 11.0 or self._paint_ms < 6.0):
            self._timer.setInterval(interval)

    def _paint(self, _event) -> None:
        st = self.style
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        w, h, k = self.width(), self.height(), self._k()
        col = self._col or self._target_accent()
        text = QtGui.QColor(st["text_color"])
        muted = QtGui.QColor(st["muted_color"])

        self._paint_background(p, w, h, k)
        spike_a = self._spike_amount()
        if spike_a > 0:
            self._paint_spike_border(p, w, h, k, spike_a)
            age = time.monotonic() - self._spike[0]
            if age < 0.6:                       # quick decaying shake
                amp = 3.0 * k * (1 - age / 0.6)
                p.translate(math.sin(age * 70) * amp, math.cos(age * 55) * amp * 0.5)
        live = self._live_a.v or 0.0
        if live < 0.999:
            p.setOpacity(1.0 - live)
            self._paint_status(p, w, h, k, text, muted, col)
        if live > 0.001 and self.current:
            p.setOpacity(live)
            self._paint_live(p, w, h, k, text, muted, col)
        p.setOpacity(1.0)
        if self.style["fx_spikes"] and self.current and spike_a > 0:
            self._paint_spike_callout(p, w, k, spike_a)
        ha = self._hover_a.v or 0.0
        if ha > 0.01:
            self._paint_controls(p, k, text, ha)
        p.end()

    def _paint_background(self, p, w, h, k) -> None:
        st = self.style
        bg = QtGui.QColor(st["bg_color"])
        a = max(0.0, min(1.0, float(st["bg_opacity"])))
        r = float(st["corner_radius"]) * min(1.0, k)
        rect = QtCore.QRectF(0.5, 0.5, w - 1, h - 1)
        if a > 0:
            if st["bg_gradient"]:
                top = bg.lighter(135) if bg.lightness() < 128 else bg.lighter(104)
                bot = bg.darker(115) if bg.lightness() >= 128 else bg
                g = QtGui.QLinearGradient(0, 0, 0, h)
                top.setAlphaF(a)
                bot.setAlphaF(a)
                g.setColorAt(0.0, top)
                g.setColorAt(1.0, bot)
                p.setBrush(g)
            else:
                bg.setAlphaF(a)
                p.setBrush(bg)
            p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(rect, r, r)
        ba = float(st["border_alpha"])
        if ba > 0:
            light = QtGui.QColor(st["bg_color"]).lightness() >= 128
            bc = QtGui.QColor(0, 0, 0) if light else QtGui.QColor(255, 255, 255)
            bc.setAlphaF(min(1.0, ba))
            p.setPen(QtGui.QPen(bc, 1))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRoundedRect(rect, r, r)

    # -- layout --------------------------------------------------------------
    def _layout(self, w, h, k):
        """Rects for header (heart+number), hr graph, stats, pulse. Adapts to
        size: compact strip, landscape, or portrait stack."""
        st = self.style
        pad = max(6.0, 10 * k)
        portrait = h > w * 0.95
        stats = st["show_stats"] and h >= 78
        pulse = st["show_pulse_graph"] and h >= (120 if not portrait else 150)
        hr_graph = st["show_hr_graph"] and w >= 170
        R = QtCore.QRectF
        if portrait:
            head_h = min(h * (0.34 if pulse else 0.5), w * 0.55)
            y = pad
            L = {"head": R(pad, y, w - 2 * pad, head_h)}
            y += head_h
            rest = h - y - pad
            sh = max(12.0, 14 * k) if stats else 0
            gh = (rest - sh) * (0.45 if pulse else 1.0) if hr_graph else 0
            L["hr"] = R(pad, y, w - 2 * pad, gh) if hr_graph else None
            y += gh
            L["stats"] = R(pad, y, w - 2 * pad, sh) if stats else None
            y += sh
            L["pulse"] = R(pad, y + 2, w - 2 * pad, h - y - pad - 2) if pulse else None
            return L
        top_h = h * 0.58 if pulse else h
        sh = max(12.0, 14 * k) if stats else 0
        row_h = top_h - sh
        L = {"head": R(pad, pad * 0.3, w * 0.5, row_h - pad * 0.3)}
        L["stats"] = R(pad, row_h - 2, w - 2 * pad, sh) if stats else None
        L["pulse"] = R(pad, top_h + 2, w - 2 * pad, h - top_h - pad - 2) if pulse else None
        L["hr"] = "right-of-head" if hr_graph else None
        L["row_h"] = row_h
        return L

    def _paint_live(self, p, w, h, k, text, muted, col) -> None:
        L = self._layout(w, h, k)
        head = L["head"]
        num_right = self._paint_head(p, head, k, text, muted, col)
        hr = L["hr"]
        if hr == "right-of-head":
            pad = max(6.0, 10 * k)
            gx = num_right + 12 * k
            hr = QtCore.QRectF(gx, pad, w - gx - pad, L["row_h"] - pad * 1.3)
            if hr.width() < 30 * k:
                hr = None
        if hr is not None and hr.height() > 8:
            self._draw_hr_graph(p, hr, k, col, muted)
        if L["stats"] is not None:
            self._draw_stats(p, L["stats"], k, muted)
        if L["pulse"] is not None and L["pulse"].height() > 10:
            self._paint_pulse(p, L["pulse"], k, muted, col)

    def _paint_head(self, p, r, k, text, muted, col) -> float:
        """Heart + big number + caption inside r. Returns the x where it ends."""
        st = self.style
        x = r.left()
        size_ref = min(r.height(), r.width() * 0.6)
        if st["show_heart"]:
            hs = size_ref * 0.15 * (1.0 + 0.22 * self._beat_env)
            cx = x + size_ref * 0.17
            cy = r.top() + r.height() * 0.44
            if st["glow"] and self._beat_env > 0.02:
                halo = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), hs * 2.4)
                g0, g1 = QtGui.QColor(col), QtGui.QColor(col)
                g0.setAlphaF(0.32 * self._beat_env)
                g1.setAlphaF(0.0)
                halo.setColorAt(0.0, g0)
                halo.setColorAt(1.0, g1)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(QtCore.QPointF(cx, cy), hs * 2.4, hs * 2.4)
            now_r = time.monotonic()
            for t_r in self._rings:              # beat shockwaves
                age = (now_r - t_r) / 0.9
                rc = QtGui.QColor(col)
                rc.setAlphaF(max(0.0, 0.55 * (1 - age)))
                p.setPen(QtGui.QPen(rc, max(1.0, 2.2 * k * (1 - age) + 0.4)))
                p.setBrush(QtCore.Qt.NoBrush)
                rad = hs * (1.1 + 2.6 * age)
                p.drawEllipse(QtCore.QPointF(cx, cy), rad, rad)
            self._draw_heart(p, cx, cy, hs, col)
            x += size_ref * 0.37
        shown = int(round(self._shown.v or self.current or 0))
        p.setFont(self._font(size_ref * 0.56, QtGui.QFont.Bold, tabular=True))
        num = str(shown)
        fm = p.fontMetrics()
        num_w = fm.horizontalAdvance("8" * len(num))   # stable width while counting
        nr = QtCore.QRectF(x, r.top(), num_w + 4, r.height() * 0.8)
        self._text(p, nr, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, num, text)
        cap, cap_col = "BPM", muted
        mood = self._mood() if st["fx_mood"] else None
        if mood:
            cap, cap_col = f"BPM  ·  {mood}", QtGui.QColor(MOODS[mood])
        elif st["show_zone"] and st["color_mode"] == "zones" and self.current:
            z, _c, name = zone_of(self.current, self.max_hr)
            if z:
                cap = f"BPM  ·  Z{z} {name}"
        p.setFont(self._font(max(8.0, size_ref * 0.11), QtGui.QFont.DemiBold))
        cap_w = p.fontMetrics().horizontalAdvance(cap)
        self._text(p, QtCore.QRectF(x + 2, r.top() + r.height() * 0.74, cap_w + 4,
                                    r.height() * 0.22),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, cap, cap_col)
        return x + max(num_w, min(cap_w, num_w * 1.6))

    def _draw_stats(self, p, rect, k, muted) -> None:
        now = time.monotonic()
        win = int(self.style["hr_window_s"])
        vals = [v for t, v in self.hr if t >= now - win]
        parts = []
        if vals:
            parts.append(f"min {min(vals)}   avg {round(sum(vals) / len(vals))}   max {max(vals)}")
        parts += [f"{key} {val}" for key, val in self.extras.items()]
        p.setFont(self._font(max(8.0, 9.5 * k), QtGui.QFont.Medium, tabular=True))
        s = p.fontMetrics().elidedText("     ".join(parts), QtCore.Qt.ElideRight,
                                       int(rect.width()))
        self._text(p, rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, s, muted)

    def _draw_heart(self, p, cx, cy, size, color) -> None:
        path = QtGui.QPainterPath()
        path.moveTo(cx, cy + size * 0.72)
        path.cubicTo(cx - size * 1.45, cy - size * 0.25, cx - size * 0.60, cy - size * 1.20,
                     cx, cy - size * 0.35)
        path.cubicTo(cx + size * 0.60, cy - size * 1.20, cx + size * 1.45, cy - size * 0.25,
                     cx, cy + size * 0.72)
        p.save()
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path)
        p.setBrush(QtGui.QColor(255, 255, 255, 70))
        p.drawEllipse(QtCore.QPointF(cx - size * 0.45, cy - size * 0.35), size * 0.28, size * 0.2)
        p.restore()

    def _stroke(self, p, pts, rect, color, k, fill=True) -> None:
        """Gradient area + (optional glow) + line through pts.

        Dense data (< 6 px per point) is drawn as a polyline: at that density
        it's visually identical to a spline and far cheaper to rasterise at
        large sizes. Sparse data gets a Catmull-Rom spline so it reads smooth.
        """
        if len(pts) < 2:
            return
        span = pts[-1].x() - pts[0].x()
        sparse = span / max(1, len(pts) - 1) > 6
        if sparse:
            path = smooth_path(pts)
        else:
            path = QtGui.QPainterPath()
            path.addPolygon(QtGui.QPolygonF(pts))
        if fill:
            grad = QtGui.QLinearGradient(0, rect.top(), 0, rect.bottom())
            c0, c1 = QtGui.QColor(color), QtGui.QColor(color)
            c0.setAlphaF(0.38)
            c1.setAlphaF(0.0)
            grad.setColorAt(0.0, c0)
            grad.setColorAt(1.0, c1)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(grad)
            base_r = QtCore.QPointF(pts[-1].x(), rect.bottom())
            base_l = QtCore.QPointF(pts[0].x(), rect.bottom())
            if sparse:
                area = QtGui.QPainterPath(path)
                area.lineTo(base_r)
                area.lineTo(base_l)
                area.closeSubpath()
                p.drawPath(area)
            else:
                p.drawPolygon(QtGui.QPolygonF(list(pts) + [base_r, base_l]))
        # line width grows with size, but capped so big overlays stay crisp + fast
        lw = float(self.style["line_width"]) * min(1.8, max(0.75, k))
        p.setBrush(QtCore.Qt.NoBrush)
        if self.style["glow"]:
            self._glow(p, path, color, lw)
        p.setPen(QtGui.QPen(color, lw, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap,
                            QtCore.Qt.RoundJoin))
        if sparse:
            p.drawPath(path)
        else:
            p.drawPolyline(QtGui.QPolygonF(pts))

    def _glow(self, p, path, color, lw) -> None:
        """Soft glow: wide strokes drawn into a 1/3-resolution image, then scaled
        up smoothly. About 9x cheaper than full-resolution wide strokes, and
        the upscale softens it nicely."""
        pad = lw * 3.0
        br = path.boundingRect().adjusted(-pad, -pad, pad, pad)
        s = 1.0 / 3.0 if br.width() < 500 else 1.0 / 4.0
        iw, ih = max(1, int(br.width() * s) + 1), max(1, int(br.height() * s) + 1)
        img = QtGui.QImage(iw, ih, QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(QtCore.Qt.transparent)
        q = QtGui.QPainter(img)
        q.setRenderHint(QtGui.QPainter.Antialiasing, True)
        q.scale(s, s)
        q.translate(-br.left(), -br.top())
        g = QtGui.QColor(color)
        for width, alpha in ((lw * 5.0, 0.11), (lw * 2.6, 0.2)):
            g.setAlphaF(alpha)
            q.setPen(QtGui.QPen(g, width, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap,
                                QtCore.Qt.RoundJoin))
            q.drawPath(path)
        q.end()
        p.save()
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        p.drawImage(QtCore.QRectF(br.left(), br.top(), iw / s, ih / s), img)
        p.restore()

    def _grid(self, p, rect, muted) -> None:
        if not self.style["show_grid"]:
            return
        g = QtGui.QColor(muted)
        g.setAlphaF(0.12)
        pen = QtGui.QPen(g, 1, QtCore.Qt.DashLine)
        pen.setDashPattern([2, 4])
        p.setPen(pen)
        for f in (0.25, 0.5, 0.75):
            y = rect.top() + rect.height() * f
            p.drawLine(QtCore.QPointF(rect.left(), y), QtCore.QPointF(rect.right(), y))

    def _draw_hr_graph(self, p, rect, k, col, muted) -> None:
        now = time.monotonic()
        win = max(5, int(self.style["hr_window_s"]))
        t0 = now - win
        data = [(t, v) for t, v in self.hr if t >= t0 - 2]
        if len(data) < 1:
            return
        data = bucket_average(data, int(max(12, rect.width() / 7)))  # ~1 point per 7 px
        # extend to "now" with the animated value so the trace scrolls smoothly
        data.append((now, self._shown.v or data[-1][1]))
        vals = [v for _, v in data]
        lo, hi = float(min(vals)), float(max(vals))
        if hi - lo < 10:
            mid = (hi + lo) / 2
            lo, hi = mid - 5, mid + 5
        dt = 1 / 60
        lo = self._hr_lo.step(lo - 3, dt)
        hi = self._hr_hi.step(hi + 3, dt)
        span = max(1.0, hi - lo)
        self._grid(p, rect, muted)
        pts = [QtCore.QPointF(rect.left() + (t - t0) / win * rect.width(),
                              rect.bottom() - max(0.0, min(1.0, (v - lo) / span)) * rect.height())
               for t, v in data]
        p.save()
        p.setClipRect(rect.adjusted(-2, -6, 6, 2))
        self._stroke(p, pts, rect, col, k)
        p.restore()
        # live dot with a ring that pulses on each beat
        end = pts[-1]
        ring = QtGui.QColor(col)
        ring.setAlphaF(0.35 * self._beat_env)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(ring)
        rr = (3.0 + 5.0 * self._beat_env) * k
        p.drawEllipse(end, rr, rr)
        p.setBrush(col)
        p.drawEllipse(end, 2.8 * k, 2.8 * k)
        avg = sum(v for _t, v in data) / len(data)
        if (self.style["show_peak"] and self._peak and self._peak[0] >= t0
                and self._peak[0] < now - 3 and self._peak[1] >= avg + 5):
            tp, vp = self._peak   # only when it stands out and isn't the live point
            px = rect.left() + (tp - t0) / win * rect.width()
            py = rect.bottom() - max(0.0, min(1.0, (vp - lo) / span)) * rect.height()
            tc = QtGui.QColor(self.style["text_color"])
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(tc)
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(px, py - 3 * k),
                                           QtCore.QPointF(px - 3.5 * k, py - 9 * k),
                                           QtCore.QPointF(px + 3.5 * k, py - 9 * k)]))
            p.setFont(self._font(max(7.5, 8 * k), QtGui.QFont.Bold))
            label = f"PEAK {vp}"
            lw_ = p.fontMetrics().horizontalAdvance(label)
            lx = min(max(rect.left(), px - lw_ / 2), rect.right() - lw_)
            ly = py - 22 * k if py - 22 * k > 0 else py + 4 * k
            self._text(p, QtCore.QRectF(lx, ly, lw_ + 2, 12 * k), QtCore.Qt.AlignCenter, label, tc)

    # ------------------------------------------------------- stream effects --
    def _spike_amount(self) -> float:
        """1.0 while a spike reaction is showing, fading out at the end."""
        if not self._spike:
            return 0.0
        age = time.monotonic() - self._spike[0]
        if age > 3.0:
            return 0.0
        return 1.0 if age < 2.2 else 1.0 - (age - 2.2) / 0.8

    def _mood(self):
        if not self.current:
            return None
        if self._spike_amount() > 0:
            return "CLUTCH!"
        z = zone_of(self.current, self.max_hr)[0] or 1
        if z >= 4:
            return "HEART RACING"
        now = time.monotonic()
        vals = [v for t, v in self.hr if t >= now - 30]
        if len(vals) >= 15:
            m = sum(vals) / len(vals)
            sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
            if z <= 2 and sd < 1.6:
                return "ICE COLD"
            if sd < 3.0:
                return "LOCKED IN"
        return None

    def _paint_spike_border(self, p, w, h, k, a) -> None:
        c = QtGui.QColor(SPIKE_COLOR)
        c.setAlphaF(min(1.0, 0.95 * a * (0.55 + 0.45 * math.sin(time.monotonic() * 14))))
        r = float(self.style["corner_radius"]) * min(1.0, k)
        p.save()
        p.setBrush(QtCore.Qt.NoBrush)
        p.setPen(QtGui.QPen(c, 2.6 * k))
        p.drawRoundedRect(QtCore.QRectF(1.5, 1.5, w - 3, h - 3), r, r)
        p.restore()

    def _paint_spike_callout(self, p, w, k, a) -> None:
        age = time.monotonic() - self._spike[0]
        t = min(1.0, age / 0.3)                  # pop in with a little overshoot
        scale = 1.0 + 0.35 * math.sin(t * math.pi) * (1 - t * 0.5) if t < 1 else 1.0
        text = f"▲ +{self._spike[1]} BPM SPIKE"
        f = self._font(max(8.0, 10.5 * k), QtGui.QFont.Black)
        fm = QtGui.QFontMetrics(f)
        tw, th = fm.horizontalAdvance(text) + 16 * k, fm.height() + 6 * k
        cx = w - tw / 2 - 10 * k - 26 * k * (self._hover_a.v or 0.0)
        cy = 10 * k + th / 2
        p.save()
        p.setOpacity(a)
        p.translate(cx, cy)
        p.scale(scale, scale)
        rect = QtCore.QRectF(-tw / 2, -th / 2, tw, th)
        glow = QtGui.QColor(SPIKE_COLOR)
        glow.setAlphaF(0.35)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(glow)
        p.drawRoundedRect(rect.adjusted(-3 * k, -3 * k, 3 * k, 3 * k), th / 2 + 3 * k, th / 2 + 3 * k)
        p.setBrush(QtGui.QColor(SPIKE_COLOR))
        p.drawRoundedRect(rect, th / 2, th / 2)
        p.setFont(f)
        p.setPen(QtGui.QColor("white"))
        p.drawText(rect, QtCore.Qt.AlignCenter, text)
        p.restore()

    def _paint_pulse(self, p, rect, k, muted, col) -> None:
        real = self.pulse_is_real
        if self.style["show_captions"] and rect.height() > 30 * k:
            p.setFont(self._font(8.2 * k, QtGui.QFont.Bold))
            cap = "PPG  ·  LIVE FROM BAND" if real else "PULSE  ·  FROM HEART RATE"
            if real:
                dot = QtGui.QColor("#3ddc97")
                dot.setAlphaF(0.55 + 0.45 * self._beat_env)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(dot)
                p.drawEllipse(QtCore.QPointF(rect.left() + 3 * k, rect.top() + 6 * k), 2.6 * k, 2.6 * k)
                capr = QtCore.QRectF(rect.left() + 9 * k, rect.top(), rect.width(), 12 * k)
            else:
                capr = QtCore.QRectF(rect.left(), rect.top(), rect.width(), 12 * k)
            self._text(p, capr, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, cap, muted)
            rect = rect.adjusted(0, 14 * k, 0, 0)
        now = time.monotonic()
        delay = self._ppg_delay() if real else 0.0
        win = max(2, int(self.style["pulse_window_s"]))
        t1 = now - delay
        t0 = t1 - win
        data = [(t, v) for t, v in self.pulse if t0 - 0.2 <= t <= t1]
        if len(data) < 3 or rect.height() < 6:
            return
        data = bucket_extrema(data, int(max(20, rect.width() / 3)))  # peaks preserved
        vals = [v for _, v in data]
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-6:
            hi = lo + 1.0
        lo = self._pu_lo.step(lo, 1 / 60)
        hi = self._pu_hi.step(hi, 1 / 60)
        span = (hi - lo) or 1.0
        top, height = rect.top() + rect.height() * 0.06, rect.height() * 0.9
        pts = [QtCore.QPointF(rect.left() + (t - t0) / win * rect.width(),
                              top + height - max(0.0, min(1.0, (v - lo) / span)) * height)
               for t, v in data]
        p.save()
        p.setClipRect(rect.adjusted(-2, -4, 2, 2))
        self._stroke(p, pts, rect, col, k, fill=True)
        p.restore()

    def _paint_status(self, p, w, h, k, text, muted, col) -> None:
        """Connecting / setup view: spinner, a title and a friendly detail."""
        pad = max(6.0, 10 * k)
        lines = [s for s in (self.status or "").split("\n") if s.strip()]
        title = lines[0] if lines else "Starting..."
        if title.startswith("["):
            title = title.split("] ", 1)[-1]
        detail = "\n".join(lines[1:])
        x = pad + 28 * k
        avail_w = w - x - pad - 20 * k
        tfont = self._font(12.5 * k, QtGui.QFont.Bold)
        dfont = self._font(max(8.5, 9.5 * k), QtGui.QFont.Normal)
        flags_wrap = QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop | QtCore.Qt.TextWordWrap
        t_h = QtGui.QFontMetrics(tfont).boundingRect(
            QtCore.QRect(0, 0, int(avail_w), 1000), flags_wrap, title).height()
        t_h = min(t_h, QtGui.QFontMetrics(tfont).lineSpacing() * 2)
        d_h = QtGui.QFontMetrics(dfont).boundingRect(
            QtCore.QRect(0, 0, int(w - x - pad), 1000), flags_wrap, detail).height() if detail else 0
        block = t_h + (d_h + 4 * k if detail else 0)
        y0 = max(pad, (h - block) / 2)            # vertically centred block
        # spinner, aligned with the title's first line
        c = QtCore.QPointF(pad + 11 * k, y0 + QtGui.QFontMetrics(tfont).lineSpacing() / 2)
        rad = 8 * k
        track = QtGui.QColor(muted)
        track.setAlphaF(0.25)
        p.setPen(QtGui.QPen(track, 2.2 * k))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(c, rad, rad)
        ang = (time.monotonic() * 300) % 360
        pen = QtGui.QPen(col, 2.2 * k, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(QtCore.QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad),
                  int(-ang * 16), int(-100 * 16))
        p.setFont(tfont)
        tr = QtCore.QRectF(x, y0, avail_w, t_h)
        self._text(p, tr, flags_wrap, title, text)
        if detail:
            p.setFont(dfont)
            self._text(p, QtCore.QRectF(x, tr.bottom() + 4 * k, w - x - pad, h - tr.bottom() - pad),
                       flags_wrap, detail, muted)

    def _paint_lock(self, p, k, text, chip) -> None:
        """Padlock chip left of the gear: closed + accent-tinted when pinned.
        Pulses briefly when a drag is blocked so it's obvious why nothing moved."""
        r = self._lock_rect()
        c = r.center()
        flash = max(0.0, 1.0 - (time.monotonic() - self._pin_flash) / 0.6)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(chip)
        grow = 3 + 3 * flash
        p.drawEllipse(c, r.width() / 2 + grow, r.height() / 2 + grow)
        col = QtGui.QColor(self._col or self._target_accent()) if self.pinned else QtGui.QColor(text)
        s = r.width()
        body = QtCore.QRectF(c.x() - s * 0.32, c.y() - s * 0.04, s * 0.64, s * 0.46)
        pen = QtGui.QPen(col, max(1.2, s * 0.11))
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        sh = QtCore.QRectF(c.x() - s * 0.2, c.y() - s * 0.4, s * 0.4, s * 0.5)
        if self.pinned:
            p.drawArc(sh, 0, 180 * 16)
            p.drawLine(QtCore.QPointF(sh.left(), sh.center().y()),
                       QtCore.QPointF(sh.left(), body.top()))
            p.drawLine(QtCore.QPointF(sh.right(), sh.center().y()),
                       QtCore.QPointF(sh.right(), body.top()))
        else:  # open shackle, swung up on the right
            o = sh.translated(s * 0.16, -s * 0.12)
            p.drawArc(o, 0, 180 * 16)
            p.drawLine(QtCore.QPointF(o.left(), o.center().y()),
                       QtCore.QPointF(o.left(), body.top()))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(body, s * 0.08, s * 0.08)

    def _paint_controls(self, p, k, text, alpha) -> None:
        p.save()
        p.setOpacity(alpha)
        g = self._gear_rect()
        c = g.center()
        light = QtGui.QColor(self.style["bg_color"]).lightness() >= 128
        chip = QtGui.QColor(255, 255, 255, 200) if light else QtGui.QColor(0, 0, 0, 140)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(chip)
        p.drawEllipse(c, g.width() / 2 + 3, g.height() / 2 + 3)
        p.setBrush(text)
        rad = g.width() / 2
        gear = QtGui.QPainterPath()
        for i in range(8):
            tooth = QtGui.QPainterPath()
            tw, th = rad * 0.34, rad * 0.32
            tooth.addRect(QtCore.QRectF(-tw / 2, -rad, tw, th))
            tr = QtGui.QTransform().translate(c.x(), c.y()).rotateRadians(i * math.pi / 4)
            gear.addPath(tr.map(tooth))
        gear.addEllipse(c, rad * 0.72, rad * 0.72)
        hole = QtGui.QPainterPath()
        hole.addEllipse(c, rad * 0.3, rad * 0.3)
        p.drawPath(gear.simplified().subtracted(hole))
        self._paint_lock(p, k, text, chip)
        if self.pinned:
            p.restore()
            return
        gr = self._grip_rect()
        pen = QtGui.QPen(text, max(1.0, 1.4 * k))
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        for i in range(3):
            o = gr.width() * (0.3 + 0.3 * i)
            p.drawLine(QtCore.QPointF(gr.right() - o, gr.bottom()),
                       QtCore.QPointF(gr.right(), gr.bottom() - o))
        p.restore()
