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


def test_legendary_detector_and_settings():
    """Verify multi-tier rarity detection (Super Rare, Epic, Legendary), rejection, settings, and dismiss."""
    sr_crop_path = (
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"\.user_uploaded\media_1795764845474_f400609b.jpg"
    )
    ep_crop_path = (
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"\.user_uploaded\media_1795764880446_cce985d4.jpg"
    )
    leg_crop_path = (
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"\.user_uploaded\media_1795756493353_12bb1bd8.jpg"
    )
    img3_path = (
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"\.user_uploaded\media_1795520215639_00426446.jpg"
    )
    yara_path = (
        r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5"
        r"\.user_uploaded\media_1795521307501_19267715.jpg"
    )
    if all(os.path.exists(p) for p in (sr_crop_path, ep_crop_path, leg_crop_path, img3_path)):
        img3 = cv2.imread(img3_path, cv2.IMREAD_COLOR)
        bh, bw = img3.shape[:2]

        def make_synth(crop_path, crop_box):
            crop = cv2.imread(crop_path, cv2.IMREAD_COLOR)
            lh, lw = crop.shape[:2]
            y0f, y1f, x0f, x1f = crop_box
            hdr = crop[int(lh * y0f):int(lh * y1f), int(lw * x0f):int(lw * x1f)]
            synth = img3.copy()
            y0, y1 = int(bh * 0.145), int(bh * 0.255)
            x0, x1 = int(bw * 0.56), int(bw * 0.90)
            synth[y0:y1, x0:x1] = cv2.resize(hdr, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
            return synth

        synth_sr = make_synth(sr_crop_path, (0.03, 0.21, 0.04, 0.96))
        synth_ep = make_synth(ep_crop_path, (0.03, 0.21, 0.04, 0.96))
        synth_lg = make_synth(leg_crop_path, (0.06, 0.27, 0.06, 0.94))

        assert vision.detect_enemy_rarity(synth_sr) == "super_rare"
        assert vision.detect_enemy_rarity(synth_ep) == "epic"
        assert vision.detect_enemy_rarity(synth_lg) == "legendary"
        assert vision.is_legendary_encounter(synth_lg) is True
        synth_lg_075 = cv2.resize(synth_lg, (int(bw * 0.75), int(bh * 0.75)), interpolation=cv2.INTER_AREA)
        assert vision.detect_enemy_rarity(synth_lg_075) == "legendary"

        if os.path.exists(yara_path):
            yara = cv2.imread(yara_path, cv2.IMREAD_COLOR)
            yh, yw = yara.shape[:2]
            s = yw / 1936.0
            ox, oy = int(round(452 * s)), int(round(124 * s))
            cw, ch = int(round(1034 * s)), min(yh - int(round(124 * s)), int(round(899 * s)))
            yara_canvas = yara[oy:oy + ch, ox:ox + cw]
            assert vision.detect_enemy_rarity(yara_canvas) == "super_rare"

        non_rare_paths = [
            img3_path,
            r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520218061_f52e6ab0.jpg",
            r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795520222735_299644be.jpg",
            r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795525501062_ade8575d.jpg",
            r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded\media_1795525922162_3dbf2781.jpg",
        ]
        for p in non_rare_paths:
            if os.path.exists(p):
                im = cv2.imread(p, cv2.IMREAD_COLOR)
                assert vision.detect_enemy_rarity(im) is None, f"False positive rarity on {p}"
    else:
        print("[SKIP] Rarity reference crops not present; skipping pixel detector check.")

    with tempfile.TemporaryDirectory() as tmp:
        s_path = os.path.join(tmp, "settings.json")
        s1 = settings_store.save_settings(
            {"find_legendary": False, "run_from_non_legendary": True, "target_tiers": ["epic", "invalid", "epic"]},
            path=s_path,
        )
        assert s1["find_legendary"] is False and s1["run_from_non_legendary"] is False
        assert s1["target_tiers"] == ["epic"]
        s2 = settings_store.save_settings(
            {"find_legendary": True, "run_from_non_legendary": True, "target_tiers": ["super_rare", "legendary"]},
            path=s_path,
        )
        assert s2["find_legendary"] is True and s2["run_from_non_legendary"] is True
        assert s2["target_tiers"] == ["super_rare", "legendary"]

    orig_settings = settings_store.load_settings()
    try:
        with TestClient(webapp.app) as client:
            eng = webapp.get_engine()
            with eng._lock:
                eng._legendary_alert = True
                eng._detected_tier = "epic"
                eng._legendaries_found = 1
            st = client.get("/api/status").json()
            assert st["legendary_alert"] is True and st["detected_tier"] == "epic"
            r_dis = client.post("/api/alert/dismiss")
            assert r_dis.status_code == 200
            st2 = client.get("/api/status").json()
            assert st2["legendary_alert"] is False and st2["detected_tier"] is None
    finally:
        if os.path.exists(settings_store.SETTINGS_PATH):
            settings_store.save_settings(orig_settings)

    print("[PASS] Multi-tier rarity detector (Super Rare/Epic/Legendary), settings, and alert checks passed.")


def test_multi_island_map_idle():
    """Verify universal map_idle anchor matches multiple islands and rejects battle screens."""
    base_dir = r"C:\Users\AMD\.gemini\antigravity\brain\c198a83f-a176-47ff-93e3-e078228ce3a5\.user_uploaded"
    island_paths = [
        ("Island 1", os.path.join(base_dir, "media_1795520207439_0f0467a8.jpg")),
        ("Island 1 Return", os.path.join(base_dir, "media_1795520233171_a99e1901.jpg")),
        ("Island 2", os.path.join(base_dir, "media_1795765699457_328d0dfd.jpg")),
    ]
    if not all(os.path.exists(p) for _, p in island_paths):
        print("[SKIP] Island reference screenshots not present; skipping multi-island map_idle check.")
        return

    templates = vision.load_templates(config.TEMPLATE_PATHS)
    for label, path in island_paths:
        im = cv2.imread(path, cv2.IMREAD_COLOR)
        assert im is not None, f"Failed to load {path}"
        hit = vision.find_template(
            im, templates["map_idle"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
        )
        assert hit is not None and hit[2] >= config.CONFIDENCE_THRESHOLD, (
            f"{label}: expected map_idle match >= {config.CONFIDENCE_THRESHOLD}, got {hit}"
        )

    # Also verify 0.75x scale on Island 2
    im2 = cv2.imread(island_paths[2][1], cv2.IMREAD_COLOR)
    h2, w2 = im2.shape[:2]
    im2_075 = cv2.resize(im2, (int(round(w2 * 0.75)), int(round(h2 * 0.75))), interpolation=cv2.INTER_AREA)
    hit2_075 = vision.find_template(
        im2_075, templates["map_idle"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
    )
    assert hit2_075 is not None and hit2_075[2] >= config.CONFIDENCE_THRESHOLD, (
        f"Island 2 [0.75x]: expected map_idle match, got {hit2_075}"
    )

    # Verify map_idle does NOT trigger on battle screens
    battle_paths = [
        os.path.join(base_dir, "media_1795520215639_00426446.jpg"),
        os.path.join(base_dir, "media_1795520218061_f52e6ab0.jpg"),
        os.path.join(base_dir, "media_1795520222735_299644be.jpg"),
        os.path.join(base_dir, "media_1795520225968_f46f7388.jpg"),
        os.path.join(base_dir, "media_1795525922162_3dbf2781.jpg"),
        os.path.join(base_dir, "media_1795526560424_00a3ad06.jpg"),
    ]
    for p in battle_paths:
        if os.path.exists(p):
            bim = cv2.imread(p, cv2.IMREAD_COLOR)
            fp = vision.find_template(
                bim, templates["map_idle"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
            )
            assert fp is None, f"Unexpected map_idle false positive on battle screen {p}: {fp}"

    # Verify NODE_POPUP still beats MAP_IDLE on popup screenshot
    popup_path = os.path.join(base_dir, "media_1795520210855_5e9fa74d.jpg")
    if os.path.exists(popup_path):
        pim = cv2.imread(popup_path, cv2.IMREAD_COLOR)
        p_hit = vision.find_template(
            pim, templates["node_popup"], config.CONFIDENCE_THRESHOLD, config.TEMPLATE_REF_SIZE
        )
        assert p_hit is not None and p_hit[2] >= config.CONFIDENCE_THRESHOLD, (
            f"Expected node_popup match on popup screenshot, got {p_hit}"
        )

    print("[PASS] Multi-island universal map_idle & battle rejection checks passed.")


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
    test_legendary_detector_and_settings()
    test_multi_island_map_idle()
    print("\nALL WEBAPP TESTS PASSED.")
