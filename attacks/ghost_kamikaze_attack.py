import math
import pygame
from config.settings import RENDER_SCALE as _RENDER_SCALE
from core.draw_layers import DrawLayer
from core.sprite_system import SpriteSheet, DIRECTIONS_4


# (forward, right) unit vectors for each facing direction, in world coords
# where +x is world-right and +y is world-down (matches
# player.py's _DIRECTION_SPAWN_OFFSETS, e.g. 'down': (0, 10)). 'right' here
# means the ghosts' formation-right relative to the way the attack is
# facing — not fixed world-right — so the left/right/middle fan (see
# GhostKamikazeAttack._formation_target()) rotates correctly with facing
# instead of only working for up/down. Derived by rotating `forward` 90°
# (right_x, right_y) = (forward_y, -forward_x); written out explicitly
# rather than computed at runtime so each row's own values are visible
# and easy to flip a sign on if the "left"/"right" ghosts come out mirrored
# from what looks right in-game.
_DIRECTION_AXES = {
    'down':  {'forward': (0, 1),  'right': (1, 0)},
    'up':    {'forward': (0, -1), 'right': (-1, 0)},
    'left':  {'forward': (-1, 0), 'right': (0, 1)},
    'right': {'forward': (1, 0),  'right': (0, -1)},
}


# Ghost kamikaze's own spawn offset, separate from player.py's shared
# _DIRECTION_SPAWN_OFFSETS (used by beam/kamehameha/final flash/etc.) so
# this attack's spawn point can be tuned without affecting every other
# attack that shares that dict. Values start matching the shared
# defaults purely as a starting point — tune freely. This is where
# origin_x/origin_y (the point ghosts appear at before fanning out to
# their formation slot — see GhostKamikazeAttack.__init__ /
# _formation_target()) comes from; get_ghost_kamikaze_spawn_offset() is
# called from Player.start_ghost_kamikaze() instead of the shared
# _get_spawn_offset().
_GHOST_KAMIKAZE_SPAWN_OFFSETS = {
    'up':    (-7,   -10),
    'down':  (7,    0),
    'left':  (-18,   -2),
    'right': (18,    2),
}


def get_ghost_kamikaze_spawn_offset(direction):
    """(offset_x, offset_y) for where the ghost kamikaze attack spawns,
    based on facing direction. Falls back to (0, 0) for an unrecognized
    direction, same convention as player.py's _get_spawn_offset()."""
    return _GHOST_KAMIKAZE_SPAWN_OFFSETS.get(direction, (0, 0))


# Shared ghost shadow sprite/cache — mirrors LayerManager._load_shadow's
# own asset path + ellipse-fallback convention in draw_layers.py, but kept
# local here since LayerManager only ever shadows whatever single object
# it has registered (matched by type(obj).__name__ against _SHADOW_TYPES,
# using that object's own .x/.y/.width/.height). The thing actually
# registered with LayerManager is one GhostKamikazeAttack, not each
# individual _Ghost inside it, so LayerManager has no way to cast a
# separate shadow per ghost — each ghost's shadow has to be drawn here,
# directly in GhostKamikazeAttack.draw(), instead.
_GHOST_SHADOW_SPRITE = None
_GHOST_SHADOW_CACHE = {}


def _load_ghost_shadow_sprite():
    global _GHOST_SHADOW_SPRITE
    if _GHOST_SHADOW_SPRITE is None:
        try:
            _GHOST_SHADOW_SPRITE = pygame.image.load(
                'assets/sprites/universal/shadow.png').convert_alpha()
        except Exception:
            s = pygame.Surface((32, 12), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (0, 0, 0, 80), s.get_rect())
            _GHOST_SHADOW_SPRITE = s
    return _GHOST_SHADOW_SPRITE


def _get_scaled_ghost_shadow(entity_width):
    """Cached shadow scaled to ~32% of entity_width, same ratio
    LayerManager._get_scaled_shadow uses for Player/Enemy/NPC, so ghost
    shadows read as the same visual language rather than a mismatched
    one-off.

    entity_width should be _PLAYER_SHADOW_REFERENCE_WIDTH (see below)
    scaled by self.scale, NOT the ghost sprite's own width — ghost_size
    is (16, 32), half the player's own 32px width (player.py's
    self.width), so keying this off the ghost's own width produced a
    shadow half the player's size instead of matching it."""
    source = _load_ghost_shadow_sprite()
    key = entity_width
    if key not in _GHOST_SHADOW_CACHE:
        orig_w = source.get_width()
        orig_h = source.get_height()
        target_w = max(8, int(entity_width * 0.32))
        target_h = max(4, int(orig_h * target_w / orig_w))
        _GHOST_SHADOW_CACHE[key] = pygame.transform.scale(source, (target_w, target_h))
    return _GHOST_SHADOW_CACHE[key]


# The width the ghost's shadow is sized against — deliberately the
# player's own width (player.py's self.width = 32), not ghost_size's
# narrower 16px, so the ghost casts the same size shadow the player
# does rather than a smaller one scaled to its own (narrower) sprite.
_PLAYER_SHADOW_REFERENCE_WIDTH = 32


# How long, in seconds, a ghost's 'homing' dash takes to ramp up from a
# standing start to full ghost_speed — see _Ghost's 'homing' branch in
# update(). Kept as a flat duration rather than e.g. a fixed distance so
# the wind-up feels the same regardless of how far the target happens to
# be when start_homing() is called (a very close target would otherwise
# barely get to accelerate at all before impact_range cuts it off).
_HOMING_ACCEL_DURATION = 0.35


# Vertical-only bob for a settled 'idle' ghost — see _Ghost.get_hover_offset().
# Amplitude is in world-space pixels (same units as self.x/self.y), scaled
# by GhostKamikazeAttack.scale at draw time like everything else the ghost
# draws at. Period is one full up-down-back-to-up cycle, in seconds.
# THESE ARE THE TWO KNOBS FOR TUNING THE HOVER: bigger _HOVER_AMPLITUDE =
# taller bob, smaller _HOVER_PERIOD = faster bob (it's a period, not a
# speed, so smaller means quicker).
_HOVER_AMPLITUDE = 2.5
_HOVER_PERIOD = 1.4

