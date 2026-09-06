"""
Treasure chest world object (Legacy of Goku Maker Engine).

A chest's sprite sheet always has exactly 2 frames laid out side by side:
frame 0 (left half) is closed, frame 1 (right half) is open. Frame width is
always half the sheet's total width; frame height is the sheet's full
height. Once a chest is opened it stays open forever — there's no closing
it back up, so `opened` is just a flag that gets flipped once and persisted
with the room like everything else placed in it.

Variants are discovered from assets/objects/chest/ at import time — one PNG
per chest skin (e.g. wood.png, gold.png) — the same way Door.list_door_types()
discovers door skins from assets/sprites/structures/door/.

x/y are the chest's CENTER, matching every other placeable object in this
engine (SavePoint, Door, FlyingPad, etc.) — see ObjectEditor._check_object_at_position.
"""

import os
import pygame

from config.settings import RENDER_SCALE
from core.draw_layers import DrawLayer

CHEST_DIR = os.path.join('assets', 'objects', 'chest')

# World units — how close the player needs to be to open a chest with E.
# Same ballpark as the world-map sign's proximity radius in game.py.
INTERACT_RADIUS = 28

# Used only when a variant's .png isn't on disk yet, so placement/testing
# still works before real art exists.
_PLACEHOLDER_CLOSED_COLOR = (139, 90, 20)
_PLACEHOLDER_OPEN_COLOR = (200, 170, 60)
_PLACEHOLDER_SIZE = (24, 20)


