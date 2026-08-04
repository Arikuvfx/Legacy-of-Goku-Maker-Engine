import math
import random
import pygame
from config.settings import RENDER_SCALE as _RENDER_SCALE
from core.draw_layers import get_beam_layer, DrawLayer


class BeamAttack:
    def __init__(self, x, y, direction, scale=_RENDER_SCALE, attack_name='kamehameha',
                 grow_speed=900, decay_speed=900,
                 frame_width=16, frame_height=16,
                 begin_frame_width=None, begin_frame_height=None,
                 end_frame_width=None, end_frame_height=None,
                 collision_frame_width=None, collision_frame_height=None,
                 middle_frame_width=6, middle_frame_height=6,
                 decay_frame_width=None, decay_frame_height=None,
                 decay_uses_begin_sprite=False,
                 begin_overlap_ratio=0.5,
                 thickness_grow_duration=0.0, thickness_shrink_duration=0.0,
                 instant_length=False, instant_reach=5000,
                 decay_style='sweep', ignore_enemy_obstruction=False,
                 push_force=None,
                 ball_frame_width=None, ball_frame_height=None,
                 circle_frame_width=None, circle_frame_height=None,
                 circle_gap=0, ball_gap=0, beam_gap=0,
                 middle_sync_random=False, rotate_to_direction=False):
        self.x = x
        self.y = y
        self.direction = direction
        self.scale = scale  # Scale factor for the beam
        # Which sprite folder/filenames to load (see load_sprites) — lets
        # subclasses like FinalFlashAttack reuse this entire class's
        # rendering pipeline while pointing at their own art. Expects
        # assets/sprites/attacks/{attack_name}/{begin,middle,end,
        # collision,decay}_{attack_name}.png, laid out exactly like the
        # kamehameha set (same per-direction rows, same frame grid).
        self.attack_name = attack_name
        # Middle tile's native (unscaled) frame size in the spritesheet —
        # the kamehameha set uses 6x6 tiles, but this isn't universal
        # (final_flash's middle tiles are a full 16x16), so it's a
        # constructor param rather than the hardcoded 6/6 it used to be.
        # decay tiles default to matching the middle tile size, since the
        # decay sweep replaces middle tiles one-for-one — pass
        # decay_frame_width/height explicitly if that attack's decay sheet
        # actually uses a different grid.
        self.middle_frame_width = middle_frame_width
        self.middle_frame_height = middle_frame_height
        self.decay_frame_width = decay_frame_width if decay_frame_width is not None else middle_frame_width
        self.decay_frame_height = decay_frame_height if decay_frame_height is not None else middle_frame_height
        # When True, skip loading a separate decay_{attack_name}.png sheet
        # entirely and just point self.decay_sprite at the already-loaded
        # begin_sprite frames instead (see load_sprites) — for attacks
        # whose decay sweep should look identical to their begin sprite
        # rather than needing its own art asset at all, e.g. banshee_blast.
        self.decay_uses_begin_sprite = decay_uses_begin_sprite
        # How far into the begin sprite's own footprint the middle tiles
        # start overlapping it, as a fraction of the begin sprite's size
        # (0.5 = start halfway through it, 1.0 = start right after it with
        # no overlap at all). 0.5 suits the kamehameha's begin sprite,
        # which — after transparent-padding trim — is a tapered orb shape
        # that's visually "done" partway through its own bounding box, so
        # overlapping a middle tile onto its back half blends the two
        # together. A begin sprite that instead fills its full frame edge
        # to edge (e.g. final_flash's untapered 16x16) needs 1.0, or a
        # middle tile ends up drawn on top of — visibly cutting off — the
        # back half of the begin sprite instead of blending with it.
        self.begin_overlap_ratio = begin_overlap_ratio
        self.length = 0
        # No hard distance cap anymore — the beam keeps extending for as
        # long as it's firing (i.e. as long as ki holds out / Q is held).
        # player.py already calls stop_beam() the instant ki hits 0 or Q is
        # released, so that's what actually ends the beam now, not an
        # arbitrary max_length. Kept as `inf` (rather than deleting the
        # concept entirely) so any external code that still reads
        # max_length keeps working without special-casing None.
        self.max_length = float('inf')
        self.grow_speed = grow_speed  # pixels per second — fast, punchy beam travel
        self.active = True

        # instant_length=True skips the grow_speed ramp entirely — self.length
        # snaps straight to its full reach (capped by any obstruction, same
        # as always) the very first update() after firing, instead of
        # climbing there over a few frames. instant_reach is what "full
        # reach" means when nothing has capped it via report_obstruction
        # (max_length still inf) — a generously large screen-space distance
        # so it reads as filling the screen instantly, same idea grow_speed
        # was approximating before, just with zero ramp-up at all now.
        self.instant_length = instant_length
        self.instant_reach = instant_reach

        # When True, report_obstruction(distance, source='enemy') calls are
        # ignored entirely — the beam passes straight through enemies
        # without stopping or ever showing the collision-sprite tip, while
        # still stopping normally at walls (source='wall', the default,
        # which this doesn't affect). enemy.py still separately checks
        # actual contact/reach for damage+push purposes using its own
        # world_length math, so this only removes the "stop/cut off at the
        # enemy" visual — damage still applies.
        self.ignore_enemy_obstruction = ignore_enemy_obstruction

        # Per-frame push distance (world px) applied to an enemy while this
        # beam is in continuous contact with it — see enemy.py's
        # check_collision_with_attack 'beam' branch and _push_from_beam().
        # None (the default) means "use the enemy's own beam_push_force",
        # which is what every beam did before this existed — set an actual
        # number here to override that per-attack, e.g. a lower value than
        # the kamehameha's default if this attack's beam stays in contact
        # for a lot more frames (longer hold, wider corridor, etc.) and the
        # per-frame push was compounding into a much bigger total shove.
        self.push_force = push_force

        # Decay: when the player releases the fire button, the beam doesn't
        # just vanish — a "consumed" front sweeps from the player's end
        # toward the tip, replacing begin/middle sprites with decay_sprite
        # as it passes, until it reaches the tip and the beam fully
        # disappears. decay_speed matches grow_speed by design (the decay
        # sweep should feel like the same beam retracting at the same pace
        # it shot out).
        #
        # decay_style picks between two totally different closing behaviors:
        #   'sweep'     — the above: a front sweeps along self.length,
        #                 painting decay_sprite as it goes (needs
        #                 decay_speed and decay_sprite_scaled).
        #   'thickness' — no lengthwise sweep at all: self.length stays put
        #                 and the whole beam just narrows back down in
        #                 thickness (see the width_scale ramp below) until
        #                 it's fully closed, then disappears — the mirror
        #                 image of how it opened, rather than a front
        #                 retracting along its length. Meant for beams using
        #                 instant_length=True, where there's no meaningful
        #                 "sweep back along the length" to show since the
        #                 length never visibly grew in the first place.
        self.decaying = False
        self.decay_length = 0.0
        self.decay_speed = decay_speed
        self.decay_style = decay_style

        # Thickness ramp: separate from self.length (which handles how FAR
        # the beam reaches — see grow_speed above), this handles how WIDE
        # (cross-axis, perpendicular to travel direction) it is, animating
        # from 0 up to full width over thickness_grow_duration seconds right
        # after firing, then back down to 0 over thickness_shrink_duration
        # seconds once decay starts. Left at 0.0 duration (the default) this
        # is a no-op — width_scale just sits at 1.0 forever, so it changes
        # nothing for beams like the kamehameha that don't pass these in.
        # Final Flash sets both to get its "pillar snaps in, then collapses
        # back down" launch/release look, independent of how fast self.length
        # itself is growing (see FinalFlashAttack's grow_speed, which is
        # deliberately near-instant and otherwise unrelated to this).
        self.thickness_grow_duration = thickness_grow_duration
        self.thickness_shrink_duration = thickness_shrink_duration
        self._thickness_age = 0.0       # time since spawn, drives the grow-in ramp
        self._thickness_decay_age = 0.0  # time since decay started, drives the shrink-out ramp
        self.width_scale = 0.0 if thickness_grow_duration > 0 else 1.0

        # Obstruction handling: an enemy or a collision_wall in the beam's
        # path calls report_obstruction(distance) every frame it's in the
        # way. update() consumes the smallest distance reported since the
        # last update() call and caps growth there, so the beam waits at
        # that point instead of passing through — see report_obstruction()
        # and update() below for the full contract.
        self._reported_block_distance = None
        # Latches True the first time the beam grows up to a reported cap
        # and stays True for the rest of this beam's life (see update()) —
        # used by the draw methods to swap the tip sprite to collision_sprite.
        self.blocked = False

        # Animation
        self.current_frame = 0
        self.frame_timer = 0
        self.frame_duration = 0.08  # Time per frame in seconds

        # When True, every middle tile shows the SAME frame at once, and
        # that frame is a fresh random pick each tick rather than the
        # sequential (current_frame + middle_index) march the default
        # kamehameha-style beams use — this is what gives banshee_blast
        # its uniform, all-at-once flicker between its 3 middle sprites
        # instead of the traveling-wave look. _synced_middle_frame is
        # rolled once per current_frame tick in update() and just read
        # (never advanced) by every middle-tile draw site.
        self.middle_sync_random = middle_sync_random
        self._synced_middle_frame = 0

        # Sprite dimensions (single frame dimensions). frame_width/height is
        # the shared fallback default — 16x16, the kamehameha set's native
        # grid — for whichever of begin/end/collision don't get their own
        # override below. Kept around (rather than replaced outright) so
        # existing subclasses that only ever passed frame_width/height
        # keep working unchanged.
        self.frame_width = frame_width  # Width of each frame in spritesheet
        self.frame_height = frame_height  # Height of each frame in spritesheet

        # begin_/end_/collision_frame_width/height let each of those three
        # sprite sheets use its own tile grid instead of all three being
        # forced to share self.frame_width/height — e.g. banshee_blast's
        # begin sheet can be a different size than its end or collision
        # sheet. Each defaults to the shared frame_width/height above when
        # not passed, same pattern as ball_/circle_frame_width/height below
        # and decay_frame_width/height defaulting to middle's grid.
        self.begin_frame_width = begin_frame_width if begin_frame_width is not None else self.frame_width
        self.begin_frame_height = begin_frame_height if begin_frame_height is not None else self.frame_height
        self.end_frame_width = end_frame_width if end_frame_width is not None else self.frame_width
        self.end_frame_height = end_frame_height if end_frame_height is not None else self.frame_height
        self.collision_frame_width = collision_frame_width if collision_frame_width is not None else self.frame_width
        self.collision_frame_height = collision_frame_height if collision_frame_height is not None else self.frame_height

        # No longer read anywhere (see _draw_vertical_down et al. and
        # _draw_decay_marker_vertical/horizontal below): the middle-tile
        # loop and the decay sweep's marker both now crop their final tile
        # to fit exactly against the tip's reserved boundary instead of
        # stopping a whole tile-length early to avoid overshooting past
        # it — the old fixed-size guard this pair of attributes fed into.
        # Cropping prevents the same overshoot without leaving the gap the
        # guard traded it for, so the guard concept is obsolete. Left here
        # (rather than deleted) purely so BigBangKamehamehaAttack, the only
        # subclass that ever set these to a nonzero value, doesn't throw
        # setting an attribute that no longer exists — it's inert dead
        # weight now and safe to remove from that subclass too.
        self.tip_overshoot_guard_height = 0
        self.tip_overshoot_guard_width = 0

        # ball_{attack_name}.png / circle_{attack_name}.png (see load_sprites)
        # default to the same 16x16 grid as begin/end, but can be overridden
        # independently in case that art uses a different tile size — same
        # idea as decay_frame_width/height defaulting to middle's grid above.
        self.ball_frame_width = ball_frame_width if ball_frame_width is not None else self.frame_width
        self.ball_frame_height = ball_frame_height if ball_frame_height is not None else self.frame_height
        self.circle_frame_width = circle_frame_width if circle_frame_width is not None else self.frame_width
        self.circle_frame_height = circle_frame_height if circle_frame_height is not None else self.frame_height

        # How far apart the ball/circle/beam trio sits, along the beam's
        # own travel axis (world px, pre-scale — multiplied by self.scale
        # like every other dimension here). Chained outward from the
        # player anchor rather than each being an independent offset from
        # some fixed point: ball_gap is the ball's distance from the
        # player; circle_gap is the circle's distance from the BALL (not
        # from the player); beam_gap is the beam's (begin/middle/end,
        # taken together) distance from the CIRCLE (not from the player or
        # ball). So the on-screen order outward from the player is always
        # ball -> circle -> beam, and bumping an earlier gap (say
        # ball_gap) carries everything chained after it (circle, beam)
        # out along with it, instead of needing every later number
        # adjusted too just to keep their relative spacing.
        # Positive = further from the player (deeper into the beam, i.e.
        # the same direction the beam is firing); 0 (the default for all
        # three) reproduces the old behavior of everything sharing one
        # exact point.
        self.ball_gap = ball_gap
        self.circle_gap = circle_gap
        self.beam_gap = beam_gap

        # Direction to row mapping (same as your player system)
        self.direction_to_row = {
            'down': 0,
            'left': 1,
            'right': 2,
            'up': 3
        }

        # rotate_to_direction=True is for attacks whose begin/middle/end/
        # collision/ball/circle/decay sheets are drawn facing 'right' only,
        # as a single row (no separate per-direction rows to index into) —
        # same idea as FlameKamehamehaAttack's own sheets. current_row
        # stays 0 (the sheet's only row) and load_sprites rotates each
        # sprite list via _rotate_frames to match self.direction instead.
        self.rotate_to_direction = rotate_to_direction
        self.direction_to_angle = {'right': 0, 'up': 90, 'left': 180, 'down': 270}

        # Get row for current direction
        self.current_row = 0 if rotate_to_direction else self.direction_to_row.get(direction, 0)

        # Sprites for each beam part
        self.begin_sprite = None
        self.middle_sprite = None
        self.end_sprite = None
        self.decay_sprite = None
        # Replaces the end (tip) sprite while self.blocked is True — an
        # impact/splash frame shown where the beam is pressed up against
        # an enemy or a collision wall instead of the normal pointed tip.
        self.collision_sprite = None

        # Optional extra pair of overlays drawn at the exact same anchor as
        # begin_sprite, on the same frame it appears — used by attacks like
        # big_bang_kamehameha that show a ball/circle flash right where the
        # beam starts. Purely additive: if ball_{attack_name}.png /
        # circle_{attack_name}.png don't exist for a given attack_name (e.g.
        # plain kamehameha), load_sprites() below just leaves these None and
        # nothing is drawn — every other beam is unaffected.
        self.ball_sprite = None
        self.circle_sprite = None

        # Initialize scaled dimensions
        self.begin_width_scaled = 0
        self.begin_height_scaled = 0
        self.middle_width_scaled = 0
        self.middle_height_scaled = 0
        self.end_width_scaled = 0
        self.end_height_scaled = 0
        self.decay_width_scaled = 0
        self.decay_height_scaled = 0
        self.collision_width_scaled = 0
        self.collision_height_scaled = 0
        self.ball_width_scaled = 0
        self.ball_height_scaled = 0
        self.circle_width_scaled = 0
        self.circle_height_scaled = 0

        self.begin_sprite_scaled = None
        self.middle_sprite_scaled = None
        self.end_sprite_scaled = None
        self.decay_sprite_scaled = None
        self.collision_sprite_scaled = None
        self.ball_sprite_scaled = None
        self.circle_sprite_scaled = None

        # Load beam sprites with correct direction
        self.load_sprites()

        # Calculate scaled dimensions
        self.calculate_scaled_dimensions()

        # Determine beam width based on sprites (for collision if needed)
        self.width = max(
            self.begin_width_scaled,
            self.middle_width_scaled,
            self.end_width_scaled,
            30  # Minimum fallback width
        )

        # Set layer based on direction
        self.draw_layer = get_beam_layer(self.direction, self.direction)
        self.y_sort = False

    def get_sort_key(self):
        # Secondary key was hardcoded to 0 — for left/right, draw_layer is
        # DrawLayer.PLAYER, the SAME bucket enemies sort into via
        # (draw_layer, y + height // 2), which is always a positive number.
        # A fixed 0 secondary meant the beam sorted before (behind) every
        # enemy in that bucket no matter where either of them actually was,
        # so it never visually layered in front of an enemy it was pushing
        # into. Using self.y here — the same coordinate space/convention
        # entities sort by — lets it interleave properly instead.
        return (self.draw_layer, self.y)

    # Pixels with alpha at or below this are treated as background noise
    # (stray antialiasing fringe from art export) rather than real content
    # when computing the trim bounding box. min_alpha=1 in get_bounding_rect
    # counts ANY nonzero alpha as content — but some rows in these sheets
    # have a couple of alpha=1/2 pixels along an otherwise-empty edge
    # (invisible on screen, but enough to stop that edge from being
    # trimmed), which left a near-invisible-but-nonzero strip of padding
    # in the reported frame size. That strip is exactly what showed up as
    # a small visual gap between the end tip and the last middle tile for
    # 'down' specifically (its row has this noise; left/right's rows
    # happen not to). Raising the threshold treats that noise as
    # background so it gets trimmed away like everything else.
    _TRIM_MIN_ALPHA = 16

    @staticmethod
    def _trim_frames(frames):
        """Crop away any transparent border shared by every frame in a list.

        Sprite sheets are often laid out on a fixed grid (e.g. 16x16) even
        when the actual drawn content is smaller, leaving empty rows/columns
        of alpha=0 pixels around the art. Left untrimmed, code that sizes or
        positions things based on the raw frame dimensions (like the beam's
        middle-tile tiling loop) ends up leaving that empty space as a visual
        gap. This computes the union of each frame's opaque bounding box and
        crops every frame to that same rect, so all frames stay the same
        size (no animation jitter) and that size reflects real content.
        """
        if not frames:
            return frames

        bbox = None
        for frame in frames:
            frame_bbox = frame.get_bounding_rect(min_alpha=BeamAttack._TRIM_MIN_ALPHA)
            if frame_bbox.width == 0 or frame_bbox.height == 0:
                continue
            bbox = frame_bbox if bbox is None else bbox.union(frame_bbox)

        # Fully transparent sprite (or nothing detected) - nothing safe to trim
        if bbox is None or bbox.width == 0 or bbox.height == 0:
            return frames

        return [frame.subsurface(bbox).copy() for frame in frames]

    def _rotate_frames(self, frames):
        """Rotate a list of right-facing frames to match self.direction —
        same helper as FlameKamehamehaAttack._rotate_frames/
        KamehamehaChargeEffect._rotate_frames, for sheets that are only
        ever drawn facing 'right' (see rotate_to_direction). Returns
        frames unrotated for 'right' or an unrecognized direction, and
        passes through None/empty untouched, since callers pass the raw
        result of _trim_frames which can be None on a load failure."""
        angle = self.direction_to_angle.get(self.direction, 0)
        if angle == 0 or not frames:
            return frames
        return [pygame.transform.rotate(frame, angle) for frame in frames]

    def load_sprites(self):
        """Load beam sprites from spritesheets with correct direction row and all animation frames"""
        try:
            # Load begin spritesheet
            begin_sheet = pygame.image.load(f'assets/sprites/attacks/{self.attack_name}/begin_{self.attack_name}.png').convert_alpha()
            begin_sheet_width = begin_sheet.get_width()
            begin_sheet_height = begin_sheet.get_height()

            # Calculate number of frames per row
            frames_per_row = begin_sheet_width // self.begin_frame_width

            # Get ALL frames for current direction row
            begin_frames = []
            for frame_index in range(frames_per_row):
                x = frame_index * self.begin_frame_width
                y = self.current_row * self.begin_frame_height
                frame = begin_sheet.subsurface(pygame.Rect(x, y, self.begin_frame_width, self.begin_frame_height))
                begin_frames.append(frame)

            self.begin_sprite = self._trim_frames(begin_frames)  # Store as LIST of frames
            if self.rotate_to_direction:
                self.begin_sprite = self._rotate_frames(self.begin_sprite)
            print(f"Loaded {len(self.begin_sprite)} begin sprites for direction {self.direction}")

        except Exception as e:
            print(f"Error loading begin beam sprite: {e}")
            self.begin_sprite = None

        try:
            # Load middle spritesheet
            middle_sheet = pygame.image.load(f'assets/sprites/attacks/{self.attack_name}/middle_{self.attack_name}.png').convert_alpha()
            middle_sheet_width = middle_sheet.get_width()
            middle_sheet_height = middle_sheet.get_height()

            # Middle sprite dimensions might be different
            middle_width = self.middle_frame_width  # Width of middle part
            middle_height = self.middle_frame_height  # Height of middle part

            # Calculate number of frames per row
            frames_per_row = middle_sheet_width // middle_width

            # Get ALL frames for current direction row
            middle_frames = []
            for frame_index in range(frames_per_row):
                x = frame_index * middle_width
                y = self.current_row * middle_height
                # Make sure we don't go out of bounds
                if y + middle_height <= middle_sheet_height:
                    frame = middle_sheet.subsurface(pygame.Rect(x, y, middle_width, middle_height))
                    middle_frames.append(frame)

            self.middle_sprite = self._trim_frames(middle_frames)  # Store as LIST of frames
            if self.rotate_to_direction:
                self.middle_sprite = self._rotate_frames(self.middle_sprite)
            print(f"Loaded {len(self.middle_sprite)} middle sprites for direction {self.direction}")

        except Exception as e:
            print(f"Error loading middle beam sprite: {e}")
            self.middle_sprite = None

        try:
            # Load end spritesheet
            end_sheet = pygame.image.load(f'assets/sprites/attacks/{self.attack_name}/end_{self.attack_name}.png').convert_alpha()
            end_sheet_width = end_sheet.get_width()
            end_sheet_height = end_sheet.get_height()

            # Calculate number of frames per row
            frames_per_row = end_sheet_width // self.end_frame_width

            # Get ALL frames for current direction row
            end_frames = []
            for frame_index in range(frames_per_row):
                x = frame_index * self.end_frame_width
                y = self.current_row * self.end_frame_height
                frame = end_sheet.subsurface(pygame.Rect(x, y, self.end_frame_width, self.end_frame_height))
                end_frames.append(frame)

            # The end tip sprite sheet has a transparent margin baked in on
            # each side (so it's stored as a full 16x16 tile), but that empty
            # margin isn't part of the beam's actual visible width/height.
            # Since the middle-tile placement math uses end_height_scaled /
            # end_width_scaled to know where to stop tiling, that untrimmed
            # padding shows up as a visible gap between the last middle tile
            # and the tip. Trim it off here so the sprite's reported size
            # matches its actual visible content.
            end_frames = self._trim_frames(end_frames)
            if self.rotate_to_direction:
                end_frames = self._rotate_frames(end_frames)

            self.end_sprite = end_frames  # Store as LIST of frames
            print(f"Loaded {len(end_frames)} end sprites for direction {self.direction}")

        except Exception as e:
            print(f"Error loading end beam sprite: {e}")
            self.end_sprite = None

        try:
            # Load collision/impact spritesheet — shown at the tip instead
            # of end_kamehameha while the beam is pressed up against an
            # enemy or a wall (self.blocked). Same sheet layout as
            # end_kamehameha.png. If this asset doesn't exist yet, this
            # fails gracefully (collision_sprite stays None) and the draw
            # methods just keep showing the normal end tip instead.
            collision_sheet = pygame.image.load(f'assets/sprites/attacks/{self.attack_name}/collision_{self.attack_name}.png').convert_alpha()
            collision_sheet_width = collision_sheet.get_width()
            collision_sheet_height = collision_sheet.get_height()

            frames_per_row = collision_sheet_width // self.collision_frame_width

            collision_frames = []
            for frame_index in range(frames_per_row):
                x = frame_index * self.collision_frame_width
                y = self.current_row * self.collision_frame_height
                if y + self.collision_frame_height <= collision_sheet_height:
                    frame = collision_sheet.subsurface(pygame.Rect(x, y, self.collision_frame_width, self.collision_frame_height))
                    collision_frames.append(frame)

            collision_frames = self._trim_frames(collision_frames)
            if self.rotate_to_direction:
                collision_frames = self._rotate_frames(collision_frames)
            self.collision_sprite = collision_frames
            print(f"Loaded {len(collision_frames)} collision sprites for direction {self.direction}")

        except Exception as e:
            print(f"No collision beam sprite loaded (tip will stay as end_kamehameha when blocked): {e}")
            self.collision_sprite = None

        try:
            # Load ball spritesheet — one of two optional overlays (see
            # circle below) drawn at the same anchor as begin_sprite, on
            # the same frame it appears. Same per-direction-row layout as
            # begin/end. Missing file (e.g. plain kamehameha has none) is
            # expected and fine — self.ball_sprite just stays None and
            # nothing extra is drawn.
            ball_sheet = pygame.image.load(f'assets/sprites/attacks/{self.attack_name}/ball_{self.attack_name}.png').convert_alpha()
            ball_sheet_height = ball_sheet.get_height()
            frames_per_row = ball_sheet.get_width() // self.ball_frame_width

            ball_frames = []
            for frame_index in range(frames_per_row):
                x = frame_index * self.ball_frame_width
                y = self.current_row * self.ball_frame_height
                if y + self.ball_frame_height <= ball_sheet_height:
                    frame = ball_sheet.subsurface(pygame.Rect(x, y, self.ball_frame_width, self.ball_frame_height))
                    ball_frames.append(frame)

            self.ball_sprite = self._trim_frames(ball_frames)
            if self.rotate_to_direction:
                self.ball_sprite = self._rotate_frames(self.ball_sprite)
            print(f"Loaded {len(self.ball_sprite)} ball sprites for direction {self.direction}")

        except Exception as e:
            print(f"No ball beam sprite loaded (this attack has no ball overlay): {e}")
            self.ball_sprite = None

        try:
            # Load circle spritesheet — the other optional overlay, same
            # layout/anchor rules as ball above.
            circle_sheet = pygame.image.load(f'assets/sprites/attacks/{self.attack_name}/circle_{self.attack_name}.png').convert_alpha()
            circle_sheet_height = circle_sheet.get_height()
            frames_per_row = circle_sheet.get_width() // self.circle_frame_width

            circle_frames = []
            for frame_index in range(frames_per_row):
                x = frame_index * self.circle_frame_width
                y = self.current_row * self.circle_frame_height
                if y + self.circle_frame_height <= circle_sheet_height:
                    frame = circle_sheet.subsurface(pygame.Rect(x, y, self.circle_frame_width, self.circle_frame_height))
                    circle_frames.append(frame)

            self.circle_sprite = self._trim_frames(circle_frames)
            if self.rotate_to_direction:
                self.circle_sprite = self._rotate_frames(self.circle_sprite)
            print(f"Loaded {len(self.circle_sprite)} circle sprites for direction {self.direction}")

        except Exception as e:
            print(f"No circle beam sprite loaded (this attack has no circle overlay): {e}")
            self.circle_sprite = None

        if self.decay_uses_begin_sprite:
            # No separate decay_{attack_name}.png to load at all — the decay
            # sweep just reuses begin_sprite's already-loaded, already-
            # trimmed-and-rotated frames wholesale (begin_sprite is loaded
            # earlier in this same method, above). Scaling downstream (see
            # the "Handle decay sprite" block) reads sizes off these frames
            # directly, so whatever begin's actual trimmed size is just
            # works — self.decay_frame_width/height are ignored here.
            self.decay_sprite = self.begin_sprite
            print(f"Decay sprite reuses begin sprite ({len(self.decay_sprite) if self.decay_sprite else 0} frames)")
        else:
            try:
                # Load decay spritesheet — assumed to follow the same layout
                # as middle_kamehameha.png (4 direction rows, 6x6 tiles),
                # since it replaces middle/begin tiles as the decay front
                # sweeps through. If this path or layout is wrong for your
                # actual asset, this fails gracefully below (decay_sprite
                # stays None) rather than crashing — the beam will just pop
                # out instantly at the end of its decay window instead of
                # showing the sweep animation.
                decay_sheet = pygame.image.load(f'assets/sprites/attacks/{self.attack_name}/decay_{self.attack_name}.png').convert_alpha()
                decay_sheet_width = decay_sheet.get_width()
                decay_sheet_height = decay_sheet.get_height()

                decay_width = self.decay_frame_width
                decay_height = self.decay_frame_height

                frames_per_row = decay_sheet_width // decay_width

                decay_frames = []
                for frame_index in range(frames_per_row):
                    x = frame_index * decay_width
                    y = self.current_row * decay_height
                    if y + decay_height <= decay_sheet_height:
                        frame = decay_sheet.subsurface(pygame.Rect(x, y, decay_width, decay_height))
                        decay_frames.append(frame)

                self.decay_sprite = self._trim_frames(decay_frames)
                if self.rotate_to_direction:
                    self.decay_sprite = self._rotate_frames(self.decay_sprite)
                print(f"Loaded {len(self.decay_sprite)} decay sprites for direction {self.direction}")

            except Exception as e:
                print(f"Error loading decay beam sprite (decay sweep will be skipped): {e}")
                self.decay_sprite = None

        # Check if we have at least some sprites
        self.use_sprites = any([self.begin_sprite, self.middle_sprite, self.end_sprite])

        if not self.use_sprites:
            print("No beam sprites loaded, using fallback rendering")

    def calculate_scaled_dimensions(self):
        """Calculate scaled dimensions for all sprites - handle lists of frames"""
        # Handle begin sprite (might be a list)
        if self.begin_sprite and isinstance(self.begin_sprite, list) and len(self.begin_sprite) > 0:
            # Use first frame to determine dimensions
            begin_rect = self.begin_sprite[0].get_rect()
            self.begin_width_scaled = int(begin_rect.width * self.scale)
            self.begin_height_scaled = int(begin_rect.height * self.scale)

            # Scale all frames in the list
            self.begin_sprite_scaled = []
            for frame in self.begin_sprite:
                scaled_frame = pygame.transform.scale(frame, (self.begin_width_scaled, self.begin_height_scaled))
                self.begin_sprite_scaled.append(scaled_frame)

            print(
                f"Begin sprite: {len(self.begin_sprite)} frames scaled to {self.begin_width_scaled}x{self.begin_height_scaled}")
        elif self.begin_sprite:
            # Single sprite (fallback)
            begin_rect = self.begin_sprite.get_rect()
            self.begin_width_scaled = int(begin_rect.width * self.scale)
            self.begin_height_scaled = int(begin_rect.height * self.scale)
            self.begin_sprite_scaled = pygame.transform.scale(
                self.begin_sprite,
                (self.begin_width_scaled, self.begin_height_scaled)
            )
        else:
            # Use fallback scaled dimensions
            self.begin_width_scaled = int(16 * self.scale)
            self.begin_height_scaled = int(16 * self.scale)

        # Handle middle sprite
        if self.middle_sprite and isinstance(self.middle_sprite, list) and len(self.middle_sprite) > 0:
            middle_rect = self.middle_sprite[0].get_rect()
            self.middle_width_scaled = int(middle_rect.width * self.scale)
            self.middle_height_scaled = int(middle_rect.height * self.scale)

            self.middle_sprite_scaled = []
            for frame in self.middle_sprite:
                scaled_frame = pygame.transform.scale(frame, (self.middle_width_scaled, self.middle_height_scaled))
                self.middle_sprite_scaled.append(scaled_frame)

            print(
                f"Middle sprite: {len(self.middle_sprite)} frames scaled to {self.middle_width_scaled}x{self.middle_height_scaled}")
        elif self.middle_sprite:
            middle_rect = self.middle_sprite.get_rect()
            self.middle_width_scaled = int(middle_rect.width * self.scale)
            self.middle_height_scaled = int(middle_rect.height * self.scale)
            self.middle_sprite_scaled = pygame.transform.scale(
                self.middle_sprite,
                (self.middle_width_scaled, self.middle_height_scaled)
            )
        else:
            self.middle_width_scaled = int(self.middle_frame_width * self.scale)
            self.middle_height_scaled = int(self.middle_frame_height * self.scale)

        # Handle end sprite
        if self.end_sprite and isinstance(self.end_sprite, list) and len(self.end_sprite) > 0:
            end_rect = self.end_sprite[0].get_rect()
            self.end_width_scaled = int(end_rect.width * self.scale)
            self.end_height_scaled = int(end_rect.height * self.scale)

            self.end_sprite_scaled = []
            for frame in self.end_sprite:
                scaled_frame = pygame.transform.scale(frame, (self.end_width_scaled, self.end_height_scaled))
                self.end_sprite_scaled.append(scaled_frame)

            print(
                f"End sprite: {len(self.end_sprite)} frames scaled to {self.end_width_scaled}x{self.end_height_scaled}")
        elif self.end_sprite:
            end_rect = self.end_sprite.get_rect()
            self.end_width_scaled = int(end_rect.width * self.scale)
            self.end_height_scaled = int(end_rect.height * self.scale)
            self.end_sprite_scaled = pygame.transform.scale(
                self.end_sprite,
                (self.end_width_scaled, self.end_height_scaled)
            )
        else:
            self.end_width_scaled = int(16 * self.scale)
            self.end_height_scaled = int(16 * self.scale)

        # Handle collision sprite
        if self.collision_sprite and isinstance(self.collision_sprite, list) and len(self.collision_sprite) > 0:
            collision_rect = self.collision_sprite[0].get_rect()
            self.collision_width_scaled = int(collision_rect.width * self.scale)
            self.collision_height_scaled = int(collision_rect.height * self.scale)

            self.collision_sprite_scaled = []
            for frame in self.collision_sprite:
                scaled_frame = pygame.transform.scale(frame, (self.collision_width_scaled, self.collision_height_scaled))
                self.collision_sprite_scaled.append(scaled_frame)

            print(
                f"Collision sprite: {len(self.collision_sprite)} frames scaled to {self.collision_width_scaled}x{self.collision_height_scaled}")
        else:
            self.collision_width_scaled = self.end_width_scaled
            self.collision_height_scaled = self.end_height_scaled

        # Handle ball overlay sprite
        if self.ball_sprite and isinstance(self.ball_sprite, list) and len(self.ball_sprite) > 0:
            ball_rect = self.ball_sprite[0].get_rect()
            self.ball_width_scaled = int(ball_rect.width * self.scale)
            self.ball_height_scaled = int(ball_rect.height * self.scale)

            self.ball_sprite_scaled = []
            for frame in self.ball_sprite:
                scaled_frame = pygame.transform.scale(frame, (self.ball_width_scaled, self.ball_height_scaled))
                self.ball_sprite_scaled.append(scaled_frame)

            print(
                f"Ball sprite: {len(self.ball_sprite)} frames scaled to {self.ball_width_scaled}x{self.ball_height_scaled}")
        else:
            self.ball_width_scaled = 0
            self.ball_height_scaled = 0

        # Handle circle overlay sprite
        if self.circle_sprite and isinstance(self.circle_sprite, list) and len(self.circle_sprite) > 0:
            circle_rect = self.circle_sprite[0].get_rect()
            self.circle_width_scaled = int(circle_rect.width * self.scale)
            self.circle_height_scaled = int(circle_rect.height * self.scale)

            self.circle_sprite_scaled = []
            for frame in self.circle_sprite:
                scaled_frame = pygame.transform.scale(frame, (self.circle_width_scaled, self.circle_height_scaled))
                self.circle_sprite_scaled.append(scaled_frame)

            print(
                f"Circle sprite: {len(self.circle_sprite)} frames scaled to {self.circle_width_scaled}x{self.circle_height_scaled}")
        else:
            self.circle_width_scaled = 0
            self.circle_height_scaled = 0

        # Handle decay sprite
        if self.decay_sprite and isinstance(self.decay_sprite, list) and len(self.decay_sprite) > 0:
            decay_rect = self.decay_sprite[0].get_rect()
            self.decay_width_scaled = int(decay_rect.width * self.scale)
            self.decay_height_scaled = int(decay_rect.height * self.scale)

            self.decay_sprite_scaled = []
            for frame in self.decay_sprite:
                scaled_frame = pygame.transform.scale(frame, (self.decay_width_scaled, self.decay_height_scaled))
                self.decay_sprite_scaled.append(scaled_frame)

            print(
                f"Decay sprite: {len(self.decay_sprite)} frames scaled to {self.decay_width_scaled}x{self.decay_height_scaled}")
        else:
            self.decay_width_scaled = int(self.decay_frame_width * self.scale)
            self.decay_height_scaled = int(self.decay_frame_height * self.scale)

    def start_decay(self):
        """Begin the retract/decay sweep instead of instantly disappearing.

        Safe to call more than once (e.g. if stop is requested twice) —
        only the first call has any effect, so an in-progress decay never
        gets reset back to 0.
        """
        if not self.decaying:
            self.decaying = True
            self.decay_length = 0.0

    def report_obstruction(self, distance, source='wall'):
        ...
        if source == 'enemy' and self.ignore_enemy_obstruction:
            return
        if distance is None:
            return
        # `distance` is measured from the beam's raw origin to whatever's in
        # the way (screen px, already *scale) — but self.length is NOT the
        # same as "distance from origin to the tip": the tip actually renders
        # at self._min_reach() + self.length (see _min_reach()/
        # get_tip_world_length()). Capping self.length directly at `distance`
        # therefore lets the tip land self._min_reach() PAST the reported
        # point before growth stops. Subtracting _min_reach() here converts
        # the reported "where should the TIP stop" distance into the
        # self.length value that actually achieves that.
        distance = max(0, distance - self._min_reach())
        if self._reported_block_distance is None or distance < self._reported_block_distance:
            self._reported_block_distance = distance

    def update(self, dt):
        # Update animation
        if self.use_sprites:
            self.frame_timer += dt
            if self.frame_timer >= self.frame_duration:
                self.frame_timer = 0
                self.current_frame += 1
                if self.middle_sync_random and self.middle_sprite_scaled:
                    self._synced_middle_frame = random.randrange(len(self.middle_sprite_scaled))

        # Apply whatever obstruction(s) were reported since the last
        # update() — done every frame, growing OR decaying, not just while
        # actively firing. This only affects max_length/growth: if nothing
        # reports anymore (the obstruction moved away or was defeated),
        # this reverts to unbounded and the beam resumes growing. It does
        # NOT un-set self.blocked — that's a one-way latch set below, kept
        # separate specifically so the collision-sprite tip doesn't
        # flicker back to normal the instant an enemy the beam just hit
        # disappears, or the player lets go and decay starts.
        self.max_length = (
            self._reported_block_distance
            if self._reported_block_distance is not None
            else float('inf')
        )
        self._reported_block_distance = None

        if self.decaying:
            if self.decay_style == 'thickness':
                # No lengthwise sweep at all — self.length just stays put
                # and the beam closes back up in thickness instead, the
                # mirror of how it opened. Goes inactive once fully closed
                # rather than waiting on decay_length to catch up to length
                # (it never moves in this mode).
                if self.thickness_shrink_duration > 0:
                    self._thickness_decay_age += dt
                    self.width_scale = max(
                        0.0, 1.0 - (self._thickness_decay_age / self.thickness_shrink_duration)
                    )
                else:
                    self.width_scale = 0.0
                if self.width_scale <= 0.0:
                    self.active = False
            else:
                # Sweep the decay front from the player's end toward the
                # tip. Once it reaches the tip, the whole beam has been
                # consumed — mark inactive so whatever owns this beam
                # (player.py) knows to drop its reference and stop calling
                # update/draw on it.
                self.decay_length += self.decay_speed * dt
                if self.decay_length >= self.length:
                    self.decay_length = self.length
                    self.active = False

                # Shrink thickness back toward 0 as the beam closes — a
                # no-op (stays at 1.0) for beams with
                # thickness_shrink_duration=0.
                if self.thickness_shrink_duration > 0:
                    self._thickness_decay_age += dt
                    self.width_scale = max(
                        0.0, 1.0 - (self._thickness_decay_age / self.thickness_shrink_duration)
                    )
        else:
            if self.instant_length:
                # Snap straight to full reach — capped by any obstruction,
                # same as the ramped path below — instead of climbing there
                # over several frames. No ramp at all, by design.
                self.length = (
                    self.max_length if self.max_length != float('inf') else self.instant_reach
                )
            elif self.length < self.max_length:
                # Grow beam (only while not decaying — releasing the button
                # freezes self.length at whatever it had reached)
                self.length += self.grow_speed * dt
                if self.length > self.max_length:
                    self.length = self.max_length
            elif self.length > self.max_length:
                # self.length already sits ahead of this frame's cap —
                # e.g. a wall/enemy that reports a CLOSER obstruction than
                # whatever capped it before (or than it had already grown
                # past due to a detection lag elsewhere, like
                # CollisionObject.get_beam_block_distance used to have).
                # The growth branch above only ever clamps DURING growth
                # (self.length < max_length), so without this, a length
                # that's already past the new, smaller cap would just sit
                # there forever instead of retracting to it — this is what
                # let Big Bang Kamehameha's tip stay stuck deep inside (or
                # past) a wall instead of snapping back to the wall's near
                # edge once collision_object.py finally reported it.
                self.length = self.max_length

            # Grow thickness up toward full width right after launch — a
            # no-op (stays at 1.0) for beams with thickness_grow_duration=0.
            if self.thickness_grow_duration > 0 and self.width_scale < 1.0:
                self._thickness_age += dt
                self.width_scale = min(1.0, self._thickness_age / self.thickness_grow_duration)

        # Blocked = pressed up against a cap right now, OR was at some
        # earlier point during this beam's life — this is what the draw
        # methods check to swap in the collision sprite. Deliberately a
        # one-way latch (only ever set True, never back to False) rather
        # than being recomputed fresh every frame: the old recomputed
        # version reverted to False (and the tip snapped back to the
        # normal pointed sprite) the instant nothing reported an
        # obstruction that frame — which happened constantly, e.g. the
        # enemy the beam just hit dies and stops existing, or the player
        # releases fire and the beam is no longer overlapping anything.
        # Once the beam has actually connected with something, the impact
        # sprite should stay for the rest of this beam's life (through
        # release/decay included), not flicker back to normal.
        if self.max_length != float('inf') and self.length >= self.max_length:
            self.blocked = True

    def draw(self, screen, camera, colors):
        if not self.active:
            return

        from config.settings import RENDER_SCALE

        # Base position (convert world to screen coordinates)
        base_screen_x = (self.x * RENDER_SCALE) - camera.x
        base_screen_y = (self.y * RENDER_SCALE) - camera.y

        # Direction-dependent offsets (adjust as needed for your sprites).
        #
        # Only the CROSS-axis component is offset here now — a small nudge
        # so the beam visually centers on the character instead of looking
        # off to one side. The offset that used to also apply ALONG the
        # travel axis (-15/+15 x for left/right, -25/+12 y for down/up) has
        # been removed: check_collision_with_attack's 'beam' branch in
        # enemy.py measures blocking_distance from this same raw self.x/
        # self.y (no offset), so shifting the rendered origin along the
        # beam's own axis made the visual tip land short of (or, combined
        # with the old centered-tip overshoot, past) the point the collision
        # code was actually aiming for — the enemy's center, half its own
        # width/height in. Keeping the origin unshifted along that axis is
        # what makes self.length in the tip-drawing methods line up with
        # the collision distance exactly.
        if self.direction == 'right':
            screen_x = base_screen_x
            screen_y = base_screen_y - 5  # Cross-axis nudge only
        elif self.direction == 'left':
            screen_x = base_screen_x
            screen_y = base_screen_y - 5  # Cross-axis nudge only
        elif self.direction == 'down':
            screen_x = base_screen_x
            screen_y = base_screen_y
        elif self.direction == 'up':
            screen_x = base_screen_x
            screen_y = base_screen_y


        if self.use_sprites:
            self._draw_with_sprites(screen, screen_x, screen_y)
        else:
            self._draw_fallback(screen, screen_x, screen_y, colors)

    def _synced_frame_index(self, sprite_list):
        """Pick which frame of `sprite_list` to show right now.

        When middle_sync_random is on, begin/decay/end all follow the same
        self._synced_middle_frame the middle tiles use (see update() and
        the middle-tile loop's own frame_index calc) instead of advancing
        independently off self.current_frame — so the whole beam steps
        through frames together as one unified animation rather than begin,
        middle, and the tip each flickering on their own schedule. Falls
        back to the old self.current_frame-based behavior when
        middle_sync_random is off (e.g. plain kamehameha), so this is a
        no-op for every beam that doesn't opt in.

        `sprite_list` may have a different frame count than the middle
        sprite's own list, so the shared index is still taken modulo
        THIS list's length rather than assuming they match.
        """
        base = self._synced_middle_frame if self.middle_sync_random else self.current_frame
        return base % len(sprite_list)

    def _tip_frames(self):
        """Which sprite list to draw at the beam's tip this frame: the
        collision/impact sprite while pressed up against an enemy or wall
        (self.blocked), otherwise the normal pointed end sprite. Falls back
        to end_sprite_scaled if no collision sprite was loaded, so this is
        always safe to call even without the collision_kamehameha asset."""
        if self.blocked and self.collision_sprite_scaled and len(self.collision_sprite_scaled) > 0:
            return self.collision_sprite_scaled
        return self.end_sprite_scaled

    def _tip_reserved_height(self):
        """How much along-travel space (world px, scaled) the sprite
        _tip_frames() will actually draw at the tip right now needs
        reserved — collision_height_scaled once self.blocked has latched
        (and a collision sprite exists), otherwise end_height_scaled.

        Single source of truth for this so the middle-tile tiling loops
        and the decay-marker methods can't disagree about it. They used
        to: the decay markers already branched on self.blocked, but the
        middle loops hardcoded end_height_scaled unconditionally — so once
        a beam whose collision sprite is taller than its end sprite (e.g.
        BigBangKamehamehaAttack's default 32-tall collision vs 16-tall end)
        actually connected with something, the middle loop kept reserving
        only the shorter end-sprite's worth of space while the taller
        collision sprite was what got drawn there, letting a middle tile
        render underneath the collision sprite's extra footprint. Since
        self.blocked is a one-way latch (see update()), this only ever
        shows up once the beam is blocked/idle — matching that symptom —
        and reads as the last middle tile flickering as the collision
        sprite's own animation frames (if not uniformly opaque) reveal/hide
        the tile hidden behind them.
        """
        if self.blocked and self.collision_sprite_scaled and len(self.collision_sprite_scaled) > 0:
            return self.collision_height_scaled
        return self.end_height_scaled

    def _tip_reserved_width(self):
        """Cross-travel counterpart to _tip_reserved_height — see there."""
        if self.blocked and self.collision_sprite_scaled and len(self.collision_sprite_scaled) > 0:
            return self.collision_width_scaled
        return self.end_width_scaled

    def _scale_cross_axis(self, frame, axis):
        """Return `frame` scaled along its CROSS-travel axis by
        self.width_scale, leaving the along-travel-axis dimension
        untouched — that dimension is what the tiling loops use to know
        how many world-pixels this tile covers, so it must never change
        or tiles would drift out of sync with self.length.

        axis='width' for vertical beams (up/down — the along-travel axis
        is height, so the cross axis being thinned is width).
        axis='height' for horizontal beams (left/right — the reverse).

        A no-op (returns frame unchanged, no transform.scale call) once
        width_scale reaches ~1.0, which is always true for beams that
        don't pass thickness_grow_duration/thickness_shrink_duration
        (e.g. the kamehameha) — so this has zero cost or behavior change
        for them.
        """
        if self.width_scale >= 0.999:
            return frame
        w, h = frame.get_size()
        if axis == 'width':
            new_size = (max(1, int(w * self.width_scale)), h)
        else:
            new_size = (w, max(1, int(h * self.width_scale)))
        return pygame.transform.scale(frame, new_size)

    def _draw_decay_marker_vertical(self, screen, screen_x, screen_y, position, anchor):
        """Draw exactly ONE decay sprite frame, at `position` (distance from
        screen_y, in the direction implied by `anchor`) — this marks the
        current leading edge of the decay sweep. Everything already swept
        past this point is fully erased (not drawn at all, not left behind
        as a trail of decay tiles); as the sweep advances, this single
        marker just moves further along and the old position is simply
        never drawn again next frame.

        anchor='midtop' for 'down' (sweeping downward from the player);
        anchor='midbottom' for 'up' (sweeping upward from the player).

        The tile is a fixed size and normally extends a full
        decay_height_scaled beyond `position` in the direction of travel —
        cropped here so that far edge never pokes past self.length (the
        beam's actual, real end) once the sweep gets within one tile's
        height of it.
        """
        if not (self.decay_sprite_scaled and len(self.decay_sprite_scaled) > 0):
            return
        frame_index = self._synced_frame_index(self.decay_sprite_scaled)
        tile = self._scale_cross_axis(self.decay_sprite_scaled[frame_index], 'width')

        # Reserve the tip's own footprint (its full height, same as the
        # middle-tile loop's `self.length - self.end_height_scaled` bound)
        # so the decay sweep can never advance into the space the
        # end/collision sprite occupies. Without this, the marker's only
        # boundary was self.length itself — the tip's OUTER edge — letting
        # the sweep advance across the tip's entire footprint (from
        # length - end_height_scaled to length) during the final stretch.
        # Since the tip is a tapered/pointed shape rather than a solid
        # rectangle filling its bounding box, that wider decay tile sitting
        # directly behind it poked out past the tip's visible silhouette —
        # the "sticking out at the tip" artifact — no matter which sprite
        # was painted on top.
        # Whichever sprite the tip is actually showing right now (end or
        # collision — see _tip_frames) may have a different height, so
        # reserve whichever one is currently in play, not just end_sprite's.
        tip_reserved = self._tip_reserved_height()
        remaining = (self.length - tip_reserved) - position
        if remaining <= 0:
            return
        tile_h = tile.get_height()
        if remaining < tile_h:
            # No forced minimum here — int() truncates, so when remaining
            # is a sub-pixel fraction (e.g. 0.4px) this comes out to 0 and
            # the marker is simply skipped for this one frame rather than
            # drawing a forced-minimum 1px sliver that would poke out
            # past self.length by however much that forcing overshot the
            # real (sub-pixel) remainder — that forced minimum was the
            # source of the still-visible tiny overshoot.
            remaining_px = int(remaining)
            if remaining_px <= 0:
                return
            if anchor == 'midtop':
                # Travel continues downward (away from the anchor) — the
                # FAR (bottom) rows are what would overshoot; keep the
                # near (top) rows, which stay anchored right at position.
                crop_rect = pygame.Rect(0, 0, tile.get_width(), remaining_px)
            else:  # midbottom
                # Travel continues upward — the FAR (top) rows overshoot;
                # keep the near (bottom) rows, anchored at position.
                crop_rect = pygame.Rect(0, tile_h - remaining_px, tile.get_width(), remaining_px)
            tile = tile.subsurface(crop_rect)

        sign = 1 if anchor == 'midtop' else -1
        tile_pos = screen_y + sign * position
        rect = tile.get_rect(**{anchor: (screen_x, tile_pos)})
        screen.blit(tile, rect)

    def _draw_decay_marker_horizontal(self, screen, screen_x, screen_y, position, anchor):
        """Horizontal counterpart to _draw_decay_marker_vertical. anchor is
        'midleft' for beams growing rightward (direction='right') or
        'midright' for beams growing leftward (direction='left'). Same
        overshoot cropping as the vertical version — see there for why."""
        if not (self.decay_sprite_scaled and len(self.decay_sprite_scaled) > 0):
            return
        frame_index = self._synced_frame_index(self.decay_sprite_scaled)
        tile = self._scale_cross_axis(self.decay_sprite_scaled[frame_index], 'height')

        # Reserve the tip's own footprint — see the matching comment in
        # _draw_decay_marker_vertical for why this can't just be
        # self.length.
        tip_reserved = self._tip_reserved_width()
        remaining = (self.length - tip_reserved) - position
        if remaining <= 0:
            return
        tile_w = tile.get_width()
        if remaining < tile_w:
            # See _draw_decay_marker_vertical — no forced minimum, skip
            # drawing entirely when remaining truncates to 0 rather than
            # forcing a 1px sliver that could overshoot self.length.
            remaining_px = int(remaining)
            if remaining_px <= 0:
                return
            if anchor == 'midleft':
                # Travel continues rightward — the FAR (right) columns
                # overshoot; keep the near (left) columns, at position.
                crop_rect = pygame.Rect(0, 0, remaining_px, tile.get_height())
            else:  # midright
                # Travel continues leftward — the FAR (left) columns
                # overshoot; keep the near (right) columns, at position.
                crop_rect = pygame.Rect(tile_w - remaining_px, 0, remaining_px, tile.get_height())
            tile = tile.subsurface(crop_rect)

        sign = 1 if anchor == 'midleft' else -1
        tile_pos = screen_x + sign * position
        rect = tile.get_rect(**{anchor: (tile_pos, screen_y)})
        screen.blit(tile, rect)

    def _draw_with_sprites(self, screen, screen_x, screen_y):
        """Draw beam using three-part sprite system"""
        if self.direction == 'down':
            self._draw_vertical_down(screen, screen_x, screen_y)
        elif self.direction == 'left':
            self._draw_horizontal_left(screen, screen_x, screen_y)
        elif self.direction == 'right':
            self._draw_horizontal_right(screen, screen_x, screen_y)
        elif self.direction == 'up':
            self._draw_vertical_up(screen, screen_x, screen_y)

    def _beam_start_offset(self):
        """Scalar distance (world px, pre-scale — multiplied by self.scale
        like every other dimension here) that the beam's own drawn body —
        begin_sprite, the middle tiles that follow it, and the decay
        marker that stands in for begin while decaying — sits beyond the
        player anchor: self.ball_gap + self.circle_gap + self.beam_gap
        chained together (see __init__). This is a magnitude, meant to be
        added into current_length/decay position, matching how
        current_length is already used in every _draw_* method below —
        NOT added directly to screen_x/screen_y (that's what begin_dx/
        begin_dy from _draw_ball_circle_overlay are for).

        The tip (end/collision) is deliberately NOT shifted by this: it
        stays positioned from self.length measured against the raw,
        unshifted player anchor (see draw()'s comment on why the origin
        can't move along the travel axis without breaking the collision
        code in enemy.py, which measures blocking_distance from that same
        raw self.x/self.y). So beam_gap doesn't change how far the beam
        reaches overall — only how much of that reach is spent on
        begin+middle before the tip, versus empty space between the
        circle and the beam's body. 0 (the default for all three gaps)
        reproduces the old behavior of the beam's body starting right at
        the anchor.
        """
        return (self.ball_gap + self.circle_gap + self.beam_gap) * self.scale

    def _draw_ball_circle_overlay(self, screen, screen_x, screen_y, anchor):
        """Draw the optional ball/circle overlays (see load_sprites),
        chained outward from the player anchor (screen_x, screen_y): the
        ball sits self.ball_gap from the player, the circle sits
        self.circle_gap beyond the ball, and the beam itself
        (begin/middle/end, drawn by the caller using the returned offset)
        sits self.beam_gap beyond the circle. 0 for all three (the
        default) reproduces the old behavior of everything sharing one
        exact point. Circle is drawn first, then ball on top of it, then
        the beam — via the returned offset — on top of both. No-ops
        per-sprite if that overlay's asset wasn't found (see
        load_sprites), so this is always safe to call.

        Returns (begin_dx, begin_dy): the offset the caller must add to
        (screen_x, screen_y) when placing begin_sprite's own anchor, so
        the beam starts self.beam_gap beyond the circle.

        Ball/circle themselves are only actually blit while self.decaying
        is False — self.active alone doesn't tell us that: it stays True
        for the entire decay sweep (only flipping False once the sweep
        reaches the tip — see update()), so gating on active let the ball
        and circle keep rendering through the whole release animation
        instead of vanishing the instant the attack button is let go. The
        offset is still computed and returned either way, since begin's
        (or the decay marker's) own position depends on it regardless of
        whether ball/circle are currently drawn.
        """
        # anchor tells us which edge is pinned to (screen_x, screen_y) —
        # from that we can recover which axis is the travel axis and
        # which direction along it counts as "further from the player"
        # (positive gap), without needing self.direction duplicated here.
        axis_sign = {
            'midtop': (0, 1),      # down: travel is +y
            'midbottom': (0, -1),  # up: travel is -y
            'midleft': (1, 0),     # right: travel is +x
            'midright': (-1, 0),   # left: travel is -x
        }.get(anchor, (0, 0))
        dx_per_px, dy_per_px = axis_sign

        ball_offset = self.ball_gap * self.scale
        circle_offset = (self.ball_gap + self.circle_gap) * self.scale
        beam_offset = (self.ball_gap + self.circle_gap + self.beam_gap) * self.scale

        if not self.decaying:
            if self.circle_sprite_scaled and len(self.circle_sprite_scaled) > 0:
                frame_index = self.current_frame % len(self.circle_sprite_scaled)
                circle_frame = self.circle_sprite_scaled[frame_index]
                circle_pos = (screen_x + dx_per_px * circle_offset, screen_y + dy_per_px * circle_offset)
                circle_rect = circle_frame.get_rect(**{anchor: circle_pos})
                screen.blit(circle_frame, circle_rect)
            if self.ball_sprite_scaled and len(self.ball_sprite_scaled) > 0:
                frame_index = self.current_frame % len(self.ball_sprite_scaled)
                ball_frame = self.ball_sprite_scaled[frame_index]
                ball_pos = (screen_x + dx_per_px * ball_offset, screen_y + dy_per_px * ball_offset)
                ball_rect = ball_frame.get_rect(**{anchor: ball_pos})
                screen.blit(ball_frame, ball_rect)

        return (dx_per_px * beam_offset, dy_per_px * beam_offset)

    def _begin_reserved(self):
        """How much along-travel space (screen/scaled px) begin's own
        drawn footprint occupies from beam_start, on whichever axis is
        this beam's travel axis. 0 if there's no begin sprite. Shared by
        _min_reach() and the draw methods so this is computed in exactly
        one place."""
        if not (self.begin_sprite_scaled and len(self.begin_sprite_scaled) > 0):
            return 0
        if self.direction in ('up', 'down'):
            return int(self.begin_height_scaled * self.begin_overlap_ratio)
        return int(self.begin_width_scaled * self.begin_overlap_ratio)

    def _tip_reserved(self):
        """Direction-agnostic wrapper over _tip_reserved_width/height —
        picks whichever axis is this beam's travel axis."""
        if self.direction in ('up', 'down'):
            return self._tip_reserved_height()
        return self._tip_reserved_width()

    def _tip_reserved_baseline(self):
        """Always end-sprite-sized (never collision-sized), unlike
        _tip_reserved() — used only by _min_reach() to anchor the tip's far
        edge at a position that doesn't move once self.blocked latches.

        _min_reach() used to call the blocked-aware _tip_reserved() here,
        which is correct for tip_boundary (see the draw methods, which still
        use the dynamic version so middle tiles stop further back to make
        room for a bigger collision sprite) but wrong for this: the instant
        self.blocked latches and swaps in a collision sprite bigger than end
        along the travel axis, effective_length itself jumped forward by that
        size difference — punching the tip further into whatever it had just
        hit, instead of stopping exactly there.
        """
        if self.direction in ('up', 'down'):
            return self.end_height_scaled
        return self.end_width_scaled

    def _min_reach(self):
        """See _tip_reserved_baseline() for why this uses the fixed baseline
        rather than the blocked-aware _tip_reserved()."""
        return self._beam_start_offset() + self._begin_reserved() + self._tip_reserved_baseline()

    def get_tip_world_length(self):
        """World-space (unscaled) distance from this beam's origin
        (self.x/self.y) to wherever the tip sprite is ACTUALLY drawn
        right now — i.e. (_min_reach() + self.length) / self.scale,
        matching each draw method's `effective_length` exactly.

        Collision code (enemy.py) used to compare against attack.length
        directly, which was correct back when effective_length had no
        floor and the tip really did sit exactly self.length from the
        origin. Once _min_reach() started adding a real spatial offset
        (needed so the tip can't render inside begin — see _min_reach),
        the tip started visually landing further out than self.length
        while collision kept checking self.length — the visible tip
        sprite would pass through an enemy several world px before
        contact/damage actually registered. Anything that needs to know
        "how far out does this beam's tip currently reach" (in world
        space, to compare against enemy positions etc.) should call this
        instead of reading attack.length directly.
        """
        return (self._min_reach() + self.length) / self.scale

    def _draw_vertical_down(self, screen, screen_x, screen_y):
        """Draw vertical beam pointing down - with animation frames"""
        decay_amount = min(self.decay_length, self.length) if self.decaying else 0
        beam_start = self._beam_start_offset()

        # See _min_reach()/get_tip_world_length() above for the full
        # history — this floor guarantees the tip renders past begin's
        # own footprint instead of behind/inside it, and grows
        # continuously (additive, not a clamp) from frame one.
        effective_length = self._min_reach() + self.length

        # Floor, not 0: the beam's body (begin/middle) never starts before
        # beam_start regardless of whether self.length has grown past it
        # yet or begin has actually been drawn this frame. Leaving this at
        # 0 let the middle-tile loop below tile all the way from the
        # player out to tip_boundary while self.length <= beam_start, then
        # jump this value up to beam_start the instant self.length crossed
        # it — which reads as the middle tiling restarting from scratch at
        # the begin sprite's position instead of growing continuously.
        current_length = beam_start

        # Ball/circle sit at a fixed distance from the player (ball_gap/
        # circle_gap) — drawn every frame the attack is active, same as
        # begin/middle/tip below (all measured against effective_length).
        begin_dx, begin_dy = self._draw_ball_circle_overlay(screen, screen_x, screen_y, 'midtop')

        # 1. Consumed region (if decaying) is fully erased — just one decay
        #    marker sprite at the current leading edge; otherwise draw
        #    begin normally. Drawn unconditionally, right when the attack
        #    fires, at its fixed beam_start position — same reasoning as
        #    ball/circle just above: it's a spatial offset, not a delay.
        if decay_amount > 0:
            self._draw_decay_marker_vertical(screen, screen_x, screen_y, beam_start + decay_amount, anchor='midtop')
            current_length = beam_start + decay_amount + self.decay_height_scaled
        elif self.begin_sprite_scaled and len(self.begin_sprite_scaled) > 0:
            frame_index = self._synced_frame_index(self.begin_sprite_scaled)
            begin_frame = self._scale_cross_axis(self.begin_sprite_scaled[frame_index], 'width')

            begin_rect = begin_frame.get_rect(midtop=(screen_x + begin_dx, screen_y + begin_dy))
            screen.blit(begin_frame, begin_rect)
            current_length = beam_start + int(self.begin_height_scaled * self.begin_overlap_ratio)

        # 2. Draw middle sections beyond whatever decay has already covered.
        #    Tiles are drawn up to tip_boundary — the point where the tip
        #    sprite (end or collision, whichever _tip_frames() is about to
        #    draw — see _tip_reserved_height) actually starts. The final
        #    tile is cropped to fit exactly whatever space remains rather
        #    than either overshooting past tip_boundary or stopping a
        #    whole tile-length short of it (which is what the old
        #    tip_overshoot_guard_height hack did — see
        #    BigBangKamehamehaAttack's now-obsolete comment on that
        #    attribute — it prevented overshoot by always leaving a gap
        #    instead; cropping the last tile prevents overshoot without
        #    needing to leave one).
        if self.middle_sprite_scaled and len(self.middle_sprite_scaled) > 0:
            middle_index = 0
            tip_boundary = effective_length - self._tip_reserved_height()
            while current_length < tip_boundary:
                frame_index = (
                    self._synced_middle_frame if self.middle_sync_random
                    else (self.current_frame + middle_index) % len(self.middle_sprite_scaled)
                )
                middle_frame = self._scale_cross_axis(self.middle_sprite_scaled[frame_index], 'width')
                tile_h = middle_frame.get_height()
                remaining = tip_boundary - current_length
                if remaining < tile_h:
                    # Round UP rather than truncate: a fractional-pixel
                    # shortfall here would leave a persistent sub-pixel gap
                    # before the tip, whereas rounding up biases toward a
                    # sub-pixel overlap instead -- invisible, since the tip
                    # sprite is drawn afterward and covers it.
                    crop_h = min(tile_h, math.ceil(remaining))
                    if crop_h <= 0:
                        break
                    # Keep the NEAR (top) rows — anchored at current_length,
                    # with travel continuing downward past this tile — so
                    # it's the FAR (bottom) rows that would cross
                    # tip_boundary; crop those off instead.
                    middle_frame = middle_frame.subsurface(
                        pygame.Rect(0, 0, middle_frame.get_width(), crop_h)
                    )

                middle_y = screen_y + current_length
                middle_rect = middle_frame.get_rect(midtop=(screen_x, middle_y))
                screen.blit(middle_frame, middle_rect)
                current_length += self.middle_height_scaled
                middle_index += 1

        # 3. Draw the tip — collision sprite while pressed against an enemy
        #    or wall (self.blocked), otherwise the normal end sprite;
        #    skipped once the decay front has swept all the way through
        #    (nothing left to have a tip on)
        #
        #    Anchored midBOTTOM (not midtop) with its bottom edge placed
        #    exactly at self.length, so the tip's outer/leading edge is what
        #    reaches self.length — previously it was centered ON self.length
        #    (midtop anchor at length - half the tip's own height), which let
        #    the sprite's outer half overshoot self.length by another half
        #    tip-height. Combined with check_collision_with_attack's 'beam'
        #    branch capping the reported length at an enemy's CENTER (half
        #    its own width/height), that extra overshoot is what made the
        #    beam visually punch through to an enemy's far edge instead of
        #    stopping halfway through it.
        tip_frames = self._tip_frames()
        if tip_frames and len(tip_frames) > 0 and decay_amount < effective_length:
            frame_index = self._synced_frame_index(tip_frames)
            end_frame = self._scale_cross_axis(tip_frames[frame_index], 'width')

            # Position from effective_length (self.length, floored at
            # beam_start — see above), not from current_length, which only
            # advances once a middle tile fits and can actually overshoot
            # near the end of a decay sweep (decay_amount + decay_height_
            # scaled can exceed self.length right before decay finishes,
            # which pushed the tip further out at exactly that moment).
            end_y = screen_y + effective_length
            end_rect = end_frame.get_rect(midbottom=(screen_x, end_y))
            screen.blit(end_frame, end_rect)

    def _draw_vertical_up(self, screen, screen_x, screen_y):
        """Draw vertical beam pointing up - with animation frames"""
        decay_amount = min(self.decay_length, self.length) if self.decaying else 0
        beam_start = self._beam_start_offset()

        # See _min_reach()/get_tip_world_length() on _draw_vertical_down.
        effective_length = self._min_reach() + self.length

        # 1. Consumed region (if decaying) is fully erased — just one decay
        #    marker sprite at the current leading edge; otherwise draw
        #    begin normally. Drawn FIRST (not last) so the tip — drawn in
        #    step 3 below — paints over it, same ordering as
        #    _draw_vertical_down/_draw_horizontal_*. Previously this was
        #    drawn last here, which is what let the decay marker (whose
        #    position is only clamped to self.length, not to
        #    self.length - end_height_scaled) paint on TOP of the
        #    end/collision tip once the sweep got within one tile-height of
        #    the beam's end, visually looking like the decay "grew past"
        #    the end/collision sprite.
        #
        #    Drawn unconditionally, right when the attack fires, at its
        #    fixed beam_start position — see _draw_vertical_down.
        begin_dx, begin_dy = self._draw_ball_circle_overlay(screen, screen_x, screen_y, 'midbottom')
        if decay_amount > 0:
            self._draw_decay_marker_vertical(screen, screen_x, screen_y, beam_start + decay_amount, anchor='midbottom')
        elif self.begin_sprite_scaled and len(self.begin_sprite_scaled) > 0:
            frame_index = self._synced_frame_index(self.begin_sprite_scaled)
            begin_frame = self._scale_cross_axis(self.begin_sprite_scaled[frame_index], 'width')

            begin_rect = begin_frame.get_rect(midbottom=(screen_x + begin_dx, screen_y + begin_dy))
            screen.blit(begin_frame, begin_rect)

        # 2. Draw middle sections, tiled outward FROM THE PLAYER (screen_y,
        #    a fixed point) — the same anchoring _draw_vertical_down uses —
        #    rather than counted inward from the tip. The tip moves every
        #    single frame while the beam is growing, so counting from the
        #    tip slides the entire grid of tile boundaries every frame: a
        #    tile that existed a moment ago doesn't land on the same screen
        #    pixels next frame. Counting from the player instead means each
        #    physical tile's screen position (and its frame index) is fixed
        #    once placed; new tiles just get appended further out as the
        #    beam grows, exactly like down/left/right already do.
        #
        #    Floor is beam_start, not 0 — see _draw_vertical_down for why
        #    (middle must never tile from the player's own position before
        #    beam_start, or it jumps/restarts once self.length crosses it).
        current_length = beam_start
        if decay_amount > 0:
            current_length = beam_start + decay_amount + self.decay_height_scaled
        elif self.begin_sprite_scaled and len(self.begin_sprite_scaled) > 0:
            current_length = beam_start + int(self.begin_height_scaled * self.begin_overlap_ratio)

        if self.middle_sprite_scaled and len(self.middle_sprite_scaled) > 0:
            middle_index = 0
            tip_boundary = effective_length - self._tip_reserved_height()
            while current_length < tip_boundary:
                frame_index = (
                    self._synced_middle_frame if self.middle_sync_random
                    else (self.current_frame + middle_index) % len(self.middle_sprite_scaled)
                )
                middle_frame = self._scale_cross_axis(self.middle_sprite_scaled[frame_index], 'width')
                tile_h = middle_frame.get_height()
                remaining = tip_boundary - current_length
                if remaining < tile_h:
                    # Round UP rather than truncate: a fractional-pixel
                    # shortfall here would leave a persistent sub-pixel gap
                    # before the tip, whereas rounding up biases toward a
                    # sub-pixel overlap instead -- invisible, since the tip
                    # sprite is drawn afterward and covers it.
                    crop_h = min(tile_h, math.ceil(remaining))
                    if crop_h <= 0:
                        break
                    # Keep the NEAR (bottom) rows — anchored at
                    # current_length, with travel continuing upward past
                    # this tile — so it's the FAR (top) rows that would
                    # cross tip_boundary; crop those off instead.
                    middle_frame = middle_frame.subsurface(
                        pygame.Rect(0, tile_h - crop_h, middle_frame.get_width(), crop_h)
                    )

                middle_y = screen_y - current_length
                middle_rect = middle_frame.get_rect(midbottom=(screen_x, middle_y))
                screen.blit(middle_frame, middle_rect)
                current_length += self.middle_height_scaled
                middle_index += 1

        # 3. Draw the tip — collision sprite while pressed against an enemy
        #    or wall (self.blocked), otherwise the normal end sprite;
        #    skipped once decay has consumed the whole beam. Positioned
        #    from self.length directly (the player at screen_y is the
        #    fixed point), same pattern as _draw_vertical_down, so the tip
        #    tracks the beam's actual length every frame.
        #
        #    Anchored midTOP (not midbottom) with its top edge placed
        #    exactly at self.length, so the tip's outer/leading edge is what
        #    reaches self.length — previously it was centered ON self.length,
        #    which let the sprite's outer half overshoot self.length by
        #    another half tip-height. See _draw_vertical_down for the full
        #    explanation of why that overshoot mattered.
        #
        #    Drawn LAST (moved from step 1) so it paints on top of the
        #    decay marker / middle tiles instead of being painted over by
        #    them — see the note on step 1 above.
        tip_frames = self._tip_frames()
        if tip_frames and len(tip_frames) > 0 and decay_amount < effective_length:
            frame_index = self._synced_frame_index(tip_frames)
            end_frame = self._scale_cross_axis(tip_frames[frame_index], 'width')

            end_y = screen_y - effective_length
            end_rect = end_frame.get_rect(midtop=(screen_x, end_y))
            screen.blit(end_frame, end_rect)

    def _draw_horizontal_right(self, screen, screen_x, screen_y):
        """Draw horizontal beam pointing right - with animation frames"""
        decay_amount = min(self.decay_length, self.length) if self.decaying else 0
        beam_start = self._beam_start_offset()
        # See _min_reach()/get_tip_world_length() on _draw_vertical_down.
        effective_length = self._min_reach() + self.length
        # Floor is beam_start, not 0 — see _draw_vertical_down for why
        # (middle must never tile from the player's own position before
        # beam_start, or it jumps/restarts once self.length crosses it).
        current_length = beam_start

        # Ball/circle overlay: drawn every frame the attack is active.
        begin_dx, begin_dy = self._draw_ball_circle_overlay(screen, screen_x, screen_y, 'midleft')

        # 1. Consumed region (if decaying) is fully erased — just one decay
        #    marker sprite at the current leading edge; otherwise draw
        #    begin normally. Drawn unconditionally, right when the attack
        #    fires — see _draw_vertical_down.
        if decay_amount > 0:
            self._draw_decay_marker_horizontal(screen, screen_x, screen_y, beam_start + decay_amount, anchor='midleft')
            current_length = beam_start + decay_amount + self.decay_width_scaled
        elif self.begin_sprite_scaled and len(self.begin_sprite_scaled) > 0:
            frame_index = self._synced_frame_index(self.begin_sprite_scaled)
            begin_frame = self._scale_cross_axis(self.begin_sprite_scaled[frame_index], 'height')

            begin_rect = begin_frame.get_rect(midleft=(screen_x + begin_dx, screen_y + begin_dy))
            screen.blit(begin_frame, begin_rect)
            current_length = beam_start + int(self.begin_width_scaled * self.begin_overlap_ratio)

        # 2. Draw middle sections
        if self.middle_sprite_scaled and len(self.middle_sprite_scaled) > 0:
            middle_index = 0
            tip_boundary = effective_length - self._tip_reserved_width()
            while current_length < tip_boundary:
                frame_index = (
                    self._synced_middle_frame if self.middle_sync_random
                    else (self.current_frame + middle_index) % len(self.middle_sprite_scaled)
                )
                middle_frame = self._scale_cross_axis(self.middle_sprite_scaled[frame_index], 'height')
                tile_w = middle_frame.get_width()
                remaining = tip_boundary - current_length
                if remaining < tile_w:
                    # Round UP rather than truncate -- see the matching
                    # comment on crop_h in the vertical draw methods.
                    crop_w = min(tile_w, math.ceil(remaining))
                    if crop_w <= 0:
                        break
                    # Keep the NEAR (left) columns — anchored at
                    # current_length, with travel continuing rightward
                    # past this tile — so it's the FAR (right) columns
                    # that would cross tip_boundary; crop those off
                    # instead.
                    middle_frame = middle_frame.subsurface(
                        pygame.Rect(0, 0, crop_w, middle_frame.get_height())
                    )

                middle_x = screen_x + current_length
                middle_rect = middle_frame.get_rect(midleft=(middle_x, screen_y))
                screen.blit(middle_frame, middle_rect)
                current_length += self.middle_width_scaled
                middle_index += 1

        # 3. Draw the tip — collision sprite while pressed against an enemy
        #    or wall (self.blocked), otherwise the normal end sprite;
        #    skipped once decay reaches it
        #
        #    Anchored midRIGHT (not midleft) with its right edge placed
        #    exactly at self.length, so the tip's outer/leading edge is what
        #    reaches self.length instead of the sprite's center overshooting
        #    past it — see _draw_vertical_down for the full explanation.
        tip_frames = self._tip_frames()
        if tip_frames and len(tip_frames) > 0 and decay_amount < effective_length:
            frame_index = self._synced_frame_index(tip_frames)
            end_frame = self._scale_cross_axis(tip_frames[frame_index], 'height')

            # Position from effective_length — see _draw_vertical_down.
            end_x = screen_x + effective_length
            end_rect = end_frame.get_rect(midright=(end_x, screen_y))
            screen.blit(end_frame, end_rect)

    def _draw_horizontal_left(self, screen, screen_x, screen_y):
        """Draw horizontal beam pointing left - with animation frames"""
        decay_amount = min(self.decay_length, self.length) if self.decaying else 0
        beam_start = self._beam_start_offset()
        # See _min_reach()/get_tip_world_length() on _draw_vertical_down.
        effective_length = self._min_reach() + self.length
        # Floor is beam_start, not 0 — see _draw_vertical_down for why
        # (middle must never tile from the player's own position before
        # beam_start, or it jumps/restarts once self.length crosses it).
        current_length = beam_start

        # Ball/circle overlay: drawn every frame the attack is active.
        begin_dx, begin_dy = self._draw_ball_circle_overlay(screen, screen_x, screen_y, 'midright')

        # 1. Consumed region (if decaying) is fully erased — just one decay
        #    marker sprite at the current leading edge; otherwise draw
        #    begin normally. Drawn unconditionally, right when the attack
        #    fires — see _draw_vertical_down.
        if decay_amount > 0:
            self._draw_decay_marker_horizontal(screen, screen_x, screen_y, beam_start + decay_amount, anchor='midright')
            current_length = beam_start + decay_amount + self.decay_width_scaled
        elif self.begin_sprite_scaled and len(self.begin_sprite_scaled) > 0:
            frame_index = self._synced_frame_index(self.begin_sprite_scaled)
            begin_frame = self._scale_cross_axis(self.begin_sprite_scaled[frame_index], 'height')

            begin_rect = begin_frame.get_rect(midright=(screen_x + begin_dx, screen_y + begin_dy))
            screen.blit(begin_frame, begin_rect)
            current_length = beam_start + int(self.begin_width_scaled * self.begin_overlap_ratio)

        # 2. Draw middle sections
        if self.middle_sprite_scaled and len(self.middle_sprite_scaled) > 0:
            middle_index = 0
            tip_boundary = effective_length - self._tip_reserved_width()
            while current_length < tip_boundary:
                frame_index = (
                    self._synced_middle_frame if self.middle_sync_random
                    else (self.current_frame + middle_index) % len(self.middle_sprite_scaled)
                )
                middle_frame = self._scale_cross_axis(self.middle_sprite_scaled[frame_index], 'height')
                tile_w = middle_frame.get_width()
                remaining = tip_boundary - current_length
                if remaining < tile_w:
                    # Round UP rather than truncate -- see the matching
                    # comment on crop_h in the vertical draw methods.
                    crop_w = min(tile_w, math.ceil(remaining))
                    if crop_w <= 0:
                        break
                    # Keep the NEAR (right) columns — anchored at
                    # current_length, with travel continuing leftward past
                    # this tile — so it's the FAR (left) columns that
                    # would cross tip_boundary; crop those off instead.
                    middle_frame = middle_frame.subsurface(
                        pygame.Rect(tile_w - crop_w, 0, crop_w, middle_frame.get_height())
                    )

                middle_x = screen_x - current_length
                middle_rect = middle_frame.get_rect(midright=(middle_x, screen_y))
                screen.blit(middle_frame, middle_rect)
                current_length += self.middle_width_scaled
                middle_index += 1

        # 3. Draw the tip — collision sprite while pressed against an enemy
        #    or wall (self.blocked), otherwise the normal end sprite;
        #    skipped once decay reaches it
        #
        #    Anchored midLEFT (not midright) with its left edge placed
        #    exactly at self.length, so the tip's outer/leading edge is what
        #    reaches self.length instead of the sprite's center overshooting
        #    past it — see _draw_vertical_down for the full explanation.
        tip_frames = self._tip_frames()
        if tip_frames and len(tip_frames) > 0 and decay_amount < effective_length:
            frame_index = self._synced_frame_index(tip_frames)
            end_frame = self._scale_cross_axis(tip_frames[frame_index], 'height')

            # Position from effective_length — see _draw_vertical_down.
            end_x = screen_x - effective_length
            end_rect = end_frame.get_rect(midleft=(end_x, screen_y))
            screen.blit(end_frame, end_rect)

    def _draw_fallback(self, screen, screen_x, screen_y, colors):
        """Fallback drawing using rectangles"""
        beam_width = self.width

        # Shrink from the player's end as decay progresses, same idea as
        # the sprite path: the segment nearest the player disappears first.
        decay_amount = min(self.decay_length, self.length) if self.decaying else 0
        visible_length = self.length - decay_amount
        if visible_length <= 0:
            return

        if self.direction == 'up':
            pygame.draw.rect(screen, colors['CYAN'],
                             (screen_x - beam_width // 2, screen_y - self.length, beam_width, visible_length))
            pygame.draw.rect(screen, colors['YELLOW'],
                             (screen_x - beam_width // 2 - 5, screen_y - self.length, beam_width + 10, visible_length), 3)
        elif self.direction == 'down':
            pygame.draw.rect(screen, colors['CYAN'],
                             (screen_x - beam_width // 2, screen_y + decay_amount, beam_width, visible_length))
            pygame.draw.rect(screen, colors['YELLOW'],
                             (screen_x - beam_width // 2 - 5, screen_y + decay_amount, beam_width + 10, visible_length), 3)
        elif self.direction == 'left':
            pygame.draw.rect(screen, colors['CYAN'],
                             (screen_x - self.length, screen_y - beam_width // 2, visible_length, beam_width))
            pygame.draw.rect(screen, colors['YELLOW'],
                             (screen_x - self.length, screen_y - beam_width // 2 - 5, visible_length, beam_width + 10), 3)
        elif self.direction == 'right':
            pygame.draw.rect(screen, colors['CYAN'],
                             (screen_x + decay_amount, screen_y - beam_width // 2, visible_length, beam_width))
            pygame.draw.rect(screen, colors['YELLOW'],
                             (screen_x + decay_amount, screen_y - beam_width // 2 - 5, visible_length, beam_width + 10), 3)

class KamehamehaChargeEffect:
    """The charge-up glow shown while the player holds Q before the beam fires.

    This is separate from BeamAttack because it needs different rules:
    - It's centered ON the player's body (not offset in front, like the beam
      itself is), for down/left/right.
    - Draw order is direction-dependent: in FRONT of the player for
      down/left/right, but BEHIND the player for up (since facing away from
      the camera, the charge reads as being held on the far side of the
      body). We reuse get_beam_layer() for this since it already encodes
      the same front/behind-by-direction rule that the beam itself uses.
    - Frame playback is a sequential run through every frame in the sheet
      (the "build-up"), followed by pulse_steps more steps alternating
      between the last two frames (the "pulse") — e.g. for a 3-frame
      sheet that's 1, 2, 3, 2, 3, 2, 3, ... in 1-indexed terms. frame_duration
      is derived from target_charge_duration and the frame count (see
      _load_sprite) so this whole sequence always takes the same total
      time regardless of how many frames the sheet actually has — see
      get_total_duration().

    charging_kamehameha.png is a single row of 16x16 frames — there's no
    per-direction art, the same charge-up animation is reused and just
    repositioned/re-layered depending on which way the player is facing.
    16x16 is just the default grid (see frame_width/frame_height below);
    pass different values for a charge sheet using a different tile size.

    rotate_to_direction=True is for attacks like banshee_blast whose
    charge sheet is drawn facing right only, with no repositioning
    nudge either — instead of the direction_offsets dict above, the
    loaded frames are rotated once (see _load_sprite) to match
    self.direction, and direction_offsets defaults to no offset at all
    for every direction rather than the kamehameha-tuned values.
    """

    def __init__(self, player, scale=_RENDER_SCALE, attack_name='kamehameha',
                 target_charge_duration=1, direction_offsets=None, pulse_steps=4,
                 rotate_to_direction=False, frame_width=16, frame_height=16,
                 hold_after_pulse=False):
        self.player = player
        # Direction only affects position/draw-order here, not which
        # frames get used (the sheet has no per-direction rows).
        self.direction = player.direction
        self.scale = scale
        self.active = True
        self.y_sort = False

        # Which sprite folder/filename to load the charge sheet from (see
        # _load_sprite) — lets subclasses like FinalFlashChargeEffect reuse
        # this whole class while pointing at their own art. Expects
        # assets/sprites/attacks/{attack_name}/charging_{attack_name}.png,
        # a single row of 16x16 frames just like the kamehameha one.
        self.attack_name = attack_name

        # Native (unscaled) frame size of the charging_{attack_name}.png
        # sheet. Defaults to 16x16 (the kamehameha charge sheet's grid),
        # but is a constructor param so attacks with a differently-sized
        # charge sheet (e.g. banshee_blast) can override it — same idea
        # as BeamAttack's begin_/end_/collision_frame_width/height.
        self.frame_width = frame_width
        self.frame_height = frame_height

        # Total time (seconds) the whole charge-up animation gets to play
        # before the beam auto-fires — the "quick, snappy" 0.3s windup.
        # frame_duration is derived from this (see _load_sprite) rather
        # than being a fixed number, so however many frames the sheet has,
        # the full build-up-then-pulse sequence always finishes exactly
        # when this many seconds are up.
        self.target_charge_duration = target_charge_duration
        # Extra alternating steps appended after the sequential run-up
        # through every frame — this is what gives the "2, 3, 2, 3" pulsing
        # feel on top of the initial "1, 2, 3" build. Must be even so the
        # pulse ends back on the same frame it started on. 0 skips the
        # pulse tail entirely (see _current_frame_index) — a straight
        # run-up that holds on the last frame, e.g. banshee_blast.
        self.pulse_steps = pulse_steps

        # When False (the default/plain-kamehameha behavior), the pulse
        # keeps alternating between the last two frames forever for as
        # long as charging continues — pulse_steps only shapes how long
        # one alternation "counts" as for frame_duration/get_total_duration
        # purposes, not how many times it actually alternates.
        #
        # When True, the pulse plays out for exactly pulse_steps steps and
        # then FREEZES on whichever frame that lands on, instead of
        # continuing to alternate — e.g. pulse_steps=1 on a 2-frame sheet
        # gives 1, 2, 1, then holds on frame 1 forever (a single bounce
        # back), rather than 1, 2, 1, 2, 1, 2, ... forever. See
        # _current_frame_index.
        self.hold_after_pulse = hold_after_pulse

        # When True, the sheet is drawn facing right only (see
        # _load_sprite) and self.frames_scaled is rotated once, right
        # after loading, to match self.direction — rather than the sheet
        # holding separate per-direction art.
        self.rotate_to_direction = rotate_to_direction
        # Same convention as FlameKamehamehaAttack.direction_to_angle:
        # pygame.transform.rotate is counter-clockwise for positive
        # degrees, so a right-facing sheet needs +90 to point up, +180
        # to point left, +270 to point down. Only consulted when
        # rotate_to_direction is True.
        self.direction_to_angle = {'right': 0, 'up': 90, 'left': 180, 'down': 270}

        self.frame_duration = 0.1  # placeholder; recalculated in _load_sprite()

        # Per-direction fine-tuning, in world units (added before scaling,
        # like vertical_offset was). x: positive = right, negative = left.
        # y: positive = down, negative = up. Tweak each direction
        # independently here instead of using one shared offset.
        # rotate_to_direction attacks skip the kamehameha-tuned values
        # below by default — rotation alone handles facing, so there's no
        # reason to assume the same nudges apply — but an explicit
        # direction_offsets can still override this per attack.
        if direction_offsets is not None:
            self.direction_offsets = direction_offsets
        elif rotate_to_direction:
            self.direction_offsets = {}
        else:
            self.direction_offsets = {
                'down':  (-4, 21),
                'left':  (8, 23),
                'right': (-8,23),
                'up':    (3, 21),
            }

        self.frame_timer = 0.0
        self.tick = 0  # advances once per frame_duration

        self.frames_scaled = []
        self.frame_w_scaled = 0
        self.frame_h_scaled = 0

        self._load_sprite()

        # get_beam_layer() returns DrawLayer.PLAYER (0) for left/right —
        # the SAME layer value as the player itself. On a tie, LayerManager's
        # sort is stable, so whichever object was added to the render list
        # first (the player, in game.py) draws first and ends up behind.
        # Using DrawLayer.EFFECTS_FRONT (50) directly guarantees this draws
        # after (in front of) the player regardless of insertion order; only
        # 'up' gets EFFECTS_BEHIND (-1) so it stays behind as intended.
        self.draw_layer = DrawLayer.EFFECTS_BEHIND if self.direction == 'up' else DrawLayer.EFFECTS_FRONT

    def _rotate_frames(self, frames):
        """Rotate a list of right-facing frames to match self.direction —
        same helper as FlameKamehamehaAttack._rotate_frames, for sheets
        that are only ever drawn facing 'right'. Returns frames unrotated
        for 'right' or an unrecognized direction, since rotating by 0
        degrees would just be a wasted copy."""
        angle = self.direction_to_angle.get(self.direction, 0)
        if angle == 0 or not frames:
            return frames
        return [pygame.transform.rotate(frame, angle) for frame in frames]

    def _load_sprite(self):
        try:
            sheet = pygame.image.load(
                f'assets/sprites/attacks/{self.attack_name}/charging_{self.attack_name}.png'
            ).convert_alpha()

            frames_per_row = sheet.get_width() // self.frame_width

            raw_frames = []
            for frame_index in range(frames_per_row):
                x = frame_index * self.frame_width
                raw_frames.append(
                    sheet.subsurface(pygame.Rect(x, 0, self.frame_width, self.frame_height))
                )

            # Reuse BeamAttack's trimming helper so any transparent padding
            # baked into the sheet doesn't throw off the centered anchor,
            # then — for rotate_to_direction attacks whose sheet is drawn
            # facing 'right' only — rotate to match self.direction before
            # scaling (same trim-then-rotate-then-scale order
            # FlameKamehamehaAttack uses for its own right-facing sheets).
            trimmed = BeamAttack._trim_frames(raw_frames)
            if self.rotate_to_direction:
                trimmed = self._rotate_frames(trimmed)

            if trimmed:
                rect = trimmed[0].get_rect()
                self.frame_w_scaled = int(rect.width * self.scale)
                self.frame_h_scaled = int(rect.height * self.scale)
                self.frames_scaled = [
                    pygame.transform.scale(f, (self.frame_w_scaled, self.frame_h_scaled))
                    for f in trimmed
                ]

            print(f"Loaded {len(self.frames_scaled)} charge sprites")

        except Exception as e:
            print(f"Error loading charging beam sprite: {e}")
            self.frames_scaled = []

        # Now that we know how many frames actually loaded, spread
        # target_charge_duration evenly across the full sequence: the
        # sequential run-up (one step per frame) plus pulse_steps more
        # for the alternating tail. This is what keeps the animation
        # snappy (~0.3s total) no matter how many frames the sheet has,
        # instead of frame_duration being a fixed value that total
        # duration has to awkwardly work around.
        count = len(self.frames_scaled)
        total_steps = max(count, 1) + (self.pulse_steps if count > 1 else 0)
        self.frame_duration = self.target_charge_duration / total_steps

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def _current_frame_index(self):
        """Map self.tick to: sequential run-up through every frame, then
        an alternating pulse between the last two frames.

        E.g. for a 3-frame sheet with pulse_steps=4, in 1-indexed terms
        that's 1, 2, 3, 2, 3, 2, 3 — the run-up plays once, then it pulses
        back and forth on the last two frames for the remaining steps.

        pulse_steps=0 (e.g. banshee_blast) skips the pulse tail entirely —
        one straight run-up through every frame, then hold on the last
        one. Without this early-out, falling through to the pulse math
        below with pulse_steps=0 would immediately flicker back to
        count - 2 the instant tick reached count, instead of holding.

        hold_after_pulse=True caps the alternation at exactly pulse_steps
        steps and then freezes there, rather than alternating forever —
        e.g. pulse_steps=1 gives 1, 2, 1, then holds on 1 (a single bounce
        back), instead of 1, 2, 1, 2, 1, 2, ... forever.
        """
        count = len(self.frames_scaled)
        if count == 0:
            return 0
        if count == 1:
            return 0
        if self.pulse_steps == 0:
            return min(self.tick, count - 1)
        if self.tick < count:
            return self.tick
        pulse_tick = self.tick - count
        if self.hold_after_pulse:
            pulse_tick = min(pulse_tick, self.pulse_steps - 1)
        return count - 2 if pulse_tick % 2 == 0 else count - 1

    def get_total_duration(self):
        """Time (seconds) for the full run-up-then-pulse sequence.

        Used by player.py to size beam_charge_required so the beam only
        auto-fires once this animation has actually finished playing.
        By construction (see _load_sprite) this always equals
        target_charge_duration, since frame_duration is derived to fit
        exactly that many steps into that many seconds.
        """
        count = len(self.frames_scaled)
        if count <= 1:
            return self.frame_duration
        total_steps = count + self.pulse_steps
        return total_steps * self.frame_duration

    def update(self, dt):
        if not self.frames_scaled:
            return
        self.frame_timer += dt
        if self.frame_timer >= self.frame_duration:
            self.frame_timer -= self.frame_duration
            self.tick += 1

    def draw(self, screen, camera, colors=None):
        if not self.active or not self.frames_scaled:
            return

        from config.settings import RENDER_SCALE

        # Centered on the player's body: x is already the sprite's
        # horizontal center, and y is the feet/bottom anchor, so subtract
        # half the sprite height to land on vertical center (same
        # convention used elsewhere, e.g. damage-number popup placement).
        # Then nudge by whatever's configured for this direction.
        offset_x, offset_y = self.direction_offsets.get(self.direction, (0, 0))
        screen_x = ((self.player.x + offset_x) * RENDER_SCALE) - camera.x
        screen_y = ((self.player.y - self.player.height / 2 + offset_y) * RENDER_SCALE) - camera.y

        frame = self.frames_scaled[self._current_frame_index()]
        rect = frame.get_rect(center=(screen_x, screen_y))
        screen.blit(frame, rect)