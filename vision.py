"""Screen capture, Windows DPI awareness, template matching, and pixel inspection."""

import ctypes
import os
import sys
import cv2
import numpy as np
import mss
import pygetwindow as gw

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_SCALED_TEMPLATE_CACHE = {}
_MAX_TEMPLATE_CACHE = 256


def enable_dpi_awareness() -> None:
    """Prevents coordinate scaling mismatches on Windows high-DPI displays."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def get_game_window_bbox(title_substr: str) -> dict:
    """Finds the first visible window matching title_substr and returns its mss bbox."""
    all_windows = gw.getAllWindows()
    matches = [
        w for w in all_windows
        if title_substr.lower() in w.title.lower() and w.width > 100 and w.height > 100
    ]
    if not matches:
        available = [w.title for w in all_windows if w.title.strip()]
        raise RuntimeError(
            f"No visible window found containing '{title_substr}'.\n"
            f"Available windows: {available}"
        )
    win = matches[0]
    return {
        "left": int(win.left),
        "top": int(win.top),
        "width": int(win.width),
        "height": int(win.height),
        "title": win.title,
    }


def load_templates(template_paths: dict) -> dict:
    """Loads all reference PNG templates from disk and validates existence."""
    loaded = {}
    missing = []
    for key, path in template_paths.items():
        resolved = path
        if not os.path.exists(resolved) and not os.path.isabs(path):
            candidate = os.path.join(_PROJECT_ROOT, path)
            if os.path.exists(candidate):
                resolved = candidate

        if not os.path.exists(resolved):
            missing.append(path)
            continue
        img = cv2.imread(resolved, cv2.IMREAD_COLOR)
        if img is None:
            missing.append(path)
        else:
            loaded[key] = img

    if missing:
        raise FileNotFoundError(
            f"Missing or unreadable template files: {missing}\n"
            "Run 'python tools/setup_wizard.py' to capture them first."
        )
    return loaded


def grab_frame(sct: mss.mss, bbox: dict) -> np.ndarray:
    """Captures the game window region as an OpenCV BGR numpy array."""
    capture_region = {
        "left": bbox["left"],
        "top": bbox["top"],
        "width": bbox["width"],
        "height": bbox["height"],
    }
    raw = np.array(sct.grab(capture_region))
    return cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)


def build_text_outline_mask(template_bgr: np.ndarray) -> np.ndarray:
    """
    Builds a foreground mask covering bright white text and its dark outline,
    ignoring variable arena background pixels behind the label.
    """
    gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY) if template_bgr.ndim == 3 else template_bgr
    white = (gray > 200).astype(np.uint8) * 255
    black = (gray < 40).astype(np.uint8) * 255
    near_white = cv2.dilate(white, np.ones((5, 5), np.uint8), iterations=1)
    return cv2.bitwise_or(white, cv2.bitwise_and(black, near_white))


def _resize_template_cached(template: np.ndarray, scale: float, is_mask: bool = False) -> np.ndarray:
    """Resizes template or mask by an explicit scale factor with bounded caching."""
    if abs(scale - 1.0) <= 0.01:
        return template

    tmpl_h, tmpl_w = template.shape[:2]
    new_w = max(1, int(round(tmpl_w * scale)))
    new_h = max(1, int(round(tmpl_h * scale)))

    cache_key = (id(template), new_w, new_h, is_mask)
    cached = _SCALED_TEMPLATE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if is_mask:
        interp = cv2.INTER_NEAREST
    else:
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(template, (new_w, new_h), interpolation=interp)
    if len(_SCALED_TEMPLATE_CACHE) >= _MAX_TEMPLATE_CACHE:
        _SCALED_TEMPLATE_CACHE.clear()
    _SCALED_TEMPLATE_CACHE[cache_key] = resized
    return resized


def _get_scaled_template(template: np.ndarray, screen_shape: tuple, ref_size: tuple) -> np.ndarray:
    """Uniformly scales template to match a static-aspect-ratio window size."""
    screen_h, screen_w = screen_shape[:2]
    ref_w, ref_h = ref_size
    scale = min(screen_w / ref_w, screen_h / ref_h)
    return _resize_template_cached(template, scale)


def _match_single(screen: np.ndarray, template: np.ndarray, threshold: float, mask: np.ndarray | None = None):
    """Runs single-scale cv2.matchTemplate and returns (cx, cy, score) or None."""
    if screen.shape[0] < template.shape[0] or screen.shape[1] < template.shape[1]:
        return None
    if mask is not None:
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED, mask=mask)
        if not np.isfinite(result).all():
            result = np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0)
    else:
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val >= threshold:
        h, w = template.shape[:2]
        cx = max_loc[0] + (w // 2)
        cy = max_loc[1] + (h // 2)
        return (cx, cy, float(max_val))
    return None


def find_template(
    screen: np.ndarray,
    template: np.ndarray,
    threshold: float,
    ref_size: tuple | None = None,
    mask: np.ndarray | None = None,
):
    """Returns (center_x, center_y, confidence) relative to window if matched, else None."""
    if mask is not None:
        screen_in = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if screen.ndim == 3 else screen
        tmpl_in = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY) if template.ndim == 3 else template
    else:
        screen_in = screen
        tmpl_in = template

    if ref_size is None:
        return _match_single(screen_in, tmpl_in, threshold, mask=mask)

    screen_h, screen_w = screen_in.shape[:2]
    ref_w, ref_h = ref_size
    base_scale = min(screen_w / ref_w, screen_h / ref_h)

    for mult in (1.0, 0.985, 1.015, 0.97, 1.03, 0.955, 1.045):
        scale = base_scale * mult
        scaled_tmpl = _resize_template_cached(tmpl_in, scale, is_mask=False)
        scaled_mask = _resize_template_cached(mask, scale, is_mask=True) if mask is not None else None
        hit = _match_single(screen_in, scaled_tmpl, threshold, mask=scaled_mask)
        if hit is not None:
            return hit
    return None


def detect_game_canvas(
    window_frame: np.ndarray,
    window_bbox: dict,
    templates: dict,
    ref_size: tuple,
    ref_origins: dict,
    threshold: float = 0.80,
) -> dict | None:
    """
    Locates the static-aspect-ratio game canvas inside a larger browser window
    using coarse-to-fine multi-scale template matching.
    """
    win_h, win_w = window_frame.shape[:2]
    ref_w, ref_h = ref_size

    best_score = -1.0
    best_key = None
    best_scale = 1.0
    best_loc = (0, 0)

    # Coarse pass across scales 0.40 -> 1.80
    for step in range(8, 37):
        scale = step * 0.05
        for key, tmpl in templates.items():
            if key not in ref_origins:
                continue
            tw = max(1, int(round(tmpl.shape[1] * scale)))
            th = max(1, int(round(tmpl.shape[0] * scale)))
            if tw >= win_w or th >= win_h:
                continue
            interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            resized = cv2.resize(tmpl, (tw, th), interpolation=interp)
            res = cv2.matchTemplate(window_frame, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val > best_score:
                best_score = float(max_val)
                best_key = key
                best_scale = scale
                best_loc = max_loc

    if best_key is None:
        return None

    # Fine pass around best coarse scale (+/- 0.05 in 0.005 increments)
    tmpl = templates[best_key]
    coarse_scale = best_scale
    for delta_step in range(-10, 11):
        scale = coarse_scale + delta_step * 0.005
        if scale <= 0.1:
            continue
        tw = max(1, int(round(tmpl.shape[1] * scale)))
        th = max(1, int(round(tmpl.shape[0] * scale)))
        if tw >= win_w or th >= win_h:
            continue
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(tmpl, (tw, th), interpolation=interp)
        res = cv2.matchTemplate(window_frame, resized, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val > best_score:
            best_score = float(max_val)
            best_scale = scale
            best_loc = max_loc

    if best_score < threshold:
        return None

    orig_x, orig_y = ref_origins[best_key]
    canvas_x = max(0, int(round(best_loc[0] - orig_x * best_scale)))
    canvas_y = max(0, int(round(best_loc[1] - orig_y * best_scale)))
    canvas_w = min(win_w - canvas_x, int(round(ref_w * best_scale)))
    canvas_h = min(win_h - canvas_y, int(round(ref_h * best_scale)))

    return {
        "left": window_bbox["left"] + canvas_x,
        "top": window_bbox["top"] + canvas_y,
        "width": canvas_w,
        "height": canvas_h,
        "offset_x": canvas_x,
        "offset_y": canvas_y,
        "scale": float(best_scale),
        "anchor": best_key,
        "confidence": float(best_score),
        "title": window_bbox.get("title", ""),
    }


def find_first_alive_moga_slot(
    screen: np.ndarray,
    squad_slots: list,
    hp_y_offset: float,
    slot_priority: list[int] | None = None,
):
    """
    Inspects the HP bar band underneath each squad portrait (Slots 1-6) in slot_priority order.
    Scans a small vertical window so minor browser-canvas offset shifts never miss the HP bar.
    Returns (slot_index_1_based, pixel_x, pixel_y) for the first alive Moga, or None.
    """
    h, w = screen.shape[:2]
    y_pad_up = max(4, int(h * 0.018))
    y_pad_down = max(4, int(h * 0.022))
    x_left = max(4, int(w * 0.018))
    x_right = max(2, int(w * 0.008))

    if slot_priority is None:
        order = list(range(1, len(squad_slots) + 1))
    else:
        order = [idx for idx in slot_priority if 1 <= int(idx) <= len(squad_slots)]

    for idx in order:
        rel_x, rel_y = squad_slots[idx - 1]
        px = int(w * rel_x)
        py = int(h * rel_y)
        hp_py = int(h * (rel_y + hp_y_offset))

        y0 = max(0, hp_py - y_pad_up)
        y1 = min(h, hp_py + y_pad_down + 1)
        x0 = max(0, px - x_left)
        x1 = min(w, px + x_right + 1)
        if x1 <= x0 or y1 <= y0:
            continue

        band = screen[y0:y1, x0:x1].astype(np.int16)
        b = band[:, :, 0]
        g = band[:, :, 1]
        r = band[:, :, 2]

        # Separate green/lime HP bar vs yellow/orange low-HP bar so portrait fur does not false-positive
        green_mask = (g > 125) & ((g - b) > 25)
        orange_mask = (r > 155) & (g > 70) & ((r - b) > 55)
        for row_idx in range(band.shape[0]):
            g_row = green_mask[row_idx]
            if float(np.mean(g_row)) >= 0.65 and float(np.std(g[row_idx][g_row])) < 40.0:
                return (idx, px, py)

            o_row = orange_mask[row_idx]
            if float(np.mean(o_row)) >= 0.80 and float(np.std(g[row_idx][o_row])) < 18.0:
                return (idx, px, py)

    return None


def is_skill_available(screen: np.ndarray, skill_norm_xy: tuple[float, float]) -> bool:
    """
    Checks whether an upper skill button is illuminated/usable rather than dimmed (0 PP).
    """
    h, w = screen.shape[:2]
    px = int(w * skill_norm_xy[0])
    py = int(h * skill_norm_xy[1])
    dx = max(6, int(w * 0.045))
    dy = max(4, int(h * 0.014))

    x0, x1 = max(0, px - dx), min(w, px + dx + 1)
    y0, y1 = max(0, py - dy), min(h, py + dy + 1)
    if x1 <= x0 or y1 <= y0:
        return False

    blue = screen[y0:y1, x0:x1, 0]
    mean_b = float(np.mean(blue))
    p90_b = float(np.percentile(blue, 90))
    return bool(mean_b >= 95.0 and p90_b >= 140.0)
