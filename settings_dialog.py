"""Settings window, opened from the overlay's gear (or a double-click). Every
change applies live and saves immediately; there's no OK/Cancel."""
from __future__ import annotations

from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from overlay import DEFAULT_STYLE, THEMES

ACCENT = "#ff4d5e"

QSS = f"""
QDialog {{ background: #14161c; color: #e8ebf1; }}
QWidget {{ font-size: 13px; }}
QLabel {{ color: #e8ebf1; }}
QLabel[hint="true"] {{ color: #8b94a3; font-size: 12px; }}
QLabel[section="true"] {{ color: #8b94a3; font-size: 11px; font-weight: 700;
                         letter-spacing: 1px; padding-top: 6px; }}
QTabWidget::pane {{ border: 1px solid #262b35; border-radius: 10px; background: #181b22;
                    top: -1px; }}
QTabBar::tab {{ background: transparent; color: #8b94a3; padding: 8px 16px; border: none;
                font-weight: 600; }}
QTabBar::tab:selected {{ color: #ffffff; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: #d6dbe4; }}
QCheckBox {{ color: #e8ebf1; spacing: 8px; }}
QComboBox, QSpinBox, QFontComboBox, QLineEdit {{ background: #22262f; border: 1px solid #343b47;
    border-radius: 7px; padding: 5px 8px; color: #e8ebf1; selection-background-color: {ACCENT}; }}
QComboBox:hover, QSpinBox:hover, QFontComboBox:hover {{ border-color: #4a5262; }}
QComboBox QAbstractItemView {{ background: #22262f; color: #e8ebf1; border: 1px solid #343b47;
    selection-background-color: #343b47; }}
QPushButton {{ background: #262b35; border: 1px solid #363d4a; border-radius: 7px;
               padding: 6px 14px; color: #e8ebf1; }}
QPushButton:hover {{ background: #2f3542; }}
QPushButton[primary="true"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white;
                               font-weight: 700; }}
QSlider::groove:horizontal {{ height: 4px; background: #2f3542; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #ffffff; width: 14px; height: 14px; margin: -5px 0;
                              border-radius: 7px; }}
QToolTip {{ background: #22262f; color: #e8ebf1; border: 1px solid #343b47; padding: 4px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: #343b47; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4a5262; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; background: none; }}
QLabel[title="true"] {{ font-size: 20px; font-weight: 800; color: #ffffff; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px;
                        border: 1px solid #4a5262; background: #22262f; }}
QCheckBox::indicator:hover {{ border-color: #6a7385; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
                                image: url(__CHECK__); }}
"""


def _check_icon_path() -> str:
    """Render a white checkmark PNG once (Qt stylesheets can't use data: URIs)."""
    import os
    import tempfile

    path = os.path.join(tempfile.gettempdir(), "whoop-hr-overlay-check.png")
    if not os.path.exists(path):
        pm = QtGui.QPixmap(32, 32)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor("white"), 4.2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap,
                         QtCore.Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline([QtCore.QPointF(8, 17), QtCore.QPointF(13.5, 22.5), QtCore.QPointF(24, 10)])
        p.end()
        pm.save(path)
    return path.replace("\\", "/")

MODE_HELP = {
    "passive": ("Reads heart rate from your everyday band while it stays connected to your "
                "phone app. No pairing. The pulse graph is drawn from your live heart rate."),
    "active": ("A dedicated spare band paired to this PC. Streams the real optical pulse "
               "waveform (PPG), beat-to-beat intervals (live HRV) and motion. A band can only "
               "talk to one app at a time, so use one that isn't on your phone."),
}


class ColorButton(QtWidgets.QPushButton):
    changed = QtCore.Signal(str)

    def __init__(self, color: str, tip: str = "") -> None:
        super().__init__()
        self.setFixedSize(64, 26)
        self.setToolTip(tip or "Click to pick a color")
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color: str) -> None:
        self.color = color
        self.setStyleSheet(f"QPushButton{{background:{color}; border:1px solid #4a5262;"
                           f"border-radius:7px;}}")

    def _pick(self) -> None:
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color), self, "Pick a color")
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(c.name())


