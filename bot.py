"""Main Finite State Machine (FSM) runner for automated resource farming."""

import random
import time
import mss
import pyautogui

import config
import vision

# Failsafe: slamming mouse cursor to top-left corner (0, 0) immediately aborts script
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


def perform_click(bbox: dict, rel_or_abs_x: int, rel_or_abs_y: int, action_label: str) -> None:
    """Applies slight random jitter and clicks absolute screen coordinates."""
    jitter_x = random.randint(-config.CLICK_JITTER_PX, config.CLICK_JITTER_PX)
    jitter_y = random.randint(-config.CLICK_JITTER_PX, config.CLICK_JITTER_PX)
    abs_x = bbox["left"] + rel_or_abs_x + jitter_x
    abs_y = bbox["top"] + rel_or_abs_y + jitter_y

    mode_tag = "[DRY-RUN]" if config.DRY_RUN else "[CLICK]"
    print(f"{mode_tag} {action_label} at window=({rel_or_abs_x}, {rel_or_abs_y})")

    if not config.DRY_RUN:
        pyautogui.click(abs_x, abs_y)


def click_normalized(bbox: dict, norm_xy: tuple, action_label: str) -> None:
    """Converts normalized (0.0-1.0) coordinates to window pixels and clicks."""
    px = int(bbox["width"] * norm_xy[0])
    py = int(bbox["height"] * norm_xy[1])
    perform_click(bbox, px, py, action_label)


def run_farming_loop() -> None:
    vision.enable_dpi_awareness()
    templates = vision.load_templates(config.TEMPLATE_PATHS)
    win_bbox = vision.get_game_window_bbox(config.WINDOW_TITLE)

    print(f"[INIT] Attached to window: '{win_bbox['title']}' ({win_bbox['width']}x{win_bbox['height']})")
    print("[INIT] Move mouse to top-left corner (0, 0) at any time to emergency-stop.")

    victories = 0
    turns_in_battle = 0
    last_state_time = time.time()
    start_time = time.time()
    canvas_offset = None
    last_win_size = None

    mss_cls = getattr(mss, "MSS", mss.mss)
    with mss_cls() as sct:
        while True:
            # Refresh window position dynamically in case user drags or resizes the browser
            try:
                win_bbox = vision.get_game_window_bbox(config.WINDOW_TITLE)
            except RuntimeError as err:
                print(f"[WARN] {err} — retrying in 2s...")
                time.sleep(2.0)
                continue

            win_size = (win_bbox["width"], win_bbox["height"])
            if canvas_offset is None or win_size != last_win_size:
                win_frame = vision.grab_frame(sct, win_bbox)
                detected = vision.detect_game_canvas(
                    win_frame,
                    win_bbox,
                    templates,
                    config.TEMPLATE_REF_SIZE,
                    config.TEMPLATE_REF_ORIGINS,
                    config.CANVAS_DETECT_THRESHOLD,
                )
                if detected is None:
                    print("[INIT] Waiting for Monster Galaxy game canvas inside browser window...")
                    time.sleep(1.0)
                    continue
                canvas_offset = (
                    detected["offset_x"],
                    detected["offset_y"],
                    detected["width"],
                    detected["height"],
                )
                last_win_size = win_size
                print(
                    f"[INIT] Locked onto game canvas: {detected['width']}x{detected['height']} "
                    f"at window offset=({detected['offset_x']}, {detected['offset_y']}) "
                    f"scale={detected['scale']:.2f}x via '{detected['anchor']}' "
                    f"(conf={detected['confidence']:.2f})"
                )

            ox, oy, cw, ch = canvas_offset
            bbox = {
                "left": win_bbox["left"] + ox,
                "top": win_bbox["top"] + oy,
                "width": cw,
                "height": ch,
                "title": win_bbox["title"],
            }
            screen = vision.grab_frame(sct, bbox)

            # PRIORITY 1: Victory Screen -> Click Continue
            if hit := vision.find_template(
                screen, templates["victory"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                victories += 1
                turns_in_battle = 0
                elapsed_min = max(0.01, (time.time() - start_time) / 60.0)
                print(
                    f"[STATE: BATTLE_VICTORY] Run #{victories} complete "
                    f"({victories / elapsed_min:.1f} runs/min, conf={hit[2]:.2f})"
                )
                perform_click(bbox, hit[0], hit[1], "Continue")
                last_state_time = time.time()
                time.sleep(1.2)

            # PRIORITY 2: Choose Moga -> Select First Alive Squad Member
            elif hit := vision.find_template(
                screen, templates["select_moga"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                print(f"[STATE: BATTLE_SELECT_MOGA] (conf={hit[2]:.2f})")
                alive_slot = vision.find_first_alive_moga_slot(
                    screen, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET
                )
                if alive_slot is None:
                    print("[HALT] All 6 squad Mogas have 0 HP. Stopping bot safely.")
                    break
                slot_num, sx, sy = alive_slot
                perform_click(bbox, sx, sy, f"Select Alive Moga (Slot {slot_num})")
                last_state_time = time.time()
                time.sleep(0.9)

            # PRIORITY 3: Active Combat -> Attack with Upper-Left Skill (with PP fallback)
            elif hit := vision.find_template(
                screen, templates["combat_active"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                turns_in_battle += 1
                if turns_in_battle <= config.MAX_PRIMARY_TURNS:
                    skill_key = "skill_top_left"
                elif turns_in_battle % 2 == 1:
                    skill_key = "skill_top_right"
                else:
                    skill_key = "skill_bottom_left"

                click_normalized(
                    bbox,
                    config.COORDS[skill_key],
                    f"Attack Turn {turns_in_battle} ({skill_key})",
                )
                last_state_time = time.time()
                time.sleep(0.7)

            # PRIORITY 4: Battle Ready -> Click Red GO!
            elif hit := vision.find_template(
                screen, templates["combat_ready"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                turns_in_battle = 0
                print(f"[STATE: BATTLE_READY] (conf={hit[2]:.2f})")
                perform_click(bbox, hit[0], hit[1], "Battle Red GO!")
                last_state_time = time.time()
                time.sleep(0.9)

            # PRIORITY 5: Stage Info Modal -> Click Light-Blue GO!
            elif hit := vision.find_template(
                screen, templates["node_popup"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                print(f"[STATE: NODE_POPUP] (conf={hit[2]:.2f})")
                perform_click(bbox, hit[0], hit[1], "Stage Popup GO!")
                last_state_time = time.time()
                time.sleep(1.2)

            # PRIORITY 6: Overworld Map -> Click Target Farm Node
            elif hit := vision.find_template(
                screen, templates["map_idle"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                print(f"[STATE: MAP_IDLE] (conf={hit[2]:.2f})")
                click_normalized(bbox, config.COORDS["farm_node"], "Select Beach Farm Node")
                last_state_time = time.time()
                time.sleep(1.5)

            # TRANSITION / ANIMATION POLLING
            else:
                if time.time() - last_state_time > config.WATCHDOG_TIMEOUT:
                    print(
                        f"[WATCHDOG] No known screen matched for {config.WATCHDOG_TIMEOUT}s. "
                        "Check for unexpected popups, depleted energy, or window obstruction."
                    )
                    last_state_time = time.time()
                time.sleep(0.25)


if __name__ == "__main__":
    run_farming_loop()
