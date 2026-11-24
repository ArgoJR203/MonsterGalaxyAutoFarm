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
    select_moga_mask = vision.build_text_outline_mask(templates["select_moga"])
    win_bbox = vision.get_game_window_bbox(config.WINDOW_TITLE)

    print(f"[INIT] Attached to window: '{win_bbox['title']}' ({win_bbox['width']}x{win_bbox['height']})")
    print("[INIT] Move mouse to top-left corner (0, 0) at any time to emergency-stop.")

    victories = 0
    turns_in_battle = 0
    zero_alive_streak = 0
    active_slot = None
    exhausted_slots = set()
    exhausted_skills = set()
    last_skill_clicked = None
    consecutive_active_clicks = 0
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

            # PRIORITY 0a: Quest Complete Modal -> Click green checkmark button
            if hit := vision.find_template(
                screen, templates["quest_ok"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                turns_in_battle = 0
                zero_alive_streak = 0
                active_slot = None
                exhausted_slots.clear()
                exhausted_skills.clear()
                last_skill_clicked = None
                consecutive_active_clicks = 0
                print(f"[STATE: QUEST_COMPLETE] (conf={hit[2]:.2f})")
                perform_click(bbox, hit[0], hit[1], "Quest Complete OK")
                last_state_time = time.time()
                time.sleep(1.0)

            # PRIORITY 0b: Accidental Capture Modal Failsafe -> Click top-right X to resume battle
            elif hit := vision.find_template(
                screen, templates["capture_modal"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                zero_alive_streak = 0
                consecutive_active_clicks = 0
                print(f"[STATE: CAPTURE_POPUP] Closing capture modal (conf={hit[2]:.2f})")
                click_normalized(bbox, config.COORDS["capture_close"], "Close Capture Modal")
                last_state_time = time.time()
                time.sleep(0.8)

            # PRIORITY 1: Victory Screen -> Click Continue
            elif hit := vision.find_template(
                screen, templates["victory"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                victories += 1
                turns_in_battle = 0
                zero_alive_streak = 0
                active_slot = None
                exhausted_slots.clear()
                exhausted_skills.clear()
                last_skill_clicked = None
                consecutive_active_clicks = 0
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
                screen,
                templates["select_moga"],
                config.CONFIDENCE_THRESHOLD,
                config.TEMPLATE_REF_SIZE,
                mask=select_moga_mask,
            ):
                consecutive_active_clicks = 0
                print(f"[STATE: BATTLE_SELECT_MOGA] (conf={hit[2]:.2f})")
                preferred_order = [s for s in range(1, 7) if s not in exhausted_slots] or list(range(1, 7))
                alive_slot = vision.find_first_alive_moga_slot(
                    screen, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, preferred_order
                )
                if alive_slot is None:
                    zero_alive_streak += 1
                    if zero_alive_streak < 3:
                        print(f"[WARN] No alive Moga detected (attempt {zero_alive_streak}/3) — retrying...")
                        time.sleep(0.6)
                        continue
                    print("[HALT] All 6 squad Mogas have 0 HP. Stopping bot safely.")
                    break
                zero_alive_streak = 0
                slot_num, sx, sy = alive_slot
                active_slot = slot_num
                exhausted_skills.clear()
                turns_in_battle = 0
                last_skill_clicked = None
                perform_click(bbox, sx, sy, f"Select Alive Moga (Slot {slot_num})")
                last_state_time = time.time()
                time.sleep(0.9)

            # PRIORITY 3: Active Combat -> Upper-Left -> Upper-Right -> Switch Moga
            elif hit := vision.find_template(
                screen, templates["combat_active"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                zero_alive_streak = 0
                if active_slot is None:
                    active_slot = 1
                if consecutive_active_clicks >= 3 and last_skill_clicked is not None:
                    exhausted_skills.add(last_skill_clicked)
                    consecutive_active_clicks = 0
                    print(f"[SKILL] {last_skill_clicked} unresponsive after 3 clicks -> marking out of PP.")

                tl_ok = (
                    "skill_top_left" not in exhausted_skills
                    and turns_in_battle < config.MAX_PRIMARY_TURNS
                    and vision.is_skill_available(screen, config.COORDS["skill_top_left"])
                )
                tr_ok = (
                    "skill_top_right" not in exhausted_skills
                    and vision.is_skill_available(screen, config.COORDS["skill_top_right"])
                )

                if tl_ok:
                    skill_key = "skill_top_left"
                elif tr_ok:
                    skill_key = "skill_top_right"
                    if "skill_top_left" not in exhausted_skills:
                        exhausted_skills.add("skill_top_left")
                        print("[SKILL] skill_top_left out/dimmed -> switching to skill_top_right")
                else:
                    if active_slot is not None:
                        exhausted_slots.add(active_slot)
                    remaining = [s for s in range(1, 7) if s not in exhausted_slots]
                    next_alive = vision.find_first_alive_moga_slot(
                        screen, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, remaining
                    )
                    if next_alive is None:
                        print("[HALT] All alive Mogas are out of usable upper skills (PP). Stopping safely.")
                        break
                    next_slot, sx, sy = next_alive
                    print(f"[SWITCH] Both upper skills exhausted on Slot {active_slot} -> switching to Slot {next_slot}")
                    perform_click(bbox, sx, sy, f"Switch to Alive Moga (Slot {next_slot})")
                    active_slot = next_slot
                    exhausted_skills.clear()
                    turns_in_battle = 0
                    consecutive_active_clicks = 0
                    last_skill_clicked = None
                    last_state_time = time.time()
                    time.sleep(1.2)
                    continue

                turns_in_battle += 1
                consecutive_active_clicks += 1
                last_skill_clicked = skill_key
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
                consecutive_active_clicks = 0
                print(f"[STATE: BATTLE_READY] (conf={hit[2]:.2f})")
                perform_click(bbox, hit[0], hit[1], "Battle Red GO!")
                last_state_time = time.time()
                time.sleep(0.9)

            # PRIORITY 5: Stage Info Modal -> Click Light-Blue GO!
            elif hit := vision.find_template(
                screen, templates["node_popup"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                consecutive_active_clicks = 0
                print(f"[STATE: NODE_POPUP] (conf={hit[2]:.2f})")
                perform_click(bbox, hit[0], hit[1], "Stage Popup GO!")
                last_state_time = time.time()
                time.sleep(1.2)

            # PRIORITY 6: Overworld Map -> Click Target Farm Node
            elif hit := vision.find_template(
                screen, templates["map_idle"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            ):
                turns_in_battle = 0
                active_slot = None
                exhausted_slots.clear()
                exhausted_skills.clear()
                last_skill_clicked = None
                consecutive_active_clicks = 0
                print(f"[STATE: MAP_IDLE] (conf={hit[2]:.2f})")
                click_normalized(bbox, config.COORDS["farm_node"], "Select Beach Farm Node")
                last_state_time = time.time()
                time.sleep(1.5)

            # TRANSITION / ANIMATION POLLING
            else:
                consecutive_active_clicks = 0
                if time.time() - last_state_time > config.WATCHDOG_TIMEOUT:
                    print(
                        f"[WATCHDOG] No known screen matched for {config.WATCHDOG_TIMEOUT}s. "
                        "Check for unexpected popups, depleted energy, or window obstruction."
                    )
                    last_state_time = time.time()
                time.sleep(0.25)


if __name__ == "__main__":
    run_farming_loop()
