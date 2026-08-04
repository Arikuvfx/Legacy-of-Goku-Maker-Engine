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

        # Internal, un-rounded camera position. The exponential ease below
        # needs a continuous float to accumulate against each frame — if we
        # fed it the rounded/shaken self.x/self.y instead, snapping to whole
        # pixels every frame would re-inject noise into the ease itself.
        # self.x/self.y (set at the end of update()) become the *rendered*
        # position: this true position plus shake, rounded once, so every
        # consumer (player sprite, shadow, anything calling camera.apply)
        # works off the exact same integer offset instead of each
        # independently truncating a moving sub-pixel value.
        self._true_x = 0.0
        self._true_y = 0.0

        # Smooth-follow state — the camera always eases toward the target
        # rather than snapping to it. This is what gives walking/running a
        # slight, deliberate follow-lag that catches up once the player
        # stops. _lerp_active is kept only so external code (e.g. the
        # cutscene runtime) can still force an instant snap for one frame
        # when it needs to reposition the camera directly; day-to-day
        # gameplay follow no longer depends on it being toggled on.
        self._lerp_active  = True
        self._lerp_speed   = 4.0   # higher = faster catch-up (good range: 3–8)

        # When True the camera position is frozen — the player is no longer tracked.
        # Used during the world-map jump sequence so the camera doesn't follow
        # the player off-screen.
        self.locked = False

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

        # When locked the camera doesn't track the target — it stays exactly
        # where it is.  Shake still runs above so an impact on the same frame
        # as the lock isn't silently dropped.
        if self.locked:
            return

        # Centre the viewport on the target (in screen space).
        target_screen_x = target.x * RENDER_SCALE
        target_screen_y = target.y * RENDER_SCALE

        desired_x = target_screen_x - self.screen_width  // 2
        desired_y = target_screen_y - self.screen_height // 2

        if self._lerp_active and dt > 0:
            # Exponential ease toward the target. This is what produces the
            # deliberate follow-lag while walking/running — the camera keeps
            # chasing a moving target, so the gap never closes until the
            # player actually stops. Left permanently on (not deactivated
            # once "close enough") so it applies to ordinary gameplay, not
            # just the moment right after a cutscene.
            #
            # This eases against _true_x/_true_y (not the rounded, shaken
            # self.x/self.y) so the accumulator stays a clean float from
            # frame to frame — rounding it here would make the ease chase a
            # jittery, pixel-snapped target instead of a smooth one.
            t = 1.0 - (1.0 / (1.0 + self._lerp_speed * dt))
            self._true_x = self._true_x + (desired_x - self._true_x) * t
            self._true_y = self._true_y + (desired_y - self._true_y) * t
        else:
            self._true_x = desired_x
            self._true_y = desired_y

        # Clamp the true (un-shaken) position to room bounds, or centre for
        # rooms smaller than the screen.
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

        # Rendered position: shake added on top of the true position, then
        # snapped to a whole pixel exactly once, here. Every consumer
        # (player sprite, ground shadow, tile draws, camera.apply(), ...)
        # now works from the same integer camera offset each frame instead
        # of each independently truncating a continuously-drifting float —
        # that mismatch (not the shake or the ease itself) was what made
        # the shadow appear to jitter relative to the player while moving.
        self.x = round(self._true_x + self.shake_offset_x)
        self.y = round(self._true_y + self.shake_offset_y)

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