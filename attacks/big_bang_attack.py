import math
import random

import pygame
from config.settings import RENDER_SCALE as _RENDER_SCALE
from core.draw_layers import DrawLayer


class BigBangAttackChargeEffect:
    """Charge-up visual for the Big Bang Attack.

    Unlike Genkidama's escalating 5-state charge (GenkidamaChargeEffect),
    this only ever reaches one final power state — holding the charge
    key longer doesn't make it stronger, it just keeps it sitting on
    that one state until released. "NUM_STATES" isn't a thing here the
    way it is for Genkidama; there's just the one held pose.

    Rather than pulsing back and forth indefinitely between two sprites
    the way Genkidama's charge does while sitting in a state below max,
    this plays a single FIXED intro sequence once and then holds:

        charge1 -> charge2 -> state1 -> (brief flicker) charge2 -> state1

    ...staying on state1 for as long as the charge key is held past that
    point. The flicker back to charge2 right after first arriving at
    state1 is deliberate — it reads as a little power surge/instability
    right as the charge locks in, rather than a flat, boring settle.

    Releasing the charge key at any point fires whatever's showing at
    that moment conceptually, but since there's only one real power
    level, what actually gets thrown (see BigBangAttackBlast) is always
    the same regardless of exactly which intro phase the release landed
    in — the intro is purely a visual tell, not a charge-level gate.
    """

    # (sprite_key, duration) pairs describing the fixed intro. The final
    # entry's duration is None, meaning "hold here forever" — update()
    # simply stops advancing once it reaches that entry, same
    # once-you're-here-you're-here idea as Genkidama's state 5 having
    # nothing above it to pulse toward.
    _SEQUENCE = [
        ('charge1', 0.18),
        ('charge2', 0.18),
        ('state1',  0.12),   # first arrival at full charge
        ('charge2', 0.08),   # quick flicker back — the "power surge" tease
        ('state1',  None),   # settles here until the player releases
    ]

    def __init__(self, player, scale=_RENDER_SCALE):
        self.player = player
        self.scale = scale
        self.active = True
        self.y_sort = False

        self.phase_index = 0
        self.phase_timer = 0.0

        # Same per-direction fine-tuning convention as
        # GenkidamaChargeEffect.direction_offsets/KamehamehaChargeEffect
        # — nudges the ball so it sits in front of the player regardless
        # of which way they're facing. Unlike those, "in front" here
        # means beside the player along whichever axis they're facing:
        # straight down/up when facing down/up, and out to the side
        # when facing left/right — not floating above the head
        # regardless of facing.
        half_w = getattr(self.player, 'width', 32) / 2
        half_h = getattr(self.player, 'height', 32) / 2
        pad = 10  # extra breathing room beyond the player's own bounds
        self.direction_offsets = {
            'down':  (0, half_h + pad),
            'up':    (0, -(half_h + pad)),
            'left':  (-(half_w + pad), 0),
            'right': (half_w + pad, 0),
        }

        self.sprites_scaled = {}
        self._load_sprites()

        # Same front/behind-by-direction convention the beam/charge
        # effects use elsewhere: only 'up' draws behind the player.
        self.draw_layer = (
            DrawLayer.EFFECTS_BEHIND if player.direction == 'up' else DrawLayer.EFFECTS_FRONT
        )

    def _load_sprites(self):
        for name in ('charge1', 'charge2', 'state1'):
            try:
                sheet = pygame.image.load(
                    f'assets/sprites/attacks/big_bang_attack/{name}.png'
                ).convert_alpha()
                w = int(sheet.get_width() * self.scale)
                h = int(sheet.get_height() * self.scale)
                self.sprites_scaled[name] = pygame.transform.scale(sheet, (w, h))
            except Exception as e:
                print(f"Error loading big_bang_attack {name} sprite: {e}")
                self.sprites_scaled[name] = None

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def is_fully_charged(self):
        """True once the intro sequence has reached its final, holding
        entry — i.e. the ball has already done its charge1->charge2->
        state1->flicker->state1 routine and is just sitting on state1
        for good. There's only one power level regardless of whether
        this is True or False (see class docstring), so nothing about
        firing depends on this — it's here in case something external
        ever wants to know the intro flourish has finished playing (e.g.
        gating a "charge complete" sound cue)."""
        return self.phase_index >= len(self._SEQUENCE) - 1

    def update(self, dt):
        _, duration = self._SEQUENCE[self.phase_index]
        if duration is None:
            return  # final entry — holds here forever, nothing to advance
        self.phase_timer += dt
        if self.phase_timer >= duration:
            self.phase_timer -= duration
            self.phase_index = min(self.phase_index + 1, len(self._SEQUENCE) - 1)

    def get_current_sprite_key(self):
        return self._SEQUENCE[self.phase_index][0]

    def get_fire_sprite(self):
        """The sprite BigBangAttackBlast should actually be handed at
        release — always the state1 art specifically, regardless of
        which intro phase happens to be showing at the exact moment the
        player lets go (there's only one power level to throw here,
        unlike GenkidamaChargeEffect.get_state_sprite(state), which
        varies by whichever of the 5 states charging reached). Mirrors
        that method's shape as an encapsulated accessor rather than
        Player reaching into sprites_scaled directly."""
        return self.sprites_scaled.get('state1')

    def _center_world_pos(self):
        offset_x, offset_y = self.direction_offsets.get(self.player.direction, (0, 0))
        # Offsets are relative to the player's own center now (not the
        # top of their head) — direction_offsets already accounts for
        # half the player's width/height plus padding, so this just
        # applies the per-direction push from wherever the player's
        # center actually is.
        return (
            self.player.x + offset_x,
            self.player.y + offset_y,
        )

    def draw(self, screen, camera, colors=None):
        if not self.active:
            return

        from config.settings import RENDER_SCALE

        center_x, center_y = self._center_world_pos()
        screen_cx = (center_x * RENDER_SCALE) - camera.x
        screen_cy = (center_y * RENDER_SCALE) - camera.y

        sprite = self.sprites_scaled.get(self.get_current_sprite_key())
        if sprite is not None:
            rect = sprite.get_rect(center=(screen_cx, screen_cy))
            screen.blit(sprite, rect)
        else:
            # Fallback if the sprite failed to load — plain circle, same
            # graceful-degradation convention as Genkidama/Projectile.
            pygame.draw.circle(screen, (255, 200, 80), (int(screen_cx), int(screen_cy)), 16)


