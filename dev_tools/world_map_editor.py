"""
dev_tools/world_map_editor.py

Standalone world-map editor. Accessible from the developer menu below the room editor.

Features
--------
* Create / switch between multiple named world maps (2896 × 2104 tiles).
* Paint 8 × 8 tiles from any tileset found in assets/tilesets/world_map/.
* Place named "location" pins on the map and assign them to in-game rooms.
* Paint the Scouter WORLD MAP silhouette directly (Scouter mode) — a
  designer-authored land mask, same idea as the room editor's Map Paint
  tool, instead of inferring land/water from the tile art's color palette.
* Ctrl+S saves; maps are stored as JSON in assets/world_maps/.

Controls (viewport)
-------------------
  Left-drag          Paint tiles (paint mode) / paint silhouette (scouter mode)
  Right-drag         Erase tiles (paint mode) / erase silhouette (scouter mode)
  Left-click         Place / select location pin (location mode)
  Middle-drag        Pan camera
  Scroll wheel       Zoom in / out
  TAB                Cycle tilesets (paint mode)
  [ / ]              Shrink / grow brush (scouter mode)
  G                  Toggle grid
  Ctrl+S             Save current map
  F2 / Escape        Close editor
"""

from __future__ import annotations

import json
import math
import os
from typing import Optional

import pygame

import dev_tools.ui_kit as uk

# ─────────────────────────────── constants ────────────────────────────────────

SAVE_DIR    = os.path.join("assets", "world_maps")
TILESET_DIR = os.path.join("assets", "tilesets", "world_map")

MAP_TILE_W   = 362   # map width in tiles
MAP_TILE_H   = 263   # map height in tiles
NATIVE_TILE  = 8      # source tile size in pixels
CHUNK_TILES  = 16     # tiles per side of a pre-composited chunk (see
                       # WorldMapEditor._get_chunk_surface) — the viewport
                       # draw loop blits whole chunks, not individual tiles.
CHUNK_BUILD_BUDGET_PER_FRAME = 24
                       # Max never-before-seen chunks composited in one
                       # draw() call. Normal panning only ever reveals a
                       # chunk or two at a time, but dragging the panel
                       # splitter can instantly expose a whole swath of
                       # map that's never been on screen this session —
                       # without a cap, that swath gets composited
                       # synchronously in a single frame (each chunk is up
                       # to CHUNK_TILES² individual blits), which is the
                       # stall you feel. Chunks that don't fit in this
                       # frame's budget are simply skipped (left blank)
                       # and picked up on a later frame once still
                       # uncached — see _get_chunk_surface / _draw_viewport.

ZOOM_LEVELS  = [1, 2, 3, 4, 6, 8]
ZOOM_DEFAULT = 2      # index into ZOOM_LEVELS

PANEL_W      = 320    # right-panel default/initial width in pixels
PANEL_MIN_W  = 220    # narrowest the user can drag the panel to
PANEL_MAX_W  = 640    # widest the user can drag the panel to
SPLITTER_HIT_W = 6    # width (px) of the invisible drag zone straddling the
                       # viewport/panel boundary, centered on the border line
PANEL_LAYOUT_SNAP = 8  # quantization grid (px) for panel *content* width —
                       # see panel_layout_w below
TOP_BAR_H    = 44     # top bar height in pixels
PALETTE_CELL = 24     # how large each tile appears in the palette grid
PALETTE_BOTTOM_MARGIN = 16  # padding kept below the palette panel, screen bottom

PIN_RADIUS   = 7      # location-pin draw radius

ICON_DIR    = os.path.join("assets", "map", "icons")
VEHICLE_DIR = os.path.join("assets", "map", "vehicle")

# ──────────────────────────────── data classes ────────────────────────────────

class WMTile:
    """One placed tile in a world map."""
    __slots__ = ('x', 'y', 'tileset', 'tx', 'ty')

    def __init__(self, x: int, y: int, tileset: str, tx: int, ty: int):
        self.x = x;  self.y = y
        self.tileset = tileset;  self.tx = tx;  self.ty = ty

    def to_dict(self) -> dict:
        return {'x': self.x, 'y': self.y, 'ts': self.tileset,
                'tx': self.tx, 'ty': self.ty}

    @staticmethod
    def from_dict(d: dict) -> WMTile:
        return WMTile(d['x'], d['y'], d['ts'], d['tx'], d['ty'])


class WMLocation:
    """Named map pin linked to an in-game room."""
    def __init__(self, x: int, y: int, name: str = '', room: str = '',
                 icon: str = '', height: int = 0):
        self.x = x;  self.y = y
        self.name = name;  self.room = room
        self.icon = icon   # filename stem from ICON_DIR, e.g. "town"
        self.height = int(height)  # visual altitude on the world map (0 = ground)

    def to_dict(self) -> dict:
        return {'x': self.x, 'y': self.y, 'name': self.name,
                'room': self.room, 'icon': self.icon, 'height': self.height}

    @staticmethod
    def from_dict(d: dict) -> 'WMLocation':
        return WMLocation(d['x'], d['y'], d.get('name', ''),
                          d.get('room', ''), d.get('icon', ''),
                          d.get('height', 0))


class WMEntity:
    """An entity (vehicle/NPC) that follows a path on the world map."""

    def __init__(self, name: str = 'entity', sprite: str = '',
                 path: list = None, closed: bool = False, height: int = 0,
                 room: str = ''):
        self.name   = name
        self.sprite = sprite          # filename stem from VEHICLE_DIR
        self.path: list[tuple[int, int]] = [tuple(p) for p in (path or [])]
        self.closed = closed          # True = loop; False = ping-pong
        self.height = int(height)     # visual altitude offset in pixels (0 = ground)
        self.room   = room            # in-game room to transition to on collision

    def to_dict(self) -> dict:
        return {'name': self.name, 'sprite': self.sprite,
                'path': [list(p) for p in self.path], 'closed': self.closed,
                'height': self.height, 'room': self.room}

    @staticmethod
    def from_dict(d: dict) -> 'WMEntity':
        return WMEntity(d.get('name', 'entity'), d.get('sprite', ''),
                        [tuple(p) for p in d.get('path', [])],
                        d.get('closed', False),
                        d.get('height', 0),
                        d.get('room', ''))


class WorldMap:
    """Data for one world map — multiple frames of tile dicts, plus a list of locations.

    Each frame is an independent dict keyed by (x, y).  The ``tiles`` property
    always returns the currently-active frame so all existing paint/erase code
    continues to work without modification.
    """

    def __init__(self, name: str):
        self.name       = name
        self._frames: list[dict[tuple[int, int], WMTile]] = [{}]
        self.frame_idx  = 0
        self.locations: list[WMLocation] = []
        self.entities:  list[WMEntity]   = []
        self.music      = ''   # track stem to play during the mode7 flying scene ('' = none)

        # Pre-composited CHUNK_TILES×CHUNK_TILES native-resolution chunk
        # surfaces, keyed (frame_idx, chunk_x, chunk_y) -> Surface|None
        # (None = chunk has no tiles, cached so empty chunks aren't
        # recomposed every frame either). Built lazily by
        # WorldMapEditor._get_chunk_surface() the first time a chunk is
        # visible, and invalidated per-chunk by _place_tiles/_erase_tile
        # below rather than rebuilt from scratch on every edit — see
        # those methods' comments for why this exists (the viewport used
        # to blit one texture per tile, which was fine at native zoom but
        # became 10,000+ draw calls a frame zoomed all the way out).
        self._chunk_cache: dict[tuple, Optional[pygame.Surface]] = {}

        # Designer-painted Scouter WORLD MAP silhouette — set of (tx, ty)
        # map-tile cells, same coordinate space as WMTile positions. This is
        # NOT derived from the tile art (see Scouter mode / game.py's
        # _build_world_map_scouter_surface for why): the designer paints the
        # land shape once, directly, the same way room.map_paint is painted
        # in the room editor's Map Paint tool. Not per-frame — the
        # silhouette doesn't change when the map's animated frame does.
        self.scouter_paint: set[tuple[int, int]] = set()

    # ── frame helpers ──────────────────────────────────────────────────────────

    @property
    def tiles(self) -> dict[tuple[int, int], WMTile]:
        """Active frame's tile dict (read/write — same object the editor mutates)."""
        return self._frames[self.frame_idx]

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    def add_frame(self) -> int:
        """Append a new blank frame and return its index."""
        self._frames.append({})
        return len(self._frames) - 1

    def duplicate_frame(self, src_idx: int) -> int:
        """Append a copy of frame *src_idx* and return its index."""
        copy = {k: WMTile(v.x, v.y, v.tileset, v.tx, v.ty)
                for k, v in self._frames[src_idx].items()}
        self._frames.append(copy)
        return len(self._frames) - 1

    def remove_frame(self, idx: int) -> int:
        """Delete frame *idx*. No-op if it's the only frame.
        Returns the new frame_idx to use after deletion."""
        if len(self._frames) <= 1:
            return 0
        self._frames.pop(idx)
        # Frame indices shift after a removal, which would leave stale
        # chunk-cache entries keyed under the old indices — simplest
        # correct fix is to drop the whole cache; frame add/remove/dup
        # are rare, editor-only actions, not something that needs to
        # stay fast the way painting or zooming does.
        self._chunk_cache.clear()
        return max(0, min(self.frame_idx, len(self._frames) - 1))

    def invalidate_chunk_at(self, tx: int, ty: int):
        """Drop the cached chunk surface covering tile (tx, ty) in the
        *current* frame, so the next draw recomposes it. Call this after
        any edit to wm.tiles."""
        cx, cy = tx // CHUNK_TILES, ty // CHUNK_TILES
        self._chunk_cache.pop((self.frame_idx, cx, cy), None)

    # ── serialisation ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            'name':      self.name,
            'width':     MAP_TILE_W,
            'height':    MAP_TILE_H,
            'frames':    [[t.to_dict() for t in f.values()]
                          for f in self._frames],
            'locations': [loc.to_dict() for loc in self.locations],
            'entities':  [e.to_dict() for e in self.entities],
            'music':     self.music,
            'scouter_paint': sorted([list(c) for c in self.scouter_paint]),
        }

    @staticmethod
    def from_dict(d: dict) -> 'WorldMap':
        wm = WorldMap(d.get('name', 'unnamed'))
        wm.music = d.get('music', '')
        if 'frames' in d:
            # New multi-frame format
            wm._frames = []
            for frame_data in d['frames']:
                frame: dict[tuple[int, int], WMTile] = {}
                for td in frame_data:
                    t = WMTile.from_dict(td)
                    frame[(t.x, t.y)] = t
                wm._frames.append(frame)
            if not wm._frames:
                wm._frames = [{}]
        else:
            # Legacy single-frame format (old saves with a 'tiles' key)
            frame: dict[tuple[int, int], WMTile] = {}
            for td in d.get('tiles', []):
                t = WMTile.from_dict(td)
                frame[(t.x, t.y)] = t
            wm._frames = [frame]
        for ld in d.get('locations', []):
            wm.locations.append(WMLocation.from_dict(ld))
        for ed in d.get('entities', []):
            wm.entities.append(WMEntity.from_dict(ed))
        wm.scouter_paint = {tuple(c) for c in d.get('scouter_paint', [])}
        return wm


# ─────────────────────────────── tileset loader ───────────────────────────────

class WMTileset:
    """Minimal 8 × 8 tileset loader.

    get_tile_raw() below replaces what used to be get_tile()'s
    per-(tile, zoom-level) pygame.transform.scale() cache. That cache
    is exactly the CPU cost gpu_renderer.py's docstring calls out:
    every new zoom level meant a fresh burst of transform.scale() calls
    for every tile newly visible at that size — a visible stutter the
    instant you zoomed out to a level you hadn't visited yet, and a
    _cache dict that grew one entry per (tile, zoom) pair forever.

    Now get_tile_raw() hands back the *same* native-resolution Surface
    object every time, cached once per tile regardless of zoom.
    Combined with GPUScreen.blit_scaled() (see _draw_viewport below),
    scaling happens on the GPU at draw time instead of on the CPU at
    zoom time, and — just as importantly — GPUScreen's own texture
    cache (keyed by Surface identity) uploads each tile exactly once
    for the whole session, instead of re-uploading a new scaled copy
    every time the zoom level changes.
    """

    def __init__(self, name: str, path: str):
        self.name  = name
        self.image: Optional[pygame.Surface] = None
        self.cols  = 0
        self.rows  = 0
        self._cache: dict[tuple, Optional[pygame.Surface]] = {}
        self._raw_cache: dict[tuple, Optional[pygame.Surface]] = {}
        try:
            self.image = pygame.image.load(path).convert_alpha()
            w, h = self.image.get_size()
            self.cols = w // NATIVE_TILE
            self.rows = h // NATIVE_TILE
        except Exception as exc:
            print(f"[WorldMapEditor] Could not load tileset '{name}': {exc}")

    def get_tile_raw(self, tx: int, ty: int) -> Optional[pygame.Surface]:
        """Native NATIVE_TILE×NATIVE_TILE Surface for tile (tx, ty), cached
        once (not per zoom). Scale this to whatever size you need at draw
        time via GPUScreen.blit_scaled() rather than pre-scaling here."""
        key = (tx, ty)
        if key not in self._raw_cache:
            if (self.image is None or tx < 0 or ty < 0
                    or tx >= self.cols or ty >= self.rows):
                self._raw_cache[key] = None
            else:
                rect = pygame.Rect(tx * NATIVE_TILE, ty * NATIVE_TILE,
                                   NATIVE_TILE, NATIVE_TILE)
                self._raw_cache[key] = self.image.subsurface(rect).copy()
        return self._raw_cache[key]

    def get_tile(self, tx: int, ty: int, display_size: int
                 ) -> Optional[pygame.Surface]:
        """Return a scaled surface for tile (tx, ty); cached per display_size.
        Kept for any remaining CPU-surface callers (e.g. thumbnail/export
        code that isn't drawing through GPUScreen) — the live viewport draw
        loop uses get_tile_raw() + blit_scaled() instead, see above."""
        key = (tx, ty, display_size)
        if key not in self._cache:
            raw = self.get_tile_raw(tx, ty)
            if raw is None:
                self._cache[key] = None
            else:
                self._cache[key] = pygame.transform.scale(
                    raw, (display_size, display_size))
        return self._cache[key]

    def get_palette_surface(self, cell_size: int) -> Optional[pygame.Surface]:
        """Return a cached surface of the full tileset scaled to cell_size,
        with grid lines already baked in.  Rebuilt only when cell_size changes."""
        key = ('_pal', cell_size)
        if key in self._cache:
            return self._cache[key]
        if self.image is None:
            self._cache[key] = None
            return None
        w = self.cols * cell_size
        h = self.rows * cell_size
        surf = pygame.transform.scale(self.image, (w, h)).convert_alpha()
        gc = (50, 50, 70)
        for r in range(self.rows + 1):
            pygame.draw.line(surf, gc, (0, r * cell_size), (w, r * cell_size))
        for c in range(self.cols + 1):
            pygame.draw.line(surf, gc, (c * cell_size, 0), (c * cell_size, h))
        self._cache[key] = surf
        return surf

    def invalidate_cache(self):
        self._cache.clear()


