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
        "opacity": float(pick("opacity", 0.96)),
        "scale": float(pick("scale", 1.0)),
        "fps": int(pick("fps", 60)),
        "movable": bool(pick("movable", False)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="whoop-hr-overlay",
        description="Live WHOOP / BLE heart-rate overlay for game screen-shares.",
    )
    ap.add_argument("--simulate", action="store_true",
                    help="use a fake HR signal instead of BLE (great for testing)")
    ap.add_argument("--serial", help="your WHOOP's serial, e.g. MGB0663331 (default: nearest WHOOP)")
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
    opts = build_opts(args, cfg)

    from PySide6 import QtCore, QtWidgets

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

    win = OverlayWindow(opts)

    # System-tray icon: the always-available way to quit (the overlay itself
    # is click-through and has no taskbar entry).
    tray = QtWidgets.QSystemTrayIcon(_make_tray_icon(), app)
    tray.setToolTip("WHOOP HR Overlay (right-click to quit)")
    menu = QtWidgets.QMenu()
    menu.addAction("Quit WHOOP HR Overlay", QtWidgets.QApplication.quit)
    tray.setContextMenu(menu)
    tray.show()

    class Bridge(QtCore.QObject):
        bpm = QtCore.Signal(int)
        status = QtCore.Signal(str)

    bridge = Bridge()
    bridge.bpm.connect(win.set_bpm)
    bridge.status.connect(win.set_status)

    serial = args.serial or cfg.get("serial")
    use_standard = args.standard or cfg.get("mode") == "standard" or (
        not serial and (args.name or args.address or cfg.get("address"))
    )
    if args.simulate:
        source = SimulatedHeartRateSource(bridge.bpm.emit, bridge.status.emit)
    elif not use_standard:
        # default: talk to the WHOOP directly over its own protocol
        source = WhoopDirectSource(
            bridge.bpm.emit,
            bridge.status.emit,
            serial=serial,
            configure_band=bool(cfg.get("configure_band", True)),
            log_path=default_log_path(),
        )
    else:
        source = BleHeartRateSource(
            bridge.bpm.emit,
            bridge.status.emit,
            address=(args.address or cfg.get("address")),
            name=(args.name or cfg.get("name")),
            scan_timeout=args.scan_timeout,
            log_path=default_log_path(),
        )

    source.start()
    app.aboutToQuit.connect(source.stop)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