# How long, in seconds, the bob takes to ramp up to full _HOVER_AMPLITUDE
# after a ghost enters 'idle', rather than applying full amplitude from
# the very first 'idle' tick. Needed because get_hover_offset() returns
# 0 the entire time a ghost is 'traveling', then starts returning
# _HOVER_AMPLITUDE * sin(_hover_phase) the instant it becomes 'idle' —
# and _hover_phase (each ghost's stagger, so all three don't bob in sync)
# is essentially random, so that first 'idle' value is essentially never
# actually 0. Without this fade-in, that's a same-frame pop from
# "no offset" to "wherever that ghost's phase happens to sit" — small
# for a ghost whose phase happens to land near a zero-crossing, visible
# for one that doesn't (the concrete bug this fixes: ghost 1's phase
# landing far from a zero-crossing, popping visibly the instant it
# reaches its formation slot). Ramping the *amplitude* up from 0 instead
# of jumping straight to it guarantees the offset is exactly 0 at the
# moment 'idle' begins no matter what _hover_phase happens to be, so
# there's never a pop to begin with.
_HOVER_FADE_IN_DURATION = 0.25


class _Ghost:
    """One of the three ghost entities spawned by GhostKamikazeAttack.

    Facing is permanent for the life of the ghost — whichever direction
    the player was facing when the attack launched is the direction its
    sprite faces the whole time (self.direction, set once in __init__ and
    never changed), same "facing locked at launch" convention
    dragon_fist already uses. That's true through 'homing' too — a ghost
    en route to an enemy that isn't directly ahead still faces the
    original launch direction rather than turning to face its target.

    Lifecycle (self.state):
      'spawning'  — creation animation plays through once and holds on
                    its last frame (same play-once-and-hold behavior
                    'impact' uses for destruction — see
                    _advance_filmstrip()), held at spawn_x/spawn_y (in
                    front of the player — see
                    GhostKamikazeAttack.spawn_next_ghost()).
                    creation_frame_duration is tuned to finish that pass
                    right around when the player's own cast-animation
                    loop that spawned this ghost finishes its loop.
                    Falls through to 'traveling' when told to from
                    outside via begin_travel() — see GhostKamikazeAttack.
                    finish_current_ghost_spawn() / Player.
                    update_ghost_kamikaze_cast() — not on any timer of
                    its own. begin_travel() cuts over immediately,
                    wherever the filmstrip happens to be — normally
                    already holding on the last frame by then; a spawn
                    landing unusually late in the cast loop, or too slow
                    a creation_frame_duration, would cut it off early
                    rather than stall the hand-off, so getting that
                    timing right is a tuning question, not something
                    this state machine papers over.
      'traveling' — switched to its idle sprite, moving from spawn_x/
                    spawn_y out to its actual formation slot (target_x/
                    target_y — left, right, or middle, relative to the
                    attack's facing — see
                    GhostKamikazeAttack._formation_target()) at
                    formation_speed. Falls through to 'idle' on arrival.
      'idle'      — settled at its formation slot on its idle sprite,
                    bobbing gently up and down in place (visual only —
                    see get_hover_offset()) while waiting on
                    GhostKamikazeAttack to resolve (hold timer running
                    out, or the player moving early — see
                    GhostKamikazeAttack.launch_now()).
      'homing'    — moving toward the locked target enemy's current
                    position, ramping up from a standing start to
                    ghost_speed over _HOMING_ACCEL_DURATION (see
                    update()'s 'homing' branch) rather than snapping
                    straight to full speed — reads as a kamikaze dash
                    winding up rather than gliding over at a flat
                    constant speed. Same idle sprite as above.
                    Ends when GhostKamikazeAttack reports a real hit via
                    trigger_impact() (see game.py's per-enemy collision
                    loop and enemy.py's 'ghost_kamikaze_attack' branch of
                    check_collision_with_attack), or — as a
                    safety net — when this ghost gets within impact_range
                    on its own, which covers the target enemy dying or
                    otherwise vanishing before that collision check ever
                    fires.
      'impact'    — brown_destruction plays once (no facing — see
                    GhostKamikazeAttack._load_destruction_sheet), then
                    done=True and GhostKamikazeAttack drops it.
      (no-target case: 'idle' skips 'homing' entirely and jumps straight
       to 'impact' via trigger_impact() — see
       GhostKamikazeAttack._resolve()).
    """

    def __init__(self, spawn_x, spawn_y, target_x, target_y, direction,
                 creation_frames, idle_frames, destruction_frames,
                 size, creation_frame_duration, idle_frame_duration, destruction_frame_duration):
        self.x = spawn_x
        self.y = spawn_y
        self.target_x = target_x
        self.target_y = target_y
        self.direction = direction
        # {direction: [Surface, ...]} for creation/idle — see
        # GhostKamikazeAttack._load_directional_sheet(). destruction_frames
        # stays a flat list (brown_destruction has no direction rows).
        self.creation_frames = creation_frames
        self.idle_frames = idle_frames
        self.destruction_frames = destruction_frames
        self.size = size
        self.creation_frame_duration = creation_frame_duration
        self.idle_frame_duration = idle_frame_duration
        self.destruction_frame_duration = destruction_frame_duration

        self.state = 'spawning'
        self.frame_index = 0
        self.frame_timer = 0.0
        self.target = None
        self.done = False
        # Snapshot of where 'traveling' started (self.x/self.y at the
        # moment the state flips over, taken the first 'traveling'
        # update() tick rather than in begin_travel() itself — just
        # convenience, since begin_travel() flips state directly) and
        # how long the ease is meant to take, both used by
        # _ease_toward() to tween position off a 0..1 progress fraction
        # instead of stepping at a constant per-frame speed. Constant
        # speed was what made the formation travel read as "snappy": full
        # speed the instant it starts, dead stop the instant it arrives.
        # _travel_duration is derived from the straight-line distance at
        # the start of the ease and formation_speed, so the *average*
        # speed across the whole trip still roughly matches
        # formation_speed — it's only the moment-to-moment pacing along
        # the way that's now eased instead of flat.
        self._travel_start_x = None
        self._travel_start_y = None
        self._travel_duration = 0.0
        self._travel_elapsed = 0.0
        # Elapsed time since start_homing() was called — drives the
        # accel ramp in update()'s 'homing' branch (see
        # _HOMING_ACCEL_DURATION above). Reset in start_homing() itself,
        # not here, since a ghost's homing can only ever begin once but
        # __init__ runs long before that.
        self._homing_elapsed = 0.0
        # Elapsed time spent in 'idle', and a per-instance phase offset
        # into the hover cycle — see get_hover_offset(). Reset isn't
        # needed the way _homing_elapsed's is: a ghost only ever enters
        # 'idle' once (from 'traveling', on arrival — see update()),
        # never re-enters it, so there's no second entry to reset for.
        self._hover_elapsed = 0.0
        self._hover_phase = (id(self) % 997) / 997.0 * (2 * math.pi)

    def begin_travel(self):
        """Ends 'spawning' and starts moving out to the formation slot on
        the idle sprite, right now — called externally by
        GhostKamikazeAttack.finish_current_ghost_spawn(), in turn called
        by Player.update_ghost_kamikaze_cast() the instant the cast
        animation loop that spawned this ghost finishes its own loop.
        No-ops if called twice or out of order (state is no longer
        'spawning').

        The creation filmstrip (see update()'s 'spawning' branch, which
        drives it with _advance_filmstrip — play through once and hold
        on the last frame, same as destruction) is timed via
        creation_frame_duration to finish on its own, on the last frame,
        right around when this gets called — it does not loop and does
        not wait to be told it's finished. So there's nothing to defer
        here: if the filmstrip already reached its last frame and is
        holding there, this just cuts over on that same frame; if it's
        still mid-pass (a slow creation_frame_duration, or a spawn that
        landed late in the player's cast loop) this simply cuts it off
        wherever it is, rather than stalling the hand-off to wait for
        frames that were never going to have time to show. Getting that
        timing right is a matter of tuning creation_frame_duration
        against the player's own cast-loop length, not something this
        method should paper over by looping/deferring — an earlier
        version of this file did exactly that (looping the creation
        filmstrip indefinitely and deferring the cutover to the next
        loop wrap) and it's what caused creation sprites to visibly
        replay an extra full pass."""
        if self.state != 'spawning':
            return
        self.state = 'traveling'
        self.frame_index = 0
        self.frame_timer = 0.0

    def _advance_filmstrip(self, frames, dt, frame_duration):
        """Play-once helper: advances frame_index up to the last frame and
        holds there (used for creation/destruction, which each play
        through exactly once). Returns True once fully played out."""
        if not frames:
            return True
        if self.frame_index >= len(frames) - 1:
            return True
        self.frame_timer += dt
        if self.frame_timer >= frame_duration:
            self.frame_timer = 0.0
            self.frame_index += 1
        return self.frame_index >= len(frames) - 1

    def _advance_idle_loop(self, dt):
        """Idle/traveling/homing filmstrip loops for as long as the ghost
        is on it, rather than playing once and holding. Whether
        ghost_idle.png actually has more than one frame per direction
        wasn't specified — this handles either case: a 1-frame row just
        holds on frame 0 forever (the `len(frames) <= 1` guard), a
        multi-frame one loops normally."""
        frames = self.idle_frames.get(self.direction) or []
        if len(frames) <= 1:
            return
        self.frame_timer += dt
        if self.frame_timer >= self.idle_frame_duration:
            self.frame_timer = 0.0
            self.frame_index = (self.frame_index + 1) % len(frames)

    def _ease_toward(self, dt, speed):
        """Smoothstep (3t²-2t³) ease-in/ease-out tween from
        _travel_start_x/y to target_x/y, used for the formation
        'traveling' state instead of _move_toward's constant-speed
        stepping. Ramps up from a standstill, cruises, then settles back
        down to a stop right on the slot, rather than snapping straight
        to full speed and slamming to a halt — that abruptness is what
        read as "snappy" with plain constant-velocity stepping.

        _travel_duration (straight-line distance at the moment travel
        started, divided by `speed`) is computed once, the first tick
        this runs — see update()'s 'traveling' branch, which sets up
        _travel_start_x/y right before calling this — so the eased trip
        still takes about as long overall as a constant-speed trip at
        `speed` would have; only the pacing along the way changes.

        Returns True once progress reaches 1.0 (arrived)."""
        if self._travel_duration <= 0:
            self.x, self.y = self.target_x, self.target_y
            return True
        self._travel_elapsed += dt
        t = min(self._travel_elapsed / self._travel_duration, 1.0)
        eased = t * t * (3 - 2 * t)
        self.x = self._travel_start_x + (self.target_x - self._travel_start_x) * eased
        self.y = self._travel_start_y + (self.target_y - self._travel_start_y) * eased
        return t >= 1.0

    def _move_toward(self, dt, speed, dest_x, dest_y):
        """Step toward (dest_x, dest_y) at `speed`; returns True once
        within a hair of arriving (and snaps exactly onto it that frame,
        so nothing sits fractionally off its slot forever)."""
        dx = dest_x - self.x
        dy = dest_y - self.y
        dist = math.hypot(dx, dy)
        if dist <= 0.5:
            self.x, self.y = dest_x, dest_y
            return True
        step = speed * dt
        if step >= dist:
            self.x, self.y = dest_x, dest_y
            return True
        self.x += dx / dist * step
        self.y += dy / dist * step
        return False

    def update(self, dt, ghost_speed, formation_speed, impact_range):
        if self.state == 'spawning':
            # Plays through once and holds on the last frame — same
            # play-once-and-hold helper 'impact' uses below for
            # destruction. This state itself never ends on a timer;
            # begin_travel() (called externally once the player's cast
            # loop that spawned this ghost finishes) is what ends it.
            frames = self.creation_frames.get(self.direction) if self.creation_frames else None
            self._advance_filmstrip(frames, dt, self.creation_frame_duration)

        elif self.state == 'traveling':
            self._advance_idle_loop(dt)
            if self._travel_start_x is None:
                # First tick of travel — snapshot where it started from
                # and derive how long the ease should take from the
                # straight-line distance and formation_speed (see
                # _ease_toward). Doing this here rather than in
                # begin_travel() itself is just convenience — begin_travel()
                # flips self.state directly, so this is simply the first
                # update() tick after that happens.
                self._travel_start_x, self._travel_start_y = self.x, self.y
                dist = math.hypot(self.target_x - self.x, self.target_y - self.y)
                self._travel_duration = dist / formation_speed if formation_speed > 0 else 0.0
                self._travel_elapsed = 0.0
            if self._ease_toward(dt, formation_speed):
                self.state = 'idle'

        elif self.state == 'idle':
            self._advance_idle_loop(dt)
            self._hover_elapsed += dt

        elif self.state == 'homing':
            self._advance_idle_loop(dt)
            if self.target is not None:
                dx = self.target.x - self.x
                dy = self.target.y - self.y
                dist = math.hypot(dx, dy)
                if dist <= impact_range:
                    # Safety net only — in normal play trigger_impact() is
                    # called externally the instant enemy.py reports a
                    # real collision, before this ever fires. Covers the
                    # target dying/despawning mid-approach.
                    self.trigger_impact()
                elif dist > 1e-6:
                    # Quadratic ease-in from a standing start up to full
                    # ghost_speed over _HOMING_ACCEL_DURATION, rather than
                    # snapping straight to ghost_speed the instant homing
                    # starts. Squaring the ramp fraction (instead of a
                    # linear ramp) is what actually reads as
                    # "accelerating" — a linear ramp still looks close to
                    # constant-speed to the eye — and it also means most
                    # of the ramp's *distance* covered happens in its back
                    # half, right as the ghost is closing on the target,
                    # which is the "more pixel motion" (bigger per-frame
                    # step) that made the old flat-speed version read as
                    # gliding rather than diving. Left running past 1.0
                    # rather than clamped there — ramp_t is clamped to
                    # 1.0 below, capping current_speed at exactly
                    # ghost_speed once the ramp's done, same top speed as
                    # before, just no longer instant.
                    self._homing_elapsed += dt
                    ramp_t = min(self._homing_elapsed / _HOMING_ACCEL_DURATION, 1.0) \
                        if _HOMING_ACCEL_DURATION > 0 else 1.0
                    current_speed = ghost_speed * (ramp_t * ramp_t)
                    step = current_speed * dt
                    self.x += dx / dist * step
                    self.y += dy / dist * step

        elif self.state == 'impact':
            if self._advance_filmstrip(self.destruction_frames, dt, self.destruction_frame_duration):
                self.done = True

    def start_homing(self, target):
        self.state = 'homing'
        self.target = target
        self._homing_elapsed = 0.0

    def get_hover_offset(self):
        """Vertical-only bob for a settled 'idle' ghost, in world-space
        pixels (same units as self.x/self.y) — positive means "currently
        above" its resting position. Purely a draw-time offset: nothing
        here ever touches self.y itself, so collision rects, the sort
        key's avg_y, and _formation_target()'s target_y are all
        completely unaffected — see GhostKamikazeAttack.draw(), the only
        caller, which applies this to the sprite's on-screen position but
        deliberately NOT to the ground shadow's, so the shadow stays put
        on the ground while the sprite bobs above it. That's what
        actually reads as hovering rather than just "wobbling."

        A sine wave rather than any kind of ease-toward/eventually-settle
        curve, since this never needs to resolve to a fixed value — it's
        meant to breathe in place for as long as 'idle' lasts, which is
        an unknown, variable amount of time (whatever's left on
        GhostKamikazeAttack's hold_timer). _hover_phase offsets each
        ghost into its own point in the cycle; without it all three
        idling ghosts would bob perfectly in sync, which reads as one
        shared animation rather than three separate idling ghosts. The
        amplitude itself fades in over _HOVER_FADE_IN_DURATION rather
        than applying at full strength from the first 'idle' tick — see
        that constant's own comment for why: without it, entering
        'idle' with a phase that isn't near a zero-crossing pops the
        sprite instantly to wherever that phase sits on the curve
        instead of easing in from the exact position it was already
        at."""
        if self.state != 'idle':
            return 0.0
        amplitude = _HOVER_AMPLITUDE * min(self._hover_elapsed / _HOVER_FADE_IN_DURATION, 1.0)
        return amplitude * math.sin(
            2 * math.pi * self._hover_elapsed / _HOVER_PERIOD + self._hover_phase)

    def trigger_impact(self):
        if self.state in ('impact', 'done'):
            return
        self.state = 'impact'
        self.frame_index = 0
        self.frame_timer = 0.0

    def is_hittable(self):
        """Only a homing ghost can land/take a hit — a still-spawning,
        traveling-to-formation, idling, or already-impacted ghost
        shouldn't register collisions."""
        return self.state == 'homing'

    def get_collision_rect(self, scale=1):
        """World-space hitbox for enemy.py's 'ghost_kamikaze_attack'
        branch of check_collision_with_attack (see game.py's per-enemy
        loop, which passes this ghost itself as the `attack` object)."""
        w, h = self.size
        return pygame.Rect(self.x - w / 2, self.y - h / 2, w, h)

    def get_current_frame(self):
        if self.state == 'spawning':
            frames = self.creation_frames.get(self.direction) if self.creation_frames else None
            return frames[self.frame_index] if frames else None
        if self.state == 'impact':
            return self.destruction_frames[self.frame_index] if self.destruction_frames else None
        # 'traveling', 'idle', and 'homing' all play from the same idle
        # sheet — see _advance_idle_loop().
        frames = self.idle_frames.get(self.direction) if self.idle_frames else None
        if not frames:
            return None
        idx = min(self.frame_index, len(frames) - 1)
        return frames[idx]


