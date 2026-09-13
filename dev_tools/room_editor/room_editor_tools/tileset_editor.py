import pygame
import pygame.gfxdraw
import os
import json
import math
from typing import List, Tuple, Optional, Set
from enum import Enum
from config.settings import RENDER_SCALE, TILE_SIZE


class TileType(Enum):
    GROUND = 0
    CLIFF = 1
    WATER = 2
    DECOR = 3


class TileDef:
    def __init__(self, tile_type, autotile=False, solid=False):
        self.tile_type = tile_type
        self.autotile = autotile
        self.solid = solid


def detect_tile_size(image: pygame.Surface, image_path: str = None) -> int:
    """Infer tile size for a tileset sheet.

    Dimension-only guessing is ambiguous once tiles get bigger than 16px:
    any 64px-tile sheet's width/height is also a clean multiple of 16, so
    a plain "does it divide evenly" check can't tell a 64x64 tileset from
    a 16x16 one. To resolve that, a filename ending in "_<size>" (e.g.
    "dirt_64.png", "water_32.png") is checked first and wins outright.
    With no such suffix, we fall back to the old 16-then-8 guess, which
    keeps every existing tileset loading exactly as before.
    """
    if image_path:
        stem = os.path.splitext(os.path.basename(image_path))[0]
        tail = stem.rsplit('_', 1)[-1]
        if tail.isdigit():
            hinted = int(tail)
            if hinted > 0:
                return hinted

    w, h = image.get_size()
    for size in (16, 8):
        if w % size == 0 and h % size == 0:
            return size
    raise ValueError("Could not auto-detect tile size")


class Tile:
    """A single placed tile — tracks position, tileset source, and render layer."""

    def __init__(self, x: int, y: int, tileset_name: str, tile_x: int, tile_y: int,
                 layer: int = -100, foreground: bool = False,
                 is_shadow: bool = False, shadow_alpha: int = 128,
                 shadow_width: int = TILE_SIZE, shadow_height: int = TILE_SIZE):
        self.x = x
        self.y = y
        self.tileset_name = tileset_name
        self.tile_x = tile_x
        self.tile_y = tile_y
        self.layer = layer
        self.foreground = foreground
        self.draw_layer = layer

        # Native shadow tile: texture-free translucent overlay data.
        self.is_shadow = bool(is_shadow)
        self.shadow_alpha = max(0, min(255, int(shadow_alpha)))
        self.shadow_width = max(1, int(shadow_width))
        self.shadow_height = max(1, int(shadow_height))

    def to_dict(self):
        """Pack tile data into a dict for JSON serialization."""
        data = {
            'x': self.x,
            'y': self.y,
            'tileset': self.tileset_name,
            'tile_x': self.tile_x,
            'tile_y': self.tile_y,
            'layer': self.layer,
            'foreground': self.foreground
        }
        if self.is_shadow:
            data.update({
                'is_shadow': True,
                'shadow_alpha': self.shadow_alpha,
                'shadow_width': self.shadow_width,
                'shadow_height': self.shadow_height,
            })
        return data

    @staticmethod
    def from_dict(data):
        """Reconstruct a Tile from saved dict data."""
        layer = data.get('layer', 75 if data.get('foreground', False) else -100)
        return Tile(
            data['x'],
            data['y'],
            data.get('tileset') or data.get('tileset_name', ''),
            data.get('tile_x', -1),
            data.get('tile_y', -1),
            layer,
            data.get('foreground', False),
            data.get('is_shadow', False),
            data.get('shadow_alpha', 128),
            data.get('shadow_width', TILE_SIZE),
            data.get('shadow_height', TILE_SIZE),
        )


