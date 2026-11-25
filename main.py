"""WHOOP Heart Rate Overlay -- entry point.

Wires a heart-rate source (real BLE or a simulator) to the on-screen overlay.
BLE runs on a background thread; readings cross onto the Qt GUI thread through
a small signal bridge.

Examples
--------
    python main.py --simulate                 # no hardware, demo the look
    python main.py --list                      # scan and print nearby HR straps
    python main.py --name WHOOP                 # connect to a strap named *WHOOP*
    python main.py --address AA:BB:CC:DD:EE:FF  # connect to a specific address
"""
from __future__ import annotations

import argparse
import json
import os
import sys

def base_dir() -> str:
    """Folder to look for config.json / write logs: next to the exe when
    frozen by PyInstaller, else next to this script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


HERE = base_dir()


def _ensure_console() -> None:
    """A --windowed exe has no console, so --list output goes nowhere. On
    Windows, attach/allocate one and reopen stdout so prints are visible."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        if k32.GetConsoleWindow() == 0 and not k32.AttachConsole(-1):
            k32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8")
        sys.stderr = sys.stdout
    except Exception:
        pass


def _make_tray_icon():
    """A small red heart drawn at runtime (no icon file to bundle)."""
    from PySide6 import QtCore, QtGui

    pm = QtGui.QPixmap(64, 64)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    cx, cy, s = 32.0, 34.0, 22.0
    path = QtGui.QPainterPath()
    path.moveTo(cx, cy + s * 0.72)
    path.cubicTo(cx - s * 1.45, cy - s * 0.25, cx - s * 0.6, cy - s * 1.2, cx, cy - s * 0.35)
    path.cubicTo(cx + s * 0.6, cy - s * 1.2, cx + s * 1.45, cy - s * 0.25, cx, cy + s * 0.72)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor("#ff4d5e"))
    p.drawPath(path)
    p.end()
    return QtGui.QIcon(pm)


