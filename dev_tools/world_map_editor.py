"""
dev_tools/world_map_editor.py

Standalone world-map editor. Accessible from the developer menu below the room editor.

Features
--------
* Create / switch between multiple named world maps (2896 × 2104 tiles).
* Paint 8 × 8 tiles from any tileset found in assets/tilesets/world_map/.
* Place named "location" pins on the map and assign them to in-game rooms.
* Ctrl+S saves; maps are stored as JSON in assets/world_maps/.

Controls (viewport)
-------------------
  Left-drag          Paint tiles (paint mode)
  Right-drag         Erase tiles (paint mode)
  Left-click         Place / select location pin (location mode)
  Middle-drag        Pan camera
  Scroll wheel       Zoom in / out
  TAB                Cycle tilesets (paint mode)
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

from objects.music_object import get_available_music_tracks

# ─────────────────────────────── constants ────────────────────────────────────

SAVE_DIR    = os.path.join("assets", "world_maps")
TILESET_DIR = os.path.join("assets", "tilesets", "world_map")

MAP_TILE_W   = 362   # map width in tiles
MAP_TILE_H   = 263   # map height in tiles
NATIVE_TILE  = 8      # source tile size in pixels

ZOOM_LEVELS  = [1, 2, 3, 4, 6, 8]
ZOOM_DEFAULT = 2      # index into ZOOM_LEVELS

PANEL_W      = 320    # right-panel width in pixels
TOP_BAR_H    = 44     # top bar height in pixels
PALETTE_CELL = 24     # how large each tile appears in the palette grid

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
        return max(0, min(self.frame_idx, len(self._frames) - 1))

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
        return wm


# ─────────────────────────────── tileset loader ───────────────────────────────

class WMTileset:
    """Minimal 8 × 8 tileset loader with a per-zoom surface cache."""

    def __init__(self, name: str, path: str):
        self.name  = name
        self.image: Optional[pygame.Surface] = None
        self.cols  = 0
        self.rows  = 0
        self._cache: dict[tuple, Optional[pygame.Surface]] = {}
        try:
            self.image = pygame.image.load(path).convert_alpha()
            w, h = self.image.get_size()
            self.cols = w // NATIVE_TILE
            self.rows = h // NATIVE_TILE
        except Exception as exc:
            print(f"[WorldMapEditor] Could not load tileset '{name}': {exc}")

    def get_tile(self, tx: int, ty: int, display_size: int
                 ) -> Optional[pygame.Surface]:
        """Return a scaled surface for tile (tx, ty); cached per display_size."""
        key = (tx, ty, display_size)
        if key not in self._cache:
            if (self.image is None or tx < 0 or ty < 0
                    or tx >= self.cols or ty >= self.rows):
                self._cache[key] = None
            else:
                rect = pygame.Rect(tx * NATIVE_TILE, ty * NATIVE_TILE,
                                   NATIVE_TILE, NATIVE_TILE)
                raw = self.image.subsurface(rect).copy()
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

    def get_frame(self, dir_row: int, frame_idx: int,
                  display_h: int) -> Optional[pygame.Surface]:
        """Return a scaled frame for *dir_row* (0-based) and *frame_idx*, cached."""
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

class WorldMapEditor:
    """Full-screen world-map tile editor."""

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.active        = False

        # Viewport geometry (recomputed if panel is hidden, but kept simple here)
        self.vp_x = 0
        self.vp_y = TOP_BAR_H
        self.vp_w = screen_width - PANEL_W
        self.vp_h = screen_height - TOP_BAR_H

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
        self.mode = 'paint'   # 'paint' | 'location' | 'entity'

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

        # ── Grid visibility ───────────────────────────────────────────────────
        self.show_grid = True

        # ── Draw caches (rebuilt only when inputs change) ─────────────────────
        self._sel_surf:       Optional[pygame.Surface] = None
        self._sel_surf_size:  tuple[int, int]          = (0, 0)
        self._vp_grid_surf:   Optional[pygame.Surface] = None
        self._vp_grid_ds:     int                      = 0

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

        # ── Map music dropdown (always visible in the panel, any mode) ────────
        self.music_dropdown_open          = False
        self.music_dropdown_names: list   = []
        self.music_dropdown_scroll        = 0
        self._music_dropdown_visible_rows = 8

        # ── New-map dialog ────────────────────────────────────────────────────
        self.new_map_dialog  = False
        self.new_map_name    = ''
        self._map_tab_scroll = 0   # index of the first visible map tab

        # ── Double-click detection ─────────────────────────────────────────────
        self._dbl_click_time: float                     = 0.0
        self._dbl_click_pos:  Optional[tuple[int, int]] = None

        # ── Double-click detection ────────────────────────────────────────────
        self._dbl_click_time:  float                    = 0.0
        self._dbl_click_pos:   Optional[tuple[int,int]] = None

        # ── Animation ────────────────────────────────────────────────────────
        self.cursor_blink = 0.0

        # ── Fonts & colors ────────────────────────────────────────────────────
        self.font_large  = pygame.font.Font(None, 28)
        self.font_medium = pygame.font.Font(None, 22)
        self.font_small  = pygame.font.Font(None, 18)

        self.C = {
            'bg':           (15,  15,  25),
            'panel':        (22,  22,  38),
            'panel_border': (55,  55,  85),
            'topbar':       (18,  18,  32),
            'accent':       (255, 215, 0),
            'text':         (240, 240, 240),
            'dim':          (150, 150, 180),
            'grid':         (38,  38,  58),
            'map_border':   (255, 215, 0),
            'pin':          (255, 75,  75),
            'pin_sel':      (255, 210, 60),
            'btn':          (48,  48,  72),
            'btn_hover':    (72,  72,  105),
            'btn_active':   (90,  65,  0),
            'success':      (70,  210, 70),
            'danger':       (210, 70,  70),
            'input_bg':     (35,  35,  55),
            'input_border': (80,  80,  120),
            'entity_path':  (80,  200, 255),
            'entity_node':  (255, 160, 40),
            'entity_sel':   (255, 220, 80),
        }

        # Cached UI rects for hit-testing
        self.ui: dict[str, pygame.Rect] = {}

    # ─────────────────────── public API ──────────────────────────────────────

    def toggle(self):
        self.active = not self.active

    def update(self, dt: float):
        if not self.active:
            return
        self.cursor_blink    = (self.cursor_blink    + dt) % 1.0
        self._entity_anim_t += dt

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

    def _in_viewport(self, mx: int, my: int) -> bool:
        return (self.vp_x <= mx < self.vp_x + self.vp_w
                and self.vp_y <= my < self.vp_y + self.vp_h)

    def _in_panel(self, mx: int, my: int) -> bool:
        return mx >= self.vp_x + self.vp_w

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

    def _get_icon(self, stem: str, size: int) -> Optional[pygame.Surface]:
        """Return a Surface for icon *stem* scaled to *size*×*size*; cached."""
        key = (stem, size)
        if key not in self._icon_cache:
            path = os.path.join(ICON_DIR, stem + '.png')
            try:
                raw = pygame.image.load(path).convert_alpha()
                self._icon_cache[key] = pygame.transform.smoothscale(raw, (size, size))
            except Exception:
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

    def _erase_tile(self, tx: int, ty: int):
        wm = self.current_map
        if wm:
            wm.tiles.pop((tx, ty), None)

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
        """Return a sorted list of track filenames from assets/audio/music/."""
        return get_available_music_tracks()

    def _cancel_loc_dialog(self):
        self.loc_dialog          = False
        self.room_dropdown_open  = False
        self.room_dropdown_scroll = 0
        self.room_dropdown_hover  = -1

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
        ))
        if len(self._undo_stack) > self._MAX_UNDO:
            self._undo_stack.pop(0)

    def _undo(self):
        """Restore the most recent snapshot from the undo stack."""
        wm = self.current_map
        if not wm or not self._undo_stack:
            return
        frames_snap, frame_idx_snap, locs_snap = self._undo_stack.pop()
        wm._frames    = frames_snap
        wm.frame_idx  = max(0, min(frame_idx_snap, len(wm._frames) - 1))
        wm.locations  = locs_snap
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
        grid_x = self.vp_x + self.vp_w + 10 - self.palette_scroll_x
        grid_y = TOP_BAR_H + 55 - self.palette_scroll_y
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
                return None
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
                self._handle_topbar_click(mx, my, event.button)
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
            elif event.button == 2:
                self.is_panning  = False
                self._ts_panning = False
            elif event.button == 3:
                self.is_erasing      = False
                self._last_paint_cell = None

        elif event.type == pygame.MOUSEMOTION:
            mx, my = event.pos

            if self.is_panning and self._pan_start_mouse and self._pan_start_cam:
                dx = self._pan_start_mouse[0] - mx
                dy = self._pan_start_mouse[1] - my
                self.cam_x = self._pan_start_cam[0] + dx
                self.cam_y = self._pan_start_cam[1] + dy
                self._clamp_camera()

            elif self._ts_panning and self._ts_pan_start_mouse and self._ts_pan_start_scroll:
                dx = self._ts_pan_start_mouse[0] - mx
                dy = self._ts_pan_start_mouse[1] - my
                ts = self.current_tileset
                max_scroll_y = max(0, ts.rows * PALETTE_CELL - 400) if ts else 0
                max_scroll_x = max(0, ts.cols * PALETTE_CELL - PANEL_W) if ts else 0
                self.palette_scroll_y = max(0, min(max_scroll_y, self._ts_pan_start_scroll[1] + dy))
                self.palette_scroll_x = max(0, min(max_scroll_x, self._ts_pan_start_scroll[0] + dx))

            elif self.is_painting and self._in_viewport(mx, my):
                tx, ty = self._screen_to_tile(mx, my)
                if (tx, ty) != self._last_paint_cell:
                    self._last_paint_cell = (tx, ty)
                    self._place_tiles(tx, ty)

            elif self.is_erasing and self._in_viewport(mx, my):
                tx, ty = self._screen_to_tile(mx, my)
                if (tx, ty) != self._last_paint_cell:
                    self._last_paint_cell = (tx, ty)
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
                max_scroll = max(0, ts.rows * PALETTE_CELL - 400)
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

    def _handle_topbar_click(self, mx: int, my: int, button: int):
        # Priority pass: delete-tab buttons sit inside tab rects, so check them first.
        for key, rect in self.ui.items():
            if key.startswith('map_del_') and rect.collidepoint(mx, my):
                idx = int(key.split('_')[-1])
                self._delete_map(idx)
                return
        for key, rect in self.ui.items():
            if not rect.collidepoint(mx, my):
                continue
            if key == 'btn_new':
                self.new_map_dialog = True
                self.new_map_name   = ''
            elif key == 'btn_save':
                self._save_current_map()
            elif key == 'btn_mode_paint':
                self.mode = 'paint'
                self._entity_stop_placing()
                self.entity_room_dropdown_open = False
            elif key == 'btn_mode_location':
                self.mode = 'location'
                self._entity_stop_placing()
                self.entity_room_dropdown_open = False
            elif key == 'btn_mode_entity':
                self.mode = 'entity'
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

    def _handle_new_map_dialog_event(self, event: pygame.event.Event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN and self.new_map_name.strip():
                self._create_map(self.new_map_name)
                self.new_map_dialog = False
            elif event.key == pygame.K_ESCAPE:
                self.new_map_dialog = False
            elif event.key == pygame.K_BACKSPACE:
                self.new_map_name = self.new_map_name[:-1]
            else:
                if len(self.new_map_name) < 32 and event.unicode.isprintable():
                    self.new_map_name += event.unicode
        elif event.type == pygame.MOUSEBUTTONDOWN:
            for key, rect in self.loc_dialog_rects.items():
                if key == 'cancel' and rect.collidepoint(event.pos):
                    self.new_map_dialog = False
                elif key == 'ok' and rect.collidepoint(event.pos):
                    if self.new_map_name.strip():
                        self._create_map(self.new_map_name)
                        self.new_map_dialog = False

    def _handle_loc_dialog_event(self, event: pygame.event.Event):
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
            elif event.key == pygame.K_ESCAPE:
                self._cancel_loc_dialog()
            elif event.key == pygame.K_BACKSPACE:
                if self.loc_dialog_field == 'name':
                    self.loc_dialog_name = self.loc_dialog_name[:-1]
                # Room field is dropdown-only — no typing
            else:
                ch = event.unicode
                if ch.isprintable():
                    if self.loc_dialog_field == 'name' and len(self.loc_dialog_name) < 40:
                        self.loc_dialog_name += ch
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

            for key, rect in self.loc_dialog_rects.items():
                if not rect.collidepoint(event.pos):
                    continue
                if key == 'field_name':
                    self.loc_dialog_field = 'name'
                    self.room_dropdown_open = False
                elif key == 'field_room':
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
            if self._height_slider_drag:
                self._height_slider_update(event.pos[0])

        elif event.type == pygame.MOUSEBUTTONUP:
            if event.button == 1:
                self._height_slider_drag = False

    def _height_slider_update(self, mouse_x: int):
        """Map mouse_x to a height value clamped to [0, 2000]."""
        HEIGHT_MIN, HEIGHT_MAX = 0, 2000
        tx = getattr(self, '_height_slider_track_x', 0)
        tw = getattr(self, '_height_slider_track_w', 1)
        t  = max(0.0, min(1.0, (mouse_x - tx) / tw))
        self.loc_dialog_height = round(HEIGHT_MIN + t * (HEIGHT_MAX - HEIGHT_MIN))

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
            pygame.draw.line(screen, sky_col,
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
            pygame.draw.line(screen, gnd_col,
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
            pygame.draw.line(screen, stem_col,
                             (icon_cx, INNER_Y + ground_y),
                             (icon_cx, INNER_Y + icon_y), 1)
            pygame.draw.circle(screen, stem_col, (icon_cx, INNER_Y + ground_y), 2)

        # Draw ground reference dot
        ground_dot_col = (120, 120, 120)
        pygame.draw.circle(screen, ground_dot_col, (icon_cx, INNER_Y + ground_y), 3, 1)

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
            pygame.draw.circle(screen, dot_col,
                               (icon_cx, INNER_Y + icon_y - ICON_SZ // 2),
                               max(4, ICON_SZ // 2))

        # Height value readout
        val_lbl = self.font_medium.render(f'{height_val}', True, self.C['text'])
        screen.blit(val_lbl, val_lbl.get_rect(
            midtop=(px_off + PW // 2, py_off + PH - 18)))

    # ── viewport ──────────────────────────────────────────────────────────────

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

        # Draw tiles
        for ty in range(sty, ety):
            sy = ty * ds - cam_yi + self.vp_y
            for tx in range(stx, etx):
                tile = wm.tiles.get((tx, ty))
                if tile is None:
                    continue
                ts = self._ts_lookup.get(tile.tileset)
                if ts is None:
                    continue
                surf = ts.get_tile(tile.tx, tile.ty, ds)
                if surf:
                    screen.blit(surf, (tx * ds - cam_xi + self.vp_x, sy))

        # Grid (only when tiles are large enough to make it readable)
        if self.show_grid and ds >= 8:
            # Rebuild the grid surface only when zoom level changes
            if self._vp_grid_ds != ds:
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
            # Blit with sub-tile offset so lines stay locked to world coords
            off_x = cam_xi % ds
            off_y = cam_yi % ds
            screen.blit(self._vp_grid_surf, (self.vp_x - off_x, self.vp_y - off_y))

        # Map boundary rect
        bx = self.vp_x - cam_xi
        by = self.vp_y - cam_yi
        pygame.draw.rect(screen, self.C['map_border'],
                         (bx, by, MAP_TILE_W * ds, MAP_TILE_H * ds), 2)

        # Location pins
        for loc in wm.locations:
            px, py = self._tile_to_screen(loc.x, loc.y)
            cx = int(px + ds / 2)
            cy = int(py + ds / 2) - getattr(loc, 'height', 0)
            if clip.collidepoint(cx, cy):
                color = (self.C['pin_sel'] if loc is self.selected_loc
                         else self.C['pin'])
                r = max(PIN_RADIUS, ds // 2)
                # Draw a thin stem from the ground point up to the elevated icon
                # so it's clear the pin is floating above its map tile.
                ground_cy = int(py + ds / 2)
                if loc.height != 0:
                    pygame.draw.line(screen, color,
                                     (cx, ground_cy), (cx, cy + r), 1)
                    pygame.draw.circle(screen, color, (cx, ground_cy), 2)
                pygame.draw.circle(screen, color, (cx, cy), r)
                pygame.draw.circle(screen, (255, 255, 255), (cx, cy), r, 1)
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
                        pygame.draw.line(screen, path_col, (ax, ay), (bx, by),
                                         2 if is_sel else 1)

            # ── Draw waypoint nodes ────────────────────────────────────────
            NODE_R = max(3, ds // 3)
            for k, (sx, sy) in enumerate(pts_screen):
                col = self.C['entity_sel'] if (is_sel and k == 0) else node_col
                pygame.draw.circle(screen, col, (sx, sy), NODE_R)
                pygame.draw.circle(screen, (255, 255, 255), (sx, sy), NODE_R, 1)
                if is_sel and k == 0:
                    # Mark start
                    s_lbl = self.font_small.render('S', True, (0, 0, 0))
                    screen.blit(s_lbl, s_lbl.get_rect(center=(sx, sy)))

            # ── Rubber-band line (only for selected entity being edited) ──
            if is_sel and self.entity_placing and self._entity_rubber and pts_screen:
                rx, ry = tc(*self._entity_rubber)
                pygame.draw.line(screen, (180, 220, 255),
                                 pts_screen[-1], (rx, ry), 1)
                pygame.draw.circle(screen, (180, 220, 255), (rx, ry), max(2, ds // 4))

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
                    surf = vs.get_frame(dir_row, frame_idx, max(ds, 8))
                    if surf:
                        r = surf.get_rect(center=(spr_cx, spr_cy))
                        screen.blit(surf, r)
                        # Draw a thin stem from ground level up to the elevated sprite
                        ground_cy = int(sy_f + half)
                        if e.height != 0:
                            pygame.draw.line(screen, path_col,
                                             (spr_cx, ground_cy), (spr_cx, spr_cy + surf.get_height() // 2), 1)
                            pygame.draw.circle(screen, path_col, (spr_cx, ground_cy), 2)
                        if is_sel:
                            pygame.draw.rect(screen, self.C['entity_sel'], r, 1)
                else:
                    # Fallback: coloured circle
                    pygame.draw.circle(screen, path_col, (spr_cx, spr_cy),
                                       max(4, ds // 2))

            # ── Entity name label (selected only) ──────────────────────────
            if is_sel and pts_screen:
                lbl = self.font_small.render(e.name, True, self.C['entity_sel'])
                screen.blit(lbl, (pts_screen[0][0] + NODE_R + 2,
                                  pts_screen[0][1] - 8))

    # ── top bar ───────────────────────────────────────────────────────────────

    def _draw_top_bar(self, screen: pygame.Surface):
        self.ui.clear()
        bar = pygame.Rect(0, 0, self.screen_width, TOP_BAR_H)
        pygame.draw.rect(screen, self.C['topbar'], bar)
        pygame.draw.line(screen, self.C['panel_border'],
                         (0, TOP_BAR_H - 1), (self.screen_width, TOP_BAR_H - 1))

        x = 6

        def _btn(label: str, key: str, active: bool = False, w: int = 0) -> int:
            tw = self.font_medium.size(label)[0]
            bw = w or tw + 18
            rect = pygame.Rect(x, 6, bw, TOP_BAR_H - 12)
            mx, my = pygame.mouse.get_pos()
            hover  = rect.collidepoint(mx, my) and my < TOP_BAR_H
            bg = (self.C['btn_active'] if active
                  else self.C['btn_hover'] if hover
                  else self.C['btn'])
            pygame.draw.rect(screen, bg, rect, border_radius=4)
            pygame.draw.rect(screen, self.C['accent'] if active else self.C['panel_border'],
                             rect, 1, border_radius=4)
            surf = self.font_medium.render(label, True, self.C['text'])
            screen.blit(surf, surf.get_rect(center=rect.center))
            self.ui[key] = rect
            return rect.right + 4

        x = _btn('+ NEW',  'btn_new',  w=64)
        x = _btn('💾 SAVE', 'btn_save', w=72)
        x += 6

        # ── Map tabs (scrollable) ─────────────────────────────────────────────
        # Reserve space on the right for frame controls (~180 px) and the mode/
        # zoom buttons (~160 px) so tabs never collide with them.
        TAB_AREA_RIGHT = self.vp_w - 360
        # Each tab = name label + 18 px padding + 18 px × delete button.
        TAB_DELETE_W = 18
        ARROW_W      = 20
        mx2, my2     = pygame.mouse.get_pos()

        # Clamp scroll so it never goes past the last tab.
        max_scroll = max(0, len(self.maps) - 1)
        self._map_tab_scroll = max(0, min(self._map_tab_scroll, max_scroll))

        # Measure how many tabs fit from _map_tab_scroll onward.
        tab_x     = x
        need_left = self._map_tab_scroll > 0
        if need_left:
            tab_x += ARROW_W + 2

        # First pass: figure out how many tabs fit so we know if we need › arrow.
        tabs_visible = []
        scan_x = tab_x
        for i in range(self._map_tab_scroll, len(self.maps)):
            wm     = self.maps[i]
            label  = wm.name[:14]
            tab_w  = min(130, self.font_medium.size(label)[0] + 18 + TAB_DELETE_W)
            if scan_x + tab_w + ARROW_W + 4 > TAB_AREA_RIGHT:
                break
            tabs_visible.append((i, label, tab_w))
            scan_x += tab_w + 4
        need_right = (self._map_tab_scroll + len(tabs_visible)) < len(self.maps)

        # Draw ‹ arrow if scrolled right.
        if need_left:
            lx = _btn('‹', 'btn_tab_scroll_left', w=ARROW_W)
        # Draw visible tabs.
        tab_x = lx if need_left else x  # noqa: F821 — lx is always set when need_left
        if not need_left:
            tab_x = x
        for i, label, tab_w in tabs_visible:
            is_active = (i == self.current_map_idx)
            bg        = self.C['accent'] if is_active else self.C['btn']
            rect      = pygame.Rect(tab_x, 6, tab_w, TOP_BAR_H - 12)
            hover_bg  = self.C['btn_hover'] if not is_active else self.C['accent']
            draw_bg   = hover_bg if rect.collidepoint(mx2, my2) and my2 < TOP_BAR_H and not is_active else bg
            pygame.draw.rect(screen, draw_bg, rect, border_radius=4)
            pygame.draw.rect(screen,
                             self.C['accent'] if is_active else self.C['panel_border'],
                             rect, 1, border_radius=4)
            # Name label (leave room for × button on the right)
            name_surf = self.font_medium.render(label, True, self.C['text'])
            name_rect = name_surf.get_rect(
                midleft=(rect.x + 6, rect.centery))
            screen.blit(name_surf, name_rect)
            # × delete button inside the tab
            del_r = pygame.Rect(rect.right - TAB_DELETE_W - 1, rect.y + 1,
                                TAB_DELETE_W, rect.height - 2)
            del_hover = del_r.collidepoint(mx2, my2) and my2 < TOP_BAR_H
            del_bg    = self.C['danger'] if del_hover else (
                (80, 30, 30) if is_active else (50, 30, 30))
            pygame.draw.rect(screen, del_bg, del_r, border_radius=3)
            x_surf = self.font_medium.render('×', True, self.C['text'])
            screen.blit(x_surf, x_surf.get_rect(center=del_r.center))
            self.ui[f'map_tab_{i}']    = rect
            self.ui[f'map_del_{i}']    = del_r
            tab_x = rect.right + 4
        x = tab_x
        # Draw › arrow if more tabs overflow to the right.
        if need_right:
            x = _btn('›', 'btn_tab_scroll_right', w=ARROW_W)

        # Frame controls — shown when a map exists
        wm_cur = self.current_map
        if wm_cur is not None:
            x += 8
            x = _btn('\u2039', 'btn_frame_prev', w=24)
            fc_label = f'Frame {wm_cur.frame_idx + 1}/{wm_cur.frame_count}'
            fc_w = self.font_medium.size(fc_label)[0] + 12
            fc_rect = pygame.Rect(x, 6, fc_w, TOP_BAR_H - 12)
            pygame.draw.rect(screen, self.C['panel'], fc_rect, border_radius=4)
            pygame.draw.rect(screen, self.C['panel_border'], fc_rect, 1, border_radius=4)
            fc_surf = self.font_medium.render(fc_label, True, self.C['text'])
            screen.blit(fc_surf, fc_surf.get_rect(center=fc_rect.center))
            self.ui['frame_label'] = fc_rect
            x = fc_rect.right + 4
            x = _btn('\u203a', 'btn_frame_next', w=24)
            x = _btn('+F', 'btn_frame_add', w=32)
            if wm_cur.frame_count > 1:
                x = _btn('-F', 'btn_frame_del', w=32)

        # Right-side buttons
        right_x = self.vp_w - 4
        # Zoom
        zoom_label = self.font_medium.render(f'{self.zoom}×', True, self.C['dim'])
        right_x -= zoom_label.get_width() + 4
        screen.blit(zoom_label, (right_x, (TOP_BAR_H - zoom_label.get_height()) // 2))
        right_x -= 30
        zi_rect = pygame.Rect(right_x, 6, 28, TOP_BAR_H - 12)
        mx2, my2 = pygame.mouse.get_pos()
        pygame.draw.rect(screen, (self.C['btn_hover'] if zi_rect.collidepoint(mx2, my2) and my2 < TOP_BAR_H else self.C['btn']), zi_rect, border_radius=4)
        screen.blit(self.font_medium.render('+', True, self.C['text']), self.font_medium.render('+', True, self.C['text']).get_rect(center=zi_rect.center))
        self.ui['btn_zoom_in'] = zi_rect
        right_x -= 32
        zo_rect = pygame.Rect(right_x, 6, 28, TOP_BAR_H - 12)
        pygame.draw.rect(screen, (self.C['btn_hover'] if zo_rect.collidepoint(mx2, my2) and my2 < TOP_BAR_H else self.C['btn']), zo_rect, border_radius=4)
        screen.blit(self.font_medium.render('−', True, self.C['text']), self.font_medium.render('−', True, self.C['text']).get_rect(center=zo_rect.center))
        self.ui['btn_zoom_out'] = zo_rect
        right_x -= 6

        # Mode buttons (anchored to right)
        right_x -= 94
        loc_rect = pygame.Rect(right_x, 6, 90, TOP_BAR_H - 12)
        bg_loc = (self.C['btn_active'] if self.mode == 'location'
                  else self.C['btn_hover'] if loc_rect.collidepoint(mx2, my2) and my2 < TOP_BAR_H
                  else self.C['btn'])
        pygame.draw.rect(screen, bg_loc, loc_rect, border_radius=4)
        pygame.draw.rect(screen, self.C['panel_border'], loc_rect, 1, border_radius=4)
        screen.blit(self.font_medium.render('Location', True, self.C['text']),
                    self.font_medium.render('Location', True, self.C['text']).get_rect(center=loc_rect.center))
        self.ui['btn_mode_location'] = loc_rect

        right_x -= 68
        ent_rect = pygame.Rect(right_x, 6, 64, TOP_BAR_H - 12)
        bg_ent = (self.C['btn_active'] if self.mode == 'entity'
                  else self.C['btn_hover'] if ent_rect.collidepoint(mx2, my2) and my2 < TOP_BAR_H
                  else self.C['btn'])
        pygame.draw.rect(screen, bg_ent, ent_rect, border_radius=4)
        pygame.draw.rect(screen, self.C['panel_border'], ent_rect, 1, border_radius=4)
        screen.blit(self.font_medium.render('Entity', True, self.C['text']),
                    self.font_medium.render('Entity', True, self.C['text']).get_rect(center=ent_rect.center))
        self.ui['btn_mode_entity'] = ent_rect

        right_x -= 66
        pnt_rect = pygame.Rect(right_x, 6, 62, TOP_BAR_H - 12)
        bg_pnt = (self.C['btn_active'] if self.mode == 'paint'
                  else self.C['btn_hover'] if pnt_rect.collidepoint(mx2, my2) and my2 < TOP_BAR_H
                  else self.C['btn'])
        pygame.draw.rect(screen, bg_pnt, pnt_rect, border_radius=4)
        pygame.draw.rect(screen, self.C['panel_border'], pnt_rect, 1, border_radius=4)
        screen.blit(self.font_medium.render('Paint', True, self.C['text']),
                    self.font_medium.render('Paint', True, self.C['text']).get_rect(center=pnt_rect.center))
        self.ui['btn_mode_paint'] = pnt_rect

    # ── right panel ───────────────────────────────────────────────────────────

    def _draw_panel(self, screen: pygame.Surface):
        panel_rect = pygame.Rect(self.vp_x + self.vp_w, TOP_BAR_H,
                                 PANEL_W, self.screen_height - TOP_BAR_H)
        pygame.draw.rect(screen, self.C['panel'], panel_rect)
        pygame.draw.line(screen, self.C['panel_border'],
                         panel_rect.topleft, panel_rect.bottomleft, 2)

        px = panel_rect.x + 10
        py = panel_rect.y + 10

        py = self._draw_map_music_row(screen, px, py)

        if self.mode == 'paint':
            self._draw_paint_panel(screen, px, py)
        elif self.mode == 'entity':
            self._draw_entity_panel(screen, px, py)
        else:
            self._draw_location_panel(screen, px, py)

        # Drawn last so the open dropdown list floats above mode-panel content.
        if self.music_dropdown_open:
            self._draw_music_dropdown_popup(screen, px)

    def _draw_map_music_row(self, screen: pygame.Surface, px: int, py: int) -> int:
        """Draw the world map's music-track picker. Shown above the mode-specific
        panel content since it applies to the whole map, not any one mode.

        Returns the y position for whatever gets drawn below it.
        """
        wm = self.current_map
        mx2, my2 = pygame.mouse.get_pos()

        lbl = self.font_small.render('Mode7 Music:', True, self.C['dim'])
        screen.blit(lbl, (px, py))
        py += 18

        btn_rect = pygame.Rect(px, py, PANEL_W - 40, 28)
        focused  = self.music_dropdown_open
        border_col = self.C['accent'] if focused else self.C['input_border']
        bg_col     = self.C['btn_hover'] if focused else self.C['input_bg']
        pygame.draw.rect(screen, bg_col, btn_rect, border_radius=4)
        pygame.draw.rect(screen, border_col, btn_rect, 1, border_radius=4)

        track = wm.music if wm else ''
        label = track if track else '<no music>'
        lbl_col = self.C['text'] if track else self.C['dim']
        lbl_surf = self.font_medium.render(label, True, lbl_col)
        screen.set_clip(pygame.Rect(btn_rect.x + 6, btn_rect.y, btn_rect.w - 26, btn_rect.h))
        screen.blit(lbl_surf, (btn_rect.x + 6, btn_rect.y + 5))
        screen.set_clip(None)
        arrow = '▲' if self.music_dropdown_open else '▼'
        arr_s = self.font_medium.render(arrow, True, self.C['dim'])
        screen.blit(arr_s, (btn_rect.right - arr_s.get_width() - 8, btn_rect.y + 5))
        self.ui['music_dropdown_btn'] = btn_rect
        py += 34

        # Clear button — separate row so it doesn't crowd the dropdown button.
        if wm and wm.music:
            clr_rect = pygame.Rect(px, py, PANEL_W - 40, 20)
            hover = clr_rect.collidepoint(mx2, my2)
            clr_lbl = self.font_small.render('× clear music', True,
                                             self.C['danger'] if hover else self.C['dim'])
            screen.blit(clr_lbl, (px, py))
            self.ui['music_clear'] = clr_rect
            py += 22
        else:
            self.ui.pop('music_clear', None)

        py += 6
        pygame.draw.line(screen, self.C['panel_border'], (px, py), (px + PANEL_W - 40, py), 1)
        py += 10
        return py

    def _draw_music_dropdown_popup(self, screen: pygame.Surface, px: int):
        """Floating track list, drawn above everything else in the panel."""
        btn_rect = self.ui.get('music_dropdown_btn')
        if not btn_rect:
            return
        mx2, my2 = pygame.mouse.get_pos()
        names = self.music_dropdown_names
        item_h = 22

        max_rows_on_screen = max(1, (self.screen_height - btn_rect.bottom - 10) // item_h)
        visible_rows = max(1, min(max_rows_on_screen, 8, len(names) or 1))
        self._music_dropdown_visible_rows = visible_rows

        max_scroll = max(0, len(names) - visible_rows)
        self.music_dropdown_scroll = max(0, min(self.music_dropdown_scroll, max_scroll))
        scroll = self.music_dropdown_scroll

        list_h = max(item_h, min(len(names), visible_rows) * item_h)
        list_rect = pygame.Rect(btn_rect.x, btn_rect.bottom, btn_rect.w, list_h)

        list_bg = pygame.Surface((list_rect.w, list_rect.h), pygame.SRCALPHA)
        list_bg.fill((30, 30, 45, 240))
        screen.blit(list_bg, list_rect.topleft)
        pygame.draw.rect(screen, self.C['accent'], list_rect, 1)

        self.ui['music_dropdown_list_rect'] = list_rect
        for key in [k for k in self.ui if k.startswith('music_dd_')]:
            del self.ui[key]

        wm = self.current_map
        cur_track = wm.music if wm else ''

        if not names:
            empty_surf = self.font_small.render('<no tracks found>', True, self.C['dim'])
            screen.blit(empty_surf, (list_rect.x + 4, list_rect.y + 4))
        else:
            visible_names = names[scroll:scroll + visible_rows]
            for i, name in enumerate(visible_names):
                item_rect = pygame.Rect(list_rect.x, list_rect.y + i * item_h, list_rect.w, item_h)
                is_sel = name == cur_track
                hovered = item_rect.collidepoint(mx2, my2)
                if is_sel:
                    pygame.draw.rect(screen, self.C['accent'], item_rect)
                elif hovered:
                    pygame.draw.rect(screen, self.C['btn_hover'], item_rect)
                col = self.C['text'] if (is_sel or hovered) else self.C['dim']
                item_surf = self.font_small.render(name, True, col)
                screen.blit(item_surf, (item_rect.x + 6, item_rect.y + 4))
                self.ui[f'music_dd_{scroll + i}'] = item_rect

            if len(names) > visible_rows:
                sh_lbl = self.font_small.render(
                    f'↑↓ scroll  ({scroll+1}–{min(scroll+visible_rows, len(names))} of {len(names)})',
                    True, self.C['dim'])
                screen.blit(sh_lbl, (list_rect.x + 4, list_rect.bottom + 2))

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

    def _draw_entity_panel(self, screen: pygame.Surface, px: int, py: int):
        wm       = self.current_map
        mx2, my2 = pygame.mouse.get_pos()

        # ── Header ──────────────────────────────────────────────────────────
        hdr = self.font_medium.render('ENTITIES', True, self.C['accent'])
        screen.blit(hdr, (px, py));  py += 28

        # Status hint
        if self.entity_placing:
            hint = self.font_small.render(
                'Left-click map → add waypoint', True, self.C['entity_path'])
            screen.blit(hint, (px, py));  py += 16
            hint2 = self.font_small.render(
                'Right-click → stop', True, self.C['dim'])
            screen.blit(hint2, (px, py));  py += 20
        else:
            hint = self.font_small.render('Select entity then Edit Path', True, self.C['dim'])
            screen.blit(hint, (px, py));  py += 22

        # ── Add Entity button ──────────────────────────────────────────────
        add_rect = pygame.Rect(px, py, PANEL_W - 20, 26)
        hover = add_rect.collidepoint(mx2, my2)
        pygame.draw.rect(screen, self.C['btn_hover'] if hover else self.C['btn'],
                         add_rect, border_radius=4)
        pygame.draw.rect(screen, self.C['accent'], add_rect, 1, border_radius=4)
        screen.blit(self.font_medium.render('+ Add Entity', True, self.C['text']),
                    self.font_medium.render('+ Add Entity', True, self.C['text']).get_rect(
                        center=add_rect.center))
        self.ui['btn_entity_add'] = add_rect
        py += 32

        # ── Entity list ───────────────────────────────────────────────────
        entities = wm.entities if wm else []
        panel_bottom = self.screen_height - TOP_BAR_H
        list_clip = pygame.Rect(px - 4, py, PANEL_W - 12,
                                min(panel_bottom - py - 10,
                                    len(entities) * 38 + 10))
        screen.set_clip(list_clip)
        for i, e in enumerate(entities):
            is_sel   = (i == self.entity_selected_idx)
            row_rect = pygame.Rect(px - 4, py, PANEL_W - 30, 34)
            bg = self.C['btn_active'] if is_sel else self.C['btn']
            pygame.draw.rect(screen, bg, row_rect, border_radius=3)

            # Sprite thumbnail
            vs = self._get_vehicle_sprite(e.sprite) if e.sprite else None
            thumb = vs.get_panel_thumb(28) if vs else None
            if thumb:
                screen.blit(thumb, (px, py + 3))
            else:
                pygame.draw.rect(screen, self.C['entity_node'],
                                 (px, py + 5, 28, 24), border_radius=3)

            # Name + info
            name_s  = self.font_small.render(
                e.name or f'entity_{i}', True,
                self.C['accent'] if is_sel else self.C['text'])
            pts_s   = self.font_small.render(
                f'{len(e.path)} pts  {"⟳ loop" if e.closed else "↔ ping-pong"}',
                True, self.C['dim'])
            screen.blit(name_s, (px + 32, py + 3))
            screen.blit(pts_s,  (px + 32, py + 18))

            # Room-linked indicator: small coloured dot when a room is assigned
            if e.room:
                dot_x = row_rect.right - 38
                dot_y = py + 7
                pygame.draw.circle(screen, self.C['entity_path'], (dot_x, dot_y), 5)
                pygame.draw.circle(screen, self.C['text'],        (dot_x, dot_y), 5, 1)
                tip_s = self.font_small.render('⇒', True, self.C['entity_path'])
                screen.blit(tip_s, (dot_x - tip_s.get_width() // 2, dot_y + 7))

            # Delete button
            del_rect = pygame.Rect(px + PANEL_W - 34, py + 7, 20, 20)
            del_bg   = self.C['danger'] if del_rect.collidepoint(mx2, my2) else (80, 40, 40)
            pygame.draw.rect(screen, del_bg, del_rect, border_radius=3)
            screen.blit(self.font_small.render('×', True, (255, 255, 255)),
                        self.font_small.render('×', True, (255, 255, 255)).get_rect(
                            center=del_rect.center))

            self.ui[f'entity_row_{i}'] = row_rect
            self.ui[f'entity_del_{i}'] = del_rect
            py += 38

        screen.set_clip(None)
        py += 8

        # ── Selected entity controls ──────────────────────────────────────
        eidx = self.entity_selected_idx
        if eidx is not None and wm and 0 <= eidx < len(wm.entities):
            e = wm.entities[eidx]
            # Divider
            pygame.draw.line(screen, self.C['panel_border'],
                             (px - 4, py), (px + PANEL_W - 20, py), 1)
            py += 8

            sel_lbl = self.font_small.render(
                f'Selected: {e.name}', True, self.C['entity_sel'])
            screen.blit(sel_lbl, (px, py));  py += 20

            # Edit Path / Done buttons
            if self.entity_placing:
                done_rect = pygame.Rect(px, py, (PANEL_W - 24) // 2 - 2, 26)
                hover = done_rect.collidepoint(mx2, my2)
                pygame.draw.rect(screen, self.C['success'] if hover else (40, 120, 40),
                                 done_rect, border_radius=4)
                screen.blit(self.font_medium.render('✓ Done', True, (255, 255, 255)),
                            self.font_medium.render('✓ Done', True, (255, 255, 255)).get_rect(
                                center=done_rect.center))
                self.ui['btn_entity_done'] = done_rect
            else:
                place_rect = pygame.Rect(px, py, (PANEL_W - 24) // 2 - 2, 26)
                hover = place_rect.collidepoint(mx2, my2)
                pygame.draw.rect(screen, self.C['btn_hover'] if hover else self.C['btn'],
                                 place_rect, border_radius=4)
                pygame.draw.rect(screen, self.C['entity_path'], place_rect, 1, border_radius=4)
                screen.blit(self.font_medium.render('✎ Edit Path', True, self.C['text']),
                            self.font_medium.render('✎ Edit Path', True, self.C['text']).get_rect(
                                center=place_rect.center))
                self.ui['btn_entity_place'] = place_rect

            clear_rect = pygame.Rect(
                px + (PANEL_W - 24) // 2 + 2, py, (PANEL_W - 24) // 2 - 2, 26)
            chover = clear_rect.collidepoint(mx2, my2)
            pygame.draw.rect(screen, (120, 40, 40) if chover else (80, 30, 30),
                             clear_rect, border_radius=4)
            screen.blit(self.font_medium.render('Clear Path', True, self.C['text']),
                        self.font_medium.render('Clear Path', True, self.C['text']).get_rect(
                            center=clear_rect.center))
            self.ui['btn_entity_clear'] = clear_rect
            py += 32

            # Closed/ping-pong toggle
            closed_rect = pygame.Rect(px, py, PANEL_W - 20, 26)
            closed_label = ('⟳ Closed Loop' if e.closed else '↔ Ping-Pong (open)')
            is_closed_hover = closed_rect.collidepoint(mx2, my2)
            bg_closed = (self.C['btn_active'] if e.closed
                         else self.C['btn_hover'] if is_closed_hover
                         else self.C['btn'])
            pygame.draw.rect(screen, bg_closed, closed_rect, border_radius=4)
            pygame.draw.rect(screen, self.C['panel_border'], closed_rect, 1, border_radius=4)
            screen.blit(self.font_medium.render(closed_label, True, self.C['text']),
                        self.font_medium.render(closed_label, True, self.C['text']).get_rect(
                            center=closed_rect.center))
            self.ui['btn_entity_closed'] = closed_rect
            py += 34

            # ── Height slider ──────────────────────────────────────────────
            EHEIGHT_MIN, EHEIGHT_MAX = 0, 2000
            h_lbl = self.font_small.render('Height (0 = ground):', True, self.C['dim'])
            screen.blit(h_lbl, (px, py));  py += 18
            track_x = px
            track_y = py
            track_w = PANEL_W - 20
            track_h = 8
            track_rect = pygame.Rect(track_x, track_y, track_w, track_h)
            pygame.draw.rect(screen, self.C['btn'], track_rect, border_radius=4)
            t_h = (e.height - EHEIGHT_MIN) / (EHEIGHT_MAX - EHEIGHT_MIN)
            t_h = max(0.0, min(1.0, t_h))
            thumb_x = int(track_x + t_h * track_w)
            fill_rect = pygame.Rect(track_x, track_y, thumb_x - track_x, track_h)
            pygame.draw.rect(screen, self.C['entity_path'], fill_rect, border_radius=4)
            pygame.draw.rect(screen, self.C['panel_border'], track_rect, 1, border_radius=4)
            pygame.draw.line(screen, self.C['dim'],
                             (track_x, track_y - 3), (track_x, track_y + track_h + 3), 1)
            THUMB_R = 8
            thumb_hover = (abs(mx2 - thumb_x) <= THUMB_R + 4
                           and abs(my2 - (track_y + track_h // 2)) <= THUMB_R + 4)
            thumb_col = self.C['entity_path'] if (self._entity_height_slider_drag or thumb_hover) else self.C['text']
            pygame.draw.circle(screen, thumb_col, (thumb_x, track_y + track_h // 2), THUMB_R)
            pygame.draw.circle(screen, self.C['bg'], (thumb_x, track_y + track_h // 2), THUMB_R - 3)
            val_s = self.font_medium.render(str(e.height), True, self.C['text'])
            screen.blit(val_s, val_s.get_rect(center=(thumb_x, track_y - 14)))
            min_s = self.font_small.render(str(EHEIGHT_MIN), True, self.C['dim'])
            max_s = self.font_small.render(str(EHEIGHT_MAX), True, self.C['dim'])
            screen.blit(min_s, (track_x, track_y + track_h + 5))
            screen.blit(max_s, (track_x + track_w - max_s.get_width(), track_y + track_h + 5))
            slider_hit = pygame.Rect(track_x, track_y - THUMB_R, track_w, track_h + THUMB_R * 2)
            self.ui['entity_height_slider'] = slider_hit
            self.ui['entity_height_track']  = track_rect
            self._entity_height_slider_track_x = track_x
            self._entity_height_slider_track_w = track_w
            py += track_h + 28

            # ── Room link dropdown ─────────────────────────────────────────
            room_lbl = self.font_small.render('Linked Room:', True, self.C['dim'])
            screen.blit(room_lbl, (px, py));  py += 18
            btn_w    = PANEL_W - 20
            btn_rect = pygame.Rect(px, py, btn_w, 28)
            ent_room_focused = self.entity_room_dropdown_open
            border_col = self.C['entity_path'] if ent_room_focused else self.C['input_border']
            bg_col     = self.C['btn_hover'] if ent_room_focused else self.C['input_bg']
            pygame.draw.rect(screen, bg_col, btn_rect, border_radius=4)
            pygame.draw.rect(screen, border_col, btn_rect, 1, border_radius=4)
            room_label = e.room if e.room else '(no room — no collision)'
            lbl_col    = self.C['text'] if e.room else self.C['dim']
            lbl_surf   = self.font_medium.render(room_label, True, lbl_col)
            # Clip label inside button
            screen.set_clip(pygame.Rect(btn_rect.x + 4, btn_rect.y,
                                        btn_rect.w - 24, btn_rect.h))
            screen.blit(lbl_surf, (btn_rect.x + 6, btn_rect.y + 6))
            screen.set_clip(None)
            arrow_s = self.font_medium.render(
                '▲' if self.entity_room_dropdown_open else '▼', True, self.C['dim'])
            screen.blit(arrow_s, (btn_rect.right - arrow_s.get_width() - 6,
                                  btn_rect.y + 6))
            self.ui['entity_room_btn'] = btn_rect
            # Clear button (×) to the right of the dropdown
            clr_r = pygame.Rect(btn_rect.right + 4, py, 20, 28)
            clr_bg = self.C['danger'] if clr_r.collidepoint(mx2, my2) else (80, 40, 40)
            pygame.draw.rect(screen, clr_bg, clr_r, border_radius=3)
            screen.blit(self.font_small.render('×', True, (255, 255, 255)),
                        self.font_small.render('×', True, (255, 255, 255)).get_rect(
                            center=clr_r.center))
            self.ui['entity_room_clear'] = clr_r
            py += 34

            # Hint: how to complete the in-room side of the link
            if e.room:
                hint_lines = [
                    'In the room editor, place a',
                    'World Map Object (world_map)',
                    f'and set entity_name = "{e.name}"',
                    'to mark where the player spawns.',
                ]
                for _hl in hint_lines:
                    hs = self.font_small.render(_hl, True, self.C['dim'])
                    screen.blit(hs, (px, py));  py += 14
                py += 4

            # ── Vehicle sprite picker ──────────────────────────────────────
            if self.vehicle_names:
                picker_lbl = self.font_small.render('Sprite:', True, self.C['dim'])
                screen.blit(picker_lbl, (px, py));  py += 18
                VCELL = 40
                VCOLS = max(1, (PANEL_W - 20) // (VCELL + 4))
                picker_clip = pygame.Rect(px - 4, py, PANEL_W - 12,
                                          self.screen_height - py - 10)
                screen.set_clip(picker_clip)
                for vi, vname in enumerate(self.vehicle_names):
                    vcol = vi % VCOLS
                    vrow = vi // VCOLS
                    cx   = px + vcol * (VCELL + 4)
                    cy   = py + vrow * (VCELL + 4)
                    if cy + VCELL > self.screen_height:
                        break
                    cell_rect = pygame.Rect(cx, cy, VCELL, VCELL)
                    v_sel   = (vname == e.sprite)
                    v_hover = cell_rect.collidepoint(mx2, my2)
                    vbg = (self.C['accent'] if v_sel
                           else self.C['btn_hover'] if v_hover
                           else self.C['btn'])
                    pygame.draw.rect(screen, vbg, cell_rect, border_radius=4)
                    pygame.draw.rect(screen,
                                     self.C['accent'] if v_sel else self.C['panel_border'],
                                     cell_rect, 1, border_radius=4)
                    vs = self._get_vehicle_sprite(vname)
                    thumb = vs.get_panel_thumb(VCELL - 6) if vs else None
                    if thumb:
                        screen.blit(thumb, thumb.get_rect(center=cell_rect.center))
                    else:
                        fb = self.font_small.render(vname[:2].upper(), True, self.C['text'])
                        screen.blit(fb, fb.get_rect(center=cell_rect.center))
                    if v_hover:
                        tip = self.font_small.render(vname, True, self.C['dim'])
                        screen.blit(tip, (cx, cy + VCELL + 2))
                    self.ui[f'vehicle_pick_{vi}'] = cell_rect
                screen.set_clip(None)
            else:
                no_v = self.font_small.render(
                    f'(no sprites in {VEHICLE_DIR})', True, self.C['dim'])
                screen.blit(no_v, (px, py))

        # ── Room dropdown popup (drawn last so it floats over sprite picker) ─
        if (self.entity_room_dropdown_open and eidx is not None
                and wm and 0 <= eidx < len(wm.entities)):
            room_names = self._get_room_names()
            MAX_VIS  = 8
            ITEM_H   = 26
            pop_w    = PANEL_W - 20
            pop_h    = min(len(room_names), MAX_VIS) * ITEM_H + 4
            if not room_names:
                pop_h = ITEM_H + 4
            btn_ref  = self.ui.get('entity_room_btn')
            pop_x    = px
            if btn_ref:
                btn_bottom = btn_ref.bottom
                # Flip above if it would go off screen
                if btn_bottom + pop_h > self.screen_height - 20:
                    pop_y = btn_ref.top - pop_h
                else:
                    pop_y = btn_bottom
            else:
                pop_y = TOP_BAR_H + 60
            popup_rect = pygame.Rect(pop_x, pop_y, pop_w, pop_h)
            pygame.draw.rect(screen, self.C['panel'], popup_rect, border_radius=4)
            pygame.draw.rect(screen, self.C['entity_path'], popup_rect, 1, border_radius=4)
            if not room_names:
                ns = self.font_small.render('(no rooms found)', True, self.C['dim'])
                screen.blit(ns, (pop_x + 6, pop_y + 5))
            else:
                cur_ent_room = wm.entities[eidx].room
                end = min(self.entity_room_dropdown_scroll + MAX_VIS, len(room_names))
                for i, rname in enumerate(
                        room_names[self.entity_room_dropdown_scroll:end]):
                    abs_idx   = self.entity_room_dropdown_scroll + i
                    item_rect = pygame.Rect(pop_x + 2, pop_y + 2 + i * ITEM_H,
                                            pop_w - 4, ITEM_H)
                    hovered   = item_rect.collidepoint(mx2, my2)
                    selected  = (rname == cur_ent_room)
                    if selected:
                        pygame.draw.rect(screen, self.C['entity_path'],
                                         item_rect, border_radius=3)
                    elif hovered:
                        pygame.draw.rect(screen, self.C['btn_hover'],
                                         item_rect, border_radius=3)
                    col = self.C['text'] if (selected or hovered) else self.C['dim']
                    ns  = self.font_medium.render(rname, True, col)
                    screen.blit(ns, (item_rect.x + 6, item_rect.y + 4))
                    self.ui[f'entity_room_dd_{abs_idx}'] = item_rect
                if len(room_names) > MAX_VIS:
                    sh_lbl = self.font_small.render(
                        f'↑↓ scroll  ({self.entity_room_dropdown_scroll+1}–{end}'
                        f' of {len(room_names)})',
                        True, self.C['dim'])
                    screen.blit(sh_lbl, (pop_x + 4, pop_y + pop_h + 2))

    def _draw_paint_panel(self, screen: pygame.Surface, px: int, py: int):
        ts = self.current_tileset
        if not ts:
            surf = self.font_medium.render('No tilesets found', True, self.C['dim'])
            screen.blit(surf, (px, py))
            return

        # Tileset name + TAB hint
        name_surf = self.font_medium.render(
            f'{ts.name}  [{self.tileset_idx + 1}/{len(self.tilesets)}]',
            True, self.C['text'])
        screen.blit(name_surf, (px, py));  py += 22
        hint = self.font_small.render('TAB to switch tileset', True, self.C['dim'])
        screen.blit(hint, (px, py));  py += 20

        # Palette grid (scrollable)
        grid_x = px;  grid_y = py
        palette_h = self.screen_height - TOP_BAR_H - 160
        clip = pygame.Rect(grid_x - 4, grid_y, PANEL_W - 12, palette_h)
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

                # Selection highlight — only reallocate the surface when its
                # pixel size changes (e.g. user drags to a different tile count)
                sel_x = ix + min_tx * PALETTE_CELL
                sel_y = iy + min_ty * PALETTE_CELL
                sel_w = (max_tx - min_tx + 1) * PALETTE_CELL
                sel_h = (max_ty - min_ty + 1) * PALETTE_CELL
                if self._sel_surf_size != (sel_w, sel_h):
                    self._sel_surf = pygame.Surface((sel_w, sel_h), pygame.SRCALPHA)
                    self._sel_surf.fill((255, 215, 0, 55))
                    self._sel_surf_size = (sel_w, sel_h)
                screen.blit(self._sel_surf, (sel_x, sel_y))
                pygame.draw.rect(screen, self.C['accent'],
                                 (sel_x, sel_y, sel_w, sel_h), 2)

        screen.set_clip(None)

        # Instructions at bottom of panel
        inst_y = self.screen_height - 150
        instructions = [
            'Left-drag: paint',
            'Right-drag: erase',
            'Mid-drag: pan',
            'Scroll: zoom / palette',
            'G: toggle grid',
            'Ctrl+S: save',
            'F2 / Esc: close',
        ]
        for line in instructions:
            s = self.font_small.render(line, True, self.C['dim'])
            screen.blit(s, (self.vp_x + self.vp_w + 10, inst_y))
            inst_y += 18

    def _draw_location_panel(self, screen: pygame.Surface, px: int, py: int):
        wm = self.current_map
        header = self.font_medium.render('LOCATIONS', True, self.C['accent'])
        screen.blit(header, (px, py));  py += 28

        hint = self.font_small.render('Click map to place pin', True, self.C['dim'])
        screen.blit(hint, (px, py));  py += 22

        if not wm or not wm.locations:
            none_s = self.font_small.render('(none yet)', True, self.C['dim'])
            screen.blit(none_s, (px, py))
            return

        clip_rect = pygame.Rect(px - 4, py, PANEL_W - 12,
                                self.screen_height - py - 20)
        screen.set_clip(clip_rect)

        for i, loc in enumerate(wm.locations):
            is_sel = (loc is self.selected_loc)
            row_rect = pygame.Rect(px - 4, py, PANEL_W - 30, 36)
            bg = self.C['btn_active'] if is_sel else self.C['btn']
            pygame.draw.rect(screen, bg, row_rect, border_radius=3)

            # Icon badge (sprite or coloured circle fallback)
            badge_cx, badge_cy = px + 14, py + 18
            pygame.draw.circle(screen,
                               self.C['pin_sel'] if is_sel else self.C['pin'],
                               (badge_cx, badge_cy), 12)
            icon_stem = getattr(loc, 'icon', '')
            if icon_stem:
                icon_surf = self._get_icon(icon_stem, 20)
                if icon_surf:
                    screen.blit(icon_surf, icon_surf.get_rect(center=(badge_cx, badge_cy)))

            name_s = self.font_small.render(loc.name or '(unnamed)', True,
                                            self.C['accent'] if is_sel else self.C['text'])
            room_s = self.font_small.render(f'→ {loc.room or "(no room)"}', True, self.C['dim'])
            screen.blit(name_s, (px + 30, py + 3))
            screen.blit(room_s, (px + 30, py + 19))

            # Delete button (× only — edit via double-click)
            mx2, my2 = pygame.mouse.get_pos()
            del_rect = pygame.Rect(px + PANEL_W - 34, py + 8, 20, 20)
            del_bg = self.C['danger'] if del_rect.collidepoint(mx2, my2) else (80, 40, 40)
            pygame.draw.rect(screen, del_bg, del_rect, border_radius=3)
            x_s = self.font_small.render('×', True, (255, 255, 255))
            screen.blit(x_s, x_s.get_rect(center=del_rect.center))

            self.ui[f'loc_entry_{i}'] = row_rect
            self.ui[f'loc_del_{i}']   = del_rect
            py += 42

        screen.set_clip(None)

    # ── dialogs ───────────────────────────────────────────────────────────────

    def _draw_dialog_base(self, screen: pygame.Surface, title: str,
                          w: int, h: int) -> tuple[int, int, int]:
        """Draw dim overlay + centered dialog box. Returns (box_x, box_y, inner_x)."""
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        screen.blit(overlay, (0, 0))
        box_x = (self.screen_width - w) // 2
        box_y = (self.screen_height - h) // 2
        pygame.draw.rect(screen, self.C['panel'], (box_x, box_y, w, h),
                         border_radius=8)
        pygame.draw.rect(screen, self.C['accent'], (box_x, box_y, w, h),
                         2, border_radius=8)
        title_s = self.font_large.render(title, True, self.C['text'])
        screen.blit(title_s, (box_x + (w - title_s.get_width()) // 2, box_y + 14))
        return box_x, box_y, box_x + 20

    def _draw_input_field(self, screen: pygame.Surface,
                          label: str, value: str, active: bool,
                          x: int, y: int, w: int) -> pygame.Rect:
        lbl = self.font_small.render(label, True, self.C['dim'])
        screen.blit(lbl, (x, y))
        cursor = '|' if (active and int(self.cursor_blink * 2) % 2 == 0) else ''
        field_rect = pygame.Rect(x, y + 18, w, 28)
        border_col = self.C['accent'] if active else self.C['input_border']
        pygame.draw.rect(screen, self.C['input_bg'], field_rect, border_radius=4)
        pygame.draw.rect(screen, border_col, field_rect, 1, border_radius=4)
        val_s = self.font_medium.render(value + cursor, True, self.C['text'])
        screen.blit(val_s, (field_rect.x + 6, field_rect.y + 5))
        return field_rect

    def _draw_new_map_dialog(self, screen: pygame.Surface):
        w, h = 420, 160
        bx, by, ix = self._draw_dialog_base(screen, 'NEW WORLD MAP', w, h)
        self.loc_dialog_rects = {}

        field = self._draw_input_field(
            screen, 'Map name:', self.new_map_name, True, ix, by + 50, w - 40)
        self.loc_dialog_rects['field'] = field

        ok_r  = pygame.Rect(bx + w // 2 - 110, by + h - 44, 100, 30)
        can_r = pygame.Rect(bx + w // 2 + 10,  by + h - 44, 100, 30)
        mx, my = pygame.mouse.get_pos()
        for rect, key, label in ((ok_r, 'ok', 'CREATE'), (can_r, 'cancel', 'CANCEL')):
            hover = rect.collidepoint(mx, my)
            pygame.draw.rect(screen, self.C['btn_hover'] if hover else self.C['btn'],
                             rect, border_radius=4)
            pygame.draw.rect(screen, self.C['accent'], rect, 1, border_radius=4)
            s = self.font_medium.render(label, True, self.C['text'])
            screen.blit(s, s.get_rect(center=rect.center))
            self.loc_dialog_rects[key] = rect

        hint = self.font_small.render('Enter to confirm · Esc to cancel',
                                      True, self.C['dim'])
        screen.blit(hint, (bx + (w - hint.get_width()) // 2, by + h - 14))

    def _draw_loc_dialog(self, screen: pygame.Surface):
        CELL      = 36   # icon cell size in picker
        COLS      = 8    # icons per row
        n_icons   = len(self.icon_names)
        icon_rows = max(1, math.ceil(n_icons / COLS)) if n_icons else 1
        w  = max(460, COLS * (CELL + 4) + 40)
        h  = 50 + 100 + 30 + 44 + 30 + icon_rows * (CELL + 4) + 20 + 44 + 20
        title = 'NEW LOCATION' if self.loc_dialog_is_new else 'EDIT LOCATION'
        bx, by, ix = self._draw_dialog_base(screen, title, w, h)
        self.loc_dialog_rects = {}
        mx, my = pygame.mouse.get_pos()

        # Name field
        n_field = self._draw_input_field(
            screen, 'Location name:',
            self.loc_dialog_name, self.loc_dialog_field == 'name',
            ix, by + 50, w - 40)
        self.loc_dialog_rects['field_name'] = n_field

        # Room dropdown button (replaces the old text input)
        room_lbl = self.font_small.render('Room ID:', True, self.C['dim'])
        screen.blit(room_lbl, (ix, by + 120))
        btn_rect = pygame.Rect(ix, by + 138, w - 40, 28)
        focused  = (self.loc_dialog_field == 'room')
        border_col = self.C['accent'] if focused else self.C['input_border']
        bg_col     = self.C['btn_hover'] if (focused or self.room_dropdown_open) else self.C['input_bg']
        pygame.draw.rect(screen, bg_col, btn_rect, border_radius=4)
        pygame.draw.rect(screen, border_col, btn_rect, 1, border_radius=4)
        # Label: current value or placeholder
        room_label = self.loc_dialog_room if self.loc_dialog_room else '(select a room…)'
        lbl_col    = self.C['text'] if self.loc_dialog_room else self.C['dim']
        lbl_surf   = self.font_medium.render(room_label, True, lbl_col)
        screen.blit(lbl_surf, (btn_rect.x + 6, btn_rect.y + 5))
        # Chevron
        arrow = '▲' if self.room_dropdown_open else '▼'
        arr_s = self.font_medium.render(arrow, True, self.C['dim'])
        screen.blit(arr_s, (btn_rect.right - arr_s.get_width() - 8, btn_rect.y + 5))
        self.loc_dialog_rects['field_room'] = btn_rect

        # ── Height slider ─────────────────────────────────────────────────────
        # 0 = ground level (left edge); 2000 = maximum elevation (right edge).
        HEIGHT_MIN, HEIGHT_MAX = 0, 2000
        slider_lbl = self.font_small.render('Height (0 = ground):', True, self.C['dim'])
        screen.blit(slider_lbl, (ix, by + 196))
        track_x = ix
        track_y = by + 214
        track_w = w - 40
        track_h = 8
        track_rect = pygame.Rect(track_x, track_y, track_w, track_h)
        # Draw track
        pygame.draw.rect(screen, self.C['btn'], track_rect, border_radius=4)
        # Fill from left (0 / ground level) to thumb position
        t = (self.loc_dialog_height - HEIGHT_MIN) / (HEIGHT_MAX - HEIGHT_MIN)
        t = max(0.0, min(1.0, t))
        thumb_x   = int(track_x + t * track_w)
        fill_rect = pygame.Rect(track_x, track_y, thumb_x - track_x, track_h)
        pygame.draw.rect(screen, self.C['accent'], fill_rect, border_radius=4)
        pygame.draw.rect(screen, self.C['panel_border'], track_rect, 1, border_radius=4)
        # Ground-level notch at the left edge
        pygame.draw.line(screen, self.C['dim'],
                         (track_x, track_y - 3), (track_x, track_y + track_h + 3), 1)
        # Draw thumb
        THUMB_R = 8
        thumb_hover = (abs(mx - thumb_x) <= THUMB_R + 4
                       and abs(my - (track_y + track_h // 2)) <= THUMB_R + 4)
        thumb_col = self.C['accent'] if (self._height_slider_drag or thumb_hover) else self.C['text']
        pygame.draw.circle(screen, thumb_col, (thumb_x, track_y + track_h // 2), THUMB_R)
        pygame.draw.circle(screen, self.C['bg'], (thumb_x, track_y + track_h // 2), THUMB_R - 3)
        # Value label + range hints
        val_s = self.font_medium.render(str(self.loc_dialog_height), True, self.C['text'])
        screen.blit(val_s, val_s.get_rect(center=(thumb_x, track_y - 14)))
        min_s = self.font_small.render(str(HEIGHT_MIN), True, self.C['dim'])
        max_s = self.font_small.render(str(HEIGHT_MAX), True, self.C['dim'])
        screen.blit(min_s, (track_x, track_y + track_h + 5))
        screen.blit(max_s, (track_x + track_w - max_s.get_width(), track_y + track_h + 5))
        # Store rects for event handling: full slider area + track rect
        slider_hit = pygame.Rect(track_x, track_y - THUMB_R,
                                 track_w, track_h + THUMB_R * 2)
        self.loc_dialog_rects['height_slider'] = slider_hit
        self.loc_dialog_rects['height_track']  = track_rect
        # Store geometry so mouse handlers can compute value without re-deriving
        self._height_slider_track_x = track_x
        self._height_slider_track_w = track_w

        # Icon picker
        icon_lbl_y = by + 256
        icon_lbl = self.font_small.render('Icon:', True, self.C['dim'])
        screen.blit(icon_lbl, (ix, icon_lbl_y))

        picker_y = icon_lbl_y + 18
        if not self.icon_names:
            no_s = self.font_small.render(
                f'(no icons found in {ICON_DIR})', True, self.C['dim'])
            screen.blit(no_s, (ix, picker_y))
        else:
            for i, stem in enumerate(self.icon_names):
                col = i % COLS
                row = i // COLS
                cx  = ix  + col * (CELL + 4)
                cy  = picker_y + row * (CELL + 4)
                cell_rect = pygame.Rect(cx, cy, CELL, CELL)
                selected  = (stem == self.loc_dialog_icon)
                hovered   = cell_rect.collidepoint(mx, my)
                bg = (self.C['accent'] if selected
                      else self.C['btn_hover'] if hovered
                      else self.C['btn'])
                pygame.draw.rect(screen, bg, cell_rect, border_radius=5)
                pygame.draw.rect(screen,
                                 self.C['accent'] if selected else self.C['panel_border'],
                                 cell_rect, 1, border_radius=5)
                surf = self._get_icon(stem, CELL - 6)
                if surf:
                    screen.blit(surf, surf.get_rect(center=cell_rect.center))
                else:
                    # Fallback: first letter of stem
                    fb = self.font_small.render(stem[:1].upper(), True, self.C['text'])
                    screen.blit(fb, fb.get_rect(center=cell_rect.center))
                # Tooltip on hover
                if hovered:
                    tip = self.font_small.render(stem, True, self.C['dim'])
                    screen.blit(tip, (cx, cy + CELL + 2))
                self.loc_dialog_rects[f'icon_{i}'] = cell_rect

        # OK / Cancel
        ok_r  = pygame.Rect(bx + w // 2 - 110, by + h - 44, 100, 30)
        can_r = pygame.Rect(bx + w // 2 + 10,  by + h - 44, 100, 30)
        for rect, key, label in ((ok_r, 'ok', 'OK'), (can_r, 'cancel', 'CANCEL')):
            hover = rect.collidepoint(mx, my)
            pygame.draw.rect(screen, self.C['btn_hover'] if hover else self.C['btn'],
                             rect, border_radius=4)
            pygame.draw.rect(screen, self.C['accent'], rect, 1, border_radius=4)
            s = self.font_medium.render(label, True, self.C['text'])
            screen.blit(s, s.get_rect(center=rect.center))
            self.loc_dialog_rects[key] = rect

        hint = self.font_small.render('Tab to switch field · Enter/Esc to confirm/cancel',
                                      True, self.C['dim'])
        screen.blit(hint, (bx + (w - hint.get_width()) // 2, by + h - 14))

        # ── Room dropdown popup (drawn last so it floats above icon picker) ───
        if self.room_dropdown_open:
            room_names = self._get_room_names()
            MAX_VIS    = 8
            ITEM_H     = 26
            pop_w      = w - 40
            pop_h      = min(len(room_names), MAX_VIS) * ITEM_H + 4
            if not room_names:
                pop_h = ITEM_H + 4
            # Position below the button; flip above if it would go off-screen
            btn_bottom = by + 138 + 28
            if btn_bottom + pop_h > self.screen_height - 20:
                pop_y = by + 138 - pop_h
            else:
                pop_y = btn_bottom
            pop_x = ix
            popup_rect = pygame.Rect(pop_x, pop_y, pop_w, pop_h)
            pygame.draw.rect(screen, self.C['panel'], popup_rect, border_radius=4)
            pygame.draw.rect(screen, self.C['accent'], popup_rect, 1, border_radius=4)
            if not room_names:
                ns = self.font_small.render('(no rooms found)', True, self.C['dim'])
                screen.blit(ns, (pop_x + 6, pop_y + 5))
            else:
                mx2, my2 = pygame.mouse.get_pos()
                end = min(self.room_dropdown_scroll + MAX_VIS, len(room_names))
                for i, name in enumerate(room_names[self.room_dropdown_scroll:end]):
                    abs_idx   = self.room_dropdown_scroll + i
                    item_rect = pygame.Rect(pop_x + 2, pop_y + 2 + i * ITEM_H,
                                            pop_w - 4, ITEM_H)
                    hovered   = item_rect.collidepoint(mx2, my2)
                    selected  = (name == self.loc_dialog_room)
                    if selected:
                        pygame.draw.rect(screen, self.C['accent'], item_rect, border_radius=3)
                    elif hovered:
                        pygame.draw.rect(screen, self.C['btn_hover'], item_rect, border_radius=3)
                    col  = self.C['text'] if (selected or hovered) else self.C['dim']
                    ns   = self.font_medium.render(name, True, col)
                    screen.blit(ns, (item_rect.x + 6, item_rect.y + 4))
                    self.loc_dialog_rects[f'dropdown_{abs_idx}'] = item_rect
                # Scroll hint
                if len(room_names) > MAX_VIS:
                    sh = self.font_small.render(
                        f'↑↓ scroll  ({self.room_dropdown_scroll+1}–{end} of {len(room_names)})',
                        True, self.C['dim'])
                    screen.blit(sh, (pop_x + 4, pop_y + pop_h + 2))