"""Offline verification test suite for the 6-state FSM and squad HP scanner."""

import os
import sys
import cv2

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import config
import vision

SCREENSHOT_EXPECTATIONS = [
    (
        "Image 1 (Map Idle)",
        r"C:/Users/AMD/.gemini/antigravity/brain/c198a83f-a176-47ff-93e3-e078228ce3a5/.user_uploaded/media_1795520207439_0f0467a8.jpg",
        "MAP_IDLE",
    ),
    (
        "Image 2 (Node Popup)",
        r"C:/Users/AMD/.gemini/antigravity/brain/c198a83f-a176-47ff-93e3-e078228ce3a5/.user_uploaded/media_1795520210855_5e9fa74d.jpg",
        "NODE_POPUP",
    ),
    (
        "Image 3 (Choose Moga)",
        r"C:/Users/AMD/.gemini/antigravity/brain/c198a83f-a176-47ff-93e3-e078228ce3a5/.user_uploaded/media_1795520215639_00426446.jpg",
        "BATTLE_SELECT_MOGA",
    ),
    (
        "Image 4 (Battle Ready)",
        r"C:/Users/AMD/.gemini/antigravity/brain/c198a83f-a176-47ff-93e3-e078228ce3a5/.user_uploaded/media_1795520218061_f52e6ab0.jpg",
        "BATTLE_READY",
    ),
    (
        "Image 5 (Battle Active)",
        r"C:/Users/AMD/.gemini/antigravity/brain/c198a83f-a176-47ff-93e3-e078228ce3a5/.user_uploaded/media_1795520222735_299644be.jpg",
        "BATTLE_ACTIVE",
    ),
    (
        "Image 6 (Victory)",
        r"C:/Users/AMD/.gemini/antigravity/brain/c198a83f-a176-47ff-93e3-e078228ce3a5/.user_uploaded/media_1795520225968_f46f7388.jpg",
        "BATTLE_VICTORY",
    ),
    (
        "Image 7 (Return to Map)",
        r"C:/Users/AMD/.gemini/antigravity/brain/c198a83f-a176-47ff-93e3-e078228ce3a5/.user_uploaded/media_1795520233171_a99e1901.jpg",
        "MAP_IDLE",
    ),
]

PRIORITY_ORDER = [
    ("BATTLE_VICTORY", "victory"),
    ("BATTLE_SELECT_MOGA", "select_moga"),
    ("BATTLE_ACTIVE", "combat_active"),
    ("BATTLE_READY", "combat_ready"),
    ("NODE_POPUP", "node_popup"),
    ("MAP_IDLE", "map_idle"),
]


def evaluate_fsm_state(screen, templates):
    """Evaluates screen in the exact priority order defined in bot.py."""
    for state_name, tmpl_key in PRIORITY_ORDER:
        hit = vision.find_template(
            screen, templates[tmpl_key], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
        )
        if hit is not None:
            return state_name, hit
    return None, None


def black_out_slot_hp(img, slot_index_0_based):
    """Zeros out the HP bar band under the specified 0-based squad slot."""
    h, w = img.shape[:2]
    rx, ry = config.COORDS["squad_slots"][slot_index_0_based]
    px = int(w * rx)
    hp_py = int(h * (ry + config.HP_BAR_Y_OFFSET))
    y_pad_up = max(2, int(h * 0.008))
    y_pad_down = max(4, int(h * 0.022))
    x_left = max(4, int(w * 0.018))
    x_right = max(2, int(w * 0.008))
    img[
        max(0, hp_py - y_pad_up):min(h, hp_py + y_pad_down + 1),
        max(0, px - x_left):min(w, px + x_right + 1),
    ] = (30, 30, 30)


