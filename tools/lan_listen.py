"""Listen for the overlay's LAN diagnostics beacon (UDP 47999) and print what
each running overlay sees: its status and the Bluetooth devices in its scans.

    python tools/lan_listen.py [seconds] [--all]

By default only WHOOP / heart-rate devices are listed per scan; --all lists all.
"""
import json
import socket
import sys
import time

PORT = 47999


def main() -> None:
    secs = float(next((a for a in sys.argv[1:] if not a.startswith("-")), 60))
    show_all = "--all" in sys.argv
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", PORT))
    s.settimeout(1.0)
    scans: dict = {}
    t0 = time.time()
    last_status = {}
    while time.time() - t0 < secs:
        try:
            data, addr = s.recvfrom(65535)
        except socket.timeout:
            continue
        try:
            m = json.loads(data)
        except ValueError:
            continue
        host = f"{m.get('host')}@{addr[0]} v{m.get('version')}"
        ts = time.strftime("%H:%M:%S")
        if m.get("kind") == "status":
            if last_status.get(host) != m["status"]:
                last_status[host] = m["status"]
                print(f"[{ts}] {host} STATUS: {m['status']!r}", flush=True)
            continue
        if m.get("kind") == "log":
            print(f"[{ts}] {host} LOG: {m['msg']}", flush=True)
            continue
        if m.get("kind") == "extras":
            if last_status.get(host + "#x") != m["extras"]:
                last_status[host + "#x"] = m["extras"]
                print(f"[{ts}] {host} EXTRAS: {m['extras']}", flush=True)
            continue
        if m.get("kind") != "scan":
            continue
        key = (host, m["id"])
        scans.setdefault(key, {})[m["part"]] = m
        if len(scans[key]) == m["parts"]:
            parts = scans.pop(key)
            devs = [d for i in sorted(parts) for d in parts[i]["devices"]]
            print(f"[{ts}] {host} SCAN: {m['count']} devices", flush=True)
            for d in devs:
                interesting = ("whoop" in d["n"].lower() or "0000180d" in d["u"]
                               or "fd4b0001" in d["u"])
                if show_all or interesting:
                    print(f"      {d['r']:>4} dBm  ..{d['a']}  {d['n'] or '(unnamed)':26s}"
                          f" svc={d['u']} mfg={d['m']}", flush=True)


if __name__ == "__main__":
    main()
