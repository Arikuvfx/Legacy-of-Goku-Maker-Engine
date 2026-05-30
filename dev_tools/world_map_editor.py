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

ICON_DIR = os.path.join("assets", "map", "icons")

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
                 icon: str = ''):
        self.x = x;  self.y = y
        self.name = name;  self.room = room
        self.icon = icon   # filename stem from ICON_DIR, e.g. "town"

    def to_dict(self) -> dict:
        return {'x': self.x, 'y': self.y, 'name': self.name,
                'room': self.room, 'icon': self.icon}

    @staticmethod
    def from_dict(d: dict) -> 'WMLocation':
        return WMLocation(d['x'], d['y'], d.get('name', ''),
                          d.get('room', ''), d.get('icon', ''))


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
        }

    @staticmethod
    def from_dict(d: dict) -> 'WorldMap':
        wm = WorldMap(d.get('name', 'unnamed'))
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

        # ── Camera ────────────────────────────────────────────────────────────
        self.cam_x    = 0.0
        self.cam_y    = 0.0
        self.zoom_idx = ZOOM_DEFAULT

        # ── Mode ──────────────────────────────────────────────────────────────
        self.mode = 'paint'   # 'paint' | 'location'

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

        # ── Location editing ──────────────────────────────────────────────────
        self.selected_loc: Optional[WMLocation] = None
        self.loc_dialog           = False
        self.loc_dialog_is_new    = False
        self.loc_dialog_new_pos: tuple[int, int] = (0, 0)
        self.loc_dialog_name      = ''
        self.loc_dialog_room      = ''
        self.loc_dialog_field     = 'name'  # 'name' | 'room'
        self.loc_dialog_rects: dict[str, pygame.Rect] = {}
        self.room_dropdown_open   = False   # whether the room dropdown popup is visible
        self.room_dropdown_scroll = 0       # first visible item index
        self.room_dropdown_hover  = -1      # hovered item index (-1 = none)

        # Reference to the game's RoomManager (set externally via set_room_manager)
        self.room_manager = None

        # ── New-map dialog ────────────────────────────────────────────────────
        self.new_map_dialog = False
        self.new_map_name   = ''

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
        }

        # Cached UI rects for hit-testing
        self.ui: dict[str, pygame.Rect] = {}

    # ─────────────────────── public API ──────────────────────────────────────

    def toggle(self):
        self.active = not self.active

    def update(self, dt: float):
        if not self.active:
            return
        self.cursor_blink = (self.cursor_blink + dt) % 1.0

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
            cx = px + ds / 2;  cy = py + ds / 2
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
            self.loc_dialog_name = ''
            self.loc_dialog_room = ''
            self.loc_dialog_icon = default_icon
        else:
            self.loc_dialog_name = loc.name if loc else ''
            self.loc_dialog_room = loc.room if loc else ''
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
                             self.loc_dialog_icon)
            wm.locations.append(loc)
            self.selected_loc = loc
        else:
            if self.selected_loc:
                self.selected_loc.name = self.loc_dialog_name.strip()
                self.selected_loc.room = self.loc_dialog_room.strip()
                self.selected_loc.icon = self.loc_dialog_icon
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

    # ─────────────────────── palette helpers ─────────────────────────────────

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
                self.is_painting     = False
                self._pal_drag_active = False
                self._last_paint_cell = None
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

        return None

    def _scroll_event(self, mx: int, my: int, direction: int):
        """Handle scroll wheel — zoom in viewport, scroll palette in panel."""
        if self._in_panel(mx, my):
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
            elif key == 'btn_mode_location':
                self.mode = 'location'
            elif key == 'btn_zoom_in':
                mx2, my2 = self.vp_x + self.vp_w // 2, self.vp_y + self.vp_h // 2
                self._scroll_event(mx2, my2, +1)
            elif key == 'btn_zoom_out':
                mx2, my2 = self.vp_x + self.vp_w // 2, self.vp_y + self.vp_h // 2
                self._scroll_event(mx2, my2, -1)
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
        elif self.mode == 'location':
            # Delete button sits inside row rect — check it first.
            # Double-click on a row opens the edit dialog.
            wm = self.current_map
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
                # Room field no longer accepts manual typing — it's a dropdown
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
            cx = int(px + ds / 2);  cy = int(py + ds / 2)
            if clip.collidepoint(cx, cy):
                color = (self.C['pin_sel'] if loc is self.selected_loc
                         else self.C['pin'])
                r = max(PIN_RADIUS, ds // 2)
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

        screen.set_clip(None)

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

        # Map tabs
        tab_start_x = x
        for i, wm in enumerate(self.maps):
            is_active = (i == self.current_map_idx)
            x = _btn(wm.name[:16], f'map_tab_{i}', active=is_active,
                     w=min(120, self.font_medium.size(wm.name[:16])[0] + 18))
            if x > self.vp_w - 340:  # leave room for frame controls + right-side buttons
                break

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

        if self.mode == 'paint':
            self._draw_paint_panel(screen, px, py)
        else:
            self._draw_location_panel(screen, px, py)

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
        h  = 50 + 100 + 30 + icon_rows * (CELL + 4) + 20 + 44 + 20
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

        # Icon picker
        icon_lbl_y = by + 196
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