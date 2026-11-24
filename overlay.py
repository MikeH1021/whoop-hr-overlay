"""The on-screen overlay widget: a heart-rate number, a pulsing heart, and a
rolling ~30 second sparkline, drawn on a translucent, always-on-top,
click-through window so it rides over a game and shows up in a full-screen
Discord / OBS share.
"""
from __future__ import annotations

import collections
import platform
import time

from PySide6 import QtCore, QtGui, QtWidgets

WINDOW_SECONDS = 30  # width of the rolling graph


def zone_color(bpm, max_hr) -> QtGui.QColor:
    """Color the number/graph by effort zone (% of max HR)."""
    if not bpm or not max_hr:
        return QtGui.QColor("#9aa4b2")
    pct = bpm / float(max_hr)
    for thr, col in (
        (0.50, "#6ea8fe"),  # recovery  - blue
        (0.60, "#48cae4"),  # warm up   - cyan
        (0.70, "#3ddc97"),  # aerobic   - green
        (0.80, "#ffd166"),  # tempo     - yellow
        (0.90, "#ff9f45"),  # threshold - orange
    ):
        if pct < thr:
            return QtGui.QColor(col)
    return QtGui.QColor("#ff5d6c")  # max - red


class OverlayWindow(QtWidgets.QWidget):
    def __init__(self, opts: dict) -> None:
        super().__init__()
        self.opts = opts
        self.max_hr = int(opts["max_hr"])
        self.scale = float(opts["scale"])

        self.samples: "collections.deque[tuple[float, int]]" = collections.deque()
        self.current = None
        self.status = "starting..."
        self.last_beat = time.monotonic()
        self.beat_env = 0.0  # 0..1 pulse envelope for the heart
        self._drag_offset = None

        self._w = int(round(272 * self.scale))
        self._h = int(round(104 * self.scale))

        self._init_window()
        self._place()

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(1000 / max(10, int(opts["fps"]))))

    # -- window setup ------------------------------------------------------
    def _init_window(self) -> None:
        flags = (
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        if not self.opts["movable"]:
            flags |= QtCore.Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("WHOOP HR Overlay")
        self.resize(self._w, self._h)
        self.setWindowOpacity(float(self.opts["opacity"]))

    def _place(self) -> None:
        screen = QtWidgets.QApplication.primaryScreen()
        geo = screen.availableGeometry()
        m = int(self.opts["margin"])
        pos = str(self.opts["position"])
        x = geo.right() - self._w - m if "right" in pos else geo.left() + m
        y = geo.bottom() - self._h - m if "bottom" in pos else geo.top() + m
        self.move(x, y)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if platform.system() == "Windows" and not self.opts["movable"]:
            self._apply_windows_click_through()

    def _apply_windows_click_through(self) -> None:
        """Belt-and-suspenders click-through on Windows via extended styles."""
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex |= (
                WS_EX_LAYERED
                | WS_EX_TRANSPARENT
                | WS_EX_TOOLWINDOW
                | WS_EX_NOACTIVATE
            )
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        except Exception:
            pass

    # -- data in (called on the GUI thread via queued signals) -------------
    @QtCore.Slot(int)
    def set_bpm(self, bpm: int) -> None:
        now = time.monotonic()
        self.current = int(bpm)
        self.samples.append((now, int(bpm)))
        self.last_beat = now  # resync the pulse to a fresh reading
        cutoff = now - WINDOW_SECONDS
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

    @QtCore.Slot(str)
    def set_status(self, text: str) -> None:
        self.status = text

    # -- animation ---------------------------------------------------------
    def _tick(self) -> None:
        now = time.monotonic()
        if self.current:
            interval = 60.0 / max(30, min(220, self.current))
            if now - self.last_beat >= interval:
                self.last_beat = now
            frac = (now - self.last_beat) / interval
            self.beat_env = max(0.0, 1.0 - frac) ** 1.6
        else:
            self.beat_env = 0.0
        self.update()

    # -- painting ----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        s = self.scale
        w, h = self.width(), self.height()
        col = zone_color(self.current, self.max_hr)

        # background panel
        p.setBrush(QtGui.QColor(12, 14, 20, 185))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 30), 1))
        p.drawRoundedRect(
            QtCore.QRectF(0.5, 0.5, w - 1, h - 1), 16 * s, 16 * s
        )
        # thin accent bar in the zone color
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(
            QtCore.QRectF(0, 10 * s, 4 * s, h - 20 * s), 2 * s, 2 * s
        )

        # pulsing heart
        heart_scale = 1.0 + 0.20 * self.beat_env
        self._draw_heart(p, 30 * s, h * 0.42, 12 * s * heart_scale, col)

        # big bpm number
        num = str(self.current) if self.current else "--"
        f_num = QtGui.QFont("Segoe UI", int(27 * s))
        f_num.setBold(True)
        p.setFont(f_num)
        p.setPen(QtGui.QColor(246, 248, 251))
        p.drawText(
            QtCore.QRectF(48 * s, 6 * s, 86 * s, h - 30 * s),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            num,
        )
        # "BPM" + status line under the number
        f_small = QtGui.QFont("Segoe UI", int(8 * s))
        f_small.setBold(True)
        p.setFont(f_small)
        p.setPen(QtGui.QColor(150, 160, 176))
        p.drawText(
            QtCore.QRectF(49 * s, h - 26 * s, 90 * s, 20 * s),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            "BPM",
        )

        # rolling sparkline
        gx, gy = 140 * s, 14 * s
        gw, gh = w - gx - 14 * s, h - 28 * s
        self._draw_sparkline(p, gx, gy, gw, gh, col)

        # before the first reading arrives, show the connection status in the
        # graph area so a double-clicked (console-less) exe still explains itself
        if not self.current:
            p.setFont(f_small)
            p.setPen(QtGui.QColor(150, 160, 176))
            p.drawText(
                QtCore.QRectF(gx, gy, gw, gh),
                QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
                self.status,
            )

    def _draw_heart(self, p, cx, cy, size, color) -> None:
        path = QtGui.QPainterPath()
        path.moveTo(cx, cy + size * 0.72)
        path.cubicTo(
            cx - size * 1.45, cy - size * 0.25,
            cx - size * 0.60, cy - size * 1.20,
            cx, cy - size * 0.35,
        )
        path.cubicTo(
            cx + size * 0.60, cy - size * 1.20,
            cx + size * 1.45, cy - size * 0.25,
            cx, cy + size * 0.72,
        )
        p.save()
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path)
        # soft inner highlight
        hi = QtGui.QColor(255, 255, 255, 60)
        p.setBrush(hi)
        p.drawEllipse(
            QtCore.QPointF(cx - size * 0.45, cy - size * 0.35),
            size * 0.28, size * 0.22,
        )
        p.restore()

    def _draw_sparkline(self, p, x, y, w, h, color) -> None:
        now = time.monotonic()
        t0 = now - WINDOW_SECONDS
        pts_data = [(t, v) for t, v in self.samples if t >= t0]

        if len(pts_data) < 2:
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 45), 1))
            p.drawLine(
                QtCore.QPointF(x, y + h / 2), QtCore.QPointF(x + w, y + h / 2)
            )
            return

        vals = [v for _t, v in pts_data]
        vmin, vmax = float(min(vals)), float(max(vals))
        if vmax - vmin < 10:
            mid = (vmax + vmin) / 2.0
            vmin, vmax = mid - 6, mid + 6
        vmin -= 3
        vmax += 3
        span = max(1.0, vmax - vmin)

        def px(t):
            return x + (t - t0) / WINDOW_SECONDS * w

        def py(v):
            return y + h - (v - vmin) / span * h

        pts = [QtCore.QPointF(px(t), py(v)) for t, v in pts_data]

        # filled area under the line
        area = QtGui.QPainterPath()
        area.moveTo(pts[0].x(), y + h)
        for pt in pts:
            area.lineTo(pt)
        area.lineTo(pts[-1].x(), y + h)
        area.closeSubpath()
        grad = QtGui.QLinearGradient(0, y, 0, y + h)
        c0 = QtGui.QColor(color)
        c0.setAlpha(120)
        c1 = QtGui.QColor(color)
        c1.setAlpha(8)
        grad.setColorAt(0.0, c0)
        grad.setColorAt(1.0, c1)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(grad)
        p.drawPath(area)

        # the line itself
        line = QtGui.QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        p.setBrush(QtCore.Qt.NoBrush)
        p.setPen(
            QtGui.QPen(
                color,
                2.0 * self.scale,
                QtCore.Qt.SolidLine,
                QtCore.Qt.RoundCap,
                QtCore.Qt.RoundJoin,
            )
        )
        p.drawPath(line)

        # leading dot
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(color)
        r = 2.8 * self.scale
        p.drawEllipse(pts[-1], r, r)

    # -- movable mode (only when --movable) --------------------------------
    def mousePressEvent(self, event) -> None:
        if self.opts["movable"] and event.button() == QtCore.Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if (
            self.opts["movable"]
            and self._drag_offset is not None
            and event.buttons() & QtCore.Qt.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            QtWidgets.QApplication.quit()
