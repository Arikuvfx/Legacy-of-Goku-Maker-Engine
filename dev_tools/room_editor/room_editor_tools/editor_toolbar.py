import pygame
import pygame.gfxdraw


class EditorToolbar:
    """
    Top toolbar for room editor with tool selection and quick actions
    """

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height

        # Toolbar dimensions
        self.height = 80
        self.padding = 10
        self.tool_size = 60
        self.tool_spacing = 10

        # Fonts
        self.font_small = pygame.font.Font(None, 16)
        self.font_medium = pygame.font.Font(None, 20)

        # Colors
        self.colors = {
            'bg': (25, 25, 40),
            'bg_transparent': (25, 25, 40, 200),
            'tool_bg': (35, 35, 55),
            'tool_hover': (45, 45, 65),
            'tool_selected': (255, 215, 0),
            'tool_border': (60, 60, 80),
            'accent': (255, 215, 0),
            'text': (255, 255, 255),
            'text_dim': (180, 180, 200),
            'success': (100, 255, 100),
            'danger': (255, 100, 100)
        }

        # Current tool
        self.current_tool = 'tiles'  # 'tiles', 'objects', 'entities', 'items', 'settings', 'weather'

        # Tool definitions
        self.tools = [
            {
                'id': 'tiles',
                'label': 'Tiles',
                'icon': self._create_tile_icon,
                'tooltip': 'Edit terrain tiles (F2)'
            },
            {
                'id': 'objects',
                'label': 'Objects',
                'icon': self._create_object_icon,
                'tooltip': 'Place objects and decorations'
            },
            {
                'id': 'entities',
                'label': 'Entities',
                'icon': self._create_entity_icon,
                'tooltip': 'Add NPCs and enemies'
            },
            {
                'id': 'items',
                'label': 'Items',
                'icon': self._create_item_icon,
                'tooltip': 'Place collectible items'
            },
            {
                'id': 'settings',
                'label': 'Room',
                'icon': self._create_settings_icon,
                'tooltip': 'Room properties'
            },
            {
                'id': 'weather',
                'label': 'Weather',
                'icon': self._create_weather_icon,
                'tooltip': 'Add weather effects'
            }
        ]

        # Action buttons (right side)
        self.actions = [
            {
                'id': 'test',
                'label': 'Test',
                'icon': self._create_play_icon,
                'tooltip': 'Test room (F5)',
                'color': self.colors['success']
            },
            {
                'id': 'save',
                'label': 'Save',
                'icon': self._create_save_icon,
                'tooltip': 'Save room (Ctrl+S)',
                'color': self.colors['accent']
            }
        ]

        # Mouse interaction
        self.hover_tool = None
        self.hover_action = None

        # Animation
        self.anim_timer = 0
        self.tool_hover_anim = [0.0] * (len(self.tools) + len(self.actions))

    def update(self, dt, mouse_pos):
        """Update toolbar state"""
        self.anim_timer += dt

        # Check tool hovers
        self.hover_tool = None
        self.hover_action = None

        tool_start_x = self.padding
        for i, tool in enumerate(self.tools):
            tool_x = tool_start_x + i * (self.tool_size + self.tool_spacing)
            tool_rect = pygame.Rect(tool_x, self.padding, self.tool_size, self.tool_size)

            if tool_rect.collidepoint(mouse_pos):
                self.hover_tool = tool['id']
                self.tool_hover_anim[i] = min(1.0, self.tool_hover_anim[i] + dt * 8)
            else:
                self.tool_hover_anim[i] = max(0.0, self.tool_hover_anim[i] - dt * 8)

        # Check action button hovers
        action_start_x = self.screen_width - self.padding - (len(self.actions) * (self.tool_size + self.tool_spacing))
        for i, action in enumerate(self.actions):
            action_x = action_start_x + i * (self.tool_size + self.tool_spacing)
            action_rect = pygame.Rect(action_x, self.padding, self.tool_size, self.tool_size)

            anim_index = len(self.tools) + i
            if action_rect.collidepoint(mouse_pos):
                self.hover_action = action['id']
                self.tool_hover_anim[anim_index] = min(1.0, self.tool_hover_anim[anim_index] + dt * 8)
            else:
                self.tool_hover_anim[anim_index] = max(0.0, self.tool_hover_anim[anim_index] - dt * 8)

    def handle_click(self, mouse_pos):
        """Handle mouse clicks on toolbar"""
        # Check tool clicks
        tool_start_x = self.padding
        for i, tool in enumerate(self.tools):
            tool_x = tool_start_x + i * (self.tool_size + self.tool_spacing)
            tool_rect = pygame.Rect(tool_x, self.padding, self.tool_size, self.tool_size)

            if tool_rect.collidepoint(mouse_pos):
                self.current_tool = tool['id']
                return tool['id']

        # Check action clicks
        action_start_x = self.screen_width - self.padding - (len(self.actions) * (self.tool_size + self.tool_spacing))
        for i, action in enumerate(self.actions):
            action_x = action_start_x + i * (self.tool_size + self.tool_spacing)
            action_rect = pygame.Rect(action_x, self.padding, self.tool_size, self.tool_size)

            if action_rect.collidepoint(mouse_pos):
                return f"action_{action['id']}"

        return None

    def draw(self, screen):
        """Draw the toolbar"""
        # Semi-transparent background
        toolbar_bg = pygame.Surface((self.screen_width, self.height), pygame.SRCALPHA)
        toolbar_bg.fill(self.colors['bg_transparent'])
        screen.blit(toolbar_bg, (0, 0))

        # Bottom border
        pygame.draw.line(screen, self.colors['accent'],
                         (0, self.height), (self.screen_width, self.height), 2)

        # Draw tools
        tool_start_x = self.padding
        for i, tool in enumerate(self.tools):
            tool_x = tool_start_x + i * (self.tool_size + self.tool_spacing)
            self._draw_tool_button(screen, tool, tool_x, self.padding, i,
                                   selected=(tool['id'] == self.current_tool))

        # Draw action buttons
        action_start_x = self.screen_width - self.padding - (len(self.actions) * (self.tool_size + self.tool_spacing))
        for i, action in enumerate(self.actions):
            action_x = action_start_x + i * (self.tool_size + self.tool_spacing)
            anim_index = len(self.tools) + i
            self._draw_action_button(screen, action, action_x, self.padding, anim_index)

        # Draw tooltip
        if self.hover_tool:
            tool = next(t for t in self.tools if t['id'] == self.hover_tool)
            self._draw_tooltip(screen, tool['tooltip'])
        elif self.hover_action:
            action = next(a for a in self.actions if a['id'] == self.hover_action)
            self._draw_tooltip(screen, action['tooltip'])

    def _draw_tool_button(self, screen, tool, x, y, index, selected=False):
        """Draw a tool button"""
        button_rect = pygame.Rect(x, y, self.tool_size, self.tool_size)

        # Hover glow
        if self.tool_hover_anim[index] > 0:
            glow_amount = int(self.tool_hover_anim[index] * 255)
            glow_surf = pygame.Surface((self.tool_size + 6, self.tool_size + 6), pygame.SRCALPHA)
            pygame.draw.rect(glow_surf, (*self.colors['accent'], glow_amount // 2),
                             (0, 0, self.tool_size + 6, self.tool_size + 6), border_radius=8)
            screen.blit(glow_surf, (x - 3, y - 3))

        # Button background
        if selected:
            bg_color = self.colors['tool_hover']
            border_color = self.colors['tool_selected']
            border_width = 3
        elif self.hover_tool == tool['id']:
            bg_color = self.colors['tool_hover']
            border_color = self.colors['accent']
            border_width = 2
        else:
            bg_color = self.colors['tool_bg']
            border_color = self.colors['tool_border']
            border_width = 1

        pygame.draw.rect(screen, bg_color, button_rect, border_radius=8)
        pygame.draw.rect(screen, border_color, button_rect, border_width, border_radius=8)

        # Draw icon
        icon_surface = tool['icon']()
        icon_rect = icon_surface.get_rect(center=(x + self.tool_size // 2, y + self.tool_size // 2 - 5))
        screen.blit(icon_surface, icon_rect)

        # Label
        label_color = self.colors['accent'] if selected else self.colors['text_dim']
        label = self.font_small.render(tool['label'], True, label_color)
        label_rect = label.get_rect(center=(x + self.tool_size // 2, y + self.tool_size - 8))
        screen.blit(label, label_rect)

    def _draw_action_button(self, screen, action, x, y, anim_index):
        """Draw an action button"""
        button_rect = pygame.Rect(x, y, self.tool_size, self.tool_size)

        # Hover glow
        if self.tool_hover_anim[anim_index] > 0:
            glow_amount = int(self.tool_hover_anim[anim_index] * 255)
            glow_surf = pygame.Surface((self.tool_size + 6, self.tool_size + 6), pygame.SRCALPHA)
            glow_color = action.get('color', self.colors['accent'])
            pygame.draw.rect(glow_surf, (*glow_color, glow_amount // 2),
                             (0, 0, self.tool_size + 6, self.tool_size + 6), border_radius=8)
            screen.blit(glow_surf, (x - 3, y - 3))

        # Button background
        is_hover = self.hover_action == action['id']
        bg_color = self.colors['tool_hover'] if is_hover else self.colors['tool_bg']
        border_color = action.get('color', self.colors['accent']) if is_hover else self.colors['tool_border']
        border_width = 2 if is_hover else 1

        pygame.draw.rect(screen, bg_color, button_rect, border_radius=8)
        pygame.draw.rect(screen, border_color, button_rect, border_width, border_radius=8)

        # Draw icon
        icon_surface = action['icon']()
        icon_rect = icon_surface.get_rect(center=(x + self.tool_size // 2, y + self.tool_size // 2 - 5))
        screen.blit(icon_surface, icon_rect)

        # Label
        label_color = action.get('color', self.colors['accent']) if is_hover else self.colors['text_dim']
        label = self.font_small.render(action['label'], True, label_color)
        label_rect = label.get_rect(center=(x + self.tool_size // 2, y + self.tool_size - 8))
        screen.blit(label, label_rect)

    def _draw_tooltip(self, screen, text):
        """Draw tooltip near mouse"""
        mouse_x, mouse_y = pygame.mouse.get_pos()

        tooltip_surf = self.font_medium.render(text, True, self.colors['text'])
        tooltip_width = tooltip_surf.get_width() + 20
        tooltip_height = tooltip_surf.get_height() + 10

        # Position tooltip
        tooltip_x = mouse_x - tooltip_width // 2
        tooltip_y = self.height + 10

        # Clamp to screen
        tooltip_x = max(5, min(tooltip_x, self.screen_width - tooltip_width - 5))

        # Background
        tooltip_rect = pygame.Rect(tooltip_x, tooltip_y, tooltip_width, tooltip_height)
        pygame.draw.rect(screen, self.colors['bg'], tooltip_rect, border_radius=5)
        pygame.draw.rect(screen, self.colors['accent'], tooltip_rect, 1, border_radius=5)

        # Text
        text_rect = tooltip_surf.get_rect(center=tooltip_rect.center)
        screen.blit(tooltip_surf, text_rect)

    # Icon creation methods
    def _create_tile_icon(self):
        """Create tiles icon"""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        # Grid of squares
        colors = [(139, 69, 19), (160, 82, 45), (101, 67, 33)]
        for i in range(3):
            for j in range(3):
                x = 2 + i * 10
                y = 2 + j * 10
                pygame.draw.rect(surf, colors[(i + j) % 3], (x, y, 8, 8))
                pygame.draw.rect(surf, (80, 50, 20), (x, y, 8, 8), 1)
        return surf

    def _create_object_icon(self):
        """Create objects icon"""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        # Simple tree
        pygame.draw.rect(surf, (101, 67, 33), (13, 18, 6, 12))  # Trunk
        pygame.gfxdraw.filled_circle(surf, 16, 12, 8, (34, 139, 34))  # Foliage
        pygame.gfxdraw.aacircle(surf, 16, 12, 8, (20, 100, 20))
        return surf

    def _create_entity_icon(self):
        """Create entities icon"""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        # Simple character
        pygame.gfxdraw.filled_circle(surf, 16, 10, 5, (255, 220, 177))  # Head
        pygame.gfxdraw.aacircle(surf, 16, 10, 5, (200, 160, 120))
        pygame.draw.rect(surf, (100, 100, 255), (11, 15, 10, 12))  # Body
        pygame.draw.rect(surf, (80, 80, 200), (11, 15, 10, 12), 1)
        return surf

    def _create_item_icon(self):
        """Create items icon"""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        # Coin/collectible
        pygame.gfxdraw.filled_circle(surf, 16, 16, 10, (255, 215, 0))
        pygame.gfxdraw.aacircle(surf, 16, 16, 10, (200, 170, 0))
        pygame.gfxdraw.filled_circle(surf, 16, 16, 6, (255, 235, 100))
        return surf

    def _create_settings_icon(self):
        """Create settings icon"""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        # Gear
        center = 16
        outer_r = 10
        inner_r = 5
        teeth = 8

        for i in range(teeth):
            angle = i * (360 / teeth)
            angle_rad = angle * 3.14159 / 180
            x1 = center + outer_r * 0.7 * pygame.math.Vector2(1, 0).rotate(angle).x
            y1 = center + outer_r * 0.7 * pygame.math.Vector2(1, 0).rotate(angle).y
            x2 = center + outer_r * pygame.math.Vector2(1, 0).rotate(angle).x
            y2 = center + outer_r * pygame.math.Vector2(1, 0).rotate(angle).y
            pygame.draw.line(surf, (180, 180, 200), (x1, y1), (x2, y2), 3)

        pygame.gfxdraw.filled_circle(surf, center, center, inner_r, (100, 100, 120))
        pygame.gfxdraw.aacircle(surf, center, center, inner_r, (150, 150, 170))
        return surf

    def _create_weather_icon(self):
        """Create weather icon"""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        # Cloud with rain
        pygame.gfxdraw.filled_circle(surf, 12, 10, 6, (200, 200, 220))
        pygame.gfxdraw.filled_circle(surf, 18, 10, 6, (200, 200, 220))
        pygame.gfxdraw.filled_circle(surf, 15, 8, 5, (200, 200, 220))
        # Rain drops
        for x in [10, 16, 22]:
            pygame.draw.line(surf, (100, 150, 255), (x, 18), (x, 26), 2)
        return surf

    def _create_play_icon(self):
        """Create play/test icon"""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        # Play triangle
        points = [(10, 8), (10, 24), (24, 16)]
        pygame.gfxdraw.filled_polygon(surf, points, (100, 255, 100))
        pygame.gfxdraw.aapolygon(surf, points, (80, 200, 80))
        return surf

    def _create_save_icon(self):
        """Create save icon"""
        surf = pygame.Surface((32, 32), pygame.SRCALPHA)
        # Floppy disk
        pygame.draw.rect(surf, (255, 215, 0), (8, 6, 16, 20))
        pygame.draw.rect(surf, (200, 170, 0), (8, 6, 16, 20), 2)
        pygame.draw.rect(surf, (40, 40, 60), (10, 8, 12, 6))  # Label area
        pygame.draw.rect(surf, (200, 170, 0), (12, 20, 8, 6))  # Metal part
        return surf