<div align="center">

# ❤️ WHOOP Heart Rate Overlay

**A live heart-rate indicator + rolling graph that sits on top of your game — and shows up in your Discord screen share.**

Stream-style HR, powered by your own WHOOP (or any Bluetooth heart-rate strap).

![platform](https://img.shields.io/badge/platform-Windows-0a7bca)
![python](https://img.shields.io/badge/python-3.9%2B-3776ab)
![ui](https://img.shields.io/badge/UI-PySide6-41cd52)
![ble](https://img.shields.io/badge/BLE-bleak-ff5d6c)
![license](https://img.shields.io/badge/license-MIT-black)

</div>

---

```
   ┌──────────────────────────────────────────────┐
   │  ❤  142   ╭───────────────╮                   │   ← top-left of your screen
   │     BPM   │      ╱╲    ╱╲  │                   │     (and in your Discord share)
   │           │ ╱╲  ╱  ╲  ╱   ●│                   │
   │           ╰───────────────╯                   │
   └──────────────────────────────────────────────┘
          big live number   +   last ~30s graph
```

A tiny translucent panel pins to a corner of your screen showing your **current heart
rate** (with a heart that pulses in time with your pulse) and a **rolling ~30-second
graph**, color-coded by effort zone. It's click-through, always-on-top, and composited
by Windows itself — so when you share your **whole screen** in Discord, your friends see
your heart spike the moment you get peeked in Counter-Strike. 😅

## ✨ Features

- **Live BPM** with a heart that pulses on every beat, colored by heart-rate zone.
- **Two graphs:** a rolling heart-rate trend, and a **pulse waveform** underneath.
- **Two modes**, picked in settings:
  - **Passive** (your everyday band): heart rate from the band's standard HR channel. No pairing,
    so the band stays connected to your phone app. The pulse graph is an animation timed to
    your live heart rate (and labeled that way).
  - **Active** (a dedicated band paired to this PC): the band's real **optical PPG waveform**
    (~25 samples/s), **beat-to-beat R-R intervals with live HRV**, and **motion**. Uses a spare
    band, because a band can only be connected to one app at a time.
- **Gear on the overlay:** hover it to reveal a gear (settings) and a resize corner; drag it
  anywhere to move it. Fonts, colors, opacity, corner radius, line width, which elements show,
  and graph spans are all adjustable. Changes apply live and save automatically.
- **Lock** makes it fully click-through for gaming (unlock from the tray heart).
- **Shows up in screen shares:** a normal desktop window, so Discord/OBS full-screen capture
  picks it up.
- **USB dongle support:** an nRF52840 dongle with the included firmware gives a much stronger
  Bluetooth radio than most desktop PCs (see [`firmware/`](firmware/README.md)).
- **One download:** a single exe with the dongle driver installer built in.

## 🔌 How it reads your WHOOP

It talks to the WHOOP 5.0/MG directly over Bluetooth, with no phone relay and no WHOOP app.
Hover the overlay, click the gear, and under **Source** pick:
- **Everyday band** for Passive mode, and/or
- **Dedicated band** for Active mode.

It only connects to the band you pick. Other WHOOPs in range are listed, but they're never
connected to or changed. Wear the band: a WHOOP only sends heart rate while it detects a pulse.

**Active mode:** the first connection pairs the dedicated band with this PC (the bond is saved
next to the exe), then switches on the band's live sensor streams. When you quit or switch
modes, it turns them back off.

## 🪟 Install on Windows

### Option A — download the .exe (easiest, no Python)

1. Go to the [**Releases**](../../releases/latest) page.
2. Download **`whoop-hr-overlay.exe`**.
3. Double-click it. That's it — it auto-connects to the nearest Bluetooth heart-rate broadcaster.

> Windows SmartScreen may say *"unknown publisher"* because the exe is unsigned — click
> **More info → Run anyway**. (It's your own build; the source is right here.)

To pin a specific strap or change the corner/size/colors, drop a **`config.json`** in the **same
folder as the exe** (copy [`config.example.json`](config.example.json) and edit it — see
[Configuration](#️-configuration)). You can also run it from a terminal with flags, e.g.
`whoop-hr-overlay.exe --simulate` or `whoop-hr-overlay.exe --position top-right`.

> No release yet? Push a version tag (`git tag v0.1.0 && git push origin v0.1.0`) or run the
> **Build Windows EXE** workflow from the **Actions** tab — GitHub builds the exe on a Windows
> runner and attaches it to a Release automatically. You can also build it yourself with
> [`build-windows.bat`](build-windows.bat).

---

### Option B — run from source

**Prerequisites:** Windows 10/11 with Bluetooth, and [Python 3.9+](https://www.python.org/downloads/windows/)
(check **"Add Python to PATH"** during install).

#### B1 — one double-click (recommended)

1. Download / clone this folder onto your PC.
2. Double-click **`run-overlay.bat`**.
   - First run builds a local virtual environment and installs `PySide6` + `bleak` automatically (takes a minute).
   - After that it launches instantly.

To pass options, run it from a terminal, e.g.:

```bat
run-overlay.bat --simulate
run-overlay.bat --name WHOOP --position top-left
```

#### B2 — manual

```bat
cd whoop-hr-overlay
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python main.py --simulate            REM test the look first
python main.py --list                REM find your strap
python main.py --name WHOOP          REM go live
```

### Create a desktop shortcut (optional)

Right-click `run-overlay.bat` → **Send to → Desktop (create shortcut)**. Edit the shortcut's
**Target** to add flags, e.g. `...\run-overlay.bat --name WHOOP`. Set **Run: Minimized** so no
console window lingers.

---

## 🎮 Making it visible in your Discord screen share

This is the important part — a couple of settings decide whether your friends see the overlay:

| Do this | Why |
|---|---|
| **Share your whole *Screen*, not an *Application Window*** | Discord's *window* capture grabs only that app's pixels. The overlay is a separate window, so only a full-**screen** share includes it. |
| **Run the game in *Borderless* / *Windowed Fullscreen*** | Exclusive-fullscreen games bypass the desktop compositor, so no overlay (Discord's, Steam's, or this one) can draw over them. Borderless looks identical and fixes it. In **CS2**: Settings → Video → **Display Mode: Fullscreen Windowed**. |
| Keep the overlay in a **corner that isn't covered by your HUD** | So it's readable in the share. Use `--position` to move it. |

> **Quick test:** start the overlay with `--simulate`, start a Discord call, share your **Screen**,
> and look at the preview — you should see the heart-rate panel. If you don't, you're sharing a
> *window* instead of the *screen*, or the game is in exclusive fullscreen.

OBS works the same way: it shows up under **Display Capture**, not **Game Capture**.

---

## 🛡️ Is this safe with anti-cheat (VAC / CS2)?

Yes. This app **does not touch the game at all** — it doesn't read or write game memory, inject
code, or hook anything. It only (a) reads heart-rate notifications over Bluetooth and (b) draws its
own normal desktop window on top, exactly like the Discord overlay, Steam overlay, or MSI Afterburner.
That category of external top-most window is not what anti-cheat targets. (As always, overlays are
used at your own discretion.)

---

## ⚙️ Configuration

Every option is a CLI flag, and you can make settings permanent by copying
**`config.example.json`** to **`config.json`** (CLI flags still override it).

| Flag | config key | Default | What it does |
|---|---|---|---|
| `--simulate` | — | off | Fake HR signal; no Bluetooth needed. |
| `--list` | — | — | Scan for BLE HR devices, print them, exit. |
| `--name <text>` | `name` | — | Connect to the first device whose name contains `<text>`. |
| `--address <addr>` | `address` | — | Connect to a specific BLE address. |
| `--max-hr <n>` | `max_hr` | `190` | Your max HR; sets where the zone colors land. |
| `--position <corner>` | `position` | `top-left` | `top-left` / `top-right` / `bottom-left` / `bottom-right`. |
| `--margin <px>` | `margin` | `24` | Gap from the screen edge. |
| `--opacity <0-1>` | `opacity` | `0.96` | Panel opacity. |
| `--scale <x>` | `scale` | `1.0` | Overall size multiplier (e.g. `1.5` for a 1440p/4K display). |
| `--fps <n>` | `fps` | `60` | Repaint rate. |
| `--movable` | `movable` | `false` | Make it draggable (turns **off** click-through so you can reposition it; drag, then relaunch without the flag). |

**Example `config.json`:**

```json
{
  "name": "WHOOP",
  "max_hr": 186,
  "position": "top-left",
  "scale": 1.25
}
```

**Zone colors** (by % of max HR): 🔵 recovery · 🟦 warm-up · 🟢 aerobic · 🟡 tempo · 🟠 threshold · 🔴 max.

**Quitting:** the overlay is click-through and stays off the taskbar, so it lives in the
**system tray** (the ^ area by the clock) as a small red heart. **Right-click it → Quit.**
Launching a new copy (e.g. a newer version) automatically closes the old one.

**Not connected?** The panel shows the nearest Bluetooth devices your PC can hear. If no
`WHOOP …` line appears, the band isn't broadcasting near this PC: start **Broadcast Heart Rate**
in the WHOOP app and wear the band.

---

## 🧰 Troubleshooting

| Symptom | Fix |
|---|---|
| **Overlay not in my Discord share** | You're sharing an *Application Window* — switch to sharing the **Screen**. And put the game in **Borderless** mode. |
| **Nothing shows over my game** | The game is in **exclusive fullscreen**. Switch to Windowed/Borderless Fullscreen. |
| **`--list` finds nothing** | Make sure **Heart Rate Broadcast** is enabled in the WHOOP app and the band is awake/worn. Only one app can hold the HR connection at a time — close other apps reading it. |
| **Status says "Bluetooth radio is not powered on"** | Windows is reporting the BLE radio as off. Toggle Bluetooth off/on, make sure Airplane mode is off, check the adapter in Device Manager, and restart the *Bluetooth Support Service* (`services.msc`). The overlay auto-retries, so you don't need to relaunch. |
| **Connects ("connected: WHOOP …") but the number stays `--`** | The band isn't sending beats — almost always because it **isn't being worn**. Put it on snug with good skin contact. (Confirmed behavior: an idle WHOOP connects, streams nothing, then disconnects.) Also make sure the phone WHOOP app isn't holding the connection. |
| **Sits on "scanning…" / "no HR match"** | Your band isn't advertising the HR service. Put its exact name in `config.json` (`"name": "WHOOP <serial>"`) to match by name instead of by service. |
| **Want to see what it's doing** | Check **`whoop-overlay.log`**, written next to the exe — it logs every scan (all nearby devices), connections, and whether the HR data channel was found. Send me that file if you're stuck. |
| **It's too small / too big** | Use `--scale 1.5` (bigger) or `0.8` (smaller). |
| **Can't reposition it** | Run once with `--movable`, drag it where you want, note the corner, then relaunch with `--position`. |
| **`py` is not recognized** | Install Python from python.org with *Add to PATH* checked, or replace `py -3` with `python`. |

---

## 🏗️ How it works

```
 WHOOP / BLE strap ──BLE notify (0x2A37)──▶  hr_source.py  (bleak, background thread)
                                                   │  Qt signal (thread-safe)
                                                   ▼
                                             overlay.py  (PySide6, GUI thread)
                                              • translucent, frameless, top-most
                                              • WS_EX_LAYERED | WS_EX_TRANSPARENT (click-through)
                                              • QPainter: pulsing heart + 30s sparkline
```

- **`hr_source.py`** — `BleHeartRateSource` (standard Heart Rate profile) and
  `SimulatedHeartRateSource`, each on their own asyncio thread.
- **`overlay.py`** — the translucent, click-through, always-on-top window and all the drawing.
- **`main.py`** — argument parsing, config loading, and the signal bridge between them.

## 📄 License

MIT — see [`LICENSE`](LICENSE). Not affiliated with or endorsed by WHOOP, Inc.
"WHOOP" is a trademark of its respective owner; this is an independent hobby project that reads
heart-rate data you already own.
