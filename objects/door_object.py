"""
door.py

A Door is a two-frame structure object (closed / open) that opens when the
player gets close. A non-permanent door then stays open for as long as the
player remains in the room — it does NOT re-close just because the player
walks back out of proximity range — and only closes again once the player
leaves the room entirely (game.py resets it via `close()` when swapping in
a new room's object lists). A permanent door stays open forever once
triggered — including across room switches, since the Room (and this Door
instance) stays live in RoomManager for the session rather than being
reconstructed from disk.

Opening a door (permanent or not) plays a one-shot sound effect via
whatever SoundManager is passed into `update()`. Which effect plays is
per-door — `door_sound` names one of the bare SFX names loaded from
assets/audio/sfx/misc/ (door1.wav .. door5.wav ship by default; see
`Door.list_door_sounds()`), same lookup-by-bare-filename convention as
every other sound_manager.play_sfx() call in the game.

Art lives in assets/sprites/structures/door/ as one sheet per door_type — no
hardcoded list of variants. Each sheet is a single image with the two frames
side by side: the left half is always the closed frame, the right half
always the open frame, so a frame's width is exactly half the sheet's total
width and both frames share the sheet's full height. This lets the room
editor's object picker discover available door types by just listing the
files in that folder (see `Door.list_door_types()`) instead of maintaining
its own hardcoded set — dropping a new sheet in makes it show up.

Sizes vary a lot between door variants (small wood doors up to huge gates),
so — mirroring world_map_object's convention — a door's width/height are
derived directly from a single frame's pixel dimensions (half the sheet)
rather than being hardcoded, so any size of art just works.
"""
import os
import pygame
from config.settings import RENDER_SCALE
from core.draw_layers import DrawLayer


