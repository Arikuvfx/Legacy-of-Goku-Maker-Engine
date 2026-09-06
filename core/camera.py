import random
from config.settings import RENDER_SCALE


class Camera:
    """
    Tracks and centres the viewport on a target entity.

    CHANGED FOR GPU RENDERING: apply() still exists unchanged for any
    code that just needs a screen-space (x, y) pair. NEW: apply_rect()
    is what draw() methods should switch to when they currently do

        screen_x, screen_y = camera.apply(obj.x, obj.y)
        scaled = pygame.transform.scale(sprite, (w * RENDER_SCALE, h * RENDER_SCALE))
        screen.blit(scaled, (screen_x, screen_y))

    That pattern is exactly what caused the room editor's zoom-out
    blowup: a CPU pixel resample (transform.scale) on every sprite,
    every frame, at a size that grows as zoom shrinks. apply_rect()
    replaces the whole thing with one call:

        dst_rect = camera.apply_rect(obj.x, obj.y, sprite_w, sprite_h)
        screen.blit_scaled(sprite, dst_rect)   # gpu_renderer.GPUScreen

    No transform.scale, no intermediate surface. self.zoom (already
    present below, previously only consumed by the room editor's
    fit-to-screen overview via getattr-with-fallback) is now the single
    place zoom lives -- the room editor should set camera.zoom directly
    from its Ctrl+scroll editor_zoom instead of building its own
    offscreen canvas and shrinking it afterwards.
    """

    def __init__(self, screen_width, screen_height):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.x = 0
        self.y = 0

        self._true_x = 0.0
        self._true_y = 0.0

        # No longer just an editor-only convenience for callers that
        # bypass apply() -- this is now the canonical zoom value that
        # apply_rect() reads on every call. Room editor: set this
        # directly from editor_zoom each frame; ordinary gameplay never
        # touches it, same as before.
        self.zoom = 1.0

        self._lerp_active  = True
        self._lerp_speed   = 4.0

        self._needs_snap = True
        self.locked = False

        self.shake_intensity = 0
        self.shake_duration  = 0
        self.shake_timer     = 0
        self.shake_offset_x  = 0
        self.shake_offset_y  = 0

    def start_shake(self, intensity=10, duration=0.3):
        self.shake_intensity = intensity
        self.shake_duration  = duration
        self.shake_timer     = duration

    def snap(self):
        self._needs_snap = True

    def update(self, target, world_width, world_height, dt=0):
        if self.shake_timer > 0:
            self.shake_timer -= dt
            shake_amount = self.shake_intensity * (self.shake_timer / self.shake_duration) if self.shake_duration > 0 else 0.0
            self.shake_offset_x = random.uniform(-shake_amount, shake_amount)
            self.shake_offset_y = random.uniform(-shake_amount, shake_amount)
        else:
            self.shake_offset_x = 0
            self.shake_offset_y = 0

        if self.locked:
            return

        target_screen_x = target.x * RENDER_SCALE
        target_screen_y = target.y * RENDER_SCALE

        desired_x = target_screen_x - self.screen_width  // 2
        desired_y = target_screen_y - self.screen_height // 2

        if self._lerp_active and dt > 0 and not self._needs_snap:
            t = 1.0 - (1.0 / (1.0 + self._lerp_speed * dt))
            self._true_x = self._true_x + (desired_x - self._true_x) * t
            self._true_y = self._true_y + (desired_y - self._true_y) * t
        else:
            self._true_x = desired_x
            self._true_y = desired_y
            self._needs_snap = False

        world_screen_width  = world_width  * RENDER_SCALE
        world_screen_height = world_height * RENDER_SCALE

        if world_screen_width <= self.screen_width:
            self._true_x = (world_screen_width - self.screen_width) // 2
        else:
            self._true_x = max(0, min(self._true_x, world_screen_width - self.screen_width))

        if world_screen_height <= self.screen_height:
            self._true_y = (world_screen_height - self.screen_height) // 2
        else:
            self._true_y = max(0, min(self._true_y, world_screen_height - self.screen_height))

        self.x = round(self._true_x + self.shake_offset_x)
        self.y = round(self._true_y + self.shake_offset_y)

    def apply(self, x, y):
        """Unchanged: screen-space (x, y) for callers that only need a
        position, not a scaled draw rect (e.g. mouse-coordinate math,
        collision debug lines)."""
        screen_x = (x * RENDER_SCALE - self.x) * self.zoom
        screen_y = (y * RENDER_SCALE - self.y) * self.zoom
        return screen_x, screen_y

    def apply_rect(self, world_x, world_y, sprite_w, sprite_h):
        """
        World position + a sprite's native pixel size -> the destination
        pygame.Rect to hand to GPUScreen.blit_scaled(). This is the
        replacement for "compute screen_x/y, then pygame.transform.scale
        the sprite, then blit" -- the GPU does the scaling as part of
        the draw, at the cost of one Rect, regardless of zoom level.

        sprite_w/sprite_h should be the sprite's UNSCALED native size
        (before RENDER_SCALE) -- same inputs you'd currently pass to
        pygame.transform.scale(sprite, (w * RENDER_SCALE, h * RENDER_SCALE)).
        """
        screen_x, screen_y = self.apply(world_x, world_y)
        scaled_w = sprite_w * RENDER_SCALE * self.zoom
        scaled_h = sprite_h * RENDER_SCALE * self.zoom
        import pygame
        return pygame.Rect(round(screen_x), round(screen_y), round(scaled_w), round(scaled_h))