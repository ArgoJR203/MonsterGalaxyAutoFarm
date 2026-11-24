"""
Interactive setup utility to:
1. Generate a calibration overlay image verifying all normalized click coordinates.
2. Interactively crop any missing UI templates directly from the live game window.
"""

import os
import sys
import cv2
import mss

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
import config
import vision


TEMPLATE_INSTRUCTIONS = [
    ("map_idle", "templates/map_discord.png", "On the MAP screen, drag a box around the 'DISCORD SERVER' sign."),
    ("node_popup", "templates/popup_go.png", "Open the node popup and drag a box around the light-blue 'GO!' button."),
    ("select_moga", "templates/choose_moga.png", "Enter battle and drag a box around the 'CHOOSE YOUR MOGA' text."),
    ("combat_ready", "templates/battle_red_go.png", "Pick a Moga and drag a box around the large red 'GO!' button."),
    ("combat_active", "templates/battle_capture.png", "Click red GO! and drag a box around the center 'CAPTURE' button."),
    ("victory", "templates/battle_continue.png", "Defeat enemy and drag a box around the light-blue 'Continue' button."),
]


def save_calibration_overlay(screen, bbox) -> None:
    """Draws target markers for all normalized coordinates onto a debug screenshot."""
    vis = screen.copy()
    h, w = vis.shape[:2]

    points = {
        "FARM_NODE": config.COORDS["farm_node"],
        "SKILL_TL": config.COORDS["skill_top_left"],
        "SKILL_TR": config.COORDS["skill_top_right"],
        "SKILL_BL": config.COORDS["skill_bottom_left"],
    }
    for idx, slot in enumerate(config.COORDS["squad_slots"], start=1):
        points[f"SLOT_{idx}"] = slot
        points[f"HP_{idx}"] = (slot[0], slot[1] + config.HP_BAR_Y_OFFSET)

    for label, (rx, ry) in points.items():
        px, py = int(w * rx), int(h * ry)
        color = (0, 255, 0) if label.startswith("HP_") else (0, 0, 255)
        cv2.circle(vis, (px, py), 5, color, -1)
        cv2.putText(vis, label, (px + 6, py), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

    out_file = os.path.join(PROJECT_ROOT, "calibration_overlay.png")
    cv2.imwrite(out_file, vis)
    print(f"[SAVED] {out_file} ({w}x{h}) — inspect to verify dot alignment.")


def run_wizard() -> None:
    vision.enable_dpi_awareness()
    os.makedirs(os.path.join(PROJECT_ROOT, "templates"), exist_ok=True)
    win_bbox = vision.get_game_window_bbox(config.WINDOW_TITLE)
    print(f"Found game window: '{win_bbox['title']}' ({win_bbox['width']}x{win_bbox['height']})")

    bbox = win_bbox
    mss_cls = getattr(mss, "MSS", mss.mss)
    with mss_cls() as sct:
        frame = vision.grab_frame(sct, win_bbox)
        try:
            templates = vision.load_templates(config.TEMPLATE_PATHS)
            detected = vision.detect_game_canvas(
                frame,
                win_bbox,
                templates,
                config.TEMPLATE_REF_SIZE,
                config.TEMPLATE_REF_ORIGINS,
                config.CANVAS_DETECT_THRESHOLD,
            )
            if detected is not None:
                bbox = detected
                frame = vision.grab_frame(sct, bbox)
                print(
                    f"Locked onto game canvas: {bbox['width']}x{bbox['height']} "
                    f"at offset=({detected['offset_x']}, {detected['offset_y']})"
                )
        except FileNotFoundError:
            pass

        save_calibration_overlay(frame, bbox)

        print("\n--- Interactive Template Cropper ---")
        print("For each prompt: navigate the game to that screen, press ENTER in terminal,")
        print("drag a tight selection box around the target UI element, and press SPACE/ENTER.")
        print("Press 'c' during selection to skip an already-existing template.\n")

        for key, rel_path, prompt in TEMPLATE_INSTRUCTIONS:
            out_path = os.path.join(PROJECT_ROOT, rel_path)
            input(f"[{key}] {prompt}\nPress ENTER when the game screen is ready...")
            frame = vision.grab_frame(sct, bbox)
            roi = cv2.selectROI(f"Crop: {key} (Press ENTER to confirm, C to skip)", frame, showCrosshair=True)
            cv2.destroyAllWindows()

            x, y, w, h = roi
            if w > 0 and h > 0:
                cropped = frame[y:y + h, x:x + w]
                cv2.imwrite(out_path, cropped)
                print(f"  -> Saved {out_path} ({w}x{h})")
            else:
                print(f"  -> Skipped {out_path}")


if __name__ == "__main__":
    run_wizard()
