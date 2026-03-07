import pygame
import os
import sys
import math


class DevMenu:
    """
    Fullscreen sprite-based developer menu with custom font and background
    """

    def __init__(self, game_config, screen_width, screen_height, sound_manager=None):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False
        self.config = game_config
        self.sound_manager = sound_manager
        self.previous_context = None  # Store previous music context

        # Build asset paths
        if getattr(sys, 'frozen', False):
            application_path = os.path.dirname(sys.executable)
        else:
            application_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        self.assets_path = os.path.join(application_path, "assets", "ui", "dev_menu")
        self.icons_path = os.path.join(self.assets_path, "icons")

        # Load sprites and fonts
        self._load_assets()

        # Menu state
        self.current_menu = 'main'  # main, config, xp_config, room_editor
        self.selected_index = -1  # Start with nothing selected (mouse or keyboard will set this)
        self.hover_index = -1  # Track which item mouse is hovering over

        # Main menu options - ADDED SPRITE EDITOR
        self.main_options = [
            {'id': 'room_editor', 'label': 'ROOM EDITOR', 'icon': 'room'},
            {'id': 'sprite_editor', 'label': 'SPRITE EDITOR', 'icon': 'sprite'},
            {'id': 'config', 'label': 'CONFIGURATION', 'icon': 'config'},
            {'id': 'close', 'label': 'CLOSE MENU', 'icon': 'close'}
        ]

        # Config menu options
        self.config_options = [
            {'id': 'xp_config', 'label': 'XP CONFIGURATION', 'icon': 'xp'},
            {'id': 'transformation_config', 'label': 'TRANSFORMATION', 'icon': 'transform'},
            {'id': 'back', 'label': 'BACK', 'icon': 'back'}
        ]

        # XP Config options
        self.xp_config_options = [
            {'id': 'max_level', 'label': 'Max Level', 'value_key': 'max_level'},
            {'id': 'base_xp', 'label': 'Base XP Required', 'value_key': 'base_xp_requirement'},
            {'id': 'xp_scaling', 'label': 'XP Scaling Factor', 'value_key': 'xp_scaling_factor'},
            {'id': 'stat_points', 'label': 'Stat Points/Level', 'value_key': 'stat_points_per_level'},
            {'id': 'back', 'label': 'BACK', 'icon': 'back'}
        ]

        # Text input state
        self.editing_text = False
        self.text_input = ""
        self.editing_field = None

        # Animation
        self.anim_timer = 0
        self.cursor_blink = 0
        self.icon_bob_offset = [0.0] * 10  # Bob offsets for each menu option

        # Mouse interaction - store clickable rectangles
        self.clickable_rects = []

    def _load_assets(self):
        """Load all sprite assets and fonts"""
        # Try to load custom font, fallback to default
        font_path = os.path.join(self.assets_path, "dev_font.ttf")
        if os.path.exists(font_path):
            self.font_title = pygame.font.Font(font_path, 48)
            self.font_large = pygame.font.Font(font_path, 32)
            self.font_medium = pygame.font.Font(font_path, 24)
            self.font_small = pygame.font.Font(font_path, 18)
        else:
            # Fallback to system font with a retro feel
            self.font_title = pygame.font.Font(None, 48)
            self.font_large = pygame.font.Font(None, 32)
            self.font_medium = pygame.font.Font(None, 24)
            self.font_small = pygame.font.Font(None, 18)

        # Try to load background sprite
        bg_path = os.path.join(self.assets_path, "background.png")
        if os.path.exists(bg_path):
            self.background = pygame.image.load(bg_path).convert()
            self.background = pygame.transform.scale(self.background, (self.screen_width, self.screen_height))
            # Create a second copy for seamless looping
            self.background_copy = self.background.copy()
            self.bg_scroll_x = 0
            self.bg_scroll_speed = 50  # pixels per second
        else:
            # Create a default gradient background
            self.background = self._create_gradient_background()
            self.background_copy = None
            self.bg_scroll_x = 0
            self.bg_scroll_speed = 0

        # Load or create UI elements
        self._load_ui_elements()

    def _create_gradient_background(self):
        """Create a default gradient background if sprite not found"""
        bg = pygame.Surface((self.screen_width, self.screen_height))

        # Create dark blue to black gradient
        for y in range(self.screen_height):
            progress = y / self.screen_height
            r = int(10 * (1 - progress))
            g = int(20 * (1 - progress))
            b = int(40 * (1 - progress))
            pygame.draw.line(bg, (r, g, b), (0, y), (self.screen_width, y))

        # Add grid pattern
        grid_color = (20, 30, 50)
        for x in range(0, self.screen_width, 40):
            pygame.draw.line(bg, grid_color, (x, 0), (x, self.screen_height), 1)
        for y in range(0, self.screen_height, 40):
            pygame.draw.line(bg, grid_color, (0, y), (self.screen_width, y), 1)

        return bg

    def _load_ui_elements(self):
        """Load or create UI element sprites"""
        # Selection box
        box_path = os.path.join(self.assets_path, "selection_box.png")
        if os.path.exists(box_path):
            self.selection_box = pygame.image.load(box_path).convert_alpha()
            # Create wider versions for longer text
            self.selection_box_medium = pygame.transform.scale(self.selection_box, (500, 60))
            self.selection_box_wide = pygame.transform.scale(self.selection_box, (550, 60))
            self.selection_box_xwide = pygame.transform.scale(self.selection_box, (600, 60))
        else:
            # Create default selection boxes of different widths
            self.selection_box = self._create_selection_box(450)
            self.selection_box_medium = self._create_selection_box(500)
            self.selection_box_wide = self._create_selection_box(550)
            self.selection_box_xwide = self._create_selection_box(600)

        # Load icons from PNG files
        self._load_icons()

    def _create_selection_box(self, width):
        """Create a default selection box sprite with specified width"""
        box = pygame.Surface((width, 60), pygame.SRCALPHA)

        # Outer glow
        for i in range(5):
            alpha = 50 - i * 10
            pygame.draw.rect(box, (255, 215, 0, alpha), (i, i, width - i * 2, 60 - i * 2), 2)

        # Main box
        pygame.draw.rect(box, (255, 215, 0, 100), (5, 5, width - 10, 50))
        pygame.draw.rect(box, (255, 215, 0, 255), (5, 5, width - 10, 50), 2)

        return box

    def _load_icons(self):
        """Load icon PNG sprites from the icons directory"""
        self.icons = {}

        # All icon types used in the menus
        icon_types = ['room', 'sprite', 'config', 'close', 'xp', 'transform', 'back']

        # Fallback colors if PNG files are not found
        icon_colors = {
            'room': (100, 255, 100),
            'sprite': (100, 200, 255),
            'config': (100, 100, 255),
            'close': (255, 50, 50),
            'xp': (255, 215, 0),
            'transform': (255, 165, 0),
            'back': (150, 150, 150)
        }

        # Create icons directory if it doesn't exist
        if not os.path.exists(self.icons_path):
            try:
                os.makedirs(self.icons_path, exist_ok=True)
            except Exception:
                pass

        # Load or create each icon
        for icon_type in icon_types:
            # Try to load PNG sprite (support multiple naming conventions)
            png_filenames = [
                f"{icon_type}.png",
                f"icon_{icon_type}.png",
                f"{icon_type}_icon.png"
            ]

            icon_loaded = False

            for filename in png_filenames:
                icon_path = os.path.join(self.icons_path, filename)
                if os.path.exists(icon_path):
                    try:
                        icon = pygame.image.load(icon_path).convert_alpha()
                        # Scale to standard size (32x32)
                        self.icons[icon_type] = pygame.transform.scale(icon, (32, 32))
                        icon_loaded = True
                        break
                    except Exception:
                        pass

            # Create simple colored circle as fallback
            if not icon_loaded:
                self.icons[icon_type] = self._create_icon(icon_colors.get(icon_type, (255, 255, 255)))

                # Save the fallback icon as PNG for future use
                try:
                    fallback_path = os.path.join(self.icons_path, f"{icon_type}_fallback.png")
                    pygame.image.save(self.icons[icon_type], fallback_path)
                except Exception:
                    pass

    def _create_icon(self, color):
        """Create a simple icon sprite"""
        icon = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.draw.circle(icon, color, (16, 16), 14)
        pygame.draw.circle(icon, (255, 255, 255), (16, 16), 14, 2)
        return icon

    def toggle(self):
        """Toggle menu visibility"""
        self.active = not self.active
        if self.active:
            self.current_menu = 'main'
            self.selected_index = -1  # Start with nothing selected (only mouse hover will highlight)
            self.hover_index = -1
            self.editing_text = False
            self.icon_bob_offset = [0.0] * 10  # Reset bob animation

            # Switch to menu music immediately (stop other music)
            if self.sound_manager:
                self.previous_context = self.sound_manager.get_current_context()
                self.sound_manager.set_context_immediate('menu')
        else:
            # Restore previous music context
            if self.sound_manager:
                if self.previous_context:
                    self.sound_manager.set_context(self.previous_context, force=True)
                else:
                    self.sound_manager.set_context('exploration', force=True)

    def handle_input(self, event):
        """Handle input events"""
        if not self.active:
            return None

        # Handle text input mode
        if self.editing_text:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    try:
                        value = float(self.text_input)
                        setattr(self.config, self.editing_field, value)
                    except:
                        pass
                    self.editing_text = False
                    self.text_input = ""
                    self.editing_field = None
                elif event.key == pygame.K_ESCAPE:
                    self.editing_text = False
                    self.text_input = ""
                    self.editing_field = None
                elif event.key == pygame.K_BACKSPACE:
                    self.text_input = self.text_input[:-1]
                else:
                    if len(self.text_input) < 20 and event.unicode.isprintable():
                        self.text_input += event.unicode
            return None

        # Handle mouse clicks
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mouse_pos = event.pos
            for clickable in self.clickable_rects:
                if clickable['rect'].collidepoint(mouse_pos):
                    # Click on menu item - select and execute
                    self.selected_index = clickable['index']
                    return self._handle_selection()

        # Handle mouse motion for hover effects
        if event.type == pygame.MOUSEMOTION:
            mouse_pos = event.pos
            self.hover_index = -1
            for clickable in self.clickable_rects:
                if clickable['rect'].collidepoint(mouse_pos):
                    self.hover_index = clickable['index']
                    break

        # Handle menu navigation (keyboard)
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if self.current_menu == 'main':
                    self.active = False
                else:
                    self._go_back()
                return None

            options = self._get_current_options()

            if event.key == pygame.K_UP or event.key == pygame.K_w:
                # If nothing selected yet, start at the last item
                if self.selected_index == -1:
                    self.selected_index = len(options) - 1
                else:
                    self.selected_index = (self.selected_index - 1) % len(options)
            elif event.key == pygame.K_DOWN or event.key == pygame.K_s:
                # If nothing selected yet, start at the first item
                if self.selected_index == -1:
                    self.selected_index = 0
                else:
                    self.selected_index = (self.selected_index + 1) % len(options)
            elif event.key == pygame.K_RETURN or event.key == pygame.K_SPACE:
                # Only handle selection if something is actually selected
                if self.selected_index != -1:
                    return self._handle_selection()

        return None

    def _get_current_options(self):
        """Get options for current menu"""
        if self.current_menu == 'main':
            return self.main_options
        elif self.current_menu == 'config':
            return self.config_options
        elif self.current_menu == 'xp_config':
            return self.xp_config_options
        return []

    def _handle_selection(self):
        """Handle menu item selection"""
        options = self._get_current_options()
        if self.selected_index >= len(options) or self.selected_index < 0:
            return None

        selected = options[self.selected_index]
        option_id = selected['id']

        if self.current_menu == 'main':
            if option_id == 'room_editor':
                return 'open_room_editor'
            elif option_id == 'sprite_editor':
                return 'open_sprite_editor'
            elif option_id == 'config':
                self.current_menu = 'config'
                self.selected_index = -1  # Reset to no selection
                self.hover_index = -1
            elif option_id == 'close':
                self.active = False

        elif self.current_menu == 'config':
            if option_id == 'xp_config':
                self.current_menu = 'xp_config'
                self.selected_index = -1  # Reset to no selection
                self.hover_index = -1
            elif option_id == 'transformation_config':
                pass
            elif option_id == 'back':
                self._go_back()

        elif self.current_menu == 'xp_config':
            if option_id == 'back':
                self._go_back()
            elif 'value_key' in selected:
                self.editing_text = True
                self.editing_field = selected['value_key']
                self.text_input = str(getattr(self.config, self.editing_field, ''))

        return None

    def _go_back(self):
        """Navigate back to previous menu"""
        if self.current_menu == 'xp_config':
            self.current_menu = 'config'
        elif self.current_menu == 'config':
            self.current_menu = 'main'
        self.selected_index = -1  # Reset to no selection
        self.hover_index = -1

    def update(self, dt):
        """Update animations"""
        if not self.active:
            return

        self.anim_timer += dt
        self.cursor_blink += dt

        # Update icon bobbing animation for selected or hovered item
        options = self._get_current_options()
        for i in range(len(options)):
            if i == self.selected_index or i == self.hover_index:
                # Bob selected/hovered icon with smooth sine wave
                self.icon_bob_offset[i] = math.sin(self.anim_timer * 3) * 5
            else:
                # Reset unselected icons
                self.icon_bob_offset[i] = self.icon_bob_offset[i] * 0.9  # Smooth damping

        # Update background scroll position
        if self.background_copy is not None:
            self.bg_scroll_x -= self.bg_scroll_speed * dt
            if self.bg_scroll_x <= -self.screen_width:
                self.bg_scroll_x = 0

    def draw(self, screen):
        """Draw the dev menu"""
        if not self.active:
            return

        # Clear clickable rects at start of each frame
        self.clickable_rects = []

        # Draw semi-transparent overlay
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 200))
        screen.blit(overlay, (0, 0))

        # Draw scrolling background
        if self.background_copy is not None:
            screen.blit(self.background, (int(self.bg_scroll_x), 0))
            screen.blit(self.background_copy, (int(self.bg_scroll_x + self.screen_width), 0))
        else:
            screen.blit(self.background, (0, 0))

        # Draw title
        title_text = self._get_menu_title()
        title_surf = self.font_title.render(title_text, True, (255, 215, 0))
        title_shadow = self.font_title.render(title_text, True, (0, 0, 0))

        title_x = (self.screen_width - title_surf.get_width()) // 2
        title_y = 40
        screen.blit(title_shadow, (title_x + 3, title_y + 3))
        screen.blit(title_surf, (title_x, title_y))

        # Draw menu options
        options = self._get_current_options()
        start_y = 150
        spacing = 80

        for i, option in enumerate(options):
            y_pos = start_y + i * spacing
            is_selected = (i == self.selected_index)
            is_hovered = (i == self.hover_index)

            # Highlight if selected OR hovered
            is_highlighted = is_selected or is_hovered

            # Draw selection box if highlighted
            if is_highlighted:
                # Determine which selection box to use based on label length
                label = option['label']
                if 'value_key' in option:
                    value = getattr(self.config, option['value_key'], '?')
                    label = f"{label}: {value}"

                # Choose box width based on text content
                test_surface = self.font_large.render(label, True, (255, 255, 255))
                text_width = test_surface.get_width()
                text_padding = 100  # Extra space for icon and margins

                # Adjusted thresholds for wider boxes
                if text_width + text_padding > 550:
                    box_width = 600
                    selection_box = self.selection_box_xwide
                elif text_width + text_padding > 500:
                    box_width = 550
                    selection_box = self.selection_box_wide
                elif text_width + text_padding > 450:
                    box_width = 500
                    selection_box = self.selection_box_medium
                else:
                    box_width = 450
                    selection_box = self.selection_box

                box_x = (self.screen_width - box_width) // 2
                pulse = abs(int((self.anim_timer * 2) % 2 - 1) * 10)
                screen.blit(selection_box, (box_x - pulse, y_pos - pulse // 2))

                # Store clickable rectangle for mouse interaction
                clickable_rect = pygame.Rect(box_x, y_pos, box_width, 60)
                self.clickable_rects.append({'rect': clickable_rect, 'index': i})
            else:
                # Even non-highlighted items should be clickable
                # Calculate approximate box size
                box_width = 450
                box_x = (self.screen_width - box_width) // 2
                clickable_rect = pygame.Rect(box_x, y_pos, box_width, 60)
                self.clickable_rects.append({'rect': clickable_rect, 'index': i})

            # Draw icon with bobbing animation for highlighted item
            if 'icon' in option and option['icon'] in self.icons:
                # Use appropriate x position based on selected box width
                if is_highlighted:
                    label = option['label']
                    if 'value_key' in option:
                        value = getattr(self.config, option['value_key'], '?')
                        label = f"{label}: {value}"

                    test_surface = self.font_large.render(label, True, (255, 255, 255))
                    text_width = test_surface.get_width()
                    text_padding = 100

                    if text_width + text_padding > 550:
                        box_width = 600
                    elif text_width + text_padding > 500:
                        box_width = 550
                    elif text_width + text_padding > 450:
                        box_width = 500
                    else:
                        box_width = 450
                else:
                    box_width = 450

                icon_x = (self.screen_width - box_width) // 2 + 20
                icon_y = y_pos + 10 + int(self.icon_bob_offset[i])

                # Add glow effect to highlighted icon
                if is_highlighted:
                    glow_size = 40
                    glow_surface = pygame.Surface((glow_size, glow_size), pygame.SRCALPHA)
                    glow_alpha = int(100 + 50 * math.sin(self.anim_timer * 4))
                    pygame.draw.circle(glow_surface, (255, 215, 0, glow_alpha),
                                       (glow_size // 2, glow_size // 2), glow_size // 2)
                    screen.blit(glow_surface,
                                (icon_x - (glow_size - 32) // 2, icon_y - (glow_size - 32) // 2))

                screen.blit(self.icons[option['icon']], (icon_x, icon_y))

            # Draw label
            label = option['label']

            # Add value if this is a config field
            if 'value_key' in option:
                value = getattr(self.config, option['value_key'], '?')
                label = f"{label}: {value}"

            color = (255, 255, 255) if is_highlighted else (180, 180, 180)
            font = self.font_large if is_highlighted else self.font_medium

            # Render label text
            label_surf = font.render(label, True, color)
            label_shadow = font.render(label, True, (0, 0, 0))

            # Calculate position based on current box width
            if is_highlighted:
                # Recalculate box width for positioning
                test_label = option['label']
                if 'value_key' in option:
                    value = getattr(self.config, option['value_key'], '?')
                    test_label = f"{test_label}: {value}"

                test_surface = self.font_large.render(test_label, True, (255, 255, 255))
                text_width = test_surface.get_width()
                text_padding = 100

                if text_width + text_padding > 550:
                    box_width = 600
                elif text_width + text_padding > 500:
                    box_width = 550
                elif text_width + text_padding > 450:
                    box_width = 500
                else:
                    box_width = 450
            else:
                box_width = 450

            # Calculate text position with proper alignment
            label_x = (self.screen_width - box_width) // 2 + 70
            label_y = y_pos + 15

            # Ensure text doesn't overflow the box
            max_text_width = box_width - 80  # Account for icon and margins
            if label_surf.get_width() > max_text_width:
                # If text is too long, scale it down
                scale_factor = max_text_width / label_surf.get_width()
                scaled_width = int(label_surf.get_width() * scale_factor)
                scaled_height = int(label_surf.get_height() * scale_factor)
                scaled_surf = pygame.transform.scale(label_surf, (scaled_width, scaled_height))
                scaled_shadow = pygame.transform.scale(label_shadow, (scaled_width, scaled_height))

                # Draw shadow first
                screen.blit(scaled_shadow, (label_x + 2, label_y + 2))
                # Draw main text
                screen.blit(scaled_surf, (label_x, label_y))

                # Add subtle text glow effect for highlighted item
                if is_highlighted:
                    glow_surf = font.render(label, True, (255, 215, 0, 50))
                    scaled_glow = pygame.transform.scale(glow_surf, (scaled_width, scaled_height))
                    screen.blit(scaled_glow, (label_x, label_y))
            else:
                # Draw shadow first
                screen.blit(label_shadow, (label_x + 2, label_y + 2))
                # Draw main text
                screen.blit(label_surf, (label_x, label_y))

                # Add subtle text glow effect for highlighted item
                if is_highlighted:
                    glow_surf = font.render(label, True, (255, 215, 0, 50))
                    screen.blit(glow_surf, (label_x, label_y))

        # Draw text input overlay if editing
        if self.editing_text:
            self._draw_text_input(screen)

    def _draw_text_input(self, screen):
        """Draw text input overlay"""
        # Semi-transparent backdrop
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        screen.blit(overlay, (0, 0))

        # Input box
        box_width = 600
        box_height = 120
        box_x = (self.screen_width - box_width) // 2
        box_y = (self.screen_height - box_height) // 2

        # Box background
        pygame.draw.rect(screen, (20, 20, 40), (box_x, box_y, box_width, box_height))
        pygame.draw.rect(screen, (255, 215, 0), (box_x, box_y, box_width, box_height), 3)

        # Prompt text
        prompt = self.font_small.render("Enter new value:", True, (200, 200, 200))
        prompt_rect = prompt.get_rect()
        prompt_x = box_x + (box_width - prompt_rect.width) // 2
        screen.blit(prompt, (prompt_x, box_y + 20))

        # Input field
        input_rect = pygame.Rect(box_x + 20, box_y + 50, box_width - 40, 40)
        pygame.draw.rect(screen, (40, 40, 60), input_rect)
        pygame.draw.rect(screen, (255, 255, 255), input_rect, 2)

        # Input text with blinking cursor
        cursor = "_" if int(self.cursor_blink * 2) % 2 == 0 else ""
        input_text = self.font_medium.render(self.text_input + cursor, True, (255, 255, 255))
        input_rect = input_text.get_rect()
        input_x = box_x + (box_width - input_rect.width) // 2
        screen.blit(input_text, (input_x, box_y + 55))

        # Instructions
        inst = self.font_small.render("ENTER to confirm | ESC to cancel", True, (150, 150, 150))
        inst_rect = inst.get_rect()
        inst_x = box_x + (box_width - inst_rect.width) // 2
        screen.blit(inst, (inst_x, box_y + box_height - 30))

    def _get_menu_title(self):
        """Get title for current menu"""
        titles = {
            'main': 'DEVELOPER MENU',
            'config': 'CONFIGURATION',
            'xp_config': 'XP SYSTEM CONFIG',
            'room_editor': 'ROOM EDITOR'
        }
        return titles.get(self.current_menu, 'DEV MENU')