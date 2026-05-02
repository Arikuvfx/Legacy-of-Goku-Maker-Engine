"""
core/cutscene_trigger.py

Invisible zone that fires a cutscene when the player walks into it.

Design notes
────────────
- No callbacks stored on the object. The game loop calls check_and_trigger(player)
  each frame and acts on the returned cutscene_id string (or None).

- one_shot=True  → trigger deactivates permanently after the first fire.
  one_shot=False → trigger fires every time the player enters (re-arms on exit).

- A small cooldown (default 1s) prevents the trigger from immediately re-firing
  if the player is still standing inside when a cutscene ends.

- In dev mode the zone renders as a purple hatched rectangle — distinct from
  red collision walls and blue room-transition portals.

Serialisation format
────────────────────
{
    "type":        "cutscene_trigger",
    "x":           100,
    "y":           80,
    "width":       48,
    "height":      48,
    "cutscene_id": "intro_cutscene",
    "one_shot":    true,
    "cooldown":    1.0,
    "room":        "throne_room"
}
"""

from __future__ import annotations

import pygame
from typing import List, Optional


# ── Trigger object ─────────────────────────────────────────────────────────────

class CutsceneTrigger:
    """Invisible zone that fires a named cutscene when the player enters."""

    def __init__(
        self,
        x: int,
        y: int,
        width: int = 48,
        height: int = 48,
        cutscene_id: str = "",
        one_shot: bool = True,
        cooldown: float = 1.0,
        room_name: str = "",
    ):
        # Position and size in world-space tiles/pixels (matches room coordinate system)
        self.x = x
        self.y = y
        self.width = width
        self.height = height

        # Which cutscene to fire — must match a key in the cutscene registry
        self.cutscene_id = cutscene_id

        # one_shot=True means this trigger is done after it fires once.
        # Useful for story beats that should only play on first visit.
        self.one_shot = one_shot

        # How long (in seconds) to block re-triggering after a fire.
        # Prevents the cutscene from instantly re-queuing if the player
        # hasn't moved out of the zone by the time it ends.
        self.cooldown = cooldown

        self.room_name = room_name

        # Editor metadata — used by the level editor to categorise/display this object
        self.id = "cutscene_trigger"
        self.name = "Cutscene Trigger"
        self.category = "System"

        # --- Runtime state (not serialised) ---
        self.active = True          # Flipped to False after a one-shot fires
        self._player_inside = False # Tracks whether the player was inside last frame
        self._cooldown_elapsed = 0.0  # Counts down to 0 after each fire

    # ── Geometry ───────────────────────────────────────────────────────────────

    def get_rect(self) -> pygame.Rect:
        return pygame.Rect(self.x, self.y, self.width, self.height)

    # ── Core API ───────────────────────────────────────────────────────────────

    def check_and_trigger(self, player) -> Optional[str]:
        """
        Call once per frame from the game loop.

        Returns cutscene_id the moment the player *enters* the zone and the
        trigger is ready to fire. Returns None every other frame.

        The caller is responsible for starting the actual cutscene runtime.
        """
        # Skip if permanently disabled or still cooling down.
        # We don't tick the cooldown here — update() handles that separately.
        if not self.active or self._cooldown_elapsed > 0:
            return None

        player_rect = player.get_collision_rect()
        zone_rect = self.get_rect()
        now_inside = zone_rect.colliderect(player_rect)

        # Only fire on the leading edge (player wasn't inside last frame, but is now).
        # This prevents the trigger from spamming every frame while the player stands inside.
        fired = now_inside and not self._player_inside

        # Update inside-state *after* the edge check, not before
        self._player_inside = now_inside

        if fired:
            self._cooldown_elapsed = self.cooldown
            if self.one_shot:
                self.active = False  # Never fire again
            return self.cutscene_id

        return None

    def reset(self):
        """Re-arm a one-shot trigger — called on room reload or new game."""
        self.active = True
        self._player_inside = False
        self._cooldown_elapsed = 0.0

    # ── Update ─────────────────────────────────────────────────────────────────

    def update(self, dt: float):
        """Tick down the post-fire cooldown. Call every frame."""
        if self._cooldown_elapsed <= 0:
            return

        self._cooldown_elapsed = max(0.0, self._cooldown_elapsed - dt)

        # Once the cooldown finishes on a repeatable trigger, clear the inside-state.
        # This forces the player to exit and re-enter before it can fire again,
        # so just standing in the zone after a cutscene doesn't immediately re-trigger.
        if self._cooldown_elapsed == 0.0 and not self.one_shot:
            self._player_inside = False

    # ── Serialisation ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON/room-file saving."""
        return {
            "type":        "cutscene_trigger",
            "x":           self.x,
            "y":           self.y,
            "width":       self.width,
            "height":      self.height,
            "cutscene_id": self.cutscene_id,
            "one_shot":    self.one_shot,
            "cooldown":    self.cooldown,
            "room":        self.room_name,
        }

    @staticmethod
    def from_dict(data: dict, room_name: str) -> "CutsceneTrigger":
        """Deserialise from a saved dict. Falls back to defaults for missing keys."""
        return CutsceneTrigger(
            x           = data.get("x", 0),
            y           = data.get("y", 0),
            width       = data.get("width", 48),
            height      = data.get("height", 48),
            cutscene_id = data.get("cutscene_id", ""),
            one_shot    = data.get("one_shot", True),
            cooldown    = data.get("cooldown", 1.0),
            room_name   = room_name,
        )


