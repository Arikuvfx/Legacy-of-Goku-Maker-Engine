import os
import pygame
import pygame.gfxdraw


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
        ]

        # Right-side action buttons
        self.actions = [
            {'id': 'zoom', 'label': 'Zoom', 'icon': self._create_zoom_icon, 'tooltip': 'Zoom to fit whole room',  'color': (100, 200, 255)},
            {'id': 'test', 'label': 'Test', 'icon': self._create_play_icon, 'tooltip': 'Test room (F5)',          'color': self.colors['success']},
            {'id': 'save', 'label': 'Save', 'icon': self._create_save_icon, 'tooltip': 'Save room (Ctrl+S)',      'color': self.colors['accent']},
        ]

        self.zoom_active = False

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

        # Tool buttons
        tool_start_x = self.padding
        for i, tool in enumerate(self.tools):
            r = pygame.Rect(tool_start_x + i * (self.tool_size + self.tool_spacing),
                            self.padding, self.tool_size, self.tool_size)
            if r.collidepoint(mouse_pos):
                if tool['id'] == 'background':
                    self.bg_panel_open = not self.bg_panel_open
                    if self.bg_panel_open:
                        self._ensure_bg_scanned()
                    return 'background_toggle'
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