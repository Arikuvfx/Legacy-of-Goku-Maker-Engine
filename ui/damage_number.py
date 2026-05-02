"""
ui/damage_number.py

Floating damage number popups — inspired by Legacy of Goku / Buu's Fury.

When the player hits an enemy (or an enemy hits the player), a number pops
above the entity and drifts upward for a short time before fading out.

────────────────────────────────────────────────────────────────────────────
How it works
────────────
  1. Somewhere a hit lands → call DamageNumberManager.spawn(world_x, world_y, amount, variant)
  2. Call manager.update(dt) every frame (before drawing).
  3. Call manager.draw(screen, camera) after layer_manager.draw_all() but
     before the HUD so numbers sit on top of sprites but below the UI.

Variants
────────
  'enemy'  → red — player dealt damage to an enemy
  'player' → red — enemy dealt damage to the player

Font loading
────────────
Looks for individual digit PNGs in  assets/ui/fonts/dmg_font/
  0.png … 9.png

Falls back to a pygame system font if any image is missing, so the feature
never hard-crashes — you will just see plain text until the real assets land.
"""

from __future__ import annotations

import os
import random
import pygame
from typing import Optional


# ── Font cache ─────────────────────────────────────────────────────────────────

# Digits loaded once, shared by every DamageNumber instance this session.
# dict[variant_key] → list of 10 pygame.Surface (index == digit value)
_digit_cache: dict[str, list] = {}

# Fallback pygame font — only created if the spritesheet is missing
_fallback_font: Optional[pygame.font.Font] = None

FONT_DIR = os.path.join("assets", "ui", "fonts", "dmg_font")

# Integer scale applied to every digit sprite at load time.
# pygame.transform.scale uses nearest-neighbor — pixel art stays sharp.
# Never use smoothscale here or the crisp edges will blur.
DIGIT_SCALE = 4


def _load_digits(variant: str) -> list:
    """
    Load digit surfaces for the given variant and cache them.

    Tries to read 0.png … 9.png from FONT_DIR. If any file is missing the
    whole variant falls back to a rendered system font so nothing crashes.
    Each digit is tinted to the variant's colour and scaled up by DIGIT_SCALE
    using nearest-neighbor interpolation to keep the pixel art sharp.
    """
    if variant in _digit_cache:
        return _digit_cache[variant]

    tint = _VARIANT_COLOR[variant]
    digits = []
    all_loaded = True

    for i in range(10):
        path = os.path.join(FONT_DIR, f"{i}.png")
        if os.path.isfile(path):
            try:
                surf = pygame.image.load(path).convert_alpha()
                # Scale up with nearest-neighbor BEFORE tinting so we're
                # colouring the already-enlarged pixels, not blended edges
                w, h = surf.get_width(), surf.get_height()
                surf = pygame.transform.scale(surf, (w * DIGIT_SCALE, h * DIGIT_SCALE))
                # Apply colour tint while preserving per-pixel alpha
                tinted = surf.copy()
                tinted.fill((*tint, 255), special_flags=pygame.BLEND_RGBA_MULT)
                digits.append(tinted)
            except pygame.error:
                all_loaded = False
                break
        else:
            all_loaded = False
            break

    if not all_loaded or not digits:
        # Sprite assets aren't in place yet — use a system font as placeholder
        digits = []  # empty list signals "use fallback path in draw"

    _digit_cache[variant] = digits
    return digits


def _get_fallback_font() -> pygame.font.Font:
    """Return (and lazily create) the shared fallback font."""
    global _fallback_font
    if _fallback_font is None:
        _fallback_font = pygame.font.Font(None, 36)  # Larger fallback to match scaled sprites
    return _fallback_font


# ── Colour palette ─────────────────────────────────────────────────────────────

_VARIANT_COLOR: dict[str, tuple] = {
    "enemy":  (255, 60,  60),   # Red — player hits enemy
    "player": (255, 60,  60),   # Red           — enemy hits player
}

_VARIANT_OUTLINE: dict[str, tuple] = {
    "enemy":  (100, 0,   0),
    "player": (100, 0,   0),
}


# ── Single damage number ────────────────────────────────────────────────────────

