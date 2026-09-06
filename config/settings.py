import pygame

pygame.init()

# ── Screen ────────────────────────────────────────────────────────────────────
SCREEN_WIDTH  = 1920
SCREEN_HEIGHT = 1080
FPS           = 60

# ── World / Tiles ─────────────────────────────────────────────────────────────
# Individual rooms can override WORLD_WIDTH/HEIGHT at load time.
TILE_SIZE    = 16
WORLD_WIDTH  = 1808
WORLD_HEIGHT = 1792

# Tiles are drawn at TILE_SIZE * RENDER_SCALE pixels on screen (48x48 at 3x).
RENDER_SCALE = 6

# The Mode7 world-map flying scene (game.py's _draw_world_map_flying_scene)
# hard-codes all of its sprite/HUD/shadow/icon sizes as multiples of a scale
# factor, tuned by eye back when RENDER_SCALE was 4. That scene's assets and
# screen resolution don't change with RENDER_SCALE, so it needs its own fixed
# constant instead of reusing RENDER_SCALE -- otherwise every future
# RENDER_SCALE tweak (made for the tile-based overworld) silently re-scales
# and misaligns the whole Mode7 scene. Keep this at 4 unless you deliberately
# want to re-tune every Mode7 size (sprite scale, shadow, HUD, minimap dot,
# location icons, blend overlay height) for a new baseline.
MODE7_SCALE = 4

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