from config.settings import RENDER_SCALE


class Camera:
    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.x = 0
        self.y = 0

        # Camera shake properties
        self.shake_intensity = 0
        self.shake_duration = 0
        self.shake_timer = 0
        self.shake_offset_x = 0
        self.shake_offset_y = 0

    def start_shake(self, intensity=10, duration=0.3):
        """Start camera shake effect"""
        self.shake_intensity = intensity
        self.shake_duration = duration
        self.shake_timer = duration

    def update(self, target, world_width, world_height, dt=0):
        """
        Update camera to follow target

        target.x, target.y: WORLD coordinates
        world_width, world_height: WORLD dimensions
        camera.x, camera.y: SCREEN coordinates (for rendering formula)
        """
        # Update camera shake
        if self.shake_timer > 0:
            self.shake_timer -= dt
            import random
            # Shake decreases over time
            shake_amount = self.shake_intensity * (self.shake_timer / self.shake_duration)
            self.shake_offset_x = random.uniform(-shake_amount, shake_amount)
            self.shake_offset_y = random.uniform(-shake_amount, shake_amount)
        else:
            self.shake_offset_x = 0
            self.shake_offset_y = 0

        # Convert target WORLD position to SCREEN position
        target_screen_x = target.x * RENDER_SCALE
        target_screen_y = target.y * RENDER_SCALE

        # Center camera on target (with shake)
        self.x = target_screen_x - self.screen_width // 2 + self.shake_offset_x
        self.y = target_screen_y - self.screen_height // 2 + self.shake_offset_y

        # Keep camera within world bounds (convert world bounds to screen space)
        world_screen_width = world_width * RENDER_SCALE
        world_screen_height = world_height * RENDER_SCALE

        self.x = max(0, min(self.x, world_screen_width - self.screen_width))
        self.y = max(0, min(self.y, world_screen_height - self.screen_height))

    def apply(self, x, y):
        """
        Apply camera offset to world coordinates
        x, y: WORLD coordinates
        Returns: SCREEN coordinates
        """
        screen_x = (x * RENDER_SCALE) - self.x
        screen_y = (y * RENDER_SCALE) - self.y
        return screen_x, screen_y