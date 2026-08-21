import os
import pygame
import pygame.gfxdraw

from core.items import (
    get_items_by_category, CATEGORY_SUPPLIES, CATEGORY_STORY_ITEMS, CATEGORY_EQUIP_BODY,
    CATEGORY_EQUIP_HANDS, CATEGORY_EQUIP_FEET, CATEGORY_EQUIP_ACCESSORY,
)


class EditorToolbar:
    """
    Top toolbar for the room editor.
    Provides tool-mode switching (tiles, objects, entities, etc.)
    and quick-action buttons (zoom, test, save) on the right side.
    The toolbar can be hidden/shown via the toggle tab at its bottom edge.

    Background panel
    ----------------
    Clicking the 'Background' tool button opens a floating panel that lets
    the designer pick an image from assets/bg and set scroll speed + parallax.
    The panel communicates back via handle_click() returning 'bg_apply',
    after which the caller should read toolbar.get_bg_settings() and apply
    the result to room.scrolling_bg.

    Callers must also:
      • call toolbar.handle_mousedown(pos) on MOUSEBUTTONDOWN
      • call toolbar.handle_mouseup()      on MOUSEBUTTONUP
      • call toolbar.handle_mousemotion(pos) on MOUSEMOTION
      • call toolbar.handle_scroll(direction) on scroll-wheel events
      • call toolbar.sync_from_room(room.scrolling_bg) when the active room changes

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
    way the 'Background' button glows when a background image is set, so
    the armed state is visible even with the panel closed.

    Equip-item sprites live in a subfolder of ITEM_SPRITE_DIR rather than
    directly in it (e.g. assets/sprites/items/equipment/body/*.png) — see
    ITEM_CATEGORY_SUBDIRS. Adding a new equip category later means adding
    both a ITEM_CATEGORY_LABELS entry and, if its sprites live in a
    subfolder, an ITEM_CATEGORY_SUBDIRS entry.
    """

    # ── Background panel constants ────────────────────────────────────────────
    BG_DIR       = os.path.join('assets', 'bg')
    THUMB_SIZE   = 96
    THUMB_PAD    = 10
    THUMB_COLS   = 4
    PANEL_W      = THUMB_COLS * (THUMB_SIZE + THUMB_PAD) + THUMB_PAD + 16
    SLIDER_H     = 14
    SLIDER_TRACK = 6
    SCROLL_MAX   = 400.0   # ±px/s range for scroll_x and scroll_y

    # ── Item panel constants ────────────────────────────────────────────────
    ITEM_SPRITE_DIR = os.path.join('assets', 'sprites', 'items')
    ITEM_THUMB_SIZE  = 72
    ITEM_THUMB_PAD   = 10
    ITEM_COLS        = 5
    ITEM_PANEL_W     = ITEM_COLS * (ITEM_THUMB_SIZE + ITEM_THUMB_PAD) + ITEM_THUMB_PAD + 16
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

        # Layout constants
        self.height      = 80
        self.padding     = 10
        self.tool_size   = 60
        self.tool_spacing = 10

        self.font_small  = pygame.font.Font(None, 16)
        self.font_medium = pygame.font.Font(None, 20)
        self.font_large  = pygame.font.Font(None, 26)

        self.colors = {
            'bg':             (25, 25, 40),
            'bg_transparent': (25, 25, 40, 200),
            'panel':          (30, 30, 50),
            'panel_border':   (70, 70, 100),
            'tool_bg':        (35, 35, 55),
            'tool_hover':     (45, 45, 65),
            'tool_selected':  (255, 215, 0),
            'tool_border':    (60, 60, 80),
            'accent':         (255, 215, 0),
            'text':           (255, 255, 255),
            'text_dim':       (180, 180, 200),
            'success':        (100, 255, 100),
            'danger':         (255, 100, 100),
            'slider_track':   (55, 55, 80),
            'slider_fill':    (255, 215, 0),
        }

        # Which editor mode is active
        self.current_tool = 'tiles'

        # Left-side mode buttons
        self.tools = [
            {'id': 'tiles',      'label': 'Tiles',      'icon': self._create_tile_icon,       'tooltip': 'Edit terrain tiles (F2)'},
            {'id': 'objects',    'label': 'Objects',    'icon': self._create_object_icon,     'tooltip': 'Place objects and decorations'},
            {'id': 'entities',   'label': 'Entities',   'icon': self._create_entity_icon,     'tooltip': 'Add NPCs and enemies'},
            {'id': 'items',      'label': 'Items',      'icon': self._create_item_icon,       'tooltip': 'Place collectible items'},
            {'id': 'settings',   'label': 'Room',       'icon': self._create_settings_icon,   'tooltip': 'Room properties'},
            {'id': 'weather',    'label': 'Weather',    'icon': self._create_weather_icon,    'tooltip': 'Add weather effects'},
            {'id': 'background', 'label': 'Background', 'icon': self._create_background_icon, 'tooltip': 'Set scrolling background (assets/bg)'},
            {'id': 'map_paint',  'label': 'Map',         'icon': self._create_map_paint_icon,  'tooltip': 'Paint the Scouter minimap shape (F6)'},
        ]

        # Right-side action buttons
        self.actions = [
            {'id': 'grid', 'label': 'Grid: 16px', 'icon': self._create_grid_icon, 'tooltip': 'Placement grid: 16px — click to cycle (Off / 8px / 16px)', 'color': (150, 220, 150)},
            {'id': 'zoom', 'label': 'Zoom', 'icon': self._create_zoom_icon, 'tooltip': 'Zoom to fit whole room',  'color': (100, 200, 255)},
            {'id': 'test', 'label': 'Test', 'icon': self._create_play_icon, 'tooltip': 'Test room (F5)',          'color': self.colors['success']},
            {'id': 'save', 'label': 'Save', 'icon': self._create_save_icon, 'tooltip': 'Save room (Ctrl+S)',      'color': self.colors['accent']},
        ]

        self.zoom_active = False

        # ── Placement grid state ────────────────────────────────────────────
        # Quick, global "snap grid" size shared by tile painting and object /
        # entity placement & repositioning. 0 == no grid (free placement).
        # Cycled via the 'Grid' action button (or read with get_grid_size()).
        self.grid_sizes      = [0, 8, 16]
        self.grid_size_index = 2  # default 16px, matches prior always-on snap behaviour
        self._sync_grid_label()

        # Hover tracking
        self.hover_tool   = None
        self.hover_action = None

        # Per-button hover animation
        self.anim_timer       = 0
        self.tool_hover_anim  = [0.0] * (len(self.tools) + len(self.actions))

        # Show/hide toggle
        self.visible      = True
        self.tab_w        = 72
        self.tab_h        = 18
        self.hover_toggle = False

        # Sprite icon cache
        self._sprites: dict = {}
        self._load_sprites()

        # ── Background panel state ────────────────────────────────────────────
        self.bg_panel_open    = False
        self._bg_files:  list = []
        self._bg_thumbs: dict = {}          # fname → Surface | None
        self._bg_scroll       = 0           # vertical scroll offset in the grid
        self._bg_selected     = ''          # chosen filename ('' = none)
        self._bg_hover        = ''
        self._bg_scroll_x     = 0.0
        self._bg_scroll_y     = 0.0
        self._bg_parallax     = 0.5
        self._bg_drag_slider  = None        # 'scroll_x' | 'scroll_y' | 'parallax' | None
        self._bg_panel_rect   = pygame.Rect(0, 0, 0, 0)
        self._bg_grid_rect    = pygame.Rect(0, 0, 0, 0)
        self._bg_thumb_rects: dict  = {}
        self._bg_slider_rects: dict = {}
        self._bg_clear_rect   = pygame.Rect(0, 0, 0, 0)
        self._bg_scan_done    = False

        # ── Item panel state ───────────────────────────────────────────────────
        self.item_panel_open  = False
        self._item_sections:  list = []   # [(label, [item_id, ...]), ...]
        self._item_icons:     dict = {}   # item_id → Surface | None
        self._item_scroll     = 0
        self._item_hover      = ''        # hovered item_id
        self._item_rects:     dict = {}   # item_id → Rect (grid cell, for hover/tooltip)
        self._item_panel_rect = pygame.Rect(0, 0, 0, 0)
        self._item_grid_rect  = pygame.Rect(0, 0, 0, 0)
        self._items_scan_done = False
        self.selected_item_id = ''  # item id currently armed for placement/assignment, '' = none

    # =========================================================================
    # Public API
    # =========================================================================

    def sync_from_room(self, scrolling_bg: dict):
        """Call whenever the active room changes to mirror its bg settings."""
        bg = scrolling_bg or {}
        self._bg_selected = bg.get('image', '')
        self._bg_scroll_x = float(bg.get('scroll_x', 0.0))
        self._bg_scroll_y = float(bg.get('scroll_y', 0.0))
        self._bg_parallax = float(bg.get('parallax', 0.5))

    def get_bg_settings(self) -> dict:
        """Return current bg settings ready to assign to room.scrolling_bg."""
        result: dict = {}
        if self._bg_selected:
            result['image'] = self._bg_selected
        result['scroll_x'] = self._bg_scroll_x
        result['scroll_y'] = self._bg_scroll_y
        result['parallax'] = self._bg_parallax
        return result

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
        label = 'Grid: Off' if size == 0 else f'Grid: {size}px'
        for a in self.actions:
            if a['id'] == 'grid':
                a['label']   = label
                a['tooltip'] = f'Placement grid: {"Off" if size == 0 else f"{size}px"} — click to cycle (Off / 8px / 16px)'
                break

    def update(self, dt, mouse_pos):
        self.anim_timer   += dt
        self.hover_tool    = None
        self.hover_action  = None
        self.hover_toggle  = self._toggle_rect().collidepoint(mouse_pos)

        if not self.visible:
            return

        tool_start_x = self.padding
        for i, tool in enumerate(self.tools):
            r = pygame.Rect(tool_start_x + i * (self.tool_size + self.tool_spacing),
                            self.padding, self.tool_size, self.tool_size)
            if r.collidepoint(mouse_pos):
                self.hover_tool = tool['id']
                self.tool_hover_anim[i] = min(1.0, self.tool_hover_anim[i] + dt * 8)
            else:
                self.tool_hover_anim[i] = max(0.0, self.tool_hover_anim[i] - dt * 8)

        action_start_x = (self.screen_width - self.padding
                          - len(self.actions) * (self.tool_size + self.tool_spacing))
        for i, action in enumerate(self.actions):
            r = pygame.Rect(action_start_x + i * (self.tool_size + self.tool_spacing),
                            self.padding, self.tool_size, self.tool_size)
            ai = len(self.tools) + i
            if r.collidepoint(mouse_pos):
                self.hover_action = action['id']
                self.tool_hover_anim[ai] = min(1.0, self.tool_hover_anim[ai] + dt * 8)
            else:
                self.tool_hover_anim[ai] = max(0.0, self.tool_hover_anim[ai] - dt * 8)

        # Hover detection inside bg panel
        if self.bg_panel_open:
            self._bg_hover = ''
            for fname, rect in self._bg_thumb_rects.items():
                if rect.collidepoint(mouse_pos):
                    self._bg_hover = fname
                    break

        # Hover detection inside item panel
        if self.item_panel_open:
            self._item_hover = ''
            for iid, rect in self._item_rects.items():
                if rect.collidepoint(mouse_pos):
                    self._item_hover = iid
                    break

    def handle_click(self, mouse_pos) -> "str | None":
        """
        Returns a token string or None.
        'bg_apply' means get_bg_settings() has new data ready to write to the room.
        """
        if self._toggle_rect().collidepoint(mouse_pos):
            self.visible = not self.visible
            return 'toolbar_toggle'

        if not self.visible:
            return None

        # Background panel intercepts all clicks when open
        if self.bg_panel_open:
            result = self._handle_bg_panel_click(mouse_pos)
            if result is not None:
                return result
            if not self._bg_panel_rect.collidepoint(mouse_pos):
                self.bg_panel_open = False
            return None

        # Item panel intercepts all clicks when open. Clicking a thumbnail
        # arms/disarms that item (see get_selected_item_id) and closes the
        # panel; clicking elsewhere inside the panel is a no-op; clicking
        # outside it just closes the panel without changing the selection.
        if self.item_panel_open:
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
            if not self._item_panel_rect.collidepoint(mouse_pos):
                self.item_panel_open = False
            return None

        # Tool buttons
        tool_start_x = self.padding
        for i, tool in enumerate(self.tools):
            r = pygame.Rect(tool_start_x + i * (self.tool_size + self.tool_spacing),
                            self.padding, self.tool_size, self.tool_size)
            if r.collidepoint(mouse_pos):
                if tool['id'] == 'background':
                    self.item_panel_open = False
                    self.bg_panel_open = not self.bg_panel_open
                    if self.bg_panel_open:
                        self._ensure_bg_scanned()
                    return 'background_toggle'
                if tool['id'] == 'items':
                    self.bg_panel_open = False
                    self.item_panel_open = not self.item_panel_open
                    if self.item_panel_open:
                        self._ensure_items_scanned()
                    self.current_tool = tool['id']
                    return 'items_toggle'
                self.current_tool = tool['id']
                return tool['id']

        # Action buttons
        action_start_x = (self.screen_width - self.padding
                          - len(self.actions) * (self.tool_size + self.tool_spacing))
        for i, action in enumerate(self.actions):
            r = pygame.Rect(action_start_x + i * (self.tool_size + self.tool_spacing),
                            self.padding, self.tool_size, self.tool_size)
            if r.collidepoint(mouse_pos):
                if action['id'] == 'zoom':
                    self.zoom_active = not self.zoom_active
                elif action['id'] == 'grid':
                    self._cycle_grid_size()
                return f"action_{action['id']}"

        return None

    def handle_mousedown(self, mouse_pos) -> "str | None":
        """Call on MOUSEBUTTONDOWN — starts slider drags.

        Returns 'bg_apply' when a slider drag begins, mirroring handle_click()'s
        protocol, so the caller knows to copy get_bg_settings() onto the room
        right away rather than waiting for a thumbnail/clear click that may
        never come.
        """
        if not self.bg_panel_open:
            return None
        for key, track in self._bg_slider_rects.items():
            if track.collidepoint(mouse_pos):
                self._bg_drag_slider = key
                self._apply_slider_drag(key, mouse_pos[0], track)
                return 'bg_apply'
        return None

    def handle_mouseup(self):
        """Call on MOUSEBUTTONUP — ends slider drags."""
        self._bg_drag_slider = None

    def handle_mousemotion(self, mouse_pos) -> "str | None":
        """Call on MOUSEMOTION — updates slider values while dragging.

        Returns 'bg_apply' while a slider is actively being dragged so the
        caller can keep the room's scrolling_bg in sync live, not just at
        drag-start.
        """
        if self._bg_drag_slider and self._bg_drag_slider in self._bg_slider_rects:
            self._apply_slider_drag(self._bg_drag_slider, mouse_pos[0],
                                    self._bg_slider_rects[self._bg_drag_slider])
            return 'bg_apply'
        return None

    def handle_scroll(self, direction):
        """Call on scroll-wheel events (direction +1 = up, -1 = down)."""
        if (self.bg_panel_open
                and self._bg_grid_rect.collidepoint(pygame.mouse.get_pos())):
            self._bg_scroll = max(0, self._bg_scroll - direction * 80)
        if (self.item_panel_open
                and self._item_grid_rect.collidepoint(pygame.mouse.get_pos())):
            self._item_scroll = max(0, self._item_scroll - direction * 80)

    # =========================================================================
    # Drawing
    # =========================================================================

    def draw(self, screen):
        if self.visible:
            toolbar_bg = pygame.Surface((self.screen_width, self.height), pygame.SRCALPHA)
            toolbar_bg.fill(self.colors['bg_transparent'])
            screen.blit(toolbar_bg, (0, 0))
            pygame.draw.line(screen, self.colors['accent'],
                             (0, self.height), (self.screen_width, self.height), 2)

            tool_start_x = self.padding
            for i, tool in enumerate(self.tools):
                x = tool_start_x + i * (self.tool_size + self.tool_spacing)
                self._draw_tool_button(screen, tool, x, self.padding, i,
                                       selected=(tool['id'] == self.current_tool))

            action_start_x = (self.screen_width - self.padding
                              - len(self.actions) * (self.tool_size + self.tool_spacing))
            for i, action in enumerate(self.actions):
                x = action_start_x + i * (self.tool_size + self.tool_spacing)
                self._draw_action_button(screen, action, x, self.padding, len(self.tools) + i)

        self._draw_toggle_tab(screen)

        # Tooltips
        if self.hover_tool:
            tool = next(t for t in self.tools if t['id'] == self.hover_tool)
            self._draw_tooltip(screen, tool['tooltip'])
        elif self.hover_action:
            action = next(a for a in self.actions if a['id'] == self.hover_action)
            tip = ('Click to return to normal view' if self.zoom_active
                   else action['tooltip']) if action['id'] == 'zoom' else action['tooltip']
            self._draw_tooltip(screen, tip)

        # Background panel (always on top)
        if self.bg_panel_open:
            self._draw_bg_panel(screen)

        # Item panel (always on top)
        if self.item_panel_open:
            self._draw_item_panel(screen)

    # =========================================================================
    # Background panel — internal
    # =========================================================================

    def _ensure_bg_scanned(self):
        if self._bg_scan_done:
            return
        self._bg_scan_done = True
        try:
            self._bg_files = sorted(
                f for f in os.listdir(self.BG_DIR)
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp'))
            )
        except OSError:
            self._bg_files = []

    def _load_thumb(self, fname):
        if fname in self._bg_thumbs:
            return self._bg_thumbs[fname]
        try:
            img = pygame.image.load(os.path.join(self.BG_DIR, fname)).convert()
            iw, ih = img.get_size()
            scale = min(self.THUMB_SIZE / iw, self.THUMB_SIZE / ih)
            self._bg_thumbs[fname] = pygame.transform.scale(
                img, (max(1, int(iw * scale)), max(1, int(ih * scale))))
        except Exception:
            self._bg_thumbs[fname] = None
        return self._bg_thumbs[fname]

    def _apply_slider_drag(self, key, mouse_x, track):
        t = max(0.0, min(1.0, (mouse_x - track.x) / max(1, track.width)))
        if key == 'scroll_x':
            self._bg_scroll_x = round((t * 2 - 1) * self.SCROLL_MAX, 1)
        elif key == 'scroll_y':
            self._bg_scroll_y = round((t * 2 - 1) * self.SCROLL_MAX, 1)
        elif key == 'parallax':
            self._bg_parallax = round(t, 2)

    def _handle_bg_panel_click(self, mouse_pos) -> "str | None":
        # Thumbnail picks
        for fname, rect in self._bg_thumb_rects.items():
            if rect.collidepoint(mouse_pos):
                # Toggle: clicking the already-selected image deselects it
                self._bg_selected = '' if self._bg_selected == fname else fname
                return 'bg_apply'
        # Clear button
        if self._bg_clear_rect.collidepoint(mouse_pos):
            self._bg_selected = ''
            self._bg_scroll_x = 0.0
            self._bg_scroll_y = 0.0
            self._bg_parallax = 0.5
            return 'bg_apply'
        return None

    def _draw_bg_panel(self, screen):
        SW, SH   = screen.get_size()
        PANEL_H  = SH - self.height - 20
        PX = (SW - self.PANEL_W) // 2
        PY = self.height + 10

        self._bg_panel_rect = pygame.Rect(PX, PY, self.PANEL_W, PANEL_H)

        # Drop shadow
        shadow = pygame.Surface((self.PANEL_W + 8, PANEL_H + 8), pygame.SRCALPHA)
        shadow.fill((0, 0, 0, 90))
        screen.blit(shadow, (PX - 4, PY - 4))

        # Panel body
        pygame.draw.rect(screen, self.colors['panel'],
                         self._bg_panel_rect, border_radius=8)
        pygame.draw.rect(screen, self.colors['accent'],
                         self._bg_panel_rect, 2, border_radius=8)

        # Title + current selection
        title_s = self.font_large.render('Scrolling Background', True, self.colors['accent'])
        screen.blit(title_s, (PX + 12, PY + 10))

        sel_name = os.path.splitext(self._bg_selected)[0] if self._bg_selected else 'None'
        sel_col  = self.colors['text'] if self._bg_selected else self.colors['text_dim']
        sel_s    = self.font_medium.render(f'Selected: {sel_name}', True, sel_col)
        screen.blit(sel_s, (PX + 12, PY + 36))

        # ── Sliders ───────────────────────────────────────────────────────────
        inner_w = self.PANEL_W - 24
        sy = PY + 62
        self._bg_slider_rects = {}

        sx_t = (self._bg_scroll_x / self.SCROLL_MAX + 1) / 2
        self._draw_slider(screen, PX + 12, sy, inner_w,
                          'scroll_x', 'Scroll X', sx_t,
                          f'{self._bg_scroll_x:+.0f} px/s')
        sy += self.SLIDER_H + 24

        sy_t = (self._bg_scroll_y / self.SCROLL_MAX + 1) / 2
        self._draw_slider(screen, PX + 12, sy, inner_w,
                          'scroll_y', 'Scroll Y', sy_t,
                          f'{self._bg_scroll_y:+.0f} px/s')
        sy += self.SLIDER_H + 24

        self._draw_slider(screen, PX + 12, sy, inner_w,
                          'parallax', 'Parallax', self._bg_parallax,
                          f'{self._bg_parallax:.2f}')
        sy += self.SLIDER_H + 20

        hint = self.font_small.render(
            '0 = fixed on screen  ·  0.5 = half camera  ·  1 = moves with camera',
            True, self.colors['text_dim'])
        screen.blit(hint, (PX + 12, sy))
        sy += 20

        # ── Clear button ──────────────────────────────────────────────────────
        sy += 4
        clr_rect = pygame.Rect(PX + 12, sy, inner_w, 26)
        mx, my   = pygame.mouse.get_pos()
        clr_hov  = clr_rect.collidepoint(mx, my)
        pygame.draw.rect(screen, (130, 40, 40) if clr_hov else (70, 25, 25),
                         clr_rect, border_radius=4)
        pygame.draw.rect(screen, self.colors['danger'], clr_rect, 1, border_radius=4)
        clr_s = self.font_medium.render('Clear Background', True, self.colors['danger'])
        screen.blit(clr_s, clr_s.get_rect(center=clr_rect.center))
        self._bg_clear_rect = clr_rect
        sy += 34

        # ── Divider ───────────────────────────────────────────────────────────
        pygame.draw.line(screen, self.colors['panel_border'],
                         (PX + 8, sy), (PX + self.PANEL_W - 8, sy))
        sy += 8

        # ── Thumbnail grid ────────────────────────────────────────────────────
        grid_rect = pygame.Rect(PX, sy, self.PANEL_W, PY + PANEL_H - sy - 8)
        self._bg_grid_rect = grid_rect
        old_clip = screen.get_clip()
        screen.set_clip(grid_rect)

        self._bg_thumb_rects = {}
        col = row = 0
        total_rows = max(1, (len(self._bg_files) + self.THUMB_COLS - 1) // self.THUMB_COLS)
        row_h      = self.THUMB_SIZE + self.THUMB_PAD
        max_scroll = max(0, total_rows * row_h - grid_rect.height)
        self._bg_scroll = min(self._bg_scroll, max_scroll)

        if not self._bg_files:
            no_s = self.font_medium.render('No images found in assets/bg',
                                           True, self.colors['text_dim'])
            screen.blit(no_s, (PX + 12, sy + 12))
        else:
            for fname in self._bg_files:
                cx = PX + self.THUMB_PAD + col * row_h
                cy = sy + self.THUMB_PAD + row * row_h - self._bg_scroll
                cell = pygame.Rect(cx, cy, self.THUMB_SIZE, self.THUMB_SIZE)
                self._bg_thumb_rects[fname] = cell

                is_sel = fname == self._bg_selected
                is_hov = fname == self._bg_hover
                border = (self.colors['accent'] if is_sel else
                          self.colors['text']   if is_hov else
                          self.colors['panel_border'])
                bw = 2 if (is_sel or is_hov) else 1

                pygame.draw.rect(screen, (18, 18, 32), cell, border_radius=4)
                pygame.draw.rect(screen, border, cell, bw, border_radius=4)

                thumb = self._load_thumb(fname)
                if thumb:
                    screen.blit(thumb, thumb.get_rect(center=cell.center))
                else:
                    q = self.font_medium.render('?', True, self.colors['text_dim'])
                    screen.blit(q, q.get_rect(center=cell.center))

                # Filename strip
                lbl = self.font_small.render(
                    os.path.splitext(fname)[0], True,
                    self.colors['accent'] if is_sel else self.colors['text_dim'])
                screen.blit(lbl, (cell.x + 2, cell.bottom - 14))

                if is_sel:
                    chk = self.font_medium.render('✓', True, self.colors['accent'])
                    screen.blit(chk, (cell.right - 18, cell.top + 2))

                col += 1
                if col >= self.THUMB_COLS:
                    col = 0
                    row += 1

        screen.set_clip(old_clip)

        # Scroll indicator dots on right edge
        if max_scroll > 0:
            n   = min(8, total_rows)
            dot_x = PX + self.PANEL_W - 6
            for d in range(n):
                dot_y  = grid_rect.top + int(grid_rect.height * d / max(1, n - 1))
                ratio  = self._bg_scroll / max(1, max_scroll)
                active = abs(d / max(1, n - 1) - ratio) < 0.15
                pygame.gfxdraw.filled_circle(
                    screen, dot_x, dot_y, 3,
                    self.colors['accent'] if active else self.colors['panel_border'])

    def _draw_slider(self, screen, x, y, width, key, label, value, display):
        """Horizontal slider with label, value readout, and thumb."""
        lbl_s = self.font_small.render(label, True, self.colors['text_dim'])
        screen.blit(lbl_s, (x, y))

        val_s = self.font_small.render(display, True, self.colors['text'])
        screen.blit(val_s, (x + width - val_s.get_width(), y))

        track_y = y + self.SLIDER_H + 2
        track   = pygame.Rect(x, track_y, width, self.SLIDER_TRACK)
        pygame.draw.rect(screen, self.colors['slider_track'], track, border_radius=3)

        fill_w = max(0, int(value * width))
        if fill_w:
            pygame.draw.rect(screen, self.colors['slider_fill'],
                             pygame.Rect(x, track_y, fill_w, self.SLIDER_TRACK),
                             border_radius=3)

        thumb_x = x + int(value * width)
        thumb_cy = track_y + self.SLIDER_TRACK // 2
        THUMB_R  = 7
        mx, my   = pygame.mouse.get_pos()
        dragging = self._bg_drag_slider == key
        hovered  = (abs(mx - thumb_x) <= THUMB_R + 3
                    and abs(my - thumb_cy) <= THUMB_R + 3)
        tcol = self.colors['accent'] if (dragging or hovered) else self.colors['text']
        pygame.gfxdraw.filled_circle(screen, thumb_x, thumb_cy, THUMB_R, tcol)
        pygame.gfxdraw.aacircle(screen, thumb_x, thumb_cy, THUMB_R,
                                 self.colors['panel_border'])

        # Centre tick mark for scroll sliders (zero point)
        if key in ('scroll_x', 'scroll_y'):
            mid_x = x + width // 2
            pygame.draw.line(screen, self.colors['panel_border'],
                             (mid_x, track_y - 3), (mid_x, track_y + self.SLIDER_TRACK + 3), 1)

        self._bg_slider_rects[key] = track

    # =========================================================================
    # Item panel — internal
    # =========================================================================

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

    def _draw_item_panel(self, screen):
        """Browse view of every consumable/equip item in core/items.py,
        grouped by category. Hovering an icon shows its name, effect text,
        and description at the bottom; clicking one arms it for placement
        (see get_selected_item_id) and closes the panel."""
        from core.items import get_item

        SW, SH  = screen.get_size()
        PANEL_H = SH - self.height - 20
        PX = (SW - self.ITEM_PANEL_W) // 2
        PY = self.height + 10

        self._item_panel_rect = pygame.Rect(PX, PY, self.ITEM_PANEL_W, PANEL_H)

        # Drop shadow
        shadow = pygame.Surface((self.ITEM_PANEL_W + 8, PANEL_H + 8), pygame.SRCALPHA)
        shadow.fill((0, 0, 0, 90))
        screen.blit(shadow, (PX - 4, PY - 4))

        # Panel body
        pygame.draw.rect(screen, self.colors['panel'],
                         self._item_panel_rect, border_radius=8)
        pygame.draw.rect(screen, self.colors['accent'],
                         self._item_panel_rect, 2, border_radius=8)

        # Title
        title_s = self.font_large.render('Items', True, self.colors['accent'])
        screen.blit(title_s, (PX + 12, PY + 10))
        if self.selected_item_id:
            _sel_data = get_item(self.selected_item_id)
            _sel_name = _sel_data['name'] if _sel_data else self.selected_item_id
            sub_text = f'Selected: {_sel_name} — click again to deselect'
        else:
            sub_text = 'Click an item to select it, then click a chest to add it as loot.'
        sub_s = self.font_small.render(sub_text, True, self.colors['text_dim'])
        screen.blit(sub_s, (PX + 12, PY + 34))

        # Reserve space at the bottom for the hovered item's description
        DESC_H = 46
        grid_top    = PY + 56
        grid_bottom = PY + PANEL_H - DESC_H - 8
        grid_rect   = pygame.Rect(PX, grid_top, self.ITEM_PANEL_W, grid_bottom - grid_top)
        self._item_grid_rect = grid_rect

        old_clip = screen.get_clip()
        screen.set_clip(grid_rect)

        self._item_rects = {}
        row_h = self.ITEM_THUMB_SIZE + self.ITEM_THUMB_PAD + 14  # + name label
        cy    = grid_top + self.ITEM_THUMB_PAD - self._item_scroll

        if not self._item_sections:
            no_s = self.font_medium.render('No items defined', True, self.colors['text_dim'])
            screen.blit(no_s, (PX + 12, grid_top + 12))
        else:
            for label, category, item_ids in self._item_sections:
                # Section header
                hdr_s = self.font_medium.render(label, True, self.colors['text'])
                screen.blit(hdr_s, (PX + self.ITEM_THUMB_PAD, cy))
                cy += 22

                col = 0
                for item_id in item_ids:
                    cx = PX + self.ITEM_THUMB_PAD + col * (self.ITEM_THUMB_SIZE + self.ITEM_THUMB_PAD)
                    cell = pygame.Rect(cx, cy, self.ITEM_THUMB_SIZE, self.ITEM_THUMB_SIZE)
                    self._item_rects[item_id] = cell

                    is_hov = item_id == self._item_hover
                    is_sel = item_id == self.selected_item_id
                    if is_sel:
                        border, bw = self.colors['tool_selected'], 3
                    elif is_hov:
                        border, bw = self.colors['accent'], 2
                    else:
                        border, bw = self.colors['panel_border'], 1

                    cell_bg = (45, 40, 15) if is_sel else (18, 18, 32)
                    pygame.draw.rect(screen, cell_bg, cell, border_radius=4)
                    pygame.draw.rect(screen, border, cell, bw, border_radius=4)

                    icon = self._load_item_icon(item_id, category)
                    if icon:
                        screen.blit(icon, icon.get_rect(center=cell.center))
                    else:
                        q = self.font_medium.render('?', True, self.colors['text_dim'])
                        screen.blit(q, q.get_rect(center=cell.center))

                    item_data = get_item(item_id)
                    name = item_data['name'] if item_data else item_id
                    lbl_color = (self.colors['tool_selected'] if is_sel
                                 else self.colors['accent'] if is_hov
                                 else self.colors['text_dim'])
                    lbl  = self.font_small.render(name, True, lbl_color)
                    lbl_rect = lbl.get_rect(midtop=(cell.centerx, cell.bottom + 2))
                    if lbl_rect.width > self.ITEM_THUMB_SIZE:
                        lbl = self.font_small.render(name[:10] + '…', True, lbl_color)
                        lbl_rect = lbl.get_rect(midtop=(cell.centerx, cell.bottom + 2))
                    screen.blit(lbl, lbl_rect)

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
                pygame.gfxdraw.filled_circle(
                    screen, dot_x, dot_y, 3,
                    self.colors['accent'] if active else self.colors['panel_border'])

        # ── Hovered item description strip ──────────────────────────────────
        desc_rect = pygame.Rect(PX + 8, PY + PANEL_H - DESC_H, self.ITEM_PANEL_W - 16, DESC_H - 6)
        pygame.draw.line(screen, self.colors['panel_border'],
                         (PX + 8, desc_rect.top - 4), (PX + self.ITEM_PANEL_W - 8, desc_rect.top - 4))

        if self._item_hover:
            item_data = get_item(self._item_hover)
            if item_data:
                name_s = self.font_medium.render(item_data['name'], True, self.colors['accent'])
                screen.blit(name_s, (desc_rect.x, desc_rect.y))
                desc_text = item_data.get('effect_text') or item_data.get('description', '')
                desc_s = self.font_small.render(desc_text, True, self.colors['text_dim'])
                screen.blit(desc_s, (desc_rect.x, desc_rect.y + 20))
        else:
            hint = self.font_small.render('Hover an item to see its effect.',
                                          True, self.colors['text_dim'])
            screen.blit(hint, (desc_rect.x, desc_rect.y + 8))

    # =========================================================================
    # Toolbar drawing helpers
    # =========================================================================

    def _draw_tool_button(self, screen, tool, x, y, index, selected=False):
        button_rect = pygame.Rect(x, y, self.tool_size, self.tool_size)
        if self.tool_hover_anim[index] > 0:
            glow_a = int(self.tool_hover_anim[index] * 255)
            gs = pygame.Surface((self.tool_size + 6, self.tool_size + 6), pygame.SRCALPHA)
            pygame.draw.rect(gs, (*self.colors['accent'], glow_a // 2),
                             (0, 0, self.tool_size + 6, self.tool_size + 6), border_radius=8)
            screen.blit(gs, (x - 3, y - 3))

        if selected:
            bg_col, bdr_col, bdr_w = self.colors['tool_hover'], self.colors['tool_selected'], 3
        elif self.hover_tool == tool['id']:
            bg_col, bdr_col, bdr_w = self.colors['tool_hover'], self.colors['accent'], 2
        else:
            bg_col, bdr_col, bdr_w = self.colors['tool_bg'], self.colors['tool_border'], 1

        # Background-tool button glows when a background is active
        if tool['id'] == 'background' and self._bg_selected:
            bdr_col = self.colors['accent']
            bdr_w   = max(bdr_w, 2)

        # Items-tool button glows when an item is armed for placement, same
        # convention as the background button above — visible even with the
        # panel closed, since the selection persists after it auto-closes.
        if tool['id'] == 'items' and self.selected_item_id:
            bdr_col = self.colors['accent']
            bdr_w   = max(bdr_w, 2)

        pygame.draw.rect(screen, bg_col, button_rect, border_radius=8)
        pygame.draw.rect(screen, bdr_col, button_rect, bdr_w, border_radius=8)

        icon = self._get_icon_surface(tool['id'], tool['icon'])
        screen.blit(icon, icon.get_rect(
            center=(x + self.tool_size // 2, y + self.tool_size // 2 - 5)))

        label_col = self.colors['accent'] if selected else self.colors['text_dim']
        label     = self.font_small.render(tool['label'], True, label_col)
        screen.blit(label, label.get_rect(
            center=(x + self.tool_size // 2, y + self.tool_size - 8)))

    def _draw_action_button(self, screen, action, x, y, anim_index):
        button_rect  = pygame.Rect(x, y, self.tool_size, self.tool_size)
        action_color = action.get('color', self.colors['accent'])

        if self.tool_hover_anim[anim_index] > 0:
            glow_a = int(self.tool_hover_anim[anim_index] * 255)
            gs = pygame.Surface((self.tool_size + 6, self.tool_size + 6), pygame.SRCALPHA)
            pygame.draw.rect(gs, (*action_color, glow_a // 2),
                             (0, 0, self.tool_size + 6, self.tool_size + 6), border_radius=8)
            screen.blit(gs, (x - 3, y - 3))

        is_hover  = self.hover_action == action['id']
        is_active = action['id'] == 'zoom' and self.zoom_active
        lit       = is_hover or is_active
        bg_col    = self.colors['tool_hover'] if lit else self.colors['tool_bg']
        bdr_col   = action_color if lit else self.colors['tool_border']
        bdr_w     = 3 if is_active else (2 if is_hover else 1)

        pygame.draw.rect(screen, bg_col, button_rect, border_radius=8)
        pygame.draw.rect(screen, bdr_col, button_rect, bdr_w, border_radius=8)

        icon = self._get_icon_surface(action['id'], action['icon'])
        screen.blit(icon, icon.get_rect(
            center=(x + self.tool_size // 2, y + self.tool_size // 2 - 5)))

        label_col = action_color if lit else self.colors['text_dim']
        label     = self.font_small.render(action['label'], True, label_col)
        screen.blit(label, label.get_rect(
            center=(x + self.tool_size // 2, y + self.tool_size - 8)))

    def _draw_tooltip(self, screen, text):
        mouse_x, _ = pygame.mouse.get_pos()
        tip_s  = self.font_medium.render(text, True, self.colors['text'])
        tip_w  = tip_s.get_width() + 20
        tip_h  = tip_s.get_height() + 10
        tip_x  = max(5, min(mouse_x - tip_w // 2, self.screen_width - tip_w - 5))
        tip_y  = self.height + 10
        tip_r  = pygame.Rect(tip_x, tip_y, tip_w, tip_h)
        pygame.draw.rect(screen, self.colors['bg'],     tip_r, border_radius=5)
        pygame.draw.rect(screen, self.colors['accent'], tip_r, 1, border_radius=5)
        screen.blit(tip_s, tip_s.get_rect(center=tip_r.center))

    def _toggle_rect(self):
        tx = (self.screen_width - self.tab_w) // 2
        ty = (self.height - self.tab_h // 2) if self.visible else 0
        return pygame.Rect(tx, ty, self.tab_w, self.tab_h)

    def _draw_toggle_tab(self, screen):
        rect   = self._toggle_rect()
        bg     = self.colors['tool_hover'] if self.hover_toggle else self.colors['tool_bg']
        border = self.colors['accent']     if self.hover_toggle else self.colors['tool_border']
        pygame.draw.rect(screen, bg, rect, border_radius=6)
        pygame.draw.rect(screen, border, rect, 1, border_radius=6)
        arrow = '▲' if self.visible else '▼'
        lbl   = self.font_small.render(
            f'{arrow} Toolbar', True,
            self.colors['accent'] if self.hover_toggle else self.colors['text_dim'])
        screen.blit(lbl, lbl.get_rect(center=rect.center))

    # =========================================================================
    # Sprite loading
    # =========================================================================

    def _load_sprites(self):
        icon_size = self.tool_size - 10
        all_ids = [t['id'] for t in self.tools] + [a['id'] for a in self.actions]
        for sid in all_ids:
            try:
                img = pygame.image.load(f'assets/ui/toolbar/{sid}.png').convert_alpha()
                iw, ih = img.get_size()
                scale  = min(icon_size / iw, icon_size / ih)
                img    = pygame.transform.scale(img, (max(1, int(iw * scale)),
                                                      max(1, int(ih * scale))))
                self._sprites[sid] = img
            except Exception:
                pass

    def _get_icon_surface(self, tool_id, fallback_fn):
        return self._sprites.get(tool_id) or fallback_fn()

    # =========================================================================
    # Procedural icon builders
    # =========================================================================

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

    def _create_weather_icon(self):
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.gfxdraw.filled_circle(surf, 12, 10, 6, (200, 200, 220))
        pygame.gfxdraw.filled_circle(surf, 18, 10, 6, (200, 200, 220))
        pygame.gfxdraw.filled_circle(surf, 15,  8, 5, (200, 200, 220))
        for rx in [10, 16, 22]:
            pygame.draw.line(surf, (100, 150, 255), (rx, 18), (rx, 26), 2)
        return surf

    def _create_background_icon(self):
        """Layered landscape silhouette suggesting a scrolling sky/ground BG."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        # Sky gradient — three bands
        pygame.draw.rect(surf, (40,  80, 160), (0, 0, 32, 10))
        pygame.draw.rect(surf, (70, 120, 200), (0, 10, 32, 8))
        # Ground band
        pygame.draw.rect(surf, (40, 100,  40), (0, 18, 32, 14))
        # Hills as circles
        pygame.gfxdraw.filled_circle(surf,  8, 20, 9, (30, 80, 30))
        pygame.gfxdraw.filled_circle(surf, 22, 22, 7, (30, 80, 30))
        # Two scroll arrows to hint motion
        pygame.draw.line(surf, (255, 255, 255), (2, 6), (8,  6), 2)
        pygame.draw.line(surf, (255, 255, 255), (2, 6), (4,  4), 2)
        pygame.draw.line(surf, (255, 255, 255), (2, 6), (4,  8), 2)
        pygame.draw.line(surf, (255, 255, 255), (24, 6), (30, 6), 2)
        pygame.draw.line(surf, (255, 255, 255), (30, 6), (28, 4), 2)
        pygame.draw.line(surf, (255, 255, 255), (30, 6), (28, 8), 2)
        return surf

    def _create_map_paint_icon(self):
        """A small blob outline (mirrors the Scouter minimap's cyan-outline
        style) with a paintbrush corner accent, so the tool reads as
        'paint the map' rather than 'view the map'."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        blob = [(6, 20), (6, 12), (10, 8), (18, 8), (22, 12), (22, 18),
                (18, 22), (12, 22)]
        pygame.gfxdraw.filled_polygon(surf, blob, (40, 90, 255))
        pygame.gfxdraw.aapolygon(surf, blob, (100, 200, 255))
        pygame.draw.line(surf, (255, 215, 0), (21, 21), (28, 28), 3)
        pygame.gfxdraw.filled_circle(surf, 28, 28, 2, (255, 215, 0))
        return surf

    def _create_grid_icon(self):
        """A simple 3x3 grid, with the active cell count reflected by how
        many divisions are drawn (Off draws a dashed/faded grid instead)."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        size = self.grid_sizes[self.grid_size_index] if hasattr(self, 'grid_sizes') else 16
        if size == 0:
            # "No grid" — a dashed outline, faded, with a diagonal slash
            col = (140, 140, 150)
            pygame.draw.rect(surf, col, (5, 5, 22, 22), 2)
            pygame.draw.line(surf, (255, 120, 120), (6, 26), (26, 6), 2)
        else:
            col = (150, 220, 150)
            divisions = 2 if size == 16 else 4  # fewer, bigger cells for 16px
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