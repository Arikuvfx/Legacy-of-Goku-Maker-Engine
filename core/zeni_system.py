"""
Zeni drop system.

Kept as a single self-contained module (same idea as core/sprite_system.py —
one file owns everything about one concern) rather than spread across
Enemy/BossEnemy: the denominations and pool tables live here, and enemies
just hold a `zeni_pool` string key and call roll_zeni_drop() on death. Add a
new denomination or rebalance a pool by editing the tables below — nothing
else needs to change.

Usage (see entities/enemy.py's get_zeni_drop() for the real call site)::

    from core.zeni_system import roll_zeni_drop, drop_value

    drop = roll_zeni_drop(self.zeni_pool)   # {'small_silver_zeni': 3, ...}
    value = drop_value(drop)                # 3
"""

import math
import os
import random

import pygame

from config.settings import RENDER_SCALE
from core.draw_layers import DrawLayer
from core.sprite_system import SpriteSheet


# ---------------------------------------------------------------------------
# Denominations — the zeni VALUE of a single piece of each type. Used by
# drop_value() to total up a roll; also the natural place to hang a sprite/
# icon key later if drops ever get drawn as world pickups instead of being
# credited straight to the player.
# ---------------------------------------------------------------------------
ZENI_VALUES = {
    'small_silver_zeni': 1,
    'small_gold_zeni':   5,
    'big_silver_zeni':   25,
    'big_gold_zeni':     100,
}

# ---------------------------------------------------------------------------
# Drop pools — what an enemy's `zeni_pool` rolls against when it dies.
#
# Each denomination entry is {'min', 'max', 'chance'}:
#   - `chance` is the odds that this denomination drops AT ALL this kill.
#   - if it does, the amount is a random int between `min` and `max`
#     inclusive (so "up to 6" means the roll can also come up short, or
#     even land on 0 only if min is 0 — set min >= 1 if a denomination
#     should never drop a zero count once it clears the chance roll).
#
# Tiers are cumulative in flavour (each one adds a new, higher-value
# denomination on top of what the previous tier could already drop) but are
# independent tables — nothing stops a designer from making tier2 NOT drop
# small_silver_zeni at all, for instance.
# ---------------------------------------------------------------------------
ZENI_POOLS = {
    # Tier 1 — weakest fodder enemies. Small silver only, up to 6 pieces.
    'tier1': {
        'small_silver_zeni': {'min': 1, 'max': 6, 'chance': 0.85},
    },
    # Tier 2 — more small silver than tier 1, plus a chance at small gold.
    'tier2': {
        'small_silver_zeni': {'min': 2, 'max': 10, 'chance': 0.9},
        'small_gold_zeni':   {'min': 1, 'max': 3,  'chance': 0.35},
    },
    # Tier 3 — introduces big silver zeni.
    'tier3': {
        'small_silver_zeni': {'min': 2, 'max': 8, 'chance': 0.85},
        'small_gold_zeni':   {'min': 1, 'max': 4, 'chance': 0.6},
        'big_silver_zeni':   {'min': 1, 'max': 2, 'chance': 0.4},
    },
    # Tier 4 — top tier (elites / bosses). Introduces big gold zeni.
    'tier4': {
        'small_gold_zeni': {'min': 1, 'max': 5, 'chance': 0.7},
        'big_silver_zeni': {'min': 1, 'max': 3, 'chance': 0.6},
        'big_gold_zeni':   {'min': 1, 'max': 2, 'chance': 0.45},
    },
}

DEFAULT_POOL = 'tier1'

# ---------------------------------------------------------------------------
# World-pickup sprites — one flat image per denomination, matching the
# character-creator's `{key}.png` naming convention. Folder is fixed; add a
# new denomination by dropping `assets/sprites/items/{new_key}.png` in and
# it just works (falls back to a magenta placeholder box if missing so a
# missing sprite never crashes a death).
# ---------------------------------------------------------------------------
ZENI_ICON_DIR = os.path.join('assets', 'sprites', 'items')
SHADOW_Y_OFFSET = 4   # default — tuned for the small (6x8) denominations

# Big denominations (14x18) are nearly 3x the small sprite's height, so the
# default offset isn't enough to push the shadow out from under the now much
# taller, opaque coin once it's settled (z-offset 0) — it's still there,
# just fully covered. Bump it here rather than raising SHADOW_Y_OFFSET
# itself, which would overshoot the small denominations.
ZENI_SHADOW_Y_OFFSETS = {
    'big_silver_zeni': 28,
    'big_gold_zeni':   28,
}

def zeni_shadow_y_offset(zeni_type):
    return ZENI_SHADOW_Y_OFFSETS.get(zeni_type, SHADOW_Y_OFFSET)

