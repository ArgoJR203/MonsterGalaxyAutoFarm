"""Tests for WebApp settings persistence, squad slot priority, and FastAPI routes."""

import os
import sys
import tempfile
import cv2
from fastapi.testclient import TestClient

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import config
import settings_store
import vision
import webapp


def test_squad_priority():
    """Verify find_first_alive_moga_slot respects custom priority and skips disabled slots."""
    img3_path = os.path.join(
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded",
        "media_1795520215639_00426446.jpg",
    )
    if not os.path.exists(img3_path):
        print("[SKIP] Local reference screenshot not present; skipping pixel priority check.")
        return

    img3 = cv2.imread(img3_path, cv2.IMREAD_COLOR)
    assert img3 is not None

    s3 = vision.find_first_alive_moga_slot(
        img3, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, [3, 2, 1]
    )
    assert s3 is not None and s3[0] == 3, f"Expected Slot 3 first, got {s3}"

    s2 = vision.find_first_alive_moga_slot(
        img3, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, [2]
    )
    assert s2 is not None and s2[0] == 2, f"Expected Slot 2, got {s2}"

    s_none = vision.find_first_alive_moga_slot(
        img3, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, [4, 5, 6]
    )
    assert s_none is None, f"Expected None for empty slots [4,5,6], got {s_none}"

    s_empty = vision.find_first_alive_moga_slot(
        img3, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, []
    )
    assert s_empty is None, f"Expected None for empty priority list, got {s_empty}"
    print("[PASS] Squad priority order checks passed.")


def test_settings_store():
    """Verify settings_store load/save roundtrip and sanitization."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "settings.json")
        initial = settings_store.load_settings(path)
        assert initial["active_node_name"] == "Mega Hole (Beach)"
        assert initial["squad_priority"] == [1, 2, 3, 4, 5, 6]

        updated = settings_store.save_settings(
            {
                "dry_run": True,
                "confidence_threshold": 1.5,  # should clamp to 0.99
                "max_primary_turns": 0,       # should clamp to 1
                "primary_skill": "invalid",   # should fallback to skill_top_left
                "squad_priority": [3, 1, 3, 99, 2],
                "farm_nodes": {
                    "Mega Hole (Beach)": [0.105, 0.672],
                    "Windhym": [0.75, 0.25],
                },
                "active_node_name": "Windhym",
            },
            path=path,
        )
        assert updated["dry_run"] is True
        assert updated["confidence_threshold"] == 0.99
        assert updated["max_primary_turns"] == 1
        assert updated["primary_skill"] == "skill_top_left"
        assert updated["squad_priority"] == [3, 1, 2]
        assert settings_store.get_active_farm_node(updated) == (0.75, 0.25)

        reloaded = settings_store.load_settings(path)
        assert reloaded == updated
    print("[PASS] settings_store persistence and sanitization checks passed.")


def test_fastapi_routes():
    """Verify FastAPI endpoints serve UI, status, preview, and settings updates."""
    orig_settings = settings_store.load_settings()
    try:
        with TestClient(webapp.app) as client:
            r_index = client.get("/")
            assert r_index.status_code == 200
            assert "Monster Galaxy Auto-Farm" in r_index.text

            r_status = client.get("/api/status")
            assert r_status.status_code == 200
            body = r_status.json()
            assert body["status"] == "idle"
            assert body["browser_open"] is False
            assert "settings" in body

            r_prev = client.get("/api/preview.jpg")
            assert r_prev.status_code == 204

            r_set = client.post(
                "/api/settings",
                json={"dry_run": True, "squad_priority": [2, 1, 3]},
            )
            assert r_set.status_code == 200
            assert r_set.json()["settings"]["dry_run"] is True
            assert r_set.json()["settings"]["squad_priority"] == [2, 1, 3]

            # Pause deadlock regression check
            eng = webapp.get_engine()
            with eng._lock:
                eng._status = "running"
            r_pause = client.post("/api/bot/pause")
            assert r_pause.status_code == 200
            # Wait briefly for worker loop to process queued pause command without deadlocking
            import time
            deadline = time.time() + 2.0
            paused_ok = False
            while time.time() < deadline:
                st = client.get("/api/status").json()
                if st["status"] == "paused":
                    paused_ok = True
                    break
                time.sleep(0.05)
            assert paused_ok, f"Pause command stalled or failed: {client.get('/api/status').json()['status']}"
            client.post("/api/bot/stop")
    finally:
        # Restore original settings.json if created during test
        if os.path.exists(settings_store.SETTINGS_PATH):
            settings_store.save_settings(orig_settings)

    print("[PASS] FastAPI endpoint checks passed.")


def test_micro_scale_choose_moga():
    """Verify choose_moga matches on user's preview screenshot and with +-4% scale distortion."""
    preview_path = (
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"\.user_uploaded\media_1795524050748_00db7db1.jpg"
    )
    if not os.path.exists(preview_path):
        print("[SKIP] Preview screenshot not present; skipping micro-scale check.")
        return

    img = cv2.imread(preview_path, cv2.IMREAD_COLOR)
    assert img is not None
    templates = vision.load_templates(config.TEMPLATE_PATHS)

    hit = vision.find_template(img, templates["select_moga"], 0.85, config.TEMPLATE_REF_SIZE)
    assert hit is not None and hit[2] >= 0.85, f"Expected select_moga match on preview, got {hit}"

    # Strip the side dark bars so we have a clean canvas, then test +-3% padding/scale drift
    canvas_clean = img[:, 36:652]
    h, w = canvas_clean.shape[:2]
    for factor in (0.97, 1.03):
        scaled_content = cv2.resize(
            canvas_clean,
            (int(round(w * factor)), int(round(h * factor))),
            interpolation=cv2.INTER_AREA if factor < 1.0 else cv2.INTER_LINEAR,
        )
        if factor < 1.0:
            canvas = cv2.copyMakeBorder(
                scaled_content,
                0,
                h - scaled_content.shape[0],
                0,
                w - scaled_content.shape[1],
                cv2.BORDER_CONSTANT,
                value=(20, 20, 20),
            )
        else:
            canvas = scaled_content[:h, :w]
        d_hit = vision.find_template(canvas, templates["select_moga"], 0.85, config.TEMPLATE_REF_SIZE)
        assert d_hit is not None and d_hit[2] >= 0.85, (
            f"Expected select_moga match with scale factor={factor}, got {d_hit}"
        )
    print(f"[PASS] Micro-scale choose_moga fallback checks passed (native conf={hit[2]:.3f}).")


