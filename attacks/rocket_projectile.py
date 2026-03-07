import os
import math
import pygame
from core.draw_layers import DrawLayer


class rocket_projectile:
    """
    Straight-line projectile fired by the RocketLauncher enemy variant.

    Behaves identically to bullet_projectile but uses a single static
    sprite instead of an animation.  The base sprite faces LEFT; rotation
    is applied at draw time.

    Sprite: assets/sprites/attacks/rocket/rocket.png
    """

    def __init__(self, x, y, dx, dy, speed, damage, direction='left'):
        self.x         = x
        self.y         = y
        self.dx        = dx       # Normalised direction vector (x component).
        self.dy        = dy       # Normalised direction vector (y component).
        self.speed     = speed    # World units per second.
        self.damage    = damage
        self.direction = direction # Cardinal / diagonal facing string for sprite rotation.
        self.radius    = 8
        self.active    = True

        self.sprite      = None
        sprite_path = 'assets/sprites/attacks/rocket/rocket.png'
        try:
            self.sprite = pygame.image.load(sprite_path).convert_alpha()
        except (pygame.error, FileNotFoundError):
            self.sprite = None

        # Render in front of most game objects; no y-sorting needed.
        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self.y_sort     = False

    def get_sort_key(self):
        """Sort key for the layer manager (fixed layer, no y-sorting)."""
        return (self.draw_layer, 0)

    def update(self, world_width, world_height, dt):
        """
        Advance the rocket one simulation step.

        Moves the rocket along its direction vector and deactivates it
        if it exits the world bounds.

        Args:
            world_width:  Width of the current room in world units.
            world_height: Height of the current room in world units.
            dt:           Delta time in seconds.
        """
        self.x += self.dx * self.speed * dt
        self.y += self.dy * self.speed * dt

        if self.x < 0 or self.x > world_width or self.y < 0 or self.y > world_height:
            self.active = False

    def check_collision_with_player(self, player):
        """
        Test whether this rocket has hit *player*.

        On a hit, applies damage and knockback in the rocket's travel
        direction, then deactivates the rocket.

        Returns:
            True if the player was hit, False otherwise.
        """
        dx      = self.x - player.x
        dy      = self.y - player.y
        dist_sq = dx * dx + dy * dy
        hit_radius = self.radius + max(player.width, player.height) // 2

        if dist_sq < hit_radius * hit_radius:
            player.take_damage(self.damage, self.dx, self.dy)
            self.active = False
            return True

        return False

    def draw(self, screen, camera, colors):
        """
        Render the rocket at its current world position.

        The sprite is rotated to match the rocket's travel direction.
        Falls back to an orange circle if the sprite failed to load.

        Args:
            screen: Pygame surface to draw onto.
            camera: Active Camera instance for world-to-screen conversion.
            colors: Colour dictionary (used by the fallback renderer).
        """
        if not self.active:
            return

        from config.settings import RENDER_SCALE

        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.sprite:
            # Base sprite faces LEFT; pygame rotates counter-clockwise.
            if self.direction == 'left':
                angle = 0
            elif self.direction == 'right':
                angle = 180
            elif self.direction == 'up':
                angle = 270
            elif self.direction == 'down':
                angle = 90
            else:
                angle = -math.degrees(math.atan2(self.dy, self.dx)) + 180

            rotated = pygame.transform.rotate(self.sprite, angle)
            rect    = rotated.get_rect(center=(int(screen_x), int(screen_y)))
            screen.blit(rotated, rect)
        else:
            pygame.draw.circle(screen, colors.get('ORANGE', (255, 140, 0)),
                               (int(screen_x), int(screen_y)), self.radius)