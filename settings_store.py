"""Thread-safe JSON settings persistence for the WebApp farming bot."""

import copy
import json
import os
import threading

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(PROJECT_ROOT, "settings.json")

VALID_SKILLS = ("skill_top_left", "skill_top_right")
VALID_TARGET_TIERS = ("super_rare", "epic", "legendary")

DEFAULT_SETTINGS = {
    "game_url": "https://monstergalaxy.online",
    "dry_run": False,
    "find_legendary": False,
    "target_tiers": ["legendary"],
    "run_from_non_legendary": False,
    "confidence_threshold": 0.85,
    "max_primary_turns": 15,
    "primary_skill": "skill_top_left",
    "squad_priority": [1, 2, 3, 4, 5, 6],
    "active_node_name": "Mega Hole (Beach)",
    "farm_nodes": {
        "Mega Hole (Beach)": [0.105, 0.672],
    },
}

_LOCK = threading.Lock()


def _sanitize_settings(raw: dict) -> dict:
    """Validates and normalizes user settings against schema defaults."""
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        for key in merged:
            if key in raw:
                merged[key] = raw[key]

    url = str(merged.get("game_url") or DEFAULT_SETTINGS["game_url"]).strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        url = DEFAULT_SETTINGS["game_url"]
    merged["game_url"] = url

    merged["dry_run"] = bool(merged.get("dry_run", False))
    merged["find_legendary"] = bool(merged.get("find_legendary", False))
    raw_tiers = merged.get("target_tiers", ["legendary"])
    seen_tiers = set()
    clean_tiers = []
    if isinstance(raw_tiers, list):
        for t in raw_tiers:
            ts = str(t).strip().lower()
            if ts in VALID_TARGET_TIERS and ts not in seen_tiers:
                seen_tiers.add(ts)
                clean_tiers.append(ts)
    merged["target_tiers"] = clean_tiers
    merged["run_from_non_legendary"] = (
        bool(merged.get("run_from_non_legendary", False)) if merged["find_legendary"] else False
    )

    try:
        conf = float(merged.get("confidence_threshold", 0.85))
    except (TypeError, ValueError):
        conf = 0.85
    merged["confidence_threshold"] = round(min(0.99, max(0.50, conf)), 2)

    try:
        turns = int(merged.get("max_primary_turns", 15))
    except (TypeError, ValueError):
        turns = 15
    merged["max_primary_turns"] = min(99, max(1, turns))

    skill = str(merged.get("primary_skill", "skill_top_left"))
    merged["primary_skill"] = skill if skill in VALID_SKILLS else "skill_top_left"

    raw_prio = merged.get("squad_priority", [1, 2, 3, 4, 5, 6])
    seen = set()
    clean_prio = []
    if isinstance(raw_prio, list):
        for item in raw_prio:
            try:
                slot_num = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= slot_num <= 6 and slot_num not in seen:
                seen.add(slot_num)
                clean_prio.append(slot_num)
    merged["squad_priority"] = clean_prio

    raw_nodes = merged.get("farm_nodes")
    clean_nodes = {}
    if isinstance(raw_nodes, dict):
        for name, coords in raw_nodes.items():
            clean_name = str(name).strip()
            if not clean_name or not isinstance(coords, (list, tuple)) or len(coords) != 2:
                continue
            try:
                nx = round(min(1.0, max(0.0, float(coords[0]))), 4)
                ny = round(min(1.0, max(0.0, float(coords[1]))), 4)
                clean_nodes[clean_name] = [nx, ny]
            except (TypeError, ValueError):
                continue

    if not clean_nodes:
        clean_nodes = copy.deepcopy(DEFAULT_SETTINGS["farm_nodes"])
    merged["farm_nodes"] = clean_nodes

    active_name = str(merged.get("active_node_name") or "").strip()
    if active_name not in clean_nodes:
        active_name = next(iter(clean_nodes))
    merged["active_node_name"] = active_name

    return merged


def load_settings(path: str = SETTINGS_PATH) -> dict:
    """Loads settings from disk, creating defaults if absent or malformed."""
    with _LOCK:
        if not os.path.exists(path):
            data = copy.deepcopy(DEFAULT_SETTINGS)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return data
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
        data = _sanitize_settings(raw)
        return data


def save_settings(updates: dict, path: str = SETTINGS_PATH) -> dict:
    """Merges updates into persisted settings, validates, writes to disk, and returns them."""
    with _LOCK:
        current = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    current = json.load(f)
            except Exception:
                current = {}
        if not isinstance(current, dict):
            current = {}

        combined = {**DEFAULT_SETTINGS, **current, **(updates or {})}
        sanitized = _sanitize_settings(combined)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sanitized, f, indent=2)
        return sanitized


def get_active_farm_node(settings: dict) -> tuple[float, float]:
    """Returns normalized (x, y) coordinates for the active farm node."""
    nodes = settings.get("farm_nodes") or DEFAULT_SETTINGS["farm_nodes"]
    name = settings.get("active_node_name")
    coords = nodes.get(name) or next(iter(nodes.values()))
    return (float(coords[0]), float(coords[1]))