class ThemeCard(QtWidgets.QAbstractButton):
    """A clickable miniature of a theme."""

    def __init__(self, name: str, theme: dict) -> None:
        super().__init__()
        self.name, self.theme = name, theme
        self.setCheckable(True)
        self.setFixedSize(112, 70)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(f"{name} theme")

    def paintEvent(self, _e) -> None:
        t = dict(DEFAULT_STYLE, **self.theme)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(3, 3, self.width() - 6, self.height() - 20)
        # checkerboard so transparency reads as transparency
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#3c4656"))
        p.drawRoundedRect(r, 8, 8)
        bg = QtGui.QColor(t["bg_color"])
        bg.setAlphaF(max(0.0, float(t["bg_opacity"])))
        p.setBrush(bg)
        p.drawRoundedRect(r, min(8, t["corner_radius"]), min(8, t["corner_radius"]))
        acc = QtGui.QColor(t["accent_color"] if t["color_mode"] == "fixed" else "#3ddc97")
        f = QtGui.QFont(t["font_family"])
        f.setPixelSize(20)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QtGui.QColor(t["text_color"]))
        p.drawText(QtCore.QRectF(r.left() + 8, r.top(), 40, r.height()),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, "72")
        path = QtGui.QPainterPath(QtCore.QPointF(r.left() + 50, r.center().y() + 6))
        for i, dy in enumerate((2, -8, 6, -3, 4, -10, 5)):
            path.lineTo(r.left() + 56 + i * 7, r.center().y() + dy)
        p.setPen(QtGui.QPen(acc, 2))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawPath(path)
        if self.isChecked():
            p.setPen(QtGui.QPen(QtGui.QColor(ACCENT), 2))
            p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), 9, 9)
        p.setPen(QtGui.QColor("#ffffff" if self.isChecked() else "#8b94a3"))
        f2 = QtGui.QFont()
        f2.setPixelSize(11)
        f2.setBold(self.isChecked())
        p.setFont(f2)
        p.drawText(QtCore.QRectF(0, self.height() - 16, self.width(), 14), QtCore.Qt.AlignCenter,
                   self.name)


def _section(text: str) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(text.upper())
    lab.setProperty("section", True)
    return lab