def zeni_icon_path(zeni_type):
    return os.path.join(ZENI_ICON_DIR, f'{zeni_type}.png')


# Ground shadow shown under a pickup while it's airborne — lives in the
# universal sprites folder since it's shared art, not per-denomination.
# Small denominations get their own dedicated 'shadowsmall'; the big ones
# share the universal 'shadow' image instead.
SHADOW_ICON_DIR = os.path.join('assets', 'sprites', 'universal')

ZENI_SHADOWS = {
    'small_silver_zeni': 'shadowsmall',
    'small_gold_zeni':   'shadowsmall',
    'big_silver_zeni':   'shadow',
    'big_gold_zeni':     'shadow',
}


def zeni_shadow_path(zeni_type):
    shadow_name = ZENI_SHADOWS.get(zeni_type)
    if shadow_name is None:
        return None
    return os.path.join(SHADOW_ICON_DIR, f'{shadow_name}.png')


# Native frame size per denomination — small_* sprites are 6x8, big_* are
# 14x18. Falls back to the small size for any unrecognised key.
ZENI_FRAME_SIZES = {
    'small_silver_zeni': (6, 8),
    'small_gold_zeni':   (6, 8),
    'big_silver_zeni':   (14, 18),
    'big_gold_zeni':     (14, 18),
}


def pool_keys():
    """Ordered list of valid pool keys — the entity editor's Zeni Pool
    selector reads this directly so it never drifts out of sync with the
    tables above."""
    return ['tier1', 'tier2', 'tier3', 'tier4']


def roll_zeni_drop(pool_key):
    """Roll a death drop for *pool_key*.

    Returns a {denomination: count} dict containing only the denominations
    that actually dropped (count > 0) — an empty dict is a valid, if
    unlucky, result. Falls back to DEFAULT_POOL for any unrecognised key
    so a bad/missing enemy config never crashes a death.
    """
    pool = ZENI_POOLS.get(pool_key, ZENI_POOLS[DEFAULT_POOL])

    drop = {}
    for zeni_type, rule in pool.items():
        if random.random() > rule['chance']:
            continue
        count = random.randint(rule['min'], rule['max'])
        if count > 0:
            drop[zeni_type] = count
    return drop


def drop_value(drop):
    """Total zeni value of a {denomination: count} drop dict (as returned
    by roll_zeni_drop)."""
    return sum(ZENI_VALUES.get(zeni_type, 0) * count for zeni_type, count in drop.items())


# ---------------------------------------------------------------------------
# World pickups — spawned from an enemy's death instead of crediting zeni
# straight to the player. One ZeniPickup per individual coin (see
# spawn_zeni_pickups) so a drop of e.g. 6 small_silver_zeni actually shows
# and scatters as 6 separate flying coins, each worth ZENI_VALUES[type].
# ---------------------------------------------------------------------------

_COLLECT_RADIUS = 12.0   # px — distance at which the player touching a settled pickup collects it
_COLLECT_RADIUS_SQ = _COLLECT_RADIUS * _COLLECT_RADIUS  # avoids a sqrt (math.hypot) every settled coin, every frame
_LIFETIME      = 8.0    # seconds before an uncollected pickup despawns

# In the last _BLINK_WARN_DURATION seconds of its lifetime, an uncollected
# pickup blinks to warn the player it's about to disappear: opacity eases
# 100 -> 0 -> 100 -> 0 -> ... on a smooth cosine wave (see _render_alpha_for_age
# in ZeniPickup.update()), repeating every _BLINK_PERIOD seconds, right up
# until it despawns at _LIFETIME.
_BLINK_WARN_DURATION = 2.0   # seconds before despawn that blinking starts
_BLINK_PERIOD         = 0.1   # seconds for one full 100 -> 0 -> 100 cycle

# Hoisted out of update() — this is a constant (doesn't depend on self), but
# was being recomputed from scratch on every single pickup, every frame.
# Cheap on its own, but it's one of several small per-call costs that add up
# once a pile gets into the thousands (see age_only() below for the bigger
# saving).
_BLINK_START = max(0.0, _LIFETIME - _BLINK_WARN_DURATION)

# Every coin gets the same single-hop landing: one long straight arc that
# matches the hit direction (only a little perpendicular jitter — see
# _PERP_SCALE), settling the instant it touches down. No second hop, no
# "pop" — that profile has been removed.
#
# The hop's distance is randomized per coin (see _HOP_DISTANCE_MIN/MAX
# below), and fall_ease is DERIVED from that distance rather than fixed: a
# coin that rolls a long distance gets a low fall_ease (a smoother, more
# gradual drop), while a coin that rolls a short distance gets a high
# fall_ease (it lingers near the peak, then stops/settles more sharply —
# reads as "didn't travel very far"). See _fall_ease_for_distance().
#
# duration/peak_fraction stay fixed across coins — distance drives
# fall_ease (below), peak_x_fraction (further below), AND arc height (also
# further below) — a coin that travels farther gets a taller arc to match,
# the same way a real toss thrown harder both goes farther and arcs higher.
_HOP_DURATION      = 1.0
_HOP_PEAK_FRACTION = 0.75   # overrides _ARC_PEAK_FRACTION below — gives the
                            # fall a bigger, slower-feeling share of real time