def load_config(path: str | None) -> dict:
    """Load config.json: an explicit --config, else one next to the exe/script."""
    if path:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        return {}
    default = os.path.join(HERE, "config.json")
    if os.path.exists(default):
        with open(default, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_config(cfg: dict) -> None:
    """Write config.json next to the exe/script (used by the tray band picker)."""
    try:
        with open(os.path.join(HERE, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except Exception:
        pass


def build_opts(args: argparse.Namespace, cfg: dict) -> dict:
    """CLI flags override config.json, which overrides built-in defaults."""

    def pick(key, default):
        val = getattr(args, key, None)
        if val is not None:
            return val
        if key in cfg:
            return cfg[key]
        return default

    return {
        "max_hr": int(pick("max_hr", 190)),
        "position": pick("position", "top-left"),
        "margin": int(pick("margin", 24)),
        "opacity": float(pick("opacity", 1.0)),
        "scale": float(pick("scale", 1.0)),
        "fps": int(pick("fps", 60)),
        "movable": bool(pick("movable", False)),
        "geometry": cfg.get("geometry"),
        "locked": bool(cfg.get("locked", False)),
        "snap": bool(cfg.get("snap", True)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="whoop-hr-overlay",
        description="Live WHOOP / BLE heart-rate overlay for game screen-shares.",
    )
    ap.add_argument("--simulate", action="store_true",
                    help="use a fake HR signal instead of BLE (great for testing)")
    ap.add_argument("--serial", help="your WHOOP's serial, e.g. MGB0663331 (default: nearest WHOOP)")
    ap.add_argument("--mode", choices=["passive", "active"],
                    help="passive: everyday band (HR only); active: dedicated band (PPG, HRV)")
    ap.add_argument("--active-serial", help="serial of the dedicated band for active mode")
    ap.add_argument("--adapter", choices=["auto", "dongle", "system"],
                    help="Bluetooth radio: auto (USB dongle if present), dongle, or system")
    ap.add_argument("--standard", action="store_true",
                    help="use any standard BLE heart-rate sensor instead of a WHOOP")
    ap.add_argument("--address", help="connect to this BLE address directly")
    ap.add_argument("--name", help="connect to the first device whose name contains this")
    ap.add_argument("--list", action="store_true",
                    help="scan for BLE heart-rate devices, print them, and exit")
    ap.add_argument("--scan-timeout", type=float, default=8.0,
                    help="seconds to scan when discovering (default 8)")
    ap.add_argument("--max-hr", type=int, help="your max HR, for zone colors (default 190)")
    ap.add_argument("--position",
                    choices=["top-left", "top-right", "bottom-left", "bottom-right"],
                    help="screen corner (default top-left)")
    ap.add_argument("--margin", type=int, help="pixels from the screen edge (default 24)")
    ap.add_argument("--opacity", type=float, help="0.0-1.0 window opacity (default 0.96)")
    ap.add_argument("--scale", type=float, help="size multiplier (default 1.0)")
    ap.add_argument("--fps", type=int, help="repaint rate (default 60)")
    ap.add_argument("--movable", action="store_true", default=None,
                    help="make the overlay draggable (disables click-through)")
    ap.add_argument("--config", help="path to a config.json")
    args = ap.parse_args()

    if args.list:
        _ensure_console()  # windowed exe has no console; make one so prints show
        from hr_source import list_devices

        print("Scanning for BLE heart-rate devices...\n")
        rows = list_devices(args.scan_timeout)
        if not rows:
            print("  (nothing found -- is the strap awake and broadcasting?)")
        for rssi, addr, name, is_hr in rows:
            tag = "  <- Heart Rate service" if is_hr else ""
            print(f"  {rssi:>4} dBm   {addr}   {name}{tag}")
        try:
            input("\nDone. Press Enter to close...")
        except Exception:
            pass
        return

    cfg = load_config(args.config)
    if args.serial:
        cfg["serial"] = args.serial
    if args.mode:
        cfg["hr_mode"] = args.mode
    if args.active_serial:
        cfg["active_serial"] = args.active_serial
    opts = build_opts(args, cfg)

    from PySide6 import QtCore, QtGui, QtWidgets

    from hr_source import (
        BleHeartRateSource,
        SimulatedHeartRateSource,
        WhoopDirectSource,
        default_log_path,
    )
    from overlay import OverlayWindow

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # the tray keeps us alive; quit is explicit

    # Single instance: if an older copy is running, tell it to exit first, so
    # launching a new version always replaces the old one.
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    srv_name = "whoop-hr-overlay-singleton"
    probe = QLocalSocket()
    probe.connectToServer(srv_name)
    if probe.waitForConnected(300):
        probe.write(b"quit\n")
        probe.flush()
        probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        QtCore.QThread.msleep(800)  # let it release its window + Bluetooth
    QLocalServer.removeServer(srv_name)
    server = QLocalServer()
    server.listen(srv_name)

    def on_peer() -> None:
        conn = server.nextPendingConnection()
        if conn is None:
            return

        def read() -> None:
            if bytes(conn.readAll()).strip() == b"quit":
                QtWidgets.QApplication.quit()

        conn.readyRead.connect(read)
        if conn.bytesAvailable():
            read()

    server.newConnection.connect(on_peer)

    win = OverlayWindow(opts, cfg.get("style"))

    class Bridge(QtCore.QObject):
        bpm = QtCore.Signal(int)
        status = QtCore.Signal(str)
        pulse = QtCore.Signal(list, float)
        extras = QtCore.Signal(dict)

    bridge = Bridge()
    bridge.bpm.connect(win.set_bpm)
    bridge.status.connect(win.set_status)
    bridge.pulse.connect(win.add_pulse)
    bridge.extras.connect(win.set_extras)

    # LAN diagnostics beacon: each scan + status change is broadcast as JSON on
    # the local network (UDP 47999) so the developer can see what this PC sees.
    beacon = LanBeacon(enabled=bool(cfg.get("lan_diagnostics", True)))
    bridge.status.connect(beacon.status)

    import hr_source as _hs

    latest_whoops: list = []

    def on_scan(rows) -> None:
        names = [r["name"] for r in rows
                 if r["name"] and _hs.looks_like_whoop(r["name"], r["uuids"], r["mfg"])]
        latest_whoops[:] = sorted(set(latest_whoops) | set(names))
        beacon.scan(rows)

    _hs.SCAN_LISTENERS.append(on_scan)
    _hs.LOG_LISTENERS.append(beacon.log)
    bridge.extras.connect(beacon.extras)

    holder = {"source": None, "gen": 0}

    # Which Bluetooth radio to use: "auto" picks a Zephyr hci_usb USB dongle
    # (driven directly via Bumble) when one is plugged in, else the OS radio.
    adapter = {"kind": "system", "dongle": "absent"}

    def choose_adapter() -> None:
        want = str(args.adapter or cfg.get("adapter", "auto")).lower()
        adapter["kind"] = "system"
        if args.simulate or want == "system":
            return
        from dongle_source import dongle_state

        adapter["dongle"] = dongle_state()
        if want == "dongle" or adapter["dongle"] == "ready":
            adapter["kind"] = "dongle"

    choose_adapter()

    def make_source():
        # Callbacks are tied to this source's generation, so a stopped source
        # that is still finishing a scan can't overwrite the new one's status.
        gen = holder["gen"]

        def live(sig):
            return lambda *a: holder["gen"] == gen and sig.emit(*a)

        on_bpm, on_status = live(bridge.bpm), live(bridge.status)
        on_pulse, on_extras = live(bridge.pulse), live(bridge.extras)
        active = cfg.get("hr_mode") == "active"

        if args.simulate:
            return SimulatedHeartRateSource(on_bpm, on_status, on_pulse=on_pulse if active else None,
                                            on_extras=on_extras if active else None)
        use_standard = args.standard or cfg.get("mode") == "standard"
        if active and not use_standard:
            from active_source import WhoopActiveSource

            return WhoopActiveSource(on_bpm, on_status, on_pulse, on_extras,
                                     serial=cfg.get("active_serial"),
                                     use_dongle=adapter["kind"] == "dongle",
                                     log_path=default_log_path())
        serial = cfg.get("serial")
        if not use_standard and adapter["kind"] == "dongle":
            from dongle_source import WhoopDongleSource

            return WhoopDongleSource(on_bpm, on_status, serial=serial,
                                     configure_band=bool(cfg.get("configure_band", True)),
                                     log_path=default_log_path())
        if not use_standard:
            return WhoopDirectSource(on_bpm, on_status, serial=serial,
                                     configure_band=bool(cfg.get("configure_band", True)),
                                     log_path=default_log_path())
        return BleHeartRateSource(on_bpm, on_status,
                                  address=(args.address or cfg.get("address")),
                                  name=(args.name or cfg.get("name")),
                                  scan_timeout=args.scan_timeout,
                                  log_path=default_log_path())

    def restart_source(message: str) -> None:
        old = holder["source"]
        holder["gen"] += 1  # silence the old source immediately
        if old is not None:
            old.stop()
        win.set_status(message)
        win.set_extras({})
        holder["source"] = make_source()
        holder["source"].start()

    # ---- settings (gear on the overlay) ---------------------------------
    dialog_ref: dict = {}

    def on_settings_change(what: str) -> None:
        save_config(cfg)
        if what == "style":
            win.apply_style(cfg.get("style", {}))
            win.set_max_hr(int(cfg.get("max_hr", 190)))
        elif what == "window":
            win.setWindowOpacity(float(cfg.get("opacity", 1.0)))
            win.set_snap(bool(cfg.get("snap", True)))
            if bool(cfg.get("locked", False)) != win.locked:
                win.set_locked(bool(cfg.get("locked", False)))
                lock_action.setChecked(win.locked)
        elif what == "source":
            choose_adapter()
            label_radio()
            restart_source("Switching source...")

    def open_settings(start_tab: str = "") -> None:
        from settings_dialog import SettingsDialog

        dlg = dialog_ref.get("dlg")
        if dlg is None or not dlg.isVisible():
            dlg = SettingsDialog(cfg, lambda: list(latest_whoops), on_settings_change,
                                 win.reset_geometry, start_tab=start_tab)
            dialog_ref["dlg"] = dlg
            # open beside the overlay, kept on the same screen
            dlg.adjustSize()
            g = win.geometry()
            screen = (QtGui.QGuiApplication.screenAt(g.center())
                      or QtGui.QGuiApplication.primaryScreen()).availableGeometry()
            x = g.right() + 12 if g.right() + 12 + dlg.width() <= screen.right() else g.left() - dlg.width() - 12
            x = max(screen.left() + 8, min(x, screen.right() - dlg.width() - 8))
            y = max(screen.top() + 8, min(g.top(), screen.bottom() - dlg.height() - 8))
            dlg.move(x, y)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    win.settings_requested.connect(lambda: open_settings())

    def on_geometry(x, y, w, h) -> None:
        cfg["geometry"] = [x, y, w, h]
        save_config(cfg)

    win.geometry_changed.connect(on_geometry)

    # ---- tray: fallback controls (always reachable, even when locked) ----
    tray = QtWidgets.QSystemTrayIcon(_make_tray_icon(), app)
    tray.setToolTip("WHOOP HR Overlay")
    menu = QtWidgets.QMenu()
    menu.addAction("Settings...", lambda: open_settings())
    lock_action = menu.addAction("Lock overlay (click-through)")
    lock_action.setCheckable(True)
    lock_action.setChecked(win.locked)

    def toggle_lock(on: bool) -> None:
        cfg["locked"] = bool(on)
        save_config(cfg)
        win.set_locked(bool(on))

    lock_action.toggled.connect(toggle_lock)
    radio = menu.addAction("")
    radio.setEnabled(False)

    def label_radio() -> None:
        radio.setText("Bluetooth: USB dongle" if adapter["kind"] == "dongle"
                      else "Bluetooth: built-in (system)")

    label_radio()

    # USB dongle plugged in but missing its driver (Windows): offer the bundled
    # driver installer, and switch over by itself once the driver is in.
    from dongle_source import launch_driver_installer

    driver_action = menu.addAction("Install USB dongle driver...")
    driver_action.triggered.connect(lambda: launch_driver_installer())
    driver_action.setVisible(adapter["dongle"] == "needs_driver")

    def poll_dongle() -> None:
        want = str(args.adapter or cfg.get("adapter", "auto")).lower()
        if args.simulate or adapter["kind"] == "dongle" or want == "system":
            return
        from dongle_source import dongle_state

        state = dongle_state()
        if state != adapter["dongle"] and state == "needs_driver":
            tray.showMessage("USB dongle needs its driver",
                             "Right-click the heart in the tray > Install USB dongle driver",
                             QtWidgets.QSystemTrayIcon.Information, 10000)
        adapter["dongle"] = state
        driver_action.setVisible(state == "needs_driver")
        if state == "ready":
            adapter["kind"] = "dongle"
            label_radio()
            tray.showMessage("USB dongle ready", "Switched to the USB Bluetooth dongle.",
                             QtWidgets.QSystemTrayIcon.Information, 5000)
            restart_source("Switching to the USB dongle...")

    dongle_timer = QtCore.QTimer(app)
    dongle_timer.timeout.connect(poll_dongle)
    dongle_timer.start(5000)

    menu.addSeparator()
    menu.addAction("Quit WHOOP HR Overlay", QtWidgets.QApplication.quit)
    tray.setContextMenu(menu)
    tray.show()
    if adapter["dongle"] == "needs_driver":
        QtCore.QTimer.singleShot(1500, lambda: tray.showMessage(
            "USB dongle needs its driver",
            "Right-click the heart in the tray > Install USB dongle driver",
            QtWidgets.QSystemTrayIcon.Information, 10000))

    bridge.bpm.connect(lambda b: tray.setToolTip(f"WHOOP HR Overlay: {b} bpm"))

    # First run: open settings on the Source tab so picking a band is obvious.
    if not args.simulate and not cfg.get("onboarded"):
        cfg["onboarded"] = True
        save_config(cfg)
        QtCore.QTimer.singleShot(1200, lambda: open_settings("source"))

    holder["source"] = make_source()
    holder["source"].start()
    app.aboutToQuit.connect(lambda: holder["source"] and holder["source"].stop())
    win.show()
    sys.exit(app.exec())


VERSION = "0.5.0"


class LanBeacon:
    """Broadcast scan results + status as JSON over UDP on the local network."""

    PORT = 47999

    def __init__(self, enabled: bool = True) -> None:
        import socket

        self.sock = None
        self.last_status = ""
        if not enabled:
            return
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.sock = s
        except Exception:
            self.sock = None

    def _send(self, payload: dict) -> None:
        if self.sock is None:
            return
        import json
        import platform

        payload.update(app="whoop-hr-overlay", version=VERSION, host=platform.node())
        data = json.dumps(payload).encode()[:60000]
        try:
            self.sock.sendto(data, ("255.255.255.255", self.PORT))
        except Exception:
            pass

    def status(self, text: str) -> None:
        self.last_status = text
        self._send({"kind": "status", "status": text[:800]})

    def log(self, msg: str) -> None:
        msg = str(msg)
        if msg.startswith("  ") and "dBm" in msg:
            return  # per-device scan lines: already covered by the scan reports
        self._send({"kind": "log", "msg": msg[:900]})

    def extras(self, extras: dict) -> None:
        self._send({"kind": "extras", "extras": {k: str(v) for k, v in extras.items()}})

    def scan(self, rows) -> None:
        # Broadcast datagrams can't be fragmented (macOS rejects >~1472 B), so
        # split the report into small parts that share a scan id.
        import json
        import time

        scan_id = int(time.time() * 1000) & 0xFFFFFFF
        parts, cur = [], []
        for r in rows:
            cur.append({"n": r["name"], "r": r["rssi"], "a": r["addr"][-8:],
                        "u": [u[:8] for u in r["uuids"]], "m": r["mfg"]})
            if len(json.dumps(cur)) > 900:
                parts.append(cur[:-1])
                cur = cur[-1:]
        parts.append(cur)
        for i, devs in enumerate(parts):
            self._send({"kind": "scan", "id": scan_id, "part": i, "parts": len(parts),
                        "count": len(rows), "status": self.last_status[:200] if i == 0 else "",
                        "devices": devs})


if __name__ == "__main__":
    main()
