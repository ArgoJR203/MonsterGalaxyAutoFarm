# Monster Galaxy Auto-Farm Bot

A computer-vision desktop automation bot in Python for farming battle resources (XP, Blue Coffee, drops) on a targeted overworld map node in **Monster Galaxy**.

Instead of relying on fragile fixed timers, every action is driven by a **Priority-Ordered Finite State Machine (FSM)** using DPI-aware screen capture (`mss`), OpenCV template matching (`opencv-python`) with automatic browser-canvas lock-on and static-ratio template scaling, and input automation (`pyautogui`).

---

## How It Works

On every tick (`~0.25s`), the bot captures the game canvas inside your browser window and checks UI landmarks in **strict priority order (1 → 6)** so it reacts immediately if an enemy faints early or a squad Moga is defeated:

```text
[6. MAP_IDLE] ──(Click Farm Node)──> [5. NODE_POPUP] ──(Click Blue GO!)──┐
      ▲                                                                   │
      │                                                                   ▼
(Click Continue)                                              [2. BATTLE_SELECT_MOGA]
      │                                                                   │
[1. BATTLE_VICTORY] <──(Enemy Faints)── [3. BATTLE_ACTIVE] <──(Click Red GO!)── [4. BATTLE_READY]
                                           │         ▲
                                           └─(Loop)──┘ (Click Skill)
```

| Priority | State | Visual Trigger | Automated Action |
| :---: | :--- | :--- | :--- |
| **1** | `BATTLE_VICTORY` | Light-blue **Continue** button | Click **Continue** (ignores replay button) |
| **2** | `BATTLE_SELECT_MOGA` | **CHOOSE YOUR MOGA** label | Scan squad slots `1–6` left-to-right and click first alive Moga |
| **3** | `BATTLE_ACTIVE` | Center **CAPTURE** button | Click primary upper-left skill (rotates to fallback skills after PP limit) |
| **4** | `BATTLE_READY` | Large red **GO!** button | Click red **GO!** |
| **5** | `NODE_POPUP` | Light-blue **GO!** modal button | Click modal **GO!** |
| **6** | `MAP_IDLE` | **DISCORD SERVER** overworld anchor | Click target farm node (`COORDS["farm_node"]`) |

---

## Features

- **Automatic Browser Canvas Lock-On:** Automatically locates the embedded game canvas inside your browser window (ignoring browser tabs, address bar, Global Chat sidebar, side padding, and taskbar) and re-locks if the window is resized.
- **Static-Ratio Template Scaling:** Uniformly scales pre-cropped reference templates (`TEMPLATE_REF_SIZE`) to match any browser zoom or window size.
- **HP-Aware Squad Selection:** Scans the HP bar strip below squad slots `1–6` for green, yellow, or orange bars (supporting partially damaged Mogas). Safely halts if all 6 slots have `0 HP`.
- **PP Exhaustion Guard:** Attacks with `skill_top_left` up to `MAX_PRIMARY_TURNS`, then alternates between `skill_top_right` and `skill_bottom_left`.
- **Built-in Safety:** Includes `DRY_RUN` mode, slight random click jitter (`CLICK_JITTER_PX`), watchdog stall logging, and `pyautogui.FAILSAFE` emergency stop.

---

## Prerequisites

- **OS:** Windows 10 / 11 (uses Windows DPI awareness and `pygetwindow`)
- **Python:** 3.10 or newer
- **Browser:** Any desktop browser running the game visibly on screen (e.g., Chrome, Brave, Edge)

---

## Project Structure

```text
game_farm_bot/
├── README.md
├── requirements.txt          # Python dependencies
├── config.py                 # Window title, thresholds, reference size, and normalized coordinates
├── vision.py                 # DPI awareness, canvas detection, template matching, and HP scan
├── bot.py                    # Main 6-state FSM loop and session logger
├── tools/
│   └── setup_wizard.py       # Calibration overlay generator and interactive ROI template cropper
└── templates/                # Reference UI landmark PNGs
    ├── battle_continue.png   # Victory "Continue" button
    ├── choose_moga.png       # "CHOOSE YOUR MOGA" prompt
    ├── battle_capture.png    # Combat "CAPTURE" button
    ├── battle_red_go.png     # Battle ready red "GO!" button
    ├── popup_go.png          # Stage modal "GO!" button
    └── map_discord.png       # Overworld "DISCORD SERVER" sign
```