_HOP_DISTANCE_MIN = 1   # px — shortest possible coin travel
_HOP_DISTANCE_MAX = 90   # px — longest possible coin travel

# fall_ease at the two ends of the distance range above. The rise always
# eases into the peak at zero vertical speed (see the sin curve in
# ZeniPickup.update()), so the fall MUST start at zero speed too or there's
# a visible snap right at the peak — that requires fall_ease > 1 (at
# exactly 1.0 the fall starts at a constant, non-zero rate of change,
# which is the jerk/stutter that was showing up on far-flying coins).
# 2.0 is the natural floor: a plain quadratic ease-in, which is exactly the
# shape of gravity accelerating something from rest — smooth, with no
# lingering pause. Values above 2 progressively flatten the curve near the
# peak instead, which is what creates the deliberate "hang, then drop"
# halt — reserved for coins that land close by. See _fall_ease_for_distance().
_FALL_EASE_NEAR = 6.0   # applied at _HOP_DISTANCE_MIN — the deliberate halt/settle feel
_FALL_EASE_FAR  = 2.0   # applied at _HOP_DISTANCE_MAX — smooth gravity-like fall, no halt


def _lerp_by_distance(distance, near_value, far_value):
    """Shared helper: linearly interpolate between *near_value* (at
    _HOP_DISTANCE_MIN) and *far_value* (at _HOP_DISTANCE_MAX) based on
    *distance*, clamped to that range. Used for both fall_ease and
    peak_x_fraction below so a coin's whole arc — not just its vertical
    fall — scales consistently with how far it actually travels."""
    span = _HOP_DISTANCE_MAX - _HOP_DISTANCE_MIN
    t = 0.0 if span <= 0 else (distance - _HOP_DISTANCE_MIN) / span
    t = max(0.0, min(1.0, t))
    return near_value + (far_value - near_value) * t


def _fall_ease_for_distance(distance):
    """Map a coin's ACTUAL travel distance (hop_distance * distance_scale —
    see ZeniPickup.__init__) to a fall_ease value: shorter distances
    (nearer) get a higher fall_ease (lingers near the peak, then stops
    more abruptly); longer distances (farther) get the floor value of 2 (a
    smooth, gravity-like fall with no lingering pause). Linear interpolation
    between _FALL_EASE_NEAR and _FALL_EASE_FAR across
    [_HOP_DISTANCE_MIN, _HOP_DISTANCE_MAX], clamped to that range — so any
    actual distance >= _HOP_DISTANCE_MAX (reachable via a high distance_scale
    even without hop_distance itself hitting the max) already gets the fully
    smooth fall_ease of 2. Don't set _FALL_EASE_FAR below 2 — anything less
    reintroduces a velocity snap right at the peak (see the comment above
    _FALL_EASE_NEAR/_FAR)."""
    return _lerp_by_distance(distance, _FALL_EASE_NEAR, _FALL_EASE_FAR)

# Arc height (peak height off the ground) at the two ends of the distance
# range above — mirrors fall_ease's near/far split via the same
# _lerp_by_distance helper. Computed from the coin's ACTUAL travel distance
# (hop_distance * distance_scale) in __init__, same as fall_ease, so a coin
# that only looks far because of a high distance_scale roll (not just a
# high base hop_distance) still gets the taller arc it should — a real
# toss thrown harder both goes farther AND arcs higher.
# NOTE: because this already bakes distance_scale into the height, update()
# must NOT multiply hop['height'] by self.distance_scale again — that
# would double-apply it (see the _hop_z line in ZeniPickup.update()).
_HOP_HEIGHT_NEAR = 9    # px — applied at _HOP_DISTANCE_MIN — short, low hop
_HOP_HEIGHT_FAR  = 13   # px — applied at _HOP_DISTANCE_MAX — long, high sweeping arc


def _hop_height_for_distance(distance):
    """Map a coin's ACTUAL travel distance to an arc height: farther
    coins arc higher, nearer coins arc lower — see the comment above
    _HOP_HEIGHT_NEAR/_FAR."""
    return _lerp_by_distance(distance, _HOP_HEIGHT_NEAR, _HOP_HEIGHT_FAR)