class GhostKamikazeAttack:
    """Three ghost entities the player summons one at a time — one per
    completed loop of the player's cast animation, see
    Player.update_ghost_kamikaze_cast() / spawn_next_ghost() — then
    unleashes at a single locked-on enemy once the player's held pose
    finishes, or the instant the player moves (see launch_now()).

    Overall phase (self.phase):
      'creating'  — waiting on the player's cast animation to spawn all
                    3 ghosts. Each already-spawned ghost plays its own
                    creation animation, then travels to and idles at its
                    formation slot, independently of how many loops are
                    left. Ends normally via finish_creation() once all 3
                    have spawned, or early via cancel() the instant the
                    player moves (see Player.move()/can_move()).
      'holding'   — all 3 ghosts spawned, player is on its held pose,
                    hold_timer counting up to hold_duration. Ends via
                    _resolve(), either from the timer running out (see
                    update()) or from launch_now() the instant the
                    player moves — but only once every ghost has
                    actually reached 'idle'; see 'aborted' below for
                    what happens if the player moves before that.
      'attacking' — _resolve() found a target enemy; every ghost is
                    homing toward it (see _Ghost.update()).
      'no_target' — _resolve() found nothing to attack; every ghost
                    jumps straight to its destruction animation in place.
      'aborted'   — player moved too early (see cancel()/launch_now())
                    — the attack never gets a target search at all;
                    every ghost created so far is destroyed on the
                    spot instead, regardless of whether an enemy was
                    actually in the room. Reached either from
                    'creating' (player moved mid-cast, before the
                    formation even finished spawning — see cancel())
                    or from 'holding' (player moved before every
                    already-spawned ghost reached its formation slot —
                    see launch_now()).
      'done'      — every ghost has finished its destruction animation;
                    active goes False here and game.py drops this object.

    Needs the room's enemy list to pick a target, which (like Instant
    Transmission's targeting) this object doesn't have on its own — see
    Game._update_ghost_kamikaze, which calls update(dt, self.enemies)
    centrally every frame rather than this being ticked from inside
    Player.update().
    """

    def __init__(self, origin_x, origin_y, direction='down', scale=_RENDER_SCALE,
                 num_ghosts=3, ghost_frame_size=(16, 32),
                 hold_duration=1.5, ghost_speed=260, impact_range=18,
                 formation_side_offset=40, formation_side_forward_offset=8,
                 formation_middle_forward_offset=20, formation_speed=90,
                 idle_frame_duration=0.15, creation_frame_duration=0.1,
                 destruction_frame_count=4, destruction_frame_duration=0.06,
                 destruction_size=(32, 32)):
        # origin_x/origin_y is where the creation animation actually
        # plays — Player.start_ghost_kamikaze() passes its own
        # _get_spawn_offset() point (same "just in front of the player"
        # spot beam/blast spawn from), not the player's exact centre, per
        # the spec: ghosts appear in front of the player, then move out
        # to their left/right/middle formation slot afterward (see
        # spawn_next_ghost() / _Ghost's 'traveling' state).
        self.origin_x = origin_x
        self.origin_y = origin_y
        # Facing is permanent for the life of the attack, same convention
        # dragon_fist uses — whichever direction the player launched this
        # in is the direction every ghost's sprite faces for its entire
        # lifetime, homing included (see _Ghost's docstring). Also
        # determines which way the left/right/middle formation fans out —
        # see _DIRECTION_AXES / _formation_target().
        self.direction = direction if direction in _DIRECTION_AXES else 'down'
        self._forward_axis = _DIRECTION_AXES[self.direction]['forward']
        self._right_axis = _DIRECTION_AXES[self.direction]['right']
        self.scale = scale
        self.num_ghosts = num_ghosts
        # Actual per-frame pixel size of ghost_create.png/ghost_idle.png
        # (16x32) — also doubles as the collision hitbox size (see
        # _Ghost.get_collision_rect()) and, unlike destruction_size below,
        # does NOT need a frame count: get_all_frames() (core.sprite_system)
        # auto-detects columns from sheet_width // ghost_frame_size[0].
        self.ghost_size = ghost_frame_size

        # The spec says "1 or 2 seconds" for the hold (and, separately,
        # for the no-target stay-in-place wait) — both are driven by
        # this single timer (see _resolve()), split-the-difference
        # placeholder, easy to retune.
        self.hold_duration = hold_duration
        self.ghost_speed = ghost_speed
        self.impact_range = impact_range
        # Formation is a triangle/V, not a flat left-right line: left and
        # right each sit formation_side_offset out along the formation's
        # right axis AND formation_side_forward_offset further forward
        # than origin (so they land diagonally out to the front-left/
        # front-right); the middle ghost has no lateral offset at all but
        # sits formation_middle_forward_offset forward instead — bigger
        # than the sides' own forward push — so it ends up further out
        # than them, forming the tip of the triangle. See
        # _formation_target() for the actual math. All three are
        # placeholders, tune freely — formation_speed sets roughly how
        # fast the ghosts travel from origin_x/origin_y out to their slot
        # (see _Ghost's 'traveling' state / _ease_toward()): the motion
        # itself is now an eased tween rather than constant-velocity
        # stepping, so this is closer to an "average" speed over the
        # whole eased trip than a literal px/sec. Kept well below
        # ghost_speed (and defaulted low, 90) since forming up should
        # look unhurried and deliberate, not fast/aggressive like the
        # actual attack run.
        self.formation_side_offset = formation_side_offset
        self.formation_side_forward_offset = formation_side_forward_offset
        self.formation_middle_forward_offset = formation_middle_forward_offset
        self.formation_speed = formation_speed

        self.idle_frame_duration = idle_frame_duration
        self.creation_frame_duration = creation_frame_duration
        # Unlike ghost_create/ghost_idle, brown_destruction has no fixed
        # native frame width to key off of — same as dragon_fist's own
        # brown_destruction handling, it's a single row cut into
        # destruction_frame_count equal-width columns (frame width
        # derived, not fixed), so frame count has to be given explicitly.
        self.destruction_frame_count = destruction_frame_count
        self.destruction_frame_duration = destruction_frame_duration
        # Display size for destruction frames only — deliberately separate
        # from ghost_size (16x32): brown_destruction is a shared effect
        # reused across attacks, not sized to match any one attack's own
        # sprite, same as dragon_fist's own destruction_size=(32, 32).
        self.destruction_size = destruction_size

        (self.creation_frames, self.idle_frames,
         self.destruction_frames) = self._load_sprites()

        self.ghosts = []
        self.phase = 'creating'
        self.hold_timer = 0.0
        self.active = True
        # Read by LayerManager, if it checks this the same way other
        # y-sorted entities do — see get_sort_key()'s note. Unlike
        # dragon_fist (a single fixed-depth chain, y_sort=False), these
        # ghosts roam the room independently like enemies do, so they
        # should sort against them rather than sit at one fixed layer.
        self.y_sort = True

    # ------------------------------------------------------------------
    # Sprite loading
    # ------------------------------------------------------------------
    def _load_sprites(self):
        """ghost_create.png/ghost_idle.png are directional sheets — 4
        stacked rows (down/left/right/up, same order/convention as
        core.sprite_system's DIRECTIONS_4), each frame a fixed 16x32 (see
        ghost_size) with the frame COUNT auto-detected per row from the
        sheet's width. brown_destruction has no direction rows at all —
        same single-row-cut-into-N-columns handling dragon_fist already
        uses for it, just with its own destruction_size instead of
        ghost_size for display (see __init__). Falls back to a plain
        placeholder shape (see draw()) if any sheet is missing, same
        graceful-degradation convention as every other attack file."""
        creation_frames = self._load_directional_sheet(
            'assets/sprites/attacks/ghost_kamikaze_attack/ghost_create.png', 'ghost_kamikaze creation')
        idle_frames = self._load_directional_sheet(
            'assets/sprites/attacks/ghost_kamikaze_attack/ghost_idle.png', 'ghost_kamikaze idle')
        destruction_frames = self._load_destruction_sheet()
        return creation_frames, idle_frames, destruction_frames

    def _load_directional_sheet(self, filepath, label):
        """Returns {direction: [Surface, ...]} for a 4-row sheet at a
        fixed ghost_size per row, or None if the file's missing."""
        try:
            sheet = SpriteSheet(filepath)
            if sheet.sheet is None:
                raise FileNotFoundError(filepath)
            frame_w, frame_h = self.ghost_size
            return {
                direction: sheet.get_all_frames(frame_w, frame_h, direction_row=row)
                for row, direction in enumerate(DIRECTIONS_4)
            }
        except Exception as e:
            print(f"No {label} sprite loaded, using fallback: {e}")
            return None

    def _load_destruction_sheet(self):
        try:
            sheet = pygame.image.load('assets/objects/brown_destruction.png').convert_alpha()
        except Exception as e:
            print(f"No brown_destruction sprite loaded, using fallback: {e}")
            return None
        return self._slice_row(sheet, self.destruction_frame_count)

    @staticmethod
    def _slice_row(sheet, frame_count):
        w, h = sheet.get_width(), sheet.get_height()
        frame_w = w // frame_count
        return [
            sheet.subsurface(pygame.Rect(i * frame_w, 0, frame_w, h)).copy()
            for i in range(frame_count)
        ]

    # ------------------------------------------------------------------
    # Creation — called from Player.update_ghost_kamikaze_cast(), once
    # per completed cast-animation loop
    # ------------------------------------------------------------------
    def _formation_target(self, index):
        """World position of formation slot `index` (0=left, 1=right,
        2=middle — the spec's spawn ordering) — a triangle/V, not a flat
        left-right line: left and right sit formation_side_offset out
        along the attack's own formation-right axis AND
        formation_side_forward_offset forward of origin_x/origin_y (see
        _DIRECTION_AXES for what "forward"/"right" mean per facing),
        landing them diagonally out front-left/front-right of the
        player. Middle has no lateral offset at all, but sits
        formation_middle_forward_offset straight forward instead — set
        larger than the sides' own forward push — so it ends up further
        out than either of them, forming the tip of the triangle facing
        away from the player. Both axes are used together for left/right
        (rather than reusing whichever raw offset happens to look right
        for down/up) precisely because _DIRECTION_AXES' rotated
        forward/right vectors are what make this generalize correctly to
        every facing in the first place — see that dict's own comment
        about flipping signs if a given direction still comes out
        mirrored from what looks right in-game.
        """
        fx, fy = self._forward_axis
        rx, ry = self._right_axis
        if index == 0:      # left: out + slightly forward
            side_sign = -1
        elif index == 1:    # right: out + slightly forward
            side_sign = 1
        else:               # middle: no lateral offset, pushed further forward than the sides
            return (self.origin_x + fx * self.formation_middle_forward_offset,
                    self.origin_y + fy * self.formation_middle_forward_offset)
        return (self.origin_x + rx * self.formation_side_offset * side_sign
                              + fx * self.formation_side_forward_offset,
                self.origin_y + ry * self.formation_side_offset * side_sign
                              + fy * self.formation_side_forward_offset)

    def spawn_next_ghost(self):
        """Spawn the next ghost in sequence (left, right, then middle),
        appearing right in front of the player (origin_x/origin_y) and
        immediately playing its creation animation there, looping until
        finish_current_ghost_spawn() ends it (see _Ghost's 'spawning'
        state / begin_travel()) and it starts moving out to its actual
        left/right/middle formation slot (see _formation_target()) via
        the 'traveling' state.

        Won't spawn a new ghost while the most recently spawned one is
        still 'spawning' — i.e. still playing its creation animation
        parked at the shared origin point — since two ghosts doing that
        in the same spot at once would visually stack on top of each
        other. It's fine, though, for the next ghost to spawn while the
        previous one is already 'traveling' out to its formation slot:
        that's what gives the attack its overlapping, staggered look —
        ghost 2 fanning out while ghost 1 is still midway to its spot,
        ghost 3 following while ghost 2 is nearly there — rather than
        each ghost waiting for the last one to fully settle before it
        even appears. Player.update_ghost_kamikaze_cast() calls this
        every frame once the cast animation is past its per-loop
        spawn-frame threshold, so it naturally keeps retrying until the
        previous ghost clears the origin rather than needing separate
        once-per-loop bookkeeping — this also means a ghost can end up
        spawning later than its "own" loop if the previous one is slow
        to clear 'spawning', rather than being skipped.

        Returns True if a ghost was actually spawned, False if blocked
        (previous ghost still in its creation animation, all num_ghosts
        already out, or past the 'creating' phase) — mostly useful for
        debugging/logging, not required by callers.
        """
        if self.phase != 'creating' or len(self.ghosts) >= self.num_ghosts:
            return False
        if self.ghosts and self.ghosts[-1].state == 'spawning':
            return False
        idx = len(self.ghosts)
        target_x, target_y = self._formation_target(idx)
        self.ghosts.append(_Ghost(
            self.origin_x, self.origin_y, target_x, target_y, self.direction,
            self.creation_frames, self.idle_frames, self.destruction_frames,
            self.ghost_size, self.creation_frame_duration, self.idle_frame_duration,
            self.destruction_frame_duration,
        ))
        return True

    def finish_current_ghost_spawn(self):
        """Called by Player.update_ghost_kamikaze_cast() the instant the
        cast-animation loop that spawned the most recent ghost finishes
        its own loop — ends that ghost's creation animation and lets it
        start moving out to its formation slot on the idle sprite (see
        _Ghost.begin_travel()). Checks every ghost rather than just the
        latest one; under the normal one-ghost-per-loop cadence only one
        is ever still 'spawning' at a time, but this stays correct even
        if that assumption changes, and begin_travel() itself is a no-op
        for any ghost that's already past 'spawning'."""
        for ghost in self.ghosts:
            ghost.begin_travel()

    def finish_creation(self):
        """Called by Player once the 3rd loop's ghost is spawned and it
        switches to its held pose — starts the hold-timer phase."""
        if self.phase == 'creating':
            self.phase = 'holding'
            self.hold_timer = 0.0

    # ------------------------------------------------------------------
    # Resolution — decide attack vs. no-target, from either the hold
    # timer running out (see update()) or the player moving early
    # ------------------------------------------------------------------
    def cancel(self):
        """Player moved during 'creating' — cancels the attack outright,
        before it ever even gets to the hold. Called by
        Player.move()/can_move() the instant the player moves while
        is_casting_ghost_kamikaze is True, mirroring launch_now()'s
        'aborted' path for the later, mid-hold case: every ghost created
        so far — whatever state it's individually in, still on its own
        creation animation, or already off traveling toward its
        formation slot if an earlier loop's ghost got a head start — is
        sent straight to its destruction animation via trigger_impact().
        No target search ever happens here, same reasoning as
        launch_now()'s 'aborted' path: the formation never finished
        forming up, so there's nothing to actually launch.

        No-op outside 'creating' — Player only ever calls this while
        is_casting_ghost_kamikaze is True, which implies phase is still
        'creating' by construction (finish_creation() is what flips it
        to 'holding', and that only happens once casting ends), but
        this stays correct even if that assumption ever changes.

        If the player moves before even the first ghost has spawned
        (early in the first loop, before ghost_kamikaze_spawn_frame_index
        is reached), self.ghosts is empty — nothing to destroy and
        nothing left to animate, so this finishes immediately rather
        than sitting in 'aborted' waiting on ghosts that don't exist;
        update()'s done-check requires self.ghosts to be non-empty, same
        as every other terminal phase."""
        if self.phase != 'creating':
            return
        if not self.ghosts:
            self.phase = 'done'
            self.active = False
            return
        self.phase = 'aborted'
        for ghost in self.ghosts:
            ghost.trigger_impact()

    def launch_now(self):
        """Player moved during the hold — cut the wait short.

        If every ghost has already reached its formation slot ('idle'),
        this is exactly a natural timeout: same decision logic as
        _resolve() (target search, then homing or destruction).
        Game._update_ghost_kamikaze passes the current enemy list into
        the very next update() call regardless of what triggered the
        resolve, so this just flips the phase to '_resolving' rather
        than needing the enemy list itself.

        If even one ghost hasn't reached 'idle' yet — still 'traveling'
        to its slot, or (rarer, but possible right at the start of the
        hold) still 'spawning' — the attack never gets to happen at
        all, no matter whether a valid target exists in the room: every
        ghost is destroyed on the spot instead, via trigger_impact()
        wherever it currently is. That's the actual rule (moving too
        early wastes the attack), not just a visual nicety — resolving
        normally in that case would let a ghost that's still mid-flight
        toward its slot snap straight into 'homing' (or 'impact', if no
        target) from wherever it happened to be, instead of ever
        finishing the formation it was still mid-way through."""
        if self.phase != 'holding':
            return
        if not all(ghost.state == 'idle' for ghost in self.ghosts):
            self.phase = 'aborted'
            for ghost in self.ghosts:
                ghost.trigger_impact()
            return
        self.phase = '_resolving'

    def _resolve(self, enemies):
        target = self._pick_target(enemies)
        if target is not None:
            self.phase = 'attacking'
            for ghost in self.ghosts:
                ghost.start_homing(target)
        else:
            self.phase = 'no_target'
            for ghost in self.ghosts:
                ghost.trigger_impact()

    def _pick_target(self, enemies):
        """Nearest enemy to the spawn point, if any. The spec just says
        "an enemy in the room" without specifying which one when there's
        more than one — nearest-to-where-the-ghosts-are is the natural
        default."""
        alive = [e for e in (enemies or []) if getattr(e, 'health', 1) > 0]
        if not alive:
            return None
        return min(alive, key=lambda e: math.hypot(e.x - self.origin_x, e.y - self.origin_y))

    # ------------------------------------------------------------------
    # Update / collision
    # ------------------------------------------------------------------
    def update(self, dt, enemies=None):
        """Called centrally from Game every frame (see class docstring)
        with the room's current enemy list, needed only at the exact
        moment 'holding' resolves."""
        if not self.active:
            return

        if self.phase == 'holding':
            self.hold_timer += dt
            if self.hold_timer >= self.hold_duration:
                self._resolve(enemies)
        elif self.phase == '_resolving':
            # Set by launch_now() the frame the player moved — resolved
            # here (rather than inside launch_now() itself) so it always
            # sees the freshest enemy list Game has for this frame.
            self._resolve(enemies)

        for ghost in self.ghosts:
            ghost.update(dt, self.ghost_speed, self.formation_speed, self.impact_range)

        if self.phase in ('attacking', 'no_target', 'aborted') and self.ghosts and all(g.done for g in self.ghosts):
            self.phase = 'done'
            self.active = False

    def get_homing_ghosts(self):
        """Ghosts currently hittable — see enemy.py's
        'ghost_kamikaze_attack' collision branch and game.py's per-enemy
        collision loop, which iterates this each frame."""
        return [g for g in self.ghosts if g.is_hittable()]

    def get_world_bounds(self):
        """World-space pygame.Rect enclosing every active ghost's current
        position — used by LayerManager._apply_decoration_occlusion
        (draw_layers.py) so a decoration in front of the swarm can still
        redraw on top of it, without touching this attack's own
        get_sort_key()/draw_layer (see that method's docstring for why
        down/left/right deliberately use a flat DrawLayer.EFFECTS_FRONT
        instead of y-sorting against the player/enemies).

        Returns None if there are no ghosts yet (e.g. still in the
        'creating' phase before the first one spawns) — same
        "not enough geometry, skip occlusion this frame" contract
        _get_occlusion_rect already expects from any get_world_bounds().
        """
        active_ghosts = [g for g in self.ghosts if getattr(g, 'state', None) != 'done']
        if not active_ghosts:
            return None
        bounds = active_ghosts[0].get_collision_rect()
        for ghost in active_ghosts[1:]:
            bounds = bounds.union(ghost.get_collision_rect())
        return bounds

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def get_sort_key(self):
        """(layer, y) — LayerManager.draw_all sorts every
        drawable_object by get_sort_key() in one pass (see
        draw_layers.py), so ALL objects in that list need a comparable
        key of the same shape; a bare number here (an earlier version of
        this returned just avg_y) breaks the instant it's compared
        against another object's (layer, y) tuple — 'int' vs 'tuple'
        isn't orderable.

        down/left/right vs up are handled differently on purpose, same
        split get_beam_layer() already uses for beams (see
        draw_layers.py):

        down/left/right — ghosts spawn out in front of the player
        (closer to the camera) and should always render on top of them.
        An earlier version of this compared the ghosts' Y position
        against the player's feet key (self.y + self.height // 2, see
        Player.get_sort_key() in player.py) and relied on the 'down'
        spawn offset (see _GHOST_KAMIKAZE_SPAWN_OFFSETS above) pushing
        origin_y far enough below the player for that comparison to
        come out in front. That's fragile — retuning the offset (e.g.
        toward 0 or negative, to spawn closer to/above the player's
        centre) flips the comparison and draws the ghosts behind again,
        with no code change to explain why. A flat layer above
        DrawLayer.PLAYER (DrawLayer.EFFECTS_FRONT) sidesteps the
        Y-comparison entirely: these ghosts always draw in front of the
        player no matter what the spawn offset is tuned to.

        up — the ghosts travel away from the camera, behind the
        player's own back, so they should NOT sit in front the way the
        other directions do. But they should still land in front of an
        enemy positioned further up the screen — a flat "always behind"
        layer can't do both at once. Instead, 'up' shares
        DrawLayer.PLAYER, the same Y-sorted bucket the player and
        enemies use, compared on the same feet-position basis as
        Player.get_sort_key() (self.y + self.height // 2) rather than
        raw centre y, so it's an apples-to-apples comparison — see
        get_beam_layer()'s own 'up' case for the identical reasoning.

        DrawLayer.PLAYER is used as a placeholder second layer (rather
        than a dedicated ghost/enemy layer) purely because it's the one
        member confirmed in use elsewhere (Player.draw_layer) — if
        draw_layers.py has a more fitting shared "normal entity" layer,
        swap it in here instead.
        """
        avg_y = (self.origin_y if not self.ghosts
                 else sum(g.y for g in self.ghosts) / len(self.ghosts))

        if self.direction != 'up':
            return (DrawLayer.EFFECTS_FRONT, avg_y)

        feet_offset = self.ghost_size[1] // 2
        return (DrawLayer.PLAYER, avg_y + feet_offset)

    def draw(self, screen, camera, colors=None):
        if not self.active:
            return
        ghost_w, ghost_h = self.ghost_size
        ghost_scaled = (int(ghost_w * self.scale), int(ghost_h * self.scale))
        dest_w, dest_h = self.destruction_size
        dest_scaled = (int(dest_w * self.scale), int(dest_h * self.scale))
        # All ghosts share one spawn point (origin_x/origin_y), and by
        # design a new ghost can start its creation animation there while
        # an earlier one is still easing away from that same spot (see
        # spawn_next_ghost()'s docstring on the intentional staggered
        # overlap, and _ease_toward()'s smoothstep, which starts an eased
        # ghost at zero velocity — so it lingers near the origin right as
        # it switches to its idle sprite). Drawing in plain self.ghosts
        # (spawn) order meant a just-spawned ghost, still on its creation
        # sprite, would render on top of an already-transitioned ghost at
        # that same point — visually reading as the idle sprite flashing
        # back to a creation frame. Drawing every still-'spawning' ghost
        # first (underneath) fixes that: an already-idle/traveling ghost's
        # sprite can never be covered by a newer ghost's creation sprite.
        draw_order = sorted(self.ghosts, key=lambda g: 0 if g.state == 'spawning' else 1)
        for ghost in draw_order:
            frame = ghost.get_current_frame()
            scaled_size = dest_scaled if ghost.state == 'impact' else ghost_scaled
            screen_x = ghost.x * self.scale - camera.x
            screen_y = ghost.y * self.scale - camera.y

            # Ground shadow, same convention as Player/Enemy/NPC (see
            # LayerManager._draw_shadow in draw_layers.py) — skipped
            # during 'impact' since brown_destruction is a ground-level
            # burst, not a standing entity.
            if ghost.state != 'impact':
                shadow_surf = _get_scaled_ghost_shadow(_PLAYER_SHADOW_REFERENCE_WIDTH * self.scale)
                shadow_x = int(screen_x - shadow_surf.get_width() // 2)
                shadow_y = int(screen_y + (ghost_h * self.scale) // 2.25 - shadow_surf.get_height() // 2)
                screen.blit(shadow_surf, (shadow_x, shadow_y))

            if frame:
                surf = pygame.transform.scale(frame, scaled_size)
            else:
                surf = pygame.Surface(scaled_size, pygame.SRCALPHA)
                pygame.draw.ellipse(surf, (190, 190, 230, 220), (0, 0, *scaled_size))
            # Hover bob (idle ghosts only — see get_hover_offset()) is
            # applied here, to the sprite's own screen position, and
            # nowhere above where shadow_x/shadow_y were computed — the
            # shadow stays anchored to screen_y (the ghost's real,
            # unbobbed ground position) so the sprite visibly rises and
            # falls above a shadow that isn't moving with it.
            hover_offset = ghost.get_hover_offset() * self.scale
            rect = surf.get_rect(center=(int(screen_x), int(screen_y - hover_offset)))
            screen.blit(surf, rect)