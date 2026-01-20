import pygame
from core.draw_layers import get_beam_layer, DrawLayer


class BeamAttack:
    def __init__(self, x, y, direction, scale=2.0):
        self.x = x
        self.y = y
        self.direction = direction
        self.scale = scale  # Scale factor for the beam
        self.length = 0
        self.max_length = 400
        self.grow_speed = 200  # pixels per second
        self.active = True

        # Animation
        self.current_frame = 0
        self.frame_timer = 0
        self.frame_duration = 0.08  # Time per frame in seconds

        # Sprite dimensions (single frame dimensions)
        self.frame_width = 16  # Width of each frame in spritesheet
        self.frame_height = 16  # Height of each frame in spritesheet

        # Direction to row mapping (same as your player system)
        self.direction_to_row = {
            'down': 0,
            'left': 1,
            'right': 2,
            'up': 3
        }

        # Get row for current direction
        self.current_row = self.direction_to_row.get(direction, 0)

        # Sprites for each beam part
        self.begin_sprite = None
        self.middle_sprite = None
        self.end_sprite = None

        # Initialize scaled dimensions
        self.begin_width_scaled = 0
        self.begin_height_scaled = 0
        self.middle_width_scaled = 0
        self.middle_height_scaled = 0
        self.end_width_scaled = 0
        self.end_height_scaled = 0

        self.begin_sprite_scaled = None
        self.middle_sprite_scaled = None
        self.end_sprite_scaled = None

        # Load beam sprites with correct direction
        self.load_sprites()

        # Calculate scaled dimensions
        self.calculate_scaled_dimensions()

        # Determine beam width based on sprites (for collision if needed)
        self.width = max(
            self.begin_width_scaled,
            self.middle_width_scaled,
            self.end_width_scaled,
            30  # Minimum fallback width
        )

        # Set layer based on direction
        self.draw_layer = get_beam_layer(self.direction, self.direction)
        self.y_sort = False

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def load_sprites(self):
        """Load beam sprites from spritesheets with correct direction row and all animation frames"""
        try:
            # Load begin spritesheet
            begin_sheet = pygame.image.load('assets/sprites/attacks/kamehameha/begin_kamehameha.png').convert_alpha()
            begin_sheet_width = begin_sheet.get_width()
            begin_sheet_height = begin_sheet.get_height()

            # Calculate number of frames per row
            frames_per_row = begin_sheet_width // self.frame_width

            # Get ALL frames for current direction row
            begin_frames = []
            for frame_index in range(frames_per_row):
                x = frame_index * self.frame_width
                y = self.current_row * self.frame_height
                frame = begin_sheet.subsurface(pygame.Rect(x, y, self.frame_width, self.frame_height))
                begin_frames.append(frame)

            self.begin_sprite = begin_frames  # Store as LIST of frames
            print(f"Loaded {len(begin_frames)} begin sprites for direction {self.direction}")

        except Exception as e:
            print(f"Error loading begin beam sprite: {e}")
            self.begin_sprite = None

        try:
            # Load middle spritesheet
            middle_sheet = pygame.image.load('assets/sprites/attacks/kamehameha/middle_kamehameha.png').convert_alpha()
            middle_sheet_width = middle_sheet.get_width()
            middle_sheet_height = middle_sheet.get_height()

            # Middle sprite dimensions might be different
            middle_width = 6  # Width of middle part
            middle_height = 6  # Height of middle part

            # Calculate number of frames per row
            frames_per_row = middle_sheet_width // middle_width

            # Get ALL frames for current direction row
            middle_frames = []
            for frame_index in range(frames_per_row):
                x = frame_index * middle_width
                y = self.current_row * middle_height
                # Make sure we don't go out of bounds
                if y + middle_height <= middle_sheet_height:
                    frame = middle_sheet.subsurface(pygame.Rect(x, y, middle_width, middle_height))
                    middle_frames.append(frame)

            self.middle_sprite = middle_frames  # Store as LIST of frames
            print(f"Loaded {len(middle_frames)} middle sprites for direction {self.direction}")

        except Exception as e:
            print(f"Error loading middle beam sprite: {e}")
            self.middle_sprite = None

        try:
            # Load end spritesheet
            end_sheet = pygame.image.load('assets/sprites/attacks/kamehameha/end_kamehameha.png').convert_alpha()
            end_sheet_width = end_sheet.get_width()
            end_sheet_height = end_sheet.get_height()

            # Calculate number of frames per row
            frames_per_row = end_sheet_width // self.frame_width

            # Get ALL frames for current direction row
            end_frames = []
            for frame_index in range(frames_per_row):
                x = frame_index * self.frame_width
                y = self.current_row * self.frame_height
                frame = end_sheet.subsurface(pygame.Rect(x, y, self.frame_width, self.frame_height))
                end_frames.append(frame)

            self.end_sprite = end_frames  # Store as LIST of frames
            print(f"Loaded {len(end_frames)} end sprites for direction {self.direction}")

        except Exception as e:
            print(f"Error loading end beam sprite: {e}")
            self.end_sprite = None

        # Check if we have at least some sprites
        self.use_sprites = any([self.begin_sprite, self.middle_sprite, self.end_sprite])

        if not self.use_sprites:
            print("No beam sprites loaded, using fallback rendering")

    def calculate_scaled_dimensions(self):
        """Calculate scaled dimensions for all sprites - handle lists of frames"""
        # Handle begin sprite (might be a list)
        if self.begin_sprite and isinstance(self.begin_sprite, list) and len(self.begin_sprite) > 0:
            # Use first frame to determine dimensions
            begin_rect = self.begin_sprite[0].get_rect()
            self.begin_width_scaled = int(begin_rect.width * self.scale)
            self.begin_height_scaled = int(begin_rect.height * self.scale)

            # Scale all frames in the list
            self.begin_sprite_scaled = []
            for frame in self.begin_sprite:
                scaled_frame = pygame.transform.scale(frame, (self.begin_width_scaled, self.begin_height_scaled))
                self.begin_sprite_scaled.append(scaled_frame)

            print(
                f"Begin sprite: {len(self.begin_sprite)} frames scaled to {self.begin_width_scaled}x{self.begin_height_scaled}")
        elif self.begin_sprite:
            # Single sprite (fallback)
            begin_rect = self.begin_sprite.get_rect()
            self.begin_width_scaled = int(begin_rect.width * self.scale)
            self.begin_height_scaled = int(begin_rect.height * self.scale)
            self.begin_sprite_scaled = pygame.transform.scale(
                self.begin_sprite,
                (self.begin_width_scaled, self.begin_height_scaled)
            )
        else:
            # Use fallback scaled dimensions
            self.begin_width_scaled = int(16 * self.scale)
            self.begin_height_scaled = int(16 * self.scale)

        # Handle middle sprite
        if self.middle_sprite and isinstance(self.middle_sprite, list) and len(self.middle_sprite) > 0:
            middle_rect = self.middle_sprite[0].get_rect()
            self.middle_width_scaled = int(middle_rect.width * self.scale)
            self.middle_height_scaled = int(middle_rect.height * self.scale)

            self.middle_sprite_scaled = []
            for frame in self.middle_sprite:
                scaled_frame = pygame.transform.scale(frame, (self.middle_width_scaled, self.middle_height_scaled))
                self.middle_sprite_scaled.append(scaled_frame)

            print(
                f"Middle sprite: {len(self.middle_sprite)} frames scaled to {self.middle_width_scaled}x{self.middle_height_scaled}")
        elif self.middle_sprite:
            middle_rect = self.middle_sprite.get_rect()
            self.middle_width_scaled = int(middle_rect.width * self.scale)
            self.middle_height_scaled = int(middle_rect.height * self.scale)
            self.middle_sprite_scaled = pygame.transform.scale(
                self.middle_sprite,
                (self.middle_width_scaled, self.middle_height_scaled)
            )
        else:
            self.middle_width_scaled = int(6 * self.scale)
            self.middle_height_scaled = int(6 * self.scale)

        # Handle end sprite
        if self.end_sprite and isinstance(self.end_sprite, list) and len(self.end_sprite) > 0:
            end_rect = self.end_sprite[0].get_rect()
            self.end_width_scaled = int(end_rect.width * self.scale)
            self.end_height_scaled = int(end_rect.height * self.scale)

            self.end_sprite_scaled = []
            for frame in self.end_sprite:
                scaled_frame = pygame.transform.scale(frame, (self.end_width_scaled, self.end_height_scaled))
                self.end_sprite_scaled.append(scaled_frame)

            print(
                f"End sprite: {len(self.end_sprite)} frames scaled to {self.end_width_scaled}x{self.end_height_scaled}")
        elif self.end_sprite:
            end_rect = self.end_sprite.get_rect()
            self.end_width_scaled = int(end_rect.width * self.scale)
            self.end_height_scaled = int(end_rect.height * self.scale)
            self.end_sprite_scaled = pygame.transform.scale(
                self.end_sprite,
                (self.end_width_scaled, self.end_height_scaled)
            )
        else:
            self.end_width_scaled = int(16 * self.scale)
            self.end_height_scaled = int(16 * self.scale)

    def update(self, dt):
        # Update animation
        if self.use_sprites:
            self.frame_timer += dt
            if self.frame_timer >= self.frame_duration:
                self.frame_timer = 0
                self.current_frame += 1

        # Grow beam
        if self.length < self.max_length:
            self.length += self.grow_speed * dt
            if self.length > self.max_length:
                self.length = self.max_length

    def draw(self, screen, camera, colors):
        if not self.active or self.length <= 0:
            return

        from config.settings import RENDER_SCALE

        # Base position (convert world to screen coordinates)
        base_screen_x = (self.x * RENDER_SCALE) - camera.x
        base_screen_y = (self.y * RENDER_SCALE) - camera.y

        # Direction-dependent offsets (adjust as needed for your sprites)
        if self.direction == 'right':
            screen_x = base_screen_x - 15  # Offset to the right from center
            screen_y = base_screen_y - 5  # Offset upward from center
        elif self.direction == 'left':
            screen_x = base_screen_x + 15  # Offset to the left from center
            screen_y = base_screen_y - 5  # Offset upward from center
        elif self.direction == 'down':
            screen_x = base_screen_x   # Offset left from center
            screen_y = base_screen_y - 25  # Offset downward from center
        elif self.direction == 'up':
            screen_x = base_screen_x  # Offset left from center
            screen_y = base_screen_y + 12  # Offset upward from center


        if self.use_sprites:
            self._draw_with_sprites(screen, screen_x, screen_y)
        else:
            self._draw_fallback(screen, screen_x, screen_y, colors)

    def _draw_with_sprites(self, screen, screen_x, screen_y):
        """Draw beam using three-part sprite system"""
        if self.direction == 'down':
            self._draw_vertical_down(screen, screen_x, screen_y)
        elif self.direction == 'left':
            self._draw_horizontal_left(screen, screen_x, screen_y)
        elif self.direction == 'right':
            self._draw_horizontal_right(screen, screen_x, screen_y)
        elif self.direction == 'up':
            self._draw_vertical_up(screen, screen_x, screen_y)

    def _draw_vertical_down(self, screen, screen_x, screen_y):
        """Draw vertical beam pointing down - with animation frames"""
        current_length = 0

        # 1. Draw begin sprite at player position
        if self.begin_sprite_scaled and len(self.begin_sprite_scaled) > 0:
            # Get current animation frame
            frame_index = self.current_frame % len(self.begin_sprite_scaled)
            begin_frame = self.begin_sprite_scaled[frame_index]

            begin_rect = begin_frame.get_rect(midtop=(screen_x, screen_y))
            screen.blit(begin_frame, begin_rect)
            current_length = self.begin_height_scaled // 2

        # 2. Draw middle sections
        if self.middle_sprite_scaled and len(self.middle_sprite_scaled) > 0:
            middle_index = 0
            while current_length < self.length - self.end_height_scaled // 2:
                # Get current animation frame for this middle segment
                frame_index = (self.current_frame + middle_index) % len(self.middle_sprite_scaled)
                middle_frame = self.middle_sprite_scaled[frame_index]

                middle_y = screen_y + current_length
                middle_rect = middle_frame.get_rect(midtop=(screen_x, middle_y))
                screen.blit(middle_frame, middle_rect)
                current_length += self.middle_height_scaled
                middle_index += 1

        # 3. Draw end sprite at the tip
        if self.end_sprite_scaled and len(self.end_sprite_scaled) > 0:
            # Get current animation frame
            frame_index = self.current_frame % len(self.end_sprite_scaled)
            end_frame = self.end_sprite_scaled[frame_index]

            end_y = screen_y + current_length
            end_rect = end_frame.get_rect(midtop=(screen_x, end_y))
            screen.blit(end_frame, end_rect)

    def _draw_vertical_up(self, screen, screen_x, screen_y):
        """Draw vertical beam pointing up - with animation frames"""
        current_length = 0

        # 1. Draw end sprite at the tip
        if self.end_sprite_scaled and len(self.end_sprite_scaled) > 0:
            # Get current animation frame
            frame_index = self.current_frame % len(self.end_sprite_scaled)
            end_frame = self.end_sprite_scaled[frame_index]

            end_y = screen_y - self.length + current_length
            end_rect = end_frame.get_rect(midbottom=(screen_x, end_y))
            screen.blit(end_frame, end_rect)
            current_length += self.end_height_scaled

        # 2. Draw middle sections (tiled upward)
        if self.middle_sprite_scaled and len(self.middle_sprite_scaled) > 0:
            middle_index = 0
            while current_length < self.length - self.begin_height_scaled // 2:
                # Get current animation frame for this middle segment
                frame_index = (self.current_frame + middle_index) % len(self.middle_sprite_scaled)
                middle_frame = self.middle_sprite_scaled[frame_index]

                middle_y = screen_y - current_length
                middle_rect = middle_frame.get_rect(midbottom=(screen_x, middle_y))
                screen.blit(middle_frame, middle_rect)
                current_length += self.middle_height_scaled
                middle_index += 1

        # 3. Draw begin sprite at player position
        if self.begin_sprite_scaled and len(self.begin_sprite_scaled) > 0:
            # Get current animation frame
            frame_index = self.current_frame % len(self.begin_sprite_scaled)
            begin_frame = self.begin_sprite_scaled[frame_index]

            begin_rect = begin_frame.get_rect(midbottom=(screen_x, screen_y))
            screen.blit(begin_frame, begin_rect)

    def _draw_horizontal_right(self, screen, screen_x, screen_y):
        """Draw horizontal beam pointing right - with animation frames"""
        current_length = 0

        # 1. Draw begin sprite at player position
        if self.begin_sprite_scaled and len(self.begin_sprite_scaled) > 0:
            # Get current animation frame
            frame_index = self.current_frame % len(self.begin_sprite_scaled)
            begin_frame = self.begin_sprite_scaled[frame_index]

            begin_rect = begin_frame.get_rect(midleft=(screen_x, screen_y))
            screen.blit(begin_frame, begin_rect)
            current_length = self.begin_width_scaled // 2

        # 2. Draw middle sections
        if self.middle_sprite_scaled and len(self.middle_sprite_scaled) > 0:
            middle_index = 0
            while current_length < self.length - self.end_width_scaled // 2:
                # Get current animation frame for this middle segment
                frame_index = (self.current_frame + middle_index) % len(self.middle_sprite_scaled)
                middle_frame = self.middle_sprite_scaled[frame_index]

                middle_x = screen_x + current_length
                middle_rect = middle_frame.get_rect(midleft=(middle_x, screen_y))
                screen.blit(middle_frame, middle_rect)
                current_length += self.middle_width_scaled
                middle_index += 1

        # 3. Draw end sprite at the tip
        if self.end_sprite_scaled and len(self.end_sprite_scaled) > 0:
            # Get current animation frame
            frame_index = self.current_frame % len(self.end_sprite_scaled)
            end_frame = self.end_sprite_scaled[frame_index]

            end_x = screen_x + current_length
            end_rect = end_frame.get_rect(midleft=(end_x, screen_y))
            screen.blit(end_frame, end_rect)

    def _draw_horizontal_left(self, screen, screen_x, screen_y):
        """Draw horizontal beam pointing left - with animation frames"""
        current_length = 0

        # 1. Draw begin sprite at player position
        if self.begin_sprite_scaled and len(self.begin_sprite_scaled) > 0:
            # Get current animation frame
            frame_index = self.current_frame % len(self.begin_sprite_scaled)
            begin_frame = self.begin_sprite_scaled[frame_index]

            begin_rect = begin_frame.get_rect(midright=(screen_x, screen_y))
            screen.blit(begin_frame, begin_rect)
            current_length = self.begin_width_scaled // 2

        # 2. Draw middle sections
        if self.middle_sprite_scaled and len(self.middle_sprite_scaled) > 0:
            middle_index = 0
            while current_length < self.length - self.end_width_scaled // 2:
                # Get current animation frame for this middle segment
                frame_index = (self.current_frame + middle_index) % len(self.middle_sprite_scaled)
                middle_frame = self.middle_sprite_scaled[frame_index]

                middle_x = screen_x - current_length
                middle_rect = middle_frame.get_rect(midright=(middle_x, screen_y))
                screen.blit(middle_frame, middle_rect)
                current_length += self.middle_width_scaled
                middle_index += 1

        # 3. Draw end sprite at the tip
        if self.end_sprite_scaled and len(self.end_sprite_scaled) > 0:
            # Get current animation frame
            frame_index = self.current_frame % len(self.end_sprite_scaled)
            end_frame = self.end_sprite_scaled[frame_index]

            end_x = screen_x - current_length
            end_rect = end_frame.get_rect(midright=(end_x, screen_y))
            screen.blit(end_frame, end_rect)

    def _draw_fallback(self, screen, screen_x, screen_y, colors):
        """Fallback drawing using rectangles"""
        beam_width = self.width

        if self.direction == 'up':
            pygame.draw.rect(screen, colors['CYAN'],
                             (screen_x - beam_width // 2, screen_y - self.length, beam_width, self.length))
            pygame.draw.rect(screen, colors['YELLOW'],
                             (screen_x - beam_width // 2 - 5, screen_y - self.length, beam_width + 10, self.length), 3)
        elif self.direction == 'down':
            pygame.draw.rect(screen, colors['CYAN'], (screen_x - beam_width // 2, screen_y, beam_width, self.length))
            pygame.draw.rect(screen, colors['YELLOW'],
                             (screen_x - beam_width // 2 - 5, screen_y, beam_width + 10, self.length), 3)
        elif self.direction == 'left':
            pygame.draw.rect(screen, colors['CYAN'],
                             (screen_x - self.length, screen_y - beam_width // 2, self.length, beam_width))
            pygame.draw.rect(screen, colors['YELLOW'],
                             (screen_x - self.length, screen_y - beam_width // 2 - 5, self.length, beam_width + 10), 3)
        elif self.direction == 'right':
            pygame.draw.rect(screen, colors['CYAN'], (screen_x, screen_y - beam_width // 2, self.length, beam_width))
            pygame.draw.rect(screen, colors['YELLOW'],
                             (screen_x, screen_y - beam_width // 2 - 5, self.length, beam_width + 10), 3)