def _hint(text: str) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(text)
    lab.setWordWrap(True)
    lab.setProperty("hint", True)
    return lab


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, cfg: dict, whoops: Callable[[], list], on_change: Callable[[str], None],
                 on_reset_geometry: Callable[[], None], parent=None,
                 start_tab: str = "") -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.style = dict(DEFAULT_STYLE, **cfg.get("style", {}))
        self.whoops = whoops
        self.on_change = on_change
        self.on_reset_geometry = on_reset_geometry
        self.setWindowTitle("WHOOP HR Overlay")
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        self.setStyleSheet(QSS.replace("__CHECK__", _check_icon_path()))
        self.setMinimumSize(470, 520)

        head = QtWidgets.QLabel("Settings")
        head.setProperty("title", True)
        sub = _hint("Changes apply instantly and are saved automatically.")

        self._seen = set(self.whoops())
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._scroll(self._source_tab()), "Source")
        self.tabs.addTab(self._scroll(self._look_tab()), "Look")
        self.tabs.addTab(self._scroll(self._graphs_tab()), "Graphs")
        self.tabs.addTab(self._scroll(self._window_tab()), "Window")
        # Band lists update live as the dongle hears bands (no Rescan needed).
        self._live = QtCore.QTimer(self)
        self._live.timeout.connect(self._refresh_bands)
        self._live.start(1500)
        names = [self.tabs.tabText(i).lower() for i in range(self.tabs.count())]
        if start_tab.lower() in names:
            self.tabs.setCurrentIndex(names.index(start_tab.lower()))

        done = QtWidgets.QPushButton("Done")
        done.setProperty("primary", True)
        done.setDefault(True)
        done.clicked.connect(self.close)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(head)
        lay.addWidget(sub)
        lay.addWidget(self.tabs, 1)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(done)
        lay.addLayout(row)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _scroll(w: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        sa = QtWidgets.QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(w)
        w.setStyleSheet("background: transparent;")
        return sa

    def _form(self) -> tuple:
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        f.setContentsMargins(16, 12, 16, 12)
        f.setHorizontalSpacing(14)
        f.setVerticalSpacing(10)
        f.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        return w, f

    APPEARANCE = {"font_family", "font_scale", "text_color", "muted_color", "bg_color",
                  "bg_opacity", "bg_gradient", "border_alpha", "color_mode", "accent_color",
                  "corner_radius", "line_width", "glow", "text_shadow"}

    def _set_style(self, key: str, value) -> None:
        self.style[key] = value
        if key in self.APPEARANCE:
            self.style["theme"] = "Custom"
            for c in getattr(self, "cards", []):
                c.setChecked(False)
        self.cfg["style"] = dict(self.style)
        self.on_change("style")

    def _set_cfg(self, key: str, value, what: str) -> None:
        self.cfg[key] = value
        self.on_change(what)

    def _slider(self, lo, hi, val, cb, suffix="", scale=1.0, tip="") -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(int(round(val / scale)))
        s.setToolTip(tip)
        lab = QtWidgets.QLabel()
        lab.setMinimumWidth(48)
        lab.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        def fmt(v):
            x = v * scale
            return f"{x:.0%}" if suffix == "%" else f"{x:g}{suffix}"

        def upd(v):
            lab.setText(fmt(v))
            cb(v * scale)

        s.valueChanged.connect(upd)
        lab.setText(fmt(s.value()))
        h.addWidget(s, 1)
        h.addWidget(lab)
        return w

    def _check(self, text, key, tip="") -> QtWidgets.QCheckBox:
        c = QtWidgets.QCheckBox(text)
        c.setChecked(bool(self.style[key]))
        c.setToolTip(tip)
        c.toggled.connect(lambda v: self._set_style(key, bool(v)))
        return c

    def _refresh_bands(self) -> None:
        now = set(self.whoops())
        if now == self._seen:
            return
        self._seen = now
        for cb in (self.passive_band, self.active_band):
            # don't yank the list out from under the user mid-pick
            if cb.view().isVisible() or cb.lineEdit().hasFocus():
                self._seen = set()   # try again next tick
                continue
            cb.refill()
        self.band_count.setText(self._band_count_text())

    def _band_count_text(self) -> str:
        n = len(set(self.whoops()))
        return (f"{n} band{'s' if n != 1 else ''} heard nearby; the list updates by itself."
                if n else "Listening for bands... the list fills in by itself.")

    def _band_combo(self, key: str, tip: str) -> QtWidgets.QComboBox:
        cb = QtWidgets.QComboBox()
        cb.setEditable(True)
        cb.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        cb.setToolTip(tip)
        cur = self.cfg.get(key) or ""

        def refill():
            text = cb.currentText()
            cb.blockSignals(True)
            cb.clear()
            for n in sorted(set(self.whoops())):
                cb.addItem(n, n.split()[-1])
            cb.setEditText(text or cur)
            cb.blockSignals(False)

        refill()
        cb.setEditText(cur)
        cb.lineEdit().setPlaceholderText("Pick from the list or type a serial")

        def commit():
            t = cb.currentText().strip()
            serial = t.split()[-1] if t else ""
            if serial != (self.cfg.get(key) or ""):
                self._set_cfg(key, serial or None, "source")

        cb.activated.connect(lambda _i: commit())
        cb.lineEdit().editingFinished.connect(commit)
        cb.refill = refill  # type: ignore[attr-defined]
        return cb

    # ------------------------------------------------------------------ tabs
    def _source_tab(self) -> QtWidgets.QWidget:
        w, f = self._form()
        f.addRow(_section("Mode"))
        mode = QtWidgets.QComboBox()
        mode.addItem("Passive: everyday band", "passive")
        mode.addItem("Active: dedicated band (full sensors)", "active")
        mode.setCurrentIndex(1 if self.cfg.get("hr_mode") == "active" else 0)
        help_lab = _hint(MODE_HELP[mode.currentData()])

        def mode_changed(_i):
            help_lab.setText(MODE_HELP[mode.currentData()])
            self._set_cfg("hr_mode", mode.currentData(), "source")

        mode.currentIndexChanged.connect(mode_changed)
        f.addRow("Mode", mode)
        f.addRow("", help_lab)

        f.addRow(_section("Bands"))
        self.passive_band = self._band_combo("serial", "The band you wear every day (Passive)")
        self.active_band = self._band_combo("active_serial", "A spare band for Active mode")
        f.addRow("Everyday band", self.passive_band)
        f.addRow("Dedicated band", self.active_band)
        self.band_count = _hint(self._band_count_text())
        f.addRow("", self.band_count)
        f.addRow("", _hint("Only the band you pick is ever connected to. Other WHOOPs nearby "
                           "are listed but left alone."))

        f.addRow(_section("Connection"))
        radio = QtWidgets.QComboBox()
        for label, val in (("Automatic (USB dongle if plugged in)", "auto"),
                           ("USB dongle", "dongle"), ("Built-in Bluetooth", "system")):
            radio.addItem(label, val)
        radio.setCurrentIndex(max(0, radio.findData(self.cfg.get("adapter", "auto"))))
        radio.currentIndexChanged.connect(
            lambda _i: self._set_cfg("adapter", radio.currentData(), "source"))
        f.addRow("Bluetooth", radio)
        maxhr = QtWidgets.QSpinBox()
        maxhr.setRange(120, 230)
        maxhr.setSuffix(" bpm")
        maxhr.setValue(int(self.cfg.get("max_hr", 190)))
        maxhr.setToolTip("Sets where the heart-rate zone colors change (roughly 220 minus age)")
        maxhr.valueChanged.connect(lambda v: self._set_cfg("max_hr", int(v), "style"))
        f.addRow("Max heart rate", maxhr)
        return w

    def _look_tab(self) -> QtWidgets.QWidget:
        w, f = self._form()
        f.addRow(_section("Theme"))
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(8)
        self.cards = []
        group = QtWidgets.QButtonGroup(w)
        group.setExclusive(False)
        for i, (name, theme) in enumerate(THEMES.items()):
            card = ThemeCard(name, theme)
            card.setChecked(self.style.get("theme") == name)
            card.clicked.connect(lambda _c=False, n=name: self._apply_theme(n))
            grid.addWidget(card, i // 3, i % 3)
            self.cards.append(card)
        gw = QtWidgets.QWidget()
        gw.setLayout(grid)
        f.addRow(gw)
        f.addRow(_hint("Pick a theme, then fine-tune anything below. Tweaks turn it into "
                       "your own Custom theme."))

        f.addRow(_section("Text"))
        self.font_box = QtWidgets.QFontComboBox()
        self.font_box.setCurrentFont(QtGui.QFont(self.style["font_family"]))
        self.font_box.currentFontChanged.connect(
            lambda fnt: self._set_style("font_family", fnt.family()))
        f.addRow("Font", self.font_box)
        f.addRow("Text size", self._slider(50, 200, self.style["font_scale"],
                                           lambda v: self._set_style("font_scale", v), "%", 0.01))
        self.color_btns = {}
        for label, key, tip in (("Text", "text_color", "Big number and titles"),
                                ("Secondary", "muted_color", "Labels, stats and captions")):
            b = ColorButton(self.style[key], tip)
            b.changed.connect(lambda c, k=key: self._set_style(k, c))
            self.color_btns[key] = b
            f.addRow(label, b)
        f.addRow(self._check("Text shadow (helps on see-through themes)", "text_shadow"))

        f.addRow(_section("Color"))
        self.cmode = QtWidgets.QComboBox()
        self.cmode.addItem("Heart-rate zones (changes with effort)", "zones")
        self.cmode.addItem("Single accent color", "fixed")
        self.cmode.setCurrentIndex(1 if self.style["color_mode"] == "fixed" else 0)
        self.cmode.currentIndexChanged.connect(
            lambda _i: self._set_style("color_mode", self.cmode.currentData()))
        f.addRow("Graph color", self.cmode)
        b = ColorButton(self.style["accent_color"], "Used when 'Single accent color' is on")
        b.changed.connect(lambda c: self._set_style("accent_color", c))
        self.color_btns["accent_color"] = b
        f.addRow("Accent", b)
        f.addRow(self._check("Glow on lines and heart", "glow"))

        f.addRow(_section("Panel"))
        b = ColorButton(self.style["bg_color"], "Panel background")
        b.changed.connect(lambda c: self._set_style("bg_color", c))
        self.color_btns["bg_color"] = b
        f.addRow("Background", b)
        f.addRow("Opacity", self._slider(0, 100, self.style["bg_opacity"],
                                         lambda v: self._set_style("bg_opacity", v), "%", 0.01))
        f.addRow(self._check("Subtle gradient", "bg_gradient"))
        f.addRow("Border", self._slider(0, 60, self.style["border_alpha"],
                                        lambda v: self._set_style("border_alpha", v), "%", 0.01))
        f.addRow("Corner radius", self._slider(
            0, 30, self.style["corner_radius"], lambda v: self._set_style("corner_radius", v), "px"))
        f.addRow("Line width", self._slider(
            5, 50, self.style["line_width"], lambda v: self._set_style("line_width", v), "px", 0.1))
        return w

    def _apply_theme(self, name: str) -> None:
        self.style.update(THEMES[name])
        self.style["theme"] = name
        self.cfg["style"] = dict(self.style)
        self.on_change("style")
        # rebuild the Look tab so every control reflects the theme's values
        idx = self.tabs.currentIndex()
        self.tabs.blockSignals(True)
        self.tabs.removeTab(1)
        self.tabs.insertTab(1, self._scroll(self._look_tab()), "Look")
        self.tabs.setCurrentIndex(idx)
        self.tabs.blockSignals(False)

    def _graphs_tab(self) -> QtWidgets.QWidget:
        w, f = self._form()
        f.addRow(_section("Show"))
        for text, key, tip in (
                ("Pulsing heart", "show_heart", "The heart icon that beats with you"),
                ("Heart-rate graph", "show_hr_graph", "Rolling heart-rate trend"),
                ("Pulse waveform", "show_pulse_graph", "PPG (Active) or beat animation (Passive)"),
                ("Stats line", "show_stats", "min / avg / max, plus HRV and motion in Active mode"),
                ("Zone label", "show_zone", "e.g. Z3 AEROBIC under the number (zone colors only)"),
                ("Graph captions", "show_captions", "Small label above the pulse graph"),
                ("Grid lines", "show_grid", "Faint guide lines behind the heart-rate graph")):
            f.addRow(self._check(text, key, tip))
        f.addRow(_section("Stream effects"))
        for text, key, tip in (
                ("Beat shockwaves", "fx_beat_rings", "A ring ripples out of the heart on every beat"),
                ("Spike alerts", "fx_spikes", "Shake, flash and a '+18 BPM SPIKE' callout when your "
                                              "heart rate jumps fast"),
                ("Mood tags", "fx_mood", "ICE COLD / LOCKED IN / HEART RACING / CLUTCH!"),
                ("Session peak marker", "show_peak", "Marks your highest heart rate on the graph")):
            f.addRow(self._check(text, key, tip))
        f.addRow("Spike trigger", self._slider(
            6, 30, self.style["spike_bpm"], lambda v: self._set_style("spike_bpm", int(v)), " bpm",
            tip="How big a jump within 8 seconds counts as a spike"))
        f.addRow(_section("Time spans"))
        f.addRow("Heart-rate graph", self._slider(
            10, 300, self.style["hr_window_s"], lambda v: self._set_style("hr_window_s", int(v)), "s"))
        f.addRow("Pulse graph", self._slider(
            2, 20, self.style["pulse_window_s"], lambda v: self._set_style("pulse_window_s", int(v)), "s"))
        f.addRow(_hint("Small sizes hide the pulse graph and stats automatically so the "
                       "number stays readable."))
        return w

    def _window_tab(self) -> QtWidgets.QWidget:
        w, f = self._form()
        f.addRow(_section("Interaction"))
        lock = QtWidgets.QCheckBox("Lock overlay (clicks pass through to the game)")
        lock.setChecked(bool(self.cfg.get("locked", False)))
        lock.toggled.connect(lambda v: self._set_cfg("locked", bool(v), "window"))
        f.addRow(lock)
        f.addRow(_hint("While locked there's no gear. Unlock from the heart icon in the "
                       "system tray."))
        snap = QtWidgets.QCheckBox("Snap to screen edges while dragging")
        snap.setChecked(bool(self.cfg.get("snap", True)))
        snap.toggled.connect(lambda v: self._set_cfg("snap", bool(v), "window"))
        f.addRow(snap)
        f.addRow(_section("Appearance"))
        f.addRow("Overall opacity", self._slider(
            20, 100, float(self.cfg.get("opacity", 1.0)),
            lambda v: self._set_cfg("opacity", v, "window"), "%", 0.01))
        f.addRow(_section("Size and position"))
        reset = QtWidgets.QPushButton("Reset size and position")
        reset.clicked.connect(self.on_reset_geometry)
        f.addRow(reset)
        f.addRow(_hint("Move: drag the overlay. Resize: drag its bottom-right corner. "
                       "Settings: the gear, or double-click the overlay."))
        return w