# Shape of each hop's arc, matched to how it actually looks in-game. See
# ZeniPickup.update() for how these are used.
#   _ARC_PEAK_FRACTION   - default fraction of the hop's duration spent
#                          rising (the rest is the drop). Override per-hop
#                          with a 'peak_fraction' key.
#   _ARC_PEAK_X_FRACTION - default fraction of the hop's horizontal distance
#                          covered by the time it peaks. Override per-hop
#                          with a 'peak_x_fraction' key — see
#                          _PEAK_X_FRACTION_NEAR/_FAR below.
_ARC_PEAK_FRACTION   = 0.18
_ARC_PEAK_X_FRACTION = 0.88

# peak_x_fraction at the two ends of the distance range (mirrors fall_ease's
# near/far split above, using the same _lerp_by_distance helper). This is
# what actually caused the lingering "halt" on far coins even after
# fall_ease was fixed: whenever peak_x_fraction != peak_fraction, the
# horizontal speed itself changes at the peak (average speed before the
# peak is peak_x_fraction/peak_fraction; after, it's
# (1-peak_x_fraction)/(1-peak_fraction) — with the values below that's a
# ~59% horizontal slowdown right at the top, on EVERY coin, regardless of
# fall_ease). Far coins get peak_x_fraction == _HOP_PEAK_FRACTION exactly,
# which makes those two average speeds equal — constant horizontal speed
# through the whole hop, no slowdown at the peak. Near coins keep the
# lopsided 0.88, which is what "creeps forward a little during the drop"
# — part of the deliberate halt/settle feel, now correctly confined to
# coins that don't travel far.
_PEAK_X_FRACTION_NEAR = 0.88               # applied at _HOP_DISTANCE_MIN — deliberate creep-to-a-stop
_PEAK_X_FRACTION_FAR  = _HOP_PEAK_FRACTION  # applied at _HOP_DISTANCE_MAX — constant speed, no slowdown


def _peak_x_fraction_for_distance(distance):
    """Map a coin's ACTUAL travel distance to a peak_x_fraction value —
    see the comment above _PEAK_X_FRACTION_NEAR/_FAR for why this (not
    fall_ease) is what actually removes the halt on far-flying coins."""
    return _lerp_by_distance(distance, _PEAK_X_FRACTION_NEAR, _PEAK_X_FRACTION_FAR)

# Multiple coins from the same kill (see spawn_zeni_pickups) must never land
# on top of each other or travel the exact same distance — these control
# how spread out they are:
#   _MIN_COIN_SPACING - minimum perpendicular-to-direction spacing (px)
#                       between coins spawned from the same drop. The
#                       total spread grows with coin count so a 10-coin
#                       drop doesn't cram into the same width as a 2-coin
#                       one.
#   _DISTANCE_VARIANCE - each coin's hop distance (and height) is scaled by
#                        a random factor in [1 - this, 1 + this], so even
#                        two coins side by side don't travel identically.
#   _PERP_SCALE        - fraction of the usual perpendicular spacing each
#                        coin actually gets (not zero) — enough to keep
#                        coins from stacking, while still reading as
#                        "thrown along the hit direction" rather than
#                        fanning out wide.
_MIN_COIN_SPACING  = 9
_DISTANCE_VARIANCE = 0.6
_PERP_SCALE        = 0.35

# Native frame size for the item sprite sheets — horizontal strip, frame
# count auto-detected from sheet width so adding/removing frames from an
# image needs no code change.
_ANIM_FPS = 4

# A settled/flying coin's sprite sits on frame 0 (idle) for this long, then
# plays through the rest of its strip once at _ANIM_FPS, then goes back to
# idling on frame 0 — idle, play, idle, play, ... rather than looping
# continuously. See ZeniPickup._anim_frame_index().
_ANIM_IDLE_DURATION = 3.0


