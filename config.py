"""Configuration constants and normalized coordinates for the farming bot."""

# Substring of the target game or browser window title (case-insensitive match)
WINDOW_TITLE = "Monster Galaxy"

# Execution flags
DRY_RUN = False               # If True, logs detected states and target coordinates without clicking
CONFIDENCE_THRESHOLD = 0.85   # Minimum OpenCV template match score (0.0 - 1.0)
CANVAS_DETECT_THRESHOLD = 0.80 # Minimum score for multi-scale game canvas detection in browser windows
MAX_PRIMARY_TURNS = 15        # Turn threshold before rotating to fallback skills (PP exhaustion guard)
WATCHDOG_TIMEOUT = 25.0       # Seconds without a state match before logging a stall warning
CLICK_JITTER_PX = 3           # Random +/- pixel offset to avoid identical robotic click coordinates

# Reference (width, height) of the game canvas when templates were captured
TEMPLATE_REF_SIZE = (1024, 890)

# Top-left (x, y) origin of each template inside TEMPLATE_REF_SIZE
TEMPLATE_REF_ORIGINS = {
    "quest_ok": (464, 718),
    "capture_modal": (566, 374),
    "map_idle": (150, 678),
    "node_popup": (448, 676),
    "select_moga": (286, 626),
    "combat_ready": (454, 768),
    "combat_active": (454, 772),
    "victory": (478, 323),
}

# Template file paths
TEMPLATE_PATHS = {
    "quest_ok": "templates/quest_ok.png",
    "capture_modal": "templates/capture_modal.png",
    "victory": "templates/battle_continue.png",
    "select_moga": "templates/choose_moga.png",
    "combat_active": "templates/battle_capture.png",
    "combat_ready": "templates/battle_red_go.png",
    "node_popup": "templates/popup_go.png",
    "map_idle": "templates/map_discord.png",
}

# Normalized (x, y) coordinates relative to the game window (0.0 to 1.0)
COORDS = {
    "farm_node": (0.105, 0.672),
    "capture_close": (0.8125, 0.2607),
    "skill_top_left": (0.315, 0.895),
    "skill_top_right": (0.685, 0.895),
    "squad_slots": [
        (0.242, 0.815),
        (0.302, 0.815),
        (0.362, 0.815),
        (0.422, 0.815),
        (0.482, 0.815),
        (0.542, 0.815),
    ],
}

# Vertical offset from squad slot center to its HP bar strip
HP_BAR_Y_OFFSET = 0.028