class BigBangAttackBlast:
    """The ball actually thrown once the charge key is released.

    Deliberately duck-type compatible with Projectile/GenkidamaBlast
    (active/x/y/radius fields, update(world_width, world_height, dt) /
    draw(screen, camera, colors) / get_sort_key()) so it drops straight
    into game.py's existing self.projectiles list and gets movement,
    bounds-checking, and render-layering for free.

    Two things set it apart from a normal Projectile or GenkidamaBlast:

    1. It PIERCES. Nothing in this class ever sets self.active = False
       on a hit — that's deliberate. enemy.py's 'big_bang_attack'
       collision branch (still needs adding — see
       check_collision_with_attack's other branches for the pattern)
       should damage whatever it touches and leave this blast active so
       it keeps traveling through and can go on to hit something else,
       the same continuous-contact shape 'beam'/'dragon_fist' already
       use there, rather than a normal Projectile's consume-on-hit. An
       enemy's own take_damage() i-frames are what stop that from
       restacking damage every single frame of overlap — nothing extra
       needed here for that.

    2. It has a hard MAX_DISTANCE, unlike Genkidama's "travels until it
       leaves the room" (there's no state-based radius/speed table here
       either, unlike Genkidama — there's only one state, so nothing to
       scale by). Reaching MAX_DISTANCE deactivates the blast and the
       caller is expected to call spawn_destruction_burst() the same
       frame active goes False, same external-spawn handoff shape as
       Game._trigger_genkidama_hit spawning a GenkidamaHitEffect rather
       than GenkidamaBlast spawning one internally.

    No white-overlay/tint effect while flying — draw() just blits the
    sprite directly, same as GenkidamaBlast.
    """

    MAX_DISTANCE = 260   # world units — travel cap before it fizzles out
    SPEED = 1.3          # world units / frame — slow, deliberate pierce

    def __init__(self, x, y, direction, sprite=None):
        self.x = x
        self.y = y
        self._start_x = x
        self._start_y = y
        self.direction = direction
        self.active = True

        self.radius = 14
        self.speed = self.SPEED

        # The already-scaled state1 sprite Surface, handed over from
        # BigBangAttackChargeEffect at the moment of release — same
        # convention as GenkidamaBlast's `sprite` param, so this class
        # doesn't need to reload/rescale art on its own.
        self.sprite = sprite

        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self.y_sort = False

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def _distance_traveled(self):
        return math.hypot(self.x - self._start_x, self.y - self._start_y)

    def update(self, world_width, world_height, dt=0.016):
        if not self.active:
            return

        if self.direction == 'up':
            self.y -= self.speed
        elif self.direction == 'down':
            self.y += self.speed
        elif self.direction == 'left':
            self.x -= self.speed
        elif self.direction == 'right':
            self.x += self.speed

        out_of_bounds = self.x < 0 or self.x > world_width or self.y < 0 or self.y > world_height
        if out_of_bounds or self._distance_traveled() >= self.MAX_DISTANCE:
            self.active = False

    def spawn_destruction_burst(self):
        """Call once, the same frame this blast goes inactive (whether
        from hitting MAX_DISTANCE or leaving the room) — hands back the
        random, staggered brown_destruction burst described in
        BigBangDestructionBurst, centered on wherever this blast died.
        Not called automatically from update() itself so the caller
        (wherever this blast's owning list is ticked) controls exactly
        when/whether it gets added to an effects list, same handoff
        shape as GenkidamaHitEffect being spawned externally rather than
        from inside GenkidamaBlast itself."""
        return BigBangDestructionBurst(self.x, self.y)

    def draw(self, screen, camera, colors=None):
        if not self.active:
            return

        from config.settings import RENDER_SCALE
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.sprite is not None:
            rect = self.sprite.get_rect(center=(int(screen_x), int(screen_y)))
            screen.blit(self.sprite, rect)
        else:
            # Fallback if the state1 sprite failed to load — same
            # two-tone circle fallback style as Projectile/GenkidamaBlast.
            fallback = colors['YELLOW'] if colors else (255, 200, 80)
            pygame.draw.circle(screen, fallback, (int(screen_x), int(screen_y)),
                                int(self.radius * RENDER_SCALE))


