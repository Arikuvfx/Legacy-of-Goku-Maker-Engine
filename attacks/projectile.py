import pygame
from core.draw_layers import DrawLayer


class Projectile:
    def __init__(self, x, y, direction):
        self.x = x
        self.y = y
        self.direction = direction
        self.speed = 4
        self.radius = 8  # Keep for collision detection
        self.active = True

        # Animation
        self.frames = []
        self.current_frame = 0
        self.frame_timer = 0
        self.frame_duration = 0.5  # Time per frame in seconds
        self.frame_width = 24  # Width of each frame in the spritesheet
        self.frame_height = 8  # Height of each frame in the spritesheet

        # Load spritesheet
        try:
            spritesheet = pygame.image.load('assets/sprites/attacks/ki_blast/ki_blast.png').convert_alpha()

            # Calculate number of frames (assumes horizontal spritesheet)
            sheet_width = spritesheet.get_width()
            num_frames = sheet_width // self.frame_width

            # Extract frames from spritesheet
            for i in range(num_frames):
                frame = spritesheet.subsurface(
                    pygame.Rect(i * self.frame_width, 0, self.frame_width, self.frame_height)
                )
                self.frames.append(frame)

            if not self.frames:
                raise FileNotFoundError("No frames extracted")
        except:
            # Fallback if spritesheet not found
            self.frames = []

        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self.y_sort = False

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def update(self, world_width, world_height, dt=0.016):
        # Update animation
        if self.frames:
            self.frame_timer += dt
            if self.frame_timer >= self.frame_duration:
                self.frame_timer = 0
                self.current_frame = (self.current_frame + 1) % len(self.frames)

        # Update position
        if self.direction == 'up':
            self.y -= self.speed
        elif self.direction == 'down':
            self.y += self.speed
        elif self.direction == 'left':
            self.x -= self.speed
        elif self.direction == 'right':
            self.x += self.speed

        # Deactivate if out of world bounds
        if self.x < 0 or self.x > world_width or self.y < 0 or self.y > world_height:
            self.active = False

    def draw(self, screen, camera, colors):
        if self.active:
            screen_x = self.x - camera.x
            screen_y = self.y - camera.y

            if self.frames:
                current_sprite = self.frames[self.current_frame]

                # Rotate sprite based on direction
                if self.direction == 'up':
                    rotated_sprite = pygame.transform.rotate(current_sprite, 0)
                elif self.direction == 'down':
                    rotated_sprite = pygame.transform.rotate(current_sprite, 180)
                elif self.direction == 'left':
                    rotated_sprite = pygame.transform.rotate(current_sprite, 90)
                elif self.direction == 'right':
                    rotated_sprite = pygame.transform.rotate(current_sprite, 270)
                else:
                    rotated_sprite = current_sprite

                # Center the sprite on the position
                sprite_rect = rotated_sprite.get_rect(center=(int(screen_x), int(screen_y)))
                screen.blit(rotated_sprite, sprite_rect)
            else:
                # Fallback drawing
                pygame.draw.circle(screen, colors['CYAN'], (int(screen_x), int(screen_y)), self.radius)
                pygame.draw.circle(screen, colors['YELLOW'], (int(screen_x), int(screen_y)), self.radius - 3)