def test_dom_canvas_hp_band_regression():
    """Verify live DOM canvas HP bar position (y~0.830-0.835) and orange fur rejection."""
    shot_path = (
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"\.user_uploaded\media_1795525501062_ade8575d.jpg"
    )
    if not os.path.exists(shot_path):
        print("[SKIP] DOM battle screenshot not present; skipping HP band regression check.")
        return

    img = cv2.imread(shot_path, cv2.IMREAD_COLOR)
    assert img is not None

    s1 = vision.find_first_alive_moga_slot(
        img, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, [1, 2, 3]
    )
    assert s1 is not None and s1[0] == 1, f"Expected Slot 1 alive, got {s1}"

    s2 = vision.find_first_alive_moga_slot(
        img, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, [2, 3]
    )
    assert s2 is not None and s2[0] == 2, f"Expected Slot 2 alive, got {s2}"

    s3 = vision.find_first_alive_moga_slot(
        img, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, [3]
    )
    assert s3 is not None and s3[0] == 3, f"Expected Slot 3 alive, got {s3}"

    # Zero out ONLY the actual HP bar strip (y=0.829..0.837) and verify portrait fur doesn't trigger alive
    fainted = img.copy()
    h, w = fainted.shape[:2]
    y0, y1 = int(h * 0.828), int(h * 0.838)
    for rx, _ in config.COORDS["squad_slots"][:3]:
        px = int(w * rx)
        fainted[y0:y1, max(0, px - 25):min(w, px + 25)] = (30, 30, 30)

    none_alive = vision.find_first_alive_moga_slot(
        fainted, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, [1, 2, 3]
    )
    assert none_alive is None, f"Expected None when HP bars cleared (fur rejection), got {none_alive}"
    print("[PASS] DOM canvas HP band & orange-fur rejection checks passed.")