---

## Installation

1. Clone the repository and enter the project directory:
   ```bash
   git clone https://github.com/ArgoJR203/MonsterGalaxyAutoFarm
   cd MonsterGalaxyAutoFarm
   ```

2. *(Optional)* Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   python -m pip install -r requirements.txt
   ```

---

## Configuration

Edit [config.py](config.py) to customize behavior:

| Setting | Default | Description |
| :--- | :--- | :--- |
| `WINDOW_TITLE` | `"Monster Galaxy"` | Case-insensitive substring of your game/browser window title |
| `DRY_RUN` | `False` | If `True`, logs matched states and click coordinates without clicking |
| `CONFIDENCE_THRESHOLD` | `0.85` | Minimum OpenCV template match score (`0.0–1.0`) |
| `CANVAS_DETECT_THRESHOLD` | `0.80` | Minimum score for initial browser game-canvas lock-on |
| `MAX_PRIMARY_TURNS` | `15` | Turns before rotating from `skill_top_left` to fallback skills |
| `WATCHDOG_TIMEOUT` | `25.0` | Seconds without a recognized state before logging a warning |
| `CLICK_JITTER_PX` | `3` | Random `±px` offset applied to each click |
| `TEMPLATE_REF_SIZE` | `(1024, 890)` | Reference `(width, height)` of the game canvas for the templates |
| `COORDS` | *(normalized)* | Relative `(0.0–1.0)` click targets for `farm_node`, skills, and `squad_slots` |

---

## Usage

### 1. Dry-Run Verification (Recommended First Run)
1. Set `DRY_RUN = True` in [config.py](config.py).
2. Open the game in your browser and ensure the tab is visible and unobstructed.
3. Start the bot:
   ```bash
   python bot.py
   ```
4. Step through one battle manually and confirm the terminal logs `[DRY-RUN]` detections for all 6 states.

### 2. Live Farming
1. Set `DRY_RUN = False` in [config.py](config.py).
2. Keep the game window visible on screen (overworld map or mid-battle).
3. Run:
   ```bash
   python bot.py
   ```
   On startup, the bot automatically locks onto the embedded game canvas (`[INIT] Locked onto game canvas: ...`) and begins farming.

### 3. Optional: Recalibrating Coordinates or Re-cropping Templates
Pre-cropped templates are included in `templates/`. If you want to target a different farm node, adjust coordinates, or re-crop templates at your native resolution:

1. Update `COORDS["farm_node"]` (or other coordinates) in [config.py](config.py).
2. With the game visible, run:
   ```bash
   python tools/setup_wizard.py
   ```
3. Inspect the generated `calibration_overlay.png` to verify red/green dots align with the farm node, squad slots, HP bars, and skill buttons.
4. Follow the terminal prompts to crop any templates (press `c` during selection to keep an existing template). If you re-crop all templates at a different native canvas size, update `TEMPLATE_REF_SIZE` and `TEMPLATE_REF_ORIGINS` in [config.py](config.py).

### Emergency Stop
- **Mouse Failsafe:** Move your cursor to the **top-left corner `(0, 0)`** of the screen to abort immediately.
- **Keyboard:** Focus the terminal and press `Ctrl + C`.

---

## Troubleshooting

- **`No visible window found containing 'Monster Galaxy'`**: Make sure the game tab is active in your browser, or set `WINDOW_TITLE` in `config.py` to match one of the window titles listed in the error output.
- **`[INIT] Waiting for Monster Galaxy game canvas inside browser window...`**: Ensure the game canvas is visible (on the map, popup, or battle screen) and not covered by another window.
- **`[WATCHDOG] No known screen matched for 25.0s`**: Check that your energy/coffee is not depleted, no unexpected modal is blocking the view, and the browser window is unobstructed.
- **`[HALT] All 6 squad Mogas have 0 HP`**: All squad slots appear fainted. Heal your Mogas before restarting.

---

## Disclaimer

This project is provided for educational and personal automation purposes. Use responsibly and in accordance with the game's terms of service.
