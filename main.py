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

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config(path: str | None) -> dict:
    """Load config.json: an explicit --config, else one next to this file."""
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
        from hr_source import list_devices

        print("Scanning for BLE heart-rate devices...\n")
        rows = list_devices(args.scan_timeout)
        if not rows:
            print("  (nothing found -- is the strap awake and broadcasting?)")
        for rssi, addr, name, is_hr in rows:
            tag = "  <- Heart Rate service" if is_hr else ""
            print(f"  {rssi:>4} dBm   {addr}   {name}{tag}")
        return

    cfg = load_config(args.config)
    opts = build_opts(args, cfg)

    from PySide6 import QtCore, QtWidgets

    from hr_source import BleHeartRateSource, SimulatedHeartRateSource
    from overlay import OverlayWindow

    app = QtWidgets.QApplication(sys.argv)
    win = OverlayWindow(opts)

    class Bridge(QtCore.QObject):
        bpm = QtCore.Signal(int)
        status = QtCore.Signal(str)

    bridge = Bridge()
    bridge.bpm.connect(win.set_bpm)
    bridge.status.connect(win.set_status)

    if args.simulate:
        source = SimulatedHeartRateSource(bridge.bpm.emit, bridge.status.emit)
    else:
        source = BleHeartRateSource(
            bridge.bpm.emit,
            bridge.status.emit,
            address=(args.address or cfg.get("address")),
            name=(args.name or cfg.get("name")),
            scan_timeout=args.scan_timeout,
        )

    source.start()
    app.aboutToQuit.connect(source.stop)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
