import pygame
from core.draw_layers import DrawLayer


class bullet_projectile:
    """
    Straight-line projectile fired by the Gunner enemy variant.

    Travels freely in any dx/dy direction (not locked to the four
    cardinal directions) and deactivates on player hit or when it
    leaves the world bounds.

    Sprite: assets/sprites/attacks/bullet/bullet.png (animated spritesheet).
    The base sprite faces LEFT; rotation is applied at draw time.
    """

    def __init__(self, x, y, dx, dy, speed, damage, direction='down'):
        self.x         = x
        self.y         = y
        self.dx        = dx        # Normalised direction vector (x component).
        self.dy        = dy        # Normalised direction vector (y component).
        self.speed     = speed     # World units per second.
        self.damage    = damage
        self.direction = direction # Cardinal / diagonal facing string for sprite rotation.
        self.radius    = 5
        self.active    = True

        # Animation
        self.frames         = []
        self.current_frame  = 0
        self.frame_timer    = 0
        self.frame_duration = 0.08
        self.frame_width    = 16
        self.frame_height   = 8

        try:
            spritesheet = pygame.image.load(
                'assets/sprites/attacks/bullet/bullet.png'
            ).convert_alpha()
            num_frames = spritesheet.get_width() // self.frame_width
            for i in range(num_frames):
                frame = spritesheet.subsurface(
                    pygame.Rect(i * self.frame_width, 0, self.frame_width, self.frame_height)
                )
                self.frames.append(frame)
        except Exception:
            self.frames = []

        # Render in front of most game objects; no y-sorting needed.
        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self.y_sort     = False

    def get_sort_key(self):
        """Sort key for the layer manager (fixed layer, no y-sorting)."""
        return (self.draw_layer, 0)

    def update(self, world_width, world_height, dt):
        """
        Advance the bullet one simulation step.

        Moves the bullet along its direction vector and deactivates it
        if it exits the world bounds.

        Args:
            world_width:  Width of the current room in world units.
            world_height: Height of the current room in world units.
            dt:           Delta time in seconds.
        """
        if self.frames:
            self.frame_timer += dt
            if self.frame_timer >= self.frame_duration:
                self.frame_timer   = 0
                self.current_frame = (self.current_frame + 1) % len(self.frames)

        self.x += self.dx * self.speed * dt
        self.y += self.dy * self.speed * dt

        if self.x < 0 or self.x > world_width or self.y < 0 or self.y > world_height:
            self.active = False

    def check_collision_with_player(self, player):
        """
        Test whether this bullet has hit *player*.

        On a hit, applies damage and knockback in the bullet's travel
        direction, then deactivates the bullet.

        Returns:
            True if the player was hit, False otherwise.
        """
        dx      = self.x - player.x
        dy      = self.y - player.y
        dist_sq = dx * dx + dy * dy
        hit_radius = self.radius + max(player.width, player.height) // 2

        if dist_sq < hit_radius * hit_radius:
            player.take_damage(self.damage, self.dx, self.dy, ignore_invulnerability=True)
            self.active = False
            return True

        return False

    def draw(self, screen, camera, colors):
        """
        Render the bullet at its current world position.

        The sprite is rotated to match the bullet's travel direction.
        Falls back to a small yellow circle if the spritesheet failed to load.

        Args:
            screen: Pygame surface to draw onto.
            camera: Active Camera instance for world-to-screen conversion.
            colors: Colour dictionary (used by the fallback renderer).
        """
        if not self.active:
            return

        from config.settings import RENDER_SCALE
        import math

        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.frames:
            frame = self.frames[self.current_frame]

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
                # Derive angle from the direction vector for diagonal shots.
                angle = -math.degrees(math.atan2(self.dy, self.dx)) + 180

            rotated = pygame.transform.rotate(frame, angle)
            rect    = rotated.get_rect(center=(int(screen_x), int(screen_y)))
            screen.blit(rotated, rect)
        else:
            pygame.draw.circle(screen, colors.get('YELLOW', (255, 255, 0)),
                               (int(screen_x), int(screen_y)), self.radius)