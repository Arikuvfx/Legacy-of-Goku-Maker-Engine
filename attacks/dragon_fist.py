import math
import pygame
from config.settings import RENDER_SCALE as _RENDER_SCALE
from core.draw_layers import DrawLayer, get_dragon_fist_layer


# World-space unit vectors for each fire direction — used to launch the head
# straight out during the 'shooting' phase (see DragonFistAttack.update()).
_DIRECTION_UNIT = {
    'up':    (0, -1),
    'down':  (0, 1),
    'left':  (-1, 0),
    'right': (1, 0),
}


class DragonFistAttack:
    """The head + trailing chain of body segments for the Dragon Fist.

    Lifecycle (see self.state):
      'shooting'   — the head launches out from the player in a straight
                      line along the direction it was thrown, at
                      shoot_speed, until it's shoot_distance out.
      'controlled' — control hands to the player: movement input drives
                      the head directly (see Player._move_dragon_fist_head),
                      clamped every frame to a leash box anchored to the
                      player's current position (see clamp_head_to_leash).
      'retracting' — started by start_retract() (Q released, or Ki ran
                      out). Everything freezes exactly where it is —
                      no more anchor tracking, chain spacing, or leash
                      clamping — while a two-part closing sequence plays
                      on top of it (see self.end_phase, _update_end_sequence()):
                        1. 'head_end'    — dragon_fist_head_end plays once,
                                           on the head only.
                        2. 'destruction' — once that's done, the WHOLE
                                           assembly (head + every body
                                           segment, including the one
                                           anchored right in front of the
                                           player) switches to
                                           brown_destruction and plays
                                           that once too.
                      self.active only goes False once both have played
                      through completely.

    body_positions[-1] is NOT part of the chain — it's a fixed anchor held
    at anchor_offset in front of the player's current position (see
    update()), so it always sits right in front of the player regardless
    of what the head is doing. The remaining num_segments-1 pieces are the
    actual chain: they're kept evenly spaced along the straight line from
    the head to that anchor every frame, proportional to how far apart
    those two currently are — see _compute_chain_targets() / _slide_chain().
    """

    def __init__(self, x, y, direction, scale=_RENDER_SCALE,
                 num_segments=5, link_distance=25,
                 shoot_speed=300, shoot_distance=60,
                 forward_range=130, lateral_range=50,
                 head_size=(64, 64), body_size=(29, 32),
                 retract_speed=0, anchor_offset=20,
                 head_end_frame_count=2, head_end_frame_duration=0.06,
                 destruction_frame_count=4, destruction_frame_duration=0.06,
                 attack_name='dragon_fist', destruction_asset='brown_destruction',
                 push_force=0.4, chain_update_fps=24,
                 chain_head_smooth_time=0.05, chain_tail_smooth_time=0.22,
                 chain_gap_safety_margin=2.0):
        self.direction = direction
        self.scale = scale

        # assets/sprites/attacks/{attack_name}/dragon_fist_head.png etc. —
        # destruction_asset is separate since brown_destruction.png lives
        # under assets/objects/, not under this attack's own sprite folder
        # (it's a shared "puff of destruction" effect, not attack-specific
        # art) — see _load_sprites().
        self.attack_name = attack_name
        self.destruction_asset = destruction_asset

        # All of the following are tunable placeholders — nothing in the
        # spec pinned down exact reach/speed numbers, so these are picked
        # to feel reasonable at a glance and are easy to retune later.
        # num_segments=5 is the one number that WAS specified (5 body
        # sprites chained behind the head).
        self.num_segments = num_segments
        self.link_distance = link_distance
        self.shoot_speed = shoot_speed
        self.shoot_distance = max(shoot_distance, 1)
        # The controlled-phase leash always reaches at least as far as the
        # initial shoot did, or the head would immediately get yanked
        # backward into the box the instant control handed over.
        self.forward_range = max(forward_range, self.shoot_distance)
        self.lateral_range = lateral_range
        self.head_size = head_size
        self.body_size = body_size
        # brown_destruction is its own fixed-size effect, not scaled to
        # whatever piece it's playing on top of (head vs. body segments
        # have different sizes) — see draw()'s 'destruction' branch.
        self.destruction_size = (32, 32)
        # No longer used now that retracting doesn't sweep the head back —
        # kept as an accepted (ignored) param so existing call sites that
        # still pass retract_speed don't break.
        self.retract_speed = retract_speed
        # How far in front of the player body_positions[-1] sits, along
        # the attack's (fixed) throw direction.
        self.anchor_offset = anchor_offset

        # Per-frame push distance for enemy.py's 'dragon_fist' collision
        # branch (read via getattr(attack, 'push_force', None) — the same
        # override hook FinalFlashAttack etc. already use). Deliberately
        # smaller than Enemy.beam_push_force (3, flame_kamehameha's
        # value): flame_kamehameha only has one narrow tip segment, so an
        # enemy typically brushes it for a frame or two at a time. Dragon
        # Fist has six simultaneous hitboxes (head + 4 chain segments +
        # anchor) spanning up to ~190 world units — an enemy can stay
        # inside SOME piece of that chain continuously for far longer,
        # so the same per-frame push accumulates over many more
        # consecutive frames and reads as a fast snap-to-the-wall instead
        # of flame_kamehameha's slow grind. Cutting the per-frame amount
        # keeps the two feeling the same despite the longer contact
        # window. Tune this directly if it still feels off either way.
        self.push_force = push_force

        # In the original game the head moves at full framerate but the
        # body segments visibly slide/ease toward their spot in the chain
        # rather than snapping straight onto it — when the head speeds up
        # or changes direction, the tail lags, then accelerates to catch
        # up, with no oscillation. That's a critically-damped spring per
        # segment (see _smooth_damp / _slide_chain): _compute_chain_targets
        # still recomputes the ideal evenly-spaced waypoints (throttled to
        # chain_update_fps, same chunky-update feel as the reference
        # footage), but body_positions eases toward those waypoints every
        # real frame instead of being set to them directly.
        self.chain_update_fps = chain_update_fps
        self.chain_update_interval = 1.0 / self.chain_update_fps
        self.chain_update_timer = 0.0
        # How quickly each segment closes the gap to its target — smaller
        # is snappier, larger is more sluggish/rubbery. Roughly "seconds
        # to mostly catch up" (see _smooth_damp).
        #
        # NOT a single shared value: body_positions[0] (right behind the
        # head) uses chain_head_smooth_time and the last free segment
        # (right before the anchor) uses chain_tail_smooth_time, with
        # everything between linearly graded — same idea as a real whip
        # or chain, where tension is tightest right at the end being
        # pulled and slack builds up toward the far end. This matters
        # for more than looks: giving every segment the SAME smooth_time
        # was what let body[0] and body[1] visibly separate — clamping
        # body[0] tight to the (unsprung, instantly-moving) head made it
        # noticeably outrun body[1], which was still catching up on its
        # own identical, slower schedule. Neighboring segments now have
        # similar catch-up rates by construction, so their relative gap
        # stays small on its own; _clamp_chain_gaps() is only a rare
        # safety net on top of that, not the thing doing the work.
        self.chain_head_smooth_time = chain_head_smooth_time
        self.chain_tail_smooth_time = chain_tail_smooth_time
        num_free = num_segments - 1
        if num_free > 1:
            step = (self.chain_tail_smooth_time - self.chain_head_smooth_time) / (num_free - 1)
            self.chain_smooth_times = [self.chain_head_smooth_time + step * i
                                        for i in range(num_free)]
        else:
            self.chain_smooth_times = [self.chain_head_smooth_time] * num_free
        # One velocity per free segment (body_positions[:-1] — the anchor
        # is pinned directly, not eased).
        self.chain_velocities = [[0.0, 0.0] for _ in range(num_segments - 1)]
        # Waypoints _slide_chain eases body_positions[:-1] toward; refreshed
        # by _compute_chain_targets() at chain_update_fps. Filled in below
        # once body_positions itself exists.
        self.chain_targets = []
        # _clamp_chain_gaps() safety-net multiplier on link_distance — kept
        # generous (well above the per-segment gradient's normal working
        # range) so it only intervenes on genuine large lag spikes (e.g. a
        # frame hitch), not as routine per-frame enforcement, which would
        # override the throttled/sliding look entirely (see
        # _clamp_chain_gaps' docstring).
        self.chain_gap_safety_margin = chain_gap_safety_margin

        # Closing-sequence timing — also placeholders (frame count/pace
        # picked to look reasonable sight-unseen; retune once the real
        # dragon_fist_head_end / brown_destruction art is in and its
        # actual frame count/desired pace is known). See _pick_head_end_frames
        # / _pick_destruction_frames for the sheet layout these assume.
        self.head_end_frame_count = head_end_frame_count
        self.head_end_frame_duration = head_end_frame_duration
        self.destruction_frame_count = destruction_frame_count
        self.destruction_frame_duration = destruction_frame_duration
        # None while shooting/controlled; 'head_end' then 'destruction'
        # once start_retract() is called — see _update_end_sequence().
        self.end_phase = None
        self.end_elapsed = 0.0

        # The shoot launches from the anchor position (body_positions[-1],
        # right in front of the player) rather than from the player's raw
        # (x, y) — otherwise the head visibly shoots out from beside/behind
        # the anchor piece instead of from it.
        dxu, dyu = _DIRECTION_UNIT.get(direction, (0, 0))
        anchor_x = x + dxu * anchor_offset
        anchor_y = y + dyu * anchor_offset
        self.origin_x = anchor_x
        self.origin_y = anchor_y

        self.head_x = anchor_x
        self.head_y = anchor_y
        # Body segments start bunched at the anchor position too.
        # body_positions[-1] is overwritten every frame in update() to
        # track the player (see _update_anchor()); the rest stretch out
        # behind the head as it travels, via the same _compute_chain_targets() / _slide_chain()
        # logic used in every phase.
        self.body_positions = [[anchor_x, anchor_y] for _ in range(num_segments)]
        self.chain_targets = [[anchor_x, anchor_y] for _ in range(num_segments - 1)]

        self.state = 'shooting'
        self.active = True

        # Layer depends on throw direction, not y-sorted — see
        # get_dragon_fist_layer(): down draws in front of the player
        # (like melee), up/left/right draw behind (like the beam's own
        # 'up' case, but simpler since Dragon Fist doesn't need to also
        # land in front of a distant enemy the way a projectile does).
        self.draw_layer = get_dragon_fist_layer(direction)
        self.y_sort = False

        (self.head_sprite, self.body_sprite,
         self.head_end_frames, self.destruction_frames) = self._load_sprites()

    def get_sort_key(self):
        """Sort key for the layer manager (fixed layer, no y-sorting)."""
        return (self.draw_layer, 0)

    def get_world_bounds(self):
        """World-space pygame.Rect enclosing the head and every body/anchor
        segment at their current positions — used by
        LayerManager._apply_decoration_occlusion (draw_layers.py) so a
        decoration in front of the chain can still redraw on top of it.
        Only matters for the 'down' throw direction in practice — that's
        the only one using DrawLayer.EFFECTS_FRONT (see
        get_dragon_fist_layer's docstring); up/left/right already draw
        behind everything via EFFECTS_BEHIND, so occlusion by a decoration
        there is a non-issue. Still computed for every direction, since
        it's cheap and correct either way.
        """
        half_head_w, half_head_h = self.head_size[0] / 2, self.head_size[1] / 2
        bounds = pygame.Rect(self.head_x - half_head_w, self.head_y - half_head_h,
                              self.head_size[0], self.head_size[1])
        half_body_w, half_body_h = self.body_size[0] / 2, self.body_size[1] / 2
        for bx, by in self.body_positions:
            bounds = bounds.union(
                pygame.Rect(bx - half_body_w, by - half_body_h, self.body_size[0], self.body_size[1]))
        return bounds

    # ------------------------------------------------------------------
    # Sprite loading
    # ------------------------------------------------------------------
    def _load_sprites(self):
        """Load the head/body sprite sheets and pick out the single frame
        that matches self.direction, once, at throw time — plus the two
        closing-sequence sheets, each sliced into a full list of frames
        instead of just one (see _pick_head_end_frames /
        _pick_destruction_frames).

        Facing is permanent for the life of the attack: whichever direction
        the player launched it in is the direction the sprites face, even
        as the head gets steered all over the leash box afterward. So
        there's no per-frame rotation in draw() at all — just a fixed
        frame chosen here and blitted as-is every frame. Same goes for
        dragon_fist_head_end (still direction-specific). brown_destruction
        plays across the whole assembly at once and isn't tied to a
        throw direction at all, so it's a single, direction-agnostic
        filmstrip.

        Sheet layout (rows stacked top-to-bottom):
          body:            row 0 = right (base), row 1 = down (base)
                           'left' = row 0 flipped horizontally
                           'up'   = row 1 flipped vertically
          head:            row 0 = down, row 1 = right (base), row 2 = up
                           'left' = row 1 flipped horizontally
          dragon_fist_head_end: same 3-row direction layout as head, but
                           each row is ALSO cut into head_end_frame_count
                           equal-width columns (a filmstrip per row) —
                           assumed rather than confirmed, since the actual
                           art isn't in yet; adjust _pick_head_end_frames
                           if the real sheet is laid out differently.
          brown_destruction: no direction rows at all — a single row cut
                           into destruction_frame_count equal-width
                           columns. Also assumed, same caveat.

        Falls back to a plain colored circle for whichever piece is
        missing (same graceful-degradation convention as
        DestructibleStone._create_placeholder), so this is always safe to
        use even before real art exists. For the two filmstrips, that
        fallback is just None — draw()'s end-sequence branches already
        fall through to _draw_piece's own placeholder handling when
        handed None, same as head_sprite/body_sprite being None.
        """
        head_sheet = None
        body_sheet = None
        head_end_sheet = None
        destruction_sheet = None
        try:
            head_sheet = pygame.image.load(
                f'assets/sprites/attacks/{self.attack_name}/dragon_fist_head.png'
            ).convert_alpha()
        except Exception as e:
            print(f"No {self.attack_name} head sprite loaded, using fallback: {e}")
        try:
            body_sheet = pygame.image.load(
                f'assets/sprites/attacks/{self.attack_name}/dragon_fist_body.png'
            ).convert_alpha()
        except Exception as e:
            print(f"No {self.attack_name} body sprite loaded, using fallback: {e}")
        try:
            head_end_sheet = pygame.image.load(
                f'assets/sprites/attacks/{self.attack_name}/dragon_fist_head_end.png'
            ).convert_alpha()
        except Exception as e:
            print(f"No {self.attack_name}_head_end sprite loaded, using fallback: {e}")
        try:
            destruction_sheet = pygame.image.load(
                f'assets/objects/{self.destruction_asset}.png'
            ).convert_alpha()
        except Exception as e:
            print(f"No {self.destruction_asset} sprite loaded, using fallback: {e}")

        head = self._pick_head_frame(head_sheet) if head_sheet else None
        body = self._pick_body_frame(body_sheet) if body_sheet else None
        head_end_frames = self._pick_head_end_frames(head_end_sheet) if head_end_sheet else None
        destruction_frames = (self._pick_destruction_frames(destruction_sheet)
                               if destruction_sheet else None)
        return head, body, head_end_frames, destruction_frames

    def _pick_head_frame(self, sheet):
        """Slice the head sheet's 3 stacked rows (down / right-base / up)
        and return the one frame matching self.direction, flipping the
        shared right-facing row for 'left'."""
        w, h = sheet.get_width(), sheet.get_height()
        row_h = h // 3
        if self.direction == 'down':
            row = 0
        elif self.direction == 'up':
            row = 2
        else:  # 'left' or 'right' share the middle row
            row = 1
        frame = sheet.subsurface(pygame.Rect(0, row * row_h, w, row_h)).copy()
        if self.direction == 'left':
            frame = pygame.transform.flip(frame, True, False)
        return frame

    def _pick_body_frame(self, sheet):
        """Slice the body sheet's 2 stacked rows (right-base / down-base)
        and return the one frame matching self.direction, flipping the
        shared base row for 'left' (horizontal) or 'up' (vertical)."""
        w, h = sheet.get_width(), sheet.get_height()
        row_h = h // 2
        if self.direction in ('left', 'right'):
            row = 0
        else:  # 'up' or 'down'
            row = 1
        frame = sheet.subsurface(pygame.Rect(0, row * row_h, w, row_h)).copy()
        if self.direction == 'left':
            frame = pygame.transform.flip(frame, True, False)
        elif self.direction == 'up':
            frame = pygame.transform.flip(frame, False, True)
        return frame

    def _pick_head_end_frames(self, sheet):
        """Same 3-stacked-direction-row layout as _pick_head_frame, except
        each row is ALSO cut into head_end_frame_count equal-width
        columns — a per-direction filmstrip instead of one static frame.
        Returns the list of frames for self.direction only, in playback
        order; 'left' flips the shared right-facing row's frames
        individually, same as the static head frame does."""
        w, h = sheet.get_width(), sheet.get_height()
        row_h = h // 3
        if self.direction == 'down':
            row = 0
        elif self.direction == 'up':
            row = 2
        else:  # 'left' or 'right' share the middle row
            row = 1
        frame_w = w // self.head_end_frame_count
        frames = []
        for i in range(self.head_end_frame_count):
            frame = sheet.subsurface(
                pygame.Rect(i * frame_w, row * row_h, frame_w, row_h)
            ).copy()
            if self.direction == 'left':
                frame = pygame.transform.flip(frame, True, False)
            frames.append(frame)
        return frames

    def _pick_destruction_frames(self, sheet):
        """brown_destruction has no direction rows at all — it's a single
        row cut into destruction_frame_count equal-width columns, played
        the same way regardless of which way the fist was thrown."""
        w, h = sheet.get_width(), sheet.get_height()
        frame_w = w // self.destruction_frame_count
        return [
            sheet.subsurface(pygame.Rect(i * frame_w, 0, frame_w, h)).copy()
            for i in range(self.destruction_frame_count)
        ]

    @staticmethod
    def _placeholder(width, height, color):
        surf = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.ellipse(surf, color, (0, 0, width, height))
        return surf

    # ------------------------------------------------------------------
    # Leash box
    # ------------------------------------------------------------------
    def _leash_bounds(self, player_x, player_y):
        """(min_x, max_x, min_y, max_y) the head may currently occupy.

        The far side (continuing outward in the throw direction) and both
        cross-axis sides are anchored to the player's CURRENT position —
        which can move during the opening lunge (see
        Player._advance_dragon_fist_lunge) or ordinary walking once the
        player's free to move again — so this is recomputed fresh every
        frame rather than fixed at throw time — and get a fixed reach,
        forward_range/lateral_range.

        Whichever side points back toward the player (the opposite of the
        direction the fist was originally thrown) is instead capped at
        the ANCHOR's (body_positions[-1]) coordinate on that axis — the
        body piece pinned anchor_offset in front of the player every
        frame (see _update_anchor(), which runs before this is called) —
        not body_positions[0], and not the player's raw position.

        body_positions[0] is the wrong reference for this: it chases the
        HEAD, snapping to within link_distance of it almost immediately,
        so as soon as the head retreats a little, body0 catches right up
        behind it and the bound clamps shut again — the head gets stuck
        after retreating only about one link_distance, unless the player
        physically moves (which is what drags the anchor, and through it
        the rest of the chain, backward). The anchor has no such lag —
        it's recomputed fresh from the player's current position every
        frame — so using it as the back edge lets the head retreat all
        the way to right in front of the player, every time, regardless
        of chain state.
        """
        anchor = self.body_positions[-1]
        if self.direction == 'right':
            return (anchor[0], player_x + self.forward_range,
                    player_y - self.lateral_range, player_y + self.lateral_range)
        elif self.direction == 'left':
            return (player_x - self.forward_range, anchor[0],
                    player_y - self.lateral_range, player_y + self.lateral_range)
        elif self.direction == 'down':
            return (player_x - self.lateral_range, player_x + self.lateral_range,
                    anchor[1], player_y + self.forward_range)
        else:  # 'up'
            return (player_x - self.lateral_range, player_x + self.lateral_range,
                    player_y - self.forward_range, anchor[1])

    def clamp_head_to_leash(self, player_x, player_y):
        min_x, max_x, min_y, max_y = self._leash_bounds(player_x, player_y)
        self.head_x = max(min_x, min(self.head_x, max_x))
        self.head_y = max(min_y, min(self.head_y, max_y))

    # ------------------------------------------------------------------
    # State control
    # ------------------------------------------------------------------
    def start_retract(self):
        """Q released, or Ki ran out — begin the closing sequence instead
        of ending instantly: dragon_fist_head_end plays once on the head
        alone, then the whole assembly (head + every body segment,
        including the anchor sitting right in front of the player)
        switches to brown_destruction and plays that once too — only
        THEN does the attack actually end (see _update_end_sequence()).

        Everything freezes exactly where it is the instant this is
        called (update() stops all anchor/chain/leash logic the moment
        end_phase is set) — same stays-exactly-where-it-was design as
        before, just stretched over these two closing animations instead
        of ending on the very next frame.

        Safe to call more than once; only the first call has any effect.
        """
        if self.state != 'retracting':
            self.state = 'retracting'
            self.end_phase = 'head_end'
            self.end_elapsed = 0.0

    def translate(self, dx, dy):
        """Shift the head, the shoot origin, and every free chain segment
        by (dx, dy) in world space, in one go.

        Used by Player during the opening lunge (see
        Player._advance_dragon_fist_lunge) so the whole fist rides along
        with the player instead of getting left behind — without this,
        only body_positions[-1] (the anchor) would track the player each
        frame, and the head/chain would appear to drift backward relative
        to the player for the length of the lunge.

        origin_x/origin_y move too, which matters during 'shooting': the
        travelled-distance check in update() is `hypot(head - origin)`,
        so shifting both by the same amount leaves that distance (and
        therefore when the shoot phase ends) unaffected by the lunge.

        body_positions[-1] is deliberately skipped — it's overwritten
        from the player's live position every update() call regardless
        (see _update_anchor()), so translating it here would just be
        immediately clobbered.
        """
        self.head_x += dx
        self.head_y += dy
        self.origin_x += dx
        self.origin_y += dy
        for seg in self.body_positions[:-1]:
            seg[0] += dx
            seg[1] += dy
        for target in self.chain_targets:
            target[0] += dx
            target[1] += dy

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------
    def update(self, dt, player_x, player_y):
        if not self.active:
            return

        if self.end_phase is not None:
            # 'retracting' only ever gets reached via start_retract(),
            # which always sets end_phase in that same call — so once
            # we're here, the assembly is frozen and only the closing
            # animations are still playing.
            self._update_end_sequence(dt)
            return

        self._update_anchor(player_x, player_y)

        if self.state == 'shooting':
            dxu, dyu = _DIRECTION_UNIT.get(self.direction, (0, 0))
            self.head_x += dxu * self.shoot_speed * dt
            self.head_y += dyu * self.shoot_speed * dt
            traveled = math.hypot(self.head_x - self.origin_x, self.head_y - self.origin_y)
            if traveled >= self.shoot_distance:
                # Snap exactly to shoot_distance so a large dt overshooting
                # this frame doesn't leave the head further out than the
                # controlled phase's own leash box would otherwise allow.
                self.head_x = self.origin_x + dxu * self.shoot_distance
                self.head_y = self.origin_y + dyu * self.shoot_distance
                self.state = 'controlled'

        elif self.state == 'controlled':
            # Movement input itself is applied directly in
            # Player._move_dragon_fist_head, once per input frame — this
            # just re-clamps every update() call in case the leash box
            # moved since (its "back" edge tracks the player's current
            # position, which can move during the opening lunge — see
            # Player._advance_dragon_fist_lunge — or from ordinary
            # walking once the player's free to move again).
            self.clamp_head_to_leash(player_x, player_y)

        # Everything about the chain — target waypoints, the spring easing
        # toward them, and the gap safety clamp — only actually advances
        # on these throttled ticks. That's deliberate: if _slide_chain
        # ran every real frame (as it used to), body_positions would
        # change value 60 times a second regardless of chain_update_fps,
        # which reads as smooth motion with occasional jerks no matter
        # how low chain_update_fps is set — lowering it only made the
        # target jump bigger, not the visible motion choppier. Stepping
        # the spring itself at chain_update_fps means body_positions
        # genuinely only change on these ticks and hold perfectly still
        # in between, which is what actually reads as "running at a
        # lower fps." step_dt (the real time since the last tick, not a
        # fixed 1/chain_update_fps) is what's fed to the spring, so the
        # per-tick jump still reflects an accelerating catch-up rather
        # than a fixed-size hop.
        self.chain_update_timer += dt
        if self.chain_update_timer >= self.chain_update_interval:
            step_dt = self.chain_update_timer
            self.chain_update_timer -= self.chain_update_interval
            self._compute_chain_targets()
            self._slide_chain(step_dt)
            self._clamp_chain_gaps()

    def _update_end_sequence(self, dt):
        """Advance whichever closing animation is currently playing, and
        hand off to the next one — or finish the attack for good — once
        it's played through all of its frames exactly once.

        Uses a single elapsed-time counter rather than a frame index:
        total_duration = frame_count * frame_duration for whichever phase
        is active, and the actual frame shown is derived from
        self.end_elapsed at draw time (see _end_frame()). Simpler than
        tracking a separate index + per-frame timer, and doesn't lose
        time across a frame boundary the way an int(dt // frame_duration)
        step-counter would on an inconsistent framerate.
        """
        if self.end_phase == 'head_end':
            total_duration = self.head_end_frame_count * self.head_end_frame_duration
        else:  # 'destruction'
            total_duration = self.destruction_frame_count * self.destruction_frame_duration

        self.end_elapsed += dt
        if self.end_elapsed >= total_duration:
            if self.end_phase == 'head_end':
                # dragon_fist_head_end has played through once — hand off
                # to the shared destruction effect across the whole
                # assembly (see draw()).
                self.end_phase = 'destruction'
            else:
                # brown_destruction has played through once too — the
                # attack is genuinely over now.
                self.active = False
            self.end_elapsed = 0.0

    def _end_frame(self, frames, frame_count, frame_duration):
        """Whichever frame of `frames` self.end_elapsed currently falls
        into, clamped to the last frame (in case of a large dt landing
        past the phase's total duration right before _update_end_sequence
        rolls it over). Returns None if `frames` failed to load, same as
        head_sprite/body_sprite being None — draw()/_draw_piece() already
        know how to fall back to a placeholder in that case."""
        if not frames:
            return None
        idx = min(int(self.end_elapsed / frame_duration), frame_count - 1)
        return frames[idx]

    def _update_anchor(self, player_x, player_y):
        """body_positions[-1] isn't part of the chain — it's pinned
        anchor_offset in front of the player's CURRENT position, along the
        attack's (fixed) throw direction, every frame. This keeps it
        sitting right in front of the player no matter what the head and
        the rest of the chain are doing.

        It's the LAST element, not the first: the pull chain in
        _compute_chain_targets() cascades head → body[0] → body[1] → ... →
        body[-1], so body[0] ends up closest to the head and body[-1]
        ends up closest to the player — body[-1] is the one that needs to
        be pinned.
        """
        dxu, dyu = _DIRECTION_UNIT.get(self.direction, (0, 0))
        anchor = self.body_positions[-1]
        anchor[0] = player_x + dxu * self.anchor_offset
        anchor[1] = player_y + dyu * self.anchor_offset

    def _compute_chain_targets(self):
        """Recompute the ideal evenly-spaced waypoint for every free
        segment (body_positions[:-1]), along the straight line from the
        head to the anchor (body_positions[-1]), proportional to however
        far apart those two currently are. Stored in self.chain_targets;
        actual segment positions are eased toward these every real frame
        by _slide_chain() rather than being set to them directly here.

        Replaces an earlier two-pass chase/pull solver (each segment
        chased whatever was one link ahead of it, capped at
        link_distance) that turned out to leave segments overlapped
        whenever the head-to-anchor distance was well under the chain's
        max possible stretch (num_segments * link_distance) — which is
        the common case, not an edge case: every segment starts bunched
        at the anchor on throw, and that solver only lets slack
        propagate one link per frame once that link's OWN gap exceeds
        link_distance. Simulating the default shoot phase (head races
        60 units out over 0.2s) showed only the first two segments ever
        separated from the anchor at all — the rest stayed stacked
        exactly on top of it, for as long as the head-anchor distance
        stayed short. That bug always existed; the opening lunge just
        made it visible for 1.5s instead of a blink-and-miss 0.2s.

        Direct proportional interpolation has no such propagation delay
        and no dependency on relative motion — every target is always
        exactly total_distance/num_links from its neighbors, including
        while the head is stationary. It's also monotonic by
        construction — no target can ever land further from the anchor
        (measured toward the head) than the one behind it.
        """
        head_x, head_y = self.head_x, self.head_y
        anchor_x, anchor_y = self.body_positions[-1]

        num_links = len(self.body_positions)  # head->0, 0->1, ..., -2->anchor
        for i in range(num_links - 1):
            frac = (i + 1) / num_links
            target = self.chain_targets[i]
            target[0] = head_x + (anchor_x - head_x) * frac
            target[1] = head_y + (anchor_y - head_y) * frac

    def _slide_chain(self, dt):
        """Ease every free segment (body_positions[:-1]) toward its
        current waypoint (self.chain_targets) using a critically-damped
        spring per axis, instead of snapping straight onto it.

        This is what gives the trailing segments their sliding,
        accelerating look: right after a waypoint jump (see
        _compute_chain_targets, throttled to chain_update_fps) each
        segment is momentarily far from its new target, so it eases
        away from rest, builds up speed, then decelerates smoothly into
        place — with no overshoot/oscillation, since the spring is
        critically damped. The anchor (body_positions[-1]) is skipped —
        it's pinned directly to the player every frame (see
        _update_anchor()), not part of this easing.
        """
        for seg, vel, target, smooth_time in zip(
                self.body_positions[:-1], self.chain_velocities,
                self.chain_targets, self.chain_smooth_times):
            seg[0], vel[0] = self._smooth_damp(
                seg[0], vel[0], target[0], smooth_time, dt)
            seg[1], vel[1] = self._smooth_damp(
                seg[1], vel[1], target[1], smooth_time, dt)

    def _clamp_chain_gaps(self):
        """Safety net: cap every consecutive gap along head -> body[0] ->
        ... -> body[-2] -> anchor at link_distance * chain_gap_safety_margin
        (generously above the per-segment smooth_time gradient's normal
        working range — see its comment in __init__). Applied right after
        _slide_chain().

        This used to be a tight link_distance clamp on just the head and
        anchor links, on the theory that only those two (attached to
        something that moves instantly/unsprung) could visibly detach.
        In practice that just relocated the problem: forcing body[0]
        tight to the head made it visibly outrun body[1], which was
        still catching up on an identical, slower schedule — a gap one
        link further down instead of at the head.

        The actual fix is the smooth_time gradient in __init__ (tightest
        near the head, loosest near the tail), which keeps every pair of
        neighbors on similar catch-up rates so their relative gap stays
        small on its own. This clamp is now just a generous fallback for
        genuine outliers (e.g. a frame hitch), not something meant to
        fire routinely — a tight per-frame clamp here would recreate the
        every-frame-recompute behavior and erase the throttled, sliding
        look entirely.
        """
        free = self.body_positions[:-1]
        if not free:
            return
        max_gap = self.link_distance * self.chain_gap_safety_margin

        prev_x, prev_y = self.head_x, self.head_y
        for seg in free:
            prev_x, prev_y = self._pull_within(seg, prev_x, prev_y, max_gap)

        anchor_x, anchor_y = self.body_positions[-1]
        next_x, next_y = anchor_x, anchor_y
        for seg in reversed(free):
            next_x, next_y = self._pull_within(seg, next_x, next_y, max_gap)

    @staticmethod
    def _pull_within(seg, ref_x, ref_y, max_dist):
        """If `seg` is further than max_dist from (ref_x, ref_y), pull it
        directly in along that line until it's exactly max_dist away.
        Mutates seg in place and returns its (possibly unchanged)
        position, so callers can chain it as the next ref point."""
        dx = seg[0] - ref_x
        dy = seg[1] - ref_y
        dist = math.hypot(dx, dy)
        if dist > max_dist and dist > 1e-6:
            scale = max_dist / dist
            seg[0] = ref_x + dx * scale
            seg[1] = ref_y + dy * scale
        return seg[0], seg[1]

    @staticmethod
    def _smooth_damp(current, current_vel, target, smooth_time, dt):
        """Critically-damped spring toward `target` (the standard
        SmoothDamp formulation — see Unity's Mathf.SmoothDamp / Game
        Programming Gems 4). Returns (new_position, new_velocity).

        Chosen over a plain exponential lerp (`pos += (target - pos) *
        rate`) specifically because a lerp is fastest at the very start
        and only decelerates — it can't produce the "accelerate away
        from rest" half of a slide. This carries velocity across calls,
        so a segment that's still moving from the last waypoint smoothly
        blends into chasing the new one instead of restarting from a
        standstill every throttled update.
        """
        smooth_time = max(0.0001, smooth_time)
        omega = 2.0 / smooth_time
        x = omega * dt
        exp = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)

        change = current - target
        temp = (current_vel + omega * change) * dt
        new_vel = (current_vel - omega * temp) * exp
        new_pos = target + (change + temp) * exp
        return new_pos, new_vel

    # ------------------------------------------------------------------
    # Collision
    # ------------------------------------------------------------------
    def get_segment_rects(self):
        """World-space pygame.Rects for every individual piece — the head
        plus all of body_positions (including the anchor sitting right in
        front of the player) — used by enemy.py's 'dragon_fist' collision
        check.

        Unlike flame_kamehameha's get_world_bounds(), which returns one
        rect enclosing its whole (always straight-line) chain, this
        returns one rect PER piece. flame_kamehameha's three segments are
        always laid out along a single straight line from the player, so
        a union rect never covers empty space. This chain bends — each
        body segment eases independently toward its own spring-damped
        target (see _slide_chain) — so a single bounding box could sweep
        across a wide arc of empty space between the head and the anchor
        and register hits nothing on the chain ever actually touched.
        Testing each piece as its own hitbox means only the segments
        actually overlapping something can land a hit.
        """
        rects = []
        hw, hh = self.head_size
        rects.append(pygame.Rect(
            self.head_x - hw / 2, self.head_y - hh / 2, hw, hh))

        bw, bh = self.body_size
        for x, y in self.body_positions:
            rects.append(pygame.Rect(x - bw / 2, y - bh / 2, bw, bh))

        return rects

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def draw(self, screen, camera, colors=None):
        if not self.active:
            return

        points = [(self.head_x, self.head_y)] + [tuple(p) for p in self.body_positions]

        if self.end_phase == 'destruction':
            # Whole assembly — head and every body segment, including the
            # anchor sitting right in front of the player — puffs into
            # the same shared brown_destruction effect together, once,
            # right before the attack actually ends.
            frame = self._end_frame(self.destruction_frames, self.destruction_frame_count,
                                     self.destruction_frame_duration)
            for i in range(len(points) - 1, 0, -1):
                self._draw_piece(screen, camera, points[i],
                                  frame, self.destruction_size, (200, 120, 60))
            self._draw_piece(screen, camera, points[0],
                              frame, self.destruction_size, (200, 120, 60))
            return

        # Body segments first, back-to-front, so the head ends up drawn on
        # top of all of them. Facing is fixed to the throw direction (see
        # _load_sprites), not to the chain's actual path, so there's
        # nothing direction-dependent to compute per segment here.
        for i in range(len(points) - 1, 0, -1):
            self._draw_piece(screen, camera, points[i],
                              self.body_sprite, self.body_size, (120, 170, 255))

        if self.end_phase == 'head_end':
            head_frame = self._end_frame(self.head_end_frames, self.head_end_frame_count,
                                          self.head_end_frame_duration)
            self._draw_piece(screen, camera, points[0],
                              head_frame, self.head_size, (255, 90, 90))
        else:
            self._draw_piece(screen, camera, points[0],
                              self.head_sprite, self.head_size, (255, 90, 90))

    def _draw_piece(self, screen, camera, pos, sprite, size, fallback_color):
        """size is a (width, height) pair — head and body can have
        different, non-square dimensions, so width/height are scaled
        independently rather than forcing everything into a square."""
        screen_x = pos[0] * self.scale - camera.x
        screen_y = pos[1] * self.scale - camera.y

        width, height = size
        scaled_w = int(width * self.scale)
        scaled_h = int(height * self.scale)
        if sprite:
            frame = pygame.transform.scale(sprite, (scaled_w, scaled_h))
        else:
            frame = self._placeholder(scaled_w, scaled_h, fallback_color)
        rect = frame.get_rect(center=(int(screen_x), int(screen_y)))
        screen.blit(frame, rect)