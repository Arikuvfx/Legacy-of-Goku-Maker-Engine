import math
import random

from core.draw_layers import DrawLayer


class Critter:
    """A small piece of ambient wildlife (squirrel, bird, butterfly, ...).

    Deliberately minimal compared to NPC/Enemy: no hitbox, no AI target,
    no dialogue, no collision with the player. All it does is play an
    animation and occasionally wander a few tiles from its spawn point,
    then settle again. get_collision_rect() always returns None so any
    code that defensively filters entities by collision rect (the way
    world_map_objects does) will skip critters automatically.

    Behavior is driven by BEHAVIOR_PROFILES, keyed by critter_type, so
    each animal can have its own pacing without new code:
        moving_state     — animation name played while wandering
        resting_state    — animation name played while settled (None if
                            the critter is never idle, e.g. a butterfly)
        use_8_directions — whether movement/animation uses 8 or 4 dirs
        speed             — wander speed in world px/sec
        rest_duration     — (min, max) seconds spent settled
        move_duration     — (min, max) seconds spent wandering per leg
    """

    BEHAVIOR_PROFILES = {
        'squirrel': {
            'moving_state': 'walk',
            'resting_state': 'idle',
            'use_8_directions': False,
            'speed': 24,
            'rest_duration': (1.5, 4.0),
            'move_duration': (0.5, 1.5),
            'flutter_amplitude': 0,
            'flutter_frequency': 0,
            'jitter_amplitude': 0,
        },
        'bird': {
            'moving_state': 'flying',
            'resting_state': 'idle',
            'use_8_directions': True,
            'speed': 40,
            'rest_duration': (1.0, 3.0),
            'move_duration': (0.8, 2.0),
            'flutter_amplitude': 0,
            'flutter_frequency': 0,
            'jitter_amplitude': 0,
        },
        'butterfly': {
            # Butterflies are only ever flying — resting_state=None means
            # update() never switches out of moving_state, it just keeps
            # picking new drift directions forever.
            'moving_state': 'flying',
            'resting_state': None,
            'use_8_directions': False,
            'speed': 50,
            'rest_duration': (0.0, 0.0),
            'move_duration': (1.0, 2.5),
            # Visual-only wing-beat wobble, perpendicular to the current
            # heading — see _flutter_offset(). Amplitude is world px,
            # frequency is oscillations/sec.
            'flutter_amplitude': 0.5,
            'flutter_frequency': 8.0,
            # Real per-frame positional noise (world px), added directly
            # to the movement itself so the flight PATH is erratic, not
            # just the sprite's draw offset. This is what actually reads
            # as "jittery" rather than "smoothly bobbing."
            'jitter_amplitude': 0.5,
        },
    }

    DEFAULT_PROFILE = {
        'moving_state': 'walk',
        'resting_state': 'idle',
        'use_8_directions': False,
        'speed': 20,
        'rest_duration': (1.5, 3.5),
        'move_duration': (0.5, 1.5),
        'flutter_amplitude': 0,
        'flutter_frequency': 0,
        'jitter_amplitude': 0,
    }

    # 4-directional movement vectors, matched to sprite_system's DIRECTIONS_4/8.
    _VECTORS_4 = {
        'down': (0, 1), 'up': (0, -1), 'left': (-1, 0), 'right': (1, 0),
    }
    _VECTORS_8 = {
        'down': (0, 1), 'down_left': (-1, 1), 'left': (-1, 0), 'up_left': (-1, -1),
        'up': (0, -1), 'up_right': (1, -1), 'right': (1, 0), 'down_right': (1, 1),
    }

    def __init__(self, x, y, critter_type='squirrel', variant='default',
                 width=16, height=16, wander_radius=32):
        self.x = x
        self.y = y
        self.spawn_x = x
        self.spawn_y = y
        self.critter_type = critter_type
        self.variant = variant
        self.width = width
        self.height = height
        self.wander_radius = wander_radius

        self.active = True
        self.sprite = None
        self.has_sprite = False

        # LayerManager.draw_all sorts everything by get_sort_key(), and
        # NPCs/Enemies get this wired up via LayerIntegrationHelper. Critter
        # isn't an NPC or Enemy (so _draw_shadow won't give it a shadow —
        # see class docstring), but it still needs its own sort key or
        # draw_all's sorted() call blows up the moment a critter is on
        # screen. Same (layer, y) shape as setup_npc/setup_enemy use.
        self.draw_layer = DrawLayer.NPCS
        self.y_sort = True

        self.profile = self.BEHAVIOR_PROFILES.get(critter_type, self.DEFAULT_PROFILE)

        self.facing = 'down'
        self.state = 'rest' if self.profile['resting_state'] else 'move'
        self._state_timer = random.uniform(*self.profile['rest_duration']) if self.state == 'rest' else 0.0

        # Per-instance phase offset so a group of butterflies don't all
        # bob in perfect unison — see _flutter_offset().
        self._flutter_time = random.uniform(0, 1000)
        self._flutter_offset_x = 0.0
        self._flutter_offset_y = 0.0

        self._pick_new_direction()

    # -- collision / interaction --------------------------------------------
    def get_collision_rect(self):
        """Critters never block or get blocked — always None, by design."""
        return None

    def can_interact(self, player):
        """Critters can't be talked to or otherwise interacted with."""
        return False

    # -- behavior -------------------------------------------------------------
    def _directions_with_frames(self, anim_name):
        """Directions that actually have a loaded frame for anim_name.

        use_8_directions in this profile and in CritterSpriteLoader's
        CRITTER_ANIMATIONS are two separate declarations that have to be
        kept in sync by hand — if a sheet only has 4 rows but this profile
        says 8, half the "directions" read past the bottom of the image and
        load as blank transparent frames (pygame silently no-ops an
        out-of-bounds blit). Checking has_animation() here means a
        mismatch degrades to "picks from whichever directions actually
        rendered" instead of "randomly turns invisible."
        """
        vectors = self._VECTORS_8 if self.profile['use_8_directions'] else self._VECTORS_4
        if self.sprite is None:
            return vectors
        available = {d: v for d, v in vectors.items() if self.sprite.has_animation(anim_name, d)}
        return available or vectors

    def _pick_new_direction(self):
        anim_name = self.profile['moving_state']
        vectors = self._directions_with_frames(anim_name)
        self.facing = random.choice(list(vectors.keys()))
        self._direction_vector = vectors[self.facing]

    def _pick_return_direction(self):
        """Face back toward spawn instead of picking blindly.

        Used when a critter with no resting_state (e.g. a butterfly) hits
        wander_radius. A uniformly random reroll has a good chance of
        picking another outward direction, which — since position is
        frozen at the boundary until an inward direction is found — reads
        as the sprite rapidly flickering between facings (jitter, plus
        stray up/down frames while nominally moving left/right) instead
        of a single clean turn back inward.
        """
        anim_name = self.profile['moving_state']
        vectors = self._directions_with_frames(anim_name)
        dx = self.spawn_x - self.x
        dy = self.spawn_y - self.y
        self.facing, self._direction_vector = max(
            vectors.items(), key=lambda item: dx * item[1][0] + dy * item[1][1]
        )

    def _update_flutter(self, dt):
        """Advance the wing-beat wobble and cache its screen-space offset.

        The offset is perpendicular to whichever way the critter is
        currently heading, which is what makes it read as "bobs up/down
        while flying left/right, sways side to side while flying up/down"
        rather than a generic shake. It's purely a draw-time offset — it
        never touches self.x/self.y — so it can't feed back into the
        wander_radius math or make the critter drift off its actual path.
        """
        amplitude = self.profile.get('flutter_amplitude', 0)
        frequency = self.profile.get('flutter_frequency', 0)

        if not amplitude or not frequency:
            self._flutter_offset_x = 0.0
            self._flutter_offset_y = 0.0
            return

        self._flutter_time += dt
        bob = amplitude * math.sin(2 * math.pi * frequency * self._flutter_time)

        dx, dy = self._direction_vector
        perp_x, perp_y = -dy, dx  # rotate heading 90°
        self._flutter_offset_x = perp_x * bob
        self._flutter_offset_y = perp_y * bob

    def _enter_rest(self):
        self.state = 'rest'
        lo, hi = self.profile['rest_duration']
        self._state_timer = random.uniform(lo, hi) if hi > 0 else 0.0

    def _enter_move(self):
        self.state = 'move'
        lo, hi = self.profile['move_duration']
        self._state_timer = random.uniform(lo, hi)
        self._pick_new_direction()

    def update(self, dt, room_width=None, room_height=None):
        """Tick the wander state machine and the sprite animation.

        No player parameter on purpose — critters never react to the
        player, which is what keeps them cheap to have several of on
        screen at once.
        """
        self._state_timer -= dt

        if self.state == 'move':
            dx, dy = self._direction_vector
            speed = self.profile['speed']
            new_x = self.x + dx * speed * dt
            new_y = self.y + dy * speed * dt

            # Real positional noise, layered onto the intended movement —
            # this is what makes the flight PATH itself erratic (jittery)
            # rather than just a smooth bob on top of a straight line.
            jitter = self.profile.get('jitter_amplitude', 0)
            if jitter:
                new_x += random.uniform(-jitter, jitter)
                new_y += random.uniform(-jitter, jitter)

            # Stay within wander_radius of spawn point.
            if math.hypot(new_x - self.spawn_x, new_y - self.spawn_y) > self.wander_radius:
                if self.profile['resting_state']:
                    self._enter_rest()
                else:
                    # No resting state (e.g. butterfly) — turn back toward
                    # spawn instead of drifting further from it. Using the
                    # inward-biased picker (rather than a blind random
                    # reroll) means this resolves in one frame instead of
                    # potentially re-rolling outward-facing directions
                    # several frames in a row while frozen at the boundary.
                    self._pick_return_direction()
            else:
                # Stay inside the room too, if bounds were supplied.
                if room_width is not None:
                    new_x = max(0, min(new_x, room_width))
                if room_height is not None:
                    new_y = max(0, min(new_y, room_height))
                self.x, self.y = new_x, new_y

            if self._state_timer <= 0:
                if self.profile['resting_state']:
                    self._enter_rest()
                else:
                    self._enter_move()

        elif self.state == 'rest':
            if self._state_timer <= 0:
                self._enter_move()

        self._update_flutter(dt)

        if self.sprite is not None:
            anim_name = self.profile['moving_state'] if self.state == 'move' else self.profile['resting_state']
            if anim_name:
                # Guards the case where moving_state and resting_state have
                # different direction counts (e.g. 8-dir flying, 4-dir idle)
                # and the current facing — valid a moment ago — has no
                # frame under the animation we're switching to.
                if not self.sprite.has_animation(anim_name, self.facing):
                    fallback = self._directions_with_frames(anim_name)
                    self.facing = next(iter(fallback))
                self.sprite.set_animation(anim_name, self.facing)
            self.sprite.update(dt)

    # -- layer manager --------------------------------------------------------
    def get_sort_key(self):
        """(layer, y) tuple consumed by LayerManager.draw_all's sort."""
        y_pos = self.y if self.y_sort else 0
        return (self.draw_layer, y_pos)

    # -- drawing ----------------------------------------------------------------
    def draw(self, surface, camera, colors=None):
        """Draw the current animation frame at this critter's world position.

        Signature matches the (surface, camera, colors) shape used by
        other entities so it can go straight into layer_manager.add_object()
        alongside enemies/NPCs for y-sorted drawing.
        """
        if self.sprite is None:
            return
        draw_x = self.x + self._flutter_offset_x
        draw_y = self.y + self._flutter_offset_y
        self.sprite.draw(surface, draw_x, draw_y, camera)