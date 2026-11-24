# Monster Galaxy Auto-Farm

A computer-vision automation bot and **WebApp control panel** for farming battle resources (XP, Blue Coffee, drops) on **Monster Galaxy** (`https://monstergalaxy.online`).

Run the game inside an isolated background Playwright Chromium browser so you can **switch tabs, browse, or work freely without losing your mouse cursor**, and configure everything from a browser dashboard at `http://127.0.0.1:8000`.

---

## Features

- **Background Browser Automation (WebApp Mode):** Runs the game in a dedicated Chromium session (`.browser_profile/`) with `page.screenshot()` and `page.mouse.click()`, leaving your main browser and physical mouse untouched.
- **Interactive Map Node Picker:** Preview the live game canvas in the dashboard and click any node on the overworld map to save and farm custom coordinates.
- **Custom Party Priority Order:** Enable/disable squad slots `1–6` and reorder their priority (e.g., `2 → 3 → 1`) so protected or low-level Mogas are skipped.
- **Priority-Ordered 6-State FSM:** Evaluates states from `BATTLE_VICTORY` → `BATTLE_SELECT_MOGA` → `BATTLE_ACTIVE` → `BATTLE_READY` → `NODE_POPUP` → `MAP_IDLE`.
- **Automatic Canvas Lock-On & Static-Ratio Scaling:** Detects the embedded game canvas inside the browser viewport (ignoring chat sidebars/toolbars) and scales templates uniformly.
- **PP Exhaustion Guard & Dry-Run Mode:** Rotates fallback skills after `max_primary_turns` and supports dry-run logging without clicking.
- **Classic Desktop Mode Included:** Still includes the standalone `mss` + `pyautogui` CLI runner (`python bot.py`).

---

## Project Structure

```text
MonsterGalaxyAutoFarm/
├── README.md
├── install.bat               # One-click Windows setup (creates .venv, installs deps & Chromium)
├── start.bat                 # One-click Windows launcher (starts WebApp & opens dashboard)
├── requirements.txt          # Python dependencies
├── config.py                 # Reference dimensions, template paths, and default coordinates
├── vision.py                 # Canvas detection, template scaling/matching, and HP bar scanner
├── settings_store.py         # Thread-safe JSON persistence (settings.json)
├── engine.py                 # Background Playwright worker & 6-state FSM controller
├── webapp.py                 # FastAPI server & API endpoints
├── web/
│   └── index.html            # WebApp dashboard UI (node picker, party priority, live logs)
├── bot.py                    # Classic desktop-window CLI mode (mss + pyautogui)
├── tools/
│   └── setup_wizard.py       # Interactive ROI template cropper & calibration overlay tool
└── templates/                # Reference UI landmark PNGs
    ├── battle_continue.png
    ├── choose_moga.png
    ├── battle_capture.png
    ├── battle_red_go.png
    ├── popup_go.png
    └── map_discord.png
```

---

## Prerequisites

- **OS:** Windows 10 / 11 (WebApp mode also works on Linux/macOS; CLI `bot.py` uses Windows DPI APIs)
- **Python:** 3.10 or newer

---

## Quick Start (Windows — Beginner Friendly)

1. Make sure **Python 3.10+** is installed (check **"Add Python to PATH"** during setup).
2. Double-click **`install.bat`** *(first time only)* — automatically creates `.venv`, installs all Python packages, and downloads Playwright Chromium.
3. Double-click **`start.bat`** — starts the WebApp server and opens **http://127.0.0.1:8000** in your browser automatically.

---

## Manual Installation (Advanced / Non-Windows)

1. Clone the repository and enter the folder:
   ```bash
   git clone https://github.com/ArgoJR203/MonsterGalaxyAutoFarm.git
   cd MonsterGalaxyAutoFarm
   ```

2. *(Optional)* Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. Install Python dependencies:
   ```bash
   python -m pip install -r requirements.txt
   ```

4. Install Playwright's Chromium browser (required for WebApp mode):
   ```bash
   python -m playwright install chromium
   ```

---

## Usage (Recommended: WebApp Mode)

1. Double-click **`start.bat`** (or run `python webapp.py`) and open **http://127.0.0.1:8000**.
2. Click **Open Game Browser** — a dedicated Chromium window opens `https://monstergalaxy.online` (your login is saved locally in `.browser_profile/`).
3. Log in once and navigate to the overworld map.
4. Configure your setup in the WebApp UI:
   - **Pick a Farm Node:** Click **Pick Node on Preview**, click any node on the live map preview, give it a name, and click **Save & Use**.
   - **Set Party Priority:** Reorder slots `1–6` with `▲` / `▼` and uncheck any slot you want to skip.
   - **Combat & Safety:** Choose primary skill, `Max Primary Turns`, `Confidence Threshold`, or toggle **Dry-Run**.
5. Click **Start**. You can now cover the Playwright browser window with other apps or switch tabs in your main browser while the bot farms in the background.

---

## Usage (Classic CLI Mode)

If you prefer running directly against your existing desktop browser window (note: uses your physical mouse cursor and requires the game window to stay visible on screen):

1. Check `WINDOW_TITLE` and `DRY_RUN` in [config.py](config.py).
2. Run:
   ```bash
   python bot.py
   ```
3. Move your mouse cursor to `(0, 0)` top-left corner at any time for emergency stop, or press `Ctrl + C`.

---

## Optional: Recalibrating Templates

If game UI buttons change, run the interactive cropper while your browser is visible:

```bash
python tools/setup_wizard.py
```

Inspect `calibration_overlay.png` to confirm coordinate alignment.

---

## Troubleshooting

- **Game pauses when Playwright window is minimized:** Do **not** minimize the Playwright Chromium window to the taskbar; instead, leave it open behind your other windows (the engine launches Chromium with background-throttling disabled for occluded windows).
- **`[HALT] No enabled alive Mogas found in priority list`**: Either all enabled squad slots fainted (`0 HP`) or all slots are unchecked in the Party Priority editor.
- **`[WATCHDOG] No known screen matched`**: Check for depleted energy/coffee or an unexpected modal in the game browser window.

---

## Disclaimer

This project is provided for educational and personal automation purposes. Use responsibly and in accordance with the game's terms of service.
