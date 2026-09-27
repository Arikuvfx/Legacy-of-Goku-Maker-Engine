"""
editor_toolbar.py — Room Editor's top toolbar.

Reworked to match the engine's shared "modern DBZ" dev-tool visual language
(dev_tools/ui_kit.py + dev_menu.py, and the same BitmapFont/vector-icon
convention room_editor.py's own chrome already migrated to): a flat navy
bar, gold/ki-blue accents, hairline borders that light up on hover, and
vector line-icons drawn straight onto the target instead of loaded PNG
sprites.

Only the *look* changed. Every public method/attribute a caller (RoomEditor,
ObjectEditor) already depends on keeps its old name and behaviour:

    get_selected_item_id() / clear_selected_item() / selected_item_id
    get_grid_size() / set_grid_size(size)
    current_tool, zoom_active, visible, height
    update(dt, mouse_pos)
    handle_click(mouse_pos)   -> same token strings as before
    handle_scroll(direction)
    draw(screen)

No old-toolbar drawing code, colors, or PNG-sprite loading remain — this is
a full re-skin, not a re-theme layered on the previous implementation.
"""

import os
import pygame
import pygame.gfxdraw

import dev_tools.ui_kit as uk
from core.items import (
    get_items_by_category, CATEGORY_SUPPLIES, CATEGORY_STORY_ITEMS, CATEGORY_EQUIP_BODY,
    CATEGORY_EQUIP_HANDS, CATEGORY_EQUIP_FEET, CATEGORY_EQUIP_ACCESSORY,
)


# =============================================================================
# Vector icon glyphs — same fn(surface, rect, color, width=...) shape as
# dev_tools.ui_kit's DEV_MENU_ICON_DRAWERS and room_editor.py's own inline
# icon set (_draw_plus_icon etc.), so the toolbar reads as part of the same
# family rather than a mismatched leftover of loaded PNGs. Icons that already
# have a good match in ui_kit (Entities, Items, Room) are used directly —
# see the `tools`/`actions` tables below.
# =============================================================================

def _draw_tiles_icon(surface, rect, color, width=3):
    """2x2 tile grid — Tiles tool."""
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    box = pygame.Rect(0, 0, s * 2, s * 2)
    box.center = (cx, cy)
    uk.draw_rect_on(surface, color, box, width, 3)
    uk.draw_line_on(surface, color, (box.centerx, box.top + 1), (box.centerx, box.bottom - 1), max(1, width - 1))
    uk.draw_line_on(surface, color, (box.left + 1, box.centery), (box.right - 1, box.centery), max(1, width - 1))


def _draw_objects_icon(surface, rect, color, width=3):
    """Crate with a cross seam — Objects tool."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    box = pygame.Rect(0, 0, s * 0.56, s * 0.5)
    box.center = (cx, cy)
    uk.draw_rect_on(surface, color, box, width, 3)
    uk.draw_line_on(surface, color, (box.left + 3, box.top + 3), (box.right - 3, box.bottom - 3), max(1, width - 1))
    uk.draw_line_on(surface, color, (box.right - 3, box.top + 3), (box.left + 3, box.bottom - 3), max(1, width - 1))


def _draw_map_paint_icon(surface, rect, color, width=3):
    """Blob outline with a brush-stroke corner — paints the Scouter minimap
    shape, distinct from ui_kit's folded-paper draw_map_icon (World Map)."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    pts = [
        (cx - s * 0.26, cy + s * 0.12), (cx - s * 0.26, cy - s * 0.10),
        (cx - s * 0.10, cy - s * 0.26), (cx + s * 0.14, cy - s * 0.26),
        (cx + s * 0.26, cy - s * 0.10), (cx + s * 0.26, cy + s * 0.12),
        (cx + s * 0.10, cy + s * 0.26), (cx - s * 0.10, cy + s * 0.26),
    ]
    for i in range(len(pts)):
        uk.draw_line_on(surface, color, pts[i], pts[(i + 1) % len(pts)], max(1, width - 1))
    bx, by = cx + s * 0.22, cy + s * 0.30
    uk.draw_line_on(surface, color, (bx - s * 0.10, by - s * 0.10), (bx + s * 0.10, by + s * 0.10), width)


def _draw_grid_icon(surface, rect, color, width=2, size=16):
    """Placement-grid glyph. Off (size 0) draws a diagonal slash through the
    frame instead of divisions, same visual language as the old toolbar."""
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.34
    box = pygame.Rect(0, 0, s * 2, s * 2)
    box.center = (cx, cy)
    uk.draw_rect_on(surface, color, box, width, 2)
    if size == 0:
        uk.draw_line_on(surface, color, box.topleft, box.bottomright, width)
    else:
        divisions = 2 if size >= 16 else 4
        for i in range(1, divisions):
            x = box.left + box.w * i / divisions
            y = box.top + box.h * i / divisions
            uk.draw_line_on(surface, color, (x, box.top), (x, box.bottom), 1)
            uk.draw_line_on(surface, color, (box.left, y), (box.right, y), 1)


def _draw_zoom_icon(surface, rect, color, width=3):
    """Magnifying glass — 'zoom to fit whole room' action."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    r = s * 0.22
    center = (cx - s * 0.06, cy - s * 0.06)
    uk.draw_circle_on(surface, color, center, r, width)
    dx = dy = 0.7071  # 45-degree handle direction
    start = (center[0] + dx * r * 0.9, center[1] + dy * r * 0.9)
    end = (center[0] + dx * r * 1.9, center[1] + dy * r * 1.9)
    uk.draw_line_on(surface, color, start, end, width)


def _draw_play_icon(surface, rect, color, width=3):
    """Right-pointing triangle outline — Test action."""
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.28
    p1 = (cx - s * 0.7, cy - s)
    p2 = (cx - s * 0.7, cy + s)
    p3 = (cx + s * 0.9, cy)
    uk.draw_line_on(surface, color, p1, p2, width)
    uk.draw_line_on(surface, color, p2, p3, width)
    uk.draw_line_on(surface, color, p3, p1, width)


def _draw_save_icon(surface, rect, color, width=2):
    """Floppy disk outline — Save action."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    body = pygame.Rect(0, 0, s * 0.5, s * 0.5)
    body.center = (cx, cy + s * 0.02)
    uk.draw_rect_on(surface, color, body, width, 3)
    label = pygame.Rect(0, 0, body.w * 0.62, body.h * 0.34)
    label.centerx = body.centerx
    label.bottom = body.bottom - 4
    uk.draw_rect_on(surface, color, label, width, 1)
    slot = pygame.Rect(0, 0, body.w * 0.36, body.h * 0.2)
    slot.centerx = body.centerx
    slot.top = body.top + 4
    uk.draw_rect_on(surface, color, slot, width, 1)


def _draw_chevron_icon(surface, rect, color, up=True, width=2):
    """Small up/down chevron — the show/hide tab."""
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.3
    if up:
        uk.draw_line_on(surface, color, (cx - s, cy + s * 0.4), (cx, cy - s * 0.5), width)
        uk.draw_line_on(surface, color, (cx, cy - s * 0.5), (cx + s, cy + s * 0.4), width)
    else:
        uk.draw_line_on(surface, color, (cx - s, cy - s * 0.4), (cx, cy + s * 0.5), width)
        uk.draw_line_on(surface, color, (cx, cy + s * 0.5), (cx + s, cy - s * 0.4), width)


