import pygame
from config.settings import RENDER_SCALE as _RENDER_SCALE
from core.draw_layers import get_beam_layer
from attacks.beam import BeamAttack


class FlameKamehamehaAttack:
    """The 'flame' Kamehameha variant — a fixed three-segment chain whose
    tip the player steers by hand with the movement keys while it's firing,
    instead of BeamAttack's beam that tiles/grows out to an arbitrary
    length on its own.

    Structure (chained end-to-end along the travel direction, closest to
    the player first):
      - segment 1 ("begin"): perfectly still, anchored at the attack's
        origin (self.x, self.y) — never moves.
      - segment 2 ("middle"): the SAME sprite/sheet as segment 1, chained
        immediately after it. Tracks the same cross-axis offset as segment
        3 every frame — just scaled down by middle_offset_scale, so it
        swings less far than the tip.
      - segment 3 ("end"): a different sprite, chained after segment 2.
        This is the one doing the "main" whip — it sits at the full,
        unscaled cross-axis offset, which the player pushes around with
        the movement keys (see set_control_input).

    Unlike BeamAttack there's no growth — this becomes fully active, at its
    full fixed length, the instant it's constructed, and just holds in
    place — offset only changing in response to player input — until
    stop() is called.

    This class itself still only represents the FIRED chain — it has no
    charge-up state of its own. The hold-to-charge beat (holding Q plays
    charging_flame_kamehameha.png via KamehamehaChargeEffect for
    flame_kamehameha_charge_required seconds) now lives one level up, in
    player.py's start_charging_flame_kamehameha()/
    update_flame_kamehameha_charge()/fire_flame_kamehameha_auto(); this
    object isn't constructed until that charge completes. Wiring that
    charge-then-spawn sequence, along with feeding movement-key input into
    set_control_input() every frame while firing, is a player.py/game.py
    concern, not this class's.

    Also unlike BeamAttack, there's no decay sweep on release — stop()
    just ends the attack outright, since a lengthwise "consumed front"
    sweep doesn't make sense for a fixed-length chain that never grew in
    the first place. If a fade-out look is wanted later, that's a good
    place to extend this.
    """

    def __init__(self, x, y, direction, scale=_RENDER_SCALE, attack_name='flame_kamehameha',
                 max_offset=10,
                 step_size=5,
                 step_duration=0.12,
                 middle_offset_scale=0.5,
                 push_force=1):
        self.x = x
        self.y = y
        self.direction = direction
        self.scale = scale
        # assets/sprites/attacks/{attack_name}/begin_{attack_name}.png and
        # end_{attack_name}.png — unlike BeamAttack, these sheets are NOT
        # laid out with one row per direction. Each sheet is drawn once,
        # facing 'right', as a single row of 3 frame_width x frame_height
        # frames. Every other direction is derived by rotating those
        # right-facing frames in load_sprites (see direction_to_angle
        # below) instead of reading a different row. There's no separate
        # middle_{attack_name}.png: segment 2 reuses segment 1's sheet.
        self.attack_name = attack_name
        self.active = True
        self.y_sort = False

        # begin/middle frames are 16x16; the end (tip) sheet uses a larger
        # 24x20 frame — different segments, different art sizes, so each
        # sheet is read with its own frame dimensions.
        self.begin_frame_width = 16
        self.begin_frame_height = 16
        self.end_frame_width = 24
        self.end_frame_height = 20
        # Degrees to rotate the right-facing sheet frames by, per
        # direction (pygame.transform.rotate is counter-clockwise for
        # positive angles): right stays as-drawn, up/left/down are each a
        # further 90 degrees around.
        self.direction_to_angle = {'right': 0, 'up': 90, 'left': 180, 'down': 270}

        # The tip's cross-axis position is player-controlled, not
        # automatic — but it still moves in the original's chunky discrete
        # hops rather than smoothly sliding: while the chain is firing,
        # player.py/game.py feeds this frame's movement-key input into
        # set_control_input() every frame, and update() hops self.offset
        # by step_size world px every step_duration seconds for as long as
        # input is held in that direction, clamped to +/- max_offset — not
        # a continuous per-frame slide. The middle segment always mirrors
        # the tip's offset, just scaled down by middle_offset_scale (see
        # _current_offsets), so the two move together at different
        # amplitudes.
        self.max_offset = max_offset
        self.step_size = step_size
        self.step_duration = step_duration
        self._step_timer = 0.0
        self.middle_offset_scale = middle_offset_scale
        self.offset = 0.0
        self._control_input = 0

        # enemy.check_collision_with_attack's 'flame_kamehameha' branch
        # reads this via getattr(attack, 'push_force', None) — same hook
        # 'beam' already supports for e.g. FinalFlashAttack — to override
        # how many world px/frame contact shoves the enemy. Left at the
        # enemy's own default (self.beam_push_force, currently 3) this
        # read like a fast shove; the original game's knockback is a much
        # slower, pixel-by-pixel push, hence the lower default here.
        self.push_force = push_force

        # Sprite-sheet frame playback (which animation frame of each
        # segment's own art is showing right now) — separate clock from
        # the oscillation stepping above.
        self.current_frame = 0
        self.frame_timer = 0.0
        self.frame_duration = 0.08

        self.begin_sprite = None    # segment 1 frames
        self.middle_sprite = None   # segment 2 frames (same sheet as begin)
        self.end_sprite = None      # segment 3 (tip) frames

        self.begin_sprite_scaled = None
        self.middle_sprite_scaled = None
        self.end_sprite_scaled = None

        self.begin_width_scaled = 0
        self.begin_height_scaled = 0
        self.middle_width_scaled = 0
        self.middle_height_scaled = 0
        self.end_width_scaled = 0
        self.end_height_scaled = 0

        self.load_sprites()
        self.calculate_scaled_dimensions()

        self.draw_layer = get_beam_layer(self.direction, self.direction)

    def get_sort_key(self):
        return (self.draw_layer, self.y)

    def _rotate_frames(self, frames):
        """Rotate a list of right-facing frames to match self.direction.
        The sheets are only ever drawn facing 'right'; up/left/down are all
        derived here rather than read from a separate row. Returns the
        frames unrotated (and untouched) for 'right' or an unrecognized
        direction, since rotating by 0 degrees would just be a wasted
        copy."""
        angle = self.direction_to_angle.get(self.direction, 0)
        if angle == 0 or not frames:
            return frames
        return [pygame.transform.rotate(frame, angle) for frame in frames]

    def load_sprites(self):
        """Segment 1 and segment 2 share one sheet (begin_{attack_name}.png);
        segment 3 loads from its own sheet (end_{attack_name}.png). Both
        sheets are drawn once, facing 'right', as a single row of 3 frames
        — there's no per-direction row like BeamAttack uses. Frames are
        trimmed with BeamAttack's _trim_frames helper (so transparent sheet
        padding doesn't throw off anchoring) and then, for any direction
        other than 'right', rotated to match via _rotate_frames."""
        try:
            begin_sheet = pygame.image.load(
                f'assets/sprites/attacks/{self.attack_name}/begin_{self.attack_name}.png'
            ).convert_alpha()
            frames_per_row = begin_sheet.get_width() // self.begin_frame_width
            begin_frames = []
            for i in range(frames_per_row):
                x = i * self.begin_frame_width
                begin_frames.append(
                    begin_sheet.subsurface(pygame.Rect(x, 0, self.begin_frame_width, self.begin_frame_height))
                )
            self.begin_sprite = self._rotate_frames(BeamAttack._trim_frames(begin_frames))
            # Segment 2 is explicitly the SAME art as segment 1 per the
            # design ("the first two parts are essentially the same
            # sprite") — no separate sheet to load for it.
            self.middle_sprite = self.begin_sprite
            print(f"Loaded {len(self.begin_sprite)} begin/middle sprites for direction {self.direction}")
        except Exception as e:
            print(f"Error loading flame kamehameha begin/middle sprite: {e}")
            self.begin_sprite = None
            self.middle_sprite = None

        try:
            end_sheet = pygame.image.load(
                f'assets/sprites/attacks/{self.attack_name}/end_{self.attack_name}.png'
            ).convert_alpha()
            frames_per_row = end_sheet.get_width() // self.end_frame_width
            end_frames = []
            for i in range(frames_per_row):
                x = i * self.end_frame_width
                end_frames.append(
                    end_sheet.subsurface(pygame.Rect(x, 0, self.end_frame_width, self.end_frame_height))
                )
            self.end_sprite = self._rotate_frames(BeamAttack._trim_frames(end_frames))
            print(f"Loaded {len(self.end_sprite)} end sprites for direction {self.direction}")
        except Exception as e:
            print(f"Error loading flame kamehameha end sprite: {e}")
            self.end_sprite = None

        self.use_sprites = any([self.begin_sprite, self.end_sprite])
        if not self.use_sprites:
            print("No flame kamehameha sprites loaded")

    def calculate_scaled_dimensions(self):
        # If a sheet failed to load, fall back to its undrawn frame size —
        # swapped for up/down since those directions are a 90-degree
        # rotation of the right-facing art and would swap width/height too.
        angle = self.direction_to_angle.get(self.direction, 0)
        swapped = angle in (90, 270)

        def scale_list(frames, fallback_w=16, fallback_h=16):
            if not frames:
                if swapped:
                    fallback_w, fallback_h = fallback_h, fallback_w
                return None, int(fallback_w * self.scale), int(fallback_h * self.scale)
            rect = frames[0].get_rect()
            w = int(rect.width * self.scale)
            h = int(rect.height * self.scale)
            scaled = [pygame.transform.scale(f, (w, h)) for f in frames]
            return scaled, w, h

        self.begin_sprite_scaled, self.begin_width_scaled, self.begin_height_scaled = \
            scale_list(self.begin_sprite, self.begin_frame_width, self.begin_frame_height)
        self.middle_sprite_scaled, self.middle_width_scaled, self.middle_height_scaled = \
            scale_list(self.middle_sprite, self.begin_frame_width, self.begin_frame_height)
        self.end_sprite_scaled, self.end_width_scaled, self.end_height_scaled = \
            scale_list(self.end_sprite, self.end_frame_width, self.end_frame_height)

    def stop(self):
        """Ends the attack immediately. No decay sweep — see the class
        docstring for why that doesn't apply to a fixed-length chain."""
        self.active = False

    def set_control_input(self, dx, dy):
        """Feed this frame's raw movement-key input (-1/0/1 axes, same
        convention as Player.move) in. Called once per frame by
        game.py/player.py while the chain is firing, before update().

        Only the axis perpendicular to the chain's fixed travel direction
        does anything: a chain fired 'left'/'right' whips vertically, so it
        reads dy; a chain fired 'up'/'down' whips horizontally, so it reads
        dx. The other axis is ignored — there's nothing sensible for e.g.
        pressing left/right to do to a chain that's already travelling
        left/right."""
        self._control_input = dy if self.direction in ('left', 'right') else dx

    def update(self, dt):
        if not self.active:
            return

        if self.use_sprites:
            self.frame_timer += dt
            if self.frame_timer >= self.frame_duration:
                self.frame_timer -= self.frame_duration
                self.current_frame += 1

        # Hop the tip toward/away from center based on the last input
        # set_control_input() recorded — one step_size jump every
        # step_duration seconds input is held, same chunky cadence the old
        # automatic oscillation used, not a smooth per-frame slide.
        # Releasing input (or reversing direction) resets the timer so the
        # next hop after that always starts a fresh full step_duration
        # rather than firing early off a leftover partial count.
        if self._control_input:
            self._step_timer += dt
            if self._step_timer >= self.step_duration:
                self._step_timer -= self.step_duration
                self.offset += self._control_input * self.step_size
                self.offset = max(-self.max_offset, min(self.max_offset, self.offset))
        else:
            self._step_timer = 0.0

    def _current_offsets(self):
        """(tip_offset, middle_offset) in world px for the current player-
        controlled position. The middle's result is just the tip's offset
        scaled down by middle_offset_scale, so it never swings as far."""
        return self.offset, self.offset * self.middle_offset_scale

    def get_world_bounds(self):
        """World-space pygame.Rect enclosing all three chained segments at
        their current whip position — used by enemy.py's collision check
        ('flame_kamehameha' attack_type), the same way beam attacks expose
        attack.length/width for enemy.py to build a corridor from.

        Mirrors the segment-chaining layout math in _draw_vertical/
        _draw_horizontal (stationary begin, then middle, then the
        offset-carrying end, each starting where the last one's edge left
        off), but in WORLD units — undoing self.scale — rather than screen
        pixels, since those are only meaningful for rendering. Doesn't
        require sprites to be loaded: the *_width_scaled/*_height_scaled
        fallbacks from calculate_scaled_dimensions cover that case too.
        """
        tip_offset, middle_offset = self._current_offsets()

        def world_dims(w_scaled, h_scaled):
            return w_scaled / self.scale, h_scaled / self.scale

        begin_w, begin_h = world_dims(self.begin_width_scaled, self.begin_height_scaled)
        middle_w, middle_h = world_dims(self.middle_width_scaled, self.middle_height_scaled)
        end_w, end_h = world_dims(self.end_width_scaled, self.end_height_scaled)

        # (cross-axis offset, world width, world height) per segment, in
        # chain order — same order/offsets as draw()'s three blits.
        segments = [(0, begin_w, begin_h), (middle_offset, middle_w, middle_h),
                    (tip_offset, end_w, end_h)]

        segment_rects = []
        if self.direction in ('down', 'up'):
            going_down = self.direction == 'down'
            sign = 1 if going_down else -1
            pos = self.y
            for cross_offset, w, h in segments:
                top = pos if going_down else pos - h
                segment_rects.append(pygame.Rect(self.x + cross_offset - w / 2, top, w, h))
                pos += sign * h
        else:
            going_right = self.direction == 'right'
            sign = 1 if going_right else -1
            pos = self.x
            for cross_offset, w, h in segments:
                left = pos if going_right else pos - w
                segment_rects.append(pygame.Rect(left, self.y + cross_offset - h / 2, w, h))
                pos += sign * w

        bounds = segment_rects[0]
        for rect in segment_rects[1:]:
            bounds = bounds.union(rect)
        return bounds

    def draw(self, screen, camera, colors=None):
        if not self.active or not self.use_sprites:
            return

        from config.settings import RENDER_SCALE
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        tip_offset, middle_offset = self._current_offsets()
        tip_offset_scaled = tip_offset * self.scale
        middle_offset_scaled = middle_offset * self.scale

        if self.direction in ('down', 'up'):
            self._draw_vertical(screen, screen_x, screen_y, tip_offset_scaled, middle_offset_scaled)
        else:
            self._draw_horizontal(screen, screen_x, screen_y, tip_offset_scaled, middle_offset_scaled)

    def _draw_vertical(self, screen, screen_x, screen_y, tip_offset, middle_offset):
        """direction == 'down': chain extends downward from the player, so
        each segment anchors midtop and the next one starts at its bottom
        edge. direction == 'up': mirror image, chain extends upward,
        anchors midbottom, next segment starts at its top edge."""
        going_down = self.direction == 'down'
        sign = 1 if going_down else -1
        frame_index = self.current_frame

        # Segment 1: stationary — no cross-axis offset at all, sits right
        # at the player's own position.
        pos = screen_y
        if self.begin_sprite_scaled:
            begin_frame = self.begin_sprite_scaled[frame_index % len(self.begin_sprite_scaled)]
            anchor = 'midtop' if going_down else 'midbottom'
            rect = begin_frame.get_rect(**{anchor: (screen_x, pos)})
            screen.blit(begin_frame, rect)
            pos += sign * self.begin_height_scaled

        # Segment 2: chained right after segment 1 along the travel axis;
        # offset side-to-side (cross axis) by the reduced middle_offset.
        if self.middle_sprite_scaled:
            middle_frame = self.middle_sprite_scaled[frame_index % len(self.middle_sprite_scaled)]
            anchor = 'midtop' if going_down else 'midbottom'
            rect = middle_frame.get_rect(**{anchor: (screen_x + middle_offset, pos)})
            screen.blit(middle_frame, rect)
            pos += sign * self.middle_height_scaled

        # Segment 3 (tip): chained after segment 2; offset by the full
        # tip_offset — the main whipping motion.
        if self.end_sprite_scaled:
            end_frame = self.end_sprite_scaled[frame_index % len(self.end_sprite_scaled)]
            anchor = 'midtop' if going_down else 'midbottom'
            rect = end_frame.get_rect(**{anchor: (screen_x + tip_offset, pos)})
            screen.blit(end_frame, rect)

    def _draw_horizontal(self, screen, screen_x, screen_y, tip_offset, middle_offset):
        """direction == 'right': chain extends rightward, segments anchor
        midleft with the next one starting at its right edge. direction ==
        'left': mirror image, anchors midright, next segment starts at its
        left edge."""
        going_right = self.direction == 'right'
        sign = 1 if going_right else -1
        frame_index = self.current_frame

        pos = screen_x
        if self.begin_sprite_scaled:
            begin_frame = self.begin_sprite_scaled[frame_index % len(self.begin_sprite_scaled)]
            anchor = 'midleft' if going_right else 'midright'
            rect = begin_frame.get_rect(**{anchor: (pos, screen_y)})
            screen.blit(begin_frame, rect)
            pos += sign * self.begin_width_scaled

        if self.middle_sprite_scaled:
            middle_frame = self.middle_sprite_scaled[frame_index % len(self.middle_sprite_scaled)]
            anchor = 'midleft' if going_right else 'midright'
            rect = middle_frame.get_rect(**{anchor: (pos, screen_y + middle_offset)})
            screen.blit(middle_frame, rect)
            pos += sign * self.middle_width_scaled

        if self.end_sprite_scaled:
            end_frame = self.end_sprite_scaled[frame_index % len(self.end_sprite_scaled)]
            anchor = 'midleft' if going_right else 'midright'
            rect = end_frame.get_rect(**{anchor: (pos, screen_y + tip_offset)})
            screen.blit(end_frame, rect)