class DamageNumber:
    """
    One floating number in world-space that drifts upward and fades out.

    Stored in world coordinates; converted to screen-space each draw call
    the same way projectiles and melee effects do it.
    """

    # Tuning — tweak these to match the feel you want
    FLOAT_SPEED   = 22.0   # World pixels per second (upward drift)
    LIFETIME      = 0.65   # Total seconds before the number is fully gone
    FADE_START    = 0.50   # Fraction of lifetime after which alpha starts dropping

    def __init__(
        self,
        world_x: float,
        world_y: float,
        amount: int,
        variant: str = "enemy",
    ):
        # Slight random horizontal jitter so stacked hits from rapid combos
        # don't perfectly overlap — same trick the GBA games use
        self.x = world_x + random.uniform(-4, 4)
        self.y = world_y

        self.amount  = amount
        self.variant = variant
        self.active  = True

        self._elapsed = 0.0
        self._alpha   = 255

        # Pre-load (or retrieve from cache) the digit surfaces
        self._digits = _load_digits(variant)

    # ── Update ──────────────────────────────────────────────────────────────────

    def update(self, dt: float):
        self._elapsed += dt
        self.y -= self.FLOAT_SPEED * dt  # Drift upward in world-space

        if self._elapsed >= self.LIFETIME:
            self.active = False

    # ── Draw ────────────────────────────────────────────────────────────────────

    def draw(self, screen: pygame.Surface, camera, render_scale: int):
        if not self.active:
            return

        from config.settings import RENDER_SCALE as RS
        rs = render_scale or RS

        # World → screen conversion, same pattern as Projectile / MeleeAttack
        screen_x = int(self.x * rs - camera.x)
        screen_y = int(self.y * rs - camera.y)

        text = str(self.amount)

        if self._digits:
            self._draw_with_sprites(screen, screen_x, screen_y, text)
        else:
            self._draw_with_font(screen, screen_x, screen_y, text)

    def _draw_with_sprites(self, screen, cx: int, cy: int, text: str):
        spacing = -4
        digits_to_draw = [self._digits[int(ch)] for ch in text if ch.isdigit()]
        total_w = sum(d.get_width() for d in digits_to_draw) + spacing * (len(digits_to_draw) - 1)
        max_h = max(d.get_height() for d in digits_to_draw)

        # Render everything onto one surface, then fade the whole thing at once.
        # This prevents the outline from outlasting the main digit at low alpha.
        composite = pygame.Surface((total_w + 2, max_h + 2), pygame.SRCALPHA)

        outline_color = _VARIANT_OUTLINE[self.variant]
        x_cursor = 1  # 1px padding so outline doesn't clip the edge
        for digit_surf in digits_to_draw:
            dw = digit_surf.get_width()
            dh = digit_surf.get_height()
            oy = (max_h - dh) // 2 + 1

            outline_surf = digit_surf.copy()
            outline_surf.fill((*outline_color, 255), special_flags=pygame.BLEND_RGBA_MULT)
            for ox, ofs in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                composite.blit(outline_surf, (x_cursor + ox, oy + ofs))

            composite.blit(digit_surf, (x_cursor, oy))
            x_cursor += dw + spacing

        # One alpha call on the whole surface — no per-digit bleed
        composite.set_alpha(self._alpha)
        screen.blit(composite, (cx - total_w // 2 - 1, cy - max_h // 2 - 1))

    def _draw_with_font(self, screen, cx: int, cy: int, text: str):
        """
        Fallback renderer — plain pygame font with a 1px outline.
        Used only when the real sprite assets aren't present yet.
        """
        font = _get_fallback_font()
        color   = _VARIANT_COLOR[self.variant]
        outline = _VARIANT_OUTLINE[self.variant]

        # Outline pass
        outline_surf = font.render(text, True, outline)
        outline_surf.set_alpha(self._alpha)
        or_ = outline_surf.get_rect(center=(cx, cy))
        for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            screen.blit(outline_surf, or_.move(ox, oy))

        # Main text
        text_surf = font.render(text, True, color)
        text_surf.set_alpha(self._alpha)
        screen.blit(text_surf, text_surf.get_rect(center=(cx, cy)))


# ── Manager ─────────────────────────────────────────────────────────────────────

class DamageNumberManager:
    """
    Owns and drives all active damage number popups.

    Intended usage (in Game):
        # __init__
        self.dmg_numbers = DamageNumberManager()

        # _update_enemies / wherever damage happens
        if enemy.check_collision_with_attack(melee, 'melee'):
            self.dmg_numbers.spawn(enemy.x, enemy.y - enemy.height // 2, enemy.last_damage_dealt)

        # _update_player / player.take_damage call site
        self.dmg_numbers.spawn(player.x, player.y - player.height // 2,
                               amount, variant='player')

        # draw() — after layer_manager.draw_all(), before HUD
        self.dmg_numbers.draw(self.logical_surface, self.camera, RENDER_SCALE)

        # update() — inside the main update method
        self.dmg_numbers.update(dt)
    """

    def __init__(self):
        self._numbers: list[DamageNumber] = []

    def spawn(
        self,
        world_x: float,
        world_y: float,
        amount: int,
        variant: str = "enemy",
    ):
        """
        Spawn a new damage popup.

        Args:
            world_x / world_y : World-space position of the hit (usually the
                                 centre-top of the entity that was struck).
            amount             : Damage value to display.
            variant            : 'enemy' or 'player' — both render in red.
        """
        if amount <= 0:
            return  # Don't clutter the screen for 0-damage events
        self._numbers.append(DamageNumber(world_x, world_y, amount, variant))

    def update(self, dt: float):
        """Tick every popup and prune the ones that have finished."""
        for num in self._numbers:
            num.update(dt)
        # Remove spent popups — list comprehension to avoid mid-loop mutation
        self._numbers = [n for n in self._numbers if n.active]

    def draw(self, screen: pygame.Surface, camera, render_scale: int):
        """Draw all active popups. Call after layer_manager.draw_all()."""
        for num in self._numbers:
            num.draw(screen, camera, render_scale)

    def clear(self):
        """Wipe all active popups — call on room transitions."""
        self._numbers.clear()