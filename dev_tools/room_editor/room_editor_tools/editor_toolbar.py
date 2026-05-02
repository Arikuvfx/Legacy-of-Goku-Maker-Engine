import pygame
import pygame.gfxdraw


class EditorToolbar:
    """
    Top toolbar for the room editor.
    Provides tool-mode switching (tiles, objects, entities, etc.)
    and quick-action buttons (zoom, test, save) on the right side.
    The toolbar can be hidden/shown via the toggle tab at its bottom edge.
    """

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height

        # Layout constants
        self.height = 80
        self.padding = 10
        self.tool_size = 60
        self.tool_spacing = 10

        self.font_small = pygame.font.Font(None, 16)
        self.font_medium = pygame.font.Font(None, 20)

        self.colors = {
            'bg':            (25, 25, 40),
            'bg_transparent': (25, 25, 40, 200),
            'tool_bg':       (35, 35, 55),
            'tool_hover':    (45, 45, 65),
            'tool_selected': (255, 215, 0),
            'tool_border':   (60, 60, 80),
            'accent':        (255, 215, 0),
            'text':          (255, 255, 255),
            'text_dim':      (180, 180, 200),
            'success':       (100, 255, 100),
            'danger':        (255, 100, 100),
        }

        # Which editor mode is active
        self.current_tool = 'tiles'  # 'tiles' | 'objects' | 'entities' | 'items' | 'settings' | 'weather'

        # Left-side mode buttons — each has a procedural icon fallback
        self.tools = [
            {'id': 'tiles',    'label': 'Tiles',    'icon': self._create_tile_icon,     'tooltip': 'Edit terrain tiles (F2)'},
            {'id': 'objects',  'label': 'Objects',  'icon': self._create_object_icon,   'tooltip': 'Place objects and decorations'},
            {'id': 'entities', 'label': 'Entities', 'icon': self._create_entity_icon,   'tooltip': 'Add NPCs and enemies'},
            {'id': 'items',    'label': 'Items',    'icon': self._create_item_icon,     'tooltip': 'Place collectible items'},
            {'id': 'settings', 'label': 'Room',     'icon': self._create_settings_icon, 'tooltip': 'Room properties'},
            {'id': 'weather',  'label': 'Weather',  'icon': self._create_weather_icon,  'tooltip': 'Add weather effects'},
        ]

        # Right-side action buttons (zoom / test / save)
        self.actions = [
            {'id': 'zoom', 'label': 'Zoom', 'icon': self._create_zoom_icon, 'tooltip': 'Zoom to fit whole room',  'color': (100, 200, 255)},
            {'id': 'test', 'label': 'Test', 'icon': self._create_play_icon, 'tooltip': 'Test room (F5)',          'color': self.colors['success']},
            {'id': 'save', 'label': 'Save', 'icon': self._create_save_icon, 'tooltip': 'Save room (Ctrl+S)',      'color': self.colors['accent']},
        ]

        # Toggle state for the zoom-fit action button
        self.zoom_active = False

        # Hover tracking
        self.hover_tool = None
        self.hover_action = None

        # Per-button hover animation (0.0–1.0), covers both tools and actions
        self.anim_timer = 0
        self.tool_hover_anim = [0.0] * (len(self.tools) + len(self.actions))

        # Show/hide toggle — tab is always rendered so the bar can be recalled
        self.visible = True
        self.tab_w = 72
        self.tab_h = 18
        self.hover_toggle = False

        # Sprite icon cache — loaded once, falls back to procedural if missing
        self._sprites: dict = {}
        self._load_sprites()

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    def update(self, dt, mouse_pos):
        """Advance animations and refresh hover state each frame."""
        self.anim_timer += dt
        self.hover_tool = None
        self.hover_action = None
        self.hover_toggle = self._toggle_rect().collidepoint(mouse_pos)

        if not self.visible:
            return

        # Animate left-side tool buttons
        tool_start_x = self.padding
        for i, tool in enumerate(self.tools):
            tool_rect = pygame.Rect(
                tool_start_x + i * (self.tool_size + self.tool_spacing),
                self.padding, self.tool_size, self.tool_size
            )
            if tool_rect.collidepoint(mouse_pos):
                self.hover_tool = tool['id']
                self.tool_hover_anim[i] = min(1.0, self.tool_hover_anim[i] + dt * 8)
            else:
                self.tool_hover_anim[i] = max(0.0, self.tool_hover_anim[i] - dt * 8)

        # Animate right-side action buttons
        action_start_x = self.screen_width - self.padding - len(self.actions) * (self.tool_size + self.tool_spacing)
        for i, action in enumerate(self.actions):
            action_rect = pygame.Rect(
                action_start_x + i * (self.tool_size + self.tool_spacing),
                self.padding, self.tool_size, self.tool_size
            )
            anim_idx = len(self.tools) + i
            if action_rect.collidepoint(mouse_pos):
                self.hover_action = action['id']
                self.tool_hover_anim[anim_idx] = min(1.0, self.tool_hover_anim[anim_idx] + dt * 8)
            else:
                self.tool_hover_anim[anim_idx] = max(0.0, self.tool_hover_anim[anim_idx] - dt * 8)

    def handle_click(self, mouse_pos):
        """
        Handle a mouse click on the toolbar.
        Returns a string token ('tiles', 'action_save', 'toolbar_toggle', …)
        or None if nothing was hit.
        """
        # The toggle tab is always clickable, even when hidden
        if self._toggle_rect().collidepoint(mouse_pos):
            self.visible = not self.visible
            return 'toolbar_toggle'

        if not self.visible:
            return None

        # Check tool buttons
        tool_start_x = self.padding
        for i, tool in enumerate(self.tools):
            tool_rect = pygame.Rect(
                tool_start_x + i * (self.tool_size + self.tool_spacing),
                self.padding, self.tool_size, self.tool_size
            )
            if tool_rect.collidepoint(mouse_pos):
                self.current_tool = tool['id']
                return tool['id']

        # Check action buttons
        action_start_x = self.screen_width - self.padding - len(self.actions) * (self.tool_size + self.tool_spacing)
        for i, action in enumerate(self.actions):
            action_rect = pygame.Rect(
                action_start_x + i * (self.tool_size + self.tool_spacing),
                self.padding, self.tool_size, self.tool_size
            )
            if action_rect.collidepoint(mouse_pos):
                if action['id'] == 'zoom':
                    self.zoom_active = not self.zoom_active
                return f"action_{action['id']}"

        return None

    def draw(self, screen):
        """Draw the full toolbar — background, buttons, toggle tab, and tooltip."""
        if self.visible:
            # Semi-transparent background
            toolbar_bg = pygame.Surface((self.screen_width, self.height), pygame.SRCALPHA)
            toolbar_bg.fill(self.colors['bg_transparent'])
            screen.blit(toolbar_bg, (0, 0))

            # Accent underline
            pygame.draw.line(screen, self.colors['accent'], (0, self.height), (self.screen_width, self.height), 2)

            # Left-side tool buttons
            tool_start_x = self.padding
            for i, tool in enumerate(self.tools):
                tool_x = tool_start_x + i * (self.tool_size + self.tool_spacing)
                self._draw_tool_button(screen, tool, tool_x, self.padding, i,
                                       selected=(tool['id'] == self.current_tool))

            # Right-side action buttons
            action_start_x = self.screen_width - self.padding - len(self.actions) * (self.tool_size + self.tool_spacing)
            for i, action in enumerate(self.actions):
                action_x = action_start_x + i * (self.tool_size + self.tool_spacing)
                self._draw_action_button(screen, action, action_x, self.padding, len(self.tools) + i)

        # Toggle tab is always rendered
        self._draw_toggle_tab(screen)

        # Tooltip for whatever is currently hovered
        if self.hover_tool:
            tool = next(t for t in self.tools if t['id'] == self.hover_tool)
            self._draw_tooltip(screen, tool['tooltip'])
        elif self.hover_action:
            action = next(a for a in self.actions if a['id'] == self.hover_action)
            tip = ('Click to return to normal view' if self.zoom_active else action['tooltip']) \
                if action['id'] == 'zoom' else action['tooltip']
            self._draw_tooltip(screen, tip)

    # -------------------------------------------------------------------------
    # Drawing helpers
    # -------------------------------------------------------------------------

    def _draw_tool_button(self, screen, tool, x, y, index, selected=False):
        """Render a single left-side mode button."""
        button_rect = pygame.Rect(x, y, self.tool_size, self.tool_size)

        # Hover glow — fades in/out via tool_hover_anim
        if self.tool_hover_anim[index] > 0:
            glow_amount = int(self.tool_hover_anim[index] * 255)
            glow_surf = pygame.Surface((self.tool_size + 6, self.tool_size + 6), pygame.SRCALPHA)
            pygame.draw.rect(glow_surf, (*self.colors['accent'], glow_amount // 2),
                             (0, 0, self.tool_size + 6, self.tool_size + 6), border_radius=8)
            screen.blit(glow_surf, (x - 3, y - 3))

        # Background & border vary by state
        if selected:
            bg_color, border_color, border_width = self.colors['tool_hover'], self.colors['tool_selected'], 3
        elif self.hover_tool == tool['id']:
            bg_color, border_color, border_width = self.colors['tool_hover'], self.colors['accent'], 2
        else:
            bg_color, border_color, border_width = self.colors['tool_bg'], self.colors['tool_border'], 1

        pygame.draw.rect(screen, bg_color, button_rect, border_radius=8)
        pygame.draw.rect(screen, border_color, button_rect, border_width, border_radius=8)

        # Icon (sprite if available, procedural fallback otherwise)
        icon = self._get_icon_surface(tool['id'], tool['icon'])
        screen.blit(icon, icon.get_rect(center=(x + self.tool_size // 2, y + self.tool_size // 2 - 5)))

        # Label text below the icon
        label_color = self.colors['accent'] if selected else self.colors['text_dim']
        label = self.font_small.render(tool['label'], True, label_color)
        screen.blit(label, label.get_rect(center=(x + self.tool_size // 2, y + self.tool_size - 8)))

    def _draw_action_button(self, screen, action, x, y, anim_index):
        """Render a single right-side action button."""
        button_rect = pygame.Rect(x, y, self.tool_size, self.tool_size)
        action_color = action.get('color', self.colors['accent'])

        # Hover glow using the action's accent colour
        if self.tool_hover_anim[anim_index] > 0:
            glow_amount = int(self.tool_hover_anim[anim_index] * 255)
            glow_surf = pygame.Surface((self.tool_size + 6, self.tool_size + 6), pygame.SRCALPHA)
            pygame.draw.rect(glow_surf, (*action_color, glow_amount // 2),
                             (0, 0, self.tool_size + 6, self.tool_size + 6), border_radius=8)
            screen.blit(glow_surf, (x - 3, y - 3))

        is_hover  = self.hover_action == action['id']
        is_active = (action['id'] == 'zoom' and self.zoom_active)
        lit = is_hover or is_active

        bg_color     = self.colors['tool_hover'] if lit else self.colors['tool_bg']
        border_color = action_color if lit else self.colors['tool_border']
        border_width = 3 if is_active else (2 if is_hover else 1)

        pygame.draw.rect(screen, bg_color, button_rect, border_radius=8)
        pygame.draw.rect(screen, border_color, button_rect, border_width, border_radius=8)

        icon = self._get_icon_surface(action['id'], action['icon'])
        screen.blit(icon, icon.get_rect(center=(x + self.tool_size // 2, y + self.tool_size // 2 - 5)))

        label_color = action_color if lit else self.colors['text_dim']
        label = self.font_small.render(action['label'], True, label_color)
        screen.blit(label, label.get_rect(center=(x + self.tool_size // 2, y + self.tool_size - 8)))

    def _draw_tooltip(self, screen, text):
        """Render a small tooltip just below the toolbar, centred on the cursor."""
        mouse_x, _ = pygame.mouse.get_pos()
        tooltip_surf = self.font_medium.render(text, True, self.colors['text'])
        tooltip_w = tooltip_surf.get_width() + 20
        tooltip_h = tooltip_surf.get_height() + 10

        # Clamp horizontally so it never overflows the screen
        tooltip_x = max(5, min(mouse_x - tooltip_w // 2, self.screen_width - tooltip_w - 5))
        tooltip_y = self.height + 10

        tooltip_rect = pygame.Rect(tooltip_x, tooltip_y, tooltip_w, tooltip_h)
        pygame.draw.rect(screen, self.colors['bg'], tooltip_rect, border_radius=5)
        pygame.draw.rect(screen, self.colors['accent'], tooltip_rect, 1, border_radius=5)
        screen.blit(tooltip_surf, tooltip_surf.get_rect(center=tooltip_rect.center))

    def _toggle_rect(self):
        """Return the rect for the show/hide tab, straddling the toolbar bottom edge."""
        tx = (self.screen_width - self.tab_w) // 2
        ty = (self.height - self.tab_h // 2) if self.visible else 0
        return pygame.Rect(tx, ty, self.tab_w, self.tab_h)

    def _draw_toggle_tab(self, screen):
        """Render the small ▲/▼ Toolbar tab — always visible regardless of toolbar state."""
        rect   = self._toggle_rect()
        bg     = self.colors['tool_hover'] if self.hover_toggle else self.colors['tool_bg']
        border = self.colors['accent']     if self.hover_toggle else self.colors['tool_border']
        pygame.draw.rect(screen, bg, rect, border_radius=6)
        pygame.draw.rect(screen, border, rect, 1, border_radius=6)

        arrow = '▲' if self.visible else '▼'
        label = self.font_small.render(f'{arrow} Toolbar', True,
                                       self.colors['accent'] if self.hover_toggle else self.colors['text_dim'])
        screen.blit(label, label.get_rect(center=rect.center))

    # -------------------------------------------------------------------------
    # Sprite loading
    # -------------------------------------------------------------------------

    def _load_sprites(self):
        """
        Try to load a PNG sprite from assets/ui/toolbar/<id>.png for every button.
        Missing files are silently ignored — the procedural icon is used as fallback.
        Sprites are scaled proportionally to fit the button's icon area.
        """
        icon_size = self.tool_size - 10  # leave room for the label
        all_ids = [t['id'] for t in self.tools] + [a['id'] for a in self.actions]
        for sid in all_ids:
            try:
                img = pygame.image.load(f'assets/ui/toolbar/{sid}.png').convert_alpha()
                iw, ih = img.get_size()
                scale = min(icon_size / iw, icon_size / ih)
                img = pygame.transform.scale(img, (max(1, int(iw * scale)), max(1, int(ih * scale))))
                self._sprites[sid] = img
            except Exception:
                pass

    def _get_icon_surface(self, tool_id, fallback_fn):
        """Return the loaded sprite, or call the procedural fallback if not loaded."""
        return self._sprites.get(tool_id) or fallback_fn()

    # -------------------------------------------------------------------------
    # Procedural icon builders
    # -------------------------------------------------------------------------

    def _create_tile_icon(self):
        """3×3 grid of earthy-coloured squares."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        colors = [(139, 69, 19), (160, 82, 45), (101, 67, 33)]
        for i in range(3):
            for j in range(3):
                x, y = 2 + i * 10, 2 + j * 10
                pygame.draw.rect(surf, colors[(i + j) % 3], (x, y, 8, 8))
                pygame.draw.rect(surf, (80, 50, 20), (x, y, 8, 8), 1)
        return surf

    def _create_object_icon(self):
        """Simple tree: brown trunk + green foliage circle."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.draw.rect(surf, (101, 67, 33), (13, 18, 6, 12))
        pygame.gfxdraw.filled_circle(surf, 16, 12, 8, (34, 139, 34))
        pygame.gfxdraw.aacircle(surf, 16, 12, 8, (20, 100, 20))
        return surf

    def _create_entity_icon(self):
        """Tiny character: skin-toned head + blue body rectangle."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.gfxdraw.filled_circle(surf, 16, 10, 5, (255, 220, 177))
        pygame.gfxdraw.aacircle(surf, 16, 10, 5, (200, 160, 120))
        pygame.draw.rect(surf, (100, 100, 255), (11, 15, 10, 12))
        pygame.draw.rect(surf, (80, 80, 200), (11, 15, 10, 12), 1)
        return surf

    def _create_item_icon(self):
        """Gold coin with a brighter inner circle."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.gfxdraw.filled_circle(surf, 16, 16, 10, (255, 215, 0))
        pygame.gfxdraw.aacircle(surf, 16, 16, 10, (200, 170, 0))
        pygame.gfxdraw.filled_circle(surf, 16, 16, 6, (255, 235, 100))
        return surf

    def _create_settings_icon(self):
        """Gear shape — radiating lines from center + inner circle."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        center, outer_r, inner_r, teeth = 16, 10, 5, 8
        for i in range(teeth):
            # Draw a short thick line at each tooth angle
            v = pygame.math.Vector2(1, 0).rotate(i * (360 / teeth))
            x1 = center + outer_r * 0.7 * v.x
            y1 = center + outer_r * 0.7 * v.y
            x2 = center + outer_r * v.x
            y2 = center + outer_r * v.y
            pygame.draw.line(surf, (180, 180, 200), (x1, y1), (x2, y2), 3)
        pygame.gfxdraw.filled_circle(surf, center, center, inner_r, (100, 100, 120))
        pygame.gfxdraw.aacircle(surf, center, center, inner_r, (150, 150, 170))
        return surf

    def _create_weather_icon(self):
        """Cloud outline (overlapping circles) with three rain drops."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.gfxdraw.filled_circle(surf, 12, 10, 6, (200, 200, 220))
        pygame.gfxdraw.filled_circle(surf, 18, 10, 6, (200, 200, 220))
        pygame.gfxdraw.filled_circle(surf, 15, 8,  5, (200, 200, 220))
        for rx in [10, 16, 22]:
            pygame.draw.line(surf, (100, 150, 255), (rx, 18), (rx, 26), 2)
        return surf

    def _create_zoom_icon(self):
        """Magnifier with outward arrows inside the lens to hint 'fit to view'."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.gfxdraw.aacircle(surf, 14, 14, 8, (100, 200, 255))
        pygame.gfxdraw.aacircle(surf, 14, 14, 7, (100, 200, 255))
        pygame.draw.line(surf, (100, 200, 255), (20, 20), (27, 27), 3)  # handle
        # Small outward directional lines inside the lens
        pygame.draw.line(surf, (200, 240, 255), (10, 14), (7,  14), 2)
        pygame.draw.line(surf, (200, 240, 255), (18, 14), (21, 14), 2)
        pygame.draw.line(surf, (200, 240, 255), (14, 10), (14, 7),  2)
        pygame.draw.line(surf, (200, 240, 255), (14, 18), (14, 21), 2)
        return surf

    def _create_play_icon(self):
        """Solid green right-pointing triangle."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pts = [(10, 8), (10, 24), (24, 16)]
        pygame.gfxdraw.filled_polygon(surf, pts, (100, 255, 100))
        pygame.gfxdraw.aapolygon(surf, pts, (80, 200, 80))
        return surf

    def _create_save_icon(self):
        """Simplified floppy disk shape."""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.draw.rect(surf, (255, 215, 0),   (8,  6, 16, 20))          # body
        pygame.draw.rect(surf, (200, 170, 0),   (8,  6, 16, 20), 2)       # body border
        pygame.draw.rect(surf, (40, 40, 60),    (10, 8, 12,  6))          # label window
        pygame.draw.rect(surf, (200, 170, 0),   (12, 20, 8,  6))          # metal tab
        return surf