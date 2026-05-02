import random
from config.settings import RENDER_SCALE


class Camera:
    """
    Tracks and centres the viewport on a target entity.

    Converts world-space coordinates to screen-space coordinates using the
    RENDER_SCALE constant.  Supports a camera-shake effect that gradually
    dampens over its duration.

    Coordinate contract
    -------------------
    - camera.x / camera.y  — screen-space offset used in rendering.
    - target.x / target.y  — world-space position of the tracked entity.
    - world_width / world_height — world-space room dimensions.
    """

    def __init__(self, screen_width, screen_height):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.x = 0
        self.y = 0

        # Post-cutscene lerp blend state
        self._lerp_active  = False
        self._lerp_speed   = 5.0   # higher = faster blend (good range: 3–8)

        # Camera shake state
        self.shake_intensity = 0
        self.shake_duration  = 0
        self.shake_timer     = 0
        self.shake_offset_x  = 0
        self.shake_offset_y  = 0

    def start_shake(self, intensity=10, duration=0.3):
        """
        Begin a camera-shake effect.

        Args:
            intensity: Maximum pixel displacement at the start of the shake.
            duration:  Duration of the shake in seconds.
        """
        self.shake_intensity = intensity
        self.shake_duration  = duration
        self.shake_timer     = duration

    def update(self, target, world_width, world_height, dt=0):
        """
        Reposition the camera to follow *target*.

        For rooms narrower or shorter than the screen the camera stays
        centred on the room.  For larger rooms it clamps so the viewport
        never shows empty space outside the room boundary.

        Args:
            target:       Entity with .x and .y world-space attributes.
            world_width:  Room width in world units.
            world_height: Room height in world units.
            dt:           Delta time in seconds (used for shake decay).
        """
        # Update the shake offset, decaying intensity over time.
        if self.shake_timer > 0:
            self.shake_timer -= dt
            shake_amount = self.shake_intensity * (self.shake_timer / self.shake_duration) if self.shake_duration > 0 else 0.0
            self.shake_offset_x = random.uniform(-shake_amount, shake_amount)
            self.shake_offset_y = random.uniform(-shake_amount, shake_amount)
        else:
            self.shake_offset_x = 0
            self.shake_offset_y = 0

        # Centre the viewport on the target (in screen space).
        target_screen_x = target.x * RENDER_SCALE
        target_screen_y = target.y * RENDER_SCALE

        desired_x = target_screen_x - self.screen_width  // 2
        desired_y = target_screen_y - self.screen_height // 2

        if self._lerp_active and dt > 0:
            t = 1.0 - (1.0 / (1.0 + self._lerp_speed * dt))
            new_x = self.x + (desired_x - self.x) * t
            new_y = self.y + (desired_y - self.y) * t
            # Stop lerping once we're close enough
            if abs(desired_x - new_x) < 0.5 and abs(desired_y - new_y) < 0.5:
                self._lerp_active = False
            self.x = new_x + self.shake_offset_x
            self.y = new_y + self.shake_offset_y
        else:
            self.x = desired_x + self.shake_offset_x
            self.y = desired_y + self.shake_offset_y

        # Clamp to room bounds, or centre for rooms smaller than the screen.
        world_screen_width  = world_width  * RENDER_SCALE
        world_screen_height = world_height * RENDER_SCALE

        if world_screen_width <= self.screen_width:
            self.x = (world_screen_width - self.screen_width) // 2 + self.shake_offset_x
        else:
            self.x = max(0, min(self.x, world_screen_width - self.screen_width))

        if world_screen_height <= self.screen_height:
            self.y = (world_screen_height - self.screen_height) // 2 + self.shake_offset_y
        else:
            self.y = max(0, min(self.y, world_screen_height - self.screen_height))

    def apply(self, x, y):
        """
        Convert world-space coordinates to screen-space coordinates.

        Args:
            x: World-space X position.
            y: World-space Y position.

        Returns:
            Tuple (screen_x, screen_y).
        """
        screen_x = (x * RENDER_SCALE) - self.x
        screen_y = (y * RENDER_SCALE) - self.y
        return screen_x, screen_y