class ZeniPickup:
    """A single dropped-zeni stack sitting in the world.

    Spawns with a single long straight hop away from the death position —
    distance randomized per coin (see _HOP_DISTANCE_MIN/MAX), with
    fall_ease derived from that distance so farther coins fall smoothly
    and nearer coins linger then stop more abruptly (see
    _fall_ease_for_distance()) — then settles in place and waits for the
    player to walk into it. See spawn_zeni_pickups() for the usual entry
    point.
    """

    _sprite_cache = {}
    _shadow_cache = {}
    _composite_cache = {}

    def __init__(self, x, y, zeni_type, count, direction=(1.0, 0.0),
                 perp_offset=None, distance_scale=None):
        self.zeni_type = zeni_type
        self.count     = count
        self.value     = ZENI_VALUES.get(zeni_type, 0) * count

        self.x = x
        self.y = y

        # Per-coin distance/height scale — random by default (see
        # _DISTANCE_VARIANCE) so no two coins, even side by side, travel
        # the exact same distance. spawn_zeni_pickups always passes this
        # explicitly; the random fallback only matters if ZeniPickup is
        # ever constructed directly. Rolled BEFORE fall_ease below, because
        # fall_ease needs to key off how far the coin actually ends up
        # travelling, not the pre-scale base distance.
        if distance_scale is None:
            distance_scale = random.uniform(1.0 - _DISTANCE_VARIANCE, 1.0 + _DISTANCE_VARIANCE)
        self.distance_scale = distance_scale

        # Distance is randomized per coin, and fall_ease, peak_x_fraction,
        # AND arc height are all derived from the coin's ACTUAL travelled
        # distance (hop_distance * distance_scale — the same product used
        # to move it in update()) rather than the pre-scale hop_distance
        # alone. Otherwise a coin that rolls a modest hop_distance but a
        # high distance_scale can visually travel the farthest of the
        # bunch while still being keyed to haltier, flatter values, which
        # is exactly backwards. Every coin gets exactly one hop (no more
        # 'pop' double-hop).
        hop_distance = random.uniform(_HOP_DISTANCE_MIN, _HOP_DISTANCE_MAX)
        actual_distance = hop_distance * distance_scale
        self._hops = (
            {'duration': _HOP_DURATION, 'distance': hop_distance,
             'height': _hop_height_for_distance(actual_distance),
             'peak_fraction': _HOP_PEAK_FRACTION,
             'fall_ease': _fall_ease_for_distance(actual_distance),
             'peak_x_fraction': _peak_x_fraction_for_distance(actual_distance)},
        )

        # Perpendicular-to-direction offset applied to the start position —
        # this is what guarantees coins from the same kill never land on
        # top of each other (see spawn_zeni_pickups' stratified layout).
        # Scaled down by _PERP_SCALE so coins still mostly track the hit
        # direction rather than fanning out wide, while never landing
        # exactly on another coin's line either.
        dir_x, dir_y = direction
        if perp_offset is None:
            perp_offset = random.uniform(-10, 10)
        perp_offset *= _PERP_SCALE
        perp_x, perp_y = -dir_y, dir_x
        self.x += perp_x * perp_offset
        self.y += perp_y * perp_offset
        self.direction = (dir_x, dir_y)

        self._hop_index      = 0      # index into self._hops; len(self._hops) once settled
        self._hop_timer       = 0.0
        self._hop_start_x     = self.x
        self._hop_start_y     = self.y
        self._hop_z           = 0.0   # visual height off the ground (cosmetic only)
        self.is_settled       = False

        self._age       = 0.0
        self._anim_timer = 0.0
        self.collected  = False
        self.active     = True
        self._render_alpha = 255   # 0-255, faded by the pre-despawn blink (see update())

        self.draw_layer = DrawLayer.ITEMS
        self.y_sort     = True

    @classmethod
    def _load_frames(cls, zeni_type):
        frames = cls._sprite_cache.get(zeni_type)
        if frames is None:
            frame_w, frame_h = ZENI_FRAME_SIZES.get(zeni_type, (6, 8))
            sheet = SpriteSheet(zeni_icon_path(zeni_type))
            raw_frames = sheet.get_all_frames(frame_w, frame_h)
            if not raw_frames:
                placeholder = pygame.Surface((frame_w, frame_h), pygame.SRCALPHA)
                placeholder.fill((255, 0, 255, 255))  # missing-sprite placeholder
                raw_frames = [placeholder]

            scaled_w = max(1, round(frame_w * RENDER_SCALE))
            scaled_h = max(1, round(frame_h * RENDER_SCALE))
            frames = [pygame.transform.scale(f, (scaled_w, scaled_h)) for f in raw_frames]
            cls._sprite_cache[zeni_type] = frames
        return frames

    @classmethod
    def _load_shadow(cls, zeni_type):
        """Ground shadow for *zeni_type*, or None if it doesn't have one
        (see ZENI_SHADOWS). Cached per zeni_type — the image itself is
        shared/static, no per-frame animation."""
        if zeni_type in cls._shadow_cache:
            return cls._shadow_cache[zeni_type]

        shadow = None
        shadow_path = zeni_shadow_path(zeni_type)
        if shadow_path is not None:
            raw_shadow = pygame.image.load(shadow_path).convert_alpha()
            scaled_w = max(1, round(raw_shadow.get_width() * RENDER_SCALE))
            scaled_h = max(1, round(raw_shadow.get_height() * RENDER_SCALE))
            shadow = pygame.transform.scale(raw_shadow, (scaled_w, scaled_h))

        cls._shadow_cache[zeni_type] = shadow
        return shadow

    @classmethod
    def _load_composite_frames(cls, zeni_type):
        """Pre-merge the shadow and each animation frame into a single cached
        image per (zeni_type, frame_idx), valid ONLY while settled (z_offset
        is always 0 then, so shadow and frame have a fixed relative offset —
        see draw()). Cuts the settled-steady-state draw cost from 2 blits +
        2 set_alpha calls down to 1 of each, which matters once a pile gets
        into the thousands. Returns a list of (surface, offset_x, offset_y)
        where offset_x/y is the surface's own centre point (i.e. blit at
        world_center - offset to land it correctly).
        """
        cached = cls._composite_cache.get(zeni_type)
        if cached is not None:
            return cached

        frames = cls._load_frames(zeni_type)
        shadow = cls._load_shadow(zeni_type)

        composites = []
        if shadow is None:
            # Nothing to merge — composite degenerates to the bare frame.
            for frame in frames:
                composites.append((frame, frame.get_width() / 2, frame.get_height() / 2))
        else:
            sw, sh = shadow.get_size()
            y_offset = zeni_shadow_y_offset(zeni_type)
            for frame in frames:
                fw, fh = frame.get_size()
                # The shadow's own top/bottom extent, shifted by y_offset —
                # bounds below must account for this or a large enough
                # offset pushes the shadow past the canvas edge and it gets
                # silently clipped (this is what was happening before this
                # fix).
                shadow_top    = -sh / 2 + y_offset
                shadow_bottom = sh / 2 + y_offset
                left   = min(-sw / 2, -fw / 2)
                top    = min(shadow_top, -fh / 2)
                right  = max(sw / 2, fw / 2)
                bottom = max(shadow_bottom, fh / 2)
                canvas_w = max(1, math.ceil(right - left))
                canvas_h = max(1, math.ceil(bottom - top))


                canvas = pygame.Surface((canvas_w, canvas_h), pygame.SRCALPHA)
                canvas.blit(shadow, (-sw / 2 - left, -sh / 2 - top + y_offset))
                canvas.blit(frame,  (-fw / 2 - left, -fh / 2 - top))
                composites.append((canvas, -left, -top))

        cls._composite_cache[zeni_type] = composites
        return composites

    def get_collision_rect(self):
        # Native (unscaled) frame size — x/y are world units, RENDER_SCALE is
        # a draw-time-only concern (see draw()).
        frame_w, frame_h = ZENI_FRAME_SIZES.get(self.zeni_type, (6, 8))
        return pygame.Rect(int(self.x - frame_w / 2), int(self.y - frame_h / 2), frame_w, frame_h)

    def update(self, dt, player):
        if not self.active:
            return

        self._age += dt
        self._anim_timer += dt
        if self._age >= _LIFETIME:
            self.active = False
            return

        # Blink warning for the last _BLINK_WARN_DURATION seconds before
        # despawn: opacity eases 100 -> 0 -> 100 -> 0 -> ... on a cosine
        # wave, one full cycle every _BLINK_PERIOD seconds, right up until
        # _LIFETIME. Full opacity (255) the rest of the time.
        if self._age >= _BLINK_START:
            elapsed = self._age - _BLINK_START
            alpha_factor = (math.cos(2 * math.pi * elapsed / _BLINK_PERIOD) + 1.0) / 2.0
            self._render_alpha = int(255 * alpha_factor)
        else:
            self._render_alpha = 255

        if not self.is_settled:
            hop = self._hops[self._hop_index]
            self._hop_timer += dt
            t = min(1.0, self._hop_timer / hop['duration'])

            # Asymmetric arc — see _ARC_PEAK_FRACTION/_ARC_PEAK_X_FRACTION's
            # comment above. Rise is a slow ease-out (climbs fast at first,
            # flattens near the top, ending at zero vertical speed); drop
            # is an ease-in whose steepness is set by this hop's
            # 'fall_ease' — derived per-coin from its hop distance (see
            # _fall_ease_for_distance). It must stay >= 2 or the fall starts
            # at a non-zero speed and snaps right at the peak instead of
            # flowing out of the rise. 2 (the far-coin end) is a smooth,
            # gravity-like fall with no pause; higher values (near coins)
            # flatten near the peak first, giving a deliberate hang/halt
            # before it drops. How much real TIME the drop gets is a
            # separate knob: each hop's 'peak_fraction' (default
            # _ARC_PEAK_FRACTION) — lower it to give the fall a bigger,
            # slower-feeling share of the duration.
            #
            # 'peak_x_fraction' (default _ARC_PEAK_X_FRACTION) is just as
            # important as fall_ease for a smooth-looking far hop: if it
            # doesn't match peak_fraction, the horizontal speed itself
            # changes at the peak (see _PEAK_X_FRACTION_NEAR/_FAR's
            # comment) — that mismatch is what still reads as a "halt"
            # even once the vertical fall_ease is fixed. Far coins get it
            # equal to peak_fraction (constant horizontal speed, no
            # slowdown); near coins keep it lopsided (a deliberate creep
            # to a stop).
            peak_fraction = hop['peak_fraction']
            peak_x_fraction = hop['peak_x_fraction']
            if t <= peak_fraction:
                rt = t / peak_fraction if peak_fraction > 0 else 1.0
                x_progress = peak_x_fraction * rt
                height_progress = math.sin(rt * math.pi / 2)
            else:
                ft = (t - peak_fraction) / (1.0 - peak_fraction) if peak_fraction < 1.0 else 1.0
                x_progress = peak_x_fraction + (1.0 - peak_x_fraction) * ft
                fall_ease = hop['fall_ease']
                height_progress = 1.0 - ft ** fall_ease

            self.x = self._hop_start_x + self.direction[0] * hop['distance'] * self.distance_scale * x_progress
            self.y = self._hop_start_y + self.direction[1] * hop['distance'] * self.distance_scale * x_progress
            # No self.distance_scale factor here — hop['height'] is already
            # derived from the scaled actual travel distance (see
            # _hop_height_for_distance in __init__); multiplying by
            # distance_scale again would double-apply it.
            self._hop_z = hop['height'] * height_progress

            if t >= 1.0:
                self._hop_index  += 1
                self._hop_timer   = 0.0
                self._hop_start_x = self.x
                self._hop_start_y = self.y
                self._hop_z       = 0.0
                if self._hop_index >= len(self._hops):
                    self.is_settled = True
        else:
            dx = player.x - self.x
            dy = player.y - self.y
            if dx * dx + dy * dy <= _COLLECT_RADIUS_SQ:
                self.collected = True
                self.active    = False

    def fast_forward_offscreen(self, dt):
        """Cheap alternative to update() for a pickup that is NOT yet
        settled but is currently outside the camera's viewport (see
        game.py's _update_zeni_pickups).

        This is the case age_only() doesn't cover, and it's the one that
        actually matters for a huge simultaneous drop: right from spawn,
        spawn_zeni_pickups scatters coins across a spread that grows with
        the drop size (see _MIN_COIN_SPACING), so with a few thousand
        coins most of them start out already far outside the screen — but
        they're still "flying" (is_settled is False), so the old code path
        ran the full sin/pow arc computation on every one of them, every
        frame, for the whole ~1s hop, same cost as an on-screen coin.
        Nobody can see that arc, so instead of animating it frame by frame
        we just advance the hop timer and, once the hop duration elapses,
        jump straight to the algebraic final landing spot and mark it
        settled — collapsing ~1 second of per-frame trig into one add and
        one compare per frame, with a single closed-form position update
        the instant the hop completes.

        Only call this for pickups where self.is_settled is already False;
        an on-screen coin must always go through the real update() so its
        arc still animates correctly. If the coin re-enters view mid-hop
        (rare — the hop only lasts ~1s), it'll resume the real arc from
        wherever this left the timer, which may show a small visual pop;
        an acceptable trade for a scenario that's a stress test to begin
        with.
        """
        if not self.active:
            return

        self._age += dt
        if self._age >= _LIFETIME:
            self.active = False
            return

        self._hop_timer += dt
        hop = self._hops[self._hop_index]
        if self._hop_timer >= hop['duration']:
            self.x = self._hop_start_x + self.direction[0] * hop['distance'] * self.distance_scale
            self.y = self._hop_start_y + self.direction[1] * hop['distance'] * self.distance_scale
            self._hop_z = 0.0
            self._hop_index += 1
            if self._hop_index >= len(self._hops):
                self.is_settled = True

    def age_only(self, dt):
        """Cheap alternative to update() for a SETTLED pickup that's currently
        outside the camera's viewport (see game.py's _update_zeni_pickups).

        A settled, off-screen coin can't be collected (the player has to be
        near it, and the player is always near the camera) and isn't being
        drawn (draw is already camera-culled — see the pile comment there),
        so its blink alpha and its distance check against the player are
        both wasted work. All that actually matters for it is aging toward
        despawn. This drops a full update() call — hop math already skipped
        once settled, but also the blink trig and the player-distance
        check — down to a single add and compare, which is the difference
        that matters once a pile gets into the thousands.

        Only call this for pickups where self.is_settled is already True;
        an unsettled (still-hopping) coin must always go through the real
        update() so its flight animation stays correct even off-screen.
        """
        if not self.active:
            return
        self._age += dt
        # Keep the sprite animation advancing even though nothing else here
        # needs it right now: if this coin drifts back on-screen later (the
        # camera can move, even if the coin can't), draw() picks up right
        # where the animation left off instead of looking frozen for
        # however long it spent off-screen, then jumping forward.
        self._anim_timer += dt
        if self._age >= _LIFETIME:
            self.active = False

    def _anim_frame_index(self, num_frames):
        """Which frame of a *num_frames*-long sprite strip to show right
        now: sit on frame 0 for _ANIM_IDLE_DURATION seconds, then step
        through frames 1..num_frames-1 once at _ANIM_FPS, then repeat —
        idle, play, idle, play, ... (not a continuous loop).
        """
        if num_frames <= 1:
            return 0
        play_duration = (num_frames - 1) / _ANIM_FPS
        cycle_length = _ANIM_IDLE_DURATION + play_duration
        t = self._anim_timer % cycle_length
        if t < _ANIM_IDLE_DURATION:
            return 0
        frame_offset = int((t - _ANIM_IDLE_DURATION) * _ANIM_FPS)
        return min(num_frames - 1, 1 + frame_offset)

    def get_sort_key(self):
        """(layer, y) tuple used by LayerManager.draw_all — see DrawableObject."""
        return (self.draw_layer, self.y)

    def draw(self, surface, camera, colors=None, render_scale=RENDER_SCALE):
        center_x = self.x * render_scale - camera.x
        center_y = self.y * render_scale - camera.y

        if self.is_settled:
            # Settled coins never have a z_offset, so the shadow and frame
            # keep a fixed relative position — safe to use the merged
            # composite (1 blit + 1 set_alpha instead of 2 of each).
            composites = self._load_composite_frames(self.zeni_type)
            idx = self._anim_frame_index(len(composites))
            composite, off_x, off_y = composites[idx]
            composite.set_alpha(self._render_alpha)
            screen_x = int(center_x - off_x)
            screen_y = int(center_y - off_y)
            surface.blit(composite, (screen_x, screen_y))
            return

        # Still mid-hop: the frame lifts by _hop_z while the shadow stays
        # pinned to the ground, so they can't share one merged image here.
        frames = self._load_frames(self.zeni_type)
        idx = self._anim_frame_index(len(frames))
        frame  = frames[idx]

        shadow = self._load_shadow(self.zeni_type)
        if shadow is not None:
            shadow.set_alpha(self._render_alpha)
            shadow_x = int(center_x - shadow.get_width() / 2)
            shadow_y = int(center_y - shadow.get_height() / 2) + zeni_shadow_y_offset(self.zeni_type)
            surface.blit(shadow, (shadow_x, shadow_y))

        frame.set_alpha(self._render_alpha)
        screen_x = int(center_x - frame.get_width() / 2)
        screen_y = int(center_y - self._hop_z * render_scale - frame.get_height() / 2)
        surface.blit(frame, (screen_x, screen_y))