class Door:
    """A door placed in a room. Swaps between a closed and an open sprite
    based on player proximity."""

    PROXIMITY_RANGE = 48  # world units — distance at which the door opens
    SPRITE_DIR = 'assets/sprites/structures/door'

    # Where the door SFX live and what to fall back to if the folder isn't
    # there yet (mirrors SPRITE_DIR's fallback-to-placeholder philosophy).
    SOUND_DIR = 'assets/audio/sfx/misc'
    DEFAULT_SOUND_NAMES = ['door1', 'door2', 'door3', 'door4', 'door5']

    def __init__(self, x, y, door_type='wood', permanent=False, width=None, height=None,
                 door_sound=None):
        # x, y are the door's CENTER — same convention as FlyingPad, LevelGate,
        # SavePoint, and DestructibleStone — so it lines up with the room
        # editor's center-anchored ghost preview instead of landing offset
        # down-right of where the preview showed it.
        self.x = x
        self.y = y
        self.door_type = door_type
        self.permanent = permanent
        self.is_open = False
        # Bare SFX name (no folder, no extension) played once when this door
        # opens — e.g. 'door1'. Defaults to the first sound list_door_sounds()
        # finds on disk (falling back to DEFAULT_SOUND_NAMES[0] if none is
        # found there yet) so a freshly-placed door always has something set.
        self.door_sound = door_sound or (self.list_door_sounds() or self.DEFAULT_SOUND_NAMES)[0]
        self.id = 'door'
        self.name = 'Door'
        self.category = 'Structures'

        # One frame's footprint defines the door's footprint. If width/height
        # are passed in (e.g. restoring from a save file) they win over
        # whatever size the asset on disk happens to be, so a door doesn't
        # change size out from under a saved room if the art gets swapped later.
        self.closed_sprite, self.open_sprite = self._load_sprites(width, height)
        self.width  = width  if width  else self.closed_sprite.get_width()
        self.height = height if height else self.closed_sprite.get_height()
        if self.closed_sprite.get_size() != (self.width, self.height):
            self.closed_sprite = pygame.transform.scale(self.closed_sprite, (self.width, self.height))
        if self.open_sprite.get_size() != (self.width, self.height):
            self.open_sprite = pygame.transform.scale(self.open_sprite, (self.width, self.height))

    def _load_sprites(self, width=None, height=None):
        """Load this door's sheet and split it into closed/open frames.
        Layout is fixed: one image, two frames side by side — left half
        closed, right half open — so frame width is always exactly half
        the sheet's total width, and frame height is the sheet's full height."""
        try:
            path = f'{self.SPRITE_DIR}/{self.door_type}.png'
            sheet = pygame.image.load(path).convert_alpha()
            sheet_w, sheet_h = sheet.get_size()
            frame_w = sheet_w // 2
            closed_sprite = sheet.subsurface((0, 0, frame_w, sheet_h)).copy()
            open_sprite   = sheet.subsurface((frame_w, 0, frame_w, sheet_h)).copy()
            return closed_sprite, open_sprite
        except Exception:
            # Asset not on disk yet — flat placeholder panels, closed frame a
            # shade darker than open so the state reads clearly even without
            # real art.
            w = width or 32
            h = height or 64
            closed_sprite = pygame.Surface((w, h), pygame.SRCALPHA)
            closed_sprite.fill((101, 67, 33))
            pygame.draw.rect(closed_sprite, (0, 0, 0), (0, 0, w, h), 2)
            pygame.draw.circle(closed_sprite, (0, 0, 0), (w - 8, h // 2), 2)  # handle
            open_sprite = pygame.Surface((w, h), pygame.SRCALPHA)
            open_sprite.fill((181, 140, 90))
            pygame.draw.rect(open_sprite, (0, 0, 0), (0, 0, w, h), 2)
            return closed_sprite, open_sprite

    @classmethod
    def list_door_types(cls):
        """Scan SPRITE_DIR for available door sheets so the room editor's
        object picker doesn't need its own hardcoded list — each .png file's
        name (minus extension) is a usable door_type. Returns [] if the
        folder doesn't exist yet rather than raising."""
        try:
            names = []
            for fname in sorted(os.listdir(cls.SPRITE_DIR)):
                stem, ext = os.path.splitext(fname)
                if ext.lower() == '.png':
                    names.append(stem)
            return names
        except Exception:
            return []

    @classmethod
    def list_door_sounds(cls):
        """Scan SOUND_DIR for door*.wav files so the object editor's door
        sound picker doesn't need its own hardcoded list — mirrors
        `list_door_types()`. Returns [] if the folder doesn't exist yet
        (caller falls back to DEFAULT_SOUND_NAMES) rather than raising."""
        try:
            names = []
            for fname in sorted(os.listdir(cls.SOUND_DIR)):
                stem, ext = os.path.splitext(fname)
                if ext.lower() == '.wav' and stem.lower().startswith('door'):
                    names.append(stem)
            return names
        except Exception:
            return []

    def get_rect(self) -> pygame.Rect:
        """`x, y` are the door's center — same convention as FlyingPad,
        LevelGate, SavePoint, and DestructibleStone — so this converts to a
        top-left-anchored Rect for collision/drawing purposes."""
        return pygame.Rect(
            int(self.x - self.width / 2), int(self.y - self.height / 2),
            self.width, self.height,
        )

    def get_current_sprite(self):
        return self.open_sprite if self.is_open else self.closed_sprite

    def get_sort_key(self):
        """(layer, y) tuple consumed by LayerManager.draw_all — doors sit in
        the same Y-sorted bucket as the player/NPCs/level gates/destructible
        stones, so the player can walk in front of or behind a door depending
        on which one is further down the room."""
        return (DrawLayer.NPCS, self.y)

    def update(self, player, sound_manager=None):
        """Call once per frame with the active player. Opens on proximity and
        then stays open — for a permanent door, forever; for a non-permanent
        door, for as long as the player is still in this room (it no longer
        re-closes just because the player stepped back out of proximity
        range). Non-permanent doors are reset closed by the caller via
        `close()` when the player actually leaves the room.

        `sound_manager` is optional (any object with .play_sfx(name), e.g.
        SoundManager) — if given, it plays `self.door_sound` once, on the
        exact frame the door transitions from closed to open."""
        if self.permanent and self.is_open:
            return

        was_open = self.is_open

        px = player.x + getattr(player, 'width', 0) / 2
        py = player.y + getattr(player, 'height', 0) / 2
        dist_sq = (px - self.x) ** 2 + (py - self.y) ** 2

        if dist_sq <= self.PROXIMITY_RANGE ** 2:
            self.is_open = True

        if self.is_open and not was_open and sound_manager is not None:
            sound_manager.play_sfx(self.door_sound)

    def close(self):
        """Force this door shut. Used when the player leaves the room a
        non-permanent door lives in — permanent doors ignore this since they
        stay open forever once triggered, including across room switches."""
        if not self.permanent:
            self.is_open = False

    def draw(self, screen, camera, colors=None):
        """`camera` only needs .x/.y screen-space offset attributes — same
        lightweight contract as LevelGate/SavePoint/MusicObject.draw(), so this
        works with both the real Camera and the editor's temp camera stand-in.
        `colors` is accepted for call-signature parity with those but unused."""
        sprite = self.get_current_sprite()
        if not sprite:
            return
        rect = self.get_rect()
        screen_x = (rect.x * RENDER_SCALE) - camera.x
        screen_y = (rect.y * RENDER_SCALE) - camera.y
        scaled_w = int(self.width * RENDER_SCALE)
        scaled_h = int(self.height * RENDER_SCALE)
        scaled = pygame.transform.scale(sprite, (scaled_w, scaled_h))
        screen.blit(scaled, (int(screen_x), int(screen_y)))

    def to_dict(self):
        return {
            'x': self.x, 'y': self.y,
            'door_type': self.door_type,
            'width': self.width, 'height': self.height,
            'permanent': self.permanent,
            'door_sound': self.door_sound,
            # Only worth persisting is_open for permanent doors — a non-permanent
            # door always starts closed on load anyway.
            'is_open': self.is_open if self.permanent else False,
        }

    @staticmethod
    def from_dict(data: dict) -> 'Door':
        door = Door(
            data.get('x', 0), data.get('y', 0),
            data.get('door_type', 'wood'),
            data.get('permanent', False),
            data.get('width'), data.get('height'),
            data.get('door_sound'),
        )
        door.is_open = bool(data.get('is_open', False)) and door.permanent
        return door


class DoorManager:
    """Tracks doors across all rooms — same shape as SavePointManager/MusicObjectManager."""

    def __init__(self):
        self.doors = {}

    def get_doors(self, room_name):
        return self.doors.get(room_name, [])

    def add_door(self, room_name, door):
        self.doors.setdefault(room_name, []).append(door)
        return door

    def remove_door(self, room_name, door):
        room_doors = self.doors.get(room_name, [])
        if door in room_doors:
            room_doors.remove(door)

    def clear_room(self, room_name):
        self.doors[room_name] = []