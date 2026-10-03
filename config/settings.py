import json
import pygame

pygame.init()

# ── Display ───────────────────────────────────────────────────────────────────
# The game renders at a real, chosen resolution (no stretching of a fixed
# 1080p frame). Everything else that has a pixel size is derived from the
# resolution height relative to BASE_HEIGHT, the height the art/UI was tuned at.
#
# Player-facing settings live in display_settings.json (created on first run
# or first F11 press):
#     {"mode": "borderless", "resolution": "native"}
#   mode        "borderless" (default) | "fullscreen" | "windowed"
#   resolution  "native" | "2560x1440" | [2560, 1440]
#               "native" = the desktop resolution (windowed: the largest
#               standard 16:9 size that fits comfortably on the desktop).
# The resolution is read once at startup, so changing it needs a restart.
# Changing the window mode (F11 / Alt+Enter) works live.
DISPLAY_SETTINGS_FILE = "display_settings.json"
BASE_HEIGHT           = 1080
FPS                   = 60

_STANDARD_16_9 = [(3840, 2160), (2560, 1440), (1920, 1080), (1600, 900), (1280, 720)]
_MIN_RES       = (960, 540)


def get_desktop_size() -> tuple:
    """Current desktop resolution of the primary display."""
    try:
        w, h = pygame.display.get_desktop_sizes()[0]
    except Exception:
        info = pygame.display.Info()
        w, h = info.current_w, info.current_h
    return (int(w), int(h))


def load_display_settings() -> dict:
    cfg = {"mode": "borderless", "resolution": "native"}
    try:
        with open(DISPLAY_SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if loaded.get("mode") in ("borderless", "fullscreen", "windowed"):
            cfg["mode"] = loaded["mode"]
        if "resolution" in loaded:
            cfg["resolution"] = loaded["resolution"]
    except (OSError, ValueError):
        pass
    return cfg


def save_display_settings(cfg: dict) -> None:
    try:
        with open(DISPLAY_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def _resolve_resolution(cfg: dict, desktop: tuple) -> tuple:
    res = cfg.get("resolution", "native")
    w = h = None
    if isinstance(res, str) and "x" in res.lower():
        try:
            w, h = (int(v) for v in res.lower().split("x"))
        except ValueError:
            w = h = None
    elif isinstance(res, (list, tuple)) and len(res) == 2:
        try:
            w, h = int(res[0]), int(res[1])
        except (TypeError, ValueError):
            w = h = None
    if w is not None and w >= _MIN_RES[0] and h >= _MIN_RES[1]:
        return (w, h)
    if cfg.get("mode") == "windowed":
        for rw, rh in _STANDARD_16_9:
            if rw <= desktop[0] * 0.95 and rh <= desktop[1] * 0.90:
                return (rw, rh)
        return _MIN_RES
    return desktop


def get_resolution_choices() -> list:
    """Resolutions the dev menu cycles through: 'native' plus every standard
    16:9 size that fits on this desktop."""
    dw, dh = get_desktop_size()
    return ["native"] + [f"{w}x{h}" for w, h in _STANDARD_16_9 if w <= dw and h <= dh]


def display_resolution_key(cfg: dict) -> str:
    """The saved resolution setting as one of get_resolution_choices()'s
    strings ('native' or 'WxH')."""
    res = cfg.get("resolution", "native")
    if isinstance(res, (list, tuple)) and len(res) == 2:
        return f"{res[0]}x{res[1]}"
    if isinstance(res, str) and res.lower() != "native":
        return res.lower()
    return "native"


def effective_resolution(cfg: dict) -> tuple:
    """The (w, h) a launch with this saved config would actually use."""
    return _resolve_resolution(cfg, get_desktop_size())


DESKTOP_WIDTH, DESKTOP_HEIGHT = get_desktop_size()
SCREEN_WIDTH, SCREEN_HEIGHT = _resolve_resolution(
    load_display_settings(), (DESKTOP_WIDTH, DESKTOP_HEIGHT))

# 1.0 at 1080p, 1.333 at 1440p, 2.0 at 4K. Use it for any hardcoded pixel size.
UI_SCALE = SCREEN_HEIGHT / BASE_HEIGHT

# Smallest text height (px) the bitmap UI font stays legible at. Editor text
# sizes are authored for 1080p; at 720p they would otherwise shrink below this.
UI_TEXT_MIN = 8


def ui(n) -> int:
    """Scale a pixel size authored for 1080p to the current resolution.

    Use for panel/button/padding/offset sizes in dev-tool chrome. Identity at
    1080p. Non-zero inputs never collapse to 0, so 1-2px hairlines survive
    low resolutions."""
    v = int(round(n * UI_SCALE))
    if v == 0 and n:
        return 1 if n > 0 else -1
    return v


def ui_text(n) -> int:
    """Like ui() but for bitmap-font heights: never smaller than UI_TEXT_MIN."""
    return max(UI_TEXT_MIN, ui(n))

# ── World / Tiles ─────────────────────────────────────────────────────────────
# Individual rooms can override WORLD_WIDTH/HEIGHT at load time.
TILE_SIZE    = 16
WORLD_WIDTH  = 1808
WORLD_HEIGHT = 1792

# Tiles are drawn at TILE_SIZE * RENDER_SCALE pixels on screen. It is an
# INTEGER so pixel art stays crisp, and tracks the resolution height so the
# same amount of world is visible: 6 at 1080p, 8 at 1440p, 12 at 4K.
# Wider-than-16:9 screens simply show more world horizontally.
RENDER_SCALE = max(1, round(6 * SCREEN_HEIGHT / BASE_HEIGHT))

# The Mode7 world-map flying scene hard-codes its sprite/HUD/shadow/icon sizes
# as multiples of this factor (tuned by eye at 4 on a 1080p screen). It scales
# with resolution height the same way, rounded to an integer.
MODE7_SCALE = max(1, round(4 * SCREEN_HEIGHT / BASE_HEIGHT))

# ── Palette ───────────────────────────────────────────────────────────────────
WHITE      = (255, 255, 255)
BLACK      = (0,   0,   0  )
GREEN      = (0,   255, 0  )
RED        = (255, 0,   0  )
BLUE       = (0,   100, 255)
GRAY       = (50,  50,  50 )
LIGHT_GRAY = (200, 200, 200)
YELLOW     = (255, 255, 0  )
DARK_GRAY  = (30,  30,  30 )
CYAN       = (0,   255, 255)
ORANGE     = (255, 165, 0  )
PURPLE     = (138, 43,  226)

def get_colors() -> dict:
    """Return the palette as a dict so subsystems can look up colors by name."""
    return {
        'WHITE':      WHITE,
        'BLACK':      BLACK,
        'GREEN':      GREEN,
        'RED':        RED,
        'BLUE':       BLUE,
        'GRAY':       GRAY,
        'LIGHT_GRAY': LIGHT_GRAY,
        'YELLOW':     YELLOW,
        'DARK_GRAY':  DARK_GRAY,
        'CYAN':       CYAN,
        'ORANGE':     ORANGE,
        'PURPLE':     PURPLE,
    }