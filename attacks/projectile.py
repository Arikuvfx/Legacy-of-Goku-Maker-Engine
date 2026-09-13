import pygame
from core.draw_layers import DrawLayer


class Projectile:
    def __init__(self, x, y, direction):
        self.x = x
        self.y = y
        self.direction = direction
        self.speed = 4  # Keep original speed
        self.radius = 8
        self.active = True

        # Set True by game.py wherever this projectile is consumed by an
        # actual collision (enemy, destructible stone, level gate) — as
        # opposed to simply expiring out of world bounds. Read by the
        # central removal loop in game.py to decide whether a
        # KiBlastHitEffect should be spawned at (self.x, self.y).
        self.hit_something = False

        # Animation
        self.frames = []
        self.current_frame = 0
        self.frame_timer = 0
        self.frame_duration = 0.1
        self.frame_width = 16
        self.frame_height = 16

        # Load spritesheet
        try:
            spritesheet = pygame.image.load('assets/sprites/attacks/ki_blast/ki_blast.png').convert_alpha()
            sheet_width = spritesheet.get_width()
            num_frames = sheet_width // self.frame_width

            for i in range(num_frames):
                frame = spritesheet.subsurface(
                    pygame.Rect(i * self.frame_width, 0, self.frame_width, self.frame_height)
                )
                self.frames.append(frame)

            if not self.frames:
                raise FileNotFoundError("No frames extracted")
        except:
            self.frames = []

        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self.y_sort = False

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def get_collision_rect(self):
        """Small square centered on (self.x, self.y), used to test against
        CollisionObject walls. Sized off self.radius so it roughly matches
        the fallback circle/sprite footprint."""
        size = self.radius * 2
        return pygame.Rect(int(self.x - self.radius), int(self.y - self.radius), size, size)

    def update(self, world_width, world_height, dt=0.016, collision_objects=None):
        # Once consumed by a collision (or expired), stop moving/animating —
        # otherwise this still-referenced-for-one-more-frame projectile
        # (see game.py's removal loop) would take one extra step past the
        # actual impact point before being removed, and any explosion
        # spawned at self.x/self.y would land slightly off from the hit.
        if not self.active:
            return

        # Update animation
        if self.frames:
            self.frame_timer += dt
            if self.frame_timer >= self.frame_duration:
                self.frame_timer = 0
                self.current_frame = (self.current_frame + 1) % len(self.frames)

        # Update position (keep original speed)
        if self.direction == 'up':
            self.y -= self.speed
        elif self.direction == 'down':
            self.y += self.speed
        elif self.direction == 'left':
            self.x -= self.speed
        elif self.direction == 'right':
            self.x += self.speed

        # Stop at walls (CollisionObject instances, e.g. from
        # CollisionObjectManager.get_collision_objects(room_name)) instead of
        # flying through them. Checked after moving so the hit position
        # (self.x, self.y) used for KiBlastHitEffect placement in game.py
        # lands right at the wall, same as the enemy-hit case.
        if collision_objects:
            my_rect = self.get_collision_rect()
            for wall in collision_objects:
                if not getattr(wall, 'active', True):
                    continue
                if my_rect.colliderect(wall.get_rect()):
                    self.active = False
                    self.hit_something = True
                    return

        # Deactivate if out of world bounds
        if self.x < 0 or self.x > world_width or self.y < 0 or self.y > world_height:
            self.active = False

    def draw(self, screen, camera, colors):
        if self.active:
            from config.settings import RENDER_SCALE
            screen_x = (self.x * RENDER_SCALE) - camera.x
            screen_y = (self.y * RENDER_SCALE) - camera.y

            if self.frames:
                current_sprite = self.frames[self.current_frame]

                # Scale sprite up to match RENDER_SCALE before rotating
                scaled_w = self.frame_width  * RENDER_SCALE
                scaled_h = self.frame_height * RENDER_SCALE
                scaled_sprite = pygame.transform.scale(current_sprite, (scaled_w, scaled_h))

                if self.direction == 'up':
                    rotated_sprite = pygame.transform.rotate(scaled_sprite, 0)
                elif self.direction == 'down':
                    rotated_sprite = pygame.transform.rotate(scaled_sprite, 180)
                elif self.direction == 'left':
                    rotated_sprite = pygame.transform.rotate(scaled_sprite, 90)
                elif self.direction == 'right':
                    rotated_sprite = pygame.transform.rotate(scaled_sprite, 270)
                else:
                    rotated_sprite = scaled_sprite

                sprite_rect = rotated_sprite.get_rect(center=(int(screen_x), int(screen_y)))
                screen.blit(rotated_sprite, sprite_rect)
            else:
                # Fallback circle also scaled up
                pygame.draw.circle(screen, colors['CYAN'],   (int(screen_x), int(screen_y)), self.radius * RENDER_SCALE)
                pygame.draw.circle(screen, colors['YELLOW'], (int(screen_x), int(screen_y)), (self.radius - 3) * RENDER_SCALE)


class KiBlastHitEffect:
    """One-shot explosion visual shown where a ki blast Projectile connects
    with an enemy. Holds explosion.png in place at the hit position for
    `duration` seconds, then deactivates itself so game.py's per-frame
    cleanup (self.ki_blast_hit_effects) removes it — same pattern as
    GenkidamaHitEffect/BurningHitEffect."""

    def __init__(self, x, y, duration=0.3):
        self.x = x
        self.y = y
        self.timer = 0.0
        self.duration = duration
        self.active = True

        try:
            self.sprite = pygame.image.load('assets/sprites/attacks/ki_blast/explosion.png').convert_alpha()
        except Exception:
            self.sprite = None

        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self.y_sort = False

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def update(self, dt=0.016):
        self.timer += dt
        if self.timer >= self.duration:
            self.active = False

    def draw(self, screen, camera, colors):
        if not self.active:
            return

        from config.settings import RENDER_SCALE
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.sprite:
            scaled_w = self.sprite.get_width() * RENDER_SCALE
            scaled_h = self.sprite.get_height() * RENDER_SCALE
            scaled_sprite = pygame.transform.scale(self.sprite, (scaled_w, scaled_h))
            sprite_rect = scaled_sprite.get_rect(center=(int(screen_x), int(screen_y)))
            screen.blit(scaled_sprite, sprite_rect)
        else:
            # Fallback if explosion.png fails to load
            pygame.draw.circle(screen, colors['YELLOW'], (int(screen_x), int(screen_y)), 10 * RENDER_SCALE)
            pygame.draw.circle(screen, colors['CYAN'],   (int(screen_x), int(screen_y)), 6 * RENDER_SCALE)