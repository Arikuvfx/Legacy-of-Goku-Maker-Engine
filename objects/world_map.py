"""
objects/world_map_object.py

Single WorldMapObject with two variants:

  'world_map'      — flat ground sprite, DrawLayer.GROUND, no y-sort, no collision.
                     Sprite: assets/objects/world_map/world_map.png

  'world_map_sign' — upright sign, DrawLayer.NPCS, y-sorted, has collision rect.
                     Sprite: assets/objects/world_map/world_map_sign.png
"""

import json
import os

import pygame
from config.settings import RENDER_SCALE
from core.draw_layers import DrawLayer

# ── world-map tile constants (must match world_map_editor.py) ─────────────────
_SAVE_DIR    = os.path.join("assets", "world_maps")
_TILESET_DIR = os.path.join("assets", "tilesets", "world_map")
_NATIVE_TILE = 8        # source tile size in pixels
_MAP_TILE_W  = 362     # map width  in tiles
_MAP_TILE_H  = 263     # map height in tiles


class _Tileset:
    """Minimal tileset loader with a per-display-size tile cache.
    Self-contained so world_map.py has no dependency on the editor module.
    """

    def __init__(self, name: str, path: str):
        self.name  = name
        self.image = None
        self.cols  = self.rows = 0
        self._cache: dict = {}
        try:
            self.image = pygame.image.load(path).convert_alpha()
            w, h = self.image.get_size()
            self.cols = w // _NATIVE_TILE
            self.rows = h // _NATIVE_TILE
        except Exception:
            pass  # image missing; get_tile() will return None

    def get_tile(self, tx: int, ty: int, px: int):
        """Return a surface for tile (tx, ty) scaled to px × px; cached."""
        key = (tx, ty, px)
        if key not in self._cache:
            if (self.image is None
                    or not (0 <= tx < self.cols)
                    or not (0 <= ty < self.rows)):
                self._cache[key] = None
            else:
                raw = self.image.subsurface(
                    pygame.Rect(tx * _NATIVE_TILE, ty * _NATIVE_TILE,
                                _NATIVE_TILE, _NATIVE_TILE)
                ).copy()
                self._cache[key] = pygame.transform.scale(raw, (px, px))
        return self._cache[key]

    def invalidate(self):
        self._cache.clear()