# ── Manager ────────────────────────────────────────────────────────────────────

class CutsceneTriggerManager:
    """
    Owns all cutscene triggers across all rooms.

    Triggers are grouped by room name so only the active room's triggers
    are checked each frame — no need to iterate the whole world.
    """

    def __init__(self):
        # room_name → list of triggers in that room
        self._triggers: dict[str, List[CutsceneTrigger]] = {}

    # ── Query ──────────────────────────────────────────────────────────────────

    def get_triggers(self, room_name: str) -> List[CutsceneTrigger]:
        return self._triggers.get(room_name, [])

    # ── Mutation ───────────────────────────────────────────────────────────────

    def add_trigger(self, trigger: CutsceneTrigger) -> CutsceneTrigger:
        self._triggers.setdefault(trigger.room_name, []).append(trigger)
        return trigger

    def remove_trigger(self, trigger: CutsceneTrigger):
        room = self._triggers.get(trigger.room_name, [])
        if trigger in room:
            room.remove(trigger)

    def clear_room(self, room_name: str):
        """Wipe all triggers from a room — handy when hot-reloading a room file."""
        self._triggers[room_name] = []

    # ── Per-frame helpers ──────────────────────────────────────────────────────

    def update(self, room_name: str, dt: float):
        """Tick cooldowns for every trigger in the current room."""
        for trigger in self.get_triggers(room_name):
            trigger.update(dt)

    def check_player(self, room_name: str, player) -> Optional[str]:
        """
        Check all triggers in the room against the player position.

        Returns the cutscene_id of the first trigger that fires this frame,
        or None. We only allow one cutscene to fire per frame — stacking
        simultaneous triggers would break the cutscene runtime.
        """
        for trigger in self.get_triggers(room_name):
            result = trigger.check_and_trigger(player)
            if result:
                return result
        return None

    def reset_room(self, room_name: str):
        """Re-arm all one-shot triggers in a room (new game / room reload)."""
        for trigger in self.get_triggers(room_name):
            trigger.reset()

    # ── Serialisation ──────────────────────────────────────────────────────────

    def save_to_dict(self) -> dict:
        return {
            room: [t.to_dict() for t in triggers]
            for room, triggers in self._triggers.items()
        }

    def load_from_dict(self, data: dict):
        self._triggers = {
            room: [CutsceneTrigger.from_dict(t, room) for t in triggers]
            for room, triggers in data.items()
        }


# ── Dev-mode rendering ─────────────────────────────────────────────────────────

