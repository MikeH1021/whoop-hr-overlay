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

    class Bridge(QtCore.QObject):
        bpm = QtCore.Signal(int)
        status = QtCore.Signal(str)

    bridge = Bridge()
    bridge.bpm.connect(win.set_bpm)
    bridge.status.connect(win.set_status)

    # LAN diagnostics beacon: each scan + status change is broadcast as JSON on
    # the local network (UDP 47999) so the developer can see what this PC sees.
    beacon = LanBeacon(enabled=bool(cfg.get("lan_diagnostics", True)))
    bridge.status.connect(beacon.status)

    import hr_source as _hs

    latest_whoops: list = []

    def on_scan(rows) -> None:
        latest_whoops[:] = [r["name"] for r in rows
                            if r["name"] and _hs.looks_like_whoop(r["name"], r["uuids"], r["mfg"])]
        beacon.scan(rows)

    _hs.SCAN_LISTENERS.append(on_scan)

    serial = args.serial or cfg.get("serial")
    use_standard = args.standard or cfg.get("mode") == "standard" or (
        not serial and (args.name or args.address or cfg.get("address"))
    )
    holder = {"source": None, "gen": 0}

    # Which Bluetooth radio to use: "auto" picks a Zephyr hci_usb USB dongle
    # (driven directly via Bumble) when one is plugged in, else the OS radio.
    want_adapter = str(args.adapter or cfg.get("adapter", "auto")).lower()
    adapter = {"kind": "system"}
    if not args.simulate and want_adapter in ("auto", "dongle"):
        from dongle_source import dongle_present

        if want_adapter == "dongle" or dongle_present():
            adapter["kind"] = "dongle"

    def make_source(serial_):
        # Callbacks are tied to this source's generation, so a stopped source
        # that is still finishing a scan can't overwrite the new one's status.
        gen = holder["gen"]

        def on_bpm(b):
            if holder["gen"] == gen:
                bridge.bpm.emit(b)

        def on_status(s):
            if holder["gen"] == gen:
                bridge.status.emit(s)

        if args.simulate:
            return SimulatedHeartRateSource(on_bpm, on_status)
        if not use_standard and adapter["kind"] == "dongle":
            from dongle_source import WhoopDongleSource

            return WhoopDongleSource(
                on_bpm,
                on_status,
                serial=serial_,
                configure_band=bool(cfg.get("configure_band", True)),
                log_path=default_log_path(),
            )
        if not use_standard:
            # default: talk to the WHOOP directly over its own protocol
            return WhoopDirectSource(
                on_bpm,
                on_status,
                serial=serial_,
                configure_band=bool(cfg.get("configure_band", True)),
                log_path=default_log_path(),
            )
        return BleHeartRateSource(
            on_bpm,
            on_status,
            address=(args.address or cfg.get("address")),
            name=(args.name or cfg.get("name")),
            scan_timeout=args.scan_timeout,
            log_path=default_log_path(),
        )

    def switch_band(name: str) -> None:
        serial_ = name.split()[-1]  # "WHOOP MGB0663331" -> "MGB0663331"
        cfg["serial"] = serial_
        save_config(cfg)
        old = holder["source"]
        holder["gen"] += 1  # silence the old source immediately
        if old is not None:
            old.stop()
        win.set_status(f"Switching to {name}...")
        holder["source"] = make_source(serial_)
        holder["source"].start()

    # System-tray icon: quit + band picker (the overlay itself is click-through
    # and has no taskbar entry).
    tray = QtWidgets.QSystemTrayIcon(_make_tray_icon(), app)
    tray.setToolTip("WHOOP HR Overlay (right-click for options)")
    menu = QtWidgets.QMenu()
    pick = menu.addMenu("Choose your WHOOP")

    def fill_picker() -> None:
        pick.clear()
        names = sorted(set(latest_whoops))
        if not names:
            a = pick.addAction("No WHOOP broadcasting near this PC yet")
            a.setEnabled(False)
        for nm in names:
            a = pick.addAction(nm)
            a.setCheckable(True)
            a.setChecked(bool(cfg.get("serial")) and cfg["serial"] in nm)
            a.triggered.connect(lambda _=False, n=nm: switch_band(n))

    pick.aboutToShow.connect(fill_picker)
    radio = menu.addAction("Bluetooth: USB dongle" if adapter["kind"] == "dongle"
                           else "Bluetooth: built-in (system)")
    radio.setEnabled(False)
    menu.addSeparator()
    menu.addAction("Quit WHOOP HR Overlay", QtWidgets.QApplication.quit)
    tray.setContextMenu(menu)
    tray.show()

    holder["source"] = make_source(serial)
    holder["source"].start()
    app.aboutToQuit.connect(lambda: holder["source"] and holder["source"].stop())
    win.show()
    sys.exit(app.exec())


VERSION = "0.2.2"


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
