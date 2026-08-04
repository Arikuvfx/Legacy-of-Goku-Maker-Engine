import math
import random

import pygame
from config.settings import RENDER_SCALE as _RENDER_SCALE
from core.draw_layers import DrawLayer


class _ChargeOrb:
    """One small orb of ki drifting in toward the charging genkidama.

    Spawns out at ORB_SPAWN_RADIUS using the far-away 'charge1' sprite, and
    drifts straight in toward the center. Once it crosses _NEAR_RADIUS it
    switches to the closer-in 'charge2' sprite, and it's dropped entirely
    once it reaches the center (i.e. gets "absorbed" into the main ball).
    """

    _NEAR_RADIUS = 24      # world units — inside this, switch charge1 -> charge2
    _ARRIVAL_RADIUS = 4    # world units — close enough in to despawn

    def __init__(self, spawn_radius, speed):
        angle = random.uniform(0, math.tau)
        self.dx = math.cos(angle)
        self.dy = math.sin(angle)
        self.distance = spawn_radius
        self.speed = speed  # world units / second, inward

    def update(self, dt):
        """Advance the orb inward. Returns False once it's arrived (caller
        should drop it)."""
        self.distance -= self.speed * dt
        return self.distance > self._ARRIVAL_RADIUS

    def offset(self):
        """Current (x, y) world-unit offset from the charge center."""
        return (self.dx * self.distance, self.dy * self.distance)

    def is_near(self):
        return self.distance <= self._NEAR_RADIUS


