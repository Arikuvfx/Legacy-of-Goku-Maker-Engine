from config.settings import RENDER_SCALE as _RENDER_SCALE
from attacks.beam import BeamAttack, KamehamehaChargeEffect


# Where the charge-up sprite sits relative to the player, per direction (world
# units, added before scaling — same convention as BeamAttack's own spawn
# offsets). Pulled out to a module-level constant (rather than left as a
# literal inside BansheeBlastChargeEffect.__init__) so player.py can import
# and reuse the exact same numbers when it spawns the fired beam — see
# BansheeBlastAttack below and Player.fire_banshee_blast_auto() in player.py,
# which both need the beam's begin sprite to start exactly where the charge
# sprite was, not at the generic _DIRECTION_SPAWN_OFFSETS every other beam
# uses.
BANSHEE_BLAST_CHARGE_OFFSETS = {'down': (-7, 22), 'up': (5, 2), 'left': (-15, 18), 'right': (15, 18)}


class BansheeBlastChargeEffect(KamehamehaChargeEffect):
    """Charge-up visual for the Banshee Blast.

    Frame playback is a single run-up followed by one bounce back, then
    a hold — e.g. for this sheet's 2 frames that's 1, 2, 1, then holds on
    1 (in 1-indexed terms). This is pulse_steps=1 with hold_after_pulse=
    True (see KamehamehaChargeEffect._current_frame_index): pulse_steps=1
    plays exactly one alternating step after the run-up (landing back on
    frame 1), and hold_after_pulse freezes there instead of continuing to
    alternate 1, 2, 1, 2, ... forever the way plain kamehameha's pulse
    does.

    The player's own sprite doesn't change between charging and firing
    for this attack — player.py should keep whatever single animation
    key it set when the charge started playing through firing, rather
    than swapping to a separate fire/firebeam key on release the way
    plain kamehameha does.

    charging_banshee_blast.png is drawn facing right only; there are no
    per-direction offset frames, so rotate_to_direction=True is passed
    through to KamehamehaChargeEffect, which rotates the loaded frames
    to match the fire direction instead of using direction_offsets.

    frame_width/frame_height are the native (unscaled) tile size of
    charging_banshee_blast.png itself — independent of whatever tile
    size the fired beam's begin/middle/end/collision sheets use (see
    BansheeBlastAttack below). Defaults to 10x10, this sheet's actual
    grid (KamehamehaChargeEffect itself still defaults to 16x16 for
    plain kamehameha), but can still be overridden here if the asset
    ever changes.

    direction_offsets is where to change the charge sprite's on-screen
    POSITION — a {direction: (x, y)} dict of world-unit nudges applied
    on top of the player's own center before scaling (see
    KamehamehaChargeEffect.draw): x positive = right, negative = left;
    y positive = down, negative = up. Defaults to
    BANSHEE_BLAST_CHARGE_OFFSETS (module-level, above) rather than {} or
    the kamehameha-tuned values, since rotate_to_direction=True still
    needs *some* per-direction nudge here to actually center on the
    player — pass your own dict to override it for one or more
    directions; any direction you don't include falls back to (0, 0).
    """

    def __init__(self, player, scale=_RENDER_SCALE, target_charge_duration=1,
                 frame_width=10, frame_height=10, direction_offsets=None):
        super().__init__(
            player,
            scale=scale,
            attack_name='banshee_blast',
            target_charge_duration=target_charge_duration,
            pulse_steps=1,
            hold_after_pulse=True,
            rotate_to_direction=True,
            frame_width=frame_width,
            frame_height=frame_height,
            direction_offsets=direction_offsets if direction_offsets is not None else BANSHEE_BLAST_CHARGE_OFFSETS,
        )


class BansheeBlastAttack(BeamAttack):
    """The fired beam. Same begin/middle/end/collision shape as plain
    kamehameha (no ball/circle overlay) — begin, middle, and end tiles
    all just play through their own frames normally, exactly like
    plain kamehameha does. The only differences from plain kamehameha
    are the sprite set used (attack_name='banshee_blast') and
    rotate_to_direction=True: the banshee_blast sheets are drawn facing
    right only, as a single row, so BeamAttack rotates them to match
    self.direction instead of reading a separate per-direction row.

    Every sprite sheet this attack loads — begin, middle, end, and
    collision — has its own independently-configurable native tile size,
    rather than all sharing one fixed grid. Each *_frame_width/height
    below defaults to this attack's actual asset sizes; pass any of them
    explicitly if a given banshee_blast sheet's grid ever changes — the
    others are unaffected.

    decay_uses_begin_sprite defaults to True here — there is no separate
    decay_banshee_blast.png at all; the decay sweep just reuses
    begin_sprite's already-loaded frames wholesale (see BeamAttack.
    load_sprites), since banshee_blast's decay is meant to look exactly
    like its begin orb. decay_frame_width/height are irrelevant as long
    as this stays True.

    middle_sync_random defaults to True here (unlike plain kamehameha,
    where it's off) — every middle tile along the beam reads the same
    self._synced_middle_frame each tick instead of each tile animating
    independently. begin, decay, and the tip (end/collision) also follow
    that same synced frame now (see BeamAttack._synced_frame_index), so
    the whole beam — not just its middle shaft — steps through frames
    together as one unified animation. Both flags are still overridable
    via kwargs if a future banshee_blast variant wants the old
    independent-per-sprite look back.
    """

    def __init__(self, x, y, direction,
                 frame_width=16, frame_height=16,
                 begin_frame_width=8, begin_frame_height=7,
                 middle_frame_width=8, middle_frame_height=7,
                 end_frame_width=8, end_frame_height=7,
                 collision_frame_width=8, collision_frame_height=16,
                 **kwargs):
        kwargs.setdefault('attack_name', 'banshee_blast')
        kwargs.setdefault('rotate_to_direction', True)
        kwargs.setdefault('middle_sync_random', True)
        kwargs.setdefault('decay_uses_begin_sprite', True)
        super().__init__(
            x, y, direction,
            frame_width=frame_width, frame_height=frame_height,
            begin_frame_width=begin_frame_width, begin_frame_height=begin_frame_height,
            middle_frame_width=middle_frame_width, middle_frame_height=middle_frame_height,
            end_frame_width=end_frame_width, end_frame_height=end_frame_height,
            collision_frame_width=collision_frame_width, collision_frame_height=collision_frame_height,
            **kwargs
        )