"""Big Bang Kamehameha — reuses BeamAttack's growing/tiling/decay beam
pipeline wholesale, but loads its art the "modern" way FlameKamehamehaAttack
does: every sheet is drawn ONCE, facing 'right', as a single row of frames —
there's no separate row per direction like BeamAttack's own load_sprites()
expects. Every other direction (up/left/down) is derived here by rotating
those right-facing frames, instead of needing four hand-drawn copies of the
same art.

Asset layout expected under assets/sprites/attacks/big_bang_kamehameha/
(each a single row of frames, facing right):
    ball_big_bang_kamehameha.png
    circle_big_bang_kamehameha.png
    begin_big_bang_kamehameha.png
    middle_big_bang_kamehameha.png
    end_big_bang_kamehameha.png
    collision_big_bang_kamehameha.png
    decay_big_bang_kamehameha.png

The charge-up (held while charging, before the beam above fires) is NOT
its own asset — BigBangKamehamehaChargeEffect below reuses the plain
Kamehameha's existing charging_kamehameha.png and position wholesale (see
that class's docstring), so there's no charging_big_bang_kamehameha.png
to add here.

The ball/circle overlays are still drawn by BeamAttack at the exact same
anchor/frame as begin_sprite (see beam.py's _draw_ball_circle_overlay) —
that part is unaffected by the rotate-based loading below.
"""

import pygame
from attacks.beam import BeamAttack, KamehamehaChargeEffect
from config.settings import RENDER_SCALE as _RENDER_SCALE


