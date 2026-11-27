"""Playwright-backed background farming engine controlled by the WebApp."""

from collections import deque
import os
import queue
import random
import threading
import time

import cv2
import numpy as np
from playwright.sync_api import sync_playwright

import config
import settings_store
import vision

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(PROJECT_ROOT, ".browser_profile")


class FarmEngine:
    """Owns the Playwright browser lifecycle and runs the 6-state FSM in a worker thread."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cmd_queue: queue.Queue = queue.Queue()
        self._stop_worker = threading.Event()

        self._templates = vision.load_templates(config.TEMPLATE_PATHS)
        self._select_moga_mask = vision.build_text_outline_mask(self._templates["select_moga"])
        self._settings = settings_store.load_settings()

        self._status = "idle"  # idle | running | paused | halted
        self._browser_open = False
        self._current_fsm_state: str | None = None
        self._victories = 0
        self._turns_in_battle = 0
        self._zero_alive_streak = 0
        self._active_slot: int | None = None
        self._exhausted_slots: set[int] = set()
        self._exhausted_skills: set[str] = set()
        self._last_skill_clicked: str | None = None
        self._consecutive_active_clicks = 0
        self._legendary_alert = False
        self._detected_tier: str | None = None
        self._legendaries_found = 0
        self._legendary_detected_at: float | None = None
        self._start_time: float | None = None
        self._last_state_time = time.time()
        self._canvas_offset: tuple[int, int, int, int] | None = None
        self._canvas_info: dict | None = None
        self._last_viewport: tuple[int, int] | None = None
        self._latest_preview_jpeg: bytes | None = None
        self._logs: deque[str] = deque(maxlen=200)

        self._playwright = None
        self._context = None
        self._page = None

        self._thread = threading.Thread(target=self._worker_loop, name="FarmEngineWorker", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Thread-safe public API (called from FastAPI handlers)
    # ------------------------------------------------------------------
    def open_browser(self) -> None:
        self._cmd_queue.put(("open_browser", None))

    def close_browser(self) -> None:
        self._cmd_queue.put(("close_browser", None))

    def start(self) -> None:
        self._cmd_queue.put(("start", None))

    def pause(self) -> None:
        self._cmd_queue.put(("pause", None))

    def stop(self) -> None:
        self._cmd_queue.put(("stop", None))

    def refresh_preview(self) -> None:
        self._cmd_queue.put(("refresh_preview", None))

    def dismiss_legendary_alert(self) -> None:
        with self._lock:
            self._legendary_alert = False
            self._detected_tier = None

    def update_settings(self, updates: dict) -> dict:
        saved = settings_store.save_settings(updates)
        with self._lock:
            self._settings = saved
        self._log(
            f"[SETTINGS] Saved: node='{saved['active_node_name']}', "
            f"squad={saved['squad_priority']}, skill={saved['primary_skill']}, "
            f"finder={saved['find_legendary']} ({saved['target_tiers']}), "
            f"hunt_run={saved['run_from_non_legendary']}, dry_run={saved['dry_run']}"
        )
        return saved

    def get_status(self) -> dict:
        with self._lock:
            elapsed_min = 0.0
            runs_per_min = 0.0
            if self._start_time is not None:
                elapsed_min = max(0.01, (time.time() - self._start_time) / 60.0)
                runs_per_min = round(self._victories / elapsed_min, 2)
            return {
                "status": self._status,
                "browser_open": self._browser_open,
                "current_fsm_state": self._current_fsm_state,
                "victories": self._victories,
                "runs_per_min": runs_per_min,
                "turns_in_battle": self._turns_in_battle,
                "canvas_locked": self._canvas_offset is not None,
                "canvas_info": self._canvas_info,
                "legendary_alert": self._legendary_alert,
                "detected_tier": self._detected_tier,
                "legendaries_found": self._legendaries_found,
                "legendary_detected_at": self._legendary_detected_at,
                "settings": self._settings,
                "logs": list(self._logs),
            }

    def get_preview_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_preview_jpeg

    def shutdown(self) -> None:
        self._stop_worker.set()
        self._cmd_queue.put(("shutdown", None))
        self._thread.join(timeout=5.0)

    # ------------------------------------------------------------------
    # Internal worker thread (owns all Playwright calls)
    # ------------------------------------------------------------------
    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        with self._lock:
            self._logs.append(line)
        print(line)

    def _worker_loop(self) -> None:
        last_idle_preview = 0.0
        try:
            while not self._stop_worker.is_set():
                self._drain_commands()

                if self._stop_worker.is_set():
                    break

                # Detect manual browser closure
                if self._page is not None and self._page.is_closed():
                    self._handle_browser_lost("[WARN] Browser tab/window was closed.")

                with self._lock:
                    status = self._status
                    browser_open = self._browser_open

                if not browser_open or self._page is None:
                    time.sleep(0.2)
                    continue

                if status == "running":
                    sleep_sec = self._run_fsm_tick()
                    time.sleep(sleep_sec)
                else:
                    # Refresh live preview roughly every 1.0s while idle/paused so node picker stays live
                    now = time.time()
                    if now - last_idle_preview >= 1.0:
                        self._capture_and_store_preview()
                        last_idle_preview = now
                    time.sleep(0.15)
        finally:
            self._teardown_playwright()

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd, _ = self._cmd_queue.get_nowait()
            except queue.Empty:
                break

            if cmd == "open_browser":
                self._cmd_open_browser()
            elif cmd == "close_browser":
                self._cmd_close_browser()
            elif cmd == "start":
                self._cmd_start()
            elif cmd == "pause":
                self._cmd_pause()
            elif cmd == "stop":
                self._cmd_stop()
            elif cmd == "refresh_preview":
                self._capture_and_store_preview()
            elif cmd == "shutdown":
                self._stop_worker.set()
                break

    def _cmd_open_browser(self) -> None:
        if self._browser_open and self._page is not None and not self._page.is_closed():
            self._log("[BROWSER] Game browser is already open.")
            return

        with self._lock:
            game_url = self._settings["game_url"]

        try:
            os.makedirs(PROFILE_DIR, exist_ok=True)
            if self._playwright is None:
                self._playwright = sync_playwright().start()

            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=False,
                viewport={"width": 1280, "height": 900},
                args=[
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                ],
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page.goto(game_url, wait_until="domcontentloaded")

            with self._lock:
                self._browser_open = True
                self._canvas_offset = None
                self._canvas_info = None
                self._last_viewport = None
            self._log(f"[BROWSER] Opened {game_url} (profile saved in .browser_profile/).")
            self._capture_and_store_preview()
        except Exception as err:
            self._log(f"[ERROR] Failed to launch Chromium: {err}")
            self._teardown_playwright()

    def _cmd_close_browser(self) -> None:
        self._teardown_playwright()
        with self._lock:
            self._status = "idle"
            self._current_fsm_state = None
        self._log("[BROWSER] Closed game browser.")

    def _reset_battle_tracking(self) -> None:
        """Clears per-battle skill and squad exhaustion state (caller holds _lock or safe context)."""
        self._turns_in_battle = 0
        self._zero_alive_streak = 0
        self._active_slot = None
        self._exhausted_slots.clear()
        self._exhausted_skills.clear()
        self._last_skill_clicked = None
        self._consecutive_active_clicks = 0

    def _cmd_start(self) -> None:
        if not self._browser_open or self._page is None or self._page.is_closed():
            self._cmd_open_browser()
            if not self._browser_open:
                return

        with self._lock:
            was_paused = self._status == "paused"
            self._status = "running"
            self._last_state_time = time.time()
            self._zero_alive_streak = 0
            self._consecutive_active_clicks = 0
            self._legendary_alert = False
            self._detected_tier = None
            if not was_paused:
                self._victories = 0
                self._reset_battle_tracking()
                self._start_time = time.time()
        self._log("[BOT] Farming resumed." if was_paused else "[BOT] Farming started.")

    def _cmd_pause(self) -> None:
        paused = False
        with self._lock:
            if self._status == "running":
                self._status = "paused"
                paused = True
        if paused:
            self._log("[BOT] Farming paused.")

    def _cmd_stop(self) -> None:
        with self._lock:
            self._status = "idle"
            self._current_fsm_state = None
            self._legendary_alert = False
            self._detected_tier = None
            self._reset_battle_tracking()
        self._log("[BOT] Farming stopped.")

    def _handle_browser_lost(self, reason: str) -> None:
        self._log(reason)
        self._teardown_playwright()
        with self._lock:
            self._status = "idle"
            self._current_fsm_state = None

    def _teardown_playwright(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._page = None
        self._playwright = None
        with self._lock:
            self._browser_open = False
            self._canvas_offset = None
            self._canvas_info = None
            self._last_viewport = None
            self._latest_preview_jpeg = None

    def _grab_page_bgr(self) -> np.ndarray | None:
        if self._page is None or self._page.is_closed():
            return None
        try:
            png_bytes = self._page.screenshot(type="jpeg", quality=85)
            buf = np.frombuffer(png_bytes, dtype=np.uint8)
            frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            return frame
        except Exception as err:
            if self._page is None or self._page.is_closed():
                self._handle_browser_lost("[WARN] Browser closed during capture.")
            else:
                self._log(f"[WARN] Capture error: {err}")
            return None

    def _ensure_canvas_lock(self, full_frame: np.ndarray) -> tuple[int, int, int, int] | None:
        fh, fw = full_frame.shape[:2]
        vp = (fw, fh)
        with self._lock:
            cached = self._canvas_offset
            last_vp = self._last_viewport

        if cached is not None and last_vp == vp:
            return cached

        # 1. Prefer exact HTML5 <canvas> bounding rect from DOM (eliminates sidebars/padding drift)
        detected = None
        if self._page is not None and not self._page.is_closed():
            try:
                dom_rect = self._page.evaluate(
                    """() => {
                        let best = null;
                        let bestArea = 0;
                        for (const c of document.querySelectorAll('canvas')) {
                            const r = c.getBoundingClientRect();
                            if (r.width > 300 && r.height > 300) {
                                const area = r.width * r.height;
                                if (area > bestArea) {
                                    bestArea = area;
                                    best = {
                                        x: Math.round(r.left),
                                        y: Math.round(r.top),
                                        w: Math.round(r.width),
                                        h: Math.round(r.height),
                                    };
                                }
                            }
                        }
                        return best;
                    }"""
                )
                if dom_rect:
                    cx = max(0, int(dom_rect["x"]))
                    cy = max(0, int(dom_rect["y"]))
                    cw = min(fw - cx, int(dom_rect["w"]))
                    ch = min(fh - cy, int(dom_rect["h"]))
                    if cw > 300 and ch > 300:
                        ref_w, ref_h = config.TEMPLATE_REF_SIZE
                        detected = {
                            "offset_x": cx,
                            "offset_y": cy,
                            "width": cw,
                            "height": ch,
                            "scale": min(cw / ref_w, ch / ref_h),
                            "anchor": "dom_canvas",
                            "confidence": 1.0,
                        }
            except Exception:
                detected = None

        # 2. Fallback to multi-scale OpenCV canvas detection
        if detected is None:
            win_bbox = {"left": 0, "top": 0, "width": fw, "height": fh, "title": "Playwright"}
            detected = vision.detect_game_canvas(
                full_frame,
                win_bbox,
                self._templates,
                config.TEMPLATE_REF_SIZE,
                config.TEMPLATE_REF_ORIGINS,
                config.CANVAS_DETECT_THRESHOLD,
            )
        if detected is None:
            return None

        offset = (detected["offset_x"], detected["offset_y"], detected["width"], detected["height"])
        info = {
            "width": detected["width"],
            "height": detected["height"],
            "offset_x": detected["offset_x"],
            "offset_y": detected["offset_y"],
            "scale": round(detected["scale"], 3),
            "anchor": detected["anchor"],
            "confidence": round(detected["confidence"], 3),
        }
        with self._lock:
            self._canvas_offset = offset
            self._canvas_info = info
            self._last_viewport = vp
        self._log(
            f"[INIT] Locked onto game canvas: {info['width']}x{info['height']} "
            f"at offset=({info['offset_x']}, {info['offset_y']}) "
            f"scale={info['scale']:.2f}x via '{info['anchor']}' (conf={info['confidence']:.2f})"
        )
        return offset

    def _store_preview_frame(self, img_bgr: np.ndarray) -> None:
        ok, enc = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok:
            with self._lock:
                self._latest_preview_jpeg = enc.tobytes()

    def _capture_and_store_preview(self) -> None:
        full_frame = self._grab_page_bgr()
        if full_frame is None:
            return
        offset = self._ensure_canvas_lock(full_frame)
        if offset is not None:
            ox, oy, cw, ch = offset
            canvas_img = full_frame[oy:oy + ch, ox:ox + cw]
            self._store_preview_frame(canvas_img)
        else:
            self._store_preview_frame(full_frame)

    def _click_canvas(self, offset: tuple[int, int, int, int], rel_x: int, rel_y: int, label: str, dry_run: bool) -> None:
        ox, oy, _, _ = offset
        jx = random.randint(-config.CLICK_JITTER_PX, config.CLICK_JITTER_PX)
        jy = random.randint(-config.CLICK_JITTER_PX, config.CLICK_JITTER_PX)
        target_x = ox + rel_x + jx
        target_y = oy + rel_y + jy
        tag = "[DRY-RUN]" if dry_run else "[CLICK]"
        self._log(f"{tag} {label} at canvas=({rel_x}, {rel_y})")
        if not dry_run and self._page is not None and not self._page.is_closed():
            self._page.mouse.click(target_x, target_y)

    def _click_canvas_norm(self, offset: tuple[int, int, int, int], norm_xy: tuple[float, float], label: str, dry_run: bool) -> None:
        _, _, cw, ch = offset
        px = int(cw * norm_xy[0])
        py = int(ch * norm_xy[1])
        self._click_canvas(offset, px, py, label, dry_run)

    def _trigger_legendary_pause(self, state_name: str, tier: str = "legendary") -> float:
        """Pauses automation and raises UI alert when a target rare Moga is encountered."""
        tier_label = tier.replace("_", " ").title()
        with self._lock:
            self._status = "paused"
            self._current_fsm_state = f"{tier.upper()}_FOUND"
            self._legendary_alert = True
            self._detected_tier = tier
            self._legendaries_found += 1
            self._legendary_detected_at = time.time()
            self._zero_alive_streak = 0
            self._consecutive_active_clicks = 0
        self._log(
            f"[RARE FOUND] {tier_label} Moga detected on {state_name}! Pausing bot for manual capture."
        )
        return 0.5

    def _run_fsm_tick(self) -> float:
        full_frame = self._grab_page_bgr()
        if full_frame is None:
            return 0.5

        offset = self._ensure_canvas_lock(full_frame)
        if offset is None:
            self._store_preview_frame(full_frame)
            with self._lock:
                self._current_fsm_state = "WAITING_CANVAS"
            return 0.8

        ox, oy, cw, ch = offset
        screen = full_frame[oy:oy + ch, ox:ox + cw]
        self._store_preview_frame(screen)

        with self._lock:
            settings = dict(self._settings)

        threshold = float(settings["confidence_threshold"])
        dry_run = bool(settings["dry_run"])
        find_legendary = bool(settings.get("find_legendary", False))
        target_tiers = set(settings.get("target_tiers") or [])
        run_from_non_legendary = find_legendary and bool(settings.get("run_from_non_legendary", False))
        max_primary_turns = int(settings["max_primary_turns"])
        primary_skill = settings["primary_skill"]
        squad_priority = list(settings["squad_priority"])
        farm_node_xy = settings_store.get_active_farm_node(settings)
        farm_node_name = settings.get("active_node_name", "Farm Node")

        # PRIORITY 0a: Quest Complete Modal -> Click green checkmark button
        if hit := vision.find_template(screen, self._templates["quest_ok"], threshold, config.TEMPLATE_REF_SIZE):
            with self._lock:
                self._reset_battle_tracking()
                self._current_fsm_state = "QUEST_COMPLETE"
                self._last_state_time = time.time()
            self._log(f"[STATE: QUEST_COMPLETE] (conf={hit[2]:.2f})")
            self._click_canvas(offset, hit[0], hit[1], "Quest Complete OK", dry_run)
            return 1.0

        # PRIORITY 0b: Accidental Capture Modal Failsafe -> Click top-right X to resume battle
        if hit := vision.find_template(screen, self._templates["capture_modal"], threshold, config.TEMPLATE_REF_SIZE):
            with self._lock:
                self._zero_alive_streak = 0
                self._consecutive_active_clicks = 0
                self._current_fsm_state = "CAPTURE_POPUP"
                self._last_state_time = time.time()
            self._log(f"[STATE: CAPTURE_POPUP] Closing capture modal (conf={hit[2]:.2f})")
            self._click_canvas_norm(offset, config.COORDS["capture_close"], "Close Capture Modal", dry_run)
            return 0.8

        # PRIORITY 1: Victory Screen -> Click Continue
        if hit := vision.find_template(screen, self._templates["victory"], threshold, config.TEMPLATE_REF_SIZE):
            with self._lock:
                self._victories += 1
                self._reset_battle_tracking()
                self._current_fsm_state = "BATTLE_VICTORY"
                self._last_state_time = time.time()
                victories = self._victories
                elapsed_min = max(0.01, (time.time() - (self._start_time or time.time())) / 60.0)
            self._log(
                f"[STATE: BATTLE_VICTORY] Run #{victories} complete "
                f"({victories / elapsed_min:.1f} runs/min, conf={hit[2]:.2f})"
            )
            self._click_canvas(offset, hit[0], hit[1], "Continue", dry_run)
            return 1.2

        # PRIORITY 2: Choose Moga -> Select First Alive Squad Member in User Priority Order
        if hit := vision.find_template(
            screen,
            self._templates["select_moga"],
            threshold,
            config.TEMPLATE_REF_SIZE,
            mask=self._select_moga_mask,
        ):
            if find_legendary and target_tiers:
                tier = vision.detect_enemy_rarity(screen)
                if tier in target_tiers:
                    return self._trigger_legendary_pause("BATTLE_SELECT_MOGA", tier)
            with self._lock:
                self._current_fsm_state = "BATTLE_SELECT_MOGA"
                self._last_state_time = time.time()
                self._consecutive_active_clicks = 0
                preferred_order = [s for s in squad_priority if s not in self._exhausted_slots] or squad_priority
            self._log(f"[STATE: BATTLE_SELECT_MOGA] (conf={hit[2]:.2f}, priority={preferred_order})")
            alive_slot = vision.find_first_alive_moga_slot(
                screen, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, preferred_order
            )
            if alive_slot is None:
                with self._lock:
                    self._zero_alive_streak += 1
                    streak = self._zero_alive_streak
                if streak < 3:
                    self._log(f"[WARN] No alive Moga detected (attempt {streak}/3) — retrying...")
                    return 0.6
                with self._lock:
                    self._status = "halted"
                self._log("[HALT] No enabled alive Mogas found in priority list. Stopping safely.")
                return 0.5
            slot_num, sx, sy = alive_slot
            with self._lock:
                self._zero_alive_streak = 0
                self._active_slot = slot_num
                self._exhausted_skills.clear()
                self._turns_in_battle = 0
                self._last_skill_clicked = None
            self._click_canvas(offset, sx, sy, f"Select Alive Moga (Slot {slot_num})", dry_run)
            return 0.9

        # PRIORITY 3: Active Combat -> Rare Tier Check / Hunt RUN / Upper Skills -> Switch Moga
        if hit := vision.find_template(screen, self._templates["combat_active"], threshold, config.TEMPLATE_REF_SIZE):
            if find_legendary and target_tiers:
                tier = vision.detect_enemy_rarity(screen)
                if tier in target_tiers:
                    return self._trigger_legendary_pause("BATTLE_ACTIVE", tier)
            if run_from_non_legendary:
                with self._lock:
                    self._zero_alive_streak = 0
                    self._consecutive_active_clicks = 0
                    self._current_fsm_state = "BATTLE_ACTIVE"
                    self._last_state_time = time.time()
                self._log("[HUNT] Non-target Moga -> clicking RUN.")
                self._click_canvas_norm(offset, config.COORDS["battle_run"], "Run (Hunt Mode)", dry_run)
                return 1.2
            secondary_skill = "skill_top_right" if primary_skill == "skill_top_left" else "skill_top_left"
            with self._lock:
                self._zero_alive_streak = 0
                self._current_fsm_state = "BATTLE_ACTIVE"
                self._last_state_time = time.time()
                if self._active_slot is None and squad_priority:
                    self._active_slot = squad_priority[0]
                if self._consecutive_active_clicks >= 3 and self._last_skill_clicked is not None:
                    self._exhausted_skills.add(self._last_skill_clicked)
                    self._consecutive_active_clicks = 0
                    self._log(
                        f"[SKILL] {self._last_skill_clicked} unresponsive after 3 clicks -> marking out of PP."
                    )
                turns = self._turns_in_battle
                exhausted_skills = set(self._exhausted_skills)
                active_slot = self._active_slot

            primary_ok = (
                primary_skill not in exhausted_skills
                and turns < max_primary_turns
                and vision.is_skill_available(screen, config.COORDS[primary_skill])
            )
            secondary_ok = (
                secondary_skill not in exhausted_skills
                and vision.is_skill_available(screen, config.COORDS[secondary_skill])
            )

            if primary_ok:
                chosen_skill = primary_skill
            elif secondary_ok:
                chosen_skill = secondary_skill
                if primary_skill not in exhausted_skills:
                    with self._lock:
                        self._exhausted_skills.add(primary_skill)
                    self._log(f"[SKILL] {primary_skill} out/dimmed -> switching to {secondary_skill}")
            else:
                # Both upper skills exhausted -> switch to next alive priority team member
                with self._lock:
                    if active_slot is not None:
                        self._exhausted_slots.add(active_slot)
                    remaining = [s for s in squad_priority if s not in self._exhausted_slots]
                next_alive = vision.find_first_alive_moga_slot(
                    screen, config.COORDS["squad_slots"], config.HP_BAR_Y_OFFSET, remaining
                )
                if next_alive is None:
                    with self._lock:
                        self._status = "halted"
                    self._log(
                        "[HALT] All enabled alive Mogas are out of usable upper skills (PP). Stopping safely."
                    )
                    return 0.5
                next_slot, sx, sy = next_alive
                self._log(
                    f"[SWITCH] Both upper skills exhausted on Slot {active_slot} -> switching to Slot {next_slot}"
                )
                self._click_canvas(offset, sx, sy, f"Switch to Alive Moga (Slot {next_slot})", dry_run)
                with self._lock:
                    self._active_slot = next_slot
                    self._exhausted_skills.clear()
                    self._turns_in_battle = 0
                    self._consecutive_active_clicks = 0
                    self._last_skill_clicked = None
                return 1.2

            with self._lock:
                self._turns_in_battle += 1
                self._consecutive_active_clicks += 1
                self._last_skill_clicked = chosen_skill
                turn_num = self._turns_in_battle
            self._click_canvas_norm(
                offset, config.COORDS[chosen_skill], f"Attack Turn {turn_num} ({chosen_skill})", dry_run
            )
            return 0.7

        # PRIORITY 4: Battle Ready -> Rare Tier Check or Click Red GO!
        if hit := vision.find_template(screen, self._templates["combat_ready"], threshold, config.TEMPLATE_REF_SIZE):
            if find_legendary and target_tiers:
                tier = vision.detect_enemy_rarity(screen)
                if tier in target_tiers:
                    return self._trigger_legendary_pause("BATTLE_READY", tier)
            with self._lock:
                self._turns_in_battle = 0
                self._consecutive_active_clicks = 0
                self._current_fsm_state = "BATTLE_READY"
                self._last_state_time = time.time()
            self._log(f"[STATE: BATTLE_READY] (conf={hit[2]:.2f})")
            self._click_canvas(offset, hit[0], hit[1], "Battle Red GO!", dry_run)
            return 0.9

        # PRIORITY 5: Stage Info Modal -> Click Light-Blue GO!
        if hit := vision.find_template(screen, self._templates["node_popup"], threshold, config.TEMPLATE_REF_SIZE):
            with self._lock:
                self._consecutive_active_clicks = 0
                self._current_fsm_state = "NODE_POPUP"
                self._last_state_time = time.time()
            self._log(f"[STATE: NODE_POPUP] (conf={hit[2]:.2f})")
            self._click_canvas(offset, hit[0], hit[1], "Stage Popup GO!", dry_run)
            return 1.2

        # PRIORITY 6: Overworld Map -> Click Active Farm Node
        if hit := vision.find_template(screen, self._templates["map_idle"], threshold, config.TEMPLATE_REF_SIZE):
            with self._lock:
                self._reset_battle_tracking()
                self._current_fsm_state = "MAP_IDLE"
                self._last_state_time = time.time()
            self._log(f"[STATE: MAP_IDLE] (conf={hit[2]:.2f})")
            self._click_canvas_norm(offset, farm_node_xy, f"Select Node: {farm_node_name}", dry_run)
            return 1.5

        # TRANSITION / ANIMATION POLLING
        with self._lock:
            self._consecutive_active_clicks = 0
            self._current_fsm_state = "TRANSITION"
            if time.time() - self._last_state_time > config.WATCHDOG_TIMEOUT:
                self._last_state_time = time.time()
                warn = True
            else:
                warn = False
        if warn:
            self._log(
                f"[WATCHDOG] No known screen matched for {config.WATCHDOG_TIMEOUT}s. "
                "Check for popups or depleted energy."
            )
        return 0.25
