"""The on-screen overlay: live BPM + pulsing heart, a rolling heart-rate graph,
and a second graph with the pulse waveform (real PPG in Active mode, a
beat-synced animation in Passive mode).

Interaction: hover to reveal a gear (opens settings) and a resize grip; drag
anywhere else to move. "Locked" makes the whole window click-through.
Layout scales with the window size; every visual is driven by ``style``.
"""
from __future__ import annotations

import collections
import math
import platform
import time

from PySide6 import QtCore, QtGui, QtWidgets

DEFAULT_STYLE = {
    "font_family": "Segoe UI",
    "font_scale": 1.0,
    "text_color": "#f6f8fb",
    "muted_color": "#9aa4b2",
    "bg_color": "#0c0e14",
    "bg_opacity": 0.72,
    "color_mode": "zones",        # "zones" (by % of max HR) or "fixed"
    "accent_color": "#ff4d5e",
    "corner_radius": 16,
    "line_width": 2.0,
    "show_heart": True,
    "show_hr_graph": True,
    "show_pulse_graph": True,
    "show_stats": True,
    "show_captions": True,
    "hr_window_s": 30,
    "pulse_window_s": 6,
}

DEFAULT_SIZE = (300, 170)
MIN_SIZE = (150, 64)

ZONES = (  # (upper bound as fraction of max HR, color)
    (0.50, "#6ea8fe"), (0.60, "#48cae4"), (0.70, "#3ddc97"),
    (0.80, "#ffd166"), (0.90, "#ff9f45"), (9.99, "#ff5d6c"),
)


def zone_color(bpm, max_hr) -> QtGui.QColor:
    if not bpm or not max_hr:
        return QtGui.QColor("#9aa4b2")
    pct = bpm / float(max_hr)
    for thr, col in ZONES:
        if pct < thr:
            return QtGui.QColor(col)
    return QtGui.QColor(ZONES[-1][1])


def pulse_template(phase: float) -> float:
    """One PPG-shaped beat over phase 0..1: sharp systolic peak, dicrotic notch,
    smaller diastolic bump, slow decay."""
    p = phase % 1.0
    return (math.exp(-((p - 0.16) / 0.065) ** 2)
            + 0.38 * math.exp(-((p - 0.44) / 0.085) ** 2)
            + 0.08 * math.exp(-((p - 0.75) / 0.2) ** 2))