class WorldMapObject:
    """World map object — variant selects appearance, layer, and collision behaviour."""

    _SIGN_COLLISION_W = 16
    _SIGN_COLLISION_H = 8

    _DEFAULTS = {
        'world_map':      {'width': 32, 'height': 37},
        'world_map_sign': {'width': 29, 'height': 32},
    }

    def __init__(self, x, y, variant='world_map', map_name='', entity_name=''):
        self.x       = float(x)
        self.y       = float(y)
        self.variant = variant
        self.map_name    = map_name     # stem of the JSON file, e.g. 'overworld'
        self.entity_name = entity_name  # name of the WMEntity this object represents
                                        # (empty = not linked to any entity)
        self.frame_idx = 0         # which frame to display (for future animation)
        self.active  = True

        d = self._DEFAULTS.get(variant, self._DEFAULTS['world_map'])
        self.width  = d['width']
        self.height = d['height']

        if variant == 'world_map_sign':
            self.draw_layer = DrawLayer.NPCS
            self.y_sort     = True
        else:
            self.draw_layer = DrawLayer.GROUND
            self.y_sort     = False

        # Tile-map data (lazy-loaded on first draw)
        self._wm_tiles: dict   = {}   # (tx, ty) → raw tile dict from JSON
        self._tilesets: dict   = {}   # tileset name → _Tileset
        self._wm_loaded: bool  = False

        # Always load the room-view sprite. For 'world_map' this is just the
        # small ground-plane icon; map_name is Mode7 metadata, not a visual source.
        self.sprite = None
        self._load_sprite()

        # Cached RENDER_SCALE-scaled copy of self.sprite, shared by draw()
        # and _draw_sign() — built lazily and only rebuilt if RENDER_SCALE
        # changes, instead of rescaling the source sprite every frame.
        self._scaled_sprite = None
        self._scaled_sprite_scale = None

    def get_sort_key(self):
        y = self.y if self.y_sort else 0
        return (self.draw_layer, y)

    def get_collision_rect(self):
        if self.variant != 'world_map_sign':
            return None
        return pygame.Rect(
            self.x - self._SIGN_COLLISION_W // 2,
            self.y - self._SIGN_COLLISION_H // 2,
            self._SIGN_COLLISION_W,
            self._SIGN_COLLISION_H,
        )

    def get_rect(self):
        """Solid rect used by the obstacle/collision system (None for non-solid variants)."""
        return self.get_collision_rect()

    def _load_sprite(self):
        try:
            self.sprite = pygame.image.load(
                f'assets/objects/world_map/{self.variant}.png'
            ).convert_alpha()
            self.width  = self.sprite.get_width()  or self.width
            self.height = self.sprite.get_height() or self.height
        except Exception:
            self.sprite = None

    # ── tile-map loading ──────────────────────────────────────────────────────

    def _load_world_map(self):
        """Load tile data from JSON and pre-load referenced tilesets."""
        self._wm_loaded = True
        path = os.path.join(_SAVE_DIR, f'{self.map_name}.json')
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            return  # missing file — draw() will fall back to sprite

        # Support multi-frame format (use self.frame_idx) and legacy 'tiles' key
        if 'frames' in data and data['frames']:
            idx = min(self.frame_idx, len(data['frames']) - 1)
            tiles_list = data['frames'][idx]
        else:
            tiles_list = data.get('tiles', [])

        for td in tiles_list:
            self._wm_tiles[(td['x'], td['y'])] = td

        # Load every tileset referenced by the tiles
        for td in self._wm_tiles.values():
            ts_name = td['ts']
            if ts_name not in self._tilesets:
                ts_path = os.path.join(_TILESET_DIR, ts_name)
                if not ts_path.lower().endswith('.png'):
                    ts_path += '.png'
                self._tilesets[ts_name] = _Tileset(ts_name, ts_path)

        # NOTE: do NOT change self.width/height here — those are the room-object
        # footprint used for placement and collision, not the Mode7 map canvas size.

    def _ensure_loaded(self):
        if not self._wm_loaded:
            self._load_world_map()

    # ── drawing ───────────────────────────────────────────────────────────────

    def update(self, dt, player=None):
        pass

    def _get_scaled_sprite(self, w, h):
        """Cached RENDER_SCALE-sized copy of self.sprite. Shared by draw()
        and _draw_sign() since a given instance only ever needs one size."""
        if self._scaled_sprite_scale != (w, h):
            self._scaled_sprite = pygame.transform.scale(self.sprite, (w, h))
            self._scaled_sprite_scale = (w, h)
        return self._scaled_sprite

    def draw(self, screen, camera, colors):
        sx = int(self.x * RENDER_SCALE - camera.x)
        sy = int(self.y * RENDER_SCALE - camera.y)

        if self.variant == 'world_map_sign':
            self._draw_sign(screen, sx, sy)
            return

        # Always draw the sprite / placeholder in the room view.
        # The tile map data (self._wm_tiles) is only used by the Mode7 renderer,
        # never painted directly into the room.
        w = int(self.width  * RENDER_SCALE)
        h = int(self.height * RENDER_SCALE)
        if self.sprite:
            scaled = self._get_scaled_sprite(w, h)
            screen.blit(scaled, scaled.get_rect(center=(sx, sy)))
        else:
            color = (139, 90, 43)
            pygame.draw.rect(screen, color,
                             pygame.Rect(sx - w // 2, sy - h // 2, w, h))

    def _draw_sign(self, screen, sx, sy):
        w = int(self.width  * RENDER_SCALE)
        h = int(self.height * RENDER_SCALE)
        if self.sprite:
            scaled = self._get_scaled_sprite(w, h)
            screen.blit(scaled, scaled.get_rect(midbottom=(sx, sy)))
        else:
            pygame.draw.rect(screen, (101, 67, 33),
                             pygame.Rect(sx - w // 2, sy - h, w, h))

    def _draw_tiles(self, screen, center_sx, center_sy):
        """Render only the tiles that intersect the current screen viewport."""
        tile_px  = _NATIVE_TILE * RENDER_SCALE
        sw, sh   = screen.get_size()

        # Top-left screen coordinate of tile (0, 0), matching the center anchor
        # used when the map was a single sprite.
        origin_x = center_sx - (_MAP_TILE_W * tile_px) // 2
        origin_y = center_sy - (_MAP_TILE_H * tile_px) // 2

        # Cull to on-screen tile range
        tx_min = max(0,          (-origin_x)          // tile_px)
        ty_min = max(0,          (-origin_y)          // tile_px)
        tx_max = min(_MAP_TILE_W, (sw - origin_x)     // tile_px + 1)
        ty_max = min(_MAP_TILE_H, (sh - origin_y)     // tile_px + 1)

        for ty in range(ty_min, ty_max + 1):
            screen_y = origin_y + ty * tile_px
            for tx in range(tx_min, tx_max + 1):
                td = self._wm_tiles.get((tx, ty))
                if td is None:
                    continue
                ts = self._tilesets.get(td['ts'])
                if ts is None:
                    continue
                surf = ts.get_tile(td['tx'], td['ty'], tile_px)
                if surf:
                    screen.blit(surf, (origin_x + tx * tile_px, screen_y))

    def to_dict(self):
        return {'type': 'world_map_object', 'variant': self.variant,
                'x': self.x, 'y': self.y, 'map_name': self.map_name,
                'entity_name': self.entity_name}

    @staticmethod
    def from_dict(data):
        return WorldMapObject(data.get('x', 0), data.get('y', 0),
                              data.get('variant', 'world_map'),
                              data.get('map_name', ''),
                              data.get('entity_name', ''))


class WorldMapObjectManager:
    def __init__(self):
        self._objects: dict = {}

    def get_objects(self, room_name):
        return self._objects.get(room_name, [])

    def add_object(self, room_name, obj):
        self._objects.setdefault(room_name, []).append(obj)

    def remove_object(self, room_name, obj):
        room = self._objects.get(room_name, [])
        if obj in room:
            room.remove(obj)

    def clear_room(self, room_name):
        self._objects[room_name] = []

    def save_to_dict(self):
        return {room: [o.to_dict() for o in objs]
                for room, objs in self._objects.items()}

    def load_from_dict(self, data):
        self._objects = {
            room: [WorldMapObject.from_dict(o) for o in objs]
            for room, objs in data.items()
        }