import pygame
import math
from config.settings import RENDER_SCALE
from core.draw_layers import DrawLayer


# Unit vectors per facing direction — used both by the aim indicator (to walk
# a point out in front of the player) and by MasenkoProjectile's caller (to
# pick a spawn offset), mirroring _DIRECTION_SPAWN_OFFSETS in player.py.
_DIRECTION_VECTORS = {
    'up':    (0, -1),
    'down':  (0,  1),
    'left':  (-1, 0),
    'right': (1,  0),
}


def _masenko_layer_for_direction(direction):
    """down/left/right draw in front of the player; up draws behind (the
    character's back is to the camera, so the ball reads as travelling
    behind them rather than in front)."""
    return DrawLayer.EFFECTS_BEHIND if direction == 'up' else DrawLayer.EFFECTS_FRONT


class MasenkoAimIndicator:
    """
    The oscillating aim marker shown while Masenko is charging.

    Walks a point back and forth along the player's facing direction between
    min_distance and max_distance (world units), bouncing at each end. The
    player releases Q at whatever moment they like — wherever the marker
    currently sits becomes the target position handed to MasenkoProjectile.
    """

    def __init__(self, player):
        self.player = player
        self.direction = player.direction

        self.min_distance = 24
        self.max_distance = 150
        self.oscillate_speed = 160  # world units per second

        self._distance = self.min_distance
        self._sign = 1  # +1 growing outward, -1 shrinking back in
        self.active = True

        # Small pulsing ring marker — used as a fallback if the sprite below
        # fails to load, and to modulate the sprite's size when it does.
        self._pulse_timer = 0.0

        self.draw_layer = DrawLayer.PLAYER
        self.y_sort = False

        # Static reticle sprite — falls back to the vector circle if missing.
        self.sprite_path = 'assets/sprites/attacks/masenko/target_indicator.png'
        self.sprite = None
        self.sprite_size = 16  # native px, used for scaling/centering
        self._load_sprite()

    def _load_sprite(self):
        try:
            self.sprite = pygame.image.load(self.sprite_path).convert_alpha()
        except (pygame.error, FileNotFoundError):
            self.sprite = None

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def update(self, dt):
        """Advance the back-and-forth sweep. Direction is re-read from the
        player each frame so turning while charging keeps the indicator
        pointed the right way."""
        if not self.active:
            return

        self.direction = self.player.direction
        self._distance += self.oscillate_speed * self._sign * dt

        if self._distance >= self.max_distance:
            self._distance = self.max_distance
            self._sign = -1
        elif self._distance <= self.min_distance:
            self._distance = self.min_distance
            self._sign = 1

        self._pulse_timer += dt

    def get_target_position(self):
        """Return the (x, y) world position the marker currently sits at —
        this is what gets captured as the throw target on release.

        Origin is the player's feet (self.player.y + height/2, matching the
        y + height/2 convention Player.get_sort_key() uses) rather than the
        sprite's vertical center — otherwise facing left/right would put the
        marker at chest height instead of on the ground in front of the
        player.
        """
        vx, vy = _DIRECTION_VECTORS.get(self.direction, (0, 1))
        feet_y = self.player.y + getattr(self.player, 'height', 0) // 2
        target_x = self.player.x + vx * self._distance
        target_y = feet_y + vy * self._distance
        return target_x, target_y

    def draw(self, screen, camera, colors=None):
        if not self.active:
            return

        target_x, target_y = self.get_target_position()
        screen_x = (target_x * RENDER_SCALE) - camera.x
        screen_y = (target_y * RENDER_SCALE) - camera.y

        # Gentle pulse so the marker doesn't read as a static dead pixel.
        pulse = 1.0 + 0.15 * math.sin(self._pulse_timer * 8.0)

        if self.sprite:
            size = max(1, int(self.sprite_size * RENDER_SCALE * pulse))
            scaled = pygame.transform.scale(self.sprite, (size, size))
            # Anchor by the BOTTOM edge, not the center — target_y is the
            # feet-level ground position, so the sprite should sit on top of
            # it rather than straddle it.
            rect = scaled.get_rect(midbottom=(int(screen_x), int(screen_y)))
            screen.blit(scaled, rect)
        else:
            radius = int(6 * RENDER_SCALE * pulse)
            center = (int(screen_x), int(screen_y - radius))
            pygame.draw.circle(screen, (255, 220, 80), center, radius, 2)
            pygame.draw.circle(screen, (255, 220, 80), center, max(1, radius // 3))


class MasenkoHoldEffect:
    """
    The hold_masenko overlay that plays above the player's head while
    charging and while the thrown ball is still in flight.

    Same spritesheet, two different frame-index patterns depending on mode:
      'hold'  — ping-pongs through every frame: 1,2,3,2,1,2,3,2,1,...
      'throw' — alternates the last two frames only: 2,3,2,3,...
    (numbers given 1-indexed, matching how the sheet's frames are described;
    internally everything is 0-indexed.)

    hold_masenko.png is assumed to be a single row of frame_width x
    frame_height frames — same convention as charging_kamehameha.png in
    beam.py, since there's no per-direction art for the overlay.
    """

    SPRITE_PATH = 'assets/sprites/attacks/masenko/hold_masenko.png'

    def __init__(self, player, scale=RENDER_SCALE, mode='hold'):
        self.player = player
        self.scale = scale
        self.active = True
        self.y_sort = False

        self.frame_width = 16
        self.frame_height = 16

        # Seconds per step in each mode — throw pulses a little faster than hold.
        self.hold_frame_duration = 0.12
        self.throw_frame_duration = 0.08

        self.mode = mode
        self.frame_duration = self.hold_frame_duration if mode == 'hold' else self.throw_frame_duration
        self.tick = 0
        self.frame_timer = 0.0

        self.frames_scaled = []
        self.frame_w_scaled = 0
        self.frame_h_scaled = 0
        self._load_sprite()

        # Position above the player, tunable per facing direction — same idea
        # as KamehamehaChargeEffect.direction_offsets. Each tuple is
        # (offset_x, offset_y) in world units (pre-scale): positive x moves
        # right, negative y moves up. Falls back to 'down' for any direction
        # not listed (e.g. 8-directional diagonals).
        self.direction_offsets = {
            'down':  (0, -26),
            'up':    (0, -26),
            'left':  (0, -26),
            'right': (0, -26),
        }

        self.draw_layer = DrawLayer.PLAYER
        self.y_sort = False

    def _load_sprite(self):
        try:
            sheet = pygame.image.load(self.SPRITE_PATH).convert_alpha()
            frames_per_row = sheet.get_width() // self.frame_width

            raw_frames = []
            for i in range(frames_per_row):
                raw_frames.append(
                    sheet.subsurface(pygame.Rect(i * self.frame_width, 0,
                                                  self.frame_width, self.frame_height))
                )

            if raw_frames:
                rect = raw_frames[0].get_rect()
                self.frame_w_scaled = int(rect.width * self.scale)
                self.frame_h_scaled = int(rect.height * self.scale)
                self.frames_scaled = [
                    pygame.transform.scale(f, (self.frame_w_scaled, self.frame_h_scaled))
                    for f in raw_frames
                ]
        except (pygame.error, FileNotFoundError) as e:
            print(f"Error loading hold_masenko sprite: {e}")
            self.frames_scaled = []

    def set_mode(self, mode):
        """Switch between 'hold' and 'throw' patterns, restarting the tick so
        the new pattern always starts from its first frame (frame 1 for hold,
        frame 2 for throw) rather than wherever the old tick left off."""
        if mode == self.mode:
            return
        self.mode = mode
        self.frame_duration = self.hold_frame_duration if mode == 'hold' else self.throw_frame_duration
        self.tick = 0
        self.frame_timer = 0.0

    def _current_frame_index(self):
        count = len(self.frames_scaled)
        if count == 0:
            return 0
        if count == 1:
            return 0

        if self.mode == 'hold':
            # Ping-pong across every frame: 0,1,2,...,count-1,...,1,0,1,2,...
            period = 2 * count - 2
            pos = self.tick % period
            return pos if pos < count else period - pos
        else:
            # 'throw' — alternate only the last two frames.
            a, b = count - 2, count - 1
            return a if self.tick % 2 == 0 else b

    def update(self, dt):
        if not self.frames_scaled:
            return
        self.frame_timer += dt
        if self.frame_timer >= self.frame_duration:
            self.frame_timer -= self.frame_duration
            self.tick += 1

    def get_sort_key(self):
        self.draw_layer = _masenko_layer_for_direction(self.player.direction)
        return (self.draw_layer, 0)

    def draw(self, screen, camera, colors=None):
        if not self.active or not self.frames_scaled:
            return

        offset_x, offset_y = self.direction_offsets.get(self.player.direction, self.direction_offsets['down'])
        screen_x = ((self.player.x + offset_x) * RENDER_SCALE) - camera.x
        screen_y = ((self.player.y + offset_y) * RENDER_SCALE) - camera.y

        frame = self.frames_scaled[self._current_frame_index()]
        rect = frame.get_rect(center=(int(screen_x), int(screen_y)))
        screen.blit(frame, rect)


class MasenkoProjectile:
    """
    The thrown ki ball. Travels a parabolic arc from the player to wherever
    the aim indicator was when Q was released, then detonates immediately
    (no fuse — unlike BombProjectile, this isn't a planted charge) into an
    AoE explosion against enemies.

    Flight math is deliberately identical to BombProjectile's arc so both
    projectiles read the same way on screen.
    """

    STATE_FLYING   = 'flying'
    STATE_EXPLODED = 'exploded'

    EXPLOSION_RADIUS = 40

    def __init__(self, start_x, start_y, target_x, target_y, damage=40, flight_time=0.45, direction='down'):
        self.start_x = start_x
        self.start_y = start_y
        self.target_x = target_x
        self.target_y = target_y

        self.x = start_x
        self.y = start_y

        self.width = 16
        self.height = 16
        self.radius = 8

        self.damage = damage
        self.active = True
        self.has_hit = False
        self.pending_explosion = None  # Set to a MasenkoExplosion on detonation.

        self.state = self.STATE_FLYING

        self.flight_time         = flight_time
        self.elapsed_time        = 0.0
        self.arc_height          = 60
        self.horizontal_distance = target_x - start_x
        self.vertical_distance   = target_y - start_y

        # direction is fixed at the moment of the throw (the ball doesn't
        # turn mid-flight) — down/left/right draw in front of the player,
        # up draws behind, same convention as the hold effect.
        self.direction  = direction
        self.draw_layer = _masenko_layer_for_direction(direction)
        self.y_sort     = False

        # Sprite / animation for the in-flight ball
        self.spritesheet     = None
        self.frame_width     = 16
        self.frame_height    = 16
        self.current_frame   = 0
        self.animation_timer = 0
        self.frame_duration  = 0.08
        self.total_frames    = 1
        self._load_sprite()

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def _load_sprite(self):
        try:
            path = 'assets/sprites/attacks/masenko/masenko_ball.png'
            self.spritesheet = pygame.image.load(path).convert_alpha()
            self.total_frames = max(1, self.spritesheet.get_width() // self.frame_width)
        except (pygame.error, FileNotFoundError):
            self.spritesheet = None

    def update(self, dt):
        if not self.active:
            return

        if self.spritesheet:
            self.animation_timer += dt
            if self.animation_timer >= self.frame_duration:
                self.animation_timer = 0
                self.current_frame = (self.current_frame + 1) % self.total_frames

        if self.state == self.STATE_FLYING:
            self._update_flying(dt)

    def _update_flying(self, dt):
        self.elapsed_time += dt
        progress = min(self.elapsed_time / self.flight_time, 1.0)

        self.x = self.start_x + self.horizontal_distance * progress
        linear_y   = self.start_y + self.vertical_distance * progress
        arc_offset = self.arc_height * 4 * progress * (1.0 - progress)
        self.y     = linear_y - arc_offset

        if progress >= 1.0:
            self.x = self.target_x
            self.y = self.target_y
            self._detonate()

    def _detonate(self):
        self.state  = self.STATE_EXPLODED
        self.active = False
        self.has_hit = True
        self.pending_explosion = MasenkoExplosion(self.x, self.y)

    def draw(self, screen, camera, colors=None):
        if self.state != self.STATE_FLYING:
            return

        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.spritesheet:
            frame_x = self.current_frame * self.frame_width
            frame_surface = self.spritesheet.subsurface(
                pygame.Rect(frame_x, 0, self.frame_width, self.frame_height))
            scaled = pygame.transform.scale(
                frame_surface,
                (int(self.width * RENDER_SCALE), int(self.height * RENDER_SCALE))
            )
            screen.blit(scaled, (
                int(screen_x - (self.width  * RENDER_SCALE) // 2),
                int(screen_y - (self.height * RENDER_SCALE) // 2)
            ))
        else:
            pygame.draw.circle(screen, (255, 230, 120),
                               (int(screen_x), int(screen_y)),
                               int(self.radius * RENDER_SCALE))
            pygame.draw.circle(screen, (255, 255, 255),
                               (int(screen_x), int(screen_y)),
                               int(self.radius * 0.5 * RENDER_SCALE))


class MasenkoExplosion:
    """Purely visual detonation, same lifecycle shape as ExplosionEffect in
    bomb_projectile.py."""

    SPRITE_PATH  = 'assets/sprites/attacks/masenko/masenko_explosion.png'
    FRAME_WIDTH  = 32
    FRAME_HEIGHT = 32

    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.active = True

        self.draw_layer = DrawLayer.PLAYER
        self.y_sort     = True

        self.spritesheet     = None
        self.total_frames    = 1
        self.current_frame   = 0
        self.frame_duration  = 0.05
        self.animation_timer = 0.0
        self._load_sprite()

        self.max_radius         = 34
        self._fallback_duration = 0.25
        self._fallback_timer    = self._fallback_duration

    def get_sort_key(self):
        return (self.draw_layer, self.y)

    def _load_sprite(self):
        try:
            sheet = pygame.image.load(self.SPRITE_PATH).convert_alpha()
            self.spritesheet = sheet
            self.total_frames = max(1, sheet.get_width() // self.FRAME_WIDTH)
        except (pygame.error, FileNotFoundError):
            self.spritesheet = None

    def update(self, dt):
        if not self.active:
            return

        if self.spritesheet:
            self.animation_timer += dt
            if self.animation_timer >= self.frame_duration:
                self.animation_timer = 0
                self.current_frame += 1
                if self.current_frame >= self.total_frames:
                    self.active = False
        else:
            self._fallback_timer -= dt
            if self._fallback_timer <= 0:
                self.active = False

    def draw(self, screen, camera, colors=None):
        if not self.active:
            return

        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.spritesheet:
            frame_surface = self.spritesheet.subsurface(
                pygame.Rect(self.current_frame * self.FRAME_WIDTH, 0,
                            self.FRAME_WIDTH, self.FRAME_HEIGHT))
            scaled_w = int(self.FRAME_WIDTH  * RENDER_SCALE)
            scaled_h = int(self.FRAME_HEIGHT * RENDER_SCALE)
            scaled   = pygame.transform.scale(frame_surface, (scaled_w, scaled_h))
            screen.blit(scaled, (int(screen_x - scaled_w // 2), int(screen_y - scaled_h // 2)))
        else:
            progress = 1.0 - (self._fallback_timer / self._fallback_duration)
            radius   = int(self.max_radius * progress * RENDER_SCALE)
            pygame.draw.circle(screen, (255, 220, 100), (int(screen_x), int(screen_y)), radius, 3)
            if radius > 5:
                pygame.draw.circle(screen, (255, 255, 200), (int(screen_x), int(screen_y)), radius // 2, 2)