class EditorToolbar:
    """
    Top toolbar for the room editor.
    Provides tool-mode switching (tiles, objects, entities, etc.)
    and quick-action buttons (grid, zoom, test, save) on the right side.
    The toolbar can be hidden/shown via the toggle tab at its bottom edge.

    Weather and Background used to live here as toolbar tools/panels; both
    moved into the Room edit view's Settings section (see room_editor.py's
    RoomEditor._draw_edit_view / background sub-panel) so they're set
    per-room alongside Room Music and Can Attack rather than as a global
    toolbar mode.

    Item panel
    ----------
    Clicking the 'Items' tool button opens a floating panel that lists every
    consumable and equip item defined in core/items.py (grouped by category,
    per ITEM_CATEGORY_LABELS), so designers can browse what exists and see
    its icon/description. Clicking a thumbnail SELECTS that item (highlighted
    border, panel closes) and clicking the same thumbnail again deselects it.

    The current selection is exposed via get_selected_item_id() (returns ''
    when nothing's selected) and can be cleared with clear_selected_item().
    EditorToolbar itself never places anything — this is just "what item is
    armed right now"; a caller such as ObjectEditor reads it and decides
    what a world click does with it (e.g. clicking a placed chest while an
    item is selected assigns that item as the chest's loot). The 'Items'
    tool button gets a lit border whenever a selection is active, the same
    way it did before, so the armed state is visible even with the panel
    closed.

    Equip-item sprites live in a subfolder of ITEM_SPRITE_DIR rather than
    directly in it (e.g. assets/sprites/items/equipment/body/*.png) — see
    ITEM_CATEGORY_SUBDIRS. Adding a new equip category later means adding
    both a ITEM_CATEGORY_LABELS entry and, if its sprites live in a
    subfolder, an ITEM_CATEGORY_SUBDIRS entry.
    """

    # ── Item panel constants ────────────────────────────────────────────────
    ITEM_SPRITE_DIR = os.path.join('assets', 'sprites', 'items')
    ITEM_THUMB_SIZE  = 72
    ITEM_THUMB_PAD   = 10
    ITEM_COLS        = 5
    ITEM_PANEL_W     = ITEM_COLS * (ITEM_THUMB_SIZE + ITEM_THUMB_PAD) + ITEM_THUMB_PAD + 16
    # Default/floor height. The panel now opens filling the available
    # height below the toolbar (see _draw_item_panel's `available_h`)
    # rather than a short fixed size — this is only the minimum a user
    # drag-resize can shrink it to.
    ITEM_PANEL_MIN_H = 260
    ITEM_RESIZE_GRIP = 8  # px-tall grab strip along the bottom edge
    ITEM_CATEGORY_LABELS = {
        CATEGORY_SUPPLIES:    'Supplies',
        CATEGORY_STORY_ITEMS: 'Story Items',
        CATEGORY_EQUIP_BODY:      'Equipment (Body)',
        CATEGORY_EQUIP_HANDS:     'Equipment (Hands)',
        CATEGORY_EQUIP_FEET:      'Equipment (Feet)',
        CATEGORY_EQUIP_ACCESSORY: 'Equipment (Accessory)',
    }
    # Categories whose sprites live in a subfolder of ITEM_SPRITE_DIR rather
    # than directly in it (e.g. assets/sprites/items/equipment/body/*.png).
    ITEM_CATEGORY_SUBDIRS = {
        CATEGORY_EQUIP_BODY:      os.path.join('equipment', 'body'),
        CATEGORY_EQUIP_HANDS:     os.path.join('equipment', 'hands'),
        CATEGORY_EQUIP_FEET:      os.path.join('equipment', 'feet'),
        CATEGORY_EQUIP_ACCESSORY: os.path.join('equipment', 'accessories'),
    }

    def __init__(self, screen_width, screen_height):
        self.screen_width  = screen_width
        self.screen_height = screen_height

        # Layout constants — a slimmer bar than the old toolbar, in line
        # with dev_menu's "functional editor first, decoration second" bars.
        self.height  = 82
        self.padding = 14
        self.btn_w   = 72
        self.btn_h   = 60
        self.gap     = 8

        # Same bitmap font family DevMenu and RoomEditor's own chrome use.
        self.font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self.label_size = 10
        self.hint_size  = 11
        self.title_size = 18
        self.body_size  = 11

        # Which editor mode is active
        self.current_tool = 'tiles'

        # Left-side mode buttons
        self.tools = [
            {'id': 'tiles',     'label': 'Tiles',     'icon': _draw_tiles_icon,     'tooltip': 'Tileset Editor',        'accent': uk.Theme.GOLD},
            {'id': 'objects',   'label': 'Objects',   'icon': _draw_objects_icon,   'tooltip': 'Object Editor',         'accent': uk.Theme.GOLD},
            {'id': 'entities',  'label': 'Entities',  'icon': uk.draw_entity_icon,  'tooltip': 'Entity Editor',         'accent': uk.Theme.GOLD},
            {'id': 'items',     'label': 'Items',     'icon': uk.draw_item_icon,    'tooltip': 'Items',                 'accent': uk.Theme.GOLD},
            {'id': 'settings',  'label': 'Room',      'icon': uk.draw_room_icon,    'tooltip': 'Room Settings',         'accent': uk.Theme.GOLD},
            {'id': 'map_paint', 'label': 'Map',       'icon': _draw_map_paint_icon, 'tooltip': 'Scouter Mini-Map',      'accent': uk.Theme.KI_BLUE},
        ]

        # Right-side action buttons
        self.actions = [
            {'id': 'grid', 'label': 'Grid: 16px', 'icon': _draw_grid_icon, 'tooltip': '', 'accent': (140, 210, 130)},
            {'id': 'zoom', 'label': 'Zoom',        'icon': _draw_zoom_icon, 'tooltip': 'Zoom', 'accent': uk.Theme.KI_BLUE},
            {'id': 'test', 'label': 'Test',        'icon': _draw_play_icon, 'tooltip': 'Test', 'accent': (140, 210, 130)},
            {'id': 'save', 'label': 'Save',        'icon': _draw_save_icon, 'tooltip': 'Save', 'accent': uk.Theme.GOLD},
        ]

        self.zoom_active = False

        # ── Placement grid state ────────────────────────────────────────────
        # Quick, global "snap grid" size shared by tile painting and object /
        # entity placement & repositioning. 0 == no grid (free placement).
        # Cycled via the 'Grid' action button (or read with get_grid_size()).
        self.grid_sizes      = [0, 8, 16]
        self.grid_size_index = 2  # default 16px, matches prior always-on snap behaviour
        self._sync_grid_label()

        # ── Icons — restored classic icon set (filled/colored, not the
        # thin vector glyphs) ──────────────────────────────────────────────
        # A PNG in assets/ui/toolbar/<id>.png still takes priority when
        # present; the procedural builder below is the fallback (and in
        # practice, since no such PNGs ship, the actual icon shown).
        self._sprites: dict = {}
        self._icon_builders = {
            'tiles':     self._create_tile_icon,
            'objects':   self._create_object_icon,
            'entities':  self._create_entity_icon,
            'items':     self._create_item_icon,
            'settings':  self._create_settings_icon,
            'map_paint': self._create_map_paint_icon,
            'grid':      self._create_grid_icon,
            'zoom':      self._create_zoom_icon,
            'test':      self._create_play_icon,
            'save':      self._create_save_icon,
        }
        self._load_sprites()

        # Hover tracking
        self.hover_tool   = None
        self.hover_action = None

        # Per-button hover animation, keyed by button id (smoothed 0..1,
        # same lerp-toward-target pattern as DevMenu's card hover anim).
        self._hover_anim = {b['id']: 0.0 for b in (self.tools + self.actions)}

        # Show/hide toggle
        self.visible      = True
        self.tab_w        = 26
        self.tab_h        = 20
        self.hover_toggle = False
        self._toggle_anim = 0.0

        # Slide animation for show/hide, chased toward 1.0 (fully open) or
        # 0.0 (fully closed) each frame in update() — same exponential-
        # approach pattern as _toggle_anim/_hover_anim above, just driving
        # the bar's vertical offset instead of a color/glow blend. Starts
        # already settled at self.visible's initial value so the bar
        # doesn't animate in on the very first frame.
        self._slide_anim   = 1.0 if self.visible else 0.0
        self._SLIDE_RATE    = 9.0  # higher = snappier slide, lower = slower/softer

        # ── Item panel state ───────────────────────────────────────────────────
        self.item_panel_open  = False
        self._item_sections:  list = []   # [(label, category, [item_id, ...]), ...]
        self._item_icons:     dict = {}   # item_id → Surface | None
        self._item_scroll     = 0
        self._item_hover      = ''        # hovered item_id
        self._item_rects:     dict = {}   # item_id → Rect (grid cell, for hover/tooltip)
        self._item_panel_rect = pygame.Rect(0, 0, 0, 0)
        self._item_grid_rect  = pygame.Rect(0, 0, 0, 0)
        self._items_scan_done = False

        # Floating-window behaviour: dragged by its header, same way any
        # OS dialog moves. Position is None until the user drags it for the
        # first time, meaning "use the default centered spot"; once dragged
        # it's remembered (in screen space) across close/reopen, same as a
        # real window keeps the spot you left it at.
        self._item_win_pos          = None   # (x, y) top-left, or None for default
        self._item_panel_header_rect = pygame.Rect(0, 0, 0, 0)
        self._item_dragging          = False
        self._item_drag_offset       = (0, 0)
        self._item_close_rect        = pygame.Rect(0, 0, 0, 0)

        # User-resizable height, dragged from the bottom edge (see
        # _update_item_panel_resize). None means "not resized yet" — fill
        # the available height below the toolbar, same as a freshly
        # reopened window rather than remembering a stale small size.
        self._item_win_height  = None
        self._item_resize_rect = pygame.Rect(0, 0, 0, 0)
        self._item_resizing    = False
        self._item_resize_offset = 0  # mouse_y - panel_bottom at drag start

        # Tracks whether _update_item_panel_cursor is the thing currently
        # holding the shared OS cursor, so it only ever releases a cursor
        # it actually claimed — see _update_item_panel_cursor.
        self._owns_cursor = False

        # Same ownership-tracking idea as _owns_cursor above, but for the
        # plain pointing-hand cursor over this toolbar's own clickable
        # widgets — see _update_hand_cursor.
        self._owns_hand_cursor = False

        self.selected_item_id = ''  # item id currently armed for placement/assignment, '' = none

    # =========================================================================
    # Public API
    # =========================================================================

    def get_selected_item_id(self) -> str:
        """The item id currently armed via the Items panel, or '' if none.
        Callers (e.g. ObjectEditor) use this to decide what a world click
        should do — assigning it as a chest's loot, etc."""
        return self.selected_item_id

    def clear_selected_item(self):
        """Disarm the current item selection, e.g. after a caller consumes
        it or the user presses ESC."""
        self.selected_item_id = ''

    def get_grid_size(self) -> int:
        """Current placement/snap grid size in world pixels. 0 means no grid
        (free placement). Shared by tile painting and object/entity
        placement & repositioning — callers should snap to this value
        wherever they currently hard-code a fixed tile size for that
        purpose."""
        return self.grid_sizes[self.grid_size_index]

    def set_grid_size(self, size: int):
        """Explicitly set the placement grid size, snapping to the nearest
        supported value (0 / 8 / 16) if given something else."""
        if size in self.grid_sizes:
            self.grid_size_index = self.grid_sizes.index(size)
        else:
            self.grid_size_index = min(
                range(len(self.grid_sizes)),
                key=lambda i: abs(self.grid_sizes[i] - size)
            )
        self._sync_grid_label()

    def _cycle_grid_size(self):
        self.grid_size_index = (self.grid_size_index + 1) % len(self.grid_sizes)
        self._sync_grid_label()

    def _sync_grid_label(self):
        size = self.grid_sizes[self.grid_size_index]
        # Cycle order is Off -> 8px -> 16px -> Off, matching self.grid_sizes.
        label = 'Grid: Off' if size == 0 else f'Grid: {size}px'
        for a in self.actions:
            if a['id'] == 'grid':
                a['label']   = label
                a['tooltip'] = label  # tooltip mirrors the button label — shows current grid state
                break

    # =========================================================================
    # Icons — sprite loading + restored procedural builders
    # =========================================================================

    def _load_sprites(self):
        icon_size = 44  # bigger custom-icon box (was 34) — still comfortably inside the 72x60 button
        for sid in self._icon_builders:
            try:
                img = pygame.image.load(f'assets/ui/toolbar/{sid}.png').convert_alpha()
                iw, ih = img.get_size()
                scale  = min(icon_size / iw, icon_size / ih)
                img    = pygame.transform.scale(img, (max(1, int(iw * scale)),
                                                      max(1, int(ih * scale))))
                self._sprites[sid] = img
            except Exception:
                pass

    def _get_icon_surface(self, bid):
        # A PNG override always wins, including for 'grid' — if you drop in
        # assets/ui/toolbar/grid.png it's used as-is (static, not size-aware).
        # With no override, grid still falls back to the size-aware
        # procedural builder so its glyph keeps reflecting Off/8px/16px.
        cached = self._sprites.get(bid)
        if cached:
            return cached
        if bid == 'grid':
            return self._icon_builders['grid']()
        builder = self._icon_builders.get(bid)
        return builder() if builder else None

    def _create_tile_icon(self):
        surf   = pygame.Surface((32, 32), pygame.SRCALPHA)
        colors = [(139, 69, 19), (160, 82, 45), (101, 67, 33)]
        for i in range(3):
            for j in range(3):
                x, y = 2 + i * 10, 2 + j * 10
                pygame.draw.rect(surf, colors[(i + j) % 3], (x, y, 8, 8))
                pygame.draw.rect(surf, (80, 50, 20), (x, y, 8, 8), 1)
        return surf

    def _create_object_icon(self):
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.draw.rect(surf, (101, 67, 33), (13, 18, 6, 12))
        pygame.gfxdraw.filled_circle(surf, 16, 12, 8, (34, 139, 34))
        pygame.gfxdraw.aacircle(surf, 16, 12, 8, (20, 100, 20))
        return surf

    def _create_entity_icon(self):
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.gfxdraw.filled_circle(surf, 16, 10, 5, (255, 220, 177))
        pygame.gfxdraw.aacircle(surf, 16, 10, 5, (200, 160, 120))
        pygame.draw.rect(surf, (100, 100, 255), (11, 15, 10, 12))
        pygame.draw.rect(surf, (80, 80, 200),   (11, 15, 10, 12), 1)
        return surf

    def _create_item_icon(self):
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.gfxdraw.filled_circle(surf, 16, 16, 10, (255, 215, 0))
        pygame.gfxdraw.aacircle(surf, 16, 16, 10, (200, 170, 0))
        pygame.gfxdraw.filled_circle(surf, 16, 16,  6, (255, 235, 100))
        return surf

    def _create_settings_icon(self):
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        center, outer_r, inner_r, teeth = 16, 10, 5, 8
        for i in range(teeth):
            v  = pygame.math.Vector2(1, 0).rotate(i * (360 / teeth))
            x1 = center + outer_r * 0.7 * v.x
            y1 = center + outer_r * 0.7 * v.y
            x2 = center + outer_r * v.x
            y2 = center + outer_r * v.y
            pygame.draw.line(surf, (180, 180, 200), (x1, y1), (x2, y2), 3)
        pygame.gfxdraw.filled_circle(surf, center, center, inner_r, (100, 100, 120))
        pygame.gfxdraw.aacircle(surf, center, center, inner_r, (150, 150, 170))
        return surf

    def _create_map_paint_icon(self):
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        blob = [(6, 20), (6, 12), (10, 8), (18, 8), (22, 12), (22, 18),
                (18, 22), (12, 22)]
        pygame.gfxdraw.filled_polygon(surf, blob, (40, 90, 255))
        pygame.gfxdraw.aapolygon(surf, blob, (100, 200, 255))
        pygame.draw.line(surf, (255, 215, 0), (21, 21), (28, 28), 3)
        pygame.gfxdraw.filled_circle(surf, 28, 28, 2, (255, 215, 0))
        return surf

    def _create_grid_icon(self):
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        size = self.grid_sizes[self.grid_size_index]
        if size == 0:
            col = (140, 140, 150)
            pygame.draw.rect(surf, col, (5, 5, 22, 22), 2)
            pygame.draw.line(surf, (255, 120, 120), (6, 26), (26, 6), 2)
        else:
            col = (150, 220, 150)
            divisions = 2 if size == 16 else 4
            pygame.draw.rect(surf, col, (5, 5, 22, 22), 2)
            step = 22 / divisions
            for i in range(1, divisions):
                x = int(5 + i * step)
                pygame.draw.line(surf, col, (x, 5), (x, 27), 1)
                y = int(5 + i * step)
                pygame.draw.line(surf, col, (5, y), (27, y), 1)
        return surf

    def _create_zoom_icon(self):
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.gfxdraw.aacircle(surf, 14, 14, 8, (100, 200, 255))
        pygame.gfxdraw.aacircle(surf, 14, 14, 7, (100, 200, 255))
        pygame.draw.line(surf, (100, 200, 255), (20, 20), (27, 27), 3)
        pygame.draw.line(surf, (200, 240, 255), (10, 14), (7,  14), 2)
        pygame.draw.line(surf, (200, 240, 255), (18, 14), (21, 14), 2)
        pygame.draw.line(surf, (200, 240, 255), (14, 10), (14,  7), 2)
        pygame.draw.line(surf, (200, 240, 255), (14, 18), (14, 21), 2)
        return surf

    def _create_play_icon(self):
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pts  = [(10, 8), (10, 24), (24, 16)]
        pygame.gfxdraw.filled_polygon(surf, pts, (100, 255, 100))
        pygame.gfxdraw.aapolygon(surf, pts, (80, 200, 80))
        return surf

    def _create_save_icon(self):
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.draw.rect(surf, (255, 215, 0), (8,  6, 16, 20))
        pygame.draw.rect(surf, (200, 170, 0), (8,  6, 16, 20), 2)
        pygame.draw.rect(surf, (40,  40, 60), (10, 8, 12,  6))
        pygame.draw.rect(surf, (200, 170, 0), (12, 20, 8,  6))
        return surf

    # =========================================================================
    # Layout
    # =========================================================================

    def _button_rects(self):
        """(id, rect, kind) for every tool/action button, in the current
        layout. Recomputed on demand rather than cached — this is a
        handful of rects, cheap enough to redo every call, and always
        matches the current screen_width/visibility without a separate
        invalidation path."""
        rects = []
        y = self.padding

        x = self.padding
        for tool in self.tools:
            rects.append((tool['id'], pygame.Rect(x, y, self.btn_w, self.btn_h), 'tool'))
            x += self.btn_w + self.gap

        total_actions_w = len(self.actions) * self.btn_w + (len(self.actions) - 1) * self.gap
        x = self.screen_width - self.padding - total_actions_w
        for action in self.actions:
            rects.append((action['id'], pygame.Rect(x, y, self.btn_w, self.btn_h), 'action'))
            x += self.btn_w + self.gap

        return rects

    def _toggle_rect(self):
        # Tab position tracks the animated slide, not just the instant
        # self.visible flag, so the tab visually stays glued to the bar's
        # bottom edge as it slides up/down instead of snapping ahead of it.
        # This also doubles as the click/hover hit-rect, which is what we
        # want: the tab should be clickable where it's actually drawn.
        tx = (self.screen_width - self.tab_w) // 2
        ty_shown  = self.height - self.tab_h // 2
        ty_hidden = 0
        ty = round(uk.lerp(ty_hidden, ty_shown, self._slide_anim))
        return pygame.Rect(tx, ty, self.tab_w, self.tab_h)

    # =========================================================================
    # Update / input
    # =========================================================================

    def update(self, dt, mouse_pos):
        self.hover_tool   = None
        self.hover_action = None
        self.hover_toggle = self._toggle_rect().collidepoint(mouse_pos)

        target_toggle = 1.0 if self.hover_toggle else 0.0
        self._toggle_anim += (target_toggle - self._toggle_anim) * min(1.0, dt * 12.0)

        target_slide = 1.0 if self.visible else 0.0
        self._slide_anim += (target_slide - self._slide_anim) * min(1.0, dt * self._SLIDE_RATE)
        # Snap once close enough so it actually settles at 0/1 instead of
        # crawling toward it forever (exponential approach never truly
        # arrives), which matters here since _draw_bar below uses
        # "> 0.001" to decide whether the bar needs drawing at all.
        if abs(target_slide - self._slide_anim) < 0.001:
            self._slide_anim = target_slide

        if self.visible:
            hovered_ids = set()
            for bid, rect, kind in self._button_rects():
                if rect.collidepoint(mouse_pos):
                    hovered_ids.add(bid)
                    if kind == 'tool':
                        self.hover_tool = bid
                    else:
                        self.hover_action = bid
            for bid in self._hover_anim:
                target = 1.0 if bid in hovered_ids else 0.0
                self._hover_anim[bid] += (target - self._hover_anim[bid]) * min(1.0, dt * 12.0)
        else:
            for bid in self._hover_anim:
                self._hover_anim[bid] += (0.0 - self._hover_anim[bid]) * min(1.0, dt * 12.0)

        self._update_item_panel_drag(mouse_pos)
        self._update_item_panel_resize(mouse_pos)
        self._update_item_panel_cursor(mouse_pos)
        self._update_hand_cursor(mouse_pos)

        # Hover detection inside item panel (skipped mid-drag/resize so the
        # description strip doesn't flicker while the window is moving)
        if self.item_panel_open and not self._item_dragging and not self._item_resizing:
            self._item_hover = ''
            for iid, rect in self._item_rects.items():
                if rect.collidepoint(mouse_pos):
                    self._item_hover = iid
                    break

    def handle_click(self, mouse_pos) -> "str | None":
        """Returns a token string or None."""
        if self._toggle_rect().collidepoint(mouse_pos):
            self.visible = not self.visible
            return 'toolbar_toggle'

        if not self.visible:
            return None

        # Item panel intercepts all clicks when open. Clicking a thumbnail
        # arms/disarms that item (see get_selected_item_id) and closes the
        # panel; clicking elsewhere inside the panel is a no-op; clicking
        # outside it just closes the panel without changing the selection.
        if self.item_panel_open:
            if self._item_close_rect.collidepoint(mouse_pos):
                self.item_panel_open = False
                return 'items_toggle'
            for item_id, rect in self._item_rects.items():
                if rect.collidepoint(mouse_pos):
                    self.selected_item_id = '' if item_id == self.selected_item_id else item_id
                    self.item_panel_open = False
                    # Hand control back to the 'objects' tool so world
                    # clicks route to ObjectEditor again — arming an item
                    # is only useful if the click that follows can reach
                    # ObjectEditor.handle_input (e.g. to hit a chest).
                    # Without this, current_tool stays 'items' (set when
                    # the Items button was first clicked) and nothing in
                    # the world is interactable afterward.
                    self.current_tool = 'objects'
                    return 'item_selected' if self.selected_item_id else 'item_deselected'
            # The resize grip straddles the panel's bottom border, so a
            # press landing just past it (in the grip but outside the
            # panel rect proper) must not be treated as "clicked outside".
            if not (self._item_panel_rect.collidepoint(mouse_pos)
                    or self._item_resize_rect.collidepoint(mouse_pos)):
                self.item_panel_open = False
            return None

        for bid, rect, kind in self._button_rects():
            if not rect.collidepoint(mouse_pos):
                continue
            if kind == 'tool':
                if bid == 'items':
                    self.item_panel_open = not self.item_panel_open
                    if self.item_panel_open:
                        self._ensure_items_scanned()
                    self.current_tool = bid
                    return 'items_toggle'
                self.current_tool = bid
                return bid
            else:
                if bid == 'zoom':
                    self.zoom_active = not self.zoom_active
                elif bid == 'grid':
                    self._cycle_grid_size()
                return f'action_{bid}'

        return None

    def handle_scroll(self, direction):
        """Call on scroll-wheel events (direction +1 = up, -1 = down)."""
        if (self.item_panel_open
                and self._item_grid_rect.collidepoint(pygame.mouse.get_pos())):
            self._item_scroll = max(0, self._item_scroll - direction * 80)

    # =========================================================================
    # Drawing
    # =========================================================================

    def draw(self, screen):
        # Keep drawing the bar for as long as it's still sliding, even
        # after self.visible has already flipped to False — otherwise the
        # bar would vanish instantly on close and only the tab would slide,
        # which is exactly the "just being closed in an instant" look this
        # replaces. self.visible itself still flips the moment the toggle
        # is clicked (handle_click, hover/click hit-testing in update()
        # unchanged), so behaviour for callers depending on it is unchanged
        # — only the drawn frame lags behind by the slide animation.
        if self._slide_anim > 0.001:
            self._draw_bar(screen)
        self._draw_toggle_tab(screen)

        if self.hover_tool:
            tool = next(t for t in self.tools if t['id'] == self.hover_tool)
            self._draw_tooltip(screen, tool['tooltip'])
        elif self.hover_action:
            action = next(a for a in self.actions if a['id'] == self.hover_action)
            tip = ('Click to return to normal view'
                   if (action['id'] == 'zoom' and self.zoom_active) else action['tooltip'])
            self._draw_tooltip(screen, tip)

        # Item panel (always on top)
        if self.item_panel_open:
            self._draw_item_panel(screen)

    def _draw_bar(self, screen):
        w = self.screen_width

        # Slide offset: 0 when fully open, -self.height when fully closed
        # (bar entirely above the top edge). Applied directly to each shape
        # below (same single-pass draw straight to `screen` as before the
        # slide was added) rather than composited on an intermediate
        # surface — layering the translucent background + glowy buttons
        # twice (once onto an offscreen surface, again blitting that onto
        # the screen) produced visible seams and a halo behind the icons
        # that isn't there when it's drawn directly, in one pass, like this.
        dy = round(-self.height * (1.0 - self._slide_anim))

        # Flat, translucent navy bar over the live room view — same base
        # tone as DevMenu's header, kept semi-transparent (unlike DevMenu's
        # opaque one) since this bar sits over editor content rather than
        # a dedicated full-screen menu.
        uk.draw_rect_on(screen, (12, 15, 23, 225), pygame.Rect(0, dy, w, self.height), 0, 0)
        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER, pygame.Rect(0, dy + self.height - 1, w, 1), 0, 0)

        for bid, rect, kind in self._button_rects():
            if kind == 'tool':
                spec = next(t for t in self.tools if t['id'] == bid)
                lit = (bid == self.current_tool) or (bid == 'items' and bool(self.selected_item_id))
            else:
                spec = next(a for a in self.actions if a['id'] == bid)
                lit = (bid == 'zoom' and self.zoom_active)
            self._draw_button(screen, rect.move(0, dy), spec, lit)
            # Hand cursor for free, same opt-in convention as ObjectEditor's
            # own buttons/fields (see _draw_mini_button et al there) —
            # skipped while the item panel is open since it intercepts
            # every click, buttons underneath included (see handle_click).
            # Also skipped whenever we're not fully open (mid-slide or
            # fully closed): the rect registered here is the button's
            # settled, un-offset position, which is only a valid
            # screen-space hit target once the bar has actually finished
            # opening (dy back to 0).
            if not self.item_panel_open and self.visible:
                uk.register_hoverable(rect)

    def _draw_button(self, screen, rect, spec, lit):
        bid = spec['id']
        t = round(self._hover_anim.get(bid, 0.0) * 20) / 20.0  # discretize for ui_kit's panel/glow cache
        accent = spec.get('accent', uk.Theme.GOLD)
        tt = max(t, 1.0 if lit else 0.0)

        lift = int(round(2 * t))
        draw_rect = rect.move(0, -lift)

        base = uk.lerp_color((22, 26, 35), (28, 33, 44), tt)
        border_col = uk.lerp_color((52, 58, 72), accent, tt if lit else t * 0.8)
        border_w = 2 if lit else 1

        uk.draw_panel(screen, draw_rect, bg=(*base, 235), border=border_col,
                      border_width=border_w, radius=8, shadow=False)

        glow_t = max(t, 0.35 if lit else 0.0)
        if glow_t > 0.02:
            uk.draw_soft_glow(screen, draw_rect.center, 22, accent, max_alpha=int(24 * glow_t))

        icon = self._get_icon_surface(bid)
        if icon:
            uk.blit_surface(screen, icon, icon.get_rect(center=draw_rect.center), transient=False)

    def _draw_toggle_tab(self, screen):
        """Collapse/expand handle for the toolbar — just the chevron, no
        "TOOLBAR" text label."""
        rect = self._toggle_rect()
        t = self._toggle_anim
        base = uk.lerp_color((22, 25, 35), (30, 34, 46), t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, t)
        uk.draw_panel(screen, rect, bg=(*base, 235), border=border, border_width=1, radius=6, shadow=False)
        # Always clickable (handle_click checks this rect before the item
        # panel's click-intercept logic), so register unconditionally.
        uk.register_hoverable(rect)

        chevron_color = uk.lerp_color(uk.Theme.TEXT_MUTED, uk.Theme.GOLD_BRIGHT, t)
        _draw_chevron_icon(screen, rect, chevron_color, up=self.visible, width=2)

    def _draw_tooltip(self, screen, text):
        if not text:
            return
        mouse_x, _ = pygame.mouse.get_pos()
        label = self.font.render(text, color=uk.Theme.TEXT_PRIMARY, height=self.hint_size)
        pad_x, pad_y = 12, 7
        w = label.get_width() + pad_x * 2
        h = label.get_height() + pad_y * 2
        x = max(6, min(mouse_x - w // 2, self.screen_width - w - 6))
        y = self.height + 8
        rect = pygame.Rect(x, y, w, h)
        uk.draw_panel(screen, rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=1, radius=6, shadow=True)
        uk.blit_surface(screen, label, label.get_rect(center=rect.center), transient=True)

    # =========================================================================
    # Item panel — internal
    # =========================================================================

    def _update_item_panel_drag(self, mouse_pos):
        """Poll-based drag, independent of whatever event convention the
        caller uses for handle_click. Cursor is NOT set here — see
        _update_item_panel_cursor, which resolves move-vs-resize priority
        once both this and _update_item_panel_resize have run (calling
        set_move_cursor/set_resize_cursor_ns from both places, in sequence,
        would just have the second call stomp the first every frame —
        the same single-shared-cursor-state trap world_map_editor's own
        update() works around for its move/resize/I-beam priority)."""
        if not self.item_panel_open:
            self._item_dragging = False
            return

        pressed = pygame.mouse.get_pressed()[0]
        hovering_header = (self._item_panel_header_rect.collidepoint(mouse_pos)
                            and not self._item_close_rect.collidepoint(mouse_pos))

        if self._item_dragging:
            if pressed:
                new_x = mouse_pos[0] - self._item_drag_offset[0]
                new_y = mouse_pos[1] - self._item_drag_offset[1]
                self._item_win_pos = (new_x, new_y)
            else:
                self._item_dragging = False
        elif pressed and hovering_header:
            px, py = self._item_panel_rect.topleft
            self._item_drag_offset = (mouse_pos[0] - px, mouse_pos[1] - py)
            self._item_dragging = True

    def _update_item_panel_resize(self, mouse_pos):
        """Poll-based drag on the bottom-edge grip, same convention as
        _update_item_panel_drag above — just adjusting _item_win_height
        instead of _item_win_pos. Once the user has resized, the panel
        stops auto-filling the available height (see _draw_item_panel)
        and remembers this explicit size across close/reopen instead.
        Cursor is NOT set here — see _update_item_panel_cursor."""
        if not self.item_panel_open:
            self._item_resizing = False
            return

        pressed = pygame.mouse.get_pressed()[0]
        hovering_grip = self._item_resize_rect.collidepoint(mouse_pos)

        if self._item_resizing:
            if pressed:
                available_h = self.screen_height - self.height - 20
                new_h = mouse_pos[1] - self._item_panel_rect.top - self._item_resize_offset
                self._item_win_height = max(self.ITEM_PANEL_MIN_H, min(new_h, available_h))
            else:
                self._item_resizing = False
        elif pressed and hovering_grip:
            self._item_resize_offset = mouse_pos[1] - self._item_panel_rect.bottom
            self._item_resizing = True

    def wants_cursor(self) -> bool:
        """True while this toolbar is actively holding the shared OS cursor
        (the item panel's move/resize-ns claim, or the plain hand cursor
        over one of this bar's own clickable widgets). Callers that also
        touch the cursor unconditionally every frame — like RoomEditor's
        text-field I-beam check — need to check this first and skip their
        own call when it's True, or they'll stomp this toolbar's cursor
        back to the arrow the instant it's set. See
        _update_item_panel_cursor and _update_hand_cursor for the full
        story."""
        return self._owns_cursor or self._owns_hand_cursor

    def _update_item_panel_cursor(self, mouse_pos):
        """Resolve the single shared OS cursor between the header's move
        affordance and the bottom grip's resize affordance, exactly once
        per frame — after both _update_item_panel_drag and
        _update_item_panel_resize have updated state. Exactly ONE setter
        is called per branch below (never a True followed by another
        setter's False) — calling two in sequence was the original bug:
        set_move_cursor(True) then set_resize_cursor_ns(False) right after
        it stomps the shared cursor state back to the arrow every single
        frame, so it never stayed on 'move'.

        That fixed the flicker *inside* this function, but ui_kit's cursor
        state is shared with every other component too (ObjectEditor's
        own object-drag cursor, in particular). set_move_cursor(False) is
        not a no-op when something else currently owns the cursor — it
        really does reset it to the arrow — so blindly calling it every
        frame this panel happens to be closed (or not hovered) stomps
        whatever ObjectEditor set a moment earlier, which then re-asserts
        it next frame, which we stomp again: the same twitch, just between
        two components instead of within one. _owns_cursor tracks whether
        we're the one who last claimed the cursor, so we only ever release
        a cursor we actually own, and otherwise leave it alone — matching
        the "exactly one thing decides the cursor this frame" rule
        world_map_editor's own update() follows internally."""
        if not self.item_panel_open:
            if self._owns_cursor:
                uk.set_move_cursor(False)
                self._owns_cursor = False
            return

        hovering_header = (self._item_panel_header_rect.collidepoint(mouse_pos)
                            and not self._item_close_rect.collidepoint(mouse_pos))
        hovering_grip = self._item_resize_rect.collidepoint(mouse_pos)
        move_hover   = self._item_dragging or hovering_header
        resize_hover = self._item_resizing or hovering_grip

        if move_hover:
            uk.set_move_cursor(True)
            self._owns_cursor = True
        elif resize_hover:
            uk.set_resize_cursor_ns(True)
            self._owns_cursor = True
        else:
            if self._owns_cursor:
                uk.set_move_cursor(False)
            self._owns_cursor = False

    def _update_hand_cursor(self, mouse_pos):
        """Pointing-hand cursor for every clickable toolbar widget — the
        tool/action buttons, the show/hide tab, and (while the item panel
        is open) its close button and item thumbnails.

        This is resolved by the toolbar itself, every frame, rather than
        relying on uk.register_hoverable + some other editor's own
        uk.update_hover_cursor call to happen to sweep up these rects too
        (see the module-level register_hoverable calls in draw() below,
        kept as a harmless belt-and-braces for whatever *does* do a
        frame-final sweep). That reliance doesn't actually cover the main
        case: whichever editor currently resolves the shared cursor only
        runs while its own tool is active, so the OTHER tool buttons in
        this bar — the ones you'd hover to switch away to a different
        editor — were never covered by anyone. Resolving it here instead
        makes every button hand-cursor-correct regardless of which tool
        (if any) currently owns the canvas below the bar.

        Same no-stomp ownership rule as _update_item_panel_cursor: only
        ever release a cursor this toolbar actually claimed. And when the
        item panel's own move/resize claim owns the cursor this frame
        (_owns_cursor), defer to it entirely rather than touching the
        shared cursor again here — that's a more specific affordance than
        a plain hand, and _update_item_panel_cursor (called just above)
        already applied it this frame; calling set_hand_cursor here too
        would just stomp it straight back to the arrow."""
        if self._owns_cursor:
            self._owns_hand_cursor = False
            return

        if self._toggle_rect().collidepoint(mouse_pos):
            hovering = True
        elif not self.visible:
            hovering = False
        elif self.item_panel_open:
            hovering = (self._item_close_rect.collidepoint(mouse_pos)
                        or any(r.collidepoint(mouse_pos) for r in self._item_rects.values()))
        else:
            hovering = any(rect.collidepoint(mouse_pos) for _, rect, _ in self._button_rects())

        if hovering:
            uk.set_hand_cursor(True)
            self._owns_hand_cursor = True
        elif self._owns_hand_cursor:
            uk.set_hand_cursor(False)
            self._owns_hand_cursor = False

    def _wrap_text(self, text, height, max_w):
        """Word-wrap `text` into lines that each fit within max_w at the
        given render height. Same rationale as object_editor's Keybinds
        popup (measure with the real font instead of guessing character
        counts) — a bitmap font's glyph widths vary too much per-letter for
        a fixed characters-per-line estimate to reliably avoid overflow."""
        words = text.split(' ')
        lines = []
        cur = ''
        for word in words:
            trial = word if not cur else cur + ' ' + word
            if self.font.size(trial, height=height)[0] <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    def _ensure_items_scanned(self):
        """Build the category → item-id sections shown in the panel. Only
        needs to run once — ITEMS is static data, not something that changes
        while the editor is open."""
        if self._items_scan_done:
            return
        self._items_scan_done = True
        self._item_sections = []
        for category, label in self.ITEM_CATEGORY_LABELS.items():
            by_cat = get_items_by_category(category)
            if by_cat:
                self._item_sections.append((label, category, list(by_cat.keys())))

    def _load_item_icon(self, item_id, category=None):
        if item_id in self._item_icons:
            return self._item_icons[item_id]
        subdir = self.ITEM_CATEGORY_SUBDIRS.get(category, '')
        try:
            img = pygame.image.load(
                os.path.join(self.ITEM_SPRITE_DIR, subdir, f'{item_id}.png')).convert_alpha()
            iw, ih = img.get_size()
            size  = self.ITEM_THUMB_SIZE - 16
            scale = min(size / iw, size / ih)
            self._item_icons[item_id] = pygame.transform.scale(
                img, (max(1, int(iw * scale)), max(1, int(ih * scale))))
        except Exception:
            self._item_icons[item_id] = None
        return self._item_icons[item_id]

    def _fit_label(self, text, height, max_w):
        """Bitmap-font text clipped to max_w, trimmed with a '...' suffix
        (period glyph x3 — BitmapFont has no single ellipsis glyph) rather
        than measured in characters, so it fits regardless of glyph width."""
        if self.font.size(text, height=height)[0] <= max_w:
            return text
        trimmed = text
        while trimmed and self.font.size(trimmed + '...', height=height)[0] > max_w:
            trimmed = trimmed[:-1]
        return trimmed + '...' if trimmed else '...'

    @staticmethod
    def _blit_clipped(screen, surf, pos, clip):
        """Blit `surf` at `pos`, trimmed to `clip` first.

        screen.set_clip() only reliably affects direct surface blits — the
        transient blit path used for freshly-rendered text/icons can paint
        straight past it (same issue tileset_editor's palette grid worked
        around). Without this, section headers and item-name labels drawn
        past the bottom of a scrolled item grid render at their true
        on-screen position instead of being hidden, leaking below the
        panel entirely rather than just being clipped at its edge."""
        dest = pygame.Rect(pos, surf.get_size())
        vis = dest.clip(clip)
        if vis.w <= 0 or vis.h <= 0:
            return
        area = vis.move(-dest.x, -dest.y)
        uk.blit_surface(screen, surf.subsurface(area), vis.topleft, transient=True)

    def _draw_item_panel(self, screen):
        """Browse view of every consumable/equip item in core/items.py,
        grouped by category. Hovering an icon shows its name, effect text,
        and description at the bottom; clicking one arms it for placement
        (see get_selected_item_id) and closes the panel.

        Floats and drags like a real window (see _update_item_panel_drag)
        rather than being docked full-height like the Object/Entity editor
        side palettes, since a browse-and-pick popup benefits from being
        movable out of the way instead of pinned to one spot."""
        from core.items import get_item

        SW, SH      = self.screen_width, self.screen_height
        available_h = SH - self.height - 20
        if self._item_win_height is None:
            raw_h = available_h
        else:
            raw_h = max(self.ITEM_PANEL_MIN_H, min(self._item_win_height, available_h))
        # draw_panel's rounded-rect fill/border and its drop shadow are each
        # cached by exact (w, h, ...) and rebuilt from scratch (at 8x
        # supersample) on a cache miss — fine when PANEL_H is one fixed
        # value, but while the resize grip is being dragged it changes by
        # 1px almost every frame, so every frame was a miss: a full-size
        # supersampled rounded-rect *and* gaussian-blurred shadow rebuild
        # per frame, which is the actual lag. Floor PANEL_H to an 8px grid
        # (same quantization size — and same cache-hit-rate rationale — as
        # the toolbar's own button hover-glow rounding, and as
        # world_map_editor's PANEL_LAYOUT_SNAP) so a drag revisits only
        # ~1/8th as many distinct sizes; flooring rather than rounding
        # keeps it from ever exceeding available_h.
        PANEL_H = max(self.ITEM_PANEL_MIN_H, (raw_h // 8) * 8)

        if self._item_win_pos is not None:
            PX, PY = self._item_win_pos
        else:
            PX = (SW - self.ITEM_PANEL_W) // 2
            PY = self.height + 10
        # Clamp every frame (rather than clamping the stored preference) so
        # a window dragged to an edge stays reachable even if the screen
        # size changes, without losing the raw dragged position.
        PX = max(4, min(PX, SW - self.ITEM_PANEL_W - 4))
        PY = max(self.height + 4, min(PY, SH - PANEL_H - 8))

        self._item_panel_rect = pygame.Rect(PX, PY, self.ITEM_PANEL_W, PANEL_H)
        uk.draw_panel(screen, self._item_panel_rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=2, radius=uk.Theme.RADIUS_PANEL, shadow=True)

        # Small grab-bar, purely a visual cue that the header can be
        # dragged — same affordance language as a mobile bottom-sheet.
        grip_w = 28
        grip_rect = pygame.Rect(PX + (self.ITEM_PANEL_W - grip_w) // 2, PY + 5, grip_w, 3)
        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER, grip_rect, 0, 2)

        # Bottom-edge resize strip — same poll-based drag pattern as the
        # header (see _update_item_panel_resize), just for height instead
        # of position. Centered on the panel's bottom border so it's easy
        # to land on without feeling like a separate control.
        self._item_resize_rect = pygame.Rect(
            PX, self._item_panel_rect.bottom - self.ITEM_RESIZE_GRIP // 2,
            self.ITEM_PANEL_W, self.ITEM_RESIZE_GRIP
        )
        resize_hov = self._item_resize_rect.collidepoint(pygame.mouse.get_pos())
        bottom_grip_rect = pygame.Rect(
            PX + (self.ITEM_PANEL_W - grip_w) // 2, self._item_panel_rect.bottom - 4, grip_w, 3
        )
        uk.draw_rect_on(
            screen, uk.Theme.GOLD if (resize_hov or self._item_resizing) else uk.Theme.PANEL_BORDER,
            bottom_grip_rect, 0, 2
        )

        title_y = PY + 12
        title_s = self.font.render('Items', color=uk.Theme.GOLD, height=self.title_size)
        uk.blit_surface(screen, title_s, (PX + 16, title_y), transient=True)

        # Close button, top-right of the header — same rounded hover-card
        # language as ui_kit.IconButton's gear/close affordances.
        close_size = 20
        close_rect = pygame.Rect(PX + self.ITEM_PANEL_W - close_size - 12, title_y - 2, close_size, close_size)
        self._item_close_rect = close_rect
        close_hov = close_rect.collidepoint(pygame.mouse.get_pos())
        uk.draw_rect_on(screen, uk.Theme.CARD_BG_HOVER if close_hov else uk.Theme.CARD_BG, close_rect, 0, 6)
        uk.draw_rect_on(screen, uk.Theme.DANGER_BRIGHT if close_hov else uk.Theme.CARD_BORDER, close_rect, 1, 6)
        uk.draw_close_icon(screen, close_rect, uk.Theme.DANGER_BRIGHT if close_hov else uk.Theme.TEXT_MUTED)
        uk.register_hoverable(close_rect)

        if self.selected_item_id:
            _sel_data = get_item(self.selected_item_id)
            _sel_name = _sel_data['name'] if _sel_data else self.selected_item_id
            sub_text = f'Selected: {_sel_name} - click again to deselect'
        else:
            sub_text = 'Click an item to select it, then click a chest to add it as loot.'

        # Wrapped (not just measured-and-hoped-for) so a long selected item
        # name, or this instruction text at a narrow panel width, can never
        # run past the panel edge or under the close button.
        content_w = self.ITEM_PANEL_W - 32 - close_size - 8
        sub_lines = self._wrap_text(sub_text, self.body_size, content_w)
        sub_line_h = self.body_size + 4
        sub_top = title_y + self.title_size + 6
        for i, line in enumerate(sub_lines):
            line_s = self.font.render(line, color=uk.Theme.TEXT_MUTED, height=self.body_size)
            uk.blit_surface(screen, line_s, (PX + 16, sub_top + i * sub_line_h), transient=True)

        header_bottom = sub_top + len(sub_lines) * sub_line_h
        divider_y = header_bottom + 6
        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER,
                        pygame.Rect(PX + 16, divider_y, self.ITEM_PANEL_W - 32, 1), 0, 0)

        # The whole header block (grip through divider) is the drag handle —
        # see _update_item_panel_drag, which reads this rect back next frame.
        header_h = (divider_y - PY) + 8
        self._item_panel_header_rect = pygame.Rect(PX, PY, self.ITEM_PANEL_W, header_h)

        # Reserve space at the bottom for the hovered item's description
        DESC_H = 62
        grid_top    = PY + header_h
        grid_bottom = PY + PANEL_H - DESC_H - 8
        grid_rect   = pygame.Rect(PX, grid_top, self.ITEM_PANEL_W, grid_bottom - grid_top)
        self._item_grid_rect = grid_rect

        old_clip = screen.get_clip()
        screen.set_clip(grid_rect)

        self._item_rects = {}
        row_h = self.ITEM_THUMB_SIZE + self.ITEM_THUMB_PAD + 14  # + name label
        cy    = grid_top + self.ITEM_THUMB_PAD - self._item_scroll

        if not self._item_sections:
            no_s = self.font.render('No items defined', color=uk.Theme.TEXT_MUTED, height=self.body_size)
            self._blit_clipped(screen, no_s, (PX + 12, grid_top + 12), grid_rect)
        else:
            for label, category, item_ids in self._item_sections:
                hdr_s = self.font.render(label, color=uk.Theme.TEXT_SECONDARY, height=self.body_size + 1)
                self._blit_clipped(screen, hdr_s, (PX + self.ITEM_THUMB_PAD, cy), grid_rect)
                cy += 22

                col = 0
                for item_id in item_ids:
                    cx = PX + self.ITEM_THUMB_PAD + col * (self.ITEM_THUMB_SIZE + self.ITEM_THUMB_PAD)
                    cell = pygame.Rect(cx, cy, self.ITEM_THUMB_SIZE, self.ITEM_THUMB_SIZE)
                    self._item_rects[item_id] = cell
                    uk.register_hoverable(cell)

                    is_hov = item_id == self._item_hover
                    is_sel = item_id == self.selected_item_id

                    if is_sel:
                        cell_bg, border, bw = uk.Theme.CARD_BG_SELECTED, uk.Theme.GOLD_BRIGHT, 3
                    elif is_hov:
                        cell_bg, border, bw = uk.Theme.CARD_BG_HOVER, uk.Theme.GOLD, 2
                    else:
                        cell_bg, border, bw = uk.Theme.CARD_BG, uk.Theme.CARD_BORDER, 1

                    uk.draw_rect_on(screen, cell_bg, cell, 0, 6)
                    uk.draw_rect_on(screen, border, cell, bw, 6)

                    icon = self._load_item_icon(item_id, category)
                    if icon:
                        uk.blit_surface(screen, icon, icon.get_rect(center=cell.center), transient=False)
                    else:
                        q = self.font.render('?', color=uk.Theme.TEXT_MUTED, height=20)
                        self._blit_clipped(screen, q, q.get_rect(center=cell.center).topleft, grid_rect)

                    item_data = get_item(item_id)
                    name = item_data['name'] if item_data else item_id
                    lbl_color = (uk.Theme.GOLD_BRIGHT if is_sel
                                 else uk.Theme.GOLD if is_hov
                                 else uk.Theme.TEXT_MUTED)
                    name = self._fit_label(name, 9, self.ITEM_THUMB_SIZE)
                    lbl  = self.font.render(name, color=lbl_color, height=9)
                    lbl_rect = lbl.get_rect(midtop=(cell.centerx, cell.bottom + 2))
                    self._blit_clipped(screen, lbl, lbl_rect.topleft, grid_rect)

                    col += 1
                    if col >= self.ITEM_COLS:
                        col = 0
                        cy += row_h
                if col != 0:
                    cy += row_h
                cy += 10  # gap before next section header

        max_scroll = max(0, cy + self._item_scroll - (grid_top + self.ITEM_THUMB_PAD) - grid_rect.height)
        self._item_scroll = min(self._item_scroll, max_scroll)

        screen.set_clip(old_clip)

        # Scroll indicator dots on right edge
        if max_scroll > 0:
            n = 8
            dot_x = PX + self.ITEM_PANEL_W - 6
            for d in range(n):
                dot_y  = grid_rect.top + int(grid_rect.height * d / max(1, n - 1))
                ratio  = self._item_scroll / max(1, max_scroll)
                active = abs(d / max(1, n - 1) - ratio) < 0.15
                uk.draw_circle_on(screen, uk.Theme.GOLD if active else uk.Theme.CARD_BORDER, (dot_x, dot_y), 3)

        # ── Hovered item description strip ──────────────────────────────────
        desc_rect = pygame.Rect(PX + 8, PY + PANEL_H - DESC_H, self.ITEM_PANEL_W - 16, DESC_H - 6)
        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER,
                        pygame.Rect(PX + 8, desc_rect.top - 4, self.ITEM_PANEL_W - 16, 1), 0, 0)

        if self._item_hover:
            item_data = get_item(self._item_hover)
            if item_data:
                name_s = self._fit_label(item_data['name'], self.body_size + 2, desc_rect.width)
                name_s = self.font.render(name_s, color=uk.Theme.GOLD, height=self.body_size + 2)
                uk.blit_surface(screen, name_s, (desc_rect.x, desc_rect.y), transient=True)

                desc_text = item_data.get('effect_text') or item_data.get('description', '')
                # Capped at 2 lines (the strip's fixed height doesn't grow
                # per-item) — a description longer than that is trimmed
                # with '...' on the last line rather than spilling out.
                full_lines = self._wrap_text(desc_text, self.body_size, desc_rect.width)
                desc_lines = full_lines[:2]
                if len(full_lines) > 2:
                    overflow = ' '.join(full_lines[1:])
                    desc_lines[1] = self._fit_label(overflow, self.body_size, desc_rect.width)
                for i, line in enumerate(desc_lines):
                    desc_s = self.font.render(line, color=uk.Theme.TEXT_MUTED, height=self.body_size)
                    uk.blit_surface(screen, desc_s, (desc_rect.x, desc_rect.y + 20 + i * (self.body_size + 4)),
                                    transient=True)
        else:
            hint = self.font.render('Hover an item to see its effect.', color=uk.Theme.TEXT_DIM, height=self.body_size)
            uk.blit_surface(screen, hint, (desc_rect.x, desc_rect.y + 8), transient=True)