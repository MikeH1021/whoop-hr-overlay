"""Settings window, opened from the overlay's gear. Every change applies live
and is saved immediately (no OK/Cancel)."""
from __future__ import annotations

from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from overlay import DEFAULT_STYLE

MODE_HELP = {
    "passive": ("Passive: reads heart rate from your everyday band while it stays "
                "connected to your phone app. No pairing. The pulse graph is an "
                "animation timed to your live heart rate."),
    "active": ("Active: a dedicated band paired to this PC's USB dongle. Streams the "
               "real optical pulse waveform (PPG), beat-to-beat intervals (live HRV) "
               "and motion. That band can't stay connected to your phone at the same "
               "time, so use a spare one."),
}


class ColorButton(QtWidgets.QPushButton):
    changed = QtCore.Signal(str)

    def __init__(self, color: str) -> None:
        super().__init__()
        self.setFixedWidth(70)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color: str) -> None:
        self.color = color
        self.setStyleSheet(f"background:{color}; border:1px solid #555; min-height:20px;")

    def _pick(self) -> None:
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color), self, "Pick a color")
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(c.name())


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, cfg: dict, whoops: Callable[[], list], on_change: Callable[[str], None],
                 on_reset_geometry: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.style = dict(DEFAULT_STYLE, **cfg.get("style", {}))
        self.whoops = whoops
        self.on_change = on_change           # called with what changed: style/source/window
        self.setWindowTitle("WHOOP HR Overlay settings")
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(420)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._source_tab(), "Source")
        tabs.addTab(self._look_tab(), "Look")
        tabs.addTab(self._graphs_tab(), "Graphs")
        tabs.addTab(self._window_tab(on_reset_geometry), "Window")
        close = QtWidgets.QPushButton("Done")
        close.clicked.connect(self.close)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(tabs)
        note = QtWidgets.QLabel("Changes apply and save immediately.")
        note.setStyleSheet("color:#888;")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(note)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)

    # -- helpers --------------------------------------------------------------
    def _set_style(self, key: str, value) -> None:
        self.style[key] = value
        self.cfg["style"] = dict(self.style)
        self.on_change("style")

    def _set_cfg(self, key: str, value, what: str) -> None:
        self.cfg[key] = value
        self.on_change(what)

    def _slider(self, lo, hi, val, cb, suffix="", scale=1.0) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(int(round(val / scale)))
        lab = QtWidgets.QLabel()
        lab.setMinimumWidth(44)

        def upd(v):
            lab.setText(f"{v * scale:g}{suffix}")
            cb(v * scale)

        s.valueChanged.connect(upd)
        lab.setText(f"{s.value() * scale:g}{suffix}")
        h.addWidget(s, 1)
        h.addWidget(lab)
        return w

    def _check(self, text, key) -> QtWidgets.QCheckBox:
        c = QtWidgets.QCheckBox(text)
        c.setChecked(bool(self.style[key]))
        c.toggled.connect(lambda v: self._set_style(key, bool(v)))
        return c

    def _band_combo(self, key: str) -> QtWidgets.QComboBox:
        cb = QtWidgets.QComboBox()
        cb.setEditable(True)
        cb.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        cur = self.cfg.get(key) or ""

        def refill():
            text = cb.currentText()
            cb.blockSignals(True)
            cb.clear()
            names = sorted(set(self.whoops()))
            for n in names:
                cb.addItem(n, n.split()[-1])
            cb.setEditText(text or cur)
            cb.blockSignals(False)

        refill()
        cb.setEditText(cur)
        cb.setPlaceholderText("serial, e.g. MGB0663331")

        def commit():
            t = cb.currentText().strip()
            serial = t.split()[-1] if t else ""
            if serial != (self.cfg.get(key) or ""):
                self._set_cfg(key, serial or None, "source")

        cb.activated.connect(lambda _i: commit())
        cb.lineEdit().editingFinished.connect(commit)
        cb.refill = refill  # type: ignore[attr-defined]
        return cb

    # -- tabs -------------------------------------------------------------------
    def _source_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        mode = QtWidgets.QComboBox()
        mode.addItem("Passive: everyday band (stays on your phone)", "passive")
        mode.addItem("Active: dedicated band (full sensors)", "active")
        mode.setCurrentIndex(1 if self.cfg.get("hr_mode") == "active" else 0)
        help_lab = QtWidgets.QLabel(MODE_HELP[mode.currentData()])
        help_lab.setWordWrap(True)
        help_lab.setStyleSheet("color:#888;")

        def mode_changed(_i):
            help_lab.setText(MODE_HELP[mode.currentData()])
            self._set_cfg("hr_mode", mode.currentData(), "source")

        mode.currentIndexChanged.connect(mode_changed)
        f.addRow("Mode", mode)
        f.addRow("", help_lab)
        self.passive_band = self._band_combo("serial")
        self.active_band = self._band_combo("active_serial")
        f.addRow("Everyday band", self.passive_band)
        f.addRow("Dedicated band", self.active_band)
        refresh = QtWidgets.QPushButton("Refresh band list")
        refresh.clicked.connect(lambda: (self.passive_band.refill(), self.active_band.refill()))
        f.addRow("", refresh)

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
        maxhr.setValue(int(self.cfg.get("max_hr", 190)))
        maxhr.valueChanged.connect(lambda v: self._set_cfg("max_hr", int(v), "style"))
        f.addRow("Max heart rate", maxhr)
        return w

    def _look_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        font = QtWidgets.QFontComboBox()
        font.setCurrentFont(QtGui.QFont(self.style["font_family"]))
        font.currentFontChanged.connect(lambda fnt: self._set_style("font_family", fnt.family()))
        f.addRow("Font", font)
        f.addRow("Text size", self._slider(50, 200, self.style["font_scale"],
                                           lambda v: self._set_style("font_scale", v), "x", 0.01))

        cmode = QtWidgets.QComboBox()
        cmode.addItem("Heart-rate zones", "zones")
        cmode.addItem("Single color", "fixed")
        cmode.setCurrentIndex(1 if self.style["color_mode"] == "fixed" else 0)
        cmode.currentIndexChanged.connect(
            lambda _i: self._set_style("color_mode", cmode.currentData()))
        f.addRow("Graph color", cmode)
        for label, key in (("Accent color", "accent_color"), ("Text color", "text_color"),
                           ("Secondary text", "muted_color"), ("Background", "bg_color")):
            b = ColorButton(self.style[key])
            b.changed.connect(lambda c, k=key: self._set_style(k, c))
            f.addRow(label, b)
        f.addRow("Background opacity", self._slider(
            0, 100, self.style["bg_opacity"], lambda v: self._set_style("bg_opacity", v), "", 0.01))
        f.addRow("Corner radius", self._slider(
            0, 30, self.style["corner_radius"], lambda v: self._set_style("corner_radius", v), "px"))
        f.addRow("Line width", self._slider(
            5, 50, self.style["line_width"], lambda v: self._set_style("line_width", v), "px", 0.1))
        reset = QtWidgets.QPushButton("Reset look to defaults")
        reset.clicked.connect(self._reset_look)
        f.addRow("", reset)
        return w

    def _reset_look(self) -> None:
        keep = {k: self.style[k] for k in ("show_heart", "show_hr_graph", "show_pulse_graph",
                                           "show_stats", "show_captions", "hr_window_s",
                                           "pulse_window_s")}
        self.style = dict(DEFAULT_STYLE, **keep)
        self.cfg["style"] = dict(self.style)
        self.on_change("style")
        QtWidgets.QMessageBox.information(self, "Reset", "Look reset. Reopen settings to see "
                                          "the default values in the controls.")

    def _graphs_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        for text, key in (("Pulsing heart icon", "show_heart"),
                          ("Heart-rate graph", "show_hr_graph"),
                          ("Pulse waveform graph", "show_pulse_graph"),
                          ("Stats line (min/avg/max, HRV...)", "show_stats"),
                          ("Graph captions", "show_captions")):
            f.addRow(self._check(text, key))
        f.addRow("Heart-rate graph span", self._slider(
            10, 300, self.style["hr_window_s"], lambda v: self._set_style("hr_window_s", int(v)), "s"))
        f.addRow("Pulse graph span", self._slider(
            2, 20, self.style["pulse_window_s"], lambda v: self._set_style("pulse_window_s", int(v)), "s"))
        return w

    def _window_tab(self, on_reset_geometry) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        lock = QtWidgets.QCheckBox("Lock overlay (clicks pass through to the game)")
        lock.setChecked(bool(self.cfg.get("locked", False)))
        lock.toggled.connect(lambda v: self._set_cfg("locked", bool(v), "window"))
        f.addRow(lock)
        hint = QtWidgets.QLabel("While locked there's no gear: unlock it from the heart "
                                "icon in the system tray.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        f.addRow(hint)
        f.addRow("Overall opacity", self._slider(
            20, 100, float(self.cfg.get("opacity", 1.0)),
            lambda v: self._set_cfg("opacity", v, "window"), "", 0.01))
        reset = QtWidgets.QPushButton("Reset size and position")
        reset.clicked.connect(on_reset_geometry)
        f.addRow(reset)
        tip = QtWidgets.QLabel("Move: drag the overlay. Resize: drag its bottom-right corner.")
        tip.setStyleSheet("color:#888;")
        f.addRow(tip)
        return w