class _DestructionPuff:
    """One randomly-offset, randomly-delayed brown_destruction burst —
    see BigBangDestructionBurst, which owns a handful of these."""

    def __init__(self, offset_x, offset_y, start_delay):
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.start_delay = start_delay
        self.elapsed = 0.0
        self.frame_index = 0
        self.frame_timer = 0.0
        self.finished = False

    def update(self, dt, frame_count, frame_duration):
        if self.finished:
            return
        self.elapsed += dt
        if self.elapsed < self.start_delay:
            return  # still waiting out its own stagger before it starts
        self.frame_timer += dt
        if self.frame_timer >= frame_duration:
            self.frame_timer -= frame_duration
            if self.frame_index >= frame_count - 1:
                # Holds on the last frame for one frame_duration (like
                # this) before finishing, rather than skipping past it —
                # same play-through-fully-before-ending shape
                # ghost_kamikaze_attack's _advance_filmstrip uses.
                self.finished = True
            else:
                self.frame_index += 1

    def is_visible(self):
        return self.elapsed >= self.start_delay and not self.finished


class BigBangDestructionBurst:
    """Spawned wherever a BigBangAttackBlast dies (max range, or leaving
    the room — see BigBangAttackBlast.spawn_destruction_burst()): a
    handful of the same brown_destruction puffs ghost_kamikaze_attack's
    _Ghost uses for its own destruction, scattered at random positions
    around the blast's final spot and started at randomly staggered
    times instead of all firing in unison — "a bunch of destruction
    sprites randomly play around it for a second or two... offset in
    when they spawn in."

    Duck-type compatible with ExplosionEffect/GenkidamaHitEffect
    (update(dt) / draw(screen, camera, colors) / get_sort_key()), so it
    drops into whatever effects list those already use — no separate
    list or draw path needed.
    """

    PUFF_COUNT = 7
    SCATTER_RADIUS = 28      # world units — how far a puff can land from center
    MAX_START_DELAY = 1.0    # seconds — latest a puff can be staggered to begin
    FRAME_DURATION = 0.08
    # brown_destruction.png is a single row cut into this many equal-width
    # columns — same convention/asset ghost_kamikaze_attack._Ghost and
    # dragon_fist's own destruction_size both already use it with.
    FRAME_COUNT = 4

    def __init__(self, x, y, scale=_RENDER_SCALE):
        self.x = x
        self.y = y
        self.scale = scale
        self.active = True
        self.y_sort = False
        self.draw_layer = DrawLayer.EFFECTS_FRONT

        self.frames_scaled = self._load_sheet()
        if not self.frames_scaled:
            # Nothing to show — don't linger around doing nothing.
            self.active = False

        self.puffs = [
            _DestructionPuff(
                offset_x=random.uniform(-self.SCATTER_RADIUS, self.SCATTER_RADIUS),
                offset_y=random.uniform(-self.SCATTER_RADIUS, self.SCATTER_RADIUS),
                start_delay=random.uniform(0.0, self.MAX_START_DELAY),
            )
            for _ in range(self.PUFF_COUNT)
        ]

    def _load_sheet(self):
        try:
            sheet = pygame.image.load('assets/objects/brown_destruction.png').convert_alpha()
        except Exception as e:
            print(f"No brown_destruction sprite loaded, using fallback: {e}")
            return []
        w, h = sheet.get_width(), sheet.get_height()
        frame_w = w // self.FRAME_COUNT
        raw_frames = [
            sheet.subsurface(pygame.Rect(i * frame_w, 0, frame_w, h)).copy()
            for i in range(self.FRAME_COUNT)
        ]
        sw = int(frame_w * self.scale)
        sh = int(h * self.scale)
        return [pygame.transform.scale(f, (sw, sh)) for f in raw_frames]

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def update(self, dt):
        if not self.active:
            return
        for puff in self.puffs:
            puff.update(dt, self.FRAME_COUNT, self.FRAME_DURATION)
        if all(puff.finished for puff in self.puffs):
            self.active = False

    def draw(self, screen, camera, colors=None):
        if not self.active or not self.frames_scaled:
            return

        from config.settings import RENDER_SCALE
        for puff in self.puffs:
            if not puff.is_visible():
                continue
            frame = self.frames_scaled[puff.frame_index]
            screen_x = ((self.x + puff.offset_x) * RENDER_SCALE) - camera.x
            screen_y = ((self.y + puff.offset_y) * RENDER_SCALE) - camera.y
            rect = frame.get_rect(center=(int(screen_x), int(screen_y)))
            screen.blit(frame, rect)