def spawn_zeni_pickups(drop, x, y, direction=(1.0, 0.0)):
    """Turn a {denomination: count} drop dict (as returned by roll_zeni_drop)
    into a list of ZeniPickup world objects centered on (x, y), each hopping
    away in `direction` (a unit vector) — the usual call site is an enemy's
    death position, with direction pointing away from whoever landed the
    killing blow.

    Spawns one ZeniPickup per individual coin (not one lumped pickup per
    denomination), so a drop of e.g. {'small_silver_zeni': 6} produces 6
    separate coins that each fly/scatter on their own — every ZeniPickup's
    `count` is always 1 here.

    Every coin across the WHOLE drop (regardless of denomination) gets a
    guaranteed-distinct perpendicular slot — stratified across a spread that
    grows with coin count (see _MIN_COIN_SPACING) plus a little random
    jitter within each slot so it doesn't look like a mechanical picket
    fence — and its own random distance_scale (see _DISTANCE_VARIANCE).
    Between the two, no two coins from the same kill land in the same spot
    or travel the same distance, even if they roll the same hop profile."""
    total = sum(drop.values())
    if total == 0:
        return []

    spread = max(total * _MIN_COIN_SPACING, _MIN_COIN_SPACING)
    slot_width = spread / total
    slots = []
    for i in range(total):
        slot_center = -spread / 2.0 + slot_width * (i + 0.5)
        slots.append(slot_center + random.uniform(-slot_width * 0.15, slot_width * 0.15))
    random.shuffle(slots)  # so slot order isn't correlated with denomination order

    pickups = []
    idx = 0
    for zeni_type, count in drop.items():
        for _ in range(count):
            distance_scale = random.uniform(1.0 - _DISTANCE_VARIANCE, 1.0 + _DISTANCE_VARIANCE)
            pickups.append(ZeniPickup(x, y, zeni_type, 1, direction,
                                       perp_offset=slots[idx], distance_scale=distance_scale))
            idx += 1
    return pickups