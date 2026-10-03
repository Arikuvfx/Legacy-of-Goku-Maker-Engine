import pygame
import pygame.gfxdraw
import os
import json
import math
from typing import List, Tuple, Optional, Set
from enum import Enum
from config.settings import RENDER_SCALE, TILE_SIZE

import dev_tools.ui_kit as uk
from config.settings import ui, ui_text


# =============================================================================
# Small vector chevron for the panel show/hide tab — same primitive
# convention (draw_line_on) as ui_kit's own icon glyphs.
# =============================================================================

def _draw_chevron_icon(surface, rect, color, left=True, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.28
    if left:
        uk.draw_line_on(surface, color, (cx + s * 0.5, cy - s), (cx - s * 0.5, cy), width)
        uk.draw_line_on(surface, color, (cx - s * 0.5, cy), (cx + s * 0.5, cy + s), width)
    else:
        uk.draw_line_on(surface, color, (cx - s * 0.5, cy - s), (cx + s * 0.5, cy), width)
        uk.draw_line_on(surface, color, (cx + s * 0.5, cy), (cx - s * 0.5, cy + s), width)


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


def detect_tile_size(image: pygame.Surface) -> int:
    """Infer tile size from image dimensions — tries 16px first, then 8px."""
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
            self.tile_width = detect_tile_size(self.image)
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
        self.show_only_active_layer = False  # when True, only current_layer is drawn ("Show only this layer" checkbox)
        self.custom_layer_value = -100
        self.delete_underlying = True
        self.layer_dropdown_open = False
        self.layer_input_active = False
        self.layer_input_text = ""
        # True right after 'Custom...' is picked: the pre-filled value gets
        # replaced by the first key typed, instead of being appended to.
        self.layer_input_fresh = False

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
        self.palette_width = ui(600)
        self.palette_x = screen_width - self.palette_width
        self.palette_y = ui(100)
        # Tallest the panel is allowed to get. The actual height (palette_height)
        # shrinks below this when the current tileset needs less room.
        # Never taller than the screen allows (940 was tuned for 1080p).
        self.palette_max_height = max(ui(300), min(ui(940), screen_height - self.palette_y - ui(10)))
        self.tileset_area_y_offset = ui(35)
        self.tileset_area_bottom_pad = ui(20)
        # Strip under the tile grid: 10px gap, two checkbox rows (18px, spaced
        # 26px apart), then the 28px tile layer row 56px below the first.
        self.palette_controls_height = ui(94)
        # palette_content_height / palette_height are properties (below): the
        # tile grid is as tall as the current tileset needs, capped so the
        # panel never exceeds palette_max_height.

        # Palette cell stays an integer multiple of the 16px tile so the scaled
        # tile art stays crisp (32 @1080p, 48 @1440p, 64 @4K, 16 @720p).
        self.grid_cell_size = max(16, round(ui(32) / 16) * 16)
        self.show_grid = True

        # Same bitmap font family + Theme colors as DevMenu / EditorToolbar's
        # own chrome, so the tileset editor reads as part of the same tool
        # family instead of a mismatched leftover panel.
        self.font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self.title_size = ui_text(16)
        self.body_size  = ui_text(11)
        self.hint_size  = ui_text(10)

        # Optional custom icon for the info ('?') badge — same PNG-override
        # convention as EditorToolbar (assets/ui/toolbar/<id>.png): drop a
        # PNG there and it replaces the procedural circle+'?' mark below.
        self._info_icon = None
        try:
            _info_img = pygame.image.load('assets/ui/toolbar/info.png').convert_alpha()
            _iw, _ih = _info_img.get_size()
            _scale = min(18 / _iw, 18 / _ih)
            self._info_icon = pygame.transform.scale(
                _info_img, (max(1, int(_iw * _scale)), max(1, int(_ih * _scale))))
        except Exception:
            pass

        # A couple of accents that don't have a direct Theme constant —
        # kept local rather than added to ui_kit since they're specific to
        # this editor's collision/selection overlays.
        self.SUCCESS   = (140, 220, 140)
        self.SELECTION = uk.Theme.KI_BLUE
        self.GRID_LINE = (58, 64, 82)

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
        self._panel_tab_w = ui(18)
        self._panel_tab_h = ui(72)
        self._hover_panel_toggle = False

        # Slide animation for the panel opening/closing — chased toward
        # 1.0 (fully open) or 0.0 (fully closed) each frame, same
        # exponential-approach pattern EditorToolbar uses for its bar.
        # Advanced from inside draw_palette() (timed off real elapsed ms)
        # since this widget has no separate per-frame update(dt) hook of
        # its own — hover state below is already resolved the same way.
        self._panel_slide_anim    = 1.0 if self.palette_visible else 0.0
        self._panel_slide_last_ms = None
        self._PANEL_SLIDE_RATE    = 9.0

        # Keybinds reference popup — toggled by the '?' info button next to
        # the palette title. Replaces the old always-on instructions footer.
        self.show_keybinds_popup = False

    @property
    def _palette_content_max_height(self) -> int:
        """The most vertical room the tile-grid box could ever get inside a
        panel that's palette_max_height tall — i.e. content height when the
        tileset fills all available space. Used both to cap
        palette_content_height and to keep the controls strip pinned to a
        fixed spot regardless of how much shorter the box actually is."""
        return (self.palette_max_height - self.tileset_area_y_offset
                - self.tileset_area_bottom_pad - self.palette_controls_height)

    @property
    def palette_content_height(self) -> int:
        """Height of the tile grid box: exactly as tall as the current
        tileset (rows * cell size) when that fits, otherwise the most the
        panel can give it (the grid then scrolls). This shrinks for a small
        tileset — but the panel itself (palette_height) does not; see
        palette_height below."""
        max_h = self._palette_content_max_height
        tileset = self.get_current_tileset()
        if tileset is None:
            return max_h
        needed = tileset.rows * self.grid_cell_size
        return max(self.grid_cell_size, min(max_h, needed))

    @property
    def palette_height(self) -> int:
        """Whole panel height. Fixed at palette_max_height so the editor's
        outer box stays a constant size — only the tile grid box inside it
        (palette_content_height) shrinks for tilesets that don't need the
        full height."""
        return self.palette_max_height

    @palette_height.setter
    def palette_height(self, value: int):
        # Assigning a height (e.g. on window resize) sets the maximum.
        self.palette_max_height = int(value)

    def toggle(self):
        """Open or close the tileset editor."""
        self.active = not self.active
        # A stroke/drag in progress when the panel is switched away would
        # never see its mouse-up (inactive editors get no input) and would
        # resume on the next open, so always start/finish clean.
        self.is_dragging = False
        self.is_erasing = False
        self.drag_start_pos = None
        self.is_palette_dragging = False
        self.show_keybinds_popup = False

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
        """Return the rect for the ◀/▶ tab that straddles the panel's left
        edge. Tracks the animated slide (_panel_slide_anim), not just the
        instant palette_visible flag, so the tab visually stays glued to
        the panel's edge as it slides in/out instead of snapping straight
        to its new spot. This also doubles as the click/hover hit-rect,
        which is what we want: the tab should be clickable where it's
        actually drawn. self.palette_x here is always the panel's settled
        resting position (draw_palette restores it after each shifted
        draw), so this is stable to call from anywhere, animating or not."""
        gap = ui(6)  # breathing room between tab and panel when panel is visible
        tx_shown  = self.palette_x - self._panel_tab_w - gap
        tx_hidden = self.screen_width - self._panel_tab_w
        tx = round(uk.lerp(tx_hidden, tx_shown, self._panel_slide_anim))
        ty = self.palette_y + (self.palette_height - self._panel_tab_h) // 2
        return pygame.Rect(tx, ty, self._panel_tab_w, self._panel_tab_h)

    def _draw_panel_toggle_tab(self, screen):
        """Render the small show/hide tab — always visible so the panel can be recalled."""
        rect = self._panel_toggle_rect()
        lit  = self._hover_panel_toggle
        bg     = uk.lerp_color((22, 25, 35), (30, 34, 46), 1.0 if lit else 0.0)
        border = uk.Theme.GOLD if lit else uk.Theme.PANEL_BORDER
        uk.draw_panel(screen, rect, bg=(*bg, 235), border=border, border_width=1,
                      radius=ui(6), shadow=False)
        chevron_color = uk.Theme.GOLD_BRIGHT if lit else uk.Theme.TEXT_MUTED
        _draw_chevron_icon(screen, rect, chevron_color, left=self.palette_visible, width=2)

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
            # Which placed tiles count as animated just changed.
            self.notify_tile_changed(None)
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
        # Which placed tiles count as animated just changed.
        self.notify_tile_changed(None)

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
        # Drop this editor's own render caches (layer-sorted list, animated
        # list, and the painting-mode background frame snapshot).
        #
        # Paint/erase invalidate these themselves, but several other paths
        # in room_editor.py (undo/redo of box-delete/paste, quick right-click
        # delete, dragging selected tiles, undoing a move) mutate room_tiles
        # directly and only call notify_tile_changed(). Without this, the
        # editor kept drawing the stale sorted list / frame snapshot, so
        # restored tiles didn't show up and deleted/moved ones lingered
        # until the game was reloaded.
        if room_name is None:
            self._sorted_tiles_cache.clear()
            self._animated_tiles_cache.clear()
            self._paint_bg_frame_cache.clear()
            self._tile_content_generation += 1
        else:
            self._invalidate_sorted_tiles_cache(room_name)

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
        tileset_x = self.palette_x + ui(20)
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
        """Returns True when the mouse is inside a named UI rect.

        Every rect in ui_rects belongs to the palette panel and is only
        refreshed while the panel is drawn. Once the panel is hidden the
        last-drawn rects go stale, so they must not be clickable then --
        otherwise a world click that happens to land there would toggle
        e.g. "Show only this layer" instead of placing a tile.
        """
        if not self.palette_visible:
            return False
        if rect_name in self.ui_rects:
            rect = self.ui_rects[rect_name]
            return rect.collidepoint(mouse_x, mouse_y)
        return False

    def _commit_layer_input(self):
        """Apply the typed custom layer. Empty / '-' input just closes the
        field and keeps the layer that was active before."""
        try:
            value = int(self.layer_input_text)
        except ValueError:
            value = None
        if value is not None:
            self.custom_layer_value = value
            self.current_layer = value
        self.layer_input_active = False
        self.layer_input_fresh = False

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
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._commit_layer_input()
                elif event.key == pygame.K_ESCAPE:
                    self.layer_input_active = False
                    self.layer_input_fresh = False
                elif event.key == pygame.K_BACKSPACE:
                    self.layer_input_fresh = False
                    self.layer_input_text = self.layer_input_text[:-1]
                else:
                    ch = event.unicode
                    if ch == '-' or (ch and ch.isascii() and ch.isdigit()):
                        # First key after opening Custom... replaces the
                        # pre-filled current value rather than appending to it.
                        if self.layer_input_fresh:
                            self.layer_input_text = ""
                            self.layer_input_fresh = False
                        if ch == '-':
                            if not self.layer_input_text:
                                self.layer_input_text = '-'
                        elif len(self.layer_input_text.lstrip('-')) < 6:
                            self.layer_input_text += ch
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

            # Real screen position of the click. While the room editor is
            # zoomed, RoomEditor._zoom_adjust_event rewrites event.pos into
            # zoom/world space, but ALL palette chrome (panel tab, layer
            # dropdown, checkboxes, info button) lives in fixed real-screen
            # coordinates. Hit-testing those against the rewritten
            # mouse_x/mouse_y meant a plain tile-placing click on the map
            # could land "inside" the Show-only-this-layer checkbox (or the
            # dropdown, etc.) and toggle it instead -- which made the layer
            # you were painting vanish until the game was reloaded.
            # mouse_x/mouse_y stay world-space and are only used for placing.
            real_x, real_y = event.dict.get('_room_editor_raw_pos', getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos()))

            # Panel show/hide toggle — checked first so it always fires
            if event.button == 1 and self._panel_toggle_rect().collidepoint(real_x, real_y):
                self.palette_visible = not self.palette_visible
                return

            if event.button == 1 and self._is_in_ui_rect(real_x, real_y, 'info_button'):
                self.show_keybinds_popup = not self.show_keybinds_popup
                return

            # While the keybinds popup is open, any other click just closes
            # it — nothing underneath (palette or world) should react to a
            # click that was really the person dismissing the popup.
            if self.show_keybinds_popup:
                self.show_keybinds_popup = False
                return

            if self._is_in_ui_rect(real_x, real_y, 'layer_dropdown'):
                self.layer_dropdown_open = not self.layer_dropdown_open
                return

            if self.layer_dropdown_open:
                for i, (name, value) in enumerate(self.LAYER_PRESETS):
                    if self._is_in_ui_rect(real_x, real_y, f'layer_option_{i}'):
                        self.current_layer_preset_index = i
                        # Picking ANY option ends a custom entry in progress —
                        # otherwise the field stays open, swallows every key,
                        # and keeps showing "Custom: ..." over the new layer.
                        self.layer_input_active = False
                        self.layer_input_fresh = False
                        if value is not None:
                            self.current_layer = value
                        else:
                            self.layer_input_active = True
                            self.layer_input_fresh = True
                            self.layer_input_text = str(self.current_layer)
                        self.layer_dropdown_open = False
                        return
                self.layer_dropdown_open = False
                return

            # Clicking anywhere else while typing a custom layer confirms it
            # (the click then goes on to do whatever it normally would).
            if self.layer_input_active and event.button in (1, 3):
                self._commit_layer_input()

            if self._is_in_ui_rect(real_x, real_y, 'delete_checkbox'):
                self.delete_underlying = not self.delete_underlying
                return

            if self._is_in_ui_rect(real_x, real_y, 'solo_layer_checkbox'):
                # Toggle "show only the selected layer" mode. It follows the
                # selection: switching layers while it's on shows just the new one.
                self.show_only_active_layer = not self.show_only_active_layer
                # Invalidate the baked tile cache so the visibility change is
                # reflected immediately — without this the old surface persists.
                self.notify_tile_changed(current_room_name)
                return

            # Pygame 1.x scroll convention: button 4 = scroll up, button 5 = scroll down
            # (MOUSEWHEEL event is preferred in newer pygame but both work)
            # Only scroll the palette when the wheel happens over the palette
            # box itself, and consume the event (return) so it doesn't also
            # fall through to the room camera pan handler.
            if event.button in (4, 5) and self._is_in_palette(real_x, real_y):
                if event.button == 4:
                    if shift_pressed:
                        self.palette_scroll_x = max(0, self.palette_scroll_x - self.grid_cell_size)
                    else:
                        self.palette_scroll_y = max(0, self.palette_scroll_y - self.grid_cell_size)
                else:
                    tileset = self.get_current_tileset()
                    if tileset:
                        if shift_pressed:
                            max_scroll_x = max(0, tileset.cols * self.grid_cell_size - self.palette_width + ui(40))
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
            # (real_x / real_y are computed once at the top of this branch.)

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
        tileset_x = self.palette_x + ui(20)
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
            uk.draw_rect_on(
                screen, uk.Theme.GOLD,
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
                uk.draw_rect_on(screen, uk.Theme.GOLD,
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
        When the "Show only this layer" checkbox is on (self.show_only_active_layer), every
        layer except current_layer is skipped entirely. Otherwise the active editing layer
        (current_layer) is always drawn at full opacity; every other layer is always dimmed, so surrounding layers stay
        visible as context without obscuring what's being edited. Uses the tileset's
        scaled/dimmed surface caches to avoid per-frame transform.scale() calls.

        The background pass splits static tiles from animated ones. Static
        tiles are baked into a cached composite (see _paint_bg_frame_cache)
        and reused with a single opaque blit as long as the camera, layer
        selection, show-only flag, and tile data haven't changed — no
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
                self.current_layer, self.show_only_active_layer,
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

        # The active layer is never hidden, so it's always the dimming reference.
        active_layer_is_visible = True

        for tile in self._get_sorted_tiles(room_name):
            # In "show only this layer" mode every other layer is skipped
            # entirely — unlike dimming, this is a full hide.
            if self.show_only_active_layer and tile.layer != self.current_layer:
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
        active_layer_is_visible = True

        for tile in self._get_animated_tiles(room_name):
            if self.show_only_active_layer and tile.layer != self.current_layer:
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

    # -------------------------------------------------------------------------
    # Clip-safe drawing helpers for the palette's tile grid.
    #
    # screen.set_clip() only reliably affects direct surface blits; the uk.*
    # draw helpers (rects, lines, blit_surface) can paint straight past it, so
    # anything drawn on top of the tileset image (selection, solid hatching,
    # animation badges, grid lines) was leaking out of the tile area when the
    # tileset was scrolled. These clip the geometry themselves instead.
    # -------------------------------------------------------------------------

    @staticmethod
    def _blit_clipped(screen, surf, pos, clip):
        dest = pygame.Rect(pos, surf.get_size())
        vis = dest.clip(clip)
        if vis.w <= 0 or vis.h <= 0:
            return
        area = vis.move(-dest.x, -dest.y)
        uk.blit_surface(screen, surf.subsurface(area), vis.topleft, transient=True)

    @staticmethod
    def _rect_border_clipped(screen, color, rect, width, clip):
        """Inset border drawn as four strips, each clipped — so an edge that's
        scrolled out of view disappears instead of being redrawn on the clip edge."""
        r = pygame.Rect(rect)
        strips = ((r.x, r.y, r.w, width), (r.x, r.bottom - width, r.w, width),
                  (r.x, r.y, width, r.h), (r.right - width, r.y, width, r.h))
        for strip in strips:
            vis = pygame.Rect(strip).clip(clip)
            if vis.w > 0 and vis.h > 0:
                uk.draw_rect_on(screen, color, vis, 0, 0)

    @staticmethod
    def _axis_line_clipped(screen, color, a, b, clip):
        """1px horizontal or vertical line, trimmed to the clip rect."""
        (x0, y0), (x1, y1) = a, b
        if y0 == y1:
            if not (clip.top <= y0 < clip.bottom):
                return
            xa, xb = max(min(x0, x1), clip.left), min(max(x0, x1), clip.right)
            if xa < xb:
                uk.draw_line_on(screen, color, (xa, y0), (xb, y0), 1)
        else:
            if not (clip.left <= x0 < clip.right):
                return
            ya, yb = max(min(y0, y1), clip.top), min(max(y0, y1), clip.bottom)
            if ya < yb:
                uk.draw_line_on(screen, color, (x0, ya), (x0, yb), 1)

    def draw_palette(self, screen: pygame.Surface):
        """Draw the tileset palette UI with layer controls."""
        if not self.active:
            return

        # Update hover state for the toggle tab
        mx, my = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        self._hover_panel_toggle = self._panel_toggle_rect().collidepoint(mx, my)
        uk.register_hoverable(self._panel_toggle_rect())

        # Advance the panel's open/close slide toward its target (chased,
        # not a fixed-duration tween — same style as EditorToolbar's bar).
        now_ms = pygame.time.get_ticks()
        dt = 0.0
        if self._panel_slide_last_ms is not None:
            dt = min(0.05, max(0.0, (now_ms - self._panel_slide_last_ms) / 1000.0))
        self._panel_slide_last_ms = now_ms
        target_slide = 1.0 if self.palette_visible else 0.0
        self._panel_slide_anim += (target_slide - self._panel_slide_anim) * min(1.0, dt * self._PANEL_SLIDE_RATE)
        if abs(target_slide - self._panel_slide_anim) < 0.001:
            self._panel_slide_anim = target_slide

        # Always draw the toggle tab so the panel can be recalled when hidden
        self._draw_panel_toggle_tab(screen)

        # Keep drawing the panel for as long as it's still sliding, even
        # after palette_visible has already flipped to False — otherwise
        # it would vanish instantly the moment the tab is clicked, before
        # the slide even starts. palette_visible itself still flips
        # immediately (unchanged), so anything that reads it for hit-
        # testing is unaffected — only the drawn frame lags behind.
        if self._panel_slide_anim <= 0.001:
            uk.update_hover_cursor((mx, my))
            return

        tileset = self.get_current_tileset()
        if not tileset:
            uk.update_hover_cursor((mx, my))
            return

        # Slide offset: 0 fully open, palette_width fully closed (panel
        # pushed entirely past the right edge). Every position below —
        # including inside the _draw_palette_* helpers this calls — is
        # already computed from self.palette_x, so temporarily shifting it
        # moves the whole panel as one unit. Drawn directly to `screen` in
        # a single pass, same as before; no intermediate offscreen surface
        # (that caused visible seams and a glow halo on the toolbar bar).
        dx = round(self.palette_width * (1.0 - self._panel_slide_anim))
        self.palette_x += dx

        palette_rect = pygame.Rect(self.palette_x, self.palette_y,
                                   self.palette_width, self.palette_height)

        uk.draw_panel(screen, palette_rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=2, radius=uk.Theme.RADIUS_PANEL, shadow=True)

        # Title
        title_s = self.font.render(f"Tileset: {tileset.name}", color=uk.Theme.GOLD, height=self.title_size)
        uk.blit_surface(screen, title_s, (self.palette_x + ui(16), self.palette_y + ui(10)), transient=True)

        # Info button — small '?' badge right after the title that opens the
        # full keybinds reference (see _draw_keybinds_popup).
        info_d = ui(18)
        info_x = self.palette_x + ui(16) + title_s.get_width() + ui(14)
        info_y = self.palette_y + ui(10) + (title_s.get_height() - info_d) // 2
        info_rect = pygame.Rect(info_x, info_y, info_d, info_d)
        self.ui_rects['info_button'] = info_rect
        uk.register_hoverable(info_rect)
        self._draw_info_button(screen, info_rect)

        # Tileset dimensions, right-aligned so it never collides with the title
        dims_text = f"{tileset.cols}x{tileset.rows} tiles ({tileset.tile_width}px)"
        dims_s = self.font.render(dims_text, color=uk.Theme.TEXT_MUTED, height=self.body_size)
        uk.blit_surface(screen, dims_s,
                        (self.palette_x + self.palette_width - dims_s.get_width() - ui(16), self.palette_y + ui(15)),
                        transient=True)

        # Tile grid — a recessed card within the panel
        tileset_y = self.palette_y + self.tileset_area_y_offset
        tileset_x = self.palette_x + ui(20)

        clip_rect = pygame.Rect(tileset_x, tileset_y,
                                self.palette_width - ui(40), self.palette_content_height)
        uk.draw_rect_on(screen, uk.Theme.CARD_BG, clip_rect, 0, ui(6))
        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER, clip_rect, 1, ui(6))
        screen.set_clip(clip_rect)

        if tileset.image:
            uk.register_hoverable(clip_rect)
            scaled_width = tileset.cols * self.grid_cell_size
            scaled_height = tileset.rows * self.grid_cell_size
            scaled_tileset = pygame.transform.scale(tileset.image, (scaled_width, scaled_height))

            draw_x = tileset_x - self.palette_scroll_x
            draw_y = tileset_y - self.palette_scroll_y
            screen.blit(scaled_tileset, (draw_x, draw_y))

            # Grid overlay (lines trimmed to the visible tile area)
            for row in range(tileset.rows + 1):
                y = draw_y + row * self.grid_cell_size
                self._axis_line_clipped(screen, self.GRID_LINE,
                                        (draw_x, y), (draw_x + scaled_width, y), clip_rect)
            for col in range(tileset.cols + 1):
                x = draw_x + col * self.grid_cell_size
                self._axis_line_clipped(screen, self.GRID_LINE,
                                        (x, draw_y), (x, draw_y + scaled_height), clip_rect)

            # Mark animated anchor tiles with a small gold badge so they're
            # identifiable at a glance while browsing the palette. A badge that
            # would be cut by the edge of the tile area is skipped.
            for (anim_tx, anim_ty) in tileset.tile_animations:
                if not (0 <= anim_tx < tileset.cols and 0 <= anim_ty < tileset.rows):
                    continue
                badge_x = draw_x + anim_tx * self.grid_cell_size + self.grid_cell_size - ui(9)
                badge_y = draw_y + anim_ty * self.grid_cell_size + 2
                if not clip_rect.contains(pygame.Rect(badge_x - ui(5), badge_y - ui(5), ui(10), ui(10))):
                    continue
                uk.draw_circle_on(screen, uk.Theme.GOLD, (badge_x, badge_y), ui(5))
                uk.draw_circle_on(screen, (14, 17, 25), (badge_x, badge_y), ui(5), 1)

            # Mark solid (collision) tiles with a hatch overlay — the same
            # "red = blocks movement" language the room editor's own
            # collision walls use, in the Theme's danger tone.
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
                    if not pygame.Rect(cell_x, cell_y, half, half).colliderect(clip_rect):
                        continue
                    hatch = pygame.Surface((half, half), pygame.SRCALPHA)
                    hatch.fill((*uk.Theme.DANGER, 60))
                    for i in range(-half, half * 2, 6):
                        pygame.draw.line(hatch, (*uk.Theme.DANGER_BRIGHT, 130), (i, 0), (i + half, half), 1)
                    self._blit_clipped(screen, hatch, (cell_x, cell_y), clip_rect)
                    self._rect_border_clipped(screen, uk.Theme.DANGER,
                                              (cell_x, cell_y, half, half), 1, clip_rect)
                # Faint quadrant divider on every 16x16 tile (not just solid
                # ones) so it's clear at a glance the palette is in 8x8
                # collision mode and where each tile's quarter boundaries
                # fall, before anything's even been painted solid yet.
                cell = self.grid_cell_size
                for row in range(tileset.rows):
                    for col in range(tileset.cols):
                        cx = draw_x + col * cell
                        cy = draw_y + row * cell
                        if not pygame.Rect(cx, cy, cell, cell).colliderect(clip_rect):
                            continue
                        self._axis_line_clipped(screen, uk.Theme.PANEL_BORDER,
                                                (cx + half, cy), (cx + half, cy + cell), clip_rect)
                        self._axis_line_clipped(screen, uk.Theme.PANEL_BORDER,
                                                (cx, cy + half), (cx + cell, cy + half), clip_rect)
            else:
                for (solid_tx, solid_ty) in tileset.solid_tiles:
                    if not (0 <= solid_tx < tileset.cols and 0 <= solid_ty < tileset.rows):
                        continue
                    cell_x = draw_x + solid_tx * self.grid_cell_size
                    cell_y = draw_y + solid_ty * self.grid_cell_size
                    size = self.grid_cell_size
                    if not pygame.Rect(cell_x, cell_y, size, size).colliderect(clip_rect):
                        continue
                    hatch = pygame.Surface((size, size), pygame.SRCALPHA)
                    hatch.fill((*uk.Theme.DANGER, 60))
                    for i in range(-size, size * 2, 6):
                        pygame.draw.line(hatch, (*uk.Theme.DANGER_BRIGHT, 130), (i, 0), (i + size, size), 1)
                    self._blit_clipped(screen, hatch, (cell_x, cell_y), clip_rect)
                    self._rect_border_clipped(screen, uk.Theme.DANGER,
                                              (cell_x, cell_y, size, size), 1, clip_rect)

            # Selection rectangle — clipped to the tile area so it can't
            # spill into the rest of the panel / world when scrolled away.
            min_x, max_x, min_y, max_y = self._get_selection_bounds()
            sel_width = max_x - min_x + 1
            sel_height = max_y - min_y + 1
            sel_x = draw_x + min_x * self.grid_cell_size
            sel_y = draw_y + min_y * self.grid_cell_size
            sel_w = sel_width * self.grid_cell_size
            sel_h = sel_height * self.grid_cell_size
            sel_rect = pygame.Rect(sel_x, sel_y, sel_w, sel_h)

            if sel_rect.colliderect(clip_rect):
                sel_surf = pygame.Surface((sel_w, sel_h), pygame.SRCALPHA)
                sel_surf.fill((*self.SELECTION, 70))
                self._blit_clipped(screen, sel_surf, (sel_x, sel_y), clip_rect)
                self._rect_border_clipped(screen, uk.Theme.GOLD, sel_rect, 3, clip_rect)

        screen.set_clip(None)

        # Selection / status info, overlaid in the top-left corner of the
        # grid — a compact HUD readout rather than a separate panel.
        sel_y = self.palette_y + ui(46)
        min_x, max_x, min_y, max_y = self._get_selection_bounds()
        sel_width = max_x - min_x + 1
        sel_height = max_y - min_y + 1
        if sel_width > 1 or sel_height > 1:
            # Tell the person right here whether 'N' will animate or un-animate
            # this exact selection — no need to remember what the dot meant.
            if not self.fps_input_active:
                if tileset.is_tile_animated(min_x, min_y):
                    hint_text, hint_color = "Animated - press N to remove", uk.Theme.GOLD
                else:
                    hint_text, hint_color = "Press N to animate this selection", uk.Theme.TEXT_DIM
                hint_s = self.font.render(hint_text, color=hint_color, height=self.body_size)
                uk.blit_surface(screen, hint_s, (self.palette_x + ui(20), sel_y), transient=True)

        # Inline FPS prompt while confirming a new animation
        if self.fps_input_active:
            prompt_text = f"New animation - FPS: {self.fps_input_text}_  (Enter to confirm, Esc to cancel)"
            prompt_s = self.font.render(prompt_text, color=uk.Theme.GOLD, height=self.body_size)
            uk.blit_surface(screen, prompt_s, (self.palette_x + ui(20), sel_y + ui(18)), transient=True)

        # Brief confirmation after animating/un-animating a selection, or
        # after toggling solid/collision on a selection.
        elif self.anim_feedback_text and pygame.time.get_ticks() < self.anim_feedback_until_ms:
            fb_s = self.font.render(self.anim_feedback_text, color=self.SUCCESS, height=self.body_size)
            uk.blit_surface(screen, fb_s, (self.palette_x + ui(20), sel_y + ui(18)), transient=True)

        # Controls strip: two checkboxes, then the tile layer row. Pinned to
        # a fixed offset from the panel top (based on the *max* content
        # height, not the current tileset's possibly-shorter box), so the
        # strip stays put and the panel's bottom doesn't move even though
        # the tile grid box above it shrinks for small tilesets. The layer
        # popup is drawn last so it sits on top.
        controls_y = tileset_y + self._palette_content_max_height + ui(10)
        self._draw_palette_checkboxes(screen, controls_y)
        self._draw_layer_row(screen, controls_y + ui(56))
        self._draw_layer_dropdown_popup(screen)
        self._draw_keybinds_popup(screen)  # centered modal — reads screen_width/height, not palette_x, so it isn't affected by the shift above

        self.palette_x -= dx  # restore — the shift above was only for this draw pass

        # Resolve the frame's cursor last, now that every clickable rect
        # drawn above (toggle tab, info button, tile grid) has had a chance
        # to register itself via register_hoverable.
        uk.update_hover_cursor((mx, my))

    def _draw_checkbox(self, screen: pygame.Surface, x: int, y: int, checked: bool, label: str) -> pygame.Rect:
        """One labelled checkbox; returns its rect for click hit-testing."""
        rect = pygame.Rect(x, y, ui(18), ui(18))
        uk.draw_rect_on(screen, uk.Theme.CARD_BG, rect, 0, ui(4))
        uk.draw_rect_on(screen, uk.Theme.GOLD if checked else uk.Theme.PANEL_BORDER, rect, 1, ui(4))
        if checked:
            uk.draw_line_on(screen, self.SUCCESS, (x + ui(3), y + ui(9)), (x + ui(7), y + ui(13)), 2)
            uk.draw_line_on(screen, self.SUCCESS, (x + ui(7), y + ui(13)), (x + ui(15), y + ui(5)), 2)
        label_s = self.font.render(label, color=uk.Theme.TEXT_MUTED, height=self.body_size)
        uk.blit_surface(screen, label_s, (x + ui(25), y + ui(3)), transient=True)
        uk.register_hoverable(rect)
        return rect

    def _draw_palette_checkboxes(self, screen: pygame.Surface, controls_y: int):
        """The two checkboxes under the tile grid."""
        checkbox_x = self.palette_x + ui(20)
        self.ui_rects['delete_checkbox'] = self._draw_checkbox(
            screen, checkbox_x, controls_y, self.delete_underlying, "Replace tiles on same layer")

        # "Show only this layer" checkbox — draws ONLY the currently selected
        # layer (self.current_layer) and hides all others while checked.
        self.ui_rects['solo_layer_checkbox'] = self._draw_checkbox(
            screen, checkbox_x, controls_y + ui(26),
            self.show_only_active_layer, "Show only this layer")

    def _draw_layer_row(self, screen: pygame.Surface, layer_row_y: int):
        """'Tile Layer:' label + dropdown button. Registers ui_rects['layer_dropdown']."""
        # Layer controls — label + dropdown button
        layer_label_s = self.font.render("Tile Layer:", color=uk.Theme.TEXT_MUTED, height=self.body_size)
        uk.blit_surface(screen, layer_label_s, (self.palette_x + ui(20), layer_row_y + ui(7)), transient=True)

        dropdown_x = self.palette_x + ui(130)
        dropdown_y = layer_row_y
        dropdown_width = ui(200)
        dropdown_height = ui(28)

        self.ui_rects['layer_dropdown'] = pygame.Rect(dropdown_x, dropdown_y, dropdown_width, dropdown_height)
        uk.draw_panel(screen, self.ui_rects['layer_dropdown'], bg=uk.Theme.CARD_BG,
                      border=uk.Theme.GOLD if self.layer_dropdown_open else uk.Theme.PANEL_BORDER,
                      border_width=1, radius=ui(6), shadow=False)
        uk.register_hoverable(self.ui_rects['layer_dropdown'])

        if self.layer_input_active:
            layer_display = f"Custom: {self.layer_input_text}_"
        else:
            preset_name, _ = self.LAYER_PRESETS[self.current_layer_preset_index]
            if preset_name == "Custom...":
                layer_display = f"Custom: {self.current_layer}"
            else:
                layer_display = f"{preset_name} ({self.current_layer})"

        layer_h = self.body_size
        max_text_w = dropdown_width - ui(30)
        while layer_h > 7 and self.font.size(layer_display, height=layer_h)[0] > max_text_w:
            layer_h -= 1
        layer_text_s = self.font.render(
            layer_display,
            color=uk.Theme.GOLD if self.layer_input_active else uk.Theme.TEXT_PRIMARY,
            height=layer_h)
        uk.blit_surface(screen, layer_text_s,
                        (dropdown_x + ui(10), dropdown_y + (dropdown_height - layer_text_s.get_height()) // 2),
                        transient=True)

        # Dropdown chevron
        ax, ay = dropdown_x + dropdown_width - ui(18), dropdown_y + dropdown_height // 2
        chevron_color = uk.Theme.GOLD if self.layer_dropdown_open else uk.Theme.TEXT_MUTED
        if self.layer_dropdown_open:
            uk.draw_line_on(screen, chevron_color, (ax - ui(5), ay + 2), (ax, ay - ui(3)), 2)
            uk.draw_line_on(screen, chevron_color, (ax, ay - ui(3)), (ax + ui(5), ay + 2), 2)
        else:
            uk.draw_line_on(screen, chevron_color, (ax - ui(5), ay - 2), (ax, ay + ui(3)), 2)
            uk.draw_line_on(screen, chevron_color, (ax, ay + ui(3)), (ax + ui(5), ay - 2), 2)

    def _draw_layer_dropdown_popup(self, screen: pygame.Surface):
        """The layer dropdown's option list. Drawn last so it sits above the
        rest of the panel. The dropdown sits at the bottom of the panel, so the
        list opens upward (over the tile grid) instead of falling off-screen."""
        if not self.layer_dropdown_open:
            return
        drop = self.ui_rects.get('layer_dropdown')
        if drop is None:
            return

        option_h = ui(26)
        menu_h = len(self.LAYER_PRESETS) * option_h
        menu_y = drop.y - menu_h
        if menu_y < 0:  # not enough room above (very short panel) -> open downward
            menu_y = drop.bottom
        mouse_pos = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        for i, (name, value) in enumerate(self.LAYER_PRESETS):
            option_rect = pygame.Rect(drop.x, menu_y + i * option_h, drop.w, option_h)
            self.ui_rects[f'layer_option_{i}'] = option_rect
            uk.register_hoverable(option_rect)

            hovered = option_rect.collidepoint(mouse_pos)
            uk.draw_panel(screen, option_rect,
                          bg=uk.Theme.CARD_BG_HOVER if hovered else uk.Theme.CARD_BG,
                          border=uk.Theme.GOLD if hovered else uk.Theme.PANEL_BORDER,
                          border_width=1, radius=ui(4), shadow=False)

            display_text = name if value is None else f"{name} ({value})"
            option_color = uk.Theme.GOLD if hovered else uk.Theme.TEXT_PRIMARY
            option_s = self.font.render(display_text, color=option_color, height=self.body_size)
            uk.blit_surface(screen, option_s,
                            (drop.x + ui(10), option_rect.y + (option_h - option_s.get_height()) // 2),
                            transient=True)

    def _draw_info_button(self, screen: pygame.Surface, rect: pygame.Rect):
        """Small circular badge that toggles the keybinds popup. Uses a
        custom icon from assets/ui/toolbar/info.png when present, falling
        back to the procedural '?' mark otherwise."""
        mouse_pos = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        hovered = rect.collidepoint(mouse_pos)
        center = rect.center
        radius = rect.width // 2

        if self.show_keybinds_popup:
            fill = uk.Theme.GOLD
            mark_color = (14, 17, 25)
        else:
            fill = (40, 44, 58) if hovered else (28, 31, 42)
            mark_color = uk.Theme.GOLD_BRIGHT if hovered else uk.Theme.TEXT_MUTED

        uk.draw_circle_on(screen, fill, center, radius)
        uk.draw_circle_on(screen, uk.Theme.GOLD, center, radius, 1)

        if self._info_icon:
            uk.blit_surface(screen, self._info_icon,
                            self._info_icon.get_rect(center=center), transient=True)
        else:
            mark_s = self.font.render("?", color=mark_color, height=self.body_size)
            uk.blit_surface(screen, mark_s,
                            (center[0] - mark_s.get_width() // 2, center[1] - mark_s.get_height() // 2),
                            transient=True)

    def _draw_keybinds_popup(self, screen: pygame.Surface):
        """Full keybind reference, opened from the '?' info button. Drawn last,
        over a dimmed backdrop, so it reads as a modal overlay above the whole
        panel (and the world beneath it). Any click while it's open closes it
        (handled in the event loop) — this method only ever draws."""
        if not self.show_keybinds_popup:
            return

        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((8, 9, 13, 170))
        screen.blit(overlay, (0, 0))

        sections = [
            ("Tileset editor", [
                ("Click / Drag Palette", "Select tiles"),
                ("Ctrl + Click", "Extend selection"),
                ("TAB", "Switch tileset"),
                ("L", "Swap layer"),
                ("Scroll / Shift+Scroll", "Pan tileset"),
            ]),
            ("Painting", [
                ("Click", "Select tiles"),
                ("Right Click", "Delete tiles"),
            ]),
            ("Other", [
                ("N", "Animate current selection"),
                ("C", "Set collision per tile"),
                ("C + Scroll", "Set collision granularity"),
                ("K", "Toggle shadow"),
                ("F2", "Close editor"),
            ]),
        ]

        row_h = ui(20)
        section_gap = ui(14)
        header_h = ui(50)
        margin_x = ui(20)
        key_indent = ui(10)       # key label offset from the left margin
        col_gap = ui(18)          # gap between the key column and the desc column
        right_pad = ui(20)

        # Cache rendered surfaces so widths are measured once and reused for
        # both sizing the panel and drawing it (avoids re-deriving layout
        # constants from guessed pixel widths, which caused the overflow /
        # overlap in the old fixed-width layout).
        key_surfaces = []
        desc_surfaces = []
        for _, rows in sections:
            for key_label, desc in rows:
                key_surfaces.append(self.font.render(key_label, color=uk.Theme.GOLD, height=self.body_size))
                desc_surfaces.append(self.font.render(desc, color=uk.Theme.TEXT_PRIMARY, height=self.body_size))

        key_col_w = max(s.get_width() for s in key_surfaces) + col_gap
        max_desc_w = max(s.get_width() for s in desc_surfaces)

        title_s = self.font.render("Keybinds", color=uk.Theme.GOLD, height=self.title_size)
        close_s = self.font.render("Click anywhere to close", color=uk.Theme.TEXT_DIM, height=self.hint_size)

        content_w = key_indent + key_col_w + max_desc_w + right_pad
        header_w = title_s.get_width() + ui(24) + close_s.get_width()
        panel_w = max(360, margin_x * 2 + max(content_w, header_w))

        # Every section header line also consumes a row, so count one extra
        # row per section on top of its keybind rows.
        content_rows = sum(1 + len(rows) for _, rows in sections)
        panel_h = header_h + content_rows * row_h + len(sections) * section_gap + ui(16)

        panel_x = (self.screen_width - panel_w) // 2
        panel_y = max(ui(30), (self.screen_height - panel_h) // 2)
        panel_rect = pygame.Rect(panel_x, panel_y, panel_w, panel_h)

        uk.draw_panel(screen, panel_rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=2, radius=uk.Theme.RADIUS_PANEL, shadow=True)

        uk.blit_surface(screen, title_s, (panel_x + margin_x, panel_y + ui(14)), transient=True)
        uk.blit_surface(screen, close_s,
                        (panel_x + panel_w - close_s.get_width() - margin_x, panel_y + ui(20)), transient=True)

        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER,
                        (panel_x + ui(16), panel_y + header_h - ui(10), panel_w - ui(32), 1), 0, 0)

        y = panel_y + header_h
        i = 0
        for section_name, rows in sections:
            section_s = self.font.render(section_name, color=uk.Theme.TEXT_MUTED, height=self.body_size)
            uk.blit_surface(screen, section_s, (panel_x + margin_x, y), transient=True)
            y += row_h
            for _ in rows:
                key_s = key_surfaces[i]
                desc_s = desc_surfaces[i]
                i += 1
                uk.blit_surface(screen, key_s, (panel_x + margin_x + key_indent, y), transient=True)
                uk.blit_surface(screen, desc_s,
                                (panel_x + margin_x + key_indent + key_col_w, y), transient=True)
                y += row_h
            y += section_gap

    def _draw_palette_controls(self, screen: pygame.Surface, controls_y: int):
        """PARKED — not currently called. What's left of the old menu that
        lived below the tile grid: native shadow status and the instructions
        footer. Kept intact so options can be re-added bit by bit.

        controls_y is the same anchor the checkboxes use; this lays out from
        the layer row (controls_y + 56) downward, so the tile grid needs to be
        shortened (palette_controls_height) to make room before it's called.
        """
        layer_row_y = controls_y + ui(56)

        # ── Native shadow status ─────────────────────────────────────────────
        shadow_y = layer_row_y + ui(36)
        shadow_status_s = self.font.render(
            f"Native Shadow: {'ON' if self.native_shadow_enabled else 'OFF'} ({round(self.native_shadow_alpha / 255 * 100)}%)",
            color=self.SUCCESS if self.native_shadow_enabled else uk.Theme.TEXT_DIM, height=self.body_size)
        uk.blit_surface(screen, shadow_status_s, (self.palette_x + ui(20), shadow_y), transient=True)
        shadow_help_s = self.font.render(
            "K: toggle - Click/drag = shadow - -/+ = opacity",
            color=uk.Theme.TEXT_DIM, height=self.hint_size)
        uk.blit_surface(screen, shadow_help_s, (self.palette_x + ui(20), shadow_y + ui(16)), transient=True)

        # ── Instructions footer — two compact columns below a hairline ─────
        divider_y = shadow_y + ui(36)
        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER,
                        (self.palette_x + ui(16), divider_y, self.palette_width - ui(32), 1), 0, 0)

        instructions = [
            "TAB: Switch Tileset", "L: Cycle Layer Presets", "G: Toggle Grid",
            "Arrows: Navigate Tiles", "Shift+Arrows: Extend Selection",
            "Click Dropdown: Choose Layer", "Click/Drag Palette: Select", "Scroll: Pan Tileset",
            "Click World: Place Pattern", "Right Click: Delete Tile",
            "K: Toggle Native Shadow (any layer)", "N: Animate Selection",
            "C: Toggle Solid", "H: Collision Granularity", "F2: Close Editor",
        ]
        col_w = (self.palette_width - ui(40)) // 2
        rows_per_col = (len(instructions) + 1) // 2
        inst_y0 = divider_y + ui(10)
        for i, inst in enumerate(instructions):
            col = i // rows_per_col
            row = i % rows_per_col
            inst_s = self.font.render(inst, color=uk.Theme.TEXT_DIM, height=self.hint_size)
            uk.blit_surface(screen, inst_s,
                            (self.palette_x + ui(20) + col * col_w, inst_y0 + row * ui(15)), transient=True)

    def draw_grid(self, screen: pygame.Surface, camera_x: int, camera_y: int,
                  room_width: int, room_height: int):
        """Overlay a grid on the world viewport matching the current
        placement-snap size (Off / 8px / 16px) — only draws lines in the
        visible frustum. Nothing is drawn while snapping is Off, since
        there's no grid to show."""
        if not self.show_grid or not self.active or self.snap_size <= 0:
            return

        step = self.snap_size

        # Draw the grid across the full viewport, including the area behind
        # the palette panel. The panel is drawn on top afterwards, so the
        # grid stays visible through it instead of being cut off at its edge.
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
            if -10 <= screen_x <= viewport_width + ui(10):
                px = math.floor(screen_x)
                uk.draw_line_on(screen, self.GRID_LINE,
                                (px, 0), (px, self.screen_height), 1)

        # Draw horizontal lines
        start_y = (visible_y_start // step) * step
        for y in range(start_y, visible_y_end + step, step):
            screen_y = (y * RENDER_SCALE) - camera_y
            if -10 <= screen_y <= self.screen_height + ui(10):
                py = math.floor(screen_y)
                uk.draw_line_on(screen, self.GRID_LINE,
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