def draw_cutscene_trigger(
    screen,
    trigger: CutsceneTrigger,
    camera_x: int,
    camera_y: int,
    render_scale: int,
    dev_mode: bool = True,
    selected: bool = False,
):
    """
    Draws the trigger zone — only visible in dev mode.

    Purple/magenta palette keeps it distinct from:
      • red  — collision walls
      • blue — room-transition portals
    """
    if not dev_mode:
        return

    # Convert from world-space to screen-space
    sx = (trigger.x * render_scale) - camera_x
    sy = (trigger.y * render_scale) - camera_y
    sw = trigger.width * render_scale
    sh = trigger.height * render_scale

    rect = pygame.Rect(int(sx), int(sy), int(sw), int(sh))

    # --- Semi-transparent fill ---
    # Brighter + more opaque when selected so it's easy to spot in the editor
    alpha = 160 if selected else 100
    fill_color = (220, 80, 255, alpha) if selected else (180, 0, 255, alpha)
    fill_surf = pygame.Surface((int(sw), int(sh)), pygame.SRCALPHA)
    fill_surf.fill(fill_color)
    screen.blit(fill_surf, (int(sx), int(sy)))

    # --- Solid border ---
    border_color = (255, 140, 255) if selected else (200, 0, 255)
    border_width = 3 if selected else 2
    pygame.draw.rect(screen, border_color, rect, border_width)

    # --- Diagonal hatch lines ---
    # Right-leaning, matches the visual style used on collision walls
    line_color = (220, 80, 255, 120) if selected else (160, 0, 200, 90)
    line_surf = pygame.Surface((int(sw), int(sh)), pygame.SRCALPHA)
    spacing = 16 * render_scale
    for i in range(int(-sh), int(sw + sh), int(spacing)):
        pygame.draw.line(line_surf, line_color, (i, 0), (i + sh, sh), 1)
    screen.blit(line_surf, (int(sx), int(sy)))

    # --- Corner drag handles ---
    # Square handles at each corner for resize-dragging in the editor
    handle = 6 * render_scale
    handle_color = (255, 200, 255) if selected else (220, 100, 255)
    for cx, cy in [
        (sx,      sy),
        (sx + sw, sy),
        (sx,      sy + sh),
        (sx + sw, sy + sh),
    ]:
        hx = int(cx - handle // 2)
        hy = int(cy - handle // 2)
        pygame.draw.rect(screen, handle_color, (hx, hy, int(handle), int(handle)))
        pygame.draw.rect(screen, (0, 0, 0),    (hx, hy, int(handle), int(handle)), 1)

    # --- Centre icon: mini clapperboard ---
    # Purely cosmetic, just makes it obvious what this zone does at a glance
    cx_f = sx + sw / 2
    cy_f = sy + sh / 2
    icon_w = min(sw * 0.5, 24 * render_scale)
    icon_h = icon_w * 0.75

    if icon_w >= 8:  # Don't bother drawing if the zone is too small
        body = pygame.Rect(
            int(cx_f - icon_w / 2),
            int(cy_f - icon_h / 2),
            int(icon_w),
            int(icon_h),
        )
        pygame.draw.rect(screen, (255, 255, 255), body, 1)

        # Clapper teeth along the top edge
        tooth_w = max(2, int(icon_w / 5))
        for i in range(4):
            tx = body.left + i * tooth_w * 2 + tooth_w // 2
            if tx + tooth_w <= body.right:
                pygame.draw.rect(
                    screen,
                    (255, 255, 255),
                    (tx, body.top - int(icon_h * 0.3), tooth_w, int(icon_h * 0.3)),
                )

    # --- Label: cutscene_id ---
    # Only drawn if the box is large enough to fit it without looking cramped
    if sw > 60 and sh > 28:
        font = pygame.font.Font(None, 18)
        label_text = trigger.cutscene_id if trigger.cutscene_id else "<no id>"
        label = font.render(label_text, True, (255, 255, 255))
        label_rect = label.get_rect(center=(int(sx + sw // 2), int(sy + sh - 14)))

        # Dark pill behind the text so it's readable over any background
        bg = pygame.Surface((label_rect.width + 8, label_rect.height + 4), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 180))
        screen.blit(bg,    (label_rect.x - 4, label_rect.y - 2))
        screen.blit(label,  label_rect)

    # --- "FIRED" badge ---
    # Shown on one-shot triggers that have already gone off, so designers
    # can see at a glance which triggers are spent during a playtest session
    if not trigger.active:
        font2 = pygame.font.Font(None, 18)
        badge = font2.render("FIRED", True, (255, 80, 80))
        badge_rect = badge.get_rect(topleft=(int(sx + 4), int(sy + 4)))
        bg2 = pygame.Surface((badge_rect.width + 6, badge_rect.height + 2), pygame.SRCALPHA)
        bg2.fill((0, 0, 0, 200))
        screen.blit(bg2,   (badge_rect.x - 3, badge_rect.y - 1))
        screen.blit(badge,  badge_rect)