def test_background_invariant_select_moga():
    """Verify masked select_moga works on non-beach arena (green grass) without false positives."""
    grass_path = (
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"\.user_uploaded\media_1795525922162_3dbf2781.jpg"
    )
    if not os.path.exists(grass_path):
        print("[SKIP] Green-grass arena screenshot not present; skipping masked check.")
        return

    img = cv2.imread(grass_path, cv2.IMREAD_COLOR)
    assert img is not None
    templates = vision.load_templates(config.TEMPLATE_PATHS)
    mask = vision.build_text_outline_mask(templates["select_moga"])

    hit = vision.find_template(
        img, templates["select_moga"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE, mask=mask
    )
    assert hit is not None and hit[2] >= config.CONFIDENCE_THRESHOLD, (
        f"Expected masked select_moga match on green grass arena, got {hit}"
    )

    alive = vision.find_first_alive_moga_slot(
        img, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, [1, 2, 3]
    )
    assert alive is not None and alive[0] == 1, f"Expected Slot 1 alive on green grass arena, got {alive}"

    non_select_screens = [
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520207439_0f0467a8.jpg",
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520210855_5e9fa74d.jpg",
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520218061_f52e6ab0.jpg",
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520222735_299644be.jpg",
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520225968_f46f7388.jpg",
    ]
    for p in non_select_screens:
        if os.path.exists(p):
            other = cv2.imread(p, cv2.IMREAD_COLOR)
            fp = vision.find_template(
                other, templates["select_moga"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE, mask=mask
            )
            assert fp is None, f"Unexpected masked select_moga false positive on {p}: {fp}"

    print(f"[PASS] Background-invariant select_moga checks passed (grass conf={hit[2]:.3f}).")


def test_background_invariant_victory():
    """Verify trimmed Continue button matches on dark/hut arena victory screens."""
    hut_victory = (
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"\.user_uploaded\media_1795526560424_00a3ad06.jpg"
    )
    if not os.path.exists(hut_victory):
        print("[SKIP] Hut victory screenshot not present; skipping victory check.")
        return

    img = cv2.imread(hut_victory, cv2.IMREAD_COLOR)
    assert img is not None
    templates = vision.load_templates(config.TEMPLATE_PATHS)
    hit = vision.find_template(
        img, templates["victory"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
    )
    assert hit is not None and hit[2] >= config.CONFIDENCE_THRESHOLD, (
        f"Expected victory Continue match on hut arena, got {hit}"
    )
    print(f"[PASS] Background-invariant victory check passed (hut conf={hit[2]:.3f}).")


def test_quest_and_capture_failsafes():
    """Verify quest_ok, capture_modal failsafe, capture_close, and upper-only skill enforcement."""
    with tempfile.TemporaryDirectory() as tmp:
        s_path = os.path.join(tmp, "settings.json")
        sanitized = settings_store.save_settings({"primary_skill": "skill_bottom_left"}, path=s_path)
        assert sanitized["primary_skill"] == "skill_top_left", (
            f"Expected lower skill to sanitize to skill_top_left, got {sanitized['primary_skill']}"
        )
        assert "skill_bottom_left" not in config.COORDS

    quest_path = (
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"\.user_uploaded\media_1795527553244_fe542918.jpg"
    )
    capture_path = (
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"\.user_uploaded\media_1795527735446_4166b6b6.jpg"
    )
    if not (os.path.exists(quest_path) and os.path.exists(capture_path)):
        print("[SKIP] Quest/Capture modal screenshots not present; skipping modal checks.")
        return

    templates = vision.load_templates(config.TEMPLATE_PATHS)
    quest_raw = cv2.imread(quest_path, cv2.IMREAD_COLOR)
    capture_raw = cv2.imread(capture_path, cv2.IMREAD_COLOR)

    quest_canvas = quest_raw[1:713, 104:923]
    capture_canvas = capture_raw[1:704, 113:922]

    q_hit = vision.find_template(
        quest_canvas, templates["quest_ok"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
    )
    assert q_hit is not None and q_hit[2] >= config.CONFIDENCE_THRESHOLD, (
        f"Expected quest_ok match, got {q_hit}"
    )

    c_hit = vision.find_template(
        capture_canvas, templates["capture_modal"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
    )
    assert c_hit is not None and c_hit[2] >= config.CONFIDENCE_THRESHOLD, (
        f"Expected capture_modal match, got {c_hit}"
    )

    # Verify capture_close hits bright white X button area in capture_canvas
    ch, cw = capture_canvas.shape[:2]
    cx = int(cw * config.COORDS["capture_close"][0])
    cy = int(ch * config.COORDS["capture_close"][1])
    patch = capture_canvas[max(0, cy - 8):min(ch, cy + 9), max(0, cx - 8):min(cw, cx + 9)]
    assert patch.max() > 200, f"Expected bright white X close button near ({cx}, {cy})"

    # Verify zero false positives across all standard screens and between modals
    other_paths = [
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520207439_0f0467a8.jpg",
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520210855_5e9fa74d.jpg",
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520215639_00426446.jpg",
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520218061_f52e6ab0.jpg",
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520222735_299644be.jpg",
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520225968_f46f7388.jpg",
    ]
    assert vision.find_template(quest_canvas, templates["capture_modal"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE) is None
    assert vision.find_template(capture_canvas, templates["quest_ok"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE) is None
    for p in other_paths:
        if os.path.exists(p):
            im = cv2.imread(p, cv2.IMREAD_COLOR)
            assert vision.find_template(im, templates["quest_ok"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE) is None, p
            assert vision.find_template(im, templates["capture_modal"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE) is None, p

    print(
        f"[PASS] Quest Complete (conf={q_hit[2]:.3f}) & Capture Modal failsafe "
        f"(conf={c_hit[2]:.3f}, close=({cx}, {cy})) checks passed."
    )


def test_skill_availability_and_switch():
    """Verify is_skill_available detects 0-PP dimmed upper skills and supports party switch."""
    img4_path = r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520218061_f52e6ab0.jpg"
    img5_path = r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520222735_299644be.jpg"
    img6_path = r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520225968_f46f7388.jpg"
    if not all(os.path.exists(p) for p in (img4_path, img5_path, img6_path)):
        print("[SKIP] Battle reference screenshots not present; skipping skill availability test.")
        return

    img4 = cv2.imread(img4_path, cv2.IMREAD_COLOR)
    img5 = cv2.imread(img5_path, cv2.IMREAD_COLOR)
    img6 = cv2.imread(img6_path, cv2.IMREAD_COLOR)

    assert vision.is_skill_available(img5, config.COORDS["skill_top_left"]) is True
    assert vision.is_skill_available(img5, config.COORDS["skill_top_right"]) is True
    assert vision.is_skill_available(img4, config.COORDS["skill_top_left"]) is False
    assert vision.is_skill_available(img4, config.COORDS["skill_top_right"]) is False
    assert vision.is_skill_available(img6, config.COORDS["skill_top_left"]) is False
    assert vision.is_skill_available(img6, config.COORDS["skill_top_right"]) is False

    # Simulate upper-left skill out of PP (dimmed from img4), upper-right still active
    h, w = img5.shape[:2]
    tl_only_out = img5.copy()
    tl_px, tl_py = int(w * config.COORDS["skill_top_left"][0]), int(h * config.COORDS["skill_top_left"][1])
    dx, dy = int(w * 0.06), int(h * 0.02)
    tl_only_out[tl_py - dy:tl_py + dy, tl_px - dx:tl_px + dx] = img4[tl_py - dy:tl_py + dy, tl_px - dx:tl_px + dx]

    assert vision.is_skill_available(tl_only_out, config.COORDS["skill_top_left"]) is False
    assert vision.is_skill_available(tl_only_out, config.COORDS["skill_top_right"]) is True

    # Simulate both upper skills out of PP -> switch to next alive priority team member (Slot 2)
    both_out = tl_only_out.copy()
    tr_px, tr_py = int(w * config.COORDS["skill_top_right"][0]), int(h * config.COORDS["skill_top_right"][1])
    both_out[tr_py - dy:tr_py + dy, tr_px - dx:tr_px + dx] = img4[tr_py - dy:tr_py + dy, tr_px - dx:tr_px + dx]

    assert vision.is_skill_available(both_out, config.COORDS["skill_top_left"]) is False
    assert vision.is_skill_available(both_out, config.COORDS["skill_top_right"]) is False
    next_alive = vision.find_first_alive_moga_slot(
        both_out, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, [2, 3]
    )
    assert next_alive is not None and next_alive[0] == 2, f"Expected switch to Slot 2, got {next_alive}"
    print("[PASS] Upper-skill availability & mid-battle squad switch checks passed.")


if __name__ == "__main__":
    test_squad_priority()
    test_settings_store()
    test_fastapi_routes()
    test_micro_scale_choose_moga()
    test_dom_canvas_hp_band_regression()
    test_background_invariant_select_moga()
    test_background_invariant_victory()
    test_quest_and_capture_failsafes()
    test_skill_availability_and_switch()
    print("\nALL WEBAPP TESTS PASSED.")