class BigBangKamehamehaAttack(BeamAttack):
    """The projectile itself — the beam that flies out once fully charged.

    Everything about growth, tiling, decay, the collision-tip swap, and the
    ball/circle overlay is inherited unchanged from BeamAttack — the only
    thing overridden here is load_sprites(), to read single-row
    right-facing sheets and rotate them per direction (see
    _rotate_frames/direction_to_angle) instead of reading a different sheet
    row per direction.
    """

    # pygame.transform.rotate is counter-clockwise for positive angles:
    # right stays as-drawn, up/left/down are each a further 90 degrees
    # around. Class attribute (not set in __init__) so it's already
    # available the moment BeamAttack.__init__ calls self.load_sprites().
    direction_to_angle = {'right': 0, 'up': 90, 'left': 180, 'down': 270}

    def __init__(self, x, y, direction, scale=_RENDER_SCALE, **kwargs):
        kwargs.pop('attack_name', None)  # this attack's identity is fixed

        # begin/end/collision do NOT share one grid for this attack (unlike
        # plain BeamAttack, where they do) — each is its own sheet with its
        # own per-frame size. Slicing end/collision using begin's
        # frame_width/frame_height was the bug that made the end sprite
        # silently fail to load: _load_row's subsurface() rect no longer
        # lined up with the actual sheet, threw (or worse, integer-divided
        # to 0 frames and "succeeded" with nothing loaded), leaving
        # end_sprite empty with nothing drawn.
        #
        # begin_frame_width/height, end_frame_width/height, and
        # collision_frame_width/height are read directly by load_sprites()
        # below and are NOT forwarded to BeamAttack.__init__ (which has no
        # concept of per-part frame sizes — it only knows a single
        # frame_width/frame_height, used for its own dimension-calc/
        # end-cap-placement math, defaulted from end_frame_width/height
        # below so the two stay in sync unless deliberately overridden).
        self.begin_frame_width = kwargs.pop('begin_frame_width', 13)
        self.begin_frame_height = kwargs.pop('begin_frame_height', 16)
        self.end_frame_width = kwargs.pop('end_frame_width', 16)
        self.end_frame_height = kwargs.pop('end_frame_height', 16)
        self.collision_frame_width = kwargs.pop('collision_frame_width', 16)
        self.collision_frame_height = kwargs.pop('collision_frame_height', 32)

        super().__init__(
            x, y, direction,
            scale=scale,
            attack_name='big_bang_kamehameha',
            # This pair feeds BeamAttack's own calculate_scaled_dimensions()
            # math for where the end cap sits — it MUST track end's actual
            # sliced size (end_frame_width/height above), not just default
            # independently, or the middle keeps growing/tiling past where
            # beam.py thinks the end cap starts while the real end sprite
            # is a different size, and visibly clips through it. Defaulting
            # to end_frame_width/height keeps them in sync automatically;
            # only pass frame_width/frame_height explicitly if this attack
            # ever needs beam.py's placement math to deliberately diverge
            # from end's real sprite size.
            frame_width=kwargs.pop('frame_width', self.end_frame_width),
            frame_height=kwargs.pop('frame_height', self.end_frame_height),
            middle_frame_width=kwargs.pop('middle_frame_width', 16),
            middle_frame_height=kwargs.pop('middle_frame_height', 16),
            # None (the BeamAttack default) falls back to middle's size —
            # pass these explicitly only if decay's sheet uses a different
            # grid than the middle tile.
            decay_frame_width=kwargs.pop('decay_frame_width', 8),
            decay_frame_height=kwargs.pop('decay_frame_height', 16),
            # None (the BeamAttack default) falls back to frame_width/
            # frame_height — which now defaults to end's real size (see
            # above) — pass these explicitly if ball's sheet uses a
            # different grid than end.
            ball_frame_width=kwargs.pop('ball_frame_width', 32),
            ball_frame_height=kwargs.pop('ball_frame_height', 32),
            circle_frame_width=kwargs.pop('circle_frame_width', 16),
            circle_frame_height=kwargs.pop('circle_frame_height', 64),
            circle_gap=kwargs.pop('circle_gap', 30),
            ball_gap=kwargs.pop('ball_gap', -10),
            beam_gap=kwargs.pop('beam_gap', 10),
            # BeamAttack's default (0.5) is tuned for the plain Kamehameha's
            # begin sprite, which — after trim — is a tapered orb that's
            # visually "done" halfway through its own bounding box, so a
            # middle tile overlapping its back half blends the two
            # together. big_bang_kamehameha's begin sprite is not that
            # shape (it fills its frame edge to edge, like final_flash's),
            # so inheriting 0.5 made the middle tile start drawing HALFWAY
            # INSIDE begin's own footprint — visibly overlapping/cutting
            # off its back half instead of aligning right after it (see
            # beam.py's own comment on begin_overlap_ratio for this exact
            # failure mode). 1.0 starts the middle tile right after begin
            # ends, with no overlap at all — tune down from there only if
            # this specific art wants a slight blend.
            begin_overlap_ratio=kwargs.pop('begin_overlap_ratio', 1.0),
            **kwargs,
        )

        # No tip_overshoot_guard_height/width assignment needed here:
        # beam.py's middle-tile loop and decay-marker cropping now both
        # crop their final tile to fit exactly against the tip's reserved
        # boundary (see _tip_reserved_height/_width and the crop logic in
        # _draw_vertical_down et al.) instead of stopping a whole tile
        # early to avoid overshoot — which used to be the guard's job, at
        # the cost of leaving that same tile-length as a gap before the
        # tip. Cropping gets the "no overshoot" guarantee without that
        # trade-off, which matters here given how close big_bang_
        # kamehameha's end (26x16 default) and collision (16x32 default)
        # sprites are in size to its own 16x16 middle tile.

    def _rotate_frames(self, frames):
        """Rotate a list of right-facing frames to match self.direction —
        same helper as FlameKamehamehaAttack._rotate_frames. No-ops (and
        returns frames untouched) for 'right' or an empty list, since
        rotating by 0 degrees would just be a wasted copy."""
        if not frames:
            return frames
        angle = self.direction_to_angle.get(self.direction, 0)
        if angle == 0:
            return frames
        return [pygame.transform.rotate(frame, angle) for frame in frames]

    def _load_row(self, filename_part, frame_w, frame_h, label):
        """Load one sheet as a single row of frame_w x frame_h frames
        (drawn once facing right), trim, then rotate for self.direction.
        Returns None (not a crash) if the file's missing — same
        graceful-degradation contract BeamAttack's own load_sprites() uses
        for collision/decay/ball/circle."""
        try:
            path = f'assets/sprites/attacks/{self.attack_name}/{filename_part}_{self.attack_name}.png'
            sheet = pygame.image.load(path).convert_alpha()
            if frame_w <= 0 or sheet.get_width() % frame_w != 0:
                raise ValueError(
                    f"sheet width {sheet.get_width()} is not evenly divisible "
                    f"by frame_w {frame_w} (frame_h {frame_h}) — wrong frame "
                    f"size for this sheet"
                )
            frames_per_row = sheet.get_width() // frame_w
            if frames_per_row == 0:
                raise ValueError(f"frame_w {frame_w} is larger than sheet width {sheet.get_width()}")
            frames = []
            for i in range(frames_per_row):
                x = i * frame_w
                frames.append(sheet.subsurface(pygame.Rect(x, 0, frame_w, frame_h)))
            frames = self._rotate_frames(self._trim_frames(frames))
            print(f"Loaded {len(frames)} {label} sprites for direction {self.direction}")
            return frames
        except Exception as e:
            # Note frame_w/frame_h here — a wrong size for THIS sheet (as
            # opposed to a missing/unreadable file) throws from the
            # subsurface() rect above, and looks identical to a missing
            # file unless the attempted dims are printed too.
            print(f"No big_bang_kamehameha {label} sprite loaded from {path} "
                  f"(tried {frame_w}x{frame_h} frames): {e}")
            return None

    def load_sprites(self):
        """Overrides BeamAttack.load_sprites() entirely — see the class
        docstring for why. calculate_scaled_dimensions() (inherited,
        unchanged) doesn't need to know any of this happened: it just reads
        each frame list's actual pixel size, which is already correctly
        oriented (width/height swapped for the 90/270 rotations) by the
        time it runs."""
        self.begin_sprite = self._load_row('begin', self.begin_frame_width, self.begin_frame_height, 'begin')
        # Unlike flame_kamehameha, middle here is its own sheet/art (a
        # tileable beam-body segment), not a reuse of begin's — same as
        # plain BeamAttack.
        self.middle_sprite = self._load_row('middle', self.middle_frame_width, self.middle_frame_height, 'middle')
        self.end_sprite = self._load_row('end', self.end_frame_width, self.end_frame_height, 'end')
        self.collision_sprite = self._load_row('collision', self.collision_frame_width, self.collision_frame_height, 'collision')
        self.decay_sprite = self._load_row('decay', self.decay_frame_width, self.decay_frame_height, 'decay')
        self.ball_sprite = self._load_row('ball', self.ball_frame_width, self.ball_frame_height, 'ball')
        self.circle_sprite = self._load_row('circle', self.circle_frame_width, self.circle_frame_height, 'circle')

        self.use_sprites = any([self.begin_sprite, self.middle_sprite, self.end_sprite])
        if not self.use_sprites:
            print("No big_bang_kamehameha sprites loaded, using fallback rendering")


class BigBangKamehamehaChargeEffect(KamehamehaChargeEffect):
    """The player-anchored charge-up animation shown while holding the
    button before the beam above actually fires.

    Big Bang Kamehameha's charge-up deliberately reuses the plain
    Kamehameha's own charging_kamehameha.png art and position — there's no
    separate charging_big_bang_kamehameha.png asset, unlike the beam art
    above (begin/middle/end/etc.) which IS unique to this attack. Passing
    attack_name='kamehameha' here (instead of 'big_bang_kamehameha', which
    BigBangKamehamehaAttack above locks itself to) is what makes
    KamehamehaChargeEffect's own load_sprites() read
    assets/sprites/attacks/kamehameha/charging_kamehameha.png — the exact
    same file/position the regular beam's charge effect already uses — so
    nothing needs duplicating for this attack's charge-up. This subclass
    still exists (rather than instantiating KamehamehaChargeEffect
    directly in player.py) so this choice is documented in one obvious
    place.
    """

    def __init__(self, player, scale=_RENDER_SCALE, **kwargs):
        kwargs.pop('attack_name', None)
        super().__init__(
            player,
            scale=scale,
            attack_name='kamehameha',
            **kwargs,
        )