class WMVehicleSprite:
    """Loader for a vehicle sprite sheet.

    Layout: frames go to the right, directions go top-to-bottom — exactly the
    same convention as character spritesheets.  Each frame row is 32 px tall;
    each frame is either 32 or 64 px wide (auto-detected).

    Direction row order (matches character spritesheet convention):
      Row 0: down
      Row 1: left
      Row 2: right
      Row 3: up
      (8-direction sheets add: down_left, up_left, up_right, down_right in rows 4-7)
    """

    # Standard row → direction name mapping (4-dir and 8-dir variants)
    _ROWS_4 = {0: 'down', 1: 'left', 2: 'right', 3: 'up'}
    _ROWS_8 = {0: 'down', 1: 'left', 2: 'right', 3: 'up',
               4: 'down_left', 5: 'up_left', 6: 'up_right', 7: 'down_right'}
    _FRAME_H = 32  # every direction row is 32 px tall

    def __init__(self, name: str, path: str):
        self.name        = name
        self.frame_w     = 32
        self.frame_h     = self._FRAME_H
        self.num_dirs    = 1
        self.num_frames  = 1
        self._frames_by_row: dict[int, list[pygame.Surface]] = {}   # row → frames
        self._thumb_cache: dict[int, Optional[pygame.Surface]] = {}
        self._scaled_cache: dict[tuple, Optional[pygame.Surface]] = {}

        try:
            sheet = pygame.image.load(path).convert_alpha()
            sw, sh = sheet.get_size()
            fh = self._FRAME_H
            self.num_dirs = max(1, sh // fh)
            # Auto-detect frame width: prefer 32; use 64 if 32 doesn't divide evenly.
            self.frame_w  = 32 if (sw % 32 == 0) else 64
            self.frame_h  = fh
            self.num_frames = max(1, sw // self.frame_w)
            for r in range(self.num_dirs):
                row_frames = []
                for f in range(self.num_frames):
                    rect = pygame.Rect(f * self.frame_w, r * fh, self.frame_w, fh)
                    row_frames.append(sheet.subsurface(rect).copy())
                self._frames_by_row[r] = row_frames
        except Exception as exc:
            print(f"[WorldMapEditor] Could not load vehicle sprite '{name}': {exc}")

    def get_frame_raw(self, dir_row: int, frame_idx: int) -> Optional[pygame.Surface]:
        """Native-resolution frame for *dir_row*/*frame_idx*, cached once —
        no per-zoom dict. Scale to the destination size at draw time via
        GPUScreen.blit_scaled() instead of pre-scaling here (see get_frame's
        docstring note below and WMTileset.get_tile_raw for the same fix
        applied to map tiles)."""
        dir_row = max(0, min(dir_row, self.num_dirs - 1))
        row_frames = self._frames_by_row.get(dir_row, self._frames_by_row.get(0))
        if not row_frames:
            return None
        return row_frames[frame_idx % len(row_frames)]

    def get_frame(self, dir_row: int, frame_idx: int,
                  display_h: int) -> Optional[pygame.Surface]:
        """Return a scaled frame for *dir_row* (0-based) and *frame_idx*, cached.
        Kept for any remaining CPU-surface callers — the live viewport draw
        loop uses get_frame_raw() + blit_scaled() instead (same rationale as
        WMTileset.get_tile vs get_tile_raw above: this cache used to mean a
        fresh pygame.transform.scale() burst for every animated entity the
        first time you visited a new zoom level)."""
        dir_row   = max(0, min(dir_row, self.num_dirs - 1))
        row_frames = self._frames_by_row.get(dir_row, self._frames_by_row.get(0))
        if not row_frames:
            return None
        frame_idx = frame_idx % len(row_frames)
        key = (dir_row, frame_idx, display_h)
        if key not in self._scaled_cache:
            raw = row_frames[frame_idx]
            aspect = self.frame_w / self.frame_h
            new_h = max(1, display_h)
            new_w = max(1, int(new_h * aspect))
            self._scaled_cache[key] = pygame.transform.scale(raw, (new_w, new_h))
        return self._scaled_cache[key]

    def get_panel_thumb(self, size: int) -> Optional[pygame.Surface]:
        """Return a square thumbnail from row 0, frame 0 for the picker panel."""
        if size not in self._thumb_cache:
            frames = self._frames_by_row.get(0, [])
            if not frames:
                self._thumb_cache[size] = None
            else:
                self._thumb_cache[size] = pygame.transform.smoothscale(frames[0], (size, size))
        return self._thumb_cache[size]


# ─────────────────────── direction helpers ────────────────────────────────────

def _vehicle_dir_row(dx: float, dy: float, num_dirs: int) -> int:
    """Map a movement vector (dx, dy) to a spritesheet row index.

    Spritesheet row order (same as character sheets):
      4-dir:  0=down  1=left  2=right  3=up
      8-dir:  0=down  1=left  2=right  3=up
              4=down_left  5=up_left  6=up_right  7=down_right
    """
    if num_dirs <= 1:
        return 0
    angle = math.atan2(dy, dx)                         # −π … π  (0 = right, π/2 = down)
    a     = (angle + math.pi * 2) % (math.pi * 2)      # 0 … 2π
    if num_dirs >= 8:
        # 8 sectors, 45° each; clockwise from east
        sector = int((a + math.pi / 8) / (math.pi / 4)) % 8
        # sector→row mapping for: down,left,right,up,down_left,up_left,up_right,down_right
        _S2R = {0: 2, 1: 7, 2: 0, 3: 4, 4: 1, 5: 5, 6: 3, 7: 6}
        return _S2R.get(sector, 0)
    else:
        # 4 sectors, 90° each
        sector = int((a + math.pi / 4) / (math.pi / 2)) % 4
        _S2R4 = {0: 2, 1: 0, 2: 1, 3: 3}   # E→right, S→down, W→left, N→up
        return _S2R4.get(sector, 0)


# ──────────────────────────────── main editor ─────────────────────────────────

# ──────────────────────── chrome: fonts, icons ─────────────────────────────

class _BitmapFontView:
    """Adapts a BitmapFont to the plain pygame.font.Font call shape —
    render(text, antialias, color) / size(text) — at one fixed pixel height.

    Same adapter dev_menu.py / room_editor.py use for their own bitmap
    fonts: BitmapFont.render() takes a `height` keyword rather than
    pygame.font.Font's fixed per-instance size, so this pins one height per
    "font" (title/large/medium/small) and otherwise stays a drop-in swap —
    every `self.font_x.render(text, True, color)` call site below keeps
    working unchanged whether font_x is bitmap- or system-font-backed."""

    def __init__(self, bitmap_font, height):
        self._font = bitmap_font
        self._height = height

    def render(self, text, antialias=True, color=(255, 255, 255)):
        return self._font.render(text, color=color, height=self._height)

    def size(self, text):
        return self._font.size(text, height=self._height)


# Vector line-glyph icon set for the World Map Editor's own chrome (top-bar
# mode/action buttons, panel headers, list-row actions). Drawn with the same
# primitives — uk.draw_line_on / uk.draw_rect_on / uk.draw_circle_on — as
# ui_kit's own DEV_MENU_ICON_DRAWERS and room_editor.py's toolbar icons, so
# these read as part of the same family instead of a mismatched one-off.

def _draw_plus_icon(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.34
    uk.draw_line_on(surface, color, (cx - s, cy), (cx + s, cy), width)
    uk.draw_line_on(surface, color, (cx, cy - s), (cx, cy + s), width)


def _draw_check_icon(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    uk.draw_line_on(surface, color, (cx - s, cy), (cx - s * 0.15, cy + s * 0.8), width)
    uk.draw_line_on(surface, color, (cx - s * 0.15, cy + s * 0.8), (cx + s, cy - s * 0.7), width)


def _draw_brush_icon(surface, rect, color, width=2):
    """Paint-brush glyph — Paint mode."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    # Handle
    uk.draw_line_on(surface, color, (cx - s * 0.26, cy - s * 0.30),
                    (cx + s * 0.10, cy + s * 0.06), max(2, width + 1))
    # Ferrule
    uk.draw_line_on(surface, color, (cx + s * 0.02, cy - s * 0.02),
                    (cx + s * 0.20, cy + s * 0.16), width)
    # Bristle splash
    tip = (cx + s * 0.24, cy + s * 0.22)
    for dx, dy in ((-0.05, 0.14), (0.06, 0.18), (0.16, 0.12)):
        uk.draw_line_on(surface, color, tip,
                        (cx + s * dx, cy + s * dy), max(1, width - 1))


def _draw_pin_icon(surface, rect, color, width=2):
    """Teardrop map-pin glyph — Location mode."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    r = s * 0.22
    head = (cx, cy - s * 0.08)
    uk.draw_circle_on(surface, color, head, r, width)
    uk.draw_line_on(surface, color, (cx - r * 0.55, head[1] + r * 0.75),
                    (cx, cy + s * 0.34), width)
    uk.draw_line_on(surface, color, (cx + r * 0.55, head[1] + r * 0.75),
                    (cx, cy + s * 0.34), width)
    uk.draw_circle_on(surface, color, head, max(1, int(r * 0.32)))


def _draw_scouter_icon(surface, rect, color, width=2):
    """Radar-sweep glyph — Scouter (silhouette) mode."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    r = s * 0.30
    uk.draw_circle_on(surface, color, (cx, cy), r, width)
    uk.draw_circle_on(surface, color, (cx, cy), max(1, int(r * 0.28)))
    uk.draw_line_on(surface, color, (cx, cy),
                    (cx + r * 0.9, cy - r * 0.75), max(1, width - 1))


def _draw_zoom_icon(surface, rect, color, plus=True, width=2):
    """Magnifying-glass glyph with a +/- mark inside — zoom buttons."""
    cx, cy = rect.centerx - 1, rect.centery - 1
    s = min(rect.w, rect.h)
    r = s * 0.26
    uk.draw_circle_on(surface, color, (cx, cy), r, width)
    hx, hy = cx + r * 0.75, cy + r * 0.75
    uk.draw_line_on(surface, color, (hx, hy), (hx + s * 0.16, hy + s * 0.16), width + 1)
    m = r * 0.45
    uk.draw_line_on(surface, color, (cx - m, cy), (cx + m, cy), width)
    if plus:
        uk.draw_line_on(surface, color, (cx, cy - m), (cx, cy + m), width)


def _draw_chevron_icon(surface, rect, color, direction=-1, width=2):
    """Simple '‹' / '›' chevron — tab and frame scroll arrows."""
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.22
    dx = -1 if direction < 0 else 1
    uk.draw_line_on(surface, color, (cx + s * dx, cy - s * 1.3), (cx - s * dx, cy), width)
    uk.draw_line_on(surface, color, (cx - s * dx, cy), (cx + s * dx, cy + s * 1.3), width)


def _draw_music_icon(surface, rect, color, width=2):
    """Eighth-note glyph — the map-music dropdown."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    stem_x = cx + s * 0.12
    top_y = cy - s * 0.30
    bot_y = cy + s * 0.22
    uk.draw_line_on(surface, color, (stem_x, top_y), (stem_x, bot_y), width)
    uk.draw_line_on(surface, color, (stem_x, top_y), (stem_x + s * 0.20, top_y + s * 0.10), width)
    uk.draw_circle_on(surface, color, (stem_x - s * 0.10, bot_y), max(2, int(s * 0.13)))


def _draw_vehicle_icon(surface, rect, color, width=2):
    """Simple car-body glyph — Entity mode / vehicle picker fallback."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    body = pygame.Rect(0, 0, s * 0.62, s * 0.26)
    body.center = (cx, cy - s * 0.02)
    uk.draw_rect_on(surface, color, body, width, 4)
    cabin = pygame.Rect(0, 0, s * 0.32, s * 0.20)
    cabin.midbottom = (cx, body.top + 2)
    uk.draw_rect_on(surface, color, cabin, width, 3)
    for wx in (body.x + body.w * 0.22, body.x + body.w * 0.78):
        uk.draw_circle_on(surface, color, (wx, body.bottom), max(2, int(s * 0.08)), width)
_CHUNK_PENDING = object()  # sentinel: chunk isn't cached yet and this
                           # frame's CHUNK_BUILD_BUDGET_PER_FRAME is used
                           # up — try again next frame instead of building
                           # it synchronously right now.


class WorldMapEditor:
    """Full-screen world-map tile editor."""

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.active        = False

        # Viewport geometry (recomputed if panel is hidden, but kept simple here)
        self.vp_x = 0
        self.vp_y = TOP_BAR_H
        self.panel_w = PANEL_W   # user-draggable; see _splitter_rect / MOUSEMOTION handling
        # Right panel shown/collapsed — pressing the active mode's top-bar
        # button toggles it (see _on_mode_button). panel_w keeps the user's
        # dragged width while collapsed, so re-opening restores it.
        self.panel_open = True
        # Quantized copy of panel_w for sizing rounded/shadowed panel-content
        # boxes (buttons, rows, cards) — NOT for the panel background/
        # splitter line/viewport, which stay pixel-exact so the panel visibly
        # tracks the cursor 1:1 while dragging. Those rounded-rect and
        # drop-shadow surfaces in ui_kit.py are cached by their *exact*
        # pixel size, so feeding them a width that changes every single
        # pixel of mouse movement means a full cache miss (rebuild +
        # supersample/blur + fresh GPU texture upload) on every one of
        # those boxes, every frame, for the whole drag — see
        # _set_panel_w/_recompute_viewport for where this gets refreshed.
        self.panel_layout_w = self.panel_w
        self.vp_w = screen_width - self.panel_w
        self.vp_h = screen_height - TOP_BAR_H

        # ── Panel splitter drag (resize the right panel like a Windows
        # split-pane border) ────────────────────────────────────────────────
        self._panel_resize_active     = False
        self._panel_resize_start_mx   = 0
        self._panel_resize_start_w    = 0

        # ── Maps ──────────────────────────────────────────────────────────────
        self.maps: list[WorldMap]    = []
        self.current_map_idx: int    = 0
        self._scan_and_load_maps()

        # ── Tilesets ──────────────────────────────────────────────────────────
        self.tilesets: list[WMTileset] = []
        self.tileset_idx: int          = 0
        self._load_tilesets()
        self._ts_lookup: dict[str, WMTileset] = {t.name: t for t in self.tilesets}

        # ── Location icons ────────────────────────────────────────────────────
        # icon_names: ordered list of stems; icon_surfs: stem → Surface (various sizes)
        self.icon_names: list[str]                        = []
        self._icon_cache: dict[tuple[str,int], Optional[pygame.Surface]] = {}
        self._load_icons()

        # ── Vehicle sprites (for entity paths) ────────────────────────────────
        self.vehicle_sprites: list[WMVehicleSprite] = []
        self.vehicle_names:   list[str]             = []
        self._load_vehicle_sprites()

        # ── Camera ────────────────────────────────────────────────────────────
        self.cam_x    = 0.0
        self.cam_y    = 0.0
        self.zoom_idx = ZOOM_DEFAULT

        # ── Mode ──────────────────────────────────────────────────────────────
        self.mode = 'paint'   # 'paint' | 'location' | 'entity' | 'scouter'

        # ── Scouter paint (silhouette for the Scouter's WORLD MAP screen) ──────
        self.scouter_brush = 1   # brush size in map tiles per side: 1/2/4/8

        # ── Entity editing ────────────────────────────────────────────────────
        self.entity_selected_idx: Optional[int]        = None   # selected entity in the list
        self.entity_placing:      bool                 = False  # currently adding waypoints
        self._entity_rubber:      Optional[tuple[int,int]] = None  # tile pos of cursor (rubber-band)
        self._entity_anim_t:      float                = 0.0    # animation clock (seconds)

        # Entity room dropdown (mirrors the location dialog room dropdown)
        self.entity_room_dropdown_open   = False
        self.entity_room_dropdown_scroll = 0

        # ── Tile brush selection ───────────────────────────────────────────────
        self.sel_tx     = 0;  self.sel_ty     = 0
        self.sel_end_tx = 0;  self.sel_end_ty = 0

        # ── Paint / erase stroke ──────────────────────────────────────────────
        self.is_painting = False
        self.is_erasing  = False
        self._last_paint_cell: Optional[tuple[int, int]] = None

        # ── Camera pan ────────────────────────────────────────────────────────
        self.is_panning     = False
        self._pan_start_mouse: Optional[tuple[int, int]] = None
        self._pan_start_cam:   Optional[tuple[float, float]] = None
        # ── Tileset palette pan (middle-drag inside the panel) ────────────────
        self._ts_panning         = False
        self._ts_pan_start_mouse: Optional[tuple[int, int]] = None
        self._ts_pan_start_scroll: Optional[tuple[int, int]] = None
        # ── Undo stack ───────────────────────────────────────────────────────
        self._undo_stack: list = []   # list of (tiles_copy, locations_copy)
        self._MAX_UNDO   = 64

        # ── Palette scroll ────────────────────────────────────────────────────
        self.palette_scroll_y  = 0
        self.palette_scroll_x  = 0
        self._pal_drag_active  = False
        self._pal_drag_start_y = 0
        self._pal_drag_tile_start_y = 0

        # Real on-screen origin of the palette grid, captured each frame it's
        # drawn (_draw_paint_panel). The panel's layout shifts vertically
        # depending on what's drawn above it (e.g. the music row), so this
        # must be read from the actual draw call rather than recomputed with
        # fixed offsets — otherwise click hit-testing drifts out of sync with
        # what's rendered.
        self._palette_grid_origin: Optional[tuple[int, int]] = None

        # ── Grid visibility ───────────────────────────────────────────────────
        self.show_grid = True

        # ── Draw caches (rebuilt only when inputs change) ─────────────────────
        self._sel_surf:       Optional[pygame.Surface] = None
        self._sel_surf_size:  tuple[int, int]          = (0, 0)
        self._vp_grid_surf:   Optional[pygame.Surface] = None
        self._vp_grid_ds:     int                      = 0
        self._vp_grid_size:   tuple[int, int]          = (0, 0)

        # ── Entity height slider state ─────────────────────────────────────────
        self._entity_height_slider_drag    = False
        self._entity_height_slider_track_x = 0
        self._entity_height_slider_track_w = 0

        # ── Location editing ──────────────────────────────────────────────────
        self.selected_loc: Optional[WMLocation] = None
        self.loc_dialog           = False
        self.loc_dialog_is_new    = False
        self.loc_dialog_new_pos: tuple[int, int] = (0, 0)
        self.loc_dialog_name      = ''
        self.loc_dialog_room      = ''
        self.loc_dialog_height    = 0     # int height value
        self.loc_dialog_field     = 'name'  # 'name' | 'room'
        self._height_slider_drag  = False  # whether the slider thumb is being dragged
        self.loc_dialog_rects: dict[str, pygame.Rect] = {}
        self.room_dropdown_open   = False   # whether the room dropdown popup is visible
        self.room_dropdown_scroll = 0       # first visible item index
        self.room_dropdown_hover  = -1      # hovered item index (-1 = none)

        # Reference to the game's RoomManager (set externally via set_room_manager)
        self.room_manager = None

        # Optional callable(map_name) the host (Game) can set to hear about
        # saves — see _save_current_map. None = editor runs standalone.
        self.on_save = None

        # ── Map music dropdown (always visible in the panel, any mode) ────────
        self.music_dropdown_open          = False
        self.music_dropdown_names: list   = []
        self.music_dropdown_scroll        = 0
        self._music_dropdown_visible_rows = 8

        # ── New-map dialog ────────────────────────────────────────────────────
        self.new_map_dialog  = False
        self.new_map_name    = ''
        self._map_tab_scroll = 0   # index of the first visible map tab

        # ── Text-field editing (New Map name / Location name) ─────────────────
        # Same state RoomEditor keeps for its inline text fields. The text
        # itself stays in new_map_name / loc_dialog_name (see the text_input
        # property near _handle_new_map_dialog_event).
        self.cursor_pos       = 0
        self.selection_anchor = None   # None = no selection; else other end of it
        self._text_drag       = False
        # Rect + text-start-x + font of the name field, captured by
        # _draw_input_field each frame so click/drag handling can hit-test
        # and turn a mouse x into a character index without knowing which
        # dialog is up.
        self._active_edit_rect   = None
        self._active_edit_text_x = None
        self._active_edit_font   = None

        # ── Dialog dragging (New Map / Location dialogs — only one is ever
        # open at a time, see draw(), so one shared offset is enough) ─────────
        self._dialog_pos_offset: list      = [0, 0]  # [dx, dy] from centered
        self._dialog_drag_active           = False
        self._dialog_drag_start_mouse: tuple[int, int] = (0, 0)
        self._dialog_drag_start_offset: tuple[int, int] = (0, 0)

        # ── Double-click detection ─────────────────────────────────────────────
        self._dbl_click_time: float                     = 0.0
        self._dbl_click_pos:  Optional[tuple[int, int]] = None

        # ── Double-click detection ────────────────────────────────────────────
        self._dbl_click_time:  float                    = 0.0
        self._dbl_click_pos:   Optional[tuple[int,int]] = None

        # ── Animation ────────────────────────────────────────────────────────
        self.cursor_blink = 0.0

        # Which of move/resize/text cursor kind (if any) update() currently
        # has "claimed" from the shared ui_kit cursor state -- see update()'s
        # cursor block for why this has to be ownership-guarded rather than
        # calling a setter unconditionally every frame.
        self._owned_cursor_kind: Optional[str] = None

        # ── Fonts (bitmap, matching DevMenu / Room Editor) ─────────────────────
        self._bitmap_font  = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self._title_bitmap_font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self._title_bitmap_font.uppercase_dir = os.path.join('assets', 'ui', 'fonts', 'uppercase')
        self._title_bitmap_font.lowercase_dir = os.path.join('assets', 'ui', 'fonts', 'lowercase')

        self.font_title  = _BitmapFontView(self._title_bitmap_font, 22)
        self.font_large  = _BitmapFontView(self._bitmap_font, 16)
        self.font_medium = _BitmapFontView(self._bitmap_font, 13)
        self.font_small  = _BitmapFontView(self._bitmap_font, 11)

        # Mode → (label, icon drawer, accent color) — single source of truth
        # for the top-bar mode buttons and the panel headers that match them.
        self.MODE_INFO = {
            'paint':    ('Paint',    _draw_brush_icon,   uk.Theme.GOLD),
            'location': ('Location', _draw_pin_icon,     uk.Theme.GOLD),
            'entity':   ('Entity',   uk.draw_entity_icon, uk.Theme.KI_BLUE),
            'scouter':  ('Scouter',  _draw_scouter_icon, (120, 220, 140)),
        }

        # Back-arrow icon, top bar (left of "New") — the exact same shared
        # PNG asset (assets/ui/dev_menu/icons/back.png) that DevMenu's own
        # header back button and Room Editor's back button use, loaded with
        # the same crop + point-sample scaling so it's pixel-identical
        # across every menu rather than a separate vector look-alike.
        self._back_icon = self._load_dev_menu_icon('back', 22)

        # Plus icon (+Frame button) — same convention as _back_icon above:
        # drop assets/ui/dev_menu/icons/plus.png in to replace the vector
        # plus glyph that button used before. Degrades to a blank
        # transparent surface if the file isn't there yet.
        self._plus_icon = self._load_dev_menu_icon('plus', 18)

        # Zoom in/out icons — same convention: drop
        # assets/ui/dev_menu/icons/zoom_in.png and zoom_out.png in to
        # replace the vector magnifying-glass glyphs those buttons used
        # before. Degrades to a blank transparent surface if the files
        # aren't there yet.
        self._zoom_in_icon  = self._load_dev_menu_icon('zoom_in', 18)
        self._zoom_out_icon = self._load_dev_menu_icon('zoom_out', 18)

        # Close/delete "X" icon — replaces uk.draw_close_icon everywhere
        # it was used in this file (list-row delete buttons, Cancel,
        # Clear/Clear Path/Clear all, the frame-delete button). Same
        # convention: drop assets/ui/dev_menu/icons/close.png in.
        # Degrades to a blank transparent surface if the file isn't
        # there yet.
        self._close_icon = self._load_dev_menu_icon('close', 18)

        # Cached UI rects for hit-testing — populated fresh by the draw
        # methods every frame (see _draw_top_bar / _draw_panel / dialogs) and
        # consumed by handle_input's click handlers. Keys are unchanged from
        # the previous UI pass so all existing input-handling logic below
        # keeps working: only how each rect gets drawn has changed.
        self.ui: dict[str, pygame.Rect] = {}

        # Map-content palette — used only by the viewport/entity-path/height-
        # preview renderers below (tiles, grid, pins, paths), which draw the
        # actual map data rather than editor chrome. Kept as a small color
        # table, same keys as before, so that content-drawing code needs no
        # changes beyond picking new theme-matching values here; every panel,
        # button, dialog and list elsewhere is built fresh from uk.Theme.
        self.C = {
            'bg':           (10,  12,  18),
            'grid':         (34,  39,  50),
            'map_border':   uk.Theme.GOLD,
            'text':         uk.Theme.TEXT_PRIMARY,
            'dim':          uk.Theme.TEXT_MUTED,
            'accent':       uk.Theme.GOLD,
            'pin':          (232, 92,  92),
            'pin_sel':      uk.Theme.GOLD_BRIGHT,
            'entity_path':  uk.Theme.KI_BLUE,
            'entity_node':  (244, 170, 90),
            'entity_sel':   uk.Theme.GOLD_BRIGHT,
        }

    # ─────────────────────── public API ──────────────────────────────────────

    def toggle(self):
        self.active = not self.active

    def update(self, dt: float):
        if not self.active:
            self._release_owned_cursor()
            return
        self.cursor_blink    = (self.cursor_blink    + dt) % 1.0
        self._entity_anim_t += dt

        # I-beam over the new-map-name / location-name text fields — built
        # from last frame's draw() (see _draw_new_map_dialog /
        # _draw_loc_dialog), same lag the existing rect hit-testing here
        # already lives with.
        text_rect = None
        if self.new_map_dialog:
            text_rect = self.loc_dialog_rects.get('field')
        elif self.loc_dialog:
            text_rect = self.loc_dialog_rects.get('field_name')
        mouse_pos = pygame.mouse.get_pos()

        text_hover = text_rect is not None and text_rect.collidepoint(mouse_pos)
        if self.new_map_dialog or self.loc_dialog:
            # A dialog is up — it intercepts all input (see handle_input's
            # "Dialog intercepts" block), so the background splitter isn't
            # actually draggable right now; only the dialog's own title bar
            # (move) and text field (I-beam) hover states apply.
            dragbar = self.loc_dialog_rects.get('_dragbar')
            move_hover = self._dialog_drag_active or (
                dragbar is not None and dragbar.collidepoint(mouse_pos))
            resize_hover = False
        else:
            # Resize cursor takes priority over the text I-beam — while
            # actively dragging the splitter, or just hovering it, show
            # the left-right arrow; only fall back to the I-beam check
            # otherwise.
            move_hover = False
            resize_hover = self._panel_resize_active or self._over_splitter(*mouse_pos)

        # Decide which hover state (if any) wins, then touch the shared OS
        # cursor ONLY when that decision actually changed since last frame.
        #
        # This used to call set_move_cursor/set_resize_cursor/set_text_cursor
        # unconditionally every frame — harmless when claiming a kind, but
        # when none of the three applied it still called
        # set_text_cursor(False), which forces the arrow right here, in
        # update(). The hand cursor for every clickable widget elsewhere
        # (toolbar buttons, panel rows, dropdowns, ...) isn't decided until
        # draw() runs update_hover_cursor() much later in the same frame, so
        # forcing the arrow here and letting draw() correct it back to hand
        # a full frame's worth of drawing later meant the OS genuinely
        # rendered the arrow for a moment before flipping back — every
        # single frame, seen as the cursor flickering. (Same bug, same fix,
        # as RoomEditor.update()'s _owns_text_cursor guard.)
        #
        # Fix: only ever touch the cursor here when we're actually claiming
        # one of these three kinds, or releasing the one we previously
        # claimed. Otherwise leave the shared cursor alone entirely, so
        # whatever draw()'s update_hover_cursor() already decided (or is
        # about to decide) is never disturbed by a speculative reset here.
        desired_kind = 'move' if move_hover else 'resize' if resize_hover else 'text' if text_hover else None
        if desired_kind != self._owned_cursor_kind:
            self._release_owned_cursor()
            if desired_kind == 'move':
                uk.set_move_cursor(True)
            elif desired_kind == 'resize':
                uk.set_resize_cursor(True)
            elif desired_kind == 'text':
                uk.set_text_cursor(True)
            self._owned_cursor_kind = desired_kind

    def _release_owned_cursor(self):
        """Release whichever of move/resize/text cursor update() currently
        holds (no-op if it isn't holding any), without touching the shared
        cursor at all otherwise. See update()'s cursor block."""
        if self._owned_cursor_kind == 'move':
            uk.set_move_cursor(False)
        elif self._owned_cursor_kind == 'resize':
            uk.set_resize_cursor(False)
        elif self._owned_cursor_kind == 'text':
            uk.set_text_cursor(False)
        self._owned_cursor_kind = None

    # ─────────────────────── properties ──────────────────────────────────────

    @property
    def current_map(self) -> Optional[WorldMap]:
        if self.maps and 0 <= self.current_map_idx < len(self.maps):
            return self.maps[self.current_map_idx]
        return None

    @property
    def current_tileset(self) -> Optional[WMTileset]:
        if self.tilesets and 0 <= self.tileset_idx < len(self.tilesets):
            return self.tilesets[self.tileset_idx]
        return None

    @property
    def zoom(self) -> int:
        return ZOOM_LEVELS[self.zoom_idx]

    @property
    def tile_px(self) -> int:
        """Display pixel size of one tile at current zoom."""
        return NATIVE_TILE * self.zoom

    # ─────────────────────── coordinate helpers ───────────────────────────────

    def _screen_to_tile(self, sx: int, sy: int) -> tuple[int, int]:
        ds = self.tile_px
        wx = sx - self.vp_x + self.cam_x
        wy = sy - self.vp_y + self.cam_y
        return int(wx // ds), int(wy // ds)

    def _tile_to_screen(self, tx: int, ty: int) -> tuple[float, float]:
        ds = self.tile_px
        return (tx * ds - self.cam_x + self.vp_x,
                ty * ds - self.cam_y + self.vp_y)

    def _clamp_camera(self):
        ds = self.tile_px
        max_x = max(0.0, MAP_TILE_W * ds - self.vp_w)
        max_y = max(0.0, MAP_TILE_H * ds - self.vp_h)
        self.cam_x = max(0.0, min(self.cam_x, max_x))
        self.cam_y = max(0.0, min(self.cam_y, max_y))

    def _palette_visible_height(self, default: int = 400) -> int:
        """Visible height of the tileset palette scroll area in Paint
        mode. Derived from _palette_grid_origin (set by _draw_paint_panel
        each frame) so the scroll-wheel/middle-drag clamps below always
        match whatever the panel is actually sized to on screen — same
        one-frame lag as the other last-frame-rect lookups in this file.
        Falls back to `default` before the first paint-panel draw."""
        if self._palette_grid_origin is None:
            return default
        _, grid_y = self._palette_grid_origin
        return max(1, self.screen_height - grid_y - PALETTE_BOTTOM_MARGIN)

    def _in_viewport(self, mx: int, my: int) -> bool:
        return (self.vp_x <= mx < self.vp_x + self.vp_w
                and self.vp_y <= my < self.vp_y + self.vp_h)

    def _in_panel(self, mx: int, my: int) -> bool:
        return mx >= self.vp_x + self.vp_w

    # ─────────────────────── panel splitter (resize) ──────────────────────────

    def _recompute_viewport(self):
        """Re-derive vp_w from panel_w and re-clamp the camera so the newly
        exposed/covered viewport edge doesn't leave it looking at empty
        space or off the map. Call this any time panel_w or panel_open
        changes. A collapsed panel takes no width, so the map view gets
        the whole screen."""
        self.vp_w = self.screen_width - (self.panel_w if self.panel_open else 0)
        # Floor to the snap grid (never up) so panel_layout_w-sized content
        # never overflows the actual (equal-or-wider) panel background.
        self.panel_layout_w = (self.panel_w // PANEL_LAYOUT_SNAP) * PANEL_LAYOUT_SNAP
        self._clamp_camera()

    def _splitter_rect(self) -> pygame.Rect:
        """Hit-region for the drag handle between viewport and panel,
        straddling the border line so it's easy to grab (matches the
        'drag the border between two panes' feel of Windows split views)."""
        border_x = self.vp_x + self.vp_w
        return pygame.Rect(border_x - SPLITTER_HIT_W // 2, self.vp_y,
                            SPLITTER_HIT_W, self.vp_h)

    def _over_splitter(self, mx: int, my: int) -> bool:
        # No panel, no splitter — otherwise its hit-strip would still sit
        # straddling the screen's right edge.
        return self.panel_open and self._splitter_rect().collidepoint(mx, my)

    def _max_panel_w(self) -> int:
        # Leave a minimum usable viewport width so the panel can never be
        # dragged wide enough to swallow the whole map view.
        return min(PANEL_MAX_W, self.screen_width - 480)

    def _set_panel_w(self, new_w: int):
        new_w = max(PANEL_MIN_W, min(self._max_panel_w(), new_w))
        if new_w != self.panel_w:
            self.panel_w = new_w
            self._recompute_viewport()

    def _set_panel_open(self, open_: bool):
        """Show or collapse the right panel; the map view grows/shrinks to
        fill whatever is free."""
        if open_ == self.panel_open:
            return
        self.panel_open = open_
        if not open_:
            # Drop transient state that lives inside the panel, so nothing
            # invisible keeps grabbing input and popups don't reappear
            # stale when the panel comes back.
            self.music_dropdown_open       = False
            self.entity_room_dropdown_open = False
            self._pal_drag_active          = False
            self._ts_panning               = False
            self._entity_height_slider_drag = False
            self._panel_resize_active      = False
        self._recompute_viewport()

    def _on_mode_button(self, mode: str):
        """Top-bar mode button. Pressing the already-active mode toggles the
        right panel; pressing a different mode switches to it and makes sure
        its panel is showing."""
        if mode == self.mode:
            self._set_panel_open(not self.panel_open)
            return
        self.mode = mode
        if mode != 'entity':
            self._entity_stop_placing()
            self.entity_room_dropdown_open = False
        self._set_panel_open(True)

    # ─────────────────────── disk I/O ────────────────────────────────────────

    def _scan_and_load_maps(self):
        os.makedirs(SAVE_DIR, exist_ok=True)
        self.maps = []
        try:
            for fname in sorted(os.listdir(SAVE_DIR)):
                if fname.lower().endswith('.json'):
                    path = os.path.join(SAVE_DIR, fname)
                    with open(path, 'r') as f:
                        self.maps.append(WorldMap.from_dict(json.load(f)))
        except Exception as exc:
            print(f"[WorldMapEditor] Error scanning maps: {exc}")

    def _save_current_map(self):
        wm = self.current_map
        if not wm:
            return
        os.makedirs(SAVE_DIR, exist_ok=True)
        path = os.path.join(SAVE_DIR, f"{wm.name}.json")
        with open(path, 'w') as f:
            json.dump(wm.to_dict(), f, indent=2)
        print(f"[WorldMapEditor] Saved '{wm.name}'")
        # Let the host (Game) know this map's JSON just changed on disk, so
        # it can drop any cached render of it — e.g. the Scouter's WORLD_MAP
        # surface and the Mode7 flying-scene texture — instead of keeping a
        # stale image around until the game process restarts. Optional: the
        # editor works standalone (no host) without this being set.
        if self.on_save is not None:
            try:
                self.on_save(wm.name)
            except Exception as e:
                print(f"[WorldMapEditor] on_save callback failed: {e}")

    def _load_tilesets(self):
        self.tilesets = []
        if not os.path.exists(TILESET_DIR):
            print(f"[WorldMapEditor] Tileset directory not found: {TILESET_DIR}")
            return
        for fname in sorted(os.listdir(TILESET_DIR)):
            if fname.lower().endswith('.png'):
                name = os.path.splitext(fname)[0]
                self.tilesets.append(WMTileset(name, os.path.join(TILESET_DIR, fname)))

    def _load_icons(self):
        """Scan ICON_DIR for PNGs and store their stems in icon_names."""
        self.icon_names = []
        if not os.path.exists(ICON_DIR):
            print(f"[WorldMapEditor] Icon directory not found: {ICON_DIR}")
            return
        for fname in sorted(os.listdir(ICON_DIR)):
            if fname.lower().endswith('.png'):
                self.icon_names.append(os.path.splitext(fname)[0])
        if not self.icon_names:
            print(f"[WorldMapEditor] no .png files found in {ICON_DIR} — "
                  f"new locations will have no icon assigned by default")

    def _get_icon(self, stem: str, size: int) -> Optional[pygame.Surface]:
        """Return a Surface for icon *stem* scaled to *size*×*size*; cached."""
        key = (stem, size)
        if key not in self._icon_cache:
            path = os.path.join(ICON_DIR, stem + '.png')
            try:
                raw = pygame.image.load(path).convert_alpha()
                self._icon_cache[key] = pygame.transform.smoothscale(raw, (size, size))
            except Exception as e:
                # Previously swallowed silently, which made a broken/missing
                # icon indistinguishable from "no icon assigned" — a pin
                # would just render as a plain circle either way. Log once
                # per (stem, size) so the real cause (bad path, corrupt
                # file, wrong stem) is visible instead of guessable.
                print(f"[WorldMapEditor] failed to load icon '{stem}' "
                      f"from {path}: {e}")
                self._icon_cache[key] = None
        return self._icon_cache[key]

    def _load_vehicle_sprites(self):
        """Scan VEHICLE_DIR for PNGs and load them as WMVehicleSprite objects."""
        self.vehicle_sprites = []
        self.vehicle_names   = []
        if not os.path.exists(VEHICLE_DIR):
            print(f"[WorldMapEditor] Vehicle directory not found: {VEHICLE_DIR}")
            return
        for fname in sorted(os.listdir(VEHICLE_DIR)):
            if fname.lower().endswith('.png'):
                stem = os.path.splitext(fname)[0]
                self.vehicle_sprites.append(
                    WMVehicleSprite(stem, os.path.join(VEHICLE_DIR, fname)))
                self.vehicle_names.append(stem)

    def _get_vehicle_sprite(self, stem: str) -> Optional[WMVehicleSprite]:
        for vs in self.vehicle_sprites:
            if vs.name == stem:
                return vs
        return None

    def _create_map(self, name: str):
        name = (name.strip().replace(' ', '_') or 'unnamed')
        existing = {m.name for m in self.maps}
        base = name; i = 2
        while name in existing:
            name = f"{base}_{i}"; i += 1
        wm = WorldMap(name)
        self.maps.append(wm)
        self.current_map_idx = len(self.maps) - 1
        self._save_current_map()

    def _delete_map(self, idx: int):
        """Delete the map at *idx*, remove its JSON file, and fix selection."""
        if not (0 <= idx < len(self.maps)):
            return
        wm = self.maps[idx]
        # Remove the file from disk if it exists.
        path = os.path.join(SAVE_DIR, f'{wm.name}.json')
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        self.maps.pop(idx)
        # Keep current_map_idx valid.
        if not self.maps:
            self.current_map_idx = 0
        else:
            self.current_map_idx = max(0, min(self.current_map_idx, len(self.maps) - 1))
            if self.current_map_idx >= idx:
                self.current_map_idx = max(0, self.current_map_idx - 1)
        # Scroll the tab strip back if it now points past the end.
        self._map_tab_scroll = max(0, min(self._map_tab_scroll,
                                          max(0, len(self.maps) - 1)))

    # ─────────────────────── tile helpers ────────────────────────────────────

    def _place_tiles(self, anchor_tx: int, anchor_ty: int):
        wm = self.current_map
        ts = self.current_tileset
        if not wm or not ts:
            return
        min_tx = min(self.sel_tx, self.sel_end_tx)
        max_tx = max(self.sel_tx, self.sel_end_tx)
        min_ty = min(self.sel_ty, self.sel_end_ty)
        max_ty = max(self.sel_ty, self.sel_end_ty)
        for dy in range(max_ty - min_ty + 1):
            for dx in range(max_tx - min_tx + 1):
                ptx = anchor_tx + dx
                pty = anchor_ty + dy
                if 0 <= ptx < MAP_TILE_W and 0 <= pty < MAP_TILE_H:
                    wm.tiles[(ptx, pty)] = WMTile(
                        ptx, pty, ts.name, min_tx + dx, min_ty + dy)
                    wm.invalidate_chunk_at(ptx, pty)

    def _erase_tile(self, tx: int, ty: int):
        wm = self.current_map
        if wm:
            wm.tiles.pop((tx, ty), None)
            wm.invalidate_chunk_at(tx, ty)

    # ─────────────────────── scouter paint helpers ────────────────────────────

    def _scouter_brush_cells(self, tx: int, ty: int) -> set[tuple[int, int]]:
        """Map-tile cells covered by the scouter brush centered at (tx, ty),
        clipped to map bounds."""
        b = self.scouter_brush
        half = b // 2
        cells = set()
        for dy in range(b):
            for dx in range(b):
                ptx = tx - half + dx
                pty = ty - half + dy
                if 0 <= ptx < MAP_TILE_W and 0 <= pty < MAP_TILE_H:
                    cells.add((ptx, pty))
        return cells

    def _scouter_paint_at(self, tx: int, ty: int):
        wm = self.current_map
        if wm:
            wm.scouter_paint.update(self._scouter_brush_cells(tx, ty))

    def _scouter_erase_at(self, tx: int, ty: int):
        wm = self.current_map
        if wm:
            wm.scouter_paint.difference_update(self._scouter_brush_cells(tx, ty))

    def _scouter_cycle_brush(self, direction: int):
        sizes = (1, 2, 4, 8)
        i = sizes.index(self.scouter_brush) if self.scouter_brush in sizes else 0
        self.scouter_brush = sizes[max(0, min(len(sizes) - 1, i + direction))]

    # ─────────────────────── location helpers ────────────────────────────────

    def _loc_pin_at(self, mx: int, my: int,
                    radius: int = 10) -> Optional[WMLocation]:
        """Return the first location pin near screen position (mx, my)."""
        wm = self.current_map
        if not wm:
            return None
        ds = self.tile_px
        for loc in wm.locations:
            px, py = self._tile_to_screen(loc.x, loc.y)
            cx = px + ds / 2
            cy = py + ds / 2 - getattr(loc, 'height', 0)
            if math.hypot(mx - cx, my - cy) <= radius:
                return loc
        return None

    def _open_loc_dialog(self, is_new: bool, pos: tuple[int, int] = (0, 0),
                         loc: Optional[WMLocation] = None):
        self.loc_dialog         = True
        self.loc_dialog_is_new  = is_new
        self.loc_dialog_new_pos = pos
        self.loc_dialog_field   = 'name'
        self._dialog_pos_offset = [0, 0]
        self.room_dropdown_open  = False
        self.room_dropdown_scroll = 0
        self.room_dropdown_hover  = -1
        default_icon = self.icon_names[0] if self.icon_names else ''
        if is_new:
            self.loc_dialog_name   = ''
            self.loc_dialog_room   = ''
            self.loc_dialog_icon   = default_icon
            self.loc_dialog_height = 0
        else:
            self.loc_dialog_name   = loc.name if loc else ''
            self.loc_dialog_room   = loc.room if loc else ''
            self.loc_dialog_height = int(loc.height if loc else 0)
            existing = (loc.icon if loc else '')
            self.loc_dialog_icon = (existing if existing in self.icon_names
                                    else default_icon)
        self.cursor_blink = 0.0
        self._begin_text_edit()

    def _commit_loc_dialog(self):
        wm = self.current_map
        if not wm:
            self._cancel_loc_dialog(); return
        self._push_undo()
        if self.loc_dialog_is_new:
            tx, ty = self.loc_dialog_new_pos
            loc = WMLocation(tx, ty,
                             self.loc_dialog_name.strip(),
                             self.loc_dialog_room.strip(),
                             self.loc_dialog_icon,
                             int(self.loc_dialog_height))
            wm.locations.append(loc)
            self.selected_loc = loc
        else:
            if self.selected_loc:
                self.selected_loc.name   = self.loc_dialog_name.strip()
                self.selected_loc.room   = self.loc_dialog_room.strip()
                self.selected_loc.icon   = self.loc_dialog_icon
                self.selected_loc.height = int(self.loc_dialog_height)
        self._cancel_loc_dialog()

    def set_room_manager(self, rm):
        """Wire up the game's RoomManager so the room dropdown can list rooms."""
        self.room_manager = rm

    def _get_room_names(self) -> list:
        """Return a sorted list of room name strings from the room manager."""
        if self.room_manager is None:
            return []
        try:
            return sorted(self.room_manager.get_room_names())
        except Exception:
            return []

    def _get_music_track_names(self) -> list:
        """Return a sorted list of track filenames from assets/audio/music/.

        Scanned directly from disk rather than imported from
        objects/music_object.py, so the world map editor still works as a
        standalone dev tool even if that module isn't importable (missing
        file, no pygame.mixer/SoundEngine around, etc.) — same rationale
        as event_editor.py's _discover_music_tracks(). Falls back to []
        on any error; the dropdown just shows no tracks instead of
        crashing the editor.
        """
        MUSIC_EXTENSIONS = ('.ogg', '.mp3', '.wav', '.it', '.xm', '.s3m', '.mod')
        music_path = os.path.join('assets', 'audio', 'music')
        try:
            return sorted({
                os.path.splitext(f)[0]
                for f in os.listdir(music_path)
                if f.lower().endswith(MUSIC_EXTENSIONS)
            })
        except Exception:
            return []

    def _cancel_loc_dialog(self):
        self.loc_dialog          = False
        self.room_dropdown_open  = False
        self.room_dropdown_scroll = 0
        self.room_dropdown_hover  = -1
        # If the dialog is closing (Enter/Escape/OK/Cancel) while its
        # title bar is still mid-drag, no MOUSEBUTTONUP ever reaches
        # _handle_dialog_drag_event to clear this — leaving it stuck True
        # and, the next time any dialog opens, forcing update()'s
        # move_hover on unconditionally regardless of actual mouse
        # position (see update()'s move_hover line). Clear it here too.
        self._dialog_drag_active = False

    def _push_undo(self):
        """Snapshot the current map state onto the undo stack."""
        wm = self.current_map
        if not wm:
            return
        import copy
        # Deep-copy every frame so independent edits on different frames are
        # each undoable. WMTile is a small dataclass so this is cheap enough.
        self._undo_stack.append((
            [dict(frame) for frame in wm._frames],    # list of shallow-copied frame dicts
            wm.frame_idx,                              # restore the active frame too
            [copy.copy(loc) for loc in wm.locations],
            set(wm.scouter_paint),
        ))
        if len(self._undo_stack) > self._MAX_UNDO:
            self._undo_stack.pop(0)

    def _undo(self):
        """Restore the most recent snapshot from the undo stack."""
        wm = self.current_map
        if not wm or not self._undo_stack:
            return
        frames_snap, frame_idx_snap, locs_snap, scouter_snap = self._undo_stack.pop()
        wm._frames       = frames_snap
        wm.frame_idx     = max(0, min(frame_idx_snap, len(wm._frames) - 1))
        wm.locations     = locs_snap
        wm.scouter_paint = scouter_snap
        if self.selected_loc not in wm.locations:
            self.selected_loc = None

    # ─────────────────────── entity helpers ──────────────────────────────────

    def _entity_pos_at_t(self, entity: WMEntity, t: float
                         ) -> Optional[tuple[float, float]]:
        """Return (tile_x, tile_y) float position of *entity* at time *t* seconds."""
        path = entity.path
        if not path:
            return None
        if len(path) == 1:
            return float(path[0][0]), float(path[0][1])

        pts = list(path)
        if entity.closed:
            pts.append(path[0])

        segs: list[float] = []
        total = 0.0
        for i in range(len(pts) - 1):
            d = math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1])
            segs.append(d)
            total += d

        if total == 0.0:
            return float(path[0][0]), float(path[0][1])

        SPEED = 2.5   # tiles per second
        if entity.closed:
            dist = (t * SPEED) % total
        else:
            cycle = total * 2.0
            phase = (t * SPEED) % cycle
            dist  = phase if phase <= total else cycle - phase

        walked = 0.0
        for i, seg_len in enumerate(segs):
            if walked + seg_len >= dist or i == len(segs) - 1:
                frac = ((dist - walked) / seg_len) if seg_len > 0 else 0.0
                frac = max(0.0, min(1.0, frac))
                x = pts[i][0] + frac * (pts[i+1][0] - pts[i][0])
                y = pts[i][1] + frac * (pts[i+1][1] - pts[i][1])
                return x, y
            walked += seg_len
        return float(path[-1][0]), float(path[-1][1])

    def _entity_add_waypoint(self, tx: int, ty: int):
        """Append a waypoint to the currently-edited entity."""
        wm = self.current_map
        if wm is None or self.entity_selected_idx is None:
            return
        idx = self.entity_selected_idx
        if 0 <= idx < len(wm.entities):
            pt = (tx, ty)
            # Avoid duplicate adjacent waypoints
            if not wm.entities[idx].path or wm.entities[idx].path[-1] != pt:
                wm.entities[idx].path.append(pt)

    def _entity_stop_placing(self):
        self.entity_placing  = False
        self._entity_rubber  = None

    def _entity_height_slider_update(self, mouse_x: int):
        """Recompute the selected entity's height from a raw mouse x position."""
        wm   = self.current_map
        eidx = self.entity_selected_idx
        if wm is None or eidx is None or not (0 <= eidx < len(wm.entities)):
            return
        EHEIGHT_MIN, EHEIGHT_MAX = 0, 2000
        t = (mouse_x - self._entity_height_slider_track_x) / max(1, self._entity_height_slider_track_w)
        t = max(0.0, min(1.0, t))
        wm.entities[eidx].height = int(round(EHEIGHT_MIN + t * (EHEIGHT_MAX - EHEIGHT_MIN)))

    def _palette_mouse_to_tile(self, mx: int, my: int
                               ) -> Optional[tuple[int, int]]:
        """Convert panel-relative mouse pos to palette tile coords."""
        if self._palette_grid_origin is None:
            # Not drawn yet this session — nothing to hit-test against.
            return None
        origin_x, origin_y = self._palette_grid_origin
        grid_x = origin_x - self.palette_scroll_x
        grid_y = origin_y - self.palette_scroll_y
        ts = self.current_tileset
        if not ts:
            return None
        col = (mx - grid_x) // PALETTE_CELL
        row = (my - grid_y) // PALETTE_CELL
        if 0 <= col < ts.cols and 0 <= row < ts.rows:
            return col, row
        return None

    # ─────────────────────── event handling ──────────────────────────────────

    def handle_input(self, event: pygame.event.Event) -> Optional[str]:
        """Process one pygame event. Returns None (editor is self-contained)."""
        if not self.active:
            return None

        # ── Dialog intercepts ─────────────────────────────────────────────────
        if self.new_map_dialog:
            self._handle_new_map_dialog_event(event)
            return None
        if self.loc_dialog:
            self._handle_loc_dialog_event(event)
            return None

        keys = pygame.key.get_pressed()
        ctrl = keys[pygame.K_LCTRL] or keys[pygame.K_RCTRL]

        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_F2, pygame.K_ESCAPE):
                self.active = False
                # Mirrors RoomEditor's 'back_to_dev_menu' convention: the Dev
                # Menu closed itself when it launched this editor (see
                # DevMenu._activate_selected), so game.py's event loop needs
                # this explicit signal to reopen it — otherwise closing the
                # editor drops all the way back into gameplay instead.
                return 'back_to_dev_menu'
            if ctrl and event.key == pygame.K_s:
                self._save_current_map()
                return None
            if ctrl and event.key == pygame.K_z:
                self._undo()
                return None
            if ctrl and event.key == pygame.K_a and self.mode == 'paint':
                ts = self.current_tileset
                if ts:
                    self.sel_tx     = 0;  self.sel_ty     = 0
                    self.sel_end_tx = ts.cols - 1
                    self.sel_end_ty = ts.rows - 1
                return None
            if event.key == pygame.K_TAB and self.mode == 'paint' and self.tilesets:
                self.tileset_idx = (self.tileset_idx + 1) % len(self.tilesets)
                self.palette_scroll_y = 0
                return None
            if event.key == pygame.K_g:
                self.show_grid = not self.show_grid
                return None
            if event.key == pygame.K_LEFTBRACKET and self.mode == 'scouter':
                self._scouter_cycle_brush(-1)
                return None
            if event.key == pygame.K_RIGHTBRACKET and self.mode == 'scouter':
                self._scouter_cycle_brush(+1)
                return None

        elif event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos
            now = pygame.time.get_ticks()
            is_double = (
                event.button == 1
                and self._dbl_click_pos is not None
                and now - self._dbl_click_time <= 400
                and math.hypot(mx - self._dbl_click_pos[0],
                               my - self._dbl_click_pos[1]) <= 8
            )
            if event.button == 1:
                self._dbl_click_time = now
                self._dbl_click_pos  = (mx, my)
            now = pygame.time.get_ticks()
            _DBL_MS = 400   # milliseconds threshold

            # Detect double-click: same button, same rough position, within threshold
            is_double = (
                event.button == 1
                and self._dbl_click_pos is not None
                and now - self._dbl_click_time <= _DBL_MS
                and math.hypot(mx - self._dbl_click_pos[0],
                               my - self._dbl_click_pos[1]) <= 8
            )
            if event.button == 1:
                self._dbl_click_time = now
                self._dbl_click_pos  = (mx, my)

            # Close the music dropdown on any click outside the panel.
            if self.music_dropdown_open and not self._in_panel(mx, my):
                self.music_dropdown_open = False

            # ── Top-bar buttons ───────────────────────────────────────────────
            if my < TOP_BAR_H:
                return self._handle_topbar_click(mx, my, event.button)

            # ── Panel splitter drag (resize panel like a Windows split view) ───
            if event.button == 1 and self._over_splitter(mx, my):
                self._panel_resize_active   = True
                self._panel_resize_start_mx = mx
                self._panel_resize_start_w  = self.panel_w
                return None

            # ── Middle-click pan ──────────────────────────────────────────────
            if event.button == 2:
                if self._in_panel(mx, my):
                    self._ts_panning          = True
                    self._ts_pan_start_mouse  = event.pos
                    self._ts_pan_start_scroll = (self.palette_scroll_x, self.palette_scroll_y)
                else:
                    self.is_panning       = True
                    self._pan_start_mouse = event.pos
                    self._pan_start_cam   = (self.cam_x, self.cam_y)
                return None

            # ── Panel interactions ────────────────────────────────────────────
            if self._in_panel(mx, my):
                self._handle_panel_click(mx, my, event.button, is_double)
                return None

            # ── Viewport interactions ─────────────────────────────────────────
            if self._in_viewport(mx, my):
                tx, ty = self._screen_to_tile(mx, my)
                if self.mode == 'paint':
                    if event.button == 1:
                        self._push_undo()
                        self.is_painting = True
                        self._last_paint_cell = (tx, ty)
                        self._place_tiles(tx, ty)
                    elif event.button == 3:
                        self._push_undo()
                        self.is_erasing = True
                        self._last_paint_cell = (tx, ty)
                        self._erase_tile(tx, ty)
                elif self.mode == 'location':
                    hit = self._loc_pin_at(mx, my)
                    if event.button == 1:
                        if hit:
                            self.selected_loc = hit
                            if is_double:
                                # Double-click on a pin → open edit dialog
                                self._open_loc_dialog(is_new=False, loc=hit)
                        else:
                            self._open_loc_dialog(is_new=True, pos=(tx, ty))
                    elif event.button == 3:
                        if hit:
                            self.selected_loc = hit
                            self._open_loc_dialog(is_new=False, loc=hit)
                elif self.mode == 'scouter':
                    if event.button == 1:
                        self._push_undo()
                        self.is_painting = True
                        self._last_paint_cell = (tx, ty)
                        self._scouter_paint_at(tx, ty)
                    elif event.button == 3:
                        self._push_undo()
                        self.is_erasing = True
                        self._last_paint_cell = (tx, ty)
                        self._scouter_erase_at(tx, ty)
                elif self.mode == 'entity':
                    if self.entity_placing:
                        if event.button == 1:
                            self._entity_add_waypoint(tx, ty)
                        elif event.button == 3:
                            self._entity_stop_placing()

            # ── Scroll ────────────────────────────────────────────────────────
            if event.button == 4:
                self._scroll_event(mx, my, +1)
            elif event.button == 5:
                self._scroll_event(mx, my, -1)

        elif event.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            self._scroll_event(mx, my, event.y)

        elif event.type == pygame.MOUSEBUTTONUP:
            if event.button == 1:
                self.is_painting              = False
                self._pal_drag_active         = False
                self._last_paint_cell         = None
                self._entity_height_slider_drag = False
                self._panel_resize_active     = False
            elif event.button == 2:
                self.is_panning  = False
                self._ts_panning = False
            elif event.button == 3:
                self.is_erasing      = False
                self._last_paint_cell = None

        elif event.type == pygame.MOUSEMOTION:
            mx, my = event.pos

            if self._panel_resize_active:
                # Dragging left grows the panel (border moves left), dragging
                # right shrinks it — same feel as a Windows split-pane border.
                delta = self._panel_resize_start_mx - mx
                self._set_panel_w(self._panel_resize_start_w + delta)

            elif self.is_panning and self._pan_start_mouse and self._pan_start_cam:
                dx = self._pan_start_mouse[0] - mx
                dy = self._pan_start_mouse[1] - my
                self.cam_x = self._pan_start_cam[0] + dx
                self.cam_y = self._pan_start_cam[1] + dy
                self._clamp_camera()

            elif self._ts_panning and self._ts_pan_start_mouse and self._ts_pan_start_scroll:
                dx = self._ts_pan_start_mouse[0] - mx
                dy = self._ts_pan_start_mouse[1] - my
                ts = self.current_tileset
                max_scroll_y = max(0, ts.rows * PALETTE_CELL - self._palette_visible_height()) if ts else 0
                max_scroll_x = max(0, ts.cols * PALETTE_CELL - self.panel_w) if ts else 0
                self.palette_scroll_y = max(0, min(max_scroll_y, self._ts_pan_start_scroll[1] + dy))
                self.palette_scroll_x = max(0, min(max_scroll_x, self._ts_pan_start_scroll[0] + dx))

            elif self.is_painting and self._in_viewport(mx, my):
                tx, ty = self._screen_to_tile(mx, my)
                if (tx, ty) != self._last_paint_cell:
                    self._last_paint_cell = (tx, ty)
                    if self.mode == 'scouter':
                        self._scouter_paint_at(tx, ty)
                    else:
                        self._place_tiles(tx, ty)

            elif self.is_erasing and self._in_viewport(mx, my):
                tx, ty = self._screen_to_tile(mx, my)
                if (tx, ty) != self._last_paint_cell:
                    self._last_paint_cell = (tx, ty)
                    if self.mode == 'scouter':
                        self._scouter_erase_at(tx, ty)
                    else:
                        self._erase_tile(tx, ty)

            elif self._pal_drag_active:
                self._handle_palette_drag(my)

            # Update entity height slider if dragging
            if self._entity_height_slider_drag and self.mode == 'entity':
                self._entity_height_slider_update(mx)

            # Update rubber-band endpoint for entity path placement
            if self.mode == 'entity' and self.entity_placing and self._in_viewport(mx, my):
                self._entity_rubber = self._screen_to_tile(mx, my)

        return None

    def _scroll_event(self, mx: int, my: int, direction: int):
        """Handle scroll wheel — zoom in viewport, scroll palette in panel."""
        if self._in_panel(mx, my):
            # If the music dropdown is open, scroll it
            if self.music_dropdown_open:
                names = self.music_dropdown_names
                visible = self._music_dropdown_visible_rows
                self.music_dropdown_scroll = max(
                    0, min(
                        self.music_dropdown_scroll - direction,
                        max(0, len(names) - visible)
                    )
                )
                return
            # If the entity room dropdown is open, scroll it
            if self.mode == 'entity' and self.entity_room_dropdown_open:
                room_names = self._get_room_names()
                MAX_VIS = 8
                self.entity_room_dropdown_scroll = max(
                    0, min(
                        self.entity_room_dropdown_scroll - direction,
                        max(0, len(room_names) - MAX_VIS)
                    )
                )
                return
            # Scroll palette
            ts = self.current_tileset
            if ts:
                max_scroll = max(0, ts.rows * PALETTE_CELL - self._palette_visible_height())
                self.palette_scroll_y = max(
                    0, min(max_scroll, self.palette_scroll_y - direction * PALETTE_CELL))
        else:
            # Zoom — keep the tile under the cursor stationary
            old_zoom = self.zoom
            self.zoom_idx = max(0, min(len(ZOOM_LEVELS) - 1,
                                       self.zoom_idx + (1 if direction > 0 else -1)))
            new_zoom = self.zoom
            if new_zoom != old_zoom:
                # Rescale camera so the pixel under the cursor stays fixed
                rel_x = mx - self.vp_x + self.cam_x
                rel_y = my - self.vp_y + self.cam_y
                factor = new_zoom / old_zoom
                self.cam_x = rel_x * factor - (mx - self.vp_x)
                self.cam_y = rel_y * factor - (my - self.vp_y)
                self._clamp_camera()

    def _handle_topbar_click(self, mx: int, my: int, button: int) -> Optional[str]:
        # Priority pass: delete-tab buttons sit inside tab rects, so check them first.
        for key, rect in self.ui.items():
            if key.startswith('map_del_') and rect.collidepoint(mx, my):
                idx = int(key.split('_')[-1])
                self._delete_map(idx)
                return None
        for key, rect in self.ui.items():
            if not rect.collidepoint(mx, my):
                continue
            if key == 'btn_back':
                self.active = False
                # Same 'back_to_dev_menu' convention RoomEditor uses — see
                # the matching comment on the F2/Escape handler above.
                return 'back_to_dev_menu'
            elif key == 'btn_new':
                self.new_map_dialog     = True
                self.new_map_name       = ''
                self._dialog_pos_offset = [0, 0]
                self._begin_text_edit()
            elif key == 'btn_save':
                self._save_current_map()
            elif key.startswith('btn_mode_'):
                self._on_mode_button(key[len('btn_mode_'):])
            elif key == 'btn_zoom_in':
                mx2, my2 = self.vp_x + self.vp_w // 2, self.vp_y + self.vp_h // 2
                self._scroll_event(mx2, my2, +1)
            elif key == 'btn_zoom_out':
                mx2, my2 = self.vp_x + self.vp_w // 2, self.vp_y + self.vp_h // 2
                self._scroll_event(mx2, my2, -1)
            elif key == 'btn_tab_scroll_left':
                self._map_tab_scroll = max(0, self._map_tab_scroll - 1)
            elif key == 'btn_tab_scroll_right':
                self._map_tab_scroll = min(max(0, len(self.maps) - 1),
                                           self._map_tab_scroll + 1)
            elif key.startswith('map_tab_'):
                idx = int(key.split('_')[-1])
                if 0 <= idx < len(self.maps):
                    self.current_map_idx = idx
            elif key == 'btn_frame_prev':
                wm = self.current_map
                if wm and wm.frame_count > 1:
                    wm.frame_idx = (wm.frame_idx - 1) % wm.frame_count
            elif key == 'btn_frame_next':
                wm = self.current_map
                if wm and wm.frame_count > 1:
                    wm.frame_idx = (wm.frame_idx + 1) % wm.frame_count
            elif key == 'btn_frame_add':
                wm = self.current_map
                if wm:
                    new_idx = wm.duplicate_frame(wm.frame_idx)
                    wm.frame_idx = new_idx
            elif key == 'btn_frame_del':
                wm = self.current_map
                if wm and wm.frame_count > 1:
                    wm.frame_idx = wm.remove_frame(wm.frame_idx)
        return None

    def _handle_panel_click(self, mx: int, my: int, button: int, is_double: bool = False):
        wm = self.current_map

        # ── Music dropdown popup items (float above mode content) ─────────────
        if button == 1 and self.music_dropdown_open:
            for key, rect in list(self.ui.items()):
                if key.startswith('music_dd_') and rect.collidepoint(mx, my):
                    idx = int(key.split('_')[-1])
                    names = self.music_dropdown_names
                    if wm and 0 <= idx < len(names):
                        wm.music = names[idx]
                    self.music_dropdown_open = False
                    return
            list_rect = self.ui.get('music_dropdown_list_rect')
            if not (list_rect and list_rect.collidepoint(mx, my)):
                # Click outside the popup (and not on the button, handled below) closes it.
                btn_rect = self.ui.get('music_dropdown_btn')
                if not (btn_rect and btn_rect.collidepoint(mx, my)):
                    self.music_dropdown_open = False

        # ── Music dropdown button / clear button ───────────────────────────────
        if button == 1:
            btn_rect = self.ui.get('music_dropdown_btn')
            if btn_rect and btn_rect.collidepoint(mx, my):
                self.music_dropdown_open = not self.music_dropdown_open
                self.music_dropdown_scroll = 0
                if self.music_dropdown_open:
                    self.music_dropdown_names = self._get_music_track_names()
                return
            clr_rect = self.ui.get('music_clear')
            if clr_rect and clr_rect.collidepoint(mx, my):
                if wm:
                    wm.music = ''
                self.music_dropdown_open = False
                return

        if self.mode == 'paint':
            keys = pygame.key.get_pressed()
            ctrl = keys[pygame.K_LCTRL] or keys[pygame.K_RCTRL]
            if ctrl:
                ts = self.current_tileset
                if ts:
                    self.sel_tx     = 0;  self.sel_ty     = 0
                    self.sel_end_tx = ts.cols - 1
                    self.sel_end_ty = ts.rows - 1
                return
            coords = self._palette_mouse_to_tile(mx, my)
            if coords:
                self.sel_tx = self.sel_end_tx = coords[0]
                self.sel_ty = self.sel_end_ty = coords[1]
                self._pal_drag_active  = True
                self._pal_drag_start_y = my
        elif self.mode == 'entity':
            self._handle_entity_panel_click(mx, my, button)
        elif self.mode == 'scouter':
            if button != 1:
                return
            for size in (1, 2, 4, 8):
                rect = self.ui.get(f'btn_scouter_brush_{size}')
                if rect and rect.collidepoint(mx, my):
                    self.scouter_brush = size
                    return
            clr_rect = self.ui.get('btn_scouter_clear')
            if clr_rect and clr_rect.collidepoint(mx, my) and wm:
                self._push_undo()
                wm.scouter_paint = set()
                return
        elif self.mode == 'location':
            # Delete button sits inside row rect — check it first.
            # Double-click on a row opens the edit dialog.
            if wm:
                # Pass 1 – delete button (higher priority)
                handled = False
                for key, rect in self.ui.items():
                    if key.startswith('loc_del_') and rect.collidepoint(mx, my):
                        idx = int(key.split('_')[-1])
                        if 0 <= idx < len(wm.locations):
                            self._push_undo()
                            if wm.locations[idx] is self.selected_loc:
                                self.selected_loc = None
                            wm.locations.pop(idx)
                        handled = True
                        break
                # Pass 2 – row (select on single-click; edit dialog on double-click)
                if not handled:
                    for key, rect in self.ui.items():
                        if key.startswith('loc_entry_') and rect.collidepoint(mx, my):
                            idx = int(key.split('_')[-1])
                            if 0 <= idx < len(wm.locations):
                                loc = wm.locations[idx]
                                self.selected_loc = loc
                                if is_double:
                                    self._open_loc_dialog(is_new=False, loc=loc)
                                else:
                                    ds = self.tile_px
                                    self.cam_x = loc.x * ds - self.vp_w / 2 + ds / 2
                                    self.cam_y = loc.y * ds - self.vp_h / 2 + ds / 2
                                    self._clamp_camera()
                            break

    def _handle_palette_drag(self, my: int):
        coords = self._palette_mouse_to_tile(*pygame.mouse.get_pos())
        if coords:
            self.sel_end_tx = coords[0]
            self.sel_end_ty = coords[1]

    def _handle_dialog_drag_event(self, event: pygame.event.Event) -> bool:
        """Shared drag-the-dialog-by-its-title-bar handling for both the
        New Map and Location dialogs (only one is ever open at a time —
        see draw() / self._dialog_pos_offset, so one shared offset covers
        both). Returns True if this event was consumed by the drag and
        the caller should stop processing it further."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            dragbar = self.loc_dialog_rects.get('_dragbar')
            if dragbar and dragbar.collidepoint(event.pos):
                self._dialog_drag_active       = True
                self._dialog_drag_start_mouse  = event.pos
                self._dialog_drag_start_offset = tuple(self._dialog_pos_offset)
                return True
        elif event.type == pygame.MOUSEMOTION:
            if self._dialog_drag_active:
                dx = event.pos[0] - self._dialog_drag_start_mouse[0]
                dy = event.pos[1] - self._dialog_drag_start_mouse[1]
                self._dialog_pos_offset[0] = self._dialog_drag_start_offset[0] + dx
                self._dialog_pos_offset[1] = self._dialog_drag_start_offset[1] + dy
                return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._dialog_drag_active:
                self._dialog_drag_active = False
                return True
        return False

    # ── Text-field editing (New Map name / Location name) ─────────────────────
    #
    # Ported from RoomEditor's inline text input: real caret position,
    # selection range, shift/drag selection, click-to-place-caret, and the OS
    # clipboard (uk.clipboard_get_text / uk.clipboard_set_text). RoomEditor
    # edits one live buffer, self.text_input. Here the two editable strings
    # already live in new_map_name / loc_dialog_name and are read directly by
    # _create_map / _commit_loc_dialog, so text_input is a property aliasing
    # whichever one the open dialog is editing — that keeps every helper
    # below identical to RoomEditor's.

    @property
    def text_input(self) -> str:
        return self.new_map_name if self.new_map_dialog else self.loc_dialog_name

    @text_input.setter
    def text_input(self, value: str):
        if self.new_map_dialog:
            self.new_map_name = value
        else:
            self.loc_dialog_name = value

    @property
    def _text_max_len(self) -> int:
        # The same caps the old `len(...) < N` checks used.
        return 32 if self.new_map_dialog else 40

    def _reset_text_edit_state(self):
        """Caret at the end of the field, no selection — used whenever the
        name field (re)gains focus, so cursor state can't be left stale."""
        self.cursor_pos = len(self.text_input)
        self.selection_anchor = None
        self._text_drag = False
        self.cursor_blink = 0.0

    def _begin_text_edit(self):
        """A dialog just opened: reset the caret and drop the field geometry
        captured for whichever dialog was drawn last."""
        self._active_edit_rect = None
        self._active_edit_text_x = None
        self._active_edit_font = None
        self._reset_text_edit_state()

    def _has_text_selection(self):
        return self.selection_anchor is not None and self.selection_anchor != self.cursor_pos

    def _text_selection_range(self):
        a, b = self.selection_anchor, self.cursor_pos
        return (a, b) if a <= b else (b, a)

    def _delete_text_selection(self):
        """Removes the selected text (if any), leaving the cursor at the
        start of where it was. Returns True if anything was deleted."""
        if not self._has_text_selection():
            return False
        s, e = self._text_selection_range()
        self.text_input = self.text_input[:s] + self.text_input[e:]
        self.cursor_pos = s
        self.selection_anchor = None
        return True

    def _insert_into_text_input(self, s):
        """Types `s` in at the cursor, replacing the selection if any and
        respecting the field's max length. Used for both single keystrokes
        and pasted text. Filters/length-checks happen before touching the
        selection, so an empty or all-non-printable `s` (e.g. a stray
        modifier-key keystroke, or pasting an empty clipboard) leaves any
        existing selection untouched instead of silently deleting it."""
        s = "".join(ch for ch in s if ch.isprintable())
        if not s:
            return
        if self._has_text_selection():
            self._delete_text_selection()
        space = self._text_max_len - len(self.text_input)
        if space <= 0:
            return
        s = s[:space]
        self.text_input = self.text_input[:self.cursor_pos] + s + self.text_input[self.cursor_pos:]
        self.cursor_pos += len(s)

    def _text_index_from_x(self, x):
        """Map an absolute mouse x-coordinate to the character index in
        self.text_input whose caret sits closest to it, using the field/font
        captured by the last draw (see _active_edit_text_x / _active_edit_font)."""
        if self._active_edit_font is None or self._active_edit_text_x is None or not self.text_input:
            return 0
        font = self._active_edit_font
        widths = [0]
        for i in range(1, len(self.text_input) + 1):
            widths.append(font.size(self.text_input[:i])[0])
        rel_x = x - self._active_edit_text_x
        best_i, best_d = 0, abs(widths[0] - rel_x)
        for i, w in enumerate(widths):
            d = abs(w - rel_x)
            if d < best_d:
                best_i, best_d = i, d
        return best_i

    def _handle_text_edit_key(self, event):
        """Editing keys for the focused name field — the same branches as
        RoomEditor's text-field KEYDOWN handling. Enter / Escape / Tab are
        left to the caller since what they do differs per dialog."""
        mods = pygame.key.get_mods()
        ctrl = bool(mods & (pygame.KMOD_CTRL | pygame.KMOD_META))
        shift = bool(mods & pygame.KMOD_SHIFT)

        if ctrl and event.key == pygame.K_a:
            self.selection_anchor = 0
            self.cursor_pos = len(self.text_input)
        elif ctrl and event.key in (pygame.K_c, pygame.K_x):
            if self._has_text_selection():
                s, e = self._text_selection_range()
                uk.clipboard_set_text(self.text_input[s:e])
                if event.key == pygame.K_x:
                    self._delete_text_selection()
        elif ctrl and event.key == pygame.K_v:
            self._insert_into_text_input(uk.clipboard_get_text())
        elif event.key == pygame.K_LEFT:
            if shift:
                if self.selection_anchor is None:
                    self.selection_anchor = self.cursor_pos
                self.cursor_pos = max(0, self.cursor_pos - 1)
            elif self._has_text_selection():
                self.cursor_pos = self._text_selection_range()[0]
                self.selection_anchor = None
            else:
                self.cursor_pos = max(0, self.cursor_pos - 1)
        elif event.key == pygame.K_RIGHT:
            if shift:
                if self.selection_anchor is None:
                    self.selection_anchor = self.cursor_pos
                self.cursor_pos = min(len(self.text_input), self.cursor_pos + 1)
            elif self._has_text_selection():
                self.cursor_pos = self._text_selection_range()[1]
                self.selection_anchor = None
            else:
                self.cursor_pos = min(len(self.text_input), self.cursor_pos + 1)
        elif event.key == pygame.K_HOME:
            if shift and self.selection_anchor is None:
                self.selection_anchor = self.cursor_pos
            elif not shift:
                self.selection_anchor = None
            self.cursor_pos = 0
        elif event.key == pygame.K_END:
            if shift and self.selection_anchor is None:
                self.selection_anchor = self.cursor_pos
            elif not shift:
                self.selection_anchor = None
            self.cursor_pos = len(self.text_input)
        elif event.key == pygame.K_BACKSPACE:
            if not self._delete_text_selection() and self.cursor_pos > 0:
                self.text_input = self.text_input[:self.cursor_pos - 1] + self.text_input[self.cursor_pos:]
                self.cursor_pos -= 1
        elif event.key == pygame.K_DELETE:
            if not self._delete_text_selection() and self.cursor_pos < len(self.text_input):
                self.text_input = self.text_input[:self.cursor_pos] + self.text_input[self.cursor_pos + 1:]
        else:
            # Reject non-printable codes (e.g. arrow keys); length cap and
            # selection-replace are handled in the helper.
            if event.unicode and event.unicode.isprintable():
                self._insert_into_text_input(event.unicode)
        self.cursor_blink = 0.0

    def _handle_text_edit_mouse(self, event) -> bool:
        """Click / drag / release on the name field — same behaviour as
        RoomEditor: click places the caret, shift+click extends the
        selection, dragging selects (clamped to the box edges). Returns True
        if the event was consumed."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._active_edit_rect is not None and self._active_edit_rect.collidepoint(event.pos):
                idx = self._text_index_from_x(event.pos[0])
                if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                    if self.selection_anchor is None:
                        self.selection_anchor = self.cursor_pos
                else:
                    self.selection_anchor = idx
                self.cursor_pos = idx
                self._text_drag = True
                self.cursor_blink = 0.0
                return True
        elif event.type == pygame.MOUSEMOTION:
            if self._text_drag and self._active_edit_rect is not None:
                # Clamp to the box so dragging past an edge still selects to
                # that edge, same as a normal textbox.
                x = max(self._active_edit_rect.left, min(event.pos[0], self._active_edit_rect.right))
                self.cursor_pos = self._text_index_from_x(x)
                self.cursor_blink = 0.0
                return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._text_drag:
                self._text_drag = False
                return True
        return False

    def _handle_new_map_dialog_event(self, event: pygame.event.Event):
        if self._handle_dialog_drag_event(event):
            return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN and self.new_map_name.strip():
                self._create_map(self.new_map_name)
                self.new_map_dialog = False
                self._dialog_drag_active = False  # see _cancel_loc_dialog
            elif event.key == pygame.K_ESCAPE:
                self.new_map_dialog = False
                self._dialog_drag_active = False  # see _cancel_loc_dialog
            else:
                self._handle_text_edit_key(event)
        elif event.type == pygame.MOUSEBUTTONDOWN:
            if self._handle_text_edit_mouse(event):
                return
            for key, rect in self.loc_dialog_rects.items():
                if key == 'cancel' and rect.collidepoint(event.pos):
                    self.new_map_dialog = False
                    self._dialog_drag_active = False  # see _cancel_loc_dialog
                elif key == 'ok' and rect.collidepoint(event.pos):
                    if self.new_map_name.strip():
                        self._create_map(self.new_map_name)
                        self.new_map_dialog = False
                        self._dialog_drag_active = False  # see _cancel_loc_dialog
        elif event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONUP):
            self._handle_text_edit_mouse(event)

    def _handle_loc_dialog_event(self, event: pygame.event.Event):
        if self._handle_dialog_drag_event(event):
            return
        if event.type == pygame.KEYDOWN:
            # If the dropdown is open, arrow keys scroll it; Enter selects; Escape closes
            if self.room_dropdown_open:
                room_names = self._get_room_names()
                MAX_VIS    = 8
                if event.key == pygame.K_ESCAPE:
                    self.room_dropdown_open = False
                elif event.key == pygame.K_RETURN:
                    # Pick the currently highlighted item (first visible if none hovered)
                    if room_names:
                        # find the hovered item from loc_dialog_rects
                        mx, my = pygame.mouse.get_pos()
                        picked = None
                        for key, rect in self.loc_dialog_rects.items():
                            if key.startswith('dropdown_') and rect.collidepoint(mx, my):
                                idx = int(key.split('_')[1])
                                picked = room_names[idx]
                                break
                        if picked is None and room_names:
                            picked = room_names[self.room_dropdown_scroll]
                        if picked:
                            self.loc_dialog_room = picked
                    self.room_dropdown_open = False
                elif event.key == pygame.K_DOWN:
                    self.room_dropdown_scroll = min(
                        self.room_dropdown_scroll + 1,
                        max(0, len(room_names) - MAX_VIS))
                elif event.key == pygame.K_UP:
                    self.room_dropdown_scroll = max(0, self.room_dropdown_scroll - 1)
                return

            if event.key == pygame.K_RETURN:
                if self.loc_dialog_field == 'name':
                    self.loc_dialog_field = 'room'
                else:
                    self._commit_loc_dialog()
            elif event.key == pygame.K_TAB:
                self.loc_dialog_field = ('room' if self.loc_dialog_field == 'name'
                                         else 'name')
                if self.loc_dialog_field == 'name':
                    self._reset_text_edit_state()
            elif event.key == pygame.K_ESCAPE:
                self._cancel_loc_dialog()
            elif self.loc_dialog_field == 'name':
                self._handle_text_edit_key(event)
            # Room field is dropdown-only — no typing

        elif event.type == pygame.MOUSEBUTTONDOWN:
            # Scroll the dropdown with the mouse wheel
            if event.button in (4, 5) and self.room_dropdown_open:
                room_names = self._get_room_names()
                MAX_VIS    = 8
                if event.button == 4:   # scroll up
                    self.room_dropdown_scroll = max(0, self.room_dropdown_scroll - 1)
                else:                   # scroll down
                    self.room_dropdown_scroll = min(
                        self.room_dropdown_scroll + 1,
                        max(0, len(room_names) - MAX_VIS))
                return

            # Height slider: start drag on left-click anywhere on the hit area
            if event.button == 1:
                hit = self.loc_dialog_rects.get('height_slider')
                if hit and hit.collidepoint(event.pos):
                    self._height_slider_drag = True
                    self._height_slider_update(event.pos[0])
                    return

            # Name field: focus it (if it wasn't already) and put the caret /
            # start a drag-selection wherever the click landed.
            if event.button == 1:
                name_rect = self.loc_dialog_rects.get('field_name')
                if name_rect is not None and name_rect.collidepoint(event.pos):
                    if self.loc_dialog_field != 'name':
                        self.loc_dialog_field = 'name'
                        self._reset_text_edit_state()
                    self.room_dropdown_open = False
                    self._handle_text_edit_mouse(event)
                    return

            for key, rect in self.loc_dialog_rects.items():
                if not rect.collidepoint(event.pos):
                    continue
                if key == 'field_room':
                    # Toggle the dropdown
                    self.loc_dialog_field = 'room'
                    self.room_dropdown_open = not self.room_dropdown_open
                    self.room_dropdown_scroll = 0
                    # Scroll so the current selection is visible
                    room_names = self._get_room_names()
                    if self.loc_dialog_room in room_names:
                        idx = room_names.index(self.loc_dialog_room)
                        MAX_VIS = 8
                        self.room_dropdown_scroll = max(0, idx - MAX_VIS // 2)
                elif key.startswith('dropdown_'):
                    idx = int(key.split('_')[1])
                    room_names = self._get_room_names()
                    if 0 <= idx < len(room_names):
                        self.loc_dialog_room = room_names[idx]
                    self.room_dropdown_open = False
                elif key.startswith('icon_'):
                    idx = int(key.split('_')[1])
                    if 0 <= idx < len(self.icon_names):
                        self.loc_dialog_icon = self.icon_names[idx]
                    self.room_dropdown_open = False
                elif key == 'ok':
                    self._commit_loc_dialog()
                elif key == 'cancel':
                    self._cancel_loc_dialog()

        elif event.type == pygame.MOUSEMOTION:
            if self._handle_text_edit_mouse(event):
                return
            if self._height_slider_drag:
                self._height_slider_update(event.pos[0])

        elif event.type == pygame.MOUSEBUTTONUP:
            if event.button == 1:
                self._handle_text_edit_mouse(event)
                self._height_slider_drag = False

    def _height_slider_update(self, mouse_x: int):
        """Map mouse_x to a height value clamped to [0, 2000]."""
        HEIGHT_MIN, HEIGHT_MAX = 0, 2000
        tx = getattr(self, '_height_slider_track_x', 0)
        tw = getattr(self, '_height_slider_track_w', 1)
        t  = max(0.0, min(1.0, (mouse_x - tx) / tw))
        self.loc_dialog_height = round(HEIGHT_MIN + t * (HEIGHT_MAX - HEIGHT_MIN))

    def _handle_entity_panel_click(self, mx: int, my: int, button: int):
        wm = self.current_map
        if not wm:
            return

        # Height slider — higher priority than named button rects
        if button == 1:
            hit = self.ui.get('entity_height_slider')
            if hit and hit.collidepoint(mx, my):
                self._entity_height_slider_drag = True
                self._entity_height_slider_update(mx)
                return

        # Room dropdown item clicks (popup floats above everything else)
        if button == 1 and self.entity_room_dropdown_open:
            eidx = self.entity_selected_idx
            room_names = self._get_room_names()
            for key, rect in self.ui.items():
                if key.startswith('entity_room_dd_') and rect.collidepoint(mx, my):
                    idx = int(key.split('_')[-1])
                    if eidx is not None and 0 <= eidx < len(wm.entities) \
                            and 0 <= idx < len(room_names):
                        wm.entities[eidx].room = room_names[idx]
                    self.entity_room_dropdown_open = False
                    return
            # Click outside popup → close it
            self.entity_room_dropdown_open = False

        for key, rect in self.ui.items():
            if not rect.collidepoint(mx, my):
                continue
            if key == 'btn_entity_add':
                self._push_undo()
                e = WMEntity(name=f'entity_{len(wm.entities)+1}',
                             sprite=self.vehicle_names[0] if self.vehicle_names else '')
                wm.entities.append(e)
                self.entity_selected_idx = len(wm.entities) - 1
                self.entity_placing = True
                self._entity_rubber = None
            elif key == 'btn_entity_place':
                # Toggle placement mode for selected entity
                self.entity_placing = not self.entity_placing
                self._entity_rubber = None
            elif key == 'btn_entity_clear':
                idx = self.entity_selected_idx
                if idx is not None and 0 <= idx < len(wm.entities):
                    self._push_undo()
                    wm.entities[idx].path = []
                self._entity_stop_placing()
            elif key == 'btn_entity_closed':
                idx = self.entity_selected_idx
                if idx is not None and 0 <= idx < len(wm.entities):
                    wm.entities[idx].closed = not wm.entities[idx].closed
            elif key == 'btn_entity_done':
                self._entity_stop_placing()
            elif key.startswith('entity_del_'):
                idx = int(key.split('_')[-1])
                if 0 <= idx < len(wm.entities):
                    self._push_undo()
                    wm.entities.pop(idx)
                    if self.entity_selected_idx == idx:
                        self.entity_selected_idx = None
                        self._entity_stop_placing()
                    elif (self.entity_selected_idx or 0) > idx:
                        self.entity_selected_idx = (self.entity_selected_idx or 1) - 1
            elif key.startswith('entity_row_'):
                idx = int(key.split('_')[-1])
                if 0 <= idx < len(wm.entities):
                    self.entity_selected_idx = idx
                    self._entity_stop_placing()
                    self.entity_room_dropdown_open = False
            elif key.startswith('vehicle_pick_'):
                vidx = int(key.split('_')[-1])
                eidx = self.entity_selected_idx
                if (eidx is not None and 0 <= eidx < len(wm.entities)
                        and 0 <= vidx < len(self.vehicle_names)):
                    wm.entities[eidx].sprite = self.vehicle_names[vidx]
            elif key == 'entity_room_btn':
                eidx = self.entity_selected_idx
                if eidx is not None and 0 <= eidx < len(wm.entities):
                    self.entity_room_dropdown_open = not self.entity_room_dropdown_open
                    self.entity_room_dropdown_scroll = 0
                    # Scroll so the current selection is visible
                    room_names = self._get_room_names()
                    cur_room = wm.entities[eidx].room
                    if cur_room in room_names:
                        idx2 = room_names.index(cur_room)
                        self.entity_room_dropdown_scroll = max(0, idx2 - 4)
            elif key == 'entity_room_clear':
                eidx = self.entity_selected_idx
                if eidx is not None and 0 <= eidx < len(wm.entities):
                    wm.entities[eidx].room = ''
                self.entity_room_dropdown_open = False

    # ─────────────────────── drawing ─────────────────────────────────────────
    def draw(self, screen: pygame.Surface):
        if not self.active:
            return
        screen.fill(self.C['bg'])
        self._draw_top_bar(screen)   # must be first — calls self.ui.clear()
        self._draw_viewport(screen)
        self._draw_panel(screen)     # must be last — writes panel rects into self.ui

        if self.new_map_dialog:
            self._draw_new_map_dialog(screen)
        elif self.loc_dialog:
            self._draw_loc_dialog(screen)

        # Height preview: show while dragging either height slider
        if self._height_slider_drag or self._entity_height_slider_drag:
            self._draw_height_preview(screen)

        # Resolve the frame's cursor last, now that every clickable widget
        # drawn above (toolbar buttons, mode buttons, map tabs, panel rows,
        # sliders, dropdown fields/items, dialog buttons, icon/vehicle
        # pickers) has had a chance to register itself via
        # register_hoverable. Yields to whichever of move/resize/I-beam
        # update() claimed this frame — see update_hover_cursor's docstring
        # and update()'s _owned_cursor_kind guard (same convention as
        # RoomEditor.draw()'s own uk.update_hover_cursor call).
        uk.update_hover_cursor(pygame.mouse.get_pos())

    def _draw_height_preview(self, screen: pygame.Surface):
        """Draw a small Mode7-style preview in the bottom-left corner showing
        how the current height setting will look in the world map flying scene."""
        import math as _pm

        # ── Preview dimensions ───────────────────────────────────────────────
        PW, PH   = 220, 160   # preview panel size
        MARGIN   = 12
        px_off   = MARGIN
        py_off   = self.screen_height - PH - MARGIN

        # ── Panel background ─────────────────────────────────────────────────
        bg_surf = pygame.Surface((PW, PH), pygame.SRCALPHA)
        bg_surf.fill((10, 10, 20, 210))
        pygame.draw.rect(bg_surf, self.C['accent'], (0, 0, PW, PH), 1, border_radius=6)
        screen.blit(bg_surf, (px_off, py_off))

        # ── Determine what we're previewing ──────────────────────────────────
        is_entity = self._entity_height_slider_drag
        if is_entity:
            wm   = self.current_map
            eidx = self.entity_selected_idx
            if wm is None or eidx is None or not (0 <= eidx < len(wm.entities)):
                return
            e          = wm.entities[eidx]
            height_val = e.height
            sprite_stem = e.sprite
            icon_stem   = ''
        else:
            height_val  = self.loc_dialog_height
            icon_stem   = getattr(self, 'loc_dialog_icon', '')
            sprite_stem = ''

        # ── Label ────────────────────────────────────────────────────────────
        lbl = self.font_small.render('Mode7 height preview', True, self.C['dim'])
        screen.blit(lbl, (px_off + 6, py_off + 5))

        # ── Simplified Mode7 ground plane ────────────────────────────────────
        # We render a tiny perspective ground strip inside the panel using the
        # same scanline formula as the game's _draw_world_map_flying_scene.
        INNER_X  = px_off + 6
        INNER_Y  = py_off + 22
        INNER_W  = PW - 12
        INNER_H  = PH - 32

        ALTITUDE = 0.5   # fixed mid-altitude for the preview camera
        SKY_FRAC = 0.35
        sky_h_p  = int(INNER_H * SKY_FRAC)
        gnd_h_p  = INNER_H - sky_h_p

        # Sky gradient
        for _row in range(sky_h_p):
            t_sky = _row / max(1, sky_h_p - 1)
            sky_col = (
                int(20  + 40  * t_sky),
                int(60  + 80  * t_sky),
                int(100 + 100 * t_sky),
            )
            screen.draw_line(sky_col,
                             (INNER_X, INNER_Y + _row),
                             (INNER_X + INNER_W - 1, INNER_Y + _row))

        # Ground gradient (dark → bright as rows approach camera)
        for _row in range(gnd_h_p):
            t_gnd = _row / max(1, gnd_h_p - 1)
            gnd_col = (
                int(15  + 25  * t_gnd),
                int(50  + 60  * t_gnd),
                int(15  + 25  * t_gnd),
            )
            screen.draw_line(gnd_col,
                             (INNER_X, INNER_Y + sky_h_p + _row),
                             (INNER_X + INNER_W - 1, INNER_Y + sky_h_p + _row))

        # ── Perspective-project the icon/sprite ──────────────────────────────
        # Simulate a location standing ~25 tile units ahead of the camera at a
        # fixed moderate depth — enough to show perspective but not too small.
        FOCAL_P  = 60.0    # simplified focal constant for the preview
        PROJ_HOR = int(INNER_H * 0.20)
        virt_gnd = INNER_H - PROJ_HOR
        DEPTH    = 28.0    # simulated ground-plane depth (arbitrary units)

        rows_f   = FOCAL_P * virt_gnd / DEPTH
        # Ground y (where the tile sits on the perspective plane)
        ground_y = int(sky_h_p + rows_f)
        ground_y = min(ground_y, INNER_H - 2)

        # Perspective scale factor
        near_depth = FOCAL_P * virt_gnd / max(1, gnd_h_p)
        persp      = max(0.1, min(2.5, near_depth / DEPTH))

        # Apply height offset — same formula as the game renderer, scaled to preview size.
        # The game renders at ~480 px tall; INNER_H is the preview height, so we
        # divide by 480 to convert the same pixel-lift into preview coordinates.
        height_persp = min(1.0, max(0.5, persp))
        lift_scaled  = max(0, int(height_val * height_persp * 0.5 * INNER_H / 480))

        icon_y  = max(sky_h_p + 2, ground_y - lift_scaled)
        icon_cx = INNER_X + INNER_W // 2

        # Draw a small stem from ground to elevated icon if height > 0
        if height_val > 0 and lift_scaled > 0:
            stem_col = self.C['accent'] if not is_entity else self.C['entity_path']
            screen.draw_line(stem_col,
                             (icon_cx, INNER_Y + ground_y),
                             (icon_cx, INNER_Y + icon_y), 1)
            screen.draw_circle(stem_col, (icon_cx, INNER_Y + ground_y), 2)

        # Draw ground reference dot
        ground_dot_col = (120, 120, 120)
        screen.draw_circle(ground_dot_col, (icon_cx, INNER_Y + ground_y), 3, 1)

        # Icon or sprite
        ICON_SZ = max(8, int(24 * persp))
        drawn   = False
        if icon_stem:
            icon_surf = self._get_icon(icon_stem, ICON_SZ)
            if icon_surf:
                r = icon_surf.get_rect(midbottom=(icon_cx, INNER_Y + icon_y))
                screen.blit(icon_surf, r)
                drawn = True
        elif sprite_stem:
            vs = self._get_vehicle_sprite(sprite_stem)
            if vs and vs._frames_by_row:
                frame_idx = int(self._entity_anim_t * 4.0) % vs.num_frames
                vsurf = vs.get_frame(0, frame_idx, ICON_SZ)
                if vsurf:
                    r = vsurf.get_rect(midbottom=(icon_cx, INNER_Y + icon_y))
                    screen.blit(vsurf, r)
                    drawn = True
        if not drawn:
            # Fallback: simple coloured circle
            dot_col = self.C['entity_path'] if is_entity else self.C['pin']
            screen.draw_circle(dot_col,
                               (icon_cx, INNER_Y + icon_y - ICON_SZ // 2),
                               max(4, ICON_SZ // 2))

        # Height value readout
        val_lbl = self.font_medium.render(f'{height_val}', True, self.C['text'])
        screen.blit(val_lbl, val_lbl.get_rect(
            midtop=(px_off + PW // 2, py_off + PH - 18)))

    # ── viewport ──────────────────────────────────────────────────────────────

    def _get_chunk_surface(self, wm: WorldMap, cx: int, cy: int,
                           allow_build: bool = True) -> Optional[pygame.Surface]:
        """Composed CHUNK_TILES×CHUNK_TILES native-resolution Surface for
        chunk (cx, cy) in wm's current frame, cached on the WorldMap
        itself (see WorldMap._chunk_cache). This is the fix for the
        zoomed-out slowdown: the viewport used to call screen.blit_scaled()
        once per visible *tile* — 10,000+ individual SDL draw calls a
        frame once tiles got small enough on screen — because each call
        costs real Python overhead (texture lookup, Rect construction,
        clip math) on top of the actual GPU draw, and that overhead is
        what scaled with tile count, not the GPU-side scaling itself.
        Composing a whole chunk once and blitting *that* one texture
        turns "one draw call per visible tile" into "one draw call per
        visible chunk" — a ~CHUNK_TILES² reduction — and the composed
        chunk is cached and reused every frame until a tile inside it
        changes (see invalidate_chunk_at).

        If the chunk isn't cached yet and `allow_build` is False (the
        caller's per-frame build budget is spent), returns _CHUNK_PENDING
        instead of building it here — see CHUNK_BUILD_BUDGET_PER_FRAME."""
        key = (wm.frame_idx, cx, cy)
        cache = wm._chunk_cache
        if key in cache:
            return cache[key]
        if not allow_build:
            return _CHUNK_PENDING

        base_tx = cx * CHUNK_TILES
        base_ty = cy * CHUNK_TILES
        surf = None
        for ly in range(CHUNK_TILES):
            ty = base_ty + ly
            if ty >= MAP_TILE_H:
                break
            for lx in range(CHUNK_TILES):
                tx = base_tx + lx
                if tx >= MAP_TILE_W:
                    break
                tile = wm.tiles.get((tx, ty))
                if tile is None:
                    continue
                ts = self._ts_lookup.get(tile.tileset)
                if ts is None:
                    continue
                raw = ts.get_tile_raw(tile.tx, tile.ty)
                if raw is None:
                    continue
                if surf is None:
                    side = CHUNK_TILES * NATIVE_TILE
                    surf = pygame.Surface((side, side), pygame.SRCALPHA)
                surf.blit(raw, (lx * NATIVE_TILE, ly * NATIVE_TILE))
        cache[key] = surf  # None cached too, so empty chunks aren't rebuilt every frame
        return surf

    def _draw_viewport(self, screen: pygame.Surface):
        clip = pygame.Rect(self.vp_x, self.vp_y, self.vp_w, self.vp_h)
        screen.set_clip(clip)

        wm = self.current_map
        if not wm:
            msg = self.font_large.render(
                "No maps — click [+ NEW] to create one", True, self.C['dim'])
            screen.blit(msg, msg.get_rect(
                center=(self.vp_x + self.vp_w // 2,
                        self.vp_y + self.vp_h // 2)))
            screen.set_clip(None)
            return

        ds = self.tile_px
        cam_xi = int(self.cam_x)
        cam_yi = int(self.cam_y)

        # Visible tile range
        stx = max(0, cam_xi // ds)
        sty = max(0, cam_yi // ds)
        etx = min(MAP_TILE_W, (cam_xi + self.vp_w) // ds + 2)
        ety = min(MAP_TILE_H, (cam_yi + self.vp_h) // ds + 2)

        # Draw tiles, one chunk at a time (see _get_chunk_surface) rather
        # than one blit_scaled() call per tile — at low zoom a viewport
        # this size can have 10,000+ tiles visible, and that many
        # individual draw calls (not the GPU scaling itself) is what was
        # costing hundreds of ms a frame.
        chunk_px = CHUNK_TILES * ds
        if etx > stx and ety > sty:
            scx0 = stx // CHUNK_TILES
            scy0 = sty // CHUNK_TILES
            scx1 = (etx - 1) // CHUNK_TILES
            scy1 = (ety - 1) // CHUNK_TILES
            build_budget = CHUNK_BUILD_BUDGET_PER_FRAME
            for cy in range(scy0, scy1 + 1):
                chunk_sy = cy * CHUNK_TILES * ds - cam_yi + self.vp_y
                for cx in range(scx0, scx1 + 1):
                    key = (wm.frame_idx, cx, cy)
                    was_cached = key in wm._chunk_cache
                    chunk = self._get_chunk_surface(
                        wm, cx, cy, allow_build=(was_cached or build_budget > 0))
                    if chunk is _CHUNK_PENDING:
                        continue  # not built yet, budget spent — try again next frame
                    if not was_cached:
                        build_budget -= 1
                    if chunk is None:
                        continue
                    chunk_sx = cx * CHUNK_TILES * ds - cam_xi + self.vp_x
                    dst = pygame.Rect(chunk_sx, chunk_sy, chunk_px, chunk_px)
                    screen.blit_scaled(chunk, dst)

        # Grid (only when tiles are large enough to make it readable)
        if self.show_grid and ds >= 8:
            # Rebuild the grid surface when zoom level OR viewport size
            # changes — it used to key off zoom alone, so resizing the
            # panel (which changes vp_w every frame while dragging) kept
            # blitting a grid surface sized for the *old* viewport, leaving
            # it too narrow/short for the new one.
            if self._vp_grid_ds != ds or self._vp_grid_size != (self.vp_w, self.vp_h):
                gs = pygame.Surface((self.vp_w + ds, self.vp_h + ds),
                                    pygame.SRCALPHA)
                gc = self.C['grid']
                cols_needed = self.vp_w // ds + 2
                rows_needed = self.vp_h // ds + 2
                for r in range(rows_needed + 1):
                    pygame.draw.line(gs, gc, (0, r * ds), (self.vp_w + ds, r * ds))
                for c in range(cols_needed + 1):
                    pygame.draw.line(gs, gc, (c * ds, 0), (c * ds, self.vp_h + ds))
                self._vp_grid_surf = gs
                self._vp_grid_ds   = ds
                self._vp_grid_size = (self.vp_w, self.vp_h)
            # Blit with sub-tile offset so lines stay locked to world coords
            off_x = cam_xi % ds
            off_y = cam_yi % ds
            screen.blit(self._vp_grid_surf, (self.vp_x - off_x, self.vp_y - off_y))

        # ── Scouter paint overlay ───────────────────────────────────────────
        # Only shown while the tool is active — dims the tile art underneath
        # (same rationale as the room editor's Map Paint tool) so the
        # painted silhouette reads clearly, then previews it with the same
        # fill/outline coloring the in-game Scouter uses (see
        # game.py's _build_world_map_scouter_surface) so what's painted
        # here looks close to what it'll actually look like there.
        if self.mode == 'scouter':
            dim = pygame.Surface((self.vp_w, self.vp_h), pygame.SRCALPHA)
            dim.fill((10, 10, 20, 120))
            screen.blit(dim, (self.vp_x, self.vp_y))

            cells = wm.scouter_paint
            if cells:
                # Matches game.py's _build_world_map_scouter_surface: outline
                # 39FF39, interior black. The interior is drawn with a little
                # visible alpha here (rather than true black) so a designer
                # can still see what's painted against the dimmed backdrop —
                # in-game it's solid black and blends into the background.
                OUTLINE = (57, 255, 57, 220)
                FILL    = (57, 255, 57, 45)
                ov = pygame.Surface((self.vp_w, self.vp_h), pygame.SRCALPHA)
                for oty in range(sty, ety):
                    osy = oty * ds - cam_yi
                    for otx in range(stx, etx):
                        if (otx, oty) not in cells:
                            continue
                        osx = otx * ds - cam_xi
                        is_border = (
                            (otx - 1, oty) not in cells or (otx + 1, oty) not in cells
                            or (otx, oty - 1) not in cells or (otx, oty + 1) not in cells
                        )
                        pygame.draw.rect(ov, OUTLINE if is_border else FILL,
                                         (osx, osy, ds, ds))
                screen.blit(ov, (self.vp_x, self.vp_y))

        # Map boundary rect
        bx = self.vp_x - cam_xi
        by = self.vp_y - cam_yi
        screen.draw_rect(self.C['map_border'],
                         (bx, by, MAP_TILE_W * ds, MAP_TILE_H * ds), 2)

        # Location pins
        # Height offset scaling: loc.height is a raw 0–2000 value representing
        # a normalized altitude fraction for the Mode7 flying scene (height/2000
        # = 0.0–1.0 altitude), NOT a pixel count. Using it as a literal pixel
        # offset (as this used to) meant any real height value shot the pin
        # far above its tile — hundreds to ~2000px — which almost always
        # landed outside the viewport's clip rect, especially when zoomed out
        # where the whole map is only a few hundred pixels tall. Since the
        # entire pin draw is gated on clip.collidepoint(cx, cy), the pin
        # simply vanished. Scale it down to a small, zoom-relative visual cue
        # instead (same idea _draw_height_preview already uses via INNER_H),
        # and cap it so a pin never travels more than a few tiles' worth of
        # pixels from its own tile regardless of height or zoom level.
        HEIGHT_VISUAL_SCALE = 0.05
        for loc in wm.locations:
            px, py = self._tile_to_screen(loc.x, loc.y)
            cx = int(px + ds / 2)
            height_px = min(int(getattr(loc, 'height', 0) * HEIGHT_VISUAL_SCALE),
                             ds * 4)
            cy = int(py + ds / 2) - height_px
            if clip.collidepoint(cx, cy):
                color = (self.C['pin_sel'] if loc is self.selected_loc
                         else self.C['pin'])
                r = max(PIN_RADIUS, ds // 2)
                # Draw a thin stem from the ground point up to the elevated icon
                # so it's clear the pin is floating above its map tile.
                ground_cy = int(py + ds / 2)
                if loc.height != 0:
                    screen.draw_line(color,
                                     (cx, ground_cy), (cx, cy + r), 1)
                    screen.draw_circle(color, (cx, ground_cy), 2)
                screen.draw_circle(color, (cx, cy), r)
                screen.draw_circle((255, 255, 255), (cx, cy), r, 1)
                # Sprite icon inside the pin
                icon_stem = getattr(loc, 'icon', '')
                if icon_stem:
                    icon_size = max(8, r * 2 - 4)
                    icon_surf = self._get_icon(icon_stem, icon_size)
                    if icon_surf:
                        screen.blit(icon_surf, icon_surf.get_rect(center=(cx, cy)))
                if loc.name:
                    label = self.font_small.render(loc.name, True, (255, 255, 255))
                    screen.blit(label, (cx + r + 2, cy - 8))

        # ── Entity paths ──────────────────────────────────────────────────────
        self._draw_entity_paths(screen, clip, wm, ds)

        screen.set_clip(None)

    def _draw_entity_paths(self, screen: pygame.Surface,
                           clip: pygame.Rect, wm: WorldMap, ds: int):
        """Draw all entity paths and their animated sprites onto the viewport."""
        half = ds / 2

        for i, e in enumerate(wm.entities):
            is_sel = (i == self.entity_selected_idx)
            path_col  = self.C['entity_sel']   if is_sel else self.C['entity_path']
            node_col  = self.C['entity_node']
            path      = e.path
            if not path:
                continue

            # Convert tile coords to screen coords (centre of tile)
            def tc(tx, ty):
                sx, sy = self._tile_to_screen(tx, ty)
                return int(sx + half), int(sy + half)

            # ── Draw path segments ─────────────────────────────────────────
            pts_screen = [tc(tx, ty) for tx, ty in path]
            if len(pts_screen) >= 2:
                # Dashed line
                all_pts = pts_screen + ([pts_screen[0]] if e.closed else [])
                DASH = max(4, ds // 2)
                for j in range(len(all_pts) - 1):
                    p0x, p0y = all_pts[j]
                    p1x, p1y = all_pts[j + 1]
                    length   = math.hypot(p1x - p0x, p1y - p0y)
                    if length == 0:
                        continue
                    steps = max(1, int(length / (DASH * 2)))
                    for s in range(steps):
                        t0 = s / steps
                        t1 = (s + 0.5) / steps
                        ax = int(p0x + t0 * (p1x - p0x))
                        ay = int(p0y + t0 * (p1y - p0y))
                        bx = int(p0x + t1 * (p1x - p0x))
                        by = int(p0y + t1 * (p1y - p0y))
                        screen.draw_line(path_col, (ax, ay), (bx, by),
                                         2 if is_sel else 1)

            # ── Draw waypoint nodes ────────────────────────────────────────
            NODE_R = max(3, ds // 3)
            for k, (sx, sy) in enumerate(pts_screen):
                col = self.C['entity_sel'] if (is_sel and k == 0) else node_col
                screen.draw_circle(col, (sx, sy), NODE_R)
                screen.draw_circle((255, 255, 255), (sx, sy), NODE_R, 1)
                if is_sel and k == 0:
                    # Mark start
                    s_lbl = self.font_small.render('S', True, (0, 0, 0))
                    screen.blit(s_lbl, s_lbl.get_rect(center=(sx, sy)))

            # ── Rubber-band line (only for selected entity being edited) ──
            if is_sel and self.entity_placing and self._entity_rubber and pts_screen:
                rx, ry = tc(*self._entity_rubber)
                screen.draw_line((180, 220, 255),
                                 pts_screen[-1], (rx, ry), 1)
                screen.draw_circle((180, 220, 255), (rx, ry), max(2, ds // 4))

            # ── Animated sprite along path ─────────────────────────────────
            pos = self._entity_pos_at_t(e, self._entity_anim_t)
            if pos is not None:
                sx_f, sy_f = self._tile_to_screen(pos[0], pos[1])
                spr_cx = int(sx_f + half)
                spr_cy = int(sy_f + half) - getattr(e, 'height', 0)
                vs = self._get_vehicle_sprite(e.sprite) if e.sprite else None
                if vs and vs._frames_by_row:
                    # Compute movement direction for correct sprite row
                    pos2 = self._entity_pos_at_t(e, self._entity_anim_t + 0.1)
                    if pos2 and pos2 != pos:
                        mdx, mdy = pos2[0] - pos[0], pos2[1] - pos[1]
                    else:
                        mdx, mdy = 0.0, 1.0
                    dir_row  = _vehicle_dir_row(mdx, mdy, vs.num_dirs)
                    ANIM_FPS = 4.0
                    frame_idx = int(self._entity_anim_t * ANIM_FPS)
                    raw = vs.get_frame_raw(dir_row, frame_idx)
                    if raw:
                        display_h = max(ds, 8)
                        aspect = vs.frame_w / vs.frame_h
                        new_h = display_h
                        new_w = max(1, int(new_h * aspect))
                        r = pygame.Rect(0, 0, new_w, new_h)
                        r.center = (spr_cx, spr_cy)
                        screen.blit_scaled(raw, r)
                        # Draw a thin stem from ground level up to the elevated sprite
                        ground_cy = int(sy_f + half)
                        if e.height != 0:
                            screen.draw_line(path_col,
                                             (spr_cx, ground_cy), (spr_cx, spr_cy + r.height // 2), 1)
                            screen.draw_circle(path_col, (spr_cx, ground_cy), 2)
                        if is_sel:
                            screen.draw_rect(self.C['entity_sel'], r, 1)
                else:
                    # Fallback: coloured circle
                    screen.draw_circle(path_col, (spr_cx, spr_cy),
                                       max(4, ds // 2))

            # ── Entity name label (selected only) ──────────────────────────
            if is_sel and pts_screen:
                lbl = self.font_small.render(e.name, True, self.C['entity_sel'])
                screen.blit(lbl, (pts_screen[0][0] + NODE_R + 2,
                                  pts_screen[0][1] - 8))

    # ── shared chrome primitives ────────────────────────────────────────────

    def _hover(self, rect: pygame.Rect) -> bool:
        mx, my = pygame.mouse.get_pos()
        return rect.collidepoint(mx, my)

    @staticmethod
    def _load_dev_menu_icon(icon_key, box_size):
        """Load one of the shared dev-menu PNG icons (assets/ui/dev_menu/icons/)
        using the exact same crop + scaling as DevMenu._load_icon and Room
        Editor's copy of it, so an icon like 'back' comes out pixel-identical
        whether it's drawn on DevMenu's own header, Room Editor, or here.
        Kept in sync with those on purpose — if their scaling logic changes,
        mirror it here too."""
        path = os.path.join('assets', 'ui', 'dev_menu', 'icons', f'{icon_key}.png')
        try:
            raw = pygame.image.load(path).convert_alpha()
        except (FileNotFoundError, pygame.error):
            return pygame.Surface((box_size, box_size), pygame.SRCALPHA)

        content_rect = raw.get_bounding_rect(min_alpha=1)
        if content_rect.width <= 0 or content_rect.height <= 0:
            content_rect = raw.get_rect()
        raw = raw.subsurface(content_rect).copy()

        iw, ih = raw.get_size()
        scale = min(box_size / max(1, iw), box_size / max(1, ih))

        nw = max(1, round(iw * scale))
        nh = max(1, round(ih * scale))
        if scale >= 1.0:
            # Enlarging: blow up to a whole-number multiple first with
            # fast point-sampling (crisp, blocky, no filtering yet), THEN
            # do the final resize down to the exact target size with
            # smoothscale (area-averaging) instead of another
            # point-sample. A second point-sample step here was the
            # uneven-thickness bug: nw/nh is essentially never an exact
            # divisor of the blown-up size, so nearest-neighbor rounds
            # each destination pixel to its nearest source pixel
            # independently — a 2px-wide stroke can land as 2px in one
            # spot and 1px a few pixels over. smoothscale instead blends
            # each destination pixel from the source pixels it actually
            # covers, so stroke width comes out even. Doing the crisp
            # blow-up first (rather than smoothscaling straight from the
            # tiny source) keeps this from reading as blurry.
            prescale = max(1, math.ceil(scale) * 2)
            big = pygame.transform.scale(raw, (iw * prescale, ih * prescale))
            scaled = pygame.transform.smoothscale(big, (nw, nh))
        else:
            # Shrinking: discarding detail rather than fabricating it, so
            # smoothscale's area-averaging is the right tool here too —
            # and, same as above, avoids the uneven-thickness point-sample
            # bug.
            scaled = pygame.transform.smoothscale(raw, (nw, nh))

        canvas = pygame.Surface((box_size, box_size), pygame.SRCALPHA)
        canvas.blit(scaled, ((box_size - nw) // 2, (box_size - nh) // 2))
        return canvas

    def _toolbar_button(self, screen, x: int, label: str, key: str,
                         icon_fn=None, icon_surface=None, active: bool = False,
                         w: int = 0, accent=None, danger: bool = False) -> int:
        """Draw one top-bar pill button, register its hit rect under `key`
        in self.ui, and return the x position for whatever comes next.
        `icon_surface` (a pre-rendered PNG, e.g. the shared back-arrow icon)
        takes priority over `icon_fn` (a vector line-glyph) when given, and
        is simply centered in the button rather than recolored per state."""
        accent = accent or uk.Theme.GOLD
        col = uk.Theme.DANGER_BRIGHT if danger else accent
        pad = 34 if (icon_fn is not None or icon_surface is not None) else 18
        tw = self.font_small.size(label)[0]
        bw = w or (tw + pad)
        rect = pygame.Rect(x, 7, bw, TOP_BAR_H - 14)
        hovered = self._hover(rect)

        if active:
            base = uk.lerp_color((22, 26, 35), col, 0.22)
            border = col
            border_w = 2
        else:
            base = uk.lerp_color((22, 26, 35), (34, 24, 24) if danger else (32, 37, 48),
                                 1.0 if hovered else 0.0)
            border = uk.lerp_color(uk.Theme.CARD_BORDER, col, 1.0 if hovered else 0.0)
            border_w = 1
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border,
                      border_width=border_w, radius=6, shadow=False)

        label_color = uk.Theme.TEXT_PRIMARY if (active or hovered) else uk.Theme.TEXT_SECONDARY
        if icon_surface is not None:
            if label:
                icon_rect = icon_surface.get_rect()
                icon_rect.midleft = (rect.x + 8, rect.centery)
                uk.blit_surface(screen, icon_surface, icon_rect, transient=False)
                surf = self.font_small.render(label, True, label_color)
                screen.blit(surf, (icon_rect.right + 5, rect.centery - surf.get_height() // 2))
            else:
                uk.blit_surface(screen, icon_surface, icon_surface.get_rect(center=rect.center),
                                transient=False)
        elif icon_fn is not None:
            icon_rect = pygame.Rect(0, 0, 15, 15)
            icon_rect.midleft = (rect.x + 8, rect.centery)
            icon_fn(screen, icon_rect, label_color)
            surf = self.font_small.render(label, True, label_color)
            screen.blit(surf, (icon_rect.right + 5, rect.centery - surf.get_height() // 2))
        else:
            surf = self.font_small.render(label, True, label_color)
            screen.blit(surf, surf.get_rect(center=rect.center))

        self.ui[key] = rect
        uk.register_hoverable(rect)
        return rect.right + 5

    def _icon_button(self, screen, rect: pygame.Rect, icon_fn=None, icon_surface=None,
                     accent=None, danger: bool = False) -> bool:
        """Small square icon button (list-row delete ×, tab ×, etc). Returns
        whether it's currently hovered, so callers can pick their own hover
        color for adjoining text if needed. `icon_surface` (a pre-rendered
        PNG) takes priority over `icon_fn` (a vector line-glyph) when both
        are given, same convention as _toolbar_button — and, like there,
        it's centered as-is rather than recolored per hover/danger state,
        since it's a fixed-color image rather than a drawn glyph."""
        accent = accent or uk.Theme.GOLD
        col = uk.Theme.DANGER_BRIGHT if danger else accent
        hovered = self._hover(rect)
        base = uk.lerp_color((26, 22, 22) if danger else (22, 26, 35),
                             (52, 26, 26) if danger else (34, 39, 50),
                             1.0 if hovered else 0.0)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, col, 1.0 if hovered else 0.0)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border,
                      border_width=1, radius=5, shadow=False)
        if icon_surface is not None:
            uk.blit_surface(screen, icon_surface, icon_surface.get_rect(center=rect.center),
                            transient=False)
        elif icon_fn is not None:
            icon_color = col if hovered else uk.Theme.TEXT_MUTED
            icon_fn(screen, rect.inflate(-int(rect.w * 0.32), -int(rect.h * 0.32)), icon_color)
        uk.register_hoverable(rect)
        return hovered

    def _pill_button(self, screen, rect: pygame.Rect, label: str,
                     icon_fn=None, icon_surface=None, accent=None, danger: bool = False,
                     active: bool = False) -> bool:
        """Medium action button used inside the right panel (Add Entity,
        Edit Path, Clear, OK/CANCEL, ...). Returns whether hovered.
        `icon_surface` (a pre-rendered PNG) takes priority over `icon_fn`
        (a vector line-glyph) when both are given, same convention as
        _toolbar_button."""
        accent = accent or uk.Theme.GOLD
        col = uk.Theme.DANGER_BRIGHT if danger else accent
        hovered = self._hover(rect)
        t = 1.0 if (hovered or active) else 0.0
        base = uk.lerp_color((22, 26, 35), (34, 24, 24) if danger else (30, 35, 46),
                             1.0 if active else (0.6 if hovered else 0.0))
        border = col if active else uk.lerp_color(uk.Theme.CARD_BORDER, col, t)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border,
                      border_width=(2 if active else 1), radius=8, shadow=False)
        if t > 0.01:
            uk.draw_soft_glow(screen, rect.center, max(rect.w, rect.h) // 2, col,
                              max_alpha=int(28 * t))
        label_color = uk.Theme.TEXT_PRIMARY if (hovered or active) else uk.Theme.TEXT_SECONDARY
        if icon_surface is not None:
            icon_rect = icon_surface.get_rect()
            icon_rect.midleft = (rect.x + 12, rect.centery)
            uk.blit_surface(screen, icon_surface, icon_rect, transient=False)
            surf = self.font_medium.render(label, True, label_color)
            screen.blit(surf, (icon_rect.right + 6, rect.centery - surf.get_height() // 2))
        elif icon_fn is not None:
            icon_rect = pygame.Rect(0, 0, 16, 16)
            icon_rect.midleft = (rect.x + 12, rect.centery)
            icon_fn(screen, icon_rect, label_color)
            surf = self.font_medium.render(label, True, label_color)
            screen.blit(surf, (icon_rect.right + 6, rect.centery - surf.get_height() // 2))
        else:
            surf = self.font_medium.render(label, True, label_color)
            screen.blit(surf, surf.get_rect(center=rect.center))
        uk.register_hoverable(rect)
        return hovered

    def _draw_field_row(self, screen, rect: pygame.Rect, label: str, has_value: bool,
                        placeholder: str, focused: bool = False,
                        accent=None) -> None:
        """Dropdown-shaped field row — the music picker, room pickers, etc.
        `label` is the current value (or placeholder text when has_value
        is False)."""
        accent = accent or uk.Theme.GOLD
        hovered = self._hover(rect)
        border = accent if focused else uk.lerp_color(
            uk.Theme.CARD_BORDER, accent, 0.6 if hovered else 0.0)
        base = uk.lerp_color((20, 23, 32), (27, 31, 42), 1.0 if (focused or hovered) else 0.0)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border,
                      border_width=(2 if focused else 1), radius=6, shadow=False)
        shown = label if has_value else placeholder
        col = uk.Theme.TEXT_PRIMARY if has_value else uk.Theme.TEXT_MUTED
        val_surf = self.font_medium.render(shown, True, col)
        clip = pygame.Rect(rect.x + 8, rect.y, rect.w - 30, rect.h)
        screen.set_clip(clip)
        screen.blit(val_surf, (rect.x + 8, rect.y + (rect.h - val_surf.get_height()) // 2))
        screen.set_clip(None)
        # Chevron: points down normally, up while the popup is open.
        arrow_rect = pygame.Rect(0, 0, 10, 10)
        arrow_rect.midright = (rect.right - 8, rect.centery)
        _draw_chevron_icon(screen, arrow_rect, uk.Theme.TEXT_MUTED,
                           direction=(-1 if focused else 1))
        uk.register_hoverable(rect)

    def _draw_text_caret(self, screen, x, y, height, color=None):
        if int(self.cursor_blink * 2) % 2 != 0:
            return
        color = color or uk.Theme.TEXT_PRIMARY
        uk.draw_rect_on(screen, color, pygame.Rect(int(x), int(y), 2, int(height)), 0, 0)

    def _draw_live_text_field(self, screen, font, text_rect, click_rect, live=True):
        """Shared tail for the field being typed into: records the field's
        font / text-start-x / hit-rect so click and drag handling can turn a
        mouse x into a caret index (see _text_index_from_x), then — when
        `live` — draws the selection highlight behind the text and the caret
        at self.cursor_pos. `text_rect` is where the text was just blitted;
        `click_rect` is what counts as "inside this field" for the mouse.

        `live=False` only records the geometry: the Location dialog's name
        field still needs it while the Room field has focus, so the click
        that moves focus back can already land the caret in the right spot."""
        self._active_edit_rect = click_rect
        self._active_edit_text_x = text_rect.x
        self._active_edit_font = font
        if not live:
            return

        if self._has_text_selection():
            s, e = self._text_selection_range()
            sx = text_rect.x + (font.size(self.text_input[:s])[0] if s else 0)
            ex = text_rect.x + (font.size(self.text_input[:e])[0] if e else 0)
            sel_rect = pygame.Rect(sx, text_rect.y, max(1, ex - sx), text_rect.height)
            uk.draw_rect_on(screen, (*uk.Theme.KI_BLUE, 90), sel_rect, 0, 0)

        caret_w = font.size(self.text_input[:self.cursor_pos])[0] if self.cursor_pos else 0
        self._draw_text_caret(screen, text_rect.x + caret_w, text_rect.y, text_rect.height)

    def _draw_range_slider(self, screen, x, y, w, value, vmin, vmax, dragging: bool,
                           accent=None, label: str = None) -> tuple[pygame.Rect, int, int]:
        """Horizontal slider used for the height controls (location dialog
        and entity panel). Returns (hit_rect, track_x, track_w) so callers
        can stash them for their own drag-update math."""
        accent = accent or uk.Theme.GOLD
        track_h = 8
        track_rect = pygame.Rect(x, y, w, track_h)
        uk.draw_rect_on(screen, uk.Theme.CARD_BG[:3], track_rect, 0, 4)
        t = 0.0 if vmax == vmin else max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))
        thumb_x = int(x + t * w)
        if thumb_x > x:
            uk.draw_rect_on(screen, accent, pygame.Rect(x, y, thumb_x - x, track_h), 0, 4)
        uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, track_rect, 1, 4)
        uk.draw_line_on(screen, uk.Theme.TEXT_DIM, (x, y - 3), (x, y + track_h + 3), 1)

        thumb_r = 8
        mx, my = pygame.mouse.get_pos()
        hovered = abs(mx - thumb_x) <= thumb_r + 4 and abs(my - (y + track_h // 2)) <= thumb_r + 6
        thumb_col = accent if (dragging or hovered) else uk.Theme.TEXT_PRIMARY
        uk.draw_circle_on(screen, thumb_col, (thumb_x, y + track_h // 2), thumb_r)
        uk.draw_circle_on(screen, uk.Theme.BG_BOTTOM, (thumb_x, y + track_h // 2), thumb_r - 3)

        val_surf = self.font_small.render(str(int(value)), True, uk.Theme.TEXT_PRIMARY)
        screen.blit(val_surf, val_surf.get_rect(centerx=thumb_x, bottom=y - 6))
        min_s = self.font_small.render(str(vmin), True, uk.Theme.TEXT_DIM)
        max_s = self.font_small.render(str(vmax), True, uk.Theme.TEXT_DIM)
        screen.blit(min_s, (x, y + track_h + 6))
        screen.blit(max_s, (x + w - max_s.get_width(), y + track_h + 6))

        hit = pygame.Rect(x, y - thumb_r, w, track_h + thumb_r * 2)
        uk.register_hoverable(hit)
        return hit, x, w

    def _draw_option_popup(self, screen, anchor: pygame.Rect, names: list,
                           current_name, scroll: int, max_visible: int,
                           rects_dict: dict, key_prefix: str, accent=None,
                           empty_text: str = '(none found)') -> pygame.Rect:
        """Floating option list anchored under a field row — shared by the
        map-music, entity-room and location-room dropdowns. Populates
        `rects_dict[f'{key_prefix}{absolute_index}']` for every visible row
        so the existing click/scroll handlers (which already look up rects
        by exactly those keys) keep working unchanged."""
        accent = accent or uk.Theme.GOLD
        mx, my = pygame.mouse.get_pos()
        item_h = 26
        visible = names[scroll:scroll + max_visible]
        list_h = (max(1, len(visible)) * item_h) + 4
        list_rect = pygame.Rect(anchor.x, anchor.bottom + 4, anchor.w, list_h)
        if list_rect.bottom > self.screen_height - 8:
            list_rect.y = max(TOP_BAR_H + 4, anchor.top - list_h - 4)

        uk.draw_panel(screen, list_rect, bg=uk.Theme.PANEL_BG, border=accent,
                      border_width=1, radius=8)

        if not names:
            s = self.font_small.render(empty_text, True, uk.Theme.TEXT_MUTED)
            screen.blit(s, (list_rect.x + 8, list_rect.y + 6))
            return list_rect

        for i, name in enumerate(visible):
            idx = scroll + i
            item_rect = pygame.Rect(list_rect.x + 2, list_rect.y + 2 + i * item_h,
                                    list_rect.w - 4, item_h)
            is_cur = (name == current_name)
            hovered = item_rect.collidepoint(mx, my)
            if is_cur:
                uk.draw_rect_on(screen, accent, item_rect, 0, 5)
            elif hovered:
                uk.draw_rect_on(screen, uk.Theme.CARD_BG_HOVER[:3], item_rect, 0, 5)
            col = (uk.Theme.BG_BOTTOM if is_cur else
                   uk.Theme.TEXT_PRIMARY if hovered else uk.Theme.TEXT_SECONDARY)
            s = self.font_small.render(name, True, col)
            screen.blit(s, (item_rect.x + 8, item_rect.y + (item_h - s.get_height()) // 2))
            rects_dict[f'{key_prefix}{idx}'] = item_rect
            uk.register_hoverable(item_rect)

        if len(names) > max_visible:
            end = min(scroll + max_visible, len(names))
            hint = self.font_small.render(
                f'\u2191\u2193 scroll  ({scroll + 1}\u2013{end} of {len(names)})',
                True, uk.Theme.TEXT_DIM)
            screen.blit(hint, (list_rect.x + 4, list_rect.bottom + 2))
        return list_rect

    # ── top bar ───────────────────────────────────────────────────────────────

    def _draw_top_bar(self, screen: pygame.Surface):
        self.ui.clear()
        bar = pygame.Rect(0, 0, self.screen_width, TOP_BAR_H)
        uk.draw_rect_on(screen, (12, 15, 23), bar, 0, 0)
        uk.draw_rect_on(screen, (43, 49, 63), pygame.Rect(0, TOP_BAR_H - 1, self.screen_width, 1), 0, 0)

        x = 8
        x = self._toolbar_button(screen, x, '', 'btn_back', icon_surface=self._back_icon, w=30)
        x += 6
        uk.draw_rect_on(screen, (43, 49, 63), pygame.Rect(x, 8, 1, TOP_BAR_H - 16), 0, 0)
        x += 8
        x = self._toolbar_button(screen, x, 'New', 'btn_new', w=68)
        x = self._toolbar_button(screen, x, 'Save', 'btn_save', w=76)
        x += 8
        uk.draw_rect_on(screen, (43, 49, 63), pygame.Rect(x, 8, 1, TOP_BAR_H - 16), 0, 0)
        x += 8

        # ── Right-anchored: zoom + mode buttons ──────────────────────────────
        # Pinned to the screen's own right edge — NOT to vp_w/self.panel_w —
        # so this block sits in a fixed spot at the top-right of the whole
        # editor and doesn't slide around as the panel splitter is dragged.
        # Drawn before the map tabs below so its leftmost extent (right_x,
        # once this loop is done) is known and the tabs can be kept clear
        # of it.
        right_x = self.screen_width - 8

        zoom_label = self.font_small.render(f'{self.zoom}\u00d7', True, uk.Theme.TEXT_MUTED)
        right_x -= zoom_label.get_width()
        screen.blit(zoom_label, (right_x, (TOP_BAR_H - zoom_label.get_height()) // 2))
        right_x -= 8

        zi_w = 30
        right_x -= zi_w
        self._toolbar_button(screen, right_x, '', 'btn_zoom_in',
                              icon_surface=self._zoom_in_icon, w=zi_w)
        right_x -= 4
        zo_w = 30
        right_x -= zo_w
        self._toolbar_button(screen, right_x, '', 'btn_zoom_out',
                              icon_surface=self._zoom_out_icon, w=zo_w)
        right_x -= 14
        uk.draw_rect_on(screen, (43, 49, 63), pygame.Rect(right_x, 8, 1, TOP_BAR_H - 16), 0, 0)
        right_x -= 10

        # Mode buttons, right-to-left: Location, Entity, Paint, Scouter.
        # Text-only — no icon_fn passed, so these stay flush left/right
        # padded like any label-only toolbar pill (see the `pad` line in
        # _toolbar_button: 18px for text-only vs 34px when an icon is drawn).
        for mode_key, ui_key in (('location', 'btn_mode_location'),
                                  ('entity',   'btn_mode_entity'),
                                  ('paint',    'btn_mode_paint'),
                                  ('scouter',  'btn_mode_scouter')):
            label, _icon_fn, accent = self.MODE_INFO[mode_key]
            bw = self.font_small.size(label)[0] + 18
            right_x -= bw
            self._toolbar_button(screen, right_x, label, ui_key,
                                  active=(self.mode == mode_key), w=bw, accent=accent)
            right_x -= 6

        # ── Map tabs (scrollable) ───────────────────────────────────────────
        TAB_AREA_RIGHT = right_x - 12   # keep tabs clear of the fixed right block above
        TAB_DELETE_W = 20
        ARROW_W = 22
        max_scroll = max(0, len(self.maps) - 1)
        self._map_tab_scroll = max(0, min(self._map_tab_scroll, max_scroll))

        tab_x = x
        need_left = self._map_tab_scroll > 0
        if need_left:
            tab_x += ARROW_W + 4

        tabs_visible = []
        scan_x = tab_x
        for i in range(self._map_tab_scroll, len(self.maps)):
            wm = self.maps[i]
            label = wm.name[:14]
            tab_w = min(140, self.font_small.size(label)[0] + 20 + TAB_DELETE_W)
            if scan_x + tab_w + ARROW_W + 4 > TAB_AREA_RIGHT:
                break
            tabs_visible.append((i, label, tab_w))
            scan_x += tab_w + 6
        need_right = (self._map_tab_scroll + len(tabs_visible)) < len(self.maps)

        if need_left:
            x = self._toolbar_button(screen, x, '', 'btn_tab_scroll_left',
                                      icon_fn=lambda s, r, c: _draw_chevron_icon(s, r, c, -1), w=ARROW_W)

        for i, label, tab_w in tabs_visible:
            is_active = (i == self.current_map_idx)
            rect = pygame.Rect(x, 7, tab_w, TOP_BAR_H - 14)
            hovered = self._hover(rect) and not is_active
            accent = uk.Theme.GOLD
            if is_active:
                base = uk.lerp_color((22, 26, 35), accent, 0.22)
                border = accent
                border_w = 2
            else:
                base = uk.lerp_color((22, 26, 35), (30, 35, 46), 1.0 if hovered else 0.0)
                border = uk.lerp_color(uk.Theme.CARD_BORDER, accent, 1.0 if hovered else 0.0)
                border_w = 1
            uk.draw_panel(screen, rect, bg=(*base, 255), border=border,
                          border_width=border_w, radius=6, shadow=False)

            del_r = pygame.Rect(rect.right - TAB_DELETE_W - 3, rect.y + 3, TAB_DELETE_W, rect.h - 6)
            self._icon_button(screen, del_r, icon_surface=self._close_icon, danger=True)

            name_color = uk.Theme.TEXT_PRIMARY if (is_active or hovered) else uk.Theme.TEXT_SECONDARY
            name_surf = self.font_small.render(label, True, name_color)
            screen.set_clip(pygame.Rect(rect.x + 6, rect.y, rect.w - TAB_DELETE_W - 12, rect.h))
            screen.blit(name_surf, (rect.x + 6, rect.centery - name_surf.get_height() // 2))
            screen.set_clip(None)

            self.ui[f'map_tab_{i}'] = rect
            self.ui[f'map_del_{i}'] = del_r
            uk.register_hoverable(rect)
            x = rect.right + 6

        if need_right:
            x = self._toolbar_button(screen, x, '', 'btn_tab_scroll_right',
                                      icon_fn=lambda s, r, c: _draw_chevron_icon(s, r, c, 1), w=ARROW_W)

        # ── Frame controls ───────────────────────────────────────────────────
        wm_cur = self.current_map
        if wm_cur is not None:
            x += 10
            uk.draw_rect_on(screen, (43, 49, 63), pygame.Rect(x, 8, 1, TOP_BAR_H - 16), 0, 0)
            x += 10
            x = self._toolbar_button(screen, x, '', 'btn_frame_prev',
                                      icon_fn=lambda s, r, c: _draw_chevron_icon(s, r, c, -1), w=24)
            fc_label = f'Frame {wm_cur.frame_idx + 1}/{wm_cur.frame_count}'
            fc_w = self.font_small.size(fc_label)[0] + 16
            fc_rect = pygame.Rect(x, 7, fc_w, TOP_BAR_H - 14)
            uk.draw_panel(screen, fc_rect, bg=(*uk.Theme.CARD_BG[:3], 255),
                          border=uk.Theme.CARD_BORDER, border_width=1, radius=6, shadow=False)
            fc_surf = self.font_small.render(fc_label, True, uk.Theme.TEXT_SECONDARY)
            screen.blit(fc_surf, fc_surf.get_rect(center=fc_rect.center))
            self.ui['frame_label'] = fc_rect
            x = fc_rect.right + 5
            x = self._toolbar_button(screen, x, '', 'btn_frame_next',
                                      icon_fn=lambda s, r, c: _draw_chevron_icon(s, r, c, 1), w=24)
            x = self._toolbar_button(screen, x, '', 'btn_frame_add', icon_surface=self._plus_icon, w=30)
            if wm_cur.frame_count > 1:
                x = self._toolbar_button(screen, x, '', 'btn_frame_del',
                                          icon_surface=self._close_icon, w=30, danger=True)

    # ── right panel ───────────────────────────────────────────────────────────

    def _draw_panel(self, screen: pygame.Surface):
        if not self.panel_open:
            return
        panel_rect = pygame.Rect(self.vp_x + self.vp_w, TOP_BAR_H,
                                 self.panel_w, self.screen_height - TOP_BAR_H)
        uk.draw_rect_on(screen, uk.Theme.PANEL_BG[:3], panel_rect, 0, 0)

        # Splitter border — thickens/brightens on hover or while actively
        # dragging, as a visual affordance that it can be grabbed.
        mx, my = pygame.mouse.get_pos()
        splitter_live = self._panel_resize_active or self._over_splitter(mx, my)
        border_w = 4 if splitter_live else 2
        border_col = uk.Theme.GOLD_BRIGHT if splitter_live else uk.Theme.GOLD
        uk.draw_rect_on(screen, border_col,
                        pygame.Rect(panel_rect.x - (border_w - 2), panel_rect.y, border_w, panel_rect.h), 0, 0)

        px = panel_rect.x + 14
        py = panel_rect.y + 12

        py = self._draw_map_music_row(screen, px, py)

        if self.mode == 'paint':
            self._draw_paint_panel(screen, px, py)
        elif self.mode == 'entity':
            self._draw_entity_panel(screen, px, py)
        elif self.mode == 'scouter':
            self._draw_scouter_panel(screen, px, py)
        else:
            self._draw_location_panel(screen, px, py)

        if self.music_dropdown_open:
            self._draw_music_dropdown_popup(screen, px)

    def _draw_map_music_row(self, screen: pygame.Surface, px: int, py: int) -> int:
        wm = self.current_map
        mx2, my2 = pygame.mouse.get_pos()
        row_w = self.panel_layout_w - 30

        lbl = self.font_small.render('MODE7 MUSIC', True, uk.Theme.TEXT_DIM)
        screen.blit(lbl, (px, py))
        py += 18

        btn_rect = pygame.Rect(px, py, row_w, 28)
        track = wm.music if wm else ''
        self._draw_field_row(screen, btn_rect, track, bool(track), '<no music>',
                             focused=self.music_dropdown_open)
        self.ui['music_dropdown_btn'] = btn_rect
        py += 34

        if wm and wm.music:
            clr_rect = pygame.Rect(px, py, row_w, 18)
            hovered = clr_rect.collidepoint(mx2, my2)
            clr_lbl = self.font_small.render(
                '\u00d7 clear music', True,
                uk.Theme.DANGER_BRIGHT if hovered else uk.Theme.TEXT_MUTED)
            screen.blit(clr_lbl, (px, py))
            self.ui['music_clear'] = clr_rect
            uk.register_hoverable(clr_rect)
            py += 22
        else:
            self.ui.pop('music_clear', None)

        py += 6
        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER, pygame.Rect(px, py, row_w, 1), 0, 0)
        py += 12
        return py

    def _draw_music_dropdown_popup(self, screen: pygame.Surface, px: int):
        btn_rect = self.ui.get('music_dropdown_btn')
        if not btn_rect:
            return
        names = self.music_dropdown_names
        item_h = 26
        max_rows_on_screen = max(1, (self.screen_height - btn_rect.bottom - 10) // item_h)
        visible_rows = max(1, min(max_rows_on_screen, 8, len(names) or 1))
        self._music_dropdown_visible_rows = visible_rows

        max_scroll = max(0, len(names) - visible_rows)
        self.music_dropdown_scroll = max(0, min(self.music_dropdown_scroll, max_scroll))

        for key in [k for k in self.ui if k.startswith('music_dd_')]:
            del self.ui[key]

        wm = self.current_map
        cur_track = wm.music if wm else ''
        list_rect = self._draw_option_popup(
            screen, btn_rect, names, cur_track, self.music_dropdown_scroll,
            visible_rows, self.ui, 'music_dd_', empty_text='<no tracks found>')
        self.ui['music_dropdown_list_rect'] = list_rect

    # ── entity panel ─────────────────────────────────────────────────────────

    def _draw_entity_panel(self, screen: pygame.Surface, px: int, py: int):
        wm = self.current_map
        row_w = self.panel_layout_w - 30
        accent = uk.Theme.KI_BLUE

        hdr = self.font_large.render('ENTITIES', True, accent)
        screen.blit(hdr, (px, py));  py += 24

        if self.entity_placing:
            hint = self.font_small.render(
                'Left-click map \u2192 add waypoint', True, accent)
            screen.blit(hint, (px, py));  py += 16
            hint2 = self.font_small.render('Right-click \u2192 stop', True, uk.Theme.TEXT_DIM)
            screen.blit(hint2, (px, py));  py += 20
        else:
            hint = self.font_small.render('Select entity, then Edit Path', True, uk.Theme.TEXT_DIM)
            screen.blit(hint, (px, py));  py += 22

        add_rect = pygame.Rect(px, py, row_w, 30)
        self._pill_button(screen, add_rect, 'Add Entity', icon_fn=_draw_plus_icon, accent=accent)
        self.ui['btn_entity_add'] = add_rect
        py += 36

        entities = wm.entities if wm else []
        panel_bottom = self.screen_height - TOP_BAR_H
        list_clip = pygame.Rect(px - 4, py, self.panel_layout_w - 12,
                                max(0, min(panel_bottom - py - 10, len(entities) * 40 + 10)))
        screen.set_clip(list_clip)
        mx2, my2 = pygame.mouse.get_pos()
        for i, e in enumerate(entities):
            is_sel = (i == self.entity_selected_idx)
            row_rect = pygame.Rect(px - 4, py, self.panel_layout_w - 30, 36)
            hovered = row_rect.collidepoint(mx2, my2)
            base = uk.lerp_color((22, 26, 35), accent, 0.22 if is_sel else (0.5 if hovered else 0.0))
            border = accent if is_sel else uk.lerp_color(uk.Theme.CARD_BORDER, accent, 1.0 if hovered else 0.0)
            uk.draw_panel(screen, row_rect, bg=(*base, 255), border=border,
                          border_width=(2 if is_sel else 1), radius=6, shadow=False)

            vs = self._get_vehicle_sprite(e.sprite) if e.sprite else None
            thumb = vs.get_panel_thumb(28) if vs else None
            thumb_rect = pygame.Rect(row_rect.x + 5, row_rect.y + 4, 28, 28)
            if thumb:
                screen.blit(thumb, thumb_rect)
            else:
                uk.draw_panel(screen, thumb_rect, bg=(*uk.Theme.CARD_BG[:3], 255),
                              border=uk.Theme.CARD_BORDER, border_width=1, radius=5, shadow=False)
                _draw_vehicle_icon(screen, thumb_rect.inflate(-6, -6), uk.Theme.TEXT_MUTED)

            name_col = uk.Theme.GOLD_BRIGHT if is_sel else uk.Theme.TEXT_PRIMARY
            name_s = self.font_small.render(e.name or f'entity_{i}', True, name_col)
            pts_s = self.font_small.render(
                f'{len(e.path)} pts  {"loop" if e.closed else "ping-pong"}',
                True, uk.Theme.TEXT_MUTED)
            screen.blit(name_s, (thumb_rect.right + 8, row_rect.y + 4))
            screen.blit(pts_s,  (thumb_rect.right + 8, row_rect.y + 19))

            if e.room:
                dot_x = row_rect.right - 40
                dot_y = row_rect.y + 10
                uk.draw_circle_on(screen, accent, (dot_x, dot_y), 5)
                uk.draw_circle_on(screen, uk.Theme.BG_BOTTOM, (dot_x, dot_y), 5, 1)

            del_rect = pygame.Rect(row_rect.right - 26, row_rect.y + 8, 20, 20)
            self._icon_button(screen, del_rect, icon_surface=self._close_icon, danger=True)

            self.ui[f'entity_row_{i}'] = row_rect
            self.ui[f'entity_del_{i}'] = del_rect
            uk.register_hoverable(row_rect)
            py += 40
        screen.set_clip(None)
        py += 8

        eidx = self.entity_selected_idx
        if eidx is not None and wm and 0 <= eidx < len(wm.entities):
            e = wm.entities[eidx]
            uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER, pygame.Rect(px - 4, py, row_w + 6, 1), 0, 0)
            py += 10

            sel_lbl = self.font_small.render(f'Selected: {e.name}', True, uk.Theme.GOLD_BRIGHT)
            screen.blit(sel_lbl, (px, py));  py += 22

            half_w = (row_w - 8) // 2
            if self.entity_placing:
                done_rect = pygame.Rect(px, py, half_w, 28)
                self._pill_button(screen, done_rect, 'Done', icon_fn=_draw_check_icon,
                                  accent=(90, 210, 110), active=True)
                self.ui['btn_entity_done'] = done_rect
            else:
                place_rect = pygame.Rect(px, py, half_w, 28)
                self._pill_button(screen, place_rect, 'Edit Path', accent=accent)
                self.ui['btn_entity_place'] = place_rect

            clear_rect = pygame.Rect(px + half_w + 8, py, half_w, 28)
            self._pill_button(screen, clear_rect, 'Clear Path', icon_surface=self._close_icon, danger=True)
            self.ui['btn_entity_clear'] = clear_rect
            py += 34

            closed_rect = pygame.Rect(px, py, row_w, 28)
            closed_label = 'Closed Loop' if e.closed else 'Ping-Pong (open)'
            self._pill_button(screen, closed_rect, closed_label, accent=accent, active=e.closed)
            self.ui['btn_entity_closed'] = closed_rect
            py += 36

            EHEIGHT_MIN, EHEIGHT_MAX = 0, 2000
            h_lbl = self.font_small.render('HEIGHT (0 = GROUND)', True, uk.Theme.TEXT_DIM)
            screen.blit(h_lbl, (px, py));  py += 20
            slider_hit, track_x, track_w = self._draw_range_slider(
                screen, px, py, row_w, e.height, EHEIGHT_MIN, EHEIGHT_MAX,
                self._entity_height_slider_drag, accent=accent)
            self.ui['entity_height_slider'] = slider_hit
            self._entity_height_slider_track_x = track_x
            self._entity_height_slider_track_w = track_w
            py += 34

            room_lbl = self.font_small.render('LINKED ROOM', True, uk.Theme.TEXT_DIM)
            screen.blit(room_lbl, (px, py));  py += 18
            btn_w = row_w - 26
            btn_rect = pygame.Rect(px, py, btn_w, 28)
            self._draw_field_row(screen, btn_rect, e.room, bool(e.room),
                                 '(no room \u2014 no collision)',
                                 focused=self.entity_room_dropdown_open, accent=accent)
            self.ui['entity_room_btn'] = btn_rect
            clr_r = pygame.Rect(btn_rect.right + 6, py, 20, 28)
            self._icon_button(screen, clr_r, icon_surface=self._close_icon, danger=True)
            self.ui['entity_room_clear'] = clr_r
            py += 34

            if e.room:
                for hl in ('In the room editor, place a',
                           'World Map Object (world_map)',
                           f'and set entity_name = "{e.name}"',
                           'to mark where the player spawns.'):
                    hs = self.font_small.render(hl, True, uk.Theme.TEXT_DIM)
                    screen.blit(hs, (px, py));  py += 14
                py += 4

            if self.vehicle_names:
                picker_lbl = self.font_small.render('SPRITE', True, uk.Theme.TEXT_DIM)
                screen.blit(picker_lbl, (px, py));  py += 18
                VCELL = 40
                VCOLS = max(1, row_w // (VCELL + 6))
                picker_clip = pygame.Rect(px - 4, py, self.panel_layout_w - 12, self.screen_height - py - 10)
                screen.set_clip(picker_clip)
                for vi, vname in enumerate(self.vehicle_names):
                    vcol, vrow = vi % VCOLS, vi // VCOLS
                    cx = px + vcol * (VCELL + 6)
                    cy = py + vrow * (VCELL + 6)
                    if cy + VCELL > self.screen_height:
                        break
                    cell_rect = pygame.Rect(cx, cy, VCELL, VCELL)
                    v_sel = (vname == e.sprite)
                    v_hover = self._hover(cell_rect)
                    base = uk.lerp_color((22, 26, 35), accent, 0.3 if v_sel else (0.5 if v_hover else 0.0))
                    border = accent if v_sel else uk.lerp_color(uk.Theme.CARD_BORDER, accent, 1.0 if v_hover else 0.0)
                    uk.draw_panel(screen, cell_rect, bg=(*base, 255), border=border,
                                  border_width=(2 if v_sel else 1), radius=6, shadow=False)
                    vs = self._get_vehicle_sprite(vname)
                    thumb = vs.get_panel_thumb(VCELL - 8) if vs else None
                    if thumb:
                        screen.blit(thumb, thumb.get_rect(center=cell_rect.center))
                    else:
                        _draw_vehicle_icon(screen, cell_rect.inflate(-10, -10), uk.Theme.TEXT_MUTED)
                    if v_hover:
                        tip = self.font_small.render(vname, True, uk.Theme.TEXT_DIM)
                        screen.blit(tip, (cx, cy + VCELL + 2))
                    self.ui[f'vehicle_pick_{vi}'] = cell_rect
                    uk.register_hoverable(cell_rect)
                screen.set_clip(None)
            else:
                no_v = self.font_small.render(f'(no sprites in {VEHICLE_DIR})', True, uk.Theme.TEXT_DIM)
                screen.blit(no_v, (px, py))

        if (self.entity_room_dropdown_open and eidx is not None
                and wm and 0 <= eidx < len(wm.entities)):
            room_names = self._get_room_names()
            for key in [k for k in self.ui if k.startswith('entity_room_dd_')]:
                del self.ui[key]
            btn_ref = self.ui.get('entity_room_btn')
            if btn_ref:
                self._draw_option_popup(
                    screen, btn_ref, room_names, wm.entities[eidx].room,
                    self.entity_room_dropdown_scroll, 8, self.ui,
                    'entity_room_dd_', accent=accent, empty_text='(no rooms found)')

    # ── paint panel ──────────────────────────────────────────────────────────

    def _draw_paint_panel(self, screen: pygame.Surface, px: int, py: int):
        ts = self.current_tileset
        row_w = self.panel_layout_w - 30
        if not ts:
            surf = self.font_medium.render('No tilesets found', True, uk.Theme.TEXT_MUTED)
            screen.blit(surf, (px, py))
            self._palette_grid_origin = None
            return

        name_surf = self.font_medium.render(
            f'{ts.name}   [{self.tileset_idx + 1}/{len(self.tilesets)}]', True, uk.Theme.TEXT_PRIMARY)
        screen.blit(name_surf, (px, py));  py += 22
        hint = self.font_small.render('TAB to switch tileset', True, uk.Theme.TEXT_DIM)
        screen.blit(hint, (px, py));  py += 18

        grid_x, grid_y = px, py
        self._palette_grid_origin = (grid_x, grid_y)
        palette_h = self._palette_visible_height()
        panel_rect = pygame.Rect(grid_x - 6, grid_y - 4, self.panel_layout_w - 20, palette_h + 8)
        uk.draw_panel(screen, panel_rect, bg=(*uk.Theme.CARD_BG[:3], 255),
                      border=uk.Theme.CARD_BORDER, border_width=1, radius=8, shadow=False)
        uk.register_hoverable(panel_rect)
        clip = pygame.Rect(grid_x - 2, grid_y, self.panel_layout_w - 24, palette_h)
        screen.set_clip(clip)

        min_tx = min(self.sel_tx, self.sel_end_tx)
        max_tx = max(self.sel_tx, self.sel_end_tx)
        min_ty = min(self.sel_ty, self.sel_end_ty)
        max_ty = max(self.sel_ty, self.sel_end_ty)

        if ts.image:
            pal_surf = ts.get_palette_surface(PALETTE_CELL)
            if pal_surf:
                ix = grid_x - self.palette_scroll_x
                iy = grid_y - self.palette_scroll_y
                screen.blit(pal_surf, (ix, iy))

                sel_x = ix + min_tx * PALETTE_CELL
                sel_y = iy + min_ty * PALETTE_CELL
                sel_w = (max_tx - min_tx + 1) * PALETTE_CELL
                sel_h = (max_ty - min_ty + 1) * PALETTE_CELL
                if self._sel_surf_size != (sel_w, sel_h):
                    self._sel_surf = pygame.Surface((sel_w, sel_h), pygame.SRCALPHA)
                    self._sel_surf.fill((*uk.Theme.GOLD, 60))
                    self._sel_surf_size = (sel_w, sel_h)
                screen.blit(self._sel_surf, (sel_x, sel_y))
                screen.draw_rect(uk.Theme.GOLD, (sel_x, sel_y, sel_w, sel_h), 2)

        screen.set_clip(None)

    # ── scouter panel ────────────────────────────────────────────────────────

    def _draw_scouter_panel(self, screen: pygame.Surface, px: int, py: int):
        wm = self.current_map
        row_w = self.panel_layout_w - 30
        accent = self.MODE_INFO['scouter'][2]

        title = self.font_large.render('Scouter Paint', True, accent)
        screen.blit(title, (px, py));  py += 22
        for line in ('Paints the silhouette shown on', "the Scouter's WORLD MAP screen."):
            s = self.font_small.render(line, True, uk.Theme.TEXT_DIM)
            screen.blit(s, (px, py));  py += 15
        py += 8

        label = self.font_small.render('BRUSH SIZE', True, uk.Theme.TEXT_DIM)
        screen.blit(label, (px, py));  py += 20
        bx = px
        for size in (1, 2, 4, 8):
            w = 46
            rect = pygame.Rect(bx, py, w, 28)
            self._pill_button(screen, rect, str(size), accent=accent, active=(self.scouter_brush == size))
            self.ui[f'btn_scouter_brush_{size}'] = rect
            bx += w + 6
        py += 36

        count = len(wm.scouter_paint) if wm else 0
        cnt_surf = self.font_small.render(f'{count} cells painted', True, uk.Theme.TEXT_DIM)
        screen.blit(cnt_surf, (px, py));  py += 24

        clr_rect = pygame.Rect(px, py, 120, 28)
        self._pill_button(screen, clr_rect, 'Clear all', icon_surface=self._close_icon, danger=True)
        self.ui['btn_scouter_clear'] = clr_rect

    # ── location panel ───────────────────────────────────────────────────────

    def _draw_location_panel(self, screen: pygame.Surface, px: int, py: int):
        wm = self.current_map
        accent = uk.Theme.GOLD
        header = self.font_large.render('LOCATIONS', True, accent)
        screen.blit(header, (px, py));  py += 24

        hint = self.font_small.render('Click map to place a pin', True, uk.Theme.TEXT_DIM)
        screen.blit(hint, (px, py));  py += 22

        if not wm or not wm.locations:
            none_s = self.font_small.render('(none yet)', True, uk.Theme.TEXT_DIM)
            screen.blit(none_s, (px, py))
            return

        clip_rect = pygame.Rect(px - 4, py, self.panel_layout_w - 12, self.screen_height - py - 20)
        screen.set_clip(clip_rect)
        mx2, my2 = pygame.mouse.get_pos()

        for i, loc in enumerate(wm.locations):
            is_sel = (loc is self.selected_loc)
            row_rect = pygame.Rect(px - 4, py, self.panel_layout_w - 30, 38)
            hovered = row_rect.collidepoint(mx2, my2)
            base = uk.lerp_color((22, 26, 35), accent, 0.22 if is_sel else (0.5 if hovered else 0.0))
            border = accent if is_sel else uk.lerp_color(uk.Theme.CARD_BORDER, accent, 1.0 if hovered else 0.0)
            uk.draw_panel(screen, row_rect, bg=(*base, 255), border=border,
                          border_width=(2 if is_sel else 1), radius=6, shadow=False)

            badge_cx, badge_cy = row_rect.x + 18, row_rect.centery
            badge_col = uk.Theme.GOLD_BRIGHT if is_sel else (232, 92, 92)
            uk.draw_circle_on(screen, badge_col, (badge_cx, badge_cy), 13)
            uk.draw_circle_on(screen, uk.Theme.BG_BOTTOM, (badge_cx, badge_cy), 13, 1)
            icon_stem = getattr(loc, 'icon', '')
            if icon_stem:
                icon_surf = self._get_icon(icon_stem, 20)
                if icon_surf:
                    screen.blit(icon_surf, icon_surf.get_rect(center=(badge_cx, badge_cy)))

            name_col = uk.Theme.GOLD_BRIGHT if is_sel else uk.Theme.TEXT_PRIMARY
            name_s = self.font_small.render(loc.name or '(unnamed)', True, name_col)
            room_s = self.font_small.render(f'\u2192 {loc.room or "(no room)"}', True, uk.Theme.TEXT_DIM)
            screen.blit(name_s, (row_rect.x + 34, row_rect.y + 5))
            screen.blit(room_s, (row_rect.x + 34, row_rect.y + 20))

            del_rect = pygame.Rect(row_rect.right - 26, row_rect.y + 9, 20, 20)
            self._icon_button(screen, del_rect, icon_surface=self._close_icon, danger=True)

            self.ui[f'loc_entry_{i}'] = row_rect
            self.ui[f'loc_del_{i}']   = del_rect
            uk.register_hoverable(row_rect)
            py += 44

        screen.set_clip(None)

    # ── dialogs ───────────────────────────────────────────────────────────────

    def _draw_dialog_base(self, screen: pygame.Surface, title: str,
                          w: int, h: int) -> tuple[int, int, int, pygame.Rect]:
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 165))
        screen.blit(overlay, (0, 0))
        ox, oy = self._dialog_pos_offset
        box_x = (self.screen_width - w) // 2 + ox
        box_y = (self.screen_height - h) // 2 + oy
        box_rect = pygame.Rect(box_x, box_y, w, h)
        uk.draw_panel(screen, box_rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=2, radius=10)
        title_s = self.font_title.render(title, True, uk.Theme.TEXT_PRIMARY)
        screen.blit(title_s, (box_x + (w - title_s.get_width()) // 2, box_y + 16))
        # Drag handle — the whole title strip is grabbable, like a window
        # title bar. Callers must stash this in self.loc_dialog_rects
        # under '_dragbar' themselves (AFTER their own rects-dict reset,
        # since this runs before that reset — see _draw_new_map_dialog /
        # _draw_loc_dialog) for _handle_dialog_drag_event to see it.
        drag_rect = pygame.Rect(box_x, box_y, w, 44)
        return box_x, box_y, box_x + 20, drag_rect

    def _draw_input_field(self, screen: pygame.Surface,
                          label: str, value: str, active: bool,
                          x: int, y: int, w: int) -> pygame.Rect:
        lbl = self.font_small.render(label.upper(), True, uk.Theme.TEXT_DIM)
        screen.blit(lbl, (x, y))
        field_rect = pygame.Rect(x, y + 18, w, 30)
        border = uk.Theme.GOLD if active else uk.Theme.CARD_BORDER
        uk.draw_panel(screen, field_rect, bg=(*uk.Theme.CARD_BG[:3], 255), border=border,
                      border_width=(2 if active else 1), radius=6, shadow=False)
        val_s = self.font_medium.render(value, True, uk.Theme.TEXT_PRIMARY)
        # An empty string renders as a 1x1 surface, which would leave the
        # caret 1px tall in an empty field — size the line from a reference
        # glyph in that case.
        line_h = val_s.get_height() if value else self.font_medium.size('A')[1]
        val_rect = pygame.Rect(field_rect.x + 8, field_rect.centery - line_h // 2,
                               val_s.get_width(), line_h)
        screen.blit(val_s, val_rect.topleft)
        self._draw_live_text_field(screen, self.font_medium, val_rect, field_rect, live=active)
        uk.register_hoverable(field_rect)
        return field_rect

    def _draw_new_map_dialog(self, screen: pygame.Surface):
        w, h = 420, 170
        bx, by, ix, drag_rect = self._draw_dialog_base(screen, 'NEW WORLD MAP', w, h)
        self.loc_dialog_rects = {}
        self.loc_dialog_rects['_dragbar'] = drag_rect

        field = self._draw_input_field(screen, 'Map name', self.new_map_name, True, ix, by + 56, w - 40)
        self.loc_dialog_rects['field'] = field

        ok_r  = pygame.Rect(bx + w // 2 - 110, by + h - 50, 100, 32)
        can_r = pygame.Rect(bx + w // 2 + 10,  by + h - 50, 100, 32)
        self._pill_button(screen, ok_r, 'Create', icon_fn=_draw_check_icon, active=True)
        self._pill_button(screen, can_r, 'Cancel', icon_surface=self._close_icon, danger=True)
        self.loc_dialog_rects['ok'] = ok_r
        self.loc_dialog_rects['cancel'] = can_r

        hint = self.font_small.render('Enter to confirm \u00b7 Esc to cancel', True, uk.Theme.TEXT_DIM)
        screen.blit(hint, (bx + (w - hint.get_width()) // 2, by + h - 16))

    def _draw_loc_dialog(self, screen: pygame.Surface):
        CELL, COLS = 38, 8
        n_icons = len(self.icon_names)
        icon_rows = max(1, math.ceil(n_icons / COLS)) if n_icons else 1
        w = max(480, COLS * (CELL + 6) + 40)
        h = 56 + 108 + 40 + 46 + 30 + icon_rows * (CELL + 6) + 24 + 48 + 20
        title = 'NEW LOCATION' if self.loc_dialog_is_new else 'EDIT LOCATION'
        bx, by, ix, drag_rect = self._draw_dialog_base(screen, title, w, h)
        self.loc_dialog_rects = {}
        self.loc_dialog_rects['_dragbar'] = drag_rect
        accent = uk.Theme.GOLD

        n_field = self._draw_input_field(
            screen, 'Location name', self.loc_dialog_name,
            self.loc_dialog_field == 'name', ix, by + 56, w - 40)
        self.loc_dialog_rects['field_name'] = n_field

        room_lbl = self.font_small.render('ROOM ID', True, uk.Theme.TEXT_DIM)
        screen.blit(room_lbl, (ix, by + 126))
        btn_rect = pygame.Rect(ix, by + 144, w - 40, 30)
        self._draw_field_row(screen, btn_rect, self.loc_dialog_room, bool(self.loc_dialog_room),
                             '(select a room\u2026)',
                             focused=(self.loc_dialog_field == 'room' or self.room_dropdown_open))
        self.loc_dialog_rects['field_room'] = btn_rect

        HEIGHT_MIN, HEIGHT_MAX = 0, 2000
        slider_lbl = self.font_small.render('HEIGHT (0 = GROUND)', True, uk.Theme.TEXT_DIM)
        screen.blit(slider_lbl, (ix, by + 204))
        slider_hit, track_x, track_w = self._draw_range_slider(
            screen, ix, by + 224, w - 40, self.loc_dialog_height,
            HEIGHT_MIN, HEIGHT_MAX, self._height_slider_drag, accent=accent)
        self.loc_dialog_rects['height_slider'] = slider_hit
        self._height_slider_track_x = track_x
        self._height_slider_track_w = track_w

        icon_lbl_y = by + 268
        icon_lbl = self.font_small.render('ICON', True, uk.Theme.TEXT_DIM)
        screen.blit(icon_lbl, (ix, icon_lbl_y))
        picker_y = icon_lbl_y + 18

        if not self.icon_names:
            no_s = self.font_small.render(f'(no icons found in {ICON_DIR})', True, uk.Theme.TEXT_DIM)
            screen.blit(no_s, (ix, picker_y))
        else:
            mx, my = pygame.mouse.get_pos()
            for i, stem in enumerate(self.icon_names):
                col, row = i % COLS, i // COLS
                cx = ix + col * (CELL + 6)
                cy = picker_y + row * (CELL + 6)
                cell_rect = pygame.Rect(cx, cy, CELL, CELL)
                selected = (stem == self.loc_dialog_icon)
                hovered = cell_rect.collidepoint(mx, my)
                base = uk.lerp_color((22, 26, 35), accent, 0.3 if selected else (0.5 if hovered else 0.0))
                border = accent if selected else uk.lerp_color(uk.Theme.CARD_BORDER, accent, 1.0 if hovered else 0.0)
                uk.draw_panel(screen, cell_rect, bg=(*base, 255), border=border,
                              border_width=(2 if selected else 1), radius=6, shadow=False)
                surf = self._get_icon(stem, CELL - 8)
                if surf:
                    screen.blit(surf, surf.get_rect(center=cell_rect.center))
                else:
                    fb = self.font_small.render(stem[:1].upper(), True, uk.Theme.TEXT_PRIMARY)
                    screen.blit(fb, fb.get_rect(center=cell_rect.center))
                if hovered:
                    tip = self.font_small.render(stem, True, uk.Theme.TEXT_DIM)
                    screen.blit(tip, (cx, cy + CELL + 2))
                self.loc_dialog_rects[f'icon_{i}'] = cell_rect
                uk.register_hoverable(cell_rect)

        ok_r  = pygame.Rect(bx + w // 2 - 110, by + h - 46, 100, 32)
        can_r = pygame.Rect(bx + w // 2 + 10,  by + h - 46, 100, 32)
        self._pill_button(screen, ok_r, 'OK')
        self._pill_button(screen, can_r, 'Cancel', danger=True)
        self.loc_dialog_rects['ok'] = ok_r
        self.loc_dialog_rects['cancel'] = can_r

        if self.room_dropdown_open:
            room_names = self._get_room_names()
            for key in [k for k in self.loc_dialog_rects if k.startswith('dropdown_')]:
                del self.loc_dialog_rects[key]
            self._draw_option_popup(
                screen, btn_rect, room_names, self.loc_dialog_room,
                self.room_dropdown_scroll, 8, self.loc_dialog_rects,
                'dropdown_', accent=accent, empty_text='(no rooms found)')