class GenkidamaChargeEffect:
    """Charge-up visual for the Genkidama.

    State model
    ------------
    There are NUM_STATES (5) power states. State N is active once the
    player has held the charge for STATE_ADVANCE_TIMES[N-2] seconds (state 1
    is active immediately — no threshold needed to reach it), capped at
    state 5: holding longer than the last threshold just keeps you there.

    While sitting in state N (for N < 5), the ball's sprite pulses back and
    forth between state N's sprite and state N+1's sprite — a visual tease
    of the next stage, exactly like the player described ("switches between
    state 1 and state 2 sprite" while in state 1, etc). At state 5 (max)
    there's nothing above it to tease, so it just sits on frame 5.

    Releasing the charge key fires whatever the CURRENT state is, using
    only that state's sprite with no pulsing — see
    Player.release_genkidama().

    Meanwhile, small charge orbs continuously spawn out at ORB_SPAWN_RADIUS
    and drift inward, switching from the far 'charge1' look to the close-in
    'charge2' look partway through, and disappearing once they reach the
    ball — visually "feeding" it.
    """

    NUM_STATES = 5

    # Seconds of total hold time required to reach states 2, 3, 4, 5
    # (state 1 needs no threshold — it's the starting state).
    STATE_ADVANCE_TIMES = [1.2, 2.4, 3.6, 4.8]

    PULSE_DURATION = 0.15  # seconds per pulse half-step ("breathing" ball)

    ORB_SPAWN_INTERVAL = 0.12   # seconds between new orbs spawning
    ORB_SPAWN_RADIUS = 70       # world units — orbs start this far out
    ORB_SPEED = 90              # world units / second, inward drift speed

    def __init__(self, player, scale=_RENDER_SCALE):
        self.player = player
        self.scale = scale
        self.active = True
        self.y_sort = False

        self.state = 1
        self.hold_time = 0.0

        self.pulse_timer = 0.0
        self.pulse_high = False  # False = show `state`, True = show `state + 1`

        self.orbs = []
        self.orb_spawn_timer = 0.0

        # Per-direction fine-tuning, in world units, same convention as
        # KamehamehaChargeEffect.direction_offsets — nudge the ball so it
        # sits in front of the player regardless of which way they're facing.
        self.direction_offsets = {
            'down':  (0, -14),
            'left':  (0, -12),
            'right': (0, -12),
            'up':    (0, -14),
        }

        # self.state_sprites_scaled[i] holds the scaled Surface for state i
        # (1-indexed — index 0 is unused padding so `state` can index directly).
        self.state_sprites_scaled = []
        self.charge_sprites_scaled = {}
        self._load_sprites()

        # Same front/behind-by-direction convention the beam/charge effect
        # uses elsewhere: only 'up' draws behind the player.
        self.draw_layer = (
            DrawLayer.EFFECTS_BEHIND if player.direction == 'up' else DrawLayer.EFFECTS_FRONT
        )

    def _load_sprites(self):
        self.state_sprites_scaled = [None]  # index 0 padding, unused
        for i in range(1, self.NUM_STATES + 1):
            try:
                sheet = pygame.image.load(
                    f'assets/sprites/attacks/genkidama/state{i}.png'
                ).convert_alpha()
                w = int(sheet.get_width() * self.scale)
                h = int(sheet.get_height() * self.scale)
                self.state_sprites_scaled.append(pygame.transform.scale(sheet, (w, h)))
            except Exception as e:
                print(f"Error loading genkidama state{i} sprite: {e}")
                self.state_sprites_scaled.append(None)

        for name in ('charge1', 'charge2'):
            try:
                sheet = pygame.image.load(
                    f'assets/sprites/attacks/genkidama/{name}.png'
                ).convert_alpha()
                w = int(sheet.get_width() * self.scale)
                h = int(sheet.get_height() * self.scale)
                self.charge_sprites_scaled[name] = pygame.transform.scale(sheet, (w, h))
            except Exception as e:
                print(f"Error loading genkidama {name} sprite: {e}")
                self.charge_sprites_scaled[name] = None

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def get_state_sprite(self, state):
        """Return the scaled Surface for the given 1-5 state, or None if it
        failed to load (caller should fall back to a plain circle)."""
        if 1 <= state <= self.NUM_STATES:
            return self.state_sprites_scaled[state]
        return None

    def update(self, dt):
        self.hold_time += dt

        new_state = 1
        for threshold in self.STATE_ADVANCE_TIMES:
            if self.hold_time >= threshold:
                new_state += 1
        self.state = min(new_state, self.NUM_STATES)

        # Pulse the ball between the current state and the next one.
        self.pulse_timer += dt
        if self.pulse_timer >= self.PULSE_DURATION:
            self.pulse_timer -= self.PULSE_DURATION
            self.pulse_high = not self.pulse_high

        # Spawn + advance charge orbs.
        self.orb_spawn_timer += dt
        if self.orb_spawn_timer >= self.ORB_SPAWN_INTERVAL:
            self.orb_spawn_timer -= self.ORB_SPAWN_INTERVAL
            self.orbs.append(_ChargeOrb(self.ORB_SPAWN_RADIUS, self.ORB_SPEED))

        self.orbs = [orb for orb in self.orbs if orb.update(dt)]

    def _center_world_pos(self):
        offset_x, offset_y = self.direction_offsets.get(self.player.direction, (0, 0))
        return (
            self.player.x + offset_x,
            self.player.y - self.player.height / 2 + offset_y,
        )

    def draw(self, screen, camera, colors=None):
        if not self.active:
            return

        from config.settings import RENDER_SCALE

        center_x, center_y = self._center_world_pos()
        screen_cx = (center_x * RENDER_SCALE) - camera.x
        screen_cy = (center_y * RENDER_SCALE) - camera.y

        # Orbs first so they sit visually behind/around the main ball.
        for orb in self.orbs:
            ox, oy = orb.offset()
            sprite = self.charge_sprites_scaled.get('charge2' if orb.is_near() else 'charge1')
            orb_screen_x = screen_cx + ox * RENDER_SCALE
            orb_screen_y = screen_cy + oy * RENDER_SCALE
            if sprite is None:
                pygame.draw.circle(screen, (120, 220, 255),
                                    (int(orb_screen_x), int(orb_screen_y)), 3)
                continue
            rect = sprite.get_rect(center=(orb_screen_x, orb_screen_y))
            screen.blit(sprite, rect)

        # Main ball — pulses between the current state and the next, if any.
        show_state = self.state
        if self.pulse_high and self.state < self.NUM_STATES:
            show_state = self.state + 1
        sprite = self.get_state_sprite(show_state)
        if sprite is not None:
            rect = sprite.get_rect(center=(screen_cx, screen_cy))
            screen.blit(sprite, rect)
        else:
            pygame.draw.circle(screen, (255, 240, 150), (int(screen_cx), int(screen_cy)), 16)


