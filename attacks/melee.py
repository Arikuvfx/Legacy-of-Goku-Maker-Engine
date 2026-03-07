import pygame
from config.settings import WHITE
from core.draw_layers import DrawLayer


class MeleeAttack:
    """
    Short-lived melee swing effect spawned when the player attacks.

    Draws a directional swish arc in front of the player for a brief
    duration, then deactivates.  Collision detection against this object
    is handled externally by the entity that owns it.
    """

    def __init__(self, x, y, direction):
        self.x         = x
        self.y         = y
        self.direction = direction  # 'up' | 'down' | 'left' | 'right'
        self.duration  = 0.2        # Seconds the attack remains active.
        self.timer     = 0
        self.active    = True
        self.size      = 40         # Length of the swish arc in screen pixels.
        self.owner     = None       # Set by the player when the attack is created.

        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self.y_sort     = False

    def get_sort_key(self):
        """Sort key for the layer manager (fixed layer, no y-sorting)."""
        return (self.draw_layer, 0)

    def update(self, dt):
        """
        Advance the attack timer and deactivate when the duration is up.

        Args:
            dt: Delta time in seconds.
        """
        self.timer += dt
        if self.timer >= self.duration:
            self.active = False

    def draw(self, screen, camera, colors):
        """
        Render the swish effect at the correct screen position.

        Draws five parallel lines that fan out from the player's hand
        position to give the impression of a sweeping strike.

        Args:
            screen: Pygame surface to draw onto.
            camera: Active Camera instance for world-to-screen conversion.
            colors: Colour dictionary (must contain 'WHITE').
        """
        if not self.active:
            return

        from config.settings import RENDER_SCALE

        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        offset = 35  # Distance from the player centre to the start of the arc.

        # Determine the start and end points of the swish based on direction.
        if self.direction == 'up':
            start_x = screen_x - self.size // 2
            start_y = screen_y - offset
            end_x   = screen_x + self.size // 2
            end_y   = screen_y - offset - self.size
        elif self.direction == 'down':
            start_x = screen_x - self.size // 2
            start_y = screen_y + offset
            end_x   = screen_x + self.size // 2
            end_y   = screen_y + offset + self.size
        elif self.direction == 'left':
            start_x = screen_x - offset
            start_y = screen_y - self.size // 2
            end_x   = screen_x - offset - self.size
            end_y   = screen_y + self.size // 2
        elif self.direction == 'right':
            start_x = screen_x + offset
            start_y = screen_y - self.size // 2
            end_x   = screen_x + offset + self.size
            end_y   = screen_y + self.size // 2

        # Draw five parallel lines spread across the swing arc.
        for i in range(5):
            offset_ratio = (i - 2) * 0.15

            if self.direction in ['up', 'down']:
                sx = start_x + offset_ratio * self.size
                ex = end_x   + offset_ratio * self.size
                sy = start_y
                ey = end_y
            else:
                sx = start_x
                ex = end_x
                sy = start_y + offset_ratio * self.size
                ey = end_y   + offset_ratio * self.size

            pygame.draw.line(screen, colors['WHITE'],
                             (int(sx), int(sy)), (int(ex), int(ey)), 3)