def main():
    templates = vision.load_templates(config.TEMPLATE_PATHS)
    assert len(templates) == 6, f"Expected 6 templates, got {len(templates)}"
    print("[PASS] Loaded all 6 templates.")

    # 1. Verify FSM state classification across all 7 real screenshots
    for label, img_path, expected_state in SCREENSHOT_EXPECTATIONS:
        screen = cv2.imread(img_path, cv2.IMREAD_COLOR)
        assert screen is not None, f"Failed to load screenshot: {img_path}"

        state, hit = evaluate_fsm_state(screen, templates)
        assert state == expected_state, (
            f"{label}: expected {expected_state}, got {state} (hit={hit})"
        )
        assert hit[2] >= config.CONFIDENCE_THRESHOLD, (
            f"{label}: confidence {hit[2]:.3f} < {config.CONFIDENCE_THRESHOLD}"
        )

        # Also log raw scores for every template to check cross-state separation
        raw_scores = {}
        for s_name, t_key in PRIORITY_ORDER:
            res = cv2.matchTemplate(screen, templates[t_key], cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            raw_scores[t_key] = round(float(max_val), 3)

        print(
            f"[PASS] {label} -> {state} at ({hit[0]}, {hit[1]}) "
            f"conf={hit[2]:.3f} | scores={raw_scores}"
        )

    # 2. Verify squad HP scanner on Image 3
    img3 = cv2.imread(SCREENSHOT_EXPECTATIONS[2][1], cv2.IMREAD_COLOR)
    alive = vision.find_first_alive_moga_slot(
        img3, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET
    )
    assert alive is not None and alive[0] == 1, f"Expected Slot 1 alive, got {alive}"
    print(f"[PASS] Image 3 alive scan -> Slot {alive[0]} at ({alive[1]}, {alive[2]})")

    img3_slot1_fainted = img3.copy()
    black_out_slot_hp(img3_slot1_fainted, 0)
    alive_2 = vision.find_first_alive_moga_slot(
        img3_slot1_fainted, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET
    )
    assert alive_2 is not None and alive_2[0] == 2, f"Expected Slot 2 alive, got {alive_2}"
    print(f"[PASS] Slot 1 fainted scan -> Slot {alive_2[0]} at ({alive_2[1]}, {alive_2[2]})")

    img3_all_fainted = img3.copy()
    for i in range(3):
        black_out_slot_hp(img3_all_fainted, i)
    alive_none = vision.find_first_alive_moga_slot(
        img3_all_fainted, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET
    )
    assert alive_none is None, f"Expected None when all Mogas fainted, got {alive_none}"
    print("[PASS] All slots fainted scan -> None (safe halt)")

    # 3. Multi-scale static-ratio verification (0.75x and 1.25x)
    for scale in (0.75, 1.25):
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        for label, img_path, expected_state in SCREENSHOT_EXPECTATIONS:
            screen = cv2.imread(img_path, cv2.IMREAD_COLOR)
            h, w = screen.shape[:2]
            scaled_screen = cv2.resize(
                screen, (int(round(w * scale)), int(round(h * scale))), interpolation=interp
            )
            state, hit = evaluate_fsm_state(scaled_screen, templates)
            assert state == expected_state, (
                f"[{scale}x] {label}: expected {expected_state}, got {state} (hit={hit})"
            )
            assert hit[2] >= config.CONFIDENCE_THRESHOLD, (
                f"[{scale}x] {label}: confidence {hit[2]:.3f} < {config.CONFIDENCE_THRESHOLD}"
            )
            print(
                f"[PASS] [{scale:.2f}x] {label} -> {state} "
                f"at ({hit[0]}, {hit[1]}) conf={hit[2]:.3f}"
            )

        h3, w3 = img3.shape[:2]
        scaled_img3 = cv2.resize(
            img3, (int(round(w3 * scale)), int(round(h3 * scale))), interpolation=interp
        )
        scaled_alive = vision.find_first_alive_moga_slot(
            scaled_img3, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET
        )
        assert scaled_alive is not None and scaled_alive[0] == 1, (
            f"[{scale}x] Expected Slot 1 alive, got {scaled_alive}"
        )
        print(
            f"[PASS] [{scale:.2f}x] Image 3 alive scan -> "
            f"Slot {scaled_alive[0]} at ({scaled_alive[1]}, {scaled_alive[2]})"
        )

    # 4. Real full-browser window screenshot test (with Global Chat sidebar, browser chrome, taskbar)
    browser_img_path = (
        r"C:/Users/AMD/.gemini/antigravity/brain/c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"/.user_uploaded/media_1795520976085_6ae79517.jpg"
    )
    browser_frame = cv2.imread(browser_img_path, cv2.IMREAD_COLOR)
    assert browser_frame is not None, f"Failed to load browser screenshot: {browser_img_path}"
    bh, bw = browser_frame.shape[:2]
    fake_win_bbox = {"left": 0, "top": 0, "width": bw, "height": bh, "title": "Monster Galaxy - Browser"}

    detected = vision.detect_game_canvas(
        browser_frame,
        fake_win_bbox,
        templates,
        config.TEMPLATE_REF_SIZE,
        config.TEMPLATE_REF_ORIGINS,
        config.CANVAS_DETECT_THRESHOLD,
    )
    assert detected is not None, "Failed to detect game canvas in browser screenshot"
    assert detected["confidence"] >= 0.85, f"Low canvas confidence: {detected['confidence']:.3f}"
    assert detected["anchor"] == "map_idle", f"Expected anchor 'map_idle', got {detected['anchor']}"

    ox, oy, cw, ch = detected["offset_x"], detected["offset_y"], detected["width"], detected["height"]
    canvas_crop = browser_frame[oy:oy + ch, ox:ox + cw]
    b_state, b_hit = evaluate_fsm_state(canvas_crop, templates)
    assert b_state == "MAP_IDLE" and b_hit[2] >= 0.85, f"Browser canvas FSM failed: {b_state}, {b_hit}"

    farm_x = ox + int(cw * config.COORDS["farm_node"][0])
    farm_y = oy + int(ch * config.COORDS["farm_node"][1])
    assert abs(farm_x - 291) <= 6 and abs(farm_y - 383) <= 6, (
        f"Farm node drifted: ({farm_x}, {farm_y}) vs expected ~(291, 383)"
    )
    print(
        f"[PASS] Browser canvas lock-on -> {cw}x{ch} at ({ox}, {oy}) "
        f"scale={detected['scale']:.3f}x conf={detected['confidence']:.3f} | "
        f"FSM={b_state} ({b_hit[2]:.3f}) | farm_node=({farm_x}, {farm_y})"
    )

    # 5. Real live browser battle screenshot (with Brave window offset from user's log)
    battle_browser_path = (
        r"C:/Users/AMD/.gemini/antigravity/brain/c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"/.user_uploaded/media_1795521307501_19267715.jpg"
    )
    battle_frame = cv2.imread(battle_browser_path, cv2.IMREAD_COLOR)
    assert battle_frame is not None, f"Failed to load browser battle screenshot: {battle_browser_path}"
    bbh, bbw = battle_frame.shape[:2]
    # The uploaded screenshot is 1024x548 (desktop width without taskbar), so uniform scale is bbw / 1936.0
    uni_s = bbw / 1936.0
    box = int(round(452 * uni_s))
    boy = int(round(124 * uni_s))
    bcw = int(round(1034 * uni_s))
    bch = min(bbh - boy, int(round(899 * uni_s)))
    battle_crop = battle_frame[boy:boy + bch, box:box + bcw]

    bm_state, bm_hit = evaluate_fsm_state(battle_crop, templates)
    assert bm_state == "BATTLE_SELECT_MOGA" and bm_hit[2] >= 0.85, (
        f"Live browser battle FSM failed: {bm_state}, {bm_hit}"
    )
    live_alive_1 = vision.find_first_alive_moga_slot(
        battle_crop, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET
    )
    assert live_alive_1 is not None and live_alive_1[0] == 1, (
        f"Expected Slot 1 alive on live browser battle screenshot, got {live_alive_1}"
    )

    battle_slot1_fainted = battle_crop.copy()
    black_out_slot_hp(battle_slot1_fainted, 0)
    live_alive_2 = vision.find_first_alive_moga_slot(
        battle_slot1_fainted, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET
    )
    assert live_alive_2 is not None and live_alive_2[0] == 2, (
        f"Expected Slot 2 alive when Slot 1 fainted on live browser screenshot, got {live_alive_2}"
    )

    battle_all_fainted = battle_crop.copy()
    for i in range(3):
        black_out_slot_hp(battle_all_fainted, i)
    live_alive_none = vision.find_first_alive_moga_slot(
        battle_all_fainted, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET
    )
    assert live_alive_none is None, (
        f"Expected None when all Mogas fainted on live browser screenshot, got {live_alive_none}"
    )
    print(
        f"[PASS] Live browser battle screenshot -> FSM={bm_state} ({bm_hit[2]:.3f}) | "
        f"Slot 1={live_alive_1[0]}, Slot 1 fainted->Slot {live_alive_2[0]}, All fainted->None"
    )

    print("\nALL OFFLINE VERIFICATION CHECKS PASSED.")


if __name__ == "__main__":
    main()
