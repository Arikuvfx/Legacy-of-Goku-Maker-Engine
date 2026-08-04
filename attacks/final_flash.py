from config.settings import RENDER_SCALE as _RENDER_SCALE
from attacks.beam import BeamAttack, KamehamehaChargeEffect

# NOTE: adjust the import path above (`attacks.beam`) to wherever beam.py
# actually lives in your project if it's not in an `attacks` package.


class FinalFlashAttack(BeamAttack):
    """Final Flash — reuses BeamAttack's entire draw/obstruction pipeline,
    but with a completely different opening/closing feel from the
    Kamehameha's travelling beam:

    - instant_length=True: self.length snaps straight to its full reach
      (instant_reach, or wherever an obstruction caps it) the moment it's
      fired — no ramp at all, not even a fast one. There's nothing to
      "grow" lengthwise; it's just instantly there.
    - thickness_grow_duration/thickness_shrink_duration: what DOES animate
      is thickness (cross-axis width) — 0 up to full right on spawn, and
      back down to 0 on release — like a pillar snapping open, then
      closing back up.
    - decay_style='thickness': release doesn't trigger the Kamehameha's
      lengthwise sweep-and-vanish at all (there's nothing to sweep — the
      length never visibly grew). Instead start_decay() just runs the
      thickness ramp in reverse — closing back up exactly the way it
      opened — and the beam goes inactive once fully closed.

    Sprites load from assets/sprites/attacks/final_flash/ via attack_name
    (begin_final_flash.png, middle_final_flash.png required; end_/
    collision_final_flash.png optional and degrade gracefully if missing,
    same as BeamAttack.load_sprites already does for kamehameha —
    decay_final_flash.png isn't needed at all in 'thickness' decay_style
    since it's never drawn).
    """

    def __init__(self, x, y, direction, scale=_RENDER_SCALE):
        super().__init__(
            x, y, direction, scale,
            attack_name='final_flash',
            instant_length=True,
            # How far it reaches when nothing obstructs it (max_length
            # stays inf) — generously past any realistic screen edge.
            instant_reach=5000,
            # Final Flash's middle tiles are a full 16x16, not the
            # Kamehameha's 6x6.
            middle_frame_width=16,
            middle_frame_height=16,
            # final_flash's begin sprite fills its whole 16x16 frame edge to
            # edge (unlike the kamehameha's tapered/trimmed begin sprite),
            # so the middle tiles should start right after it rather than
            # overlapping halfway into it — see BeamAttack's
            # begin_overlap_ratio docstring for why 0.5 (the default) would
            # otherwise draw a middle tile on top of, cutting off, its back
            # half.
            begin_overlap_ratio=1.0,
            # The open/close pillar animation — quick since this reads as
            # a near-instant flash rather than a slow bloom; tune to taste.
            thickness_grow_duration=0.15,
            thickness_shrink_duration=0.15,
            # Close back up in thickness instead of the Kamehameha's
            # lengthwise sweep — see class docstring.
            decay_style='thickness',
            # Passes straight through enemies instead of stopping/showing
            # the collision-sprite tip on contact — it still deals damage
            # (see enemy.py's separate contact check), it just doesn't
            # visually cut off the way the kamehameha does. Walls still
            # stop it normally — this only affects enemy contact.
            ignore_enemy_obstruction=True,
            # How hard it shoves an enemy per contact frame (world px) —
            # lower than the enemy's own default (beam_push_force, 3) since
            # this beam stays in contact for a lot more frames than the
            # kamehameha typically does (it doesn't stop/cut off at the
            # enemy — see ignore_enemy_obstruction above — and can cover a
            # much wider corridor), so the per-frame push was compounding
            # into a far bigger total shove than intended. Tune to taste —
            # 0 disables pushback entirely while still dealing damage.
            push_force=1,
        )


class FinalFlashChargeEffect(KamehamehaChargeEffect):
    """Charge-up glow for Final Flash — same run-up-then-pulse behavior as
    KamehamehaChargeEffect, defaulting to the same 1-second windup, just
    pointed at its own sprite sheet (assets/sprites/attacks/final_flash/
    charging_final_flash.png).

    direction_offsets here starts as a copy of the Kamehameha's tuned
    values as a placeholder — once you see how the final_flash charge
    pose actually sits on the player sprite (it may well be a different
    stance/scale), adjust these numbers rather than the base class's.
    """

    def __init__(self, player, scale=_RENDER_SCALE):
        super().__init__(
            player, scale,
            attack_name='final_flash',
            target_charge_duration=1,
            direction_offsets={
                'down':  (-4, 21),
                'left':  (8, 23),
                'right': (-8, 23),
                'up':    (3, 21),
            },
        )