class GenkidamaBlast:
    """The ball actually thrown once the charge key is released.

    Deliberately duck-type compatible with Projectile (same active/x/y/
    radius fields and the same update(world_width, world_height, dt) /
    draw(screen, camera, colors) / get_sort_key() signatures) so it can be
    dropped straight into game.py's existing self.projectiles list and get
    movement, bounds-checking, enemy-collision, and render-layering for
    free — no separate list or loop needed.

    Like Projectile, it travels in a straight line until it leaves the
    world bounds (or is deactivated by a collision elsewhere) — there's no
    hard range cap, matching "it moves infinitely until it either touches
    something or leaves the bounds of the room."
    """

    # Per-state tuning: (radius, speed multiplier). Higher states are
    # bigger, slightly slower (feels heavier/more powerful), and hit harder
    # — actual damage numbers come from wherever Projectile's damage is
    # normally read (e.g. enemy.check_collision_with_attack), scaled by
    # whatever convention that already uses; radius is what matters here
    # for hit detection.
    _STATE_STATS = {
        1: (10, 1.0),
        2: (13, 1.0),
        3: (16, 0.9),
        4: (20, 0.85),
        5: (26, 0.75),
    }

    BASE_SPEED = 1  # matches Projectile.speed

    def __init__(self, x, y, direction, state, sprite=None):
        self.x = x
        self.y = y
        self.direction = direction
        self.state = max(1, min(state, 5))
        self.active = True

        radius, speed_mult = self._STATE_STATS[self.state]
        self.radius = radius
        self.speed = self.BASE_SPEED * speed_mult

        # The already-scaled state sprite Surface, handed over from the
        # GenkidamaChargeEffect at the moment of release, so this class
        # doesn't need to reload/rescale art on its own.
        self.sprite = sprite

        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self.y_sort = False

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def update(self, world_width, world_height, dt=0.016):
        if self.direction == 'up':
            self.y -= self.speed
        elif self.direction == 'down':
            self.y += self.speed
        elif self.direction == 'left':
            self.x -= self.speed
        elif self.direction == 'right':
            self.x += self.speed

        if self.x < 0 or self.x > world_width or self.y < 0 or self.y > world_height:
            self.active = False

    def draw(self, screen, camera, colors):
        if not self.active:
            return

        from config.settings import RENDER_SCALE
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.sprite is not None:
            rect = self.sprite.get_rect(center=(int(screen_x), int(screen_y)))
            screen.blit(self.sprite, rect)
        else:
            # Fallback if state sprites failed to load — same two-tone
            # circle fallback style as Projectile.
            pygame.draw.circle(screen, colors['YELLOW'], (int(screen_x), int(screen_y)),
                                self.radius * RENDER_SCALE)
            pygame.draw.circle(screen, colors['CYAN'], (int(screen_x), int(screen_y)),
                                max(1, self.radius - 3) * RENDER_SCALE)


class GenkidamaHitEffect:
    """The impact flash spawned wherever a GenkidamaBlast connects with an
    enemy or a destructible object (see Game._trigger_genkidama_hit).

    Plays 'hit.png' — a horizontal spritesheet strip, frames assumed square
    and sized off the sheet's own height — all the way through PLAY_COUNT
    times (default 2, i.e. "plays twice") and then deactivates. Duck-type
    compatible with ExplosionEffect/other effect objects: update(dt) (no
    world-bounds args needed, unlike Projectile) + draw(screen, camera,
    colors) + get_sort_key(), so it drops straight into
    self.genkidama_hit_effects and gets ticked/rendered the same way
    self.explosions already is.
    """

    PLAY_COUNT = 2        # how many full passes through the strip before vanishing
    FRAME_DURATION = 0.06  # seconds per frame — tune for a snappier/slower flash

    def __init__(self, x, y, scale=_RENDER_SCALE):
        self.x = x
        self.y = y
        self.scale = scale
        self.active = True
        self.y_sort = False
        self.draw_layer = DrawLayer.EFFECTS_FRONT

        self.current_frame = 0
        self.frame_timer = 0.0
        self.loops_done = 0

        self.frames_scaled = []
        self._load_sprite()

        if not self.frames_scaled:
            # Nothing to show — don't linger around doing nothing.
            self.active = False

    def _load_sprite(self):
        try:
            sheet = pygame.image.load('assets/sprites/attacks/genkidama/hit.png').convert_alpha()
            # Frames assumed square, laid out left-to-right in a single row —
            # frame size is taken directly from the sheet's own height.
            frame_size = sheet.get_height()
            num_frames = max(1, sheet.get_width() // frame_size)
            raw_frames = [
                sheet.subsurface(pygame.Rect(i * frame_size, 0, frame_size, frame_size))
                for i in range(num_frames)
            ]
            w = int(frame_size * self.scale)
            h = int(frame_size * self.scale)
            self.frames_scaled = [pygame.transform.scale(f, (w, h)) for f in raw_frames]
        except Exception as e:
            print(f"Error loading genkidama hit sprite: {e}")
            self.frames_scaled = []

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def update(self, dt):
        if not self.active or not self.frames_scaled:
            return

        self.frame_timer += dt
        if self.frame_timer >= self.FRAME_DURATION:
            self.frame_timer -= self.FRAME_DURATION
            self.current_frame += 1
            if self.current_frame >= len(self.frames_scaled):
                self.current_frame = 0
                self.loops_done += 1
                if self.loops_done >= self.PLAY_COUNT:
                    self.active = False

    def draw(self, screen, camera, colors=None):
        if not self.active or not self.frames_scaled:
            return

        from config.settings import RENDER_SCALE
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        frame = self.frames_scaled[self.current_frame]
        rect = frame.get_rect(center=(int(screen_x), int(screen_y)))
        screen.blit(frame, rect)