class Tileset:
    def __init__(self, name: str, image_path: str):
        self.name = name
        self.image_path = image_path
        self.image = None
        self.tile_width = 16
        self.tile_height = 16
        self.cols = 0
        self.rows = 0
        self._tile_transparency_cache = {}
        # key: (tile_x, tile_y) → True if every pixel is fully opaque (alpha == 255).
        # Lets get_scaled_tile_surface() hand out a non-alpha Surface for these tiles,
        # which pygame blits with its fast opaque path instead of per-pixel alpha
        # blending — a big win for animated tiles (water/lava/etc.) that get blitted
        # individually every frame instead of once into a baked static surface.
        self._tile_opaque_cache = {}
        self._scaled_cache = {}  # key: (tile_x, tile_y, scale) → pre-scaled Surface
        self._dimmed_cache = {}  # key: (tile_x, tile_y, scale, alpha) → pre-scaled, alpha-reduced Surface

        # (tile_x, tile_y) anchor -> {'frames': [(tx,ty), ...], 'fps': float}
        # The anchor is whatever coordinate is painted into the room; 'frames'
        # is the sequence cycled through at runtime (anchor is usually frames[0]).
        self.tile_animations = {}

        # Set of (tile_x, tile_y) tileset coordinates marked "solid" (blocks
        # movement) in the tileset editor's collision paint mode. Any room
        # tile painted from one of these coordinates gets an automatic
        # full-tile CollisionObject generated for it -- see
        # RoomEditor._sync_tile_collision. Purely tileset-graphic-level data;
        # has no notion of which rooms use it.
        #
        # Used when collision_granularity == 16 (the default, and the only
        # option for an 8px tileset — there's nothing finer to subdivide
        # into). For collision_granularity == 8 on a 16px tileset, per-tile
        # solidity lives in solid_subtiles instead, at quarter-tile
        # precision — see that attribute and is_tile_solid()/
        # set_tile_solid() below for how the two stay in sync.
        self.solid_tiles = set()

        # Collision editing granularity for this tileset, in pixels: either
        # 16 (one solid flag per whole tile — the original/default
        # behavior) or 8 (one solid flag per 8x8 quadrant of each tile,
        # letting a single 16x16 tile carry partial collision, e.g. just
        # its bottom-right corner for a rounded ledge). Only meaningful
        # when tile_width == 16; toggled via the tileset editor's 'H' key
        # (see TilesetEditor._toggle_collision_granularity).
        self.collision_granularity = 16

        # Set of (tile_x, tile_y, sub_x, sub_y) tileset+quadrant coordinates
        # marked solid when collision_granularity == 8. sub_x/sub_y are each
        # 0 or 1, selecting which 8x8 quadrant of the 16x16 tile (tile_x,
        # tile_y) — (0,0) top-left, (1,0) top-right, (0,1) bottom-left,
        # (1,1) bottom-right. Empty (and unused) while
        # collision_granularity == 16.
        self.solid_subtiles = set()

        try:
            self.image = pygame.image.load(image_path).convert_alpha()
            self.tile_width = detect_tile_size(self.image, image_path)
            self.tile_height = self.tile_width
            w, h = self.image.get_size()
            self.cols = w // self.tile_width
            self.rows = h // self.tile_height
            self._build_transparency_cache()
            self._load_tile_animations(image_path)
            self._load_tile_collision(image_path)
        except (pygame.error, ValueError) as e:
            print(f"Error loading tileset {name}: {e}")

    def _load_tile_animations(self, image_path):
        """Load optional animated-tile definitions from a sidecar JSON file.

        Looked up next to the tileset image as '<name>.anim.json'. Format:
            {
              "animations": [
                {"anchor": [5, 3], "frames": [[5,3],[6,3],[7,3],[8,3]], "fps": 6}
              ]
            }
        'anchor' is the tile coordinate that appears in the palette and gets
        painted into rooms as a normal Tile. 'frames' is the coordinate
        sequence cycled through at runtime (all frames must live in this same
        tileset image). Missing/malformed files are silently ignored so a
        tileset with no animations behaves exactly as before.
        """
        anim_path = os.path.splitext(image_path)[0] + '.anim.json'
        if not os.path.exists(anim_path):
            return

        try:
            with open(anim_path, 'r') as f:
                data = json.load(f)
            for entry in data.get('animations', []):
                anchor = tuple(entry['anchor'])
                frames = [tuple(fr) for fr in entry.get('frames', [])]
                fps = max(0.1, float(entry.get('fps', 6)))
                if frames:
                    self.tile_animations[anchor] = {'frames': frames, 'fps': fps}
        except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
            print(f"Error loading tile animations for {self.name}: {e}")

    def _load_tile_collision(self, image_path):
        """Load which tiles are marked solid from a sidecar JSON file.

        Looked up next to the tileset image as '<name>.collision.json'. Format:
            {
              "granularity": 8,
              "solid": [[3, 1], [3, 2], [4, 1]],
              "solid_subtiles": [[5, 0, 1, 0], [5, 0, 1, 1]]
            }
        "granularity" defaults to 16 (whole-tile) when absent, so existing
        collision.json files written before 8x8 collision existed keep
        loading exactly as before. "solid" is whole-tile entries (used at
        granularity 16); "solid_subtiles" is (tile_x, tile_y, sub_x, sub_y)
        quadrant entries (used at granularity 8) — see solid_subtiles'
        docstring above for what sub_x/sub_y mean. Missing/malformed files
        are silently ignored so a tileset with no collision data defined
        behaves exactly as before (nothing is solid).
        """
        collision_path = os.path.splitext(image_path)[0] + '.collision.json'
        if not os.path.exists(collision_path):
            return

        try:
            with open(collision_path, 'r') as f:
                data = json.load(f)
            self.solid_tiles = {tuple(coord) for coord in data.get('solid', [])}
            granularity = data.get('granularity', 16)
            self.collision_granularity = 8 if granularity == 8 and self.tile_width == 16 else 16
            self.solid_subtiles = {tuple(coord) for coord in data.get('solid_subtiles', [])}
        except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
            print(f"Error loading tile collision for {self.name}: {e}")

    def is_tile_solid(self, tile_x, tile_y):
        """True if (tile_x, tile_y) blocks movement.

        At collision_granularity 16 this is just membership in
        solid_tiles. At granularity 8 there's no single flag for the whole
        tile — this reports True only when every one of its four 8x8
        quadrants is solid, so a caller that only understands whole-tile
        collision (a simple "is this tile solid" check) still gets a sane
        answer for a fully-solid tile, and doesn't overclaim solidity for
        one that's only partially solid. Callers that need the actual
        partial shape should use is_subtile_solid()/get_solid_rects_px()
        instead.
        """
        if self.collision_granularity == 8:
            return all(self.is_subtile_solid(tile_x, tile_y, sx, sy)
                       for sx in (0, 1) for sy in (0, 1))
        return (tile_x, tile_y) in self.solid_tiles

    def is_subtile_solid(self, tile_x, tile_y, sub_x, sub_y):
        """True if the (sub_x, sub_y) 8x8 quadrant of tile (tile_x, tile_y)
        blocks movement. At collision_granularity 16 (or for an 8px
        tileset, where there's only one quadrant) this just defers to
        is_tile_solid() — the whole tile is the only unit that exists.
        """
        if self.collision_granularity == 8:
            return (tile_x, tile_y, sub_x, sub_y) in self.solid_subtiles
        return (tile_x, tile_y) in self.solid_tiles

    def set_tile_solid(self, tile_x, tile_y, solid):
        """Set (or clear) the solid flag for one tile and persist immediately.

        At collision_granularity 8, this sets all four quadrants together —
        i.e. "mark this whole tile solid/non-solid" still works exactly as
        a single action even in 8x8 mode; use set_subtile_solid() to target
        just one quadrant.
        """
        if self.collision_granularity == 8:
            for sx in (0, 1):
                for sy in (0, 1):
                    self._set_subtile_solid_no_save(tile_x, tile_y, sx, sy, solid)
        else:
            key = (tile_x, tile_y)
            if solid:
                self.solid_tiles.add(key)
            else:
                self.solid_tiles.discard(key)
        self.save_tile_collision()

    def _set_subtile_solid_no_save(self, tile_x, tile_y, sub_x, sub_y, solid):
        """set_subtile_solid()'s actual mutation, without the save — shared
        by set_subtile_solid() and set_tile_solid()'s all-four-quadrants
        case above so toggling a whole tile doesn't write the sidecar file
        four times in a row."""
        key = (tile_x, tile_y, sub_x, sub_y)
        if solid:
            self.solid_subtiles.add(key)
        else:
            self.solid_subtiles.discard(key)

    def set_subtile_solid(self, tile_x, tile_y, sub_x, sub_y, solid):
        """Set (or clear) the solid flag for a single 8x8 quadrant and
        persist immediately. Only meaningful at collision_granularity 8 —
        see set_collision_granularity() to switch a 16px tileset into that
        mode first."""
        self._set_subtile_solid_no_save(tile_x, tile_y, sub_x, sub_y, solid)
        self.save_tile_collision()

    def get_solid_rects_px(self, tile_x, tile_y):
        """Return this tile's solid area(s) as a list of tile-local pixel
        rects [(px, py, w, h), ...], (0, 0) being the tile's top-left
        corner. Empty list means the tile has no collision at all.

        This is what a consumer that needs the *actual shape* (e.g.
        RoomEditor._sync_tile_collision building CollisionObjects for a
        placed tile) should use instead of is_tile_solid() — at
        collision_granularity 8 a tile can be solid in just one or two of
        its four quadrants, and is_tile_solid() alone can't express that.
        """
        if self.collision_granularity == 8:
            return [
                (sx * 8, sy * 8, 8, 8)
                for sx in (0, 1) for sy in (0, 1)
                if self.is_subtile_solid(tile_x, tile_y, sx, sy)
            ]
        return [(0, 0, self.tile_width, self.tile_height)] if self.is_tile_solid(tile_x, tile_y) else []

    def set_collision_granularity(self, size):
        """Switch this tileset's collision editing granularity between 16
        (whole-tile) and 8 (quarter-tile), converting existing data so
        nothing is silently lost or overclaimed:

          16 -> 8: every currently-solid whole tile becomes solid in all
                   four of its quadrants — identical collision footprint,
                   just re-expressed at the finer granularity.
          8 -> 16: a tile becomes solid only if ALL FOUR of its quadrants
                   already were — a tile that was only partially solid
                   (e.g. just one corner) loses that partial collision
                   rather than have it silently promoted to full-tile
                   solid, which would make the room's collision more
                   restrictive than what was actually authored.

        No-op on an 8px tileset (tile_width != 16) — there's nothing finer
        than the whole tile to subdivide into, so size 8 there would be
        indistinguishable from size 16 anyway.
        """
        size = 8 if size == 8 else 16
        if self.tile_width != 16 or size == self.collision_granularity:
            return

        if size == 8:
            for (tx, ty) in self.solid_tiles:
                for sx in (0, 1):
                    for sy in (0, 1):
                        self.solid_subtiles.add((tx, ty, sx, sy))
            self.solid_tiles = set()
        else:
            tiles_covered = {(tx, ty) for (tx, ty, sx, sy) in self.solid_subtiles}
            self.solid_tiles = {
                (tx, ty) for (tx, ty) in tiles_covered
                if all((tx, ty, sx, sy) in self.solid_subtiles for sx in (0, 1) for sy in (0, 1))
            }
            self.solid_subtiles = set()

        self.collision_granularity = size
        self.save_tile_collision()

    def save_tile_collision(self):
        """Write self.solid_tiles/solid_subtiles back out to the
        '<name>.collision.json' sidecar.

        Overwrites the whole file with the current in-memory state, same
        convention as save_tile_animations -- the palette UI is the single
        source of truth, no manual JSON editing required.
        """
        if not self.image_path:
            return
        collision_path = os.path.splitext(self.image_path)[0] + '.collision.json'
        data = {
            'granularity': self.collision_granularity,
            'solid': [list(coord) for coord in sorted(self.solid_tiles)],
            'solid_subtiles': [list(coord) for coord in sorted(self.solid_subtiles)],
        }
        try:
            with open(collision_path, 'w') as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            print(f"Error saving tile collision for {self.name}: {e}")

    def get_animated_coords(self, tile_x, tile_y, tick_ms):
        """Resolve (tile_x, tile_y) to its current animation frame, if animated.

        tick_ms should be a shared clock (e.g. pygame.time.get_ticks()) so every
        placed instance of the same animated tile stays in sync with the others.
        Tiles with no animation defined are returned unchanged.
        """
        anim = self.tile_animations.get((tile_x, tile_y))
        if not anim:
            return tile_x, tile_y
        frames = anim['frames']
        frame_idx = int(tick_ms * anim['fps'] / 1000) % len(frames)
        return frames[frame_idx]

    def is_tile_animated(self, tile_x, tile_y):
        """True if (tile_x, tile_y) is the anchor frame of an animation."""
        return (tile_x, tile_y) in self.tile_animations

    def set_animation(self, anchor, frames, fps):
        """Register (or replace) an animation and immediately persist it to disk.

        Called by the palette's 'mark selection as animated' action — the
        person never touches the JSON file directly.
        """
        self.tile_animations[tuple(anchor)] = {
            'frames': [tuple(f) for f in frames],
            'fps': max(0.1, float(fps)),
        }
        self.save_tile_animations()

    def remove_animation(self, anchor):
        """Un-mark an animation and persist the change to disk."""
        self.tile_animations.pop(tuple(anchor), None)
        self.save_tile_animations()

    def save_tile_animations(self):
        """Write self.tile_animations back out to the '<name>.anim.json' sidecar.

        Overwrites the whole file with the current in-memory state, so the
        palette UI is always the single source of truth — no manual JSON
        editing required.
        """
        if not self.image_path:
            return
        anim_path = os.path.splitext(self.image_path)[0] + '.anim.json'
        data = {
            'animations': [
                {'anchor': list(anchor), 'frames': [list(f) for f in info['frames']], 'fps': info['fps']}
                for anchor, info in self.tile_animations.items()
            ]
        }
        try:
            with open(anim_path, 'w') as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            print(f"Error saving tile animations for {self.name}: {e}")

    def _build_transparency_cache(self):
        """Pre-scan every tile once and record both whether it's fully transparent
        (skips empty blits at draw time) and fully opaque (lets animated-tile
        rendering use a faster non-alpha blit — see _tile_opaque_cache above).
        One pixel pass per tile does both checks instead of two separate scans.
        """
        if not self.image:
            return

        for ty in range(self.rows):
            for tx in range(self.cols):
                tile_surface = self.get_tile_surface(tx, ty)
                if tile_surface:
                    transparent, opaque = self._scan_tile_alpha(tile_surface)
                    self._tile_transparency_cache[(tx, ty)] = transparent
                    self._tile_opaque_cache[(tx, ty)] = opaque

    def _scan_tile_alpha(self, surface: pygame.Surface) -> Tuple[bool, bool]:
        """Single pixel pass returning (is fully transparent, is fully opaque).

        A tile with no alpha channel at all can't be transparent and is
        always opaque. Otherwise scans pixels, bailing out early the moment
        neither result could still hold (most tiles resolve this almost
        immediately since mixed-alpha edge pixels tend to show up early).
        """
        if not (surface.get_flags() & pygame.SRCALPHA):
            return False, True

        all_transparent = True
        all_opaque = True
        width, height = surface.get_size()
        for y in range(height):
            for x in range(width):
                a = surface.get_at((x, y))[3]
                if a != 0:
                    all_transparent = False
                if a != 255:
                    all_opaque = False
                if not all_transparent and not all_opaque:
                    return False, False
        return all_transparent, all_opaque

    def _is_tile_transparent(self, surface: pygame.Surface) -> bool:
        """Returns True if every pixel has alpha == 0."""
        transparent, _ = self._scan_tile_alpha(surface)
        return transparent

    def is_tile_empty(self, tile_x: int, tile_y: int) -> bool:
        """Returns True if the tile at (tile_x, tile_y) was fully transparent when the cache was built."""
        return self._tile_transparency_cache.get((tile_x, tile_y), False)

    def get_tile_surface(self, tile_x: int, tile_y: int) -> Optional[pygame.Surface]:
        """Extract and return a copy of the tile surface at grid coords (tile_x, tile_y)."""
        if not self.image or tile_x >= self.cols or tile_y >= self.rows or tile_x < 0 or tile_y < 0:
            return None

        rect = pygame.Rect(
            tile_x * self.tile_width,
            tile_y * self.tile_height,
            self.tile_width,
            self.tile_height
        )
        return self.image.subsurface(rect).copy()

    def get_scaled_tile_surface(self, tile_x: int, tile_y: int, scale: int) -> Optional[pygame.Surface]:
        """Return a pre-scaled tile surface, creating and caching it on first call.

        This avoids calling pygame.transform.scale() and subsurface().copy() on
        every tile every frame — the heavy work happens once and is reused.

        Tiles that are fully opaque (see _tile_opaque_cache) are converted with
        convert() instead of kept as convert_alpha(). Every placed tile still
        looks identical since there's no transparency to lose, but blitting a
        non-alpha Surface skips pygame's per-pixel alpha-blend path in favour of
        a straight copy — which matters most here for animated tiles, which are
        blitted individually every frame rather than baked once into a static
        surface like everything else.
        """
        key = (tile_x, tile_y, scale)
        if key not in self._scaled_cache:
            raw = self.get_tile_surface(tile_x, tile_y)
            if raw is None:
                self._scaled_cache[key] = None
            else:
                scaled = pygame.transform.scale(
                    raw, (self.tile_width * scale, self.tile_height * scale)
                )
                if self._tile_opaque_cache.get((tile_x, tile_y)):
                    try:
                        scaled = scaled.convert()
                    except pygame.error:
                        pass  # no display surface yet (e.g. headless tooling) — keep the alpha version
                self._scaled_cache[key] = scaled
        return self._scaled_cache[key]

    def invalidate_scaled_cache(self):
        """Clear the scaled surface cache (call if the tileset image is replaced at runtime)."""
        self._scaled_cache.clear()
        self._dimmed_cache.clear()

    def get_dimmed_tile_surface(self, tile_x: int, tile_y: int, scale: int, alpha: int) -> Optional[pygame.Surface]:
        """Return a pre-scaled tile surface at reduced alpha, creating and caching it on first call.

        Built from the same cached scaled surface as get_scaled_tile_surface, so the only
        extra per-tile-type cost is a one-time copy() + set_alpha(); nothing is redone per frame.
        """
        key = (tile_x, tile_y, scale, alpha)
        if key not in self._dimmed_cache:
            base = self.get_scaled_tile_surface(tile_x, tile_y, scale)
            if base is None:
                self._dimmed_cache[key] = None
            else:
                dimmed = base.copy()
                dimmed.set_alpha(alpha)
                self._dimmed_cache[key] = dimmed
        return self._dimmed_cache[key]


class TilesetManager:
    """Holds every loaded tileset and hands them out by name."""

    def __init__(self):
        self.tilesets: dict[str, Tileset] = {}
        self.tileset_names: List[str] = []

    def load_tilesets_from_folder(self, folder_path: str):
        """Scan a folder and load every .png file as a tileset."""
        if not os.path.exists(folder_path):
            print(f"Tileset folder not found: {folder_path}")
            return

        for file_name in os.listdir(folder_path):
            if file_name.lower().endswith('.png'):
                name = os.path.splitext(file_name)[0]
                path = os.path.join(folder_path, file_name)
                self.load_tileset(name, path)

    def load_default_tilesets(self):
        """Load tilesets from the default assets/tilesets/ folder. Falls back to a placeholder if none are found."""
        default_path = "assets/tilesets/"
        self.load_tilesets_from_folder(default_path)

        if not self.tilesets:
            placeholder = Tileset("placeholder", "")
            self.tilesets["placeholder"] = placeholder
            self.tileset_names.append("placeholder")

    def load_tileset(self, name: str, image_path: str):
        """Load a single tileset by name and file path."""
        tileset = Tileset(name, image_path)
        self.tilesets[name] = tileset
        if name not in self.tileset_names:
            self.tileset_names.append(name)

    def get_tileset(self, name: str) -> Optional[Tileset]:
        """Return the named tileset, or None if it hasn't been loaded."""
        return self.tilesets.get(name)


