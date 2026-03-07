"""
Character Switch Menu System - Full Sprite-Based Implementation

Features:
- Full screen scanline background overlay
- Properly sized and centered canvas
- 9-slice border sprite scaling (no distortion)
- All buttons/UI elements use sprites
- Characters positioned as shown in reference
- Pressed button states for arrows, A, and B buttons
"""

import pygame
import os
from config.settings import RENDER_SCALE
from core.bitmap_font import BitmapFont


class CharacterSwitchMenu:
    """Fully sprite-based character switch menu"""

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False
        self.selected_character = 0

        # Canvas sizing - fit within screen with margins
        self.canvas_width = int(screen_width * 0.85)  # 85% of screen width
        self.canvas_height = int(screen_height * 0.75)  # 75% of screen height
        self.canvas_x = (screen_width - self.canvas_width) // 2
        self.canvas_y = (screen_height - self.canvas_height) // 2

        # Available characters with per-character animation state
        self.characters = [
            {
                'id': 'goku',
                'name': 'Goku',
                'unlocked': True,
                'costume': 'base',
                'animation_frame': 0,
                'animation_timer': 0.0
            },
            {
                'id': 'gohan',
                'name': 'Gohan',
                'unlocked': True,
                'costume': 'base',
                'animation_frame': 0,
                'animation_timer': 0.0
            },
            {
                'id': 'vegeta',
                'name': 'Vegeta',
                'unlocked': True,
                'costume': 'base',
                'animation_frame': 0,
                'animation_timer': 0.0
            }
        ]

        # Button press state tracking
        self.button_states = {
            'left': False,
            'right': False,
            'a': False,
            'b': False
        }

        # Button press timers (for auto-reset after brief display)
        self.button_press_timers = {
            'left': 0.0,
            'right': 0.0,
            'a': 0.0,
            'b': 0.0
        }
        self.button_press_duration = 0.1  # How long to show pressed state (seconds)

        # Load all sprites
        self._load_ui_sprites()
        self._load_character_sprites()

        # Animation settings
        self.animation_speed = 0.15  # Seconds per frame

        # Bitmap font for title (scaled appropriately)
        font_scale = max(2, int(RENDER_SCALE * 2))
        self.bitmap_font = BitmapFont(
            'assets/ui/fonts',
            letter_spacing=int(2 * RENDER_SCALE),
            scale=font_scale
        )

        # Colors
        self.bg_scanline_dark = (0, 80, 0)
        self.bg_scanline_light = (0, 100, 0)
        self.border_outer = (255, 215, 0)  # Yellow border
        self.border_inner = (180, 100, 0)
        self.border_green = (0, 255, 0)
        self.title_color = (255, 255, 0)
        self.text_color = (255, 255, 0)  # Yellow text
        self.text_shadow_color = (0, 0, 0)  # Black shadow
        self.shadow_offset = (2, 2)  # Shadow offset (x, y) in pixels

        # Background texture offset - adjust these values to shift the tiled background
        self.bg_offset_x = 0  # Horizontal offset (pixels)
        self.bg_offset_y = 0  # Vertical offset (pixels) - change this to shift up/down

    def _load_ui_sprites(self):
        """Load UI element sprites (buttons, arrows, box borders)"""
        # Load button sprites (normal and pressed)
        try:
            self.button_a = pygame.image.load('assets/ui/buttons/button_a.png').convert_alpha()
        except:
            self.button_a = None

        try:
            self.button_a_pressed = pygame.image.load('assets/ui/buttons/button_a_pressed.png').convert_alpha()
        except:
            self.button_a_pressed = None

        try:
            self.button_b = pygame.image.load('assets/ui/buttons/button_b.png').convert_alpha()
        except:
            self.button_b = None

        try:
            self.button_b_pressed = pygame.image.load('assets/ui/buttons/button_b_pressed.png').convert_alpha()
        except:
            self.button_b_pressed = None

        # Load arrow sprites (normal and pressed)
        try:
            self.arrow_left = pygame.image.load('assets/ui/buttons/arrow_left.png').convert_alpha()
        except:
            self.arrow_left = None

        try:
            self.arrow_left_pressed = pygame.image.load('assets/ui/buttons/arrow_left_pressed.png').convert_alpha()
        except:
            self.arrow_left_pressed = None

        try:
            self.arrow_right = pygame.image.load('assets/ui/buttons/arrow_right.png').convert_alpha()
        except:
            self.arrow_right = None

        try:
            self.arrow_right_pressed = pygame.image.load('assets/ui/buttons/arrow_right_pressed.png').convert_alpha()
        except:
            self.arrow_right_pressed = None

        # Load greyed arrow sprites (for disabled state)
        try:
            self.arrow_left_greyed = pygame.image.load('assets/ui/buttons/arrow_left_greyed.png').convert_alpha()
        except:
            self.arrow_left_greyed = None

        try:
            self.arrow_right_greyed = pygame.image.load('assets/ui/buttons/arrow_right_greyed.png').convert_alpha()
        except:
            self.arrow_right_greyed = None

        # Load box border sprite (native size: 227x131)
        try:
            self.box_sprite = pygame.image.load('assets/ui/textbox/border.png').convert_alpha()
        except:
            self.box_sprite = None

        # Load background texture for tiling
        try:
            bg_tex = pygame.image.load('assets/ui/textbox/background_texture.png').convert_alpha()
            # Scale up the texture before tiling (adjust this value to make it bigger/smaller)
            # 2.0 = 2x size, 3.0 = 3x size, 4.0 = 4x size, etc.
            texture_scale = 4.5
            scaled_width = int(bg_tex.get_width() * texture_scale)
            scaled_height = int(bg_tex.get_height() * texture_scale)
            self.bg_texture = pygame.transform.scale(bg_tex, (scaled_width, scaled_height))
        except:
            self.bg_texture = None

    def _load_character_sprites(self):
        """Load BOTH idle.png and walk.png sprite sheets for all characters"""
        for char in self.characters:
            char_id = char['id']
            costume = char['costume']

            # Load IDLE spritesheet (for non-selected characters)
            idle_path = f'assets/sprites/{char_id}/{costume}/idle.png'
            walk_path = f'assets/sprites/{char_id}/{costume}/walk.png'

            # --- LOAD IDLE SPRITE (facing down, frame 0) ---
            try:
                idle_sheet = pygame.image.load(idle_path).convert_alpha()

                total_width = idle_sheet.get_width()
                total_height = idle_sheet.get_height()

                frame_width = total_width // 2
                frame_height = total_height // 4

                idle_frame = idle_sheet.subsurface(
                    pygame.Rect(0, 0, frame_width, frame_height)
                )
                # Scale appropriately for menu (not too large)
                scale_factor = max(2, int(RENDER_SCALE * 1.5))
                char['idle_sprite'] = pygame.transform.scale(
                    idle_frame,
                    (frame_width * scale_factor, frame_height * scale_factor)
                )
                char['has_idle'] = True

            except (pygame.error, FileNotFoundError) as e:
                char['idle_sprite'] = None
                char['has_idle'] = False

            # --- LOAD WALK ANIMATION (for selected character) ---
            try:
                walk_sheet = pygame.image.load(walk_path).convert_alpha()

                total_width = walk_sheet.get_width()
                total_height = walk_sheet.get_height()

                frame_width = total_width // 4
                frame_height = total_height // 4

                walk_down_frames = []
                scale_factor = max(2, int(RENDER_SCALE * 1.5))
                for i in range(4):
                    frame = walk_sheet.subsurface(
                        pygame.Rect(i * frame_width, 0, frame_width, frame_height)
                    )
                    scaled_frame = pygame.transform.scale(
                        frame,
                        (frame_width * scale_factor, frame_height * scale_factor)
                    )
                    walk_down_frames.append(scaled_frame)

                char['walk_frames'] = walk_down_frames
                char['has_walk'] = True

            except (pygame.error, FileNotFoundError) as e:
                char['walk_frames'] = None
                char['has_walk'] = False

    def open(self, current_character='goku'):
        """Open the character switch menu"""
        self.active = True

        for i, char in enumerate(self.characters):
            if char['id'] == current_character:
                self.selected_character = i
                break

        # Reset all character animations
        for char in self.characters:
            char['animation_frame'] = 0
            char['animation_timer'] = 0.0

        # Reset button states
        for key in self.button_states:
            self.button_states[key] = False
            self.button_press_timers[key] = 0.0

    def close(self):
        """Close the menu"""
        self.active = False

    def set_character_unlocked(self, character_id, unlocked=True):
        """Unlock or lock a character"""
        for char in self.characters:
            if char['id'] == character_id:
                char['unlocked'] = unlocked
                break

    def _set_button_pressed(self, button_name):
        """Set a button to pressed state"""
        self.button_states[button_name] = True
        self.button_press_timers[button_name] = self.button_press_duration

    def update(self, dt):
        """Update animation state"""
        if not self.active:
            return

        # Update button press timers
        for button_name in self.button_press_timers:
            if self.button_press_timers[button_name] > 0:
                self.button_press_timers[button_name] -= dt
                if self.button_press_timers[button_name] <= 0:
                    self.button_states[button_name] = False

        # Update character animations
        for i, char in enumerate(self.characters):
            is_selected = (i == self.selected_character)

            if is_selected and char['unlocked']:
                # Animate selected character
                char['animation_timer'] += dt
                if char['animation_timer'] >= self.animation_speed:
                    char['animation_timer'] = 0.0
                    if char['has_walk'] and char['walk_frames']:
                        char['animation_frame'] = (char['animation_frame'] + 1) % len(char['walk_frames'])

    def handle_input(self, event):
        """Handle input events. Returns selected character ID or 'close' or None"""
        if not self.active:
            return None

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_LEFT:
                self._set_button_pressed('left')
                # Move selection left
                if self.selected_character > 0:
                    self.selected_character -= 1
                    # Reset animation for newly selected
                    self.characters[self.selected_character]['animation_frame'] = 0
                    self.characters[self.selected_character]['animation_timer'] = 0.0

            elif event.key == pygame.K_RIGHT:
                self._set_button_pressed('right')
                # Move selection right
                if self.selected_character < len(self.characters) - 1:
                    self.selected_character += 1
                    # Reset animation for newly selected
                    self.characters[self.selected_character]['animation_frame'] = 0
                    self.characters[self.selected_character]['animation_timer'] = 0.0

            elif event.key == pygame.K_z or event.key == pygame.K_RETURN:  # A button
                self._set_button_pressed('a')
                selected = self.characters[self.selected_character]
                if selected['unlocked']:
                    self.close()
                    return selected['id']

            elif event.key == pygame.K_x or event.key == pygame.K_ESCAPE:  # B button
                self._set_button_pressed('b')
                self.close()
                return 'close'

        return None

    def _draw_scanlines(self, screen, rect):
        """Draw retro scanline effect over a rect"""
        scanline_height = 2
        for y in range(rect.top, rect.bottom, scanline_height * 2):
            line_rect = pygame.Rect(rect.left, y, rect.width, scanline_height)
            pygame.draw.rect(screen, self.bg_scanline_dark, line_rect)
            line_rect.y += scanline_height
            pygame.draw.rect(screen, self.bg_scanline_light, line_rect)

    def _render_text_with_shadow(self, screen, text, position, anchor='center'):
        """
        Render text with drop shadow effect
        Args:
            screen: pygame surface to draw on
            text: text string to render
            position: (x, y) tuple for text position
            anchor: alignment - 'center', 'midleft', etc.
        """
        # Render shadow (offset position, dark color)
        shadow_surface = self.bitmap_font.render(text)
        # Colorize the shadow (if bitmap font doesn't support color directly)
        shadow_colored = shadow_surface.copy()
        shadow_colored.fill(self.text_shadow_color, special_flags=pygame.BLEND_RGBA_MULT)
        shadow_x = position[0] + self.shadow_offset[0]
        shadow_y = position[1] + self.shadow_offset[1]

        if anchor == 'center':
            shadow_rect = shadow_colored.get_rect(center=(shadow_x, shadow_y))
        elif anchor == 'midleft':
            shadow_rect = shadow_colored.get_rect(midleft=(shadow_x, shadow_y))
        else:
            shadow_rect = shadow_colored.get_rect(topleft=(shadow_x, shadow_y))

        screen.blit(shadow_colored, shadow_rect)

        # Render main text (yellow)
        text_surface = self.bitmap_font.render(text)
        # Colorize the text
        text_colored = text_surface.copy()
        text_colored.fill(self.text_color, special_flags=pygame.BLEND_RGBA_MULT)

        if anchor == 'center':
            text_rect = text_colored.get_rect(center=position)
        elif anchor == 'midleft':
            text_rect = text_colored.get_rect(midleft=position)
        else:
            text_rect = text_colored.get_rect(topleft=position)

        screen.blit(text_colored, text_rect)

    def _draw_tiled_background(self, screen, rect):
        """Draw a tiled background texture over a rect"""
        if not self.bg_texture:
            # Fallback to scanlines if texture not loaded
            self._draw_scanlines(screen, rect)
            return

        texture_width = self.bg_texture.get_width()
        texture_height = self.bg_texture.get_height()

        # Apply offsets (modulo to keep within one tile's range)
        offset_x = self.bg_offset_x % texture_width
        offset_y = self.bg_offset_y % texture_height

        # Calculate starting position (accounting for offset)
        start_x = rect.left - offset_x
        start_y = rect.top - offset_y

        # Calculate how many times to tile in x and y (add extra to account for offset)
        tiles_x = (rect.width // texture_width) + 3  # +3 to ensure full coverage with offset
        tiles_y = (rect.height // texture_height) + 3

        # Draw tiled texture
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                x = start_x + (tx * texture_width)
                y = start_y + (ty * texture_height)
                screen.blit(self.bg_texture, (x, y))

    def _draw_9slice_sprite(self, screen, sprite, x, y, width, height, corner_size=16):
        """
        Draw a sprite using 9-slice scaling to prevent distortion
        Corners stay original size, edges and center stretch
        """
        if not sprite:
            return False

        sprite_width = sprite.get_width()
        sprite_height = sprite.get_height()

        # Scale factor to make the border thicker - adjust this value to control border thickness
        # Higher values = thicker border (try 2.0, 3.0, 4.0, etc.)
        border_scale = 4.0

        # Make sure corner size doesn't exceed sprite dimensions
        corner_w = min(corner_size, sprite_width // 3)
        corner_h = min(corner_size, sprite_height // 3)

        # Scaled corner dimensions (what we'll actually draw)
        scaled_corner_w = int(corner_w * border_scale)
        scaled_corner_h = int(corner_h * border_scale)

        # Define the 9 regions of the source sprite
        # Corners
        top_left = sprite.subsurface(pygame.Rect(0, 0, corner_w, corner_h))
        top_right = sprite.subsurface(pygame.Rect(sprite_width - corner_w, 0, corner_w, corner_h))
        bottom_left = sprite.subsurface(pygame.Rect(0, sprite_height - corner_h, corner_w, corner_h))
        bottom_right = sprite.subsurface(
            pygame.Rect(sprite_width - corner_w, sprite_height - corner_h, corner_w, corner_h))

        # Edges
        top_edge = sprite.subsurface(pygame.Rect(corner_w, 0, sprite_width - 2 * corner_w, corner_h))
        bottom_edge = sprite.subsurface(
            pygame.Rect(corner_w, sprite_height - corner_h, sprite_width - 2 * corner_w, corner_h))
        left_edge = sprite.subsurface(pygame.Rect(0, corner_h, corner_w, sprite_height - 2 * corner_h))
        right_edge = sprite.subsurface(
            pygame.Rect(sprite_width - corner_w, corner_h, corner_w, sprite_height - 2 * corner_h))

        # Center
        center = sprite.subsurface(
            pygame.Rect(corner_w, corner_h, sprite_width - 2 * corner_w, sprite_height - 2 * corner_h))

        # Calculate dimensions for stretched parts (accounting for scaled corners)
        center_width = width - 2 * scaled_corner_w
        center_height = height - 2 * scaled_corner_h

        # Draw corners (scaled by border_scale)
        screen.blit(pygame.transform.scale(top_left, (scaled_corner_w, scaled_corner_h)), (x, y))
        screen.blit(pygame.transform.scale(top_right, (scaled_corner_w, scaled_corner_h)),
                    (x + width - scaled_corner_w, y))
        screen.blit(pygame.transform.scale(bottom_left, (scaled_corner_w, scaled_corner_h)),
                    (x, y + height - scaled_corner_h))
        screen.blit(pygame.transform.scale(bottom_right, (scaled_corner_w, scaled_corner_h)),
                    (x + width - scaled_corner_w, y + height - scaled_corner_h))

        # Draw edges (scaled in one dimension for length, border_scale for thickness)
        scaled_top = pygame.transform.scale(top_edge, (center_width, scaled_corner_h))
        screen.blit(scaled_top, (x + scaled_corner_w, y))

        scaled_bottom = pygame.transform.scale(bottom_edge, (center_width, scaled_corner_h))
        screen.blit(scaled_bottom, (x + scaled_corner_w, y + height - scaled_corner_h))

        scaled_left = pygame.transform.scale(left_edge, (scaled_corner_w, center_height))
        screen.blit(scaled_left, (x, y + scaled_corner_h))

        scaled_right = pygame.transform.scale(right_edge, (scaled_corner_w, center_height))
        screen.blit(scaled_right, (x + width - scaled_corner_w, y + scaled_corner_h))

        # Draw center (scaled in both dimensions)
        scaled_center = pygame.transform.scale(center, (center_width, center_height))
        screen.blit(scaled_center, (x + scaled_corner_w, y + scaled_corner_h))

        return True

    def _draw_button_sprite(self, screen, sprite_normal, sprite_pressed, is_pressed, x, y, label):
        """Draw a button sprite with label (showing pressed or normal state)"""
        sprite = sprite_pressed if (is_pressed and sprite_pressed) else sprite_normal

        # Calculate button scale based on canvas size
        button_scale = max(2, int(self.canvas_height * 0.06))

        if sprite:
            # Calculate scale factor to maintain aspect ratio
            # Scale based on height, then calculate width proportionally
            target_height = button_scale
            scale_factor = target_height / sprite.get_height()
            scaled_width = int(sprite.get_width() * scale_factor)
            scaled_height = target_height

            scaled_sprite = pygame.transform.scale(
                sprite,
                (scaled_width, scaled_height)
            )
            screen.blit(scaled_sprite, (x, y))

            # Draw label next to button
            label_x = x + scaled_sprite.get_width() + int(5 * RENDER_SCALE)
            label_y = y + scaled_sprite.get_height() // 2

            # Use bitmap font for labels with shadow
            self._render_text_with_shadow(screen, label, (label_x, label_y), anchor='midleft')
        else:
            # Fallback: draw circle (darker when pressed)
            button_size = button_scale
            color = (140, 140, 140) if is_pressed else (180, 180, 180)
            pygame.draw.circle(screen, color,
                               (x + button_size // 2, y + button_size // 2), button_size // 2)
            pygame.draw.circle(screen, (100, 100, 100),
                               (x + button_size // 2, y + button_size // 2), button_size // 2, 3)

            # Label with shadow
            self._render_text_with_shadow(screen, label, (x + button_size + 10, y + button_size // 2), anchor='midleft')

    def _draw_arrow_sprite(self, screen, sprite_normal, sprite_pressed, sprite_greyed, is_pressed, is_disabled, x, y):
        """Draw an arrow button sprite (showing pressed, normal, or greyed state)

        IMPORTANT: Pressed state takes priority over disabled state.
        This ensures the pressed animation shows before switching to greyed when at boundaries.
        """
        # Choose sprite based on state - PRESSED TAKES PRIORITY
        if is_pressed and sprite_pressed:
            sprite = sprite_pressed
        elif is_disabled and sprite_greyed:
            sprite = sprite_greyed
        else:
            sprite = sprite_normal

        # Calculate arrow scale based on canvas size
        arrow_size = max(20, int(self.canvas_height * 0.06))

        if sprite:
            scaled_sprite = pygame.transform.scale(
                sprite,
                (arrow_size, arrow_size)
            )
            sprite_rect = scaled_sprite.get_rect(center=(x, y))
            screen.blit(scaled_sprite, sprite_rect)
        else:
            # Fallback arrow (darker when pressed or disabled)
            box_size = arrow_size
            if is_pressed:
                color = (140, 140, 140)  # Pressed takes priority
            elif is_disabled:
                color = (80, 80, 80)  # Very dark for disabled
            else:
                color = (180, 180, 180)
            box_rect = pygame.Rect(x - box_size // 2, y - box_size // 2, box_size, box_size)
            pygame.draw.rect(screen, color, box_rect)
            pygame.draw.rect(screen, (100, 100, 100), box_rect, 3)

    def _draw_character_sprite(self, screen, character, x, y, is_selected):
        """
        Draw character sprite
        SELECTED: Use walk spritesheet (animated)
        NOT SELECTED: Use idle spritesheet (static)
        """
        if is_selected:
            # SELECTED: Show animated walk sprites
            if character['has_walk'] and character['walk_frames']:
                frame_index = character['animation_frame']
                frame = character['walk_frames'][frame_index]
                sprite_rect = frame.get_rect(center=(x, y))
                screen.blit(frame, sprite_rect)
            else:
                self._draw_fallback_sprite(screen, character, x, y)
        else:
            # NOT SELECTED: Show idle sprite
            if character['has_idle'] and character['idle_sprite']:
                sprite_rect = character['idle_sprite'].get_rect(center=(x, y))
                screen.blit(character['idle_sprite'], sprite_rect)
            else:
                self._draw_fallback_sprite(screen, character, x, y)

    def _draw_fallback_sprite(self, screen, character, x, y):
        """Draw fallback sprite if assets not found"""
        sprite_size = int(self.canvas_height * 0.15)
        sprite_rect = pygame.Rect(x - sprite_size // 2, y - sprite_size // 2, sprite_size, sprite_size)

        if not character['unlocked']:
            # Locked character - dark silhouette
            pygame.draw.rect(screen, (30, 30, 30), sprite_rect)
            pygame.draw.rect(screen, (80, 80, 80), sprite_rect, 2)
        else:
            # Placeholder
            pygame.draw.circle(screen, (100, 100, 200), (x, y), sprite_size // 2)
            pygame.draw.circle(screen, (0, 0, 0), (x, y), sprite_size // 2, 3)

    def draw(self, screen):
        """Draw the character switch menu"""
        if not self.active:
            return

        # FULL SCREEN SCANLINE BACKGROUND - covers everything
        full_screen_rect = pygame.Rect(0, 0, self.screen_width, self.screen_height)
        self._draw_tiled_background(screen, full_screen_rect)

        # Title at top of canvas
        title_margin = int(self.canvas_height * 0.08)
        title_text = "Switch Character"
        self._render_text_with_shadow(
            screen,
            title_text,
            (self.screen_width // 2, self.canvas_y - 50 + title_margin),
            anchor='center'
        )

        # Inner box dimensions (within canvas, with margins)
        inner_margin = int(self.canvas_height)
        inner_box_width = self.canvas_width * 1.1 - (inner_margin)
        inner_box_height = int(self.canvas_height) // 1.4
        box_x = self.canvas_x + (self.canvas_width - inner_box_width) // 2
        box_y = self.canvas_y + title_margin

        # Draw box border using 9-slice scaling or procedural
        sprite_drawn = False
        if self.box_sprite:
            # Use 9-slice scaling to prevent distortion (227x131 native)
            sprite_drawn = self._draw_9slice_sprite(
                screen,
                self.box_sprite,
                box_x,
                box_y,
                inner_box_width,
                inner_box_height,
                corner_size=20  # Size of corners to preserve (increased for thicker border)
            )

        if not sprite_drawn:
            # Procedural border (fallback)
            outer_border_rect = pygame.Rect(box_x - 6, box_y - 6, inner_box_width + 12, inner_box_height + 12)
            pygame.draw.rect(screen, self.border_outer, outer_border_rect, 0)

            inner_border_rect = pygame.Rect(box_x - 3, box_y - 3, inner_box_width + 6, inner_box_height + 6)
            pygame.draw.rect(screen, self.border_inner, inner_border_rect, 0)

            green_border_rect = pygame.Rect(box_x - 1, box_y - 1, inner_box_width + 2, inner_box_height + 2)
            pygame.draw.rect(screen, self.border_green, green_border_rect, 0)

            # Inner scanlines
            inner_rect = pygame.Rect(box_x, box_y, inner_box_width, inner_box_height)
            self._draw_tiled_background(screen, inner_rect)

        # Character area positioning
        character_y = box_y + inner_box_height // 2

        # Calculate character positions (spread evenly)
        num_chars = len(self.characters)
        character_spacing = inner_box_width // (num_chars + 1)

        # Arrow positioning
        arrow_margin = int(inner_box_width * 0.08)

        # Determine if arrows should be disabled (greyed out)
        left_disabled = (self.selected_character <= 0)
        right_disabled = (self.selected_character >= len(self.characters) - 1)

        # Draw left arrow (with pressed/greyed state)
        left_arrow_x = box_x + arrow_margin
        left_arrow_y = character_y
        self._draw_arrow_sprite(
            screen,
            self.arrow_left,
            self.arrow_left_pressed,
            self.arrow_left_greyed,
            self.button_states['left'],
            left_disabled,
            left_arrow_x,
            left_arrow_y
        )

        # Draw right arrow (with pressed/greyed state)
        right_arrow_x = box_x + inner_box_width - arrow_margin
        right_arrow_y = character_y
        self._draw_arrow_sprite(
            screen,
            self.arrow_right,
            self.arrow_right_pressed,
            self.arrow_right_greyed,
            self.button_states['right'],
            right_disabled,
            right_arrow_x,
            right_arrow_y
        )

        # Draw characters
        for i, char in enumerate(self.characters):
            char_x = box_x + character_spacing * (i + 1)
            char_y = character_y

            is_selected = (i == self.selected_character)

            # Draw character sprite
            self._draw_character_sprite(screen, char, char_x, char_y, is_selected)

        # Bottom buttons positioning
        button_y_offset = int(self.canvas_height * 0.315)
        button_y = self.canvas_y + self.canvas_height - button_y_offset

        # A button (Select) - left side (with pressed state)
        button_margin = int(self.canvas_width * 0.213)
        select_x = self.canvas_x + button_margin
        self._draw_button_sprite(
            screen,
            self.button_a,
            self.button_a_pressed,
            self.button_states['a'],
            select_x,
            button_y,
            "Select"
        )

        # B button (Cancel) - right side (with pressed state)
        cancel_x = self.canvas_x + self.canvas_width - button_margin - int(self.canvas_width * 0.45)
        self._draw_button_sprite(
            screen,
            self.button_b,
            self.button_b_pressed,
            self.button_states['b'],
            cancel_x,
            button_y,
            "Cancel"
        )