class OverlayWindow(QtWidgets.QWidget):
    settings_requested = QtCore.Signal()
    geometry_changed = QtCore.Signal(int, int, int, int)

    def __init__(self, opts: dict, style: dict | None = None) -> None:
        super().__init__()
        self.opts = opts
        self.style = dict(DEFAULT_STYLE, **(style or {}))
        self.max_hr = int(opts.get("max_hr", 190))
        self.locked = bool(opts.get("locked", False))

        self.hr = collections.deque()          # (t, bpm)
        self.pulse = collections.deque()       # (t, value)
        self.current = None
        self.status = "starting..."
        self.extras: dict = {}                 # e.g. {"HRV": "54 ms"}
        self.pulse_real_until = 0.0            # real PPG recently received
        self._ppg_t = 0.0                      # next real-sample timestamp
        self._phase = 0.0
        self._last_gen = time.monotonic()
        self._beat_env = 0.0
        self._scale_lo = None
        self._scale_hi = None
        self._hover = False
        self._drag = None
        self._resize = None

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

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(1000 / max(10, int(opts.get("fps", 60)))))

    # -- placement / locking ------------------------------------------------
    def _place(self, geometry) -> None:
        if geometry and len(geometry) == 4:
            x, y, w, h = (int(v) for v in geometry)
            self.setGeometry(x, y, max(MIN_SIZE[0], w), max(MIN_SIZE[1], h))
            # keep it on a visible screen if monitors changed
            if QtGui.QGuiApplication.screenAt(self.geometry().center()) is None:
                geometry = None
            else:
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
        self.show()  # changing flags hides the window
        self._apply_win32_styles()

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
            ex = ex | TRANSPARENT if self.locked else ex & ~TRANSPARENT
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        except Exception:
            pass

    # -- style --------------------------------------------------------------
    def apply_style(self, style: dict) -> None:
        self.style = dict(DEFAULT_STYLE, **style)
        self.update()

    def set_max_hr(self, v: int) -> None:
        self.max_hr = int(v)

    # -- data in (GUI thread, via queued signals) ---------------------------
    @QtCore.Slot(int)
    def set_bpm(self, bpm: int) -> None:
        now = time.monotonic()
        self.current = int(bpm)
        self.hr.append((now, int(bpm)))
        cutoff = now - max(10, int(self.style["hr_window_s"])) - 2
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
        """Real PPG samples (already detrended). Timestamps are spread over
        the batch so bursty BLE delivery still scrolls smoothly."""
        if not samples or rate <= 0:
            return
        now = time.monotonic()
        if self._ppg_t < now - 2.0 or self._ppg_t > now + 2.0:
            self._ppg_t = now - len(samples) / rate
        for v in samples:
            self.pulse.append((self._ppg_t, float(v)))
            self._ppg_t += 1.0 / rate
        self.pulse_real_until = now + 3.0
        self._trim_pulse(now)

    def _trim_pulse(self, now: float) -> None:
        cutoff = now - max(2, int(self.style["pulse_window_s"])) - 3
        while self.pulse and self.pulse[0][0] < cutoff:
            self.pulse.popleft()

    @property
    def pulse_is_real(self) -> bool:
        return time.monotonic() < self.pulse_real_until

    # -- animation ----------------------------------------------------------
    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.2, now - self._last_gen)
        bpm = self.current
        if bpm:
            self._phase += dt * bpm / 60.0
            p = self._phase % 1.0
            self._beat_env = max(0.0, 1.0 - p / 0.35) ** 1.6
        else:
            self._beat_env = 0.0
        # beat-synced pulse animation when no real PPG is arriving
        if bpm and not self.pulse_is_real:
            t = self._last_gen
            step = 1.0 / 60.0
            ph = self._phase - dt * bpm / 60.0
            while t + step <= now:
                t += step
                ph += step * bpm / 60.0
                self.pulse.append((t, pulse_template(ph)))
            self._last_gen = t
        else:
            self._last_gen = now
        self._trim_pulse(now)
        self.update()

    # -- geometry of interactive bits --------------------------------------
    def _k(self) -> float:
        return max(0.6, min(self.width() / DEFAULT_SIZE[0], self.height() / DEFAULT_SIZE[1]))

    def _gear_rect(self) -> QtCore.QRectF:
        g = 18 * self._k()
        return QtCore.QRectF(self.width() - g - 6, 6, g, g)

    def _grip_rect(self) -> QtCore.QRectF:
        g = 16 * self._k()
        return QtCore.QRectF(self.width() - g - 3, self.height() - g - 3, g, g)

    # -- mouse --------------------------------------------------------------
    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.unsetCursor()
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        p = event.position()
        if self._gear_rect().contains(p):
            self.settings_requested.emit()
        elif self._grip_rect().contains(p):
            self._resize = (event.globalPosition().toPoint(), self.size())
        else:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        gp = event.globalPosition().toPoint()
        if self._drag is not None:
            self.move(gp - self._drag)
        elif self._resize is not None:
            start, size = self._resize
            d = gp - start
            self.resize(max(MIN_SIZE[0], size.width() + d.x()),
                        max(MIN_SIZE[1], size.height() + d.y()))
        else:
            p = event.position()
            if self._gear_rect().contains(p):
                self.setCursor(QtCore.Qt.PointingHandCursor)
            elif self._grip_rect().contains(p):
                self.setCursor(QtCore.Qt.SizeFDiagCursor)
            else:
                self.setCursor(QtCore.Qt.OpenHandCursor)

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

    # -- painting -----------------------------------------------------------
    def _font(self, px: float, bold: bool = True) -> QtGui.QFont:
        f = QtGui.QFont(self.style["font_family"])
        f.setPixelSize(max(6, int(px * float(self.style["font_scale"]))))
        f.setBold(bold)
        return f

    def _accent(self) -> QtGui.QColor:
        if self.style["color_mode"] == "fixed":
            return QtGui.QColor(self.style["accent_color"])
        return zone_color(self.current, self.max_hr)

    def paintEvent(self, _event) -> None:
        st = self.style
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        w, h, k = self.width(), self.height(), self._k()
        col = self._accent()
        text = QtGui.QColor(st["text_color"])
        muted = QtGui.QColor(st["muted_color"])

        bg = QtGui.QColor(st["bg_color"])
        bg.setAlphaF(max(0.0, min(1.0, float(st["bg_opacity"]))))
        r = float(st["corner_radius"]) * min(1.0, k)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 26), 1))
        p.setBrush(bg)
        p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, w - 1, h - 1), r, r)

        pad = 10 * k
        show_pulse = st["show_pulse_graph"]
        top_h = h * 0.56 if show_pulse else h

        if not self.current:
            self._paint_status(p, pad, h, w, k, text, muted, col)
        else:
            self._paint_top(p, pad, top_h, w, k, text, muted, col)
            if show_pulse:
                self._paint_pulse(p, QtCore.QRectF(pad, top_h, w - 2 * pad, h - top_h - pad),
                                  k, muted, col)

        if self._hover and not self.locked:
            self._paint_controls(p, k, text)
        p.end()

    def _paint_status(self, p, pad, h, w, k, text, muted, col) -> None:
        x = pad
        if self.style["show_heart"]:
            self._draw_heart(p, pad + 12 * k, pad + 14 * k, 10 * k, col)
            x = pad + 28 * k
        p.setFont(self._font(11 * k, bold=False))
        p.setPen(muted)
        p.drawText(QtCore.QRectF(x, pad, w - x - pad - 22 * k, h - 2 * pad),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop | QtCore.Qt.TextWordWrap,
                   self.status)

    def _paint_top(self, p, pad, top_h, w, k, text, muted, col) -> None:
        st = self.style
        stats_h = 14 * k if st["show_stats"] else 0
        row_h = top_h - stats_h                      # number + graph row
        x = pad
        if st["show_heart"]:
            size = row_h * 0.14 * (1.0 + 0.2 * self._beat_env)
            self._draw_heart(p, x + row_h * 0.17, row_h * 0.46, size, col)
            x += row_h * 0.36
        p.setFont(self._font(row_h * 0.5))
        p.setPen(text)
        num = str(self.current)
        num_w = p.fontMetrics().horizontalAdvance(num)
        p.drawText(QtCore.QRectF(x, pad * 0.2, num_w + 4, row_h * 0.8),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, num)
        p.setFont(self._font(row_h * 0.12))
        p.setPen(muted)
        p.drawText(QtCore.QRectF(x + 2, row_h * 0.74, num_w + 40 * k, row_h * 0.22),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "BPM")
        gx = x + max(num_w, 30 * k) + 14 * k
        if st["show_hr_graph"] and w - gx - pad > 30 * k:
            area = QtCore.QRectF(gx, pad, w - gx - pad - (24 * k if self._hover else 0),
                                 row_h - pad * 1.2)
            self._draw_hr_graph(p, area, k, col)
        if st["show_stats"]:
            self._draw_stats(p, QtCore.QRectF(pad, row_h, w - 2 * pad - (18 * k if self._hover else 0),
                                              stats_h), k, muted)

    def _draw_stats(self, p, rect, k, muted) -> None:
        now = time.monotonic()
        win = int(self.style["hr_window_s"])
        vals = [v for t, v in self.hr if t >= now - win]
        parts = []
        if vals:
            parts.append(f"min {min(vals)}  avg {round(sum(vals) / len(vals))}  max {max(vals)}")
        parts += [f"{key} {val}" for key, val in self.extras.items()]
        p.setFont(self._font(9.5 * k, bold=False))
        p.setPen(muted)
        text = p.fontMetrics().elidedText("   ·   ".join(parts), QtCore.Qt.ElideRight,
                                          int(rect.width()))
        p.drawText(rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text)

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
        p.setBrush(QtGui.QColor(255, 255, 255, 60))
        p.drawEllipse(QtCore.QPointF(cx - size * 0.45, cy - size * 0.35), size * 0.28, size * 0.22)
        p.restore()

    def _line(self, p, pts, rect, color, k) -> None:
        area = QtGui.QPainterPath()
        area.moveTo(pts[0].x(), rect.bottom())
        for pt in pts:
            area.lineTo(pt)
        area.lineTo(pts[-1].x(), rect.bottom())
        area.closeSubpath()
        grad = QtGui.QLinearGradient(0, rect.top(), 0, rect.bottom())
        c0, c1 = QtGui.QColor(color), QtGui.QColor(color)
        c0.setAlpha(110)
        c1.setAlpha(6)
        grad.setColorAt(0.0, c0)
        grad.setColorAt(1.0, c1)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(grad)
        p.drawPath(area)
        line = QtGui.QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        p.setBrush(QtCore.Qt.NoBrush)
        p.setPen(QtGui.QPen(color, float(self.style["line_width"]) * max(0.7, k),
                            QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
        p.drawPath(line)

    def _draw_hr_graph(self, p, rect, k, col) -> None:
        now = time.monotonic()
        win = max(5, int(self.style["hr_window_s"]))
        t0 = now - win
        data = [(t, v) for t, v in self.hr if t >= t0]
        if len(data) < 2:
            return
        vals = [v for _, v in data]
        lo, hi = float(min(vals)), float(max(vals))
        if hi - lo < 10:
            mid = (hi + lo) / 2
            lo, hi = mid - 5, mid + 5
        lo, hi = lo - 3, hi + 3
        pts = [QtCore.QPointF(rect.left() + (t - t0) / win * rect.width(),
                              rect.bottom() - (v - lo) / (hi - lo) * rect.height())
               for t, v in data]
        self._line(p, pts, rect, col, k)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(pts[-1], 2.8 * k, 2.8 * k)

    def _paint_pulse(self, p, rect, k, muted, col) -> None:
        real = self.pulse_is_real
        if self.style["show_captions"]:
            p.setFont(self._font(8.5 * k, bold=True))
            p.setPen(muted)
            cap = "PPG  live from band" if real else "PULSE  from heart rate"
            p.drawText(QtCore.QRectF(rect.left(), rect.top(), rect.width(), 12 * k),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, cap)
            rect = rect.adjusted(0, 13 * k, 0, 0)
        now = time.monotonic()
        delay = 1.0 if real else 0.0   # absorb bursty BLE delivery
        win = max(2, int(self.style["pulse_window_s"]))
        t1 = now - delay
        t0 = t1 - win
        data = [(t, v) for t, v in self.pulse if t0 <= t <= t1]
        if len(data) < 3 or rect.height() < 6:
            return
        vals = [v for _, v in data]
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-6:
            hi = lo + 1.0
        # smooth autoscale so the trace doesn't jump around
        a = 0.08
        self._scale_lo = lo if self._scale_lo is None else self._scale_lo + a * (lo - self._scale_lo)
        self._scale_hi = hi if self._scale_hi is None else self._scale_hi + a * (hi - self._scale_hi)
        lo, hi = self._scale_lo, self._scale_hi
        span = (hi - lo) or 1.0
        pts = [QtCore.QPointF(rect.left() + (t - t0) / win * rect.width(),
                              rect.bottom() - max(0.0, min(1.0, (v - lo) / span)) * rect.height())
               for t, v in data]
        self._line(p, pts, rect, col, k)

    def _paint_controls(self, p, k, text) -> None:
        # gear
        g = self._gear_rect()
        c = g.center()
        p.save()
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(0, 0, 0, 120))
        p.drawEllipse(c, g.width() / 2 + 2, g.height() / 2 + 2)
        p.setBrush(text)
        rad = g.width() / 2
        gear = QtGui.QPainterPath()
        for i in range(8):
            ang = i * math.pi / 4
            tooth = QtGui.QPainterPath()
            tw, th = rad * 0.34, rad * 0.32
            tooth.addRect(QtCore.QRectF(-tw / 2, -rad, tw, th))
            tr = QtGui.QTransform().translate(c.x(), c.y()).rotateRadians(ang)
            gear.addPath(tr.map(tooth))
        gear.addEllipse(c, rad * 0.72, rad * 0.72)
        hole = QtGui.QPainterPath()
        hole.addEllipse(c, rad * 0.3, rad * 0.3)
        p.drawPath(gear.simplified().subtracted(hole))
        # resize grip
        gr = self._grip_rect()
        pen = QtGui.QPen(text, max(1.0, 1.4 * k))
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        for i in range(3):
            o = gr.width() * (0.3 + 0.3 * i)
            p.drawLine(QtCore.QPointF(gr.right() - o, gr.bottom()),
                       QtCore.QPointF(gr.right(), gr.bottom() - o))
        p.restore()
