import pygame
import os
from config.settings import RENDER_SCALE
from core.bitmap_font import BitmapFont

# "Press E" indicator font, cached module-wide instead of rebuilt every
# frame a player stands near a save point (same fix applied across
# flying_pad.py/nimbus_cloud.py/room_transition.py/trigger_box.py).
_INDICATOR_FONT = None


def _get_indicator_font():
    global _INDICATOR_FONT
    if _INDICATOR_FONT is None:
        _INDICATOR_FONT = pygame.font.Font(None, 20)
    return _INDICATOR_FONT


class SavePoint:
    """Interactive save point that player can activate"""

    def __init__(self, x, y, variant='big'):
        """
        Initialize save point

        Args:
            x, y: World coordinates
            variant: 'big' or 'small'
        """
        self.x = x
        self.y = y
        self.variant = variant

        # Set dimensions based on variant (world-unit size matching the actual sprite)
        if variant == 'big':
            self.width = 64
            self.height = 52
        else:  # small — sprite is 32×27
            self.width = 32
            self.height = 27

        self.active = True

        # Layer-manager integration
        from core.draw_layers import DrawLayer
        self.draw_layer = DrawLayer.GROUND  # layer -100, always behind the player
        self.y_sort = False

        # Visual properties for fallback rendering
        self.color = (255, 215, 0)  # Gold/yellow
        self.glow_intensity = 0
        self.glow_direction = 1
        self.glow_speed = 2

        # Interaction properties
        self.interaction_range = 40
        self.is_player_nearby = False

        # Sprite loading
        self.sprite = None
        self._load_sprite()

        # Cache of the sprite pre-scaled to a given render_scale, so draw()
        # only calls pygame.transform.scale() when render_scale changes
        # instead of on every frame.
        self._scaled_sprite = None
        self._scaled_sprite_scale = None

    def get_sort_key(self):
        """Used by LayerManager to sort against other world objects."""
        return (self.draw_layer, 0)

    def _load_sprite(self):
        """Load custom sprite or use None for fallback"""
        try:
            # Try loading custom sprite
            sprite_path = f'assets/objects/save_points/{self.variant}_save_point.png'
            self.sprite = pygame.image.load(sprite_path).convert_alpha()
        except:
            # No custom sprite found, will use fallback rendering
            self.sprite = None

    def update(self, dt, player):
        """
        Update save point state

        Args:
            dt: Delta time
            player: Player object to check proximity
        """
        # Update glow animation
        self.glow_intensity += self.glow_speed * dt * self.glow_direction
        if self.glow_intensity >= 1.0:
            self.glow_intensity = 1.0
            self.glow_direction = -1
        elif self.glow_intensity <= 0.0:
            self.glow_intensity = 0.0
            self.glow_direction = 1

        # Check if player is nearby
        distance = ((self.x - player.x) ** 2 + (self.y - player.y) ** 2) ** 0.5
        self.is_player_nearby = distance < self.interaction_range

    def draw(self, screen, camera, colors, render_scale=RENDER_SCALE):
        """Draw the save point (compatible with both layer manager and direct calls)"""
        # Convert world coordinates to screen coordinates
        screen_x = (self.x * render_scale) - camera.x
        screen_y = (self.y * render_scale) - camera.y

        # If we have a custom sprite, use it
        if self.sprite:
            # Scale sprite to appropriate size (cached — only rescale when
            # render_scale actually changes, not on every frame)
            scaled_width = int(self.width * render_scale)
            scaled_height = int(self.height * render_scale)
            if self._scaled_sprite_scale != render_scale:
                self._scaled_sprite = pygame.transform.scale(self.sprite, (scaled_width, scaled_height))
                self._scaled_sprite_scale = render_scale
            scaled_sprite = self._scaled_sprite

            # Draw the sprite
            sprite_rect = scaled_sprite.get_rect(
                center=(int(screen_x), int(screen_y))
            )
            screen.blit(scaled_sprite, sprite_rect)
        else:
            # Fallback: Draw procedural graphics
            main_rect = pygame.Rect(
                screen_x - (self.width * render_scale) // 2,
                screen_y - (self.height * render_scale) // 2,
                self.width * render_scale,
                self.height * render_scale
            )

            # Draw main orb/crystal
            if self.variant == 'big':
                # Draw as a diamond/crystal shape
                points = [
                    (main_rect.centerx, main_rect.top),  # Top
                    (main_rect.right, main_rect.centery),  # Right
                    (main_rect.centerx, main_rect.bottom),  # Bottom
                    (main_rect.left, main_rect.centery)  # Left
                ]
                pygame.draw.polygon(screen, self.color, points)
                pygame.draw.polygon(screen, (255, 255, 200), points, 2)
            else:
                # Small version - simple circle
                pygame.draw.circle(screen, self.color, main_rect.center, main_rect.width // 2)
                pygame.draw.circle(screen, (255, 255, 200), main_rect.center, main_rect.width // 2, 2)

        # Draw interaction indicator if player is nearby
        if self.is_player_nearby:
            indicator_y = screen_y - (self.height * render_scale) - 10
            indicator_text = "Press E"
            font = _get_indicator_font()
            text_surface = font.render(indicator_text, True, (255, 255, 255))
            text_rect = text_surface.get_rect(center=(screen_x, indicator_y))


class SavePointMenu:
    """Enhanced menu that appears when interacting with a big save point"""

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False
        self.selected_option = 0
        self.options = ['Save', 'Switch Characters']

        # Load the menu background sprite
        self.menu_sprite = None
        self._load_menu_sprite()

        # Load the arrow sprite for selection indicator
        self.arrow_sprite = None
        self._load_arrow_sprite()

        # Menu dimensions — scale the sprite by the largest integer multiplier
        # that fits comfortably on screen, so pixels are always uniform.
        _sprite_w, _sprite_h = 144, 40
        _scale = max(1, int(screen_height * 0.24 / _sprite_h))
        self.menu_width = _sprite_w * _scale
        self.menu_height = _sprite_h * _scale
        self.padding = max(8, int(screen_width * 0.012))

        # Font scale proportional to menu height so text fits inside the box
        font_scale = 6
        self.bitmap_font = BitmapFont(
            'assets/ui/fonts',
            letter_spacing=6,
            scale=font_scale
        )

        # Colors
        self.text_color = (255, 255, 255)  # White
        self.arrow_color = (255, 215, 0)  # Gold (for fallback)

        # Animation states
        self.scale_progress = 0.0  # 0.0 to 1.0
        self.scale_speed = 8.0  # How fast the menu scales up
        self.is_opening = False

        # Typewriter effect
        self.typewriter_chars_shown = [0, 0]  # Chars shown for each option
        self.typewriter_speed = 25.0  # Characters per second
        self.typewriter_timer = 0.0
        self.typewriter_complete = False

        # Arrow blink effect
        self.arrow_blink_timer = 0.0
        self.arrow_blink_speed = 4  # Blinks per second
        self.arrow_visible = True

    def _load_menu_sprite(self):
        """Load the menu background sprite"""
        try:
            self.menu_sprite = pygame.image.load('assets/ui/textbox/small_box.png').convert_alpha()
        except:
            # If sprite not found, will use fallback rendering
            self.menu_sprite = None

    def _load_arrow_sprite(self):
        """Load the arrow indicator sprite"""
        try:
            self.arrow_sprite = pygame.image.load('assets/ui/textbox/arrow.png').convert_alpha()
        except:
            # If sprite not found, will use fallback rendering (star)
            self.arrow_sprite = None

    def open(self):
        """Open the menu with animations"""
        self.active = True
        self.selected_option = 0
        self.is_opening = True
        self.scale_progress = 0.0
        self.typewriter_chars_shown = [0, 0]
        self.typewriter_timer = 0.0
        self.typewriter_complete = False
        self.arrow_blink_timer = 0.0
        self.arrow_visible = True

    def close(self):
        """Close the menu"""
        self.active = False
        self.is_opening = False

    def update(self, dt):
        """Update menu animations"""
        if not self.active:
            return

        # Update scale animation
        if self.is_opening and self.scale_progress < 1.0:
            self.scale_progress += self.scale_speed * dt
            if self.scale_progress >= 1.0:
                self.scale_progress = 1.0
                self.is_opening = False

        # Update typewriter effect (only starts after scale is complete)
        if self.scale_progress >= 1.0 and not self.typewriter_complete:
            self.typewriter_timer += dt
            self.typewrite_running = True

            # Calculate how many characters should be shown
            chars_to_show = int(self.typewriter_timer * self.typewriter_speed)

            # Update each option's visible characters
            char_count = 0
            all_complete = True
            for i, option in enumerate(self.options):
                option_length = len(option)

                if char_count + option_length <= chars_to_show:
                    # Entire option is shown
                    self.typewriter_chars_shown[i] = option_length
                    char_count += option_length
                elif char_count < chars_to_show:
                    # Partially show this option
                    self.typewriter_chars_shown[i] = chars_to_show - char_count
                    all_complete = False
                    break
                else:
                    # Not started yet
                    self.typewriter_chars_shown[i] = 0
                    all_complete = False

            if all_complete:
                self.typewriter_complete = True

        # Update arrow blink (continuously, starts as soon as menu opens)
        self.arrow_blink_timer += dt
        blink_period = 1.0 / self.arrow_blink_speed

        # Toggle visibility based on timer
        if self.arrow_blink_timer >= blink_period:
            self.arrow_visible = not self.arrow_visible
            self.arrow_blink_timer = 0.0

    def handle_input(self, event):
        """
        Handle keyboard input for menu navigation

        Returns:
            'save': User selected Save
            'switch_characters': User selected Switch Characters
            'close': User closed menu
            None: No action
        """
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_UP or event.key == pygame.K_w:
                self.selected_option = (self.selected_option - 1) % len(self.options)
                return None
            elif event.key == pygame.K_DOWN or event.key == pygame.K_s:
                self.selected_option = (self.selected_option + 1) % len(self.options)
                return None
            elif event.key == pygame.K_e:
                # User pressed E on an option
                if self.selected_option == 0:
                    return 'save'
                elif self.selected_option == 1:
                    return 'switch_characters'
            elif event.key == pygame.K_ESCAPE:
                self.close()
                return 'close'

        return None

    def draw(self, screen):
        """Draw the save point menu with animations"""
        if not self.active:
            return

        # Apply easing to scale (ease-out for smooth deceleration)
        scale_factor = self._ease_out_back(self.scale_progress)

        # Calculate scaled dimensions
        current_width = int(self.menu_width * scale_factor)
        current_height = int(self.menu_height * scale_factor)

        # Position centred horizontally, near the bottom of the screen
        menu_x = (self.screen_width - current_width) // 2
        menu_y = self.screen_height - current_height - 120

        # Draw the menu background sprite
        if self.menu_sprite:
            if current_width > 0 and current_height > 0:
                scaled_sprite = pygame.transform.scale(self.menu_sprite, (current_width, current_height))
                screen.blit(scaled_sprite, (menu_x, menu_y))
        else:
            # Fallback: Draw procedural background
            if current_width > 0 and current_height > 0:
                border_thickness = int(8 * RENDER_SCALE * scale_factor)
                outer_border = pygame.Rect(
                    menu_x - border_thickness,
                    menu_y - border_thickness,
                    current_width + border_thickness * 2,
                    current_height + border_thickness * 2
                )
                pygame.draw.rect(screen, (255, 215, 0), outer_border, 0, border_radius=int(8 * RENDER_SCALE))

                corner_size = int(20 * RENDER_SCALE * scale_factor)
                for corner_x, corner_y in [
                    (outer_border.left, outer_border.top),
                    (outer_border.right - corner_size, outer_border.top),
                    (outer_border.left, outer_border.bottom - corner_size),
                    (outer_border.right - corner_size, outer_border.bottom - corner_size)
                ]:
                    corner_rect = pygame.Rect(corner_x, corner_y, corner_size, corner_size)
                    pygame.draw.rect(screen, (200, 150, 0), corner_rect, 0, border_radius=int(4 * RENDER_SCALE))

                inner_border_thickness = int(4 * RENDER_SCALE * scale_factor)
                inner_border = pygame.Rect(
                    menu_x - inner_border_thickness,
                    menu_y - inner_border_thickness,
                    current_width + inner_border_thickness * 2,
                    current_height + inner_border_thickness * 2
                )
                pygame.draw.rect(screen, (0, 255, 0), inner_border, 0, border_radius=int(4 * RENDER_SCALE))

                menu_rect = pygame.Rect(menu_x, menu_y, current_width, current_height)
                pygame.draw.rect(screen, (0, 100, 0), menu_rect, 0, border_radius=int(4 * RENDER_SCALE))

                scanline_spacing = int(3 * RENDER_SCALE)
                for i in range(0, current_height, scanline_spacing):
                    line_y = menu_y + i
                    pygame.draw.line(screen, (0, 80, 0), (menu_x, line_y), (menu_x + current_width, line_y))

        # Only draw text if scale is complete or nearly complete
        if self.scale_progress >= 0.8:
            menu_center_x = menu_x + (current_width // 2)

            # --- INDEPENDENT LAYOUT ---
            # Each option has its own Y offset from menu_y, fully decoupled.
            # Adjust individual values here without affecting the other option.
            option_y_offsets = [
                int(24 * scale_factor) + 24,   # "Save" — distance from top edge
                int(56 * scale_factor) + 76,   # "Switch Characters" — independent position
            ]

            for i, option in enumerate(self.options):
                option_y = menu_y + option_y_offsets[i]

                # Get the text to display (typewriter effect)
                chars_to_show = self.typewriter_chars_shown[i]
                display_text = option[:chars_to_show]

                if not display_text:
                    continue

                # Render full text for width measurement, then clip to show only the visible portion
                full_text_surface = self.bitmap_font.render(option)
                full_text_width = full_text_surface.get_width()

                # Render the partial text to actually display
                text_surface = self.bitmap_font.render(display_text)
                text_height = text_surface.get_height()

                # Each option has its own independent horizontal nudge from centre.
                # Adjust individual values here without affecting the other option.
                text_x_offsets = [-3, 13]  # [Save, Switch Characters]
                text_x = menu_center_x - (full_text_width // 2) + text_x_offsets[i]

                # Draw arrow when selected option starts typing
                if i == self.selected_option and chars_to_show > 0:
                    arrow_spacing = int(6 * RENDER_SCALE) - 24

                    # Only draw arrow if it should be visible (blink effect)
                    if self.arrow_visible:
                        if self.arrow_sprite:
                            # Scale arrow to match text height
                            arrow_scale = text_height / self.arrow_sprite.get_height()
                            scaled_arrow = pygame.transform.scale(
                                self.arrow_sprite,
                                (int(self.arrow_sprite.get_width() * arrow_scale),
                                 int(self.arrow_sprite.get_height() * arrow_scale))
                            )

                            # Position arrow left of text, vertically centered with text row
                            arrow_x = text_x - scaled_arrow.get_width() - arrow_spacing
                            arrow_y = option_y + (text_height // 2) - (scaled_arrow.get_height() // 2) + 6

                            screen.blit(scaled_arrow, (arrow_x, arrow_y))
                        else:
                            # Fallback: Draw a star indicator
                            arrow_x = text_x - arrow_spacing
                            arrow_y = option_y + (text_height // 2)

                            scale = RENDER_SCALE * 1.5
                            star_points = [
                                (arrow_x, arrow_y - int(6 * scale)),
                                (arrow_x + int(3 * scale), arrow_y - int(2 * scale)),
                                (arrow_x + int(8 * scale), arrow_y - int(2 * scale)),
                                (arrow_x + int(4 * scale), arrow_y + int(1 * scale)),
                                (arrow_x + int(6 * scale), arrow_y + int(6 * scale)),
                                (arrow_x, arrow_y + int(3 * scale)),
                                (arrow_x - int(6 * scale), arrow_y + int(6 * scale)),
                                (arrow_x - int(4 * scale), arrow_y + int(1 * scale)),
                                (arrow_x - int(8 * scale), arrow_y - int(2 * scale)),
                                (arrow_x - int(3 * scale), arrow_y - int(2 * scale))
                            ]
                            pygame.draw.polygon(screen, self.arrow_color, star_points)

                # Draw text string at its independent coordinate slot
                screen.blit(text_surface, (text_x, option_y))

    def _ease_out_back(self, t):
        """
        Easing function for smooth scale-up with slight overshoot
        Creates a bounce-back effect
        """
        c1 = 1.70158
        c3 = c1 + 1

        return 1 + c3 * pow(t - 1, 3) + c1 * pow(t - 1, 2)


class SavePointManager:
    """Manages all save points in a room"""

    def __init__(self):
        self.save_points = {}  # Dictionary of room_name -> list of save points

    def add_save_point(self, room_name, save_point):
        """Add a save point to a room"""
        if room_name not in self.save_points:
            self.save_points[room_name] = []
        self.save_points[room_name].append(save_point)

    def remove_save_point(self, room_name, save_point):
        """Remove a save point from a room"""
        if room_name in self.save_points and save_point in self.save_points[room_name]:
            self.save_points[room_name].remove(save_point)

    def get_save_points(self, room_name):
        """Get all save points for a room"""
        return self.save_points.get(room_name, [])

    def clear_room(self, room_name):
        """Clear all save points in a room"""
        if room_name in self.save_points:
            self.save_points[room_name] = []

    def get_nearby_save_point(self, room_name, player, max_distance=40):
        """
        Find the nearest save point within range of the player

        Args:
            room_name: Current room
            player: Player object
            max_distance: Maximum interaction distance

        Returns:
            SavePoint object or None
        """
        save_points = self.get_save_points(room_name)
        nearest = None
        nearest_distance = max_distance

        for save_point in save_points:
            if not save_point.active:
                continue

            distance = ((save_point.x - player.x) ** 2 + (save_point.y - player.y) ** 2) ** 0.5
            if distance < nearest_distance:
                nearest = save_point
                nearest_distance = distance

        return nearest