class TilesetEditor:
    """Tileset palette and tile-painting editor."""

    # Alpha (0-255) used to dim tiles on layers other than the active editing layer.
    # Always on while the tile editor is active — the active layer itself is always
    # drawn at full opacity.
    INACTIVE_LAYER_ALPHA = 90

    LAYER_PRESETS = [
        ("Ground", -100),
        ("Floor Decor", -75),
        ("Shadows", -50),
        ("Background", -25),
        ("Base", 0),
        ("Foreground", 75),
        ("Top", 100),
        ("Custom...", None)
    ]

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width = screen_width
        self.screen_height = screen_height

        # Continuous editor zoom (Ctrl+scroll), kept in sync by RoomEditor
        # each frame. Click/drag placement gets its position pre-converted
        # by RoomEditor._zoom_adjust_event(), but anything here that reads
        # the live cursor directly (draw_tile_preview, the Delete/X-at-
        # cursor shortcut) has to do that conversion itself, using this.
        self.editor_zoom = 1.0

        self.tileset_manager = TilesetManager()
        self.tileset_manager.load_default_tilesets()

        self.palette_scroll_x = 0
        self.palette_scroll_y = 0

        self.active = False
        self.current_tileset_index = 0
        self.selected_tile_x = 0
        self.selected_tile_y = 0

        self.selection_start_x = 0
        self.selection_start_y = 0
        self.selection_end_x = 0
        self.selection_end_y = 0
        self.is_multi_selecting = False

        # ── Layer state ──────────────────────────────────────────────────────
        self.current_layer_preset_index = 0
        self.current_layer = -100
        self.hidden_layers = set()  # layer values fully hidden via the per-layer checkbox
        self.custom_layer_value = -100
        self.delete_underlying = True
        self.layer_dropdown_open = False
        self.layer_input_active = False
        self.layer_input_text = ""

        self.foreground_mode = False

        # Native shadow brush is independent of the layer value.
        # Enable it explicitly; the current layer still determines the shadow's
        # z-order, so a shadow can live on Ground, Base, Foreground, Top, etc.
        self.native_shadow_enabled = False
        self.native_shadow_alpha = 128
        self.native_shadow_cell_w = TILE_SIZE
        self.native_shadow_cell_h = TILE_SIZE

        self._last_stroke_cell = None  # (grid_x, grid_y) of last placed/erased cell

        # ── Animated tile authoring (palette: select frames, press A) ──────────
        self.fps_input_active = False
        self.fps_input_text = "6"
        self._pending_anim_anchor = None
        self._pending_anim_frames = None
        self.anim_feedback_text = ""      # brief on-screen confirmation, e.g. "Animated: 4 frames @ 6fps"
        self.anim_feedback_until_ms = 0

        # ── Palette geometry ─────────────────────────────────────────────────
        self.palette_width = 600
        self.palette_x = screen_width - self.palette_width
        self.palette_y = 100
        self.palette_height = 940
        self.palette_content_height = 500
        self.tileset_area_y_offset = 35

        self.grid_cell_size = 32
        self.show_grid = True

        self.font_large = pygame.font.Font(None, 32)
        self.font_medium = pygame.font.Font(None, 24)
        self.font_small = pygame.font.Font(None, 18)

        self.colors = {
            'bg': (20, 20, 30),
            'panel': (30, 30, 45),
            'panel_light': (45, 45, 65),
            'accent': (255, 215, 0),
            'selection': (100, 150, 255),
            'text': (255, 255, 255),
            'text_dim': (180, 180, 200),
            'success': (100, 255, 100),
            'danger': (255, 100, 100),
            'grid': (44, 149, 44, 80),
            'grid_dim': (44, 149, 44, 30),
            'button': (60, 60, 80),
            'button_hover': (80, 80, 100),
            'checkbox': (100, 100, 120)
        }

        self.grid_size = 16
        # The anchor-snap grid used when deciding *where* a stamp/erase
        # lands — this is the one driven by the room editor toolbar's Grid
        # control (Off / 8px / 16px), kept in sync via set_snap_size().
        # 0 means "no grid": stamps land at the exact (unsnapped) mouse
        # position instead of the nearest grid_size multiple.
        # (Multi-cell pattern spacing is unrelated to this — it always uses
        # the active tileset's own tile_width/tile_height, read directly
        # from the tileset in _place_tiles/draw_tile_preview, so it's
        # correct for 8px, 16px, 32px, etc. tilesets automatically.)
        self.snap_size = self.grid_size
        self.room_tiles: dict[str, List[Tile]] = {}

        # Extra subscribers notified whenever notify_tile_changed() fires,
        # alongside (not instead of) the single external on_tile_changed
        # hook game.py installs for baked-surface cache invalidation. Lets
        # the room editor keep tile-derived collision in sync without
        # fighting over who owns on_tile_changed. See notify_tile_changed()
        # and add_tile_change_listener().
        self._tile_change_listeners = []

        # Cache of room_tiles sorted by layer, keyed by room name. draw_tiles()
        # is called twice per frame (background + foreground passes) and used
        # to re-sort the *entire* tile list both times, every frame, even
        # though tile layers only change on edits. That's a full O(n log n)
        # sort paid twice per frame for nothing — this cache is rebuilt only
        # when the room's tiles are actually mutated (see
        # _invalidate_sorted_tiles_cache, called from every place that adds,
        # removes, or reloads room_tiles).
        self._sorted_tiles_cache: dict[str, List[Tile]] = {}

        # Bumped by _invalidate_sorted_tiles_cache (i.e. on every paint/erase/
        # reload) so the background-pass frame cache below knows when the
        # tile data underneath it has actually changed, as opposed to just
        # being redrawn identically frame after frame.
        self._tile_content_generation = 0

        # ── Painting-mode background frame cache ────────────────────────────
        # draw_tiles() re-blends every visible tile every frame — most of them
        # through get_dimmed_tile_surface(), which is an SRCALPHA surface, so
        # this is a full per-pixel alpha blend pass every frame even when the
        # camera hasn't moved and nothing was edited. That's the same cost
        # profile documented in game.py's blit_room_tiles() PERFORMANCE NOTE,
        # and the fix is the same one applied there: cache the *blended
        # result* keyed on everything that could change it, and reuse it with
        # a plain opaque blit until the key actually changes.
        #
        # Restricted to the background pass only, same reasoning as
        # blit_room_tiles: the foreground pass is interleaved with entities/
        # decorations that move independently every frame, so a full-screen
        # snapshot of it would go stale immediately. The background pass is
        # the first thing drawn each frame, so snapshotting the whole screen
        # after drawing it is safe.
        #
        # Skipped for any frame where an animated tile is actually on
        # screen (see _has_visible_animated_tile) — those keep ticking
        # regardless of camera/tile-data staying put. A room can freely
        # contain animated tiles elsewhere without disabling this; only
        # what's currently visible matters.
        self._paint_bg_frame_cache: dict[str, dict] = {}
        self._animated_tiles_cache: dict[str, List[Tile]] = {}

        self.is_dragging = False
        self.is_erasing = False
        self.drag_start_pos = None
        self.is_palette_dragging = False

        self.ui_rects = {}

        # Panel show/hide toggle (same pattern as EditorToolbar)
        self.palette_visible = True
        self._panel_tab_w = 18
        self._panel_tab_h = 72
        self._hover_panel_toggle = False

    def toggle(self):
        """Open or close the tileset editor."""
        self.active = not self.active

    def set_snap_size(self, size: int):
        """Set the placement-snap grid used for stamp/erase anchoring (0 =
        off / pixel-precise, otherwise the snap size in world pixels).
        Called by the room editor each frame to mirror the toolbar's quick
        Grid control. Does not affect the native tileset pitch used to
        space multi-cell patterns — that always stays at the sprite size
        so painted tiles keep tiling correctly."""
        self.snap_size = max(0, int(size))

    # -------------------------------------------------------------------------
    # Panel show/hide tab
    # -------------------------------------------------------------------------

    def _panel_toggle_rect(self):
        """Return the rect for the ◀/▶ tab that straddles the panel's left edge."""
        gap = 6  # breathing room between tab and panel when panel is visible
        tx = (self.palette_x - self._panel_tab_w - gap) if self.palette_visible else (self.screen_width - self._panel_tab_w)
        ty = self.palette_y + (self.palette_height - self._panel_tab_h) // 2
        return pygame.Rect(tx, ty, self._panel_tab_w, self._panel_tab_h)

    def _draw_panel_toggle_tab(self, screen):
        """Render the small ◀/▶ tab — always visible so the panel can be recalled."""
        rect   = self._panel_toggle_rect()
        bg     = self.colors['button_hover'] if self._hover_panel_toggle else self.colors['button']
        border = self.colors['accent']       if self._hover_panel_toggle else (60, 60, 80)
        screen.draw_rect( bg,     rect, border_radius=6)
        screen.draw_rect( border, rect, 1, border_radius=6)
        arrow = '◀' if self.palette_visible else '▶'
        label = self.font_small.render(
            arrow, True,
            self.colors['accent'] if self._hover_panel_toggle else self.colors['text_dim']
        )
        screen.blit(label, label.get_rect(center=rect.center))

    def get_current_tileset(self) -> Optional[Tileset]:
        """Return the active tileset object, or None if the list is empty."""
        names = self.tileset_manager.tileset_names
        if names and 0 <= self.current_tileset_index < len(names):
            return self.tileset_manager.get_tileset(names[self.current_tileset_index])
        return None

    def _get_selection_bounds(self):
        """Normalize selection coords into (min_x, max_x, min_y, max_y)."""
        min_x = min(self.selection_start_x, self.selection_end_x)
        max_x = max(self.selection_start_x, self.selection_end_x)
        min_y = min(self.selection_start_y, self.selection_end_y)
        max_y = max(self.selection_start_y, self.selection_end_y)
        return min_x, max_x, min_y, max_y

    def _set_anim_feedback(self, text, duration_ms=2500):
        """Show a brief on-screen confirmation near the palette selection info."""
        self.anim_feedback_text = text
        self.anim_feedback_until_ms = pygame.time.get_ticks() + duration_ms

    def _toggle_animate_selection(self):
        """'N' in the palette: turn the current multi-tile selection into an
        animation, or remove it if the selection is already animated.

        The anchor (the tile you actually paint into rooms) is always the
        top-left of the selection; the frame order is left-to-right,
        top-to-bottom through the selected rectangle. No JSON editing —
        this writes straight to the tileset and saves the sidecar file.
        """
        tileset = self.get_current_tileset()
        if not tileset:
            return

        min_x, max_x, min_y, max_y = self._get_selection_bounds()
        anchor = (min_x, min_y)

        # Already animated — remove it and stop here.
        if tileset.is_tile_animated(*anchor):
            tileset.remove_animation(anchor)
            self._set_anim_feedback("Animation removed")
            return

        frames = [
            (tx, ty)
            for ty in range(min_y, max_y + 1)
            for tx in range(min_x, max_x + 1)
            if not tileset.is_tile_empty(tx, ty)
        ]

        if len(frames) < 2:
            self._set_anim_feedback("Select 2+ tiles first, then press A")
            return

        # Ask for playback speed via the same inline text-entry pattern used
        # for custom layer values — defaults to 6 so pressing Enter just works.
        self._pending_anim_anchor = anchor
        self._pending_anim_frames = frames
        self.fps_input_active = True
        self.fps_input_text = "6"

    def _confirm_pending_animation(self, fps):
        """Finish creating the pending animation once FPS is confirmed."""
        tileset = self.get_current_tileset()
        if not tileset or not self._pending_anim_anchor or not self._pending_anim_frames:
            self._pending_anim_anchor = None
            self._pending_anim_frames = None
            return

        tileset.set_animation(self._pending_anim_anchor, self._pending_anim_frames, fps)
        self._set_anim_feedback(f"Animated: {len(self._pending_anim_frames)} frames @ {fps:g}fps")
        self._pending_anim_anchor = None
        self._pending_anim_frames = None

    def add_tile_change_listener(self, fn):
        """Register an extra callback for notify_tile_changed() (see below),
        without disturbing the single on_tile_changed hook game.py owns.
        `fn` is called as fn(room_name, cells) — room_name may be None for
        a global change (e.g. a tile's solid flag changed, which can't be
        pinned to one room)."""
        self._tile_change_listeners.append(fn)

    def notify_tile_changed(self, room_name=None, cells=None):
        """Central dispatch for 'tiles changed' notifications.

        Every place that mutates room_tiles — this file's own paint/erase/
        native-shadow code, and room_editor.py's undo/redo, copy-paste, and
        quick-delete paths — funnels through here instead of calling
        on_tile_changed directly, so a single change fans out to: (1) the
        external on_tile_changed hook game.py installs to invalidate the
        baked room-surface cache, and (2) any listeners registered via
        add_tile_change_listener (the room editor's auto tile-collision
        resync).
        """
        if callable(getattr(self, 'on_tile_changed', None)):
            self.on_tile_changed(room_name, cells=cells)
        for listener in self._tile_change_listeners:
            listener(room_name, cells)

    def _toggle_solid_selection(self):
        """'C' in the palette: toggle the 'solid' (blocks movement) flag for
        every non-empty tile in the current selection, and immediately
        resync whichever room is open so the effect is visible without
        needing to repaint anything.

        A mixed selection (some solid, some not) is set fully solid on the
        first press; a further press clears all of them — same one-key
        toggle feel as animation's 'N'.

        Single-tile selection on a tileset currently in 8x8 collision mode
        (see _toggle_collision_granularity) is a special case: instead of
        toggling the whole tile, this targets just the 8x8 quadrant the
        mouse is hovering over, so a 16x16 tile can carry partial collision
        (e.g. only its bottom-right corner). Point at the quadrant you want
        and press C — no separate paint mode needed. A multi-tile selection
        always toggles whole tiles (all four quadrants together), since a
        single keypress can't sensibly express an arbitrary per-quadrant
        pattern across several tiles at once.
        """
        tileset = self.get_current_tileset()
        if not tileset:
            return

        min_x, max_x, min_y, max_y = self._get_selection_bounds()
        cells = [
            (tx, ty)
            for ty in range(min_y, max_y + 1)
            for tx in range(min_x, max_x + 1)
            if not tileset.is_tile_empty(tx, ty)
        ]
        if not cells:
            return

        if len(cells) == 1 and tileset.collision_granularity == 8:
            tx, ty = cells[0]
            sub = self._hovered_subtile(tx, ty)
            if sub is not None:
                sub_x, sub_y = sub
                make_solid = not tileset.is_subtile_solid(tx, ty, sub_x, sub_y)
                tileset.set_subtile_solid(tx, ty, sub_x, sub_y, make_solid)
                verb = "Marked solid" if make_solid else "Marked non-solid"
                self._set_anim_feedback(f"{verb}: quadrant ({sub_x},{sub_y}) of tile ({tx},{ty})")
                self.notify_tile_changed(None)
                return
            # Mouse isn't over the selected tile (e.g. selection was moved
            # with arrow keys, not the mouse) — fall through to the normal
            # whole-tile toggle below rather than silently doing nothing.

        make_solid = not all(tileset.is_tile_solid(tx, ty) for tx, ty in cells)
        for tx, ty in cells:
            tileset.set_tile_solid(tx, ty, make_solid)

        verb = "Marked solid" if make_solid else "Marked non-solid"
        self._set_anim_feedback(f"{verb}: {len(cells)} tile(s)")

        # Solid state is a tileset-level property, not tied to one room, so
        # there's no single room_name to pass — the room editor's listener
        # resyncs whichever room is currently open.
        self.notify_tile_changed(None)

    def _toggle_collision_granularity(self):
        """'H' in the palette: switch the current tileset's collision
        editing granularity between 16 (whole-tile) and 8 (quarter-tile).
        No-op with a feedback message on an 8px tileset — there's nothing
        finer than the whole tile to subdivide into there. See
        Tileset.set_collision_granularity for the data-conversion rules
        applied when switching.
        """
        tileset = self.get_current_tileset()
        if not tileset:
            return

        if tileset.tile_width != 16:
            self._set_anim_feedback("8x8 collision only applies to 16x16 tilesets")
            return

        new_size = 8 if tileset.collision_granularity == 16 else 16
        tileset.set_collision_granularity(new_size)
        self._set_anim_feedback(f"Collision grid: {new_size}x{new_size}")
        self.notify_tile_changed(None)

    def _palette_to_subtile_coords(self, mouse_x: int, mouse_y: int):
        """Like _palette_to_tile_coords, but also resolves which 8x8
        quadrant (sub_x, sub_y, each 0 or 1) of a 16x16 tile the position
        falls in — used for 8x8-granularity collision painting.

        Returns (tile_x, tile_y, sub_x, sub_y), or None if outside the
        tileset area. sub_x/sub_y are always (0, 0) for an 8px tileset —
        there's only one quadrant, the whole tile. The math works at any
        palette zoom level (self.grid_cell_size) because the quadrant
        boundary is always exactly the midpoint of the cell on screen,
        regardless of how many actual pixels that maps to.
        """
        tileset_x = self.palette_x + 20
        tileset_y = self.palette_y + self.tileset_area_y_offset
        rel_x = mouse_x - tileset_x + self.palette_scroll_x
        rel_y = mouse_y - tileset_y + self.palette_scroll_y

        if rel_x < 0 or rel_y < 0:
            return None

        tile_x = int(rel_x // self.grid_cell_size)
        tile_y = int(rel_y // self.grid_cell_size)
        half = self.grid_cell_size / 2
        sub_x = 1 if (rel_x % self.grid_cell_size) >= half else 0
        sub_y = 1 if (rel_y % self.grid_cell_size) >= half else 0
        return tile_x, tile_y, sub_x, sub_y

    def _hovered_subtile(self, tile_x, tile_y):
        """Return (sub_x, sub_y) for whichever 8x8 quadrant of tile
        (tile_x, tile_y) the mouse currently sits over in the palette, or
        None if the mouse isn't over that tile at all (outside the palette
        entirely, or hovering a different tile)."""
        mouse_x, mouse_y = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        if not self._is_in_palette(mouse_x, mouse_y):
            return None
        coords = self._palette_to_subtile_coords(mouse_x, mouse_y)
        if coords is None:
            return None
        tx, ty, sub_x, sub_y = coords
        if (tx, ty) != (tile_x, tile_y):
            return None
        return sub_x, sub_y

    def _is_in_palette(self, mouse_x: int, mouse_y: int) -> bool:
        """Returns True when the mouse is over the palette panel."""
        if not self.palette_visible:
            return False
        return (self.palette_x <= mouse_x < self.palette_x + self.palette_width
                and self.palette_y <= mouse_y < self.palette_y + self.palette_height)

    def _is_in_ui_rect(self, mouse_x: int, mouse_y: int, rect_name: str) -> bool:
        """Returns True when the mouse is inside a named UI rect."""
        if rect_name in self.ui_rects:
            rect = self.ui_rects[rect_name]
            return rect.collidepoint(mouse_x, mouse_y)
        return False

    def handle_input(self, event, camera_x: int, camera_y: int, current_room_name: str):
        """Route keyboard and mouse events while the editor is active."""
        if not self.active:
            return

        keys = pygame.key.get_pressed()
        ctrl_pressed = keys[pygame.K_LCTRL] or keys[pygame.K_RCTRL]
        shift_pressed = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]

        if event.type == pygame.KEYDOWN:
            # Handle FPS input text entry (confirming a new animation)
            if self.fps_input_active:
                if event.key == pygame.K_RETURN:
                    try:
                        fps = float(self.fps_input_text) if self.fps_input_text else 6.0
                    except ValueError:
                        fps = 6.0
                    self._confirm_pending_animation(fps)
                    self.fps_input_active = False
                elif event.key == pygame.K_ESCAPE:
                    self.fps_input_active = False
                    self._pending_anim_anchor = None
                    self._pending_anim_frames = None
                elif event.key == pygame.K_BACKSPACE:
                    self.fps_input_text = self.fps_input_text[:-1]
                elif event.unicode.isdigit() or (event.unicode == '.' and '.' not in self.fps_input_text):
                    self.fps_input_text += event.unicode
                return

            # Handle layer input text entry
            if self.layer_input_active:
                if event.key == pygame.K_RETURN:
                    try:
                        self.custom_layer_value = int(self.layer_input_text)
                        self.current_layer = self.custom_layer_value
                        self.layer_input_active = False
                    except ValueError:
                        pass
                elif event.key == pygame.K_ESCAPE:
                    self.layer_input_active = False
                elif event.key == pygame.K_BACKSPACE:
                    self.layer_input_text = self.layer_input_text[:-1]
                elif event.key == pygame.K_MINUS or event.unicode == '-':
                    if not self.layer_input_text:
                        self.layer_input_text += '-'
                elif event.unicode.isdigit():
                    self.layer_input_text += event.unicode
                return

            if event.key == pygame.K_TAB:
                names = self.tileset_manager.tileset_names
                if names:
                    self.current_tileset_index = (self.current_tileset_index + 1) % len(names)
                    # Reset selection and scroll so the new tileset opens clean
                    self.selected_tile_x = 0
                    self.selected_tile_y = 0
                    self.selection_start_x = 0
                    self.selection_start_y = 0
                    self.selection_end_x = 0
                    self.selection_end_y = 0
                    self.palette_scroll_x = 0
                    self.palette_scroll_y = 0

            elif event.key == pygame.K_g:
                self.show_grid = not self.show_grid

            # Cycle through layer presets, skipping Custom... which has no fixed value
            elif event.key == pygame.K_l:
                next_index = (self.current_layer_preset_index + 1) % len(self.LAYER_PRESETS)
                while self.LAYER_PRESETS[next_index][1] is None:
                    next_index = (next_index + 1) % len(self.LAYER_PRESETS)
                self.current_layer_preset_index = next_index
                _, preset_value = self.LAYER_PRESETS[self.current_layer_preset_index]
                self.current_layer = preset_value
            # Arrow keys: plain = move cursor, Shift = extend selection, Ctrl = move without resetting selection
            elif event.key == pygame.K_LEFT:
                if not shift_pressed:
                    self.selected_tile_x = max(0, self.selected_tile_x - 1)
                    if not ctrl_pressed:
                        self.selection_start_x = self.selected_tile_x
                        self.selection_start_y = self.selected_tile_y
                        self.selection_end_x = self.selected_tile_x
                        self.selection_end_y = self.selected_tile_y
                else:
                    self.selection_end_x = max(self.selection_start_x, self.selection_end_x - 1)

            elif event.key == pygame.K_RIGHT:
                tileset = self.get_current_tileset()
                if tileset:
                    if not shift_pressed:
                        self.selected_tile_x = min(tileset.cols - 1, self.selected_tile_x + 1)
                        if not ctrl_pressed:
                            self.selection_start_x = self.selected_tile_x
                            self.selection_start_y = self.selected_tile_y
                            self.selection_end_x = self.selected_tile_x
                            self.selection_end_y = self.selected_tile_y
                    else:
                        self.selection_end_x = min(tileset.cols - 1, self.selection_end_x + 1)

            elif event.key == pygame.K_UP:
                if not shift_pressed:
                    self.selected_tile_y = max(0, self.selected_tile_y - 1)
                    if not ctrl_pressed:
                        self.selection_start_x = self.selected_tile_x
                        self.selection_start_y = self.selected_tile_y
                        self.selection_end_x = self.selected_tile_x
                        self.selection_end_y = self.selected_tile_y
                else:
                    self.selection_end_y = max(self.selection_start_y, self.selection_end_y - 1)

            elif event.key == pygame.K_DOWN:
                tileset = self.get_current_tileset()
                if tileset:
                    if not shift_pressed:
                        self.selected_tile_y = min(tileset.rows - 1, self.selected_tile_y + 1)
                        if not ctrl_pressed:
                            self.selection_start_x = self.selected_tile_x
                            self.selection_start_y = self.selected_tile_y
                            self.selection_end_x = self.selected_tile_x
                            self.selection_end_y = self.selected_tile_y
                    else:
                        self.selection_end_y = min(tileset.rows - 1, self.selection_end_y + 1)

            elif event.key == pygame.K_DELETE or event.key == pygame.K_x:
                mouse_x, mouse_y = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
                mouse_x, mouse_y = mouse_x / self.editor_zoom, mouse_y / self.editor_zoom
                world_x = (mouse_x + camera_x) // RENDER_SCALE
                world_y = (mouse_y + camera_y) // RENDER_SCALE
                self._delete_tile_at_position(world_x, world_y, current_room_name)

            elif event.key == pygame.K_n and not ctrl_pressed:
                self._toggle_animate_selection()

            elif event.key == pygame.K_c and not ctrl_pressed:
                self._toggle_solid_selection()

            elif event.key == pygame.K_h and not ctrl_pressed:
                self._toggle_collision_granularity()

            elif event.key == pygame.K_k and not ctrl_pressed:
                self.native_shadow_enabled = not self.native_shadow_enabled
                self._last_stroke_cell = None

            elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                if self._is_native_shadow_mode():
                    self.native_shadow_alpha = max(0, self.native_shadow_alpha - 16)

            elif event.key in (pygame.K_EQUALS, pygame.K_KP_PLUS):
                if self._is_native_shadow_mode():
                    self.native_shadow_alpha = min(255, self.native_shadow_alpha + 16)

        elif event.type == pygame.MOUSEBUTTONDOWN:
            mouse_x, mouse_y = event.pos

            # Panel show/hide toggle — checked first so it always fires
            if event.button == 1 and self._panel_toggle_rect().collidepoint(mouse_x, mouse_y):
                self.palette_visible = not self.palette_visible
                return

            if self._is_in_ui_rect(mouse_x, mouse_y, 'layer_dropdown'):
                self.layer_dropdown_open = not self.layer_dropdown_open
                return

            if self.layer_dropdown_open:
                for i, (name, value) in enumerate(self.LAYER_PRESETS):
                    if self._is_in_ui_rect(mouse_x, mouse_y, f'layer_option_{i}'):
                        self.current_layer_preset_index = i
                        if value is not None:
                            self.current_layer = value
                        else:
                            self.layer_input_active = True
                            self.layer_input_text = str(self.current_layer)
                        self.layer_dropdown_open = False
                        return
                self.layer_dropdown_open = False
                return

            if self._is_in_ui_rect(mouse_x, mouse_y, 'delete_checkbox'):
                self.delete_underlying = not self.delete_underlying
                return

            if self._is_in_ui_rect(mouse_x, mouse_y, 'hide_layer_checkbox'):
                # Toggles visibility of only the currently selected layer —
                # other layers are unaffected and keep their own hidden state.
                if self.current_layer in self.hidden_layers:
                    self.hidden_layers.discard(self.current_layer)
                else:
                    self.hidden_layers.add(self.current_layer)
                # Invalidate the baked tile cache so the visibility change is
                # reflected immediately — without this the old surface persists.
                self.notify_tile_changed(current_room_name)
                return

            # Pygame 1.x scroll convention: button 4 = scroll up, button 5 = scroll down
            # (MOUSEWHEEL event is preferred in newer pygame but both work)
            # Only scroll the palette when the wheel happens over the palette
            # box itself, and consume the event (return) so it doesn't also
            # fall through to the room camera pan handler.
            if event.button in (4, 5) and self._is_in_palette(mouse_x, mouse_y):
                if event.button == 4:
                    if shift_pressed:
                        self.palette_scroll_x = max(0, self.palette_scroll_x - self.grid_cell_size)
                    else:
                        self.palette_scroll_y = max(0, self.palette_scroll_y - self.grid_cell_size)
                else:
                    tileset = self.get_current_tileset()
                    if tileset:
                        if shift_pressed:
                            max_scroll_x = max(0, tileset.cols * self.grid_cell_size - self.palette_width + 40)
                            self.palette_scroll_x = min(max_scroll_x, self.palette_scroll_x + self.grid_cell_size)
                        else:
                            max_scroll_y = max(0, tileset.rows * self.grid_cell_size - self.palette_content_height)
                            self.palette_scroll_y = min(max_scroll_y, self.palette_scroll_y + self.grid_cell_size)
                return

            # Gate on the real screen position, not (mouse_x, mouse_y) — those
            # may already be zoom/room-space coordinates rewritten upstream by
            # room_editor._zoom_adjust_event (continuous zoom, or the
            # fit-to-room overview), and palette_x/palette_y are fixed screen
            # constants. Checking the rewritten coords against them makes
            # every off-palette click in a room bigger than the screen look
            # like it landed on the palette once zoomed out far enough.
            real_x, real_y = event.dict.get('_room_editor_raw_pos', getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos()))

            if event.button == 1:
                if self._is_in_palette(real_x, real_y):
                    self._handle_palette_click(mouse_x, mouse_y, ctrl_pressed)
                    self.is_palette_dragging = True
                else:
                    self.is_dragging = True
                    world_x = (mouse_x + camera_x) // RENDER_SCALE
                    world_y = (mouse_y + camera_y) // RENDER_SCALE
                    self.drag_start_pos = (world_x, world_y)
                    self._place_tiles(world_x, world_y, current_room_name)

            elif event.button == 3:
                if not self._is_in_palette(real_x, real_y):
                    self.is_erasing = True
                    world_x = (mouse_x + camera_x) // RENDER_SCALE
                    world_y = (mouse_y + camera_y) // RENDER_SCALE
                    self._delete_tile_at_position(world_x, world_y, current_room_name)

        elif event.type == pygame.MOUSEBUTTONUP:
            if event.button == 1:
                self.is_dragging = False
                self.drag_start_pos = None
                self.is_palette_dragging = False
                self._last_stroke_cell = None
            elif event.button == 3:
                self.is_erasing = False
                self._last_stroke_cell = None

        elif event.type == pygame.MOUSEMOTION:
            # Same real-screen-position gating as MOUSEBUTTONDOWN above —
            # event.pos here may already be zoom/room-space.
            real_x, real_y = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
            if self.is_dragging and not self._is_in_palette(real_x, real_y):
                mouse_x, mouse_y = event.pos
                world_x = (mouse_x + camera_x) // RENDER_SCALE
                world_y = (mouse_y + camera_y) // RENDER_SCALE
                self._place_tiles(world_x, world_y, current_room_name)
            elif self.is_erasing and not self._is_in_palette(real_x, real_y):
                mouse_x, mouse_y = event.pos
                world_x = (mouse_x + camera_x) // RENDER_SCALE
                world_y = (mouse_y + camera_y) // RENDER_SCALE
                self._delete_tile_at_position(world_x, world_y, current_room_name)
            elif self.is_palette_dragging:
                mouse_x, mouse_y = event.pos
                if self._is_in_palette(real_x, real_y):
                    self._handle_palette_drag(mouse_x, mouse_y)

    def _palette_to_tile_coords(self, mouse_x: int, mouse_y: int):
        """Convert a screen mouse position to tileset grid coords, accounting for scroll.

        Returns (tile_x, tile_y) or None if the position is outside the tileset area.
        """
        tileset_x = self.palette_x + 20
        tileset_y = self.palette_y + self.tileset_area_y_offset
        rel_x = mouse_x - tileset_x + self.palette_scroll_x
        rel_y = mouse_y - tileset_y + self.palette_scroll_y

        if rel_x < 0 or rel_y < 0:
            return None

        return int(rel_x // self.grid_cell_size), int(rel_y // self.grid_cell_size)

    def _handle_palette_click(self, mouse_x: int, mouse_y: int, ctrl_pressed: bool):
        """Select a tile (or keep the anchor point when Ctrl is held) from a palette click."""
        tileset = self.get_current_tileset()
        if not tileset:
            return

        coords = self._palette_to_tile_coords(mouse_x, mouse_y)
        if coords is None:
            return

        tile_x, tile_y = coords
        if not (0 <= tile_x < tileset.cols and 0 <= tile_y < tileset.rows):
            return

        self.selected_tile_x = tile_x
        self.selected_tile_y = tile_y

        # Ctrl+click extends the existing selection; plain click resets it to a single tile
        if not ctrl_pressed:
            self.selection_start_x = tile_x
            self.selection_start_y = tile_y
            self.selection_end_x = tile_x
            self.selection_end_y = tile_y

    def _handle_palette_drag(self, mouse_x: int, mouse_y: int):
        """Extend the selection rectangle as the user drags across the palette."""
        tileset = self.get_current_tileset()
        if not tileset:
            return

        coords = self._palette_to_tile_coords(mouse_x, mouse_y)
        if coords is None:
            return

        # Clamp to valid tile range so dragging past the edge doesn't overflow
        tile_x = min(tileset.cols - 1, max(0, coords[0]))
        tile_y = min(tileset.rows - 1, max(0, coords[1]))

        self.selection_end_x = tile_x
        self.selection_end_y = tile_y

    def _snap_anchor(self, world_x, world_y):
        """Snap a world position to the current placement-snap grid
        (self.snap_size). 0 means no grid — return the raw pixel position
        unsnapped. Shared by tile stamping, erasing, and the grid overlay
        so they can never drift apart."""
        if self.snap_size <= 0:
            return int(world_x), int(world_y)
        return (int(world_x) // self.snap_size) * self.snap_size, \
               (int(world_y) // self.snap_size) * self.snap_size

    def _is_native_shadow_mode(self) -> bool:
        """True when the texture-free native shadow brush is active.

        Shadows are deliberately independent of the selected layer. The current
        layer is still stored on the shadow tile and therefore controls its z-order.
        """
        return self.native_shadow_enabled

    # Backwards-compatible helper name for any external callers.
    def _is_native_shadow_layer(self) -> bool:
        return self._is_native_shadow_mode()

    def _native_shadow_size(self, tileset=None):
        """Return the native shadow brush size.

        When the room editor's Grid control is active, native shadows use that
        exact grid spacing (8px, 16px, etc.) instead of the global TILE_SIZE.
        With Grid Off, fall back to the selected tileset's native footprint.
        """
        if self.snap_size > 0:
            size = max(1, int(self.snap_size))
            return size, size

        if tileset is not None:
            return (
                max(1, int(getattr(tileset, 'tile_width', TILE_SIZE))),
                max(1, int(getattr(tileset, 'tile_height', TILE_SIZE))),
            )

        return (
            max(1, int(self.native_shadow_cell_w)),
            max(1, int(self.native_shadow_cell_h)),
        )

    def _make_native_shadow_tile(self, x, y):
        return Tile(
            int(x), int(y), '__native_shadow__', -1, -1,
            self.current_layer, self.current_layer >= 75,
            is_shadow=True,
            shadow_alpha=self.native_shadow_alpha,
            shadow_width=self.native_shadow_cell_w,
            shadow_height=self.native_shadow_cell_h,
        )

    def _place_native_shadow(self, world_x: int, world_y: int, room_name: str):
        """Paint one native translucent-black shadow cell on the current layer."""
        grid_x, grid_y = self._snap_anchor(world_x, world_y)
        if (grid_x, grid_y) == self._last_stroke_cell:
            return
        self._last_stroke_cell = (grid_x, grid_y)

        if room_name not in self.room_tiles:
            self.room_tiles[room_name] = []

        self._invalidate_sorted_tiles_cache(room_name)

        new_w = max(1, int(self.native_shadow_cell_w))
        new_h = max(1, int(self.native_shadow_cell_h))

        kept = []
        touched_cells = []
        for tile in self.room_tiles[room_name]:
            if not (tile.layer == self.current_layer and getattr(tile, 'is_shadow', False)):
                kept.append(tile)
                continue
            old_w = max(1, int(getattr(tile, 'shadow_width', TILE_SIZE)))
            old_h = max(1, int(getattr(tile, 'shadow_height', TILE_SIZE)))
            fully_covered = (
                grid_x <= tile.x and grid_y <= tile.y and
                grid_x + new_w >= tile.x + old_w and
                grid_y + new_h >= tile.y + old_h
            )
            if not fully_covered:
                kept.append(tile)
            else:
                touched_cells.append((tile.x, tile.y, old_w, old_h))

        kept.append(self._make_native_shadow_tile(grid_x, grid_y))
        self.room_tiles[room_name] = kept
        touched_cells.append((grid_x, grid_y, new_w, new_h))

        self.notify_tile_changed(room_name, cells=touched_cells)

    def _erase_native_shadow_at_position(self, world_x: int, world_y: int, room_name: str):
        if room_name not in self.room_tiles:
            return

        removed = []
        kept = []
        for tile in self.room_tiles[room_name]:
            if tile.layer != self.current_layer or not getattr(tile, 'is_shadow', False):
                kept.append(tile)
                continue
            w = max(1, int(getattr(tile, 'shadow_width', TILE_SIZE)))
            h = max(1, int(getattr(tile, 'shadow_height', TILE_SIZE)))
            if tile.x <= world_x < tile.x + w and tile.y <= world_y < tile.y + h:
                removed.append(tile)
            else:
                kept.append(tile)

        if not removed:
            return

        self.room_tiles[room_name] = kept
        self._invalidate_sorted_tiles_cache(room_name)
        touched_cells = [
            (
                tile.x, tile.y,
                max(1, int(getattr(tile, 'shadow_width', TILE_SIZE))),
                max(1, int(getattr(tile, 'shadow_height', TILE_SIZE))),
            )
            for tile in removed
        ]
        self.notify_tile_changed(room_name, cells=touched_cells)

    def _place_tiles(self, world_x: int, world_y: int, room_name: str):
        """Stamp the current selection pattern into the room at the snapped world position.

        Each tile in the multi-tile selection is offset relative to the top-left of the
        selection and placed at the corresponding grid cell. Empty (fully transparent) tiles
        in the selection are silently skipped so they don't erase valid tiles underneath.
        If delete_underlying is on, existing tiles on the same layer that
        are fully covered by the new tile are removed before it's inserted;
        tiles only partially overlapped are left alone since part of their
        sprite would still be visible.
        """
        # Native shadows do not require a darkened tileset sprite and may live on any layer.
        if self._is_native_shadow_mode():
            self.native_shadow_cell_w, self.native_shadow_cell_h = self._native_shadow_size(
                self.get_current_tileset()
            )
            self._place_native_shadow(world_x, world_y, room_name)
            return

        tileset = self.get_current_tileset()
        if not tileset:
            return

        # Snap the drop point to the current placement-snap grid (Off / 8px
        # / 16px, driven by the room editor toolbar's Grid control).
        grid_x, grid_y = self._snap_anchor(world_x, world_y)

        # Skip if the mouse hasn't moved to a new cell — avoids redundant
        # cache rebuilds from rapid MOUSEMOTION events on the same tile
        if (grid_x, grid_y) == self._last_stroke_cell:
            return
        self._last_stroke_cell = (grid_x, grid_y)

        if room_name not in self.room_tiles:
            self.room_tiles[room_name] = []

        self._invalidate_sorted_tiles_cache(room_name)

        min_x, max_x, min_y, max_y = self._get_selection_bounds()

        touched_cells = []
        for ty in range(min_y, max_y + 1):
            for tx in range(min_x, max_x + 1):
                if tileset.is_tile_empty(tx, ty):
                    continue

                # Multi-cell pattern spacing always uses the tileset's own
                # native sprite pitch (not the placement-snap grid, and not
                # a fixed constant) so painted tiles keep tiling seamlessly
                # for tilesets of any size (8px, 16px, 32px...) regardless
                # of what snap size is currently active.
                offset_x = (tx - min_x) * tileset.tile_width
                offset_y = (ty - min_y) * tileset.tile_height
                tile_x = grid_x + offset_x
                tile_y = grid_y + offset_y

                if self.delete_underlying:
                    # Remove any existing tile on this layer that the new
                    # tile fully covers — i.e. the old tile's sprite would
                    # be entirely hidden underneath the new one.
                    #
                    # This used to be an exact (t.x == tile_x and t.y ==
                    # tile_y) match, which only works when every tile on the
                    # layer was placed on the same snap grid. A tile stamped
                    # in "No Grid" mode sits at an arbitrary pixel offset, so
                    # a tile placed on top of it later with, say, a 16x16
                    # grid essentially never lands on that exact pixel — the
                    # old tile was never actually removed, it just got
                    # visually covered. Deleting the new tile afterwards then
                    # "revealed" the old one still sitting in the room data.
                    #
                    # A first pass used "any overlap" instead of "fully
                    # covered", but that's too aggressive when tile sizes
                    # differ: stamping one 16x16 tile can brush against many
                    # 8x8 tiles without actually hiding them, and those were
                    # getting deleted even though most of their sprite was
                    # still visible outside the new tile's area. Requiring
                    # full containment means only tiles that would actually
                    # be hidden get cleared — a partially-overlapped smaller
                    # tile stays right where it is.
                    new_w, new_h = tileset.tile_width, tileset.tile_height
                    kept = []
                    for t in self.room_tiles[room_name]:
                        if t.layer != self.current_layer:
                            kept.append(t)
                            continue
                        t_tileset = self.tileset_manager.get_tileset(t.tileset_name)
                        t_w, t_h = (t_tileset.tile_width, t_tileset.tile_height) if t_tileset else (self.grid_size, self.grid_size)
                        fully_covered = (
                            tile_x <= t.x and tile_y <= t.y and
                            tile_x + new_w >= t.x + t_w and
                            tile_y + new_h >= t.y + t_h
                        )
                        if not fully_covered:
                            kept.append(t)
                    self.room_tiles[room_name] = kept

                new_tile = Tile(
                    tile_x, tile_y,
                    tileset.name,
                    tx, ty,
                    self.current_layer,
                    self.current_layer >= 75  # treat layer 75+ as foreground
                )
                self.room_tiles[room_name].append(new_tile)
                # Include this tile's real footprint (not a fixed grid
                # constant) so the renderer's incremental patch clears
                # exactly the right area — otherwise a coarser assumed
                # size bleeds into neighboring tiles on finer tilesets
                # (e.g. 8px) and erases their already-baked pixels.
                touched_cells.append((tile_x, tile_y, tileset.tile_width, tileset.tile_height))

        # Notify listeners (e.g. auto-save, tile-collision resync) that the
        # room content changed. Pass the exact cells touched so the
        # renderer can patch just those spots in the baked surface instead
        # of rebuilding the whole room.
        self.notify_tile_changed(room_name, cells=touched_cells)

    def _delete_tile_at_position(self, world_x: int, world_y: int, room_name: str):
        """Remove tile(s) at the given world position on the current layer.

        Always hit-tests the raw click point against each tile's real
        footprint (from its tileset's tile_width/tile_height), regardless
        of the currently active placement-snap grid.

        This used to branch on snap_size: an exact top-left match when a
        grid was active, hit-testing only in "No Grid" mode. That broke as
        soon as tiles of different sizes shared a layer — e.g. deleting an
        8x8 tile while the active placement grid is 16x16 snaps the click
        to a 16px grid line the 8x8 tile was never placed on, so it could
        never match. The active grid is about where *new* tiles get
        stamped; it has nothing to do with where an *existing* tile
        actually sits, so deletion shouldn't be constrained by it at all —
        click anywhere on a tile's visible sprite and it comes up for
        removal, no matter what size it is or what grid you're currently
        placing with.
        """
        if self._is_native_shadow_mode():
            self._erase_native_shadow_at_position(world_x, world_y, room_name)
            return

        if room_name not in self.room_tiles:
            return

        # Only used to de-duplicate repeated erases while dragging across
        # the same spot — the actual match below always uses the raw,
        # unsnapped click position.
        grid_x, grid_y = self._snap_anchor(world_x, world_y)

        # Skip if still on the same cell/position as the last erase
        if (grid_x, grid_y) == self._last_stroke_cell:
            return
        self._last_stroke_cell = (grid_x, grid_y)

        removed = []
        for tile in self.room_tiles[room_name]:
            if tile.layer != self.current_layer:
                continue
            removed_tileset = self.tileset_manager.get_tileset(tile.tileset_name)
            if removed_tileset:
                w, h = removed_tileset.tile_width, removed_tileset.tile_height
            else:
                w, h = self.grid_size, self.grid_size
            if tile.x <= world_x < tile.x + w and tile.y <= world_y < tile.y + h:
                removed.append(tile)

        if not removed:
            return

        removed_ids = set(id(t) for t in removed)
        self.room_tiles[room_name] = [
            tile for tile in self.room_tiles[room_name]
            if id(tile) not in removed_ids
        ]
        self._invalidate_sorted_tiles_cache(room_name)

        # Footprint of what was actually removed (not a fixed grid constant)
        # — same reasoning as _place_tiles: the renderer's incremental patch
        # needs the real tile size so it clears exactly this spot and
        # nothing on a neighboring cell. Only fall back to grid_size if the
        # tileset can't be resolved (e.g. deleted/renamed tileset file).
        touched_cells = []
        for tile in removed:
            removed_tileset = self.tileset_manager.get_tileset(tile.tileset_name)
            if removed_tileset:
                cell_w, cell_h = removed_tileset.tile_width, removed_tileset.tile_height
            else:
                cell_w, cell_h = self.grid_size, self.grid_size
            touched_cells.append((tile.x, tile.y, cell_w, cell_h))

        # Notify listeners (e.g. auto-save, tile-collision resync) that the
        # room content changed. Pass the exact cell(s) touched so the
        # renderer can patch just those spots in the baked surface instead
        # of rebuilding the whole room.
        self.notify_tile_changed(room_name, cells=touched_cells)

    def draw_tile_preview(self, screen: pygame.Surface, camera_x: int, camera_y: int):
        """Draw a semi-transparent ghost of the selected tile pattern under the cursor."""
        if not self.active:
            return

        mouse_x, mouse_y = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        if self._is_in_palette(mouse_x, mouse_y):
            return


        mouse_x, mouse_y = mouse_x / self.editor_zoom, mouse_y / self.editor_zoom
        world_x = (mouse_x + camera_x) // RENDER_SCALE
        world_y = (mouse_y + camera_y) // RENDER_SCALE
        grid_x, grid_y = self._snap_anchor(world_x, world_y)

        tileset = self.get_current_tileset()
        if self._is_native_shadow_mode():
            shadow_w, shadow_h = self._native_shadow_size(tileset)
            screen_x = int(grid_x * RENDER_SCALE - camera_x)
            screen_y = int(grid_y * RENDER_SCALE - camera_y)
            preview_surface = pygame.Surface(
                (shadow_w * RENDER_SCALE, shadow_h * RENDER_SCALE), pygame.SRCALPHA
            )
            preview_surface.fill((0, 0, 0, max(0, min(255, int(self.native_shadow_alpha)))))
            screen.blit(preview_surface, (screen_x, screen_y))
            screen.draw_rect(
                self.colors['accent'],
                (screen_x, screen_y, shadow_w * RENDER_SCALE, shadow_h * RENDER_SCALE),
                2,
            )
            return

        if not tileset:
            return

        min_x, max_x, min_y, max_y = self._get_selection_bounds()
        scaled_width = tileset.tile_width * RENDER_SCALE
        scaled_height = tileset.tile_height * RENDER_SCALE
        tick_ms = pygame.time.get_ticks()

        for ty in range(min_y, max_y + 1):
            for tx in range(min_x, max_x + 1):
                if tileset.is_tile_empty(tx, ty):
                    continue

                # Resolve to the current animation frame (no-op for static tiles)
                disp_x, disp_y = tileset.get_animated_coords(tx, ty, tick_ms)

                # Use the cache to avoid repeated transform.scale calls per frame
                scaled_tile = tileset.get_scaled_tile_surface(disp_x, disp_y, RENDER_SCALE)
                if not scaled_tile:
                    continue

                offset_x = (tx - min_x) * tileset.tile_width
                offset_y = (ty - min_y) * tileset.tile_height
                tile_world_x = grid_x + offset_x
                tile_world_y = grid_y + offset_y

                screen_x = int((tile_world_x * RENDER_SCALE) - camera_x)
                screen_y = int((tile_world_y * RENDER_SCALE) - camera_y)

                # Copy so we can set alpha without mutating the cached surface
                preview_surface = scaled_tile.copy()
                preview_surface.set_alpha(128)
                screen.blit(preview_surface, (screen_x, screen_y))

                # Accent border so it reads clearly over any background
                screen.draw_rect( self.colors['accent'],
                                 (screen_x, screen_y, scaled_width, scaled_height), 2)

    def _invalidate_sorted_tiles_cache(self, room_name: str):
        """Drop the cached layer-sorted tile order for a room. Call this
        anywhere room_tiles[room_name] is reassigned or appended to."""
        self._sorted_tiles_cache.pop(room_name, None)
        self._animated_tiles_cache.pop(room_name, None)
        # Any edit invalidates the painting-mode background frame cache too —
        # bumping the generation is enough to miss the cache key everywhere
        # it's checked, without having to also track room_name here.
        self._tile_content_generation += 1
        self._paint_bg_frame_cache.pop(room_name, None)

    def _get_sorted_tiles(self, room_name: str) -> List[Tile]:
        """Layer-sorted view of room_tiles[room_name], cached across frames.

        Rebuilt only when _invalidate_sorted_tiles_cache() has been called
        for this room (i.e. on an actual edit), not on every draw call.
        """
        cached = self._sorted_tiles_cache.get(room_name)
        if cached is None:
            cached = sorted(self.room_tiles[room_name], key=lambda t: t.layer)
            self._sorted_tiles_cache[room_name] = cached
        return cached

    def _get_animated_tiles(self, room_name: str) -> List[Tile]:
        """The (usually small) subset of placed tiles in this room that are
        animated, cached alongside the sorted-tiles cache.

        Used to cheaply check whether *any* animated tile is currently on
        screen, without re-scanning the whole tile list every frame.
        """
        cached = self._animated_tiles_cache.get(room_name)
        if cached is None:
            cached = []
            for tile in self.room_tiles.get(room_name, []):
                if getattr(tile, 'is_shadow', False):
                    continue
                tileset = self.tileset_manager.get_tileset(tile.tileset_name)
                if tileset and tileset.is_tile_animated(tile.tile_x, tile.tile_y):
                    cached.append(tile)
            self._animated_tiles_cache[room_name] = cached
        return cached

    def draw_tiles(self, screen: pygame.Surface, camera_x: int, camera_y: int,
                   room_name: str, layer: str = 'background'):
        """Draw all tiles for a room at the specified rendering pass ('background' or 'foreground').

        Tiles with layer >= 0 are drawn in the foreground pass; everything below 0 is background.
        Layers toggled off via the "Hide this layer" checkbox (self.hidden_layers) are skipped
        entirely. Of the remaining layers, the active editing layer (current_layer) is always
        drawn at full opacity; every other layer is always dimmed, so surrounding layers stay
        visible as context without obscuring what's being edited. Uses the tileset's
        scaled/dimmed surface caches to avoid per-frame transform.scale() calls.

        The background pass splits static tiles from animated ones. Static
        tiles are baked into a cached composite (see _paint_bg_frame_cache)
        and reused with a single opaque blit as long as the camera, layer
        selection, hidden-layer set, and tile data haven't changed — no
        per-pixel alpha blending on frames where nothing moved. Animated
        tiles are deliberately excluded from that composite (their frame
        advances independently of all of that) and are instead redrawn
        individually on top every frame — but that's normally a small
        fraction of the room's tiles, so the per-frame cost scales with how
        many animated tiles exist, not with total tile count.
        """
        if room_name not in self.room_tiles:
            return

        if layer == 'background':
            cache_key = (
                camera_x, camera_y, self.screen_width, self.screen_height,
                self.current_layer, frozenset(self.hidden_layers),
                self._tile_content_generation,
            )
            cached = self._paint_bg_frame_cache.get(room_name)
            if cached is not None and cached['key'] == cache_key \
                    and cached['surface'].get_size() == screen.get_size():
                screen.blit(cached['surface'], (0, 0))
            else:
                self._draw_tiles_pass(screen, camera_x, camera_y, room_name, layer,
                                       skip_animated=True)
                # A GPUScreen has no software framebuffer to snapshot. Do not
                # read GPU pixels back to the CPU just to populate this cache:
                # that would erase the performance benefit of GPU zooming.
                if hasattr(screen, 'copy'):
                    snapshot = screen.copy()
                    self._paint_bg_frame_cache[room_name] = {
                        'key': cache_key,
                        'surface': snapshot,
                    }

            self._draw_animated_tiles_pass(screen, camera_x, camera_y, room_name, layer)
            return

        self._draw_tiles_pass(screen, camera_x, camera_y, room_name, layer)

    def _draw_tiles_pass(self, screen: pygame.Surface, camera_x: int, camera_y: int,
                          room_name: str, layer: str, skip_animated: bool = False):
        """Do the actual per-tile draw loop for one pass.

        Called directly for the foreground pass (never frame-cached — see
        draw_tiles) and for the background pass on a cache miss, where
        skip_animated=True leaves animated tiles out of the composite being
        baked (see _draw_animated_tiles_pass, which draws them separately).
        """
        tick_ms = pygame.time.get_ticks()

        # If the currently-selected layer is itself hidden, there's no visible
        # "active" layer to compare against — don't dim everything else just
        # because the reference layer happens to be invisible.
        active_layer_is_visible = self.current_layer not in self.hidden_layers

        for tile in self._get_sorted_tiles(room_name):
            # Layers hidden via the per-layer checkbox are skipped entirely —
            # unlike dimming, this is a full hide, and only ever affects the
            # specific layer(s) the checkbox was toggled on.
            if tile.layer in self.hidden_layers:
                continue

            # Split world tiles into two draw passes so foreground tiles render on top
            if layer == 'background' and tile.layer >= 0:
                continue
            if layer == 'foreground' and tile.layer < 0:
                continue

            if getattr(tile, 'is_shadow', False):
                self._draw_native_shadow(
                    screen, tile, camera_x, camera_y,
                    active_layer_is_visible
                )
                continue

            tileset = self.tileset_manager.get_tileset(tile.tileset_name)
            if not tileset:
                continue

            if skip_animated and tileset.is_tile_animated(tile.tile_x, tile.tile_y):
                continue

            self._draw_single_tile(screen, tile, tileset, camera_x, camera_y,
                                    tick_ms, active_layer_is_visible)

    def _draw_animated_tiles_pass(self, screen: pygame.Surface, camera_x: int, camera_y: int,
                                   room_name: str, layer: str):
        """Redraw just the room's animated tiles for one pass — the
        counterpart to skip_animated in _draw_tiles_pass. Iterates the
        (usually short) _get_animated_tiles list instead of every tile in
        the room, so this stays cheap regardless of total tile count."""
        tick_ms = pygame.time.get_ticks()
        active_layer_is_visible = self.current_layer not in self.hidden_layers

        for tile in self._get_animated_tiles(room_name):
            if tile.layer in self.hidden_layers:
                continue
            if layer == 'background' and tile.layer >= 0:
                continue
            if layer == 'foreground' and tile.layer < 0:
                continue

            if getattr(tile, 'is_shadow', False):
                self._draw_native_shadow(
                    screen, tile, camera_x, camera_y,
                    active_layer_is_visible
                )
                continue

            tileset = self.tileset_manager.get_tileset(tile.tileset_name)
            if not tileset:
                continue

            self._draw_single_tile(screen, tile, tileset, camera_x, camera_y,
                                    tick_ms, active_layer_is_visible)

    def _draw_native_shadow(self, screen: pygame.Surface, tile: Tile,
                            camera_x: int, camera_y: int,
                            active_layer_is_visible: bool):
        """Draw a native translucent black overlay."""
        screen_x = (tile.x * RENDER_SCALE) - camera_x
        screen_y = (tile.y * RENDER_SCALE) - camera_y
        width = max(1, int(getattr(tile, 'shadow_width', TILE_SIZE))) * RENDER_SCALE
        height = max(1, int(getattr(tile, 'shadow_height', TILE_SIZE))) * RENDER_SCALE

        if not (-width <= screen_x <= self.screen_width and
                -height <= screen_y <= self.screen_height):
            return

        alpha = max(0, min(255, int(getattr(tile, 'shadow_alpha', 128))))
        if active_layer_is_visible and tile.layer != self.current_layer:
            alpha = int(alpha * (self.INACTIVE_LAYER_ALPHA / 255.0))

        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, alpha))
        screen.blit(overlay, (int(screen_x), int(screen_y)))

    def _draw_single_tile(self, screen: pygame.Surface, tile: Tile, tileset: 'Tileset',
                           camera_x: int, camera_y: int, tick_ms: int,
                           active_layer_is_visible: bool):
        """Shared per-tile draw logic used by both _draw_tiles_pass and
        _draw_animated_tiles_pass, so the two draw loops can't drift apart
        on positioning, culling, or dimming behavior."""
        screen_x = (tile.x * RENDER_SCALE) - camera_x
        screen_y = (tile.y * RENDER_SCALE) - camera_y

        scaled_width = tileset.tile_width * RENDER_SCALE
        scaled_height = tileset.tile_height * RENDER_SCALE

        # Skip tiles that are entirely off-screen
        if not (-scaled_width <= screen_x <= self.screen_width and
                -scaled_height <= screen_y <= self.screen_height):
            return

        # Resolve to the current animation frame (no-op for static tiles)
        disp_x, disp_y = tileset.get_animated_coords(tile.tile_x, tile.tile_y, tick_ms)

        # Active layer stays full-strength; every other layer is dimmed —
        # but only while the active layer itself is actually visible.
        # Using a cached, pre-alpha'd surface so this costs nothing extra per frame.
        if active_layer_is_visible and tile.layer != self.current_layer:
            draw_surface = tileset.get_dimmed_tile_surface(
                disp_x, disp_y, RENDER_SCALE, self.INACTIVE_LAYER_ALPHA
            )
        else:
            draw_surface = tileset.get_scaled_tile_surface(disp_x, disp_y, RENDER_SCALE)

        if not draw_surface:
            return

        screen.blit(draw_surface, (int(screen_x), int(screen_y)))

    def draw_palette(self, screen: pygame.Surface):
        """Draw the tileset palette UI with layer controls"""
        if not self.active:
            return

        # Update hover state for the toggle tab
        mx, my = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        self._hover_panel_toggle = self._panel_toggle_rect().collidepoint(mx, my)

        # Always draw the toggle tab so the panel can be recalled when hidden
        self._draw_panel_toggle_tab(screen)

        if not self.palette_visible:
            return

        tileset = self.get_current_tileset()
        if not tileset:
            return

        palette_height = 940
        palette_rect = pygame.Rect(self.palette_x, self.palette_y,
                                   self.palette_width, palette_height)

        # Background
        palette_bg = pygame.Surface((self.palette_width, palette_height), pygame.SRCALPHA)
        palette_bg.fill((*self.colors['bg'], 230))
        screen.blit(palette_bg, (self.palette_x, self.palette_y))
        screen.draw_rect( self.colors['accent'], palette_rect, 2)

        # Title
        title_text = self.font_medium.render(f"Tileset: {tileset.name}", True, self.colors['text'])
        screen.blit(title_text, (self.palette_x + 20, self.palette_y + 10))

        # Tileset dimensions
        dims_text = f"{tileset.cols}x{tileset.rows} tiles ({tileset.tile_width}px)"
        dims_surf = self.font_small.render(dims_text, True, self.colors['text_dim'])
        screen.blit(dims_surf, (self.palette_x + 280, self.palette_y + 15))

        # Draw tileset with grid
        tileset_y = self.palette_y + self.tileset_area_y_offset
        tileset_x = self.palette_x + 20

        # Create clipping rect for scrollable area
        clip_rect = pygame.Rect(tileset_x, tileset_y,
                                self.palette_width - 40, self.palette_content_height)
        screen.set_clip(clip_rect)

        # Draw the tileset image scaled up
        if tileset.image:
            scaled_width = tileset.cols * self.grid_cell_size
            scaled_height = tileset.rows * self.grid_cell_size
            scaled_tileset = pygame.transform.scale(tileset.image, (scaled_width, scaled_height))

            draw_x = tileset_x - self.palette_scroll_x
            draw_y = tileset_y - self.palette_scroll_y
            screen.blit(scaled_tileset, (draw_x, draw_y))

            # Draw grid overlay
            for row in range(tileset.rows + 1):
                y = draw_y + row * self.grid_cell_size
                screen.draw_line( self.colors['grid_dim'][:3],
                                 (draw_x, y), (draw_x + scaled_width, y), 1)

            for col in range(tileset.cols + 1):
                x = draw_x + col * self.grid_cell_size
                screen.draw_line( self.colors['grid_dim'][:3],
                                 (x, draw_y), (x, draw_y + scaled_height), 1)

            # Mark animated anchor tiles with a small badge so they're
            # identifiable at a glance while browsing the palette
            for (anim_tx, anim_ty) in tileset.tile_animations:
                if not (0 <= anim_tx < tileset.cols and 0 <= anim_ty < tileset.rows):
                    continue
                badge_x = draw_x + anim_tx * self.grid_cell_size + self.grid_cell_size - 9
                badge_y = draw_y + anim_ty * self.grid_cell_size + 2
                screen.draw_circle( self.colors['accent'], (badge_x, badge_y), 5)
                screen.draw_circle( (20, 20, 30), (badge_x, badge_y), 5, 1)

            # Mark solid (collision) tiles with a red hatch overlay so their
            # footprint is visible at a glance — echoes the red hatched look
            # collision walls get drawn with in the room editor itself, so
            # "red = blocks movement" reads consistently in both places.
            #
            # At collision_granularity 16 this hatches whole tiles from
            # solid_tiles, same as always. At granularity 8 it instead
            # hatches individual 8x8 quadrants from solid_subtiles, so a
            # tile that's only partially solid shows exactly which corner.
            if tileset.collision_granularity == 8:
                half = self.grid_cell_size // 2
                for (solid_tx, solid_ty, sub_x, sub_y) in tileset.solid_subtiles:
                    if not (0 <= solid_tx < tileset.cols and 0 <= solid_ty < tileset.rows):
                        continue
                    cell_x = draw_x + solid_tx * self.grid_cell_size + sub_x * half
                    cell_y = draw_y + solid_ty * self.grid_cell_size + sub_y * half
                    hatch = pygame.Surface((half, half), pygame.SRCALPHA)
                    hatch.fill((255, 0, 0, 60))
                    for i in range(-half, half * 2, 6):
                        pygame.draw.line(hatch, (255, 0, 0, 130), (i, 0), (i + half, half), 1)
                    screen.blit(hatch, (cell_x, cell_y))
                    screen.draw_rect((255, 0, 0), (cell_x, cell_y, half, half), 1)
                # Faint quadrant divider on every 16x16 tile (not just solid
                # ones) so it's clear at a glance the palette is in 8x8
                # collision mode and where each tile's quarter boundaries
                # fall, before anything's even been painted solid yet.
                for row in range(tileset.rows):
                    for col in range(tileset.cols):
                        mid_x = draw_x + col * self.grid_cell_size + half
                        mid_y = draw_y + row * self.grid_cell_size + half
                        screen.draw_line((150, 150, 170),
                                         (mid_x, draw_y + row * self.grid_cell_size),
                                         (mid_x, draw_y + row * self.grid_cell_size + self.grid_cell_size), 1)
                        screen.draw_line((150, 150, 170),
                                         (draw_x + col * self.grid_cell_size, mid_y),
                                         (draw_x + col * self.grid_cell_size + self.grid_cell_size, mid_y), 1)
            else:
                for (solid_tx, solid_ty) in tileset.solid_tiles:
                    if not (0 <= solid_tx < tileset.cols and 0 <= solid_ty < tileset.rows):
                        continue
                    cell_x = draw_x + solid_tx * self.grid_cell_size
                    cell_y = draw_y + solid_ty * self.grid_cell_size
                    size = self.grid_cell_size
                    hatch = pygame.Surface((size, size), pygame.SRCALPHA)
                    hatch.fill((255, 0, 0, 60))
                    for i in range(-size, size * 2, 6):
                        pygame.draw.line(hatch, (255, 0, 0, 130), (i, 0), (i + size, size), 1)
                    screen.blit(hatch, (cell_x, cell_y))
                    screen.draw_rect((255, 0, 0), (cell_x, cell_y, size, size), 1)

            # Draw selection rectangle
            min_x, max_x, min_y, max_y = self._get_selection_bounds()
            sel_width = max_x - min_x + 1
            sel_height = max_y - min_y + 1
            sel_x = draw_x + min_x * self.grid_cell_size
            sel_y = draw_y + min_y * self.grid_cell_size
            sel_w = sel_width * self.grid_cell_size
            sel_h = sel_height * self.grid_cell_size

            # Selection fill
            sel_surf = pygame.Surface((sel_w, sel_h), pygame.SRCALPHA)
            sel_surf.fill((*self.colors['selection'], 60))
            screen.blit(sel_surf, (sel_x, sel_y))

            # Selection border
            screen.draw_rect( self.colors['accent'],
                             (sel_x, sel_y, sel_w, sel_h), 3)

        screen.set_clip(None)

        # Selection info
        sel_y = self.palette_y + 45
        min_x, max_x, min_y, max_y = self._get_selection_bounds()
        sel_width = max_x - min_x + 1
        sel_height = max_y - min_y + 1
        if sel_width > 1 or sel_height > 1:
            sel_text = f"Selection: {sel_width}x{sel_height}"
            sel_surf = self.font_small.render(sel_text, True, self.colors['selection'])
            screen.blit(sel_surf, (self.palette_x + 20, sel_y))

            # Tell the person right here whether 'N' will animate or un-animate
            # this exact selection — no need to remember what the dot meant.
            if not self.fps_input_active:
                if tileset.is_tile_animated(min_x, min_y):
                    hint_text, hint_color = "Animated \u2014 press N to remove", self.colors['accent']
                else:
                    hint_text, hint_color = "Press N to animate this selection", self.colors['text_dim']
                hint_surf = self.font_small.render(hint_text, True, hint_color)
                screen.blit(hint_surf, (self.palette_x + 20 + sel_surf.get_width() + 12, sel_y))

        # Collision hint — shown for any selection (unlike the animation
        # hint above, which only makes sense for a multi-tile run). Reflects
        # whether every non-empty tile in the selection is already solid,
        # so it's accurate even right after a mixed selection gets toggled.
        #
        # At collision_granularity 8 with exactly one tile selected, this
        # instead reports the hovered quadrant's own state — matching what
        # 'C' will actually do in that case (see _toggle_solid_selection).
        solid_cells = [
            (tx, ty)
            for ty in range(min_y, max_y + 1)
            for tx in range(min_x, max_x + 1)
            if not tileset.is_tile_empty(tx, ty)
        ]
        if solid_cells:
            quadrant_hint = None
            if len(solid_cells) == 1 and tileset.collision_granularity == 8:
                tx, ty = solid_cells[0]
                sub = self._hovered_subtile(tx, ty)
                if sub is not None:
                    sub_x, sub_y = sub
                    if tileset.is_subtile_solid(tx, ty, sub_x, sub_y):
                        quadrant_hint = (f"Quadrant ({sub_x},{sub_y}) solid \u2014 press C to clear",
                                         (255, 100, 100))
                    else:
                        quadrant_hint = (f"Press C to mark quadrant ({sub_x},{sub_y}) solid",
                                         self.colors['text_dim'])

            if quadrant_hint is not None:
                collision_hint_text, collision_hint_color = quadrant_hint
            else:
                all_solid = all(tileset.is_tile_solid(tx, ty) for tx, ty in solid_cells)
                if all_solid:
                    collision_hint_text, collision_hint_color = "Solid \u2014 press C to clear", (255, 100, 100)
                else:
                    collision_hint_text, collision_hint_color = "Press C to mark solid (blocks movement)", self.colors['text_dim']
            collision_hint_surf = self.font_small.render(collision_hint_text, True, collision_hint_color)
            screen.blit(collision_hint_surf, (self.palette_x + 20, sel_y + 18))

        # Collision granularity indicator — only meaningful for a 16x16
        # tileset (an 8px tileset has nothing finer to switch to), shown
        # on its own row below the collision hint / animation prompt /
        # feedback banner (all of which share sel_y + 36) so it never
        # overlaps whichever of those happens to be showing.
        if tileset.tile_width == 16:
            granularity_text = f"Collision grid: {tileset.collision_granularity}x{tileset.collision_granularity} \u2014 press H to switch"
            granularity_surf = self.font_small.render(granularity_text, True, self.colors['text_dim'])
            screen.blit(granularity_surf, (self.palette_x + 20, sel_y + 54))

        # Inline FPS prompt while confirming a new animation
        if self.fps_input_active:
            prompt_text = f"New animation \u2014 FPS: {self.fps_input_text}_  (Enter to confirm, Esc to cancel)"
            prompt_surf = self.font_small.render(prompt_text, True, self.colors['accent'])
            screen.blit(prompt_surf, (self.palette_x + 20, sel_y + 36))

        # Brief confirmation after animating/un-animating a selection, or
        # after toggling solid/collision on a selection.
        elif self.anim_feedback_text and pygame.time.get_ticks() < self.anim_feedback_until_ms:
            fb_surf = self.font_small.render(self.anim_feedback_text, True, self.colors['success'])
            screen.blit(fb_surf, (self.palette_x + 20, sel_y + 36))

        # Controls below the palette content
        controls_y = tileset_y + self.palette_content_height + 10

        # Delete underlying checkbox
        checkbox_y = controls_y
        checkbox_x = self.palette_x + 20
        checkbox_size = 18

        checkbox_rect = pygame.Rect(checkbox_x, checkbox_y, checkbox_size, checkbox_size)
        self.ui_rects['delete_checkbox'] = checkbox_rect

        screen.draw_rect( self.colors['checkbox'], checkbox_rect)
        screen.draw_rect( self.colors['accent'], checkbox_rect, 1)

        if self.delete_underlying:
            # Draw checkmark
            screen.draw_line( self.colors['success'],
                             (checkbox_x + 3, checkbox_y + 9),
                             (checkbox_x + 7, checkbox_y + 13), 2)
            screen.draw_line( self.colors['success'],
                             (checkbox_x + 7, checkbox_y + 13),
                             (checkbox_x + 15, checkbox_y + 5), 2)

        checkbox_label = self.font_small.render("Replace tiles on same layer", True, self.colors['text_dim'])
        screen.blit(checkbox_label, (checkbox_x + 25, checkbox_y + 2))

        # "Hide this layer" checkbox — hides ONLY the currently selected layer
        # (self.current_layer); other layers keep their own independent hidden state.
        hide_checkbox_y = controls_y + 25
        hide_checkbox_x = self.palette_x + 20

        hide_checkbox_rect = pygame.Rect(hide_checkbox_x, hide_checkbox_y, checkbox_size, checkbox_size)
        self.ui_rects['hide_layer_checkbox'] = hide_checkbox_rect

        screen.draw_rect( self.colors['checkbox'], hide_checkbox_rect)
        screen.draw_rect( self.colors['accent'], hide_checkbox_rect, 1)

        if self.current_layer in self.hidden_layers:
            # Draw checkmark
            screen.draw_line( self.colors['success'],
                             (hide_checkbox_x + 3, hide_checkbox_y + 9),
                             (hide_checkbox_x + 7, hide_checkbox_y + 13), 2)
            screen.draw_line( self.colors['success'],
                             (hide_checkbox_x + 7, hide_checkbox_y + 13),
                             (hide_checkbox_x + 15, hide_checkbox_y + 5), 2)

        hide_checkbox_label = self.font_small.render("Hide this layer", True, self.colors['text_dim'])
        screen.blit(hide_checkbox_label, (hide_checkbox_x + 25, hide_checkbox_y + 2))

        # Layer controls — label on the right half, dropdown beside it
        layer_y = controls_y + 50

        layer_label = self.font_small.render("Tile Layer:", True, self.colors['text_dim'])
        screen.blit(layer_label, (self.palette_x + 340, layer_y))

        dropdown_x = self.palette_x + 420
        dropdown_y = layer_y - 5
        dropdown_width = 150
        dropdown_height = 28

        self.ui_rects['layer_dropdown'] = pygame.Rect(dropdown_x, dropdown_y, dropdown_width, dropdown_height)

        screen.draw_rect( self.colors['button'], self.ui_rects['layer_dropdown'])
        screen.draw_rect( self.colors['accent'], self.ui_rects['layer_dropdown'], 1)

        # Current layer text
        if self.layer_input_active:
            layer_display = f"Custom: {self.layer_input_text}_"
        else:
            preset_name, _ = self.LAYER_PRESETS[self.current_layer_preset_index]
            if preset_name == "Custom...":
                layer_display = f"Custom: {self.current_layer}"
            else:
                layer_display = f"{preset_name} ({self.current_layer})"

        layer_text = self.font_small.render(layer_display, True, self.colors['text'])
        text_width = layer_text.get_width()
        # Left-align when text overflows; otherwise centre it with a small indent
        if text_width > dropdown_width - 16:
            screen.blit(layer_text, (dropdown_x + 4, dropdown_y + 6))
        else:
            screen.blit(layer_text, (dropdown_x + 8, dropdown_y + 6))

        # Dropdown arrow
        arrow_x = dropdown_x + dropdown_width - 20
        arrow_y = dropdown_y + 14
        screen.draw_polygon( self.colors['text'], [
            (arrow_x, arrow_y - 4),
            (arrow_x + 8, arrow_y - 4),
            (arrow_x + 4, arrow_y + 2)
        ])

        # Draw dropdown menu if open
        if self.layer_dropdown_open:
            menu_y = dropdown_y + dropdown_height
            for i, (name, value) in enumerate(self.LAYER_PRESETS):
                option_rect = pygame.Rect(dropdown_x, menu_y + i * 25, dropdown_width, 25)
                self.ui_rects[f'layer_option_{i}'] = option_rect

                # Highlight hovered option
                mouse_pos = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
                if option_rect.collidepoint(mouse_pos):
                    screen.draw_rect( self.colors['button_hover'], option_rect)
                else:
                    screen.draw_rect( self.colors['button'], option_rect)

                screen.draw_rect( self.colors['accent'], option_rect, 1)

                display_text = name if value is None else f"{name} ({value})"
                option_text = self.font_small.render(display_text, True, self.colors['text'])
                screen.blit(option_text, (dropdown_x + 8, menu_y + i * 25 + 5))

        shadow_status = self.font_small.render(
            f"Native Shadow: {'ON' if self.native_shadow_enabled else 'OFF'}  ({round(self.native_shadow_alpha / 255 * 100)}%)",
            True, self.colors['success'] if self.native_shadow_enabled else self.colors['text_dim']
        )
        screen.blit(shadow_status, (self.palette_x + 340, layer_y + 35))
        shadow_help = self.font_small.render(
            "K: toggle  Click/drag = shadow  -/+ = opacity",
            True, self.colors['text_dim']
        )
        screen.blit(shadow_help, (self.palette_x + 340, layer_y + 53))

        # Instructions
        instructions = [
            "TAB: Switch Tileset",
            "L: Cycle Layer Presets",
            "G: Toggle Grid",
            "Arrows: Navigate Tiles",
            "Shift+Arrows: Extend Selection",
            "Click Dropdown: Choose Layer",
            "Click/Drag Palette: Select",
            "Scroll: Pan Tileset",
            "Click World: Place Pattern",
            "Right Click: Delete Tile",
            "K: Toggle Native Shadow brush (works on any layer)",
            "Shadow opacity: - / +",
            "Select frames, A: Animate",
            "\u25cf on tile = animated",
            "F2: Close Editor"
        ]

        inst_y = layer_y + 35  # sits just below the layer dropdown row
        for inst in instructions:
            inst_surf = self.font_small.render(inst, True, self.colors['text_dim'])
            screen.blit(inst_surf, (self.palette_x + 20, inst_y))
            inst_y += 18

    def draw_grid(self, screen: pygame.Surface, camera_x: int, camera_y: int,
                  room_width: int, room_height: int):
        """Overlay a grid on the world viewport matching the current
        placement-snap size (Off / 8px / 16px) — only draws lines in the
        visible frustum. Nothing is drawn while snapping is Off, since
        there's no grid to show."""
        if not self.show_grid or not self.active or self.snap_size <= 0:
            return

        step = self.snap_size

        # Room viewport is everything left of the palette panel (when it's
        # open). During continuous editor zoom, `screen` is a virtual
        # coordinate space (screen_width == real_width / zoom), while
        # palette_x is still a REAL screen coordinate. Convert the palette edge
        # into that virtual space before clipping.
        if self.palette_visible:
            zoom = max(0.001, float(getattr(self, 'editor_zoom', 1.0)))
            viewport_width = min(
                self.screen_width,
                int(math.ceil(self.palette_x / zoom))
            )
        else:
            viewport_width = self.screen_width

        visible_x_start = camera_x // RENDER_SCALE
        visible_y_start = camera_y // RENDER_SCALE
        visible_x_end = (camera_x + viewport_width) // RENDER_SCALE
        visible_y_end = (camera_y + self.screen_height) // RENDER_SCALE

        clip_rect = pygame.Rect(0, 0, viewport_width, self.screen_height)
        screen.set_clip(clip_rect)

        # Draw vertical lines
        # Use floor rather than int() to convert to a pixel column: int()
        # truncates toward zero, so lines left of world x=0 (negative
        # screen_x) get rounded the opposite way from lines to the right
        # (positive screen_x). That mismatch lands exactly on the grid cell
        # straddling the origin, shrinking it by a pixel and making its
        # bounding lines look merged/thicker than the rest of the grid.
        start_x = (visible_x_start // step) * step
        for x in range(start_x, visible_x_end + step, step):
            screen_x = (x * RENDER_SCALE) - camera_x
            if -10 <= screen_x <= viewport_width + 10:
                px = math.floor(screen_x)
                screen.draw_line( self.colors['grid'][:3],
                                 (px, 0), (px, self.screen_height), 1)

        # Draw horizontal lines
        start_y = (visible_y_start // step) * step
        for y in range(start_y, visible_y_end + step, step):
            screen_y = (y * RENDER_SCALE) - camera_y
            if -10 <= screen_y <= self.screen_height + 10:
                py = math.floor(screen_y)
                screen.draw_line( self.colors['grid'][:3],
                                 (0, py), (viewport_width, py), 1)

        screen.set_clip(None)

    def save_room_tiles(self, room_name: str, filepath: str):
        """Serialize all tiles for a room to a JSON file."""
        if room_name not in self.room_tiles:
            return

        data = {
            'room': room_name,
            'tiles': [tile.to_dict() for tile in self.room_tiles[room_name]]
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    def load_room_tiles(self, room_name: str, filepath: str):
        """Deserialize tiles for a room from a JSON file. Initialises to an empty list on any error."""
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)

            self.room_tiles[room_name] = [Tile.from_dict(tile_data) for tile_data in data['tiles']]
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            print(f"Error loading room tiles: {e}")
            self.room_tiles[room_name] = []
        self._invalidate_sorted_tiles_cache(room_name)