class Chest:
    """A placeable, once-only-openable chest."""

    def __init__(self, x, y, chest_type='wood', opened=False, item_id='', item_qty=1):
        self.x = x
        self.y = y
        self.chest_type = chest_type
        self.opened = opened
        # What the player gets on opening. '' = empty chest — still
        # openable (the lid still animates), it just has nothing inside.
        self.item_id = item_id
        self.item_qty = max(1, item_qty)
        self.width = _PLACEHOLDER_SIZE[0]
        self.height = _PLACEHOLDER_SIZE[1]
        self.closed_sprite = None
        self.open_sprite = None
        self._load_sprites()

        # Pre-scaled render-ready copies of closed_sprite/open_sprite, built
        # lazily the first time each is actually drawn and cached from then
        # on — draw() used to call pygame.transform.scale() on the source
        # sprite fresh every single frame, which is wasted work for a
        # sprite that only has two possible states and never actually
        # changes size at runtime.
        self._scaled_closed = None
        self._scaled_open = None
        self._scaled_render_scale = None

        # LayerManager (core/draw_layers.py) requires every drawable to
        # expose get_sort_key() — same (layer, y) shape as Door/SavePoint/
        # LevelGate/etc. y_sort=True so the chest draws in front of or
        # behind the player correctly depending on their relative Y, same
        # as any other same-level world object.
        self.draw_layer = DrawLayer.ITEMS
        self.y_sort = True

        # Set each frame by update() — mirrors SavePoint.is_player_nearby
        # so game.py's interact handling can treat chests the same way.
        self.is_player_nearby = False

    def get_sort_key(self):
        """(layer, y) tuple consumed by LayerManager.draw_all()."""
        return (self.draw_layer, self.y)

    def update(self, dt, player):
        """Track whether the player is close enough to open this chest.
        Already-opened chests still track proximity (harmless) — game.py
        is the one that decides an opened chest is no longer interactable."""
        dx = player.x - self.x
        dy = player.y - self.y
        self.is_player_nearby = (dx * dx + dy * dy) <= INTERACT_RADIUS ** 2

    def get_collision_rect(self):
        """World-space collision box (world units, not RENDER_SCALEd — same
        convention as the other solid objects fed into player.obstacles).

        Uses the closed frame's actual opaque-pixel bounds
        (_collision_local_rect), not the full frame_w x frame_h — the two
        frames share one canvas size (frame_h = whole sheet height), so the
        closed frame is padded with transparent pixels above it to make
        room for the taller open lid. Using the raw frame size would make
        the chest collide with a bunch of empty air above the actual
        closed-chest art. The footprint is fixed from the closed frame and
        doesn't change when the chest opens — it's still the same physical
        box, only the lid art changes."""
        frame_left = self.x - self.width / 2
        frame_top = self.y - self.height / 2
        local = self._collision_local_rect
        return pygame.Rect(
            int(frame_left + local.x),
            int(frame_top + local.y),
            local.width,
            local.height,
        )

    def _load_sprites(self):
        path = os.path.join(CHEST_DIR, f'{self.chest_type}.png')
        try:
            sheet = pygame.image.load(path).convert_alpha()
            sheet_w, sheet_h = sheet.get_size()
            frame_w = sheet_w // 2
            frame_h = sheet_h
            self.closed_sprite = sheet.subsurface((0, 0, frame_w, frame_h)).copy()
            self.open_sprite = sheet.subsurface((frame_w, 0, frame_w, frame_h)).copy()
            self.width = frame_w
            self.height = frame_h
        except (pygame.error, OSError, FileNotFoundError):
            # Asset not on disk yet — hand-drawn placeholder so placement
            # and testing still work before real art exists.
            self.closed_sprite = self._make_placeholder(_PLACEHOLDER_CLOSED_COLOR)
            self.open_sprite = self._make_placeholder(_PLACEHOLDER_OPEN_COLOR)

        # Tight bounding rect of the closed frame's actual opaque pixels,
        # in that frame's own local coordinates (0,0 = frame's top-left).
        # See get_collision_rect() for why this — not the full frame — is
        # what gets used for collision. Falls back to the whole frame if
        # the closed sprite is entirely transparent (nothing to bound).
        bounds = self.closed_sprite.get_bounding_rect()
        self._collision_local_rect = bounds if bounds.width and bounds.height \
            else pygame.Rect(0, 0, self.width, self.height)

    def _make_placeholder(self, color):
        surf = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        surf.fill(color)
        pygame.draw.rect(surf, (0, 0, 0), (0, 0, self.width, self.height), 2)
        return surf

    @property
    def sprite(self):
        """Whichever frame matches the chest's current state."""
        return self.open_sprite if self.opened else self.closed_sprite

    def open(self):
        """Open the chest. Permanent — there's no closing it back up."""
        self.opened = True

    def grant_loot(self, player):
        """Append this chest's item to player.inventory, item_qty times —
        same append-per-unit convention _apply_mission_rewards uses for
        mission item rewards. No-op for an empty chest (item_id == '').
        Doesn't check/flip `opened` itself; game.py calls open() separately
        so a chest can still play its open animation with nothing inside."""
        if not self.item_id:
            return
        inventory = getattr(player, 'inventory', None)
        if inventory is None:
            return
        for _ in range(self.item_qty):
            inventory.append(self.item_id)

    def draw(self, screen, camera, colors=None):
        """Draw the chest's current frame, centered on (x, y) like every
        other placed object."""
        sprite = self.sprite
        if not sprite:
            return
        screen_x = int(self.x * RENDER_SCALE - camera.x)
        screen_y = int(self.y * RENDER_SCALE - camera.y)

        # Rebuild the scaled cache if RENDER_SCALE changed (or hasn't been
        # built yet) — otherwise reuse it instead of rescaling every frame.
        if self._scaled_render_scale != RENDER_SCALE:
            size = (max(1, int(self.width * RENDER_SCALE)), max(1, int(self.height * RENDER_SCALE)))
            self._scaled_closed = pygame.transform.scale(self.closed_sprite, size)
            self._scaled_open = pygame.transform.scale(self.open_sprite, size)
            self._scaled_render_scale = RENDER_SCALE

        scaled = self._scaled_open if self.opened else self._scaled_closed
        screen.blit(scaled, scaled.get_rect(center=(screen_x, screen_y)))

    def to_dict(self):
        return {
            'x': self.x,
            'y': self.y,
            'chest_type': self.chest_type,
            'opened': self.opened,
            'item_id': self.item_id,
            'item_qty': self.item_qty,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            data.get('x', 0),
            data.get('y', 0),
            data.get('chest_type', 'wood'),
            data.get('opened', False),
            data.get('item_id', ''),
            data.get('item_qty', 1),
        )

    @staticmethod
    def list_chest_types():
        """Every chest skin available on disk — one entry per .png file in
        assets/objects/chest/, named after the file (minus extension).
        Returns [] (not an error) if the folder doesn't exist yet, so
        callers can fall back to a sensible default."""
        try:
            return sorted(
                os.path.splitext(f)[0] for f in os.listdir(CHEST_DIR)
                if f.lower().endswith('.png')
            )
        except OSError:
            return []


class ChestManager:
    """Per-room registry of placed chests — same shape as DoorManager/
    SavePointManager (a plain dict of room_name -> list[Chest], directly
    assignable so RoomEditor's room<->manager sync can do
    `chest_manager.chests[room_name] = room.chests` like it does for doors)."""

    def __init__(self):
        self.chests = {}  # room_name -> list[Chest]

    def get_chests(self, room_name):
        return self.chests.get(room_name, [])

    def add_chest(self, room_name, chest):
        self.chests.setdefault(room_name, []).append(chest)

    def remove_chest(self, room_name, chest):
        if chest in self.chests.get(room_name, []):
            self.chests[room_name].remove(chest)