import pygame
import random
import time
import math
from config.settings import (
    WORLD_WIDTH, WORLD_HEIGHT,
    WHITE, GRAY, PURPLE, BLUE, RED, YELLOW, BLACK,
    RENDER_SCALE,
)
from attacks import Projectile, BeamAttack, MeleeAttack, KamehamehaChargeEffect
from attacks.banshee_blast import BansheeBlastAttack, BansheeBlastChargeEffect, BANSHEE_BLAST_CHARGE_OFFSETS
# NOTE: add these two to attacks/__init__.py's exports (alongside BeamAttack/
# KamehamehaChargeEffect) so this import keeps working — they currently only
# live in attacks/final_flash.py.
from attacks.final_flash import FinalFlashAttack, FinalFlashChargeEffect
from attacks.big_bang_kamehameha import BigBangKamehamehaAttack, BigBangKamehamehaChargeEffect
from attacks.genkidama import GenkidamaChargeEffect, GenkidamaBlast
from attacks.masenko import MasenkoAimIndicator, MasenkoHoldEffect, MasenkoProjectile
# NOTE: same deal as the FinalFlash import above — add these to attacks/__init__.py's
# exports if you want them importable from `attacks` directly instead.
from attacks.burning_attack import BurningAttack, BurningChargeEffect
from attacks.flame_kamehameha import FlameKamehamehaAttack
from attacks.energy_sword import EnergySwordChargeEffect, EnergySwordSpinEffect
from attacks.dragon_fist import DragonFistAttack, _DIRECTION_UNIT as _DRAGON_FIST_DIRECTION_UNIT
from attacks.ghost_kamikaze_attack import GhostKamikazeAttack, get_ghost_kamikaze_spawn_offset
from attacks.big_bang_attack import BigBangAttackChargeEffect, BigBangAttackBlast
from core.sprite_system import create_character_sprite
from core.draw_layers import DrawLayer


# ---------------------------------------------------------------------------
# Per-direction spawn offsets used by both blast and beam.
# (offset_x, offset_y) in world units, relative to the player centre.
# ---------------------------------------------------------------------------
_DIRECTION_SPAWN_OFFSETS = {
    'up':    (0,   -15),
    'down':  (0,    10),
    'left':  (-12,   4),
    'right': (12,    4),
}

# Collapses the sword spin's 8-directional octants down to the 4-directional
# facing self.direction otherwise uses everywhere else (idle, melee, spawn
# offsets, ...). Diagonals lean horizontal since left/right is the axis the
# rest of the game already treats as the "primary" facing (flips, offsets).
# Used only when the spin ends with the player having stayed in place —
# see Player.stop_sword_spin().
_OCTANT_TO_CARDINAL = {
    'up':         'up',
    'up_right':   'right',
    'right':      'right',
    'down_right': 'right',
    'down':       'down',
    'down_left':  'left',
    'left':       'left',
    'up_left':    'left',
}


class Player:
    def __init__(self, x, y, character='goku', costume='base', game_config=None):
        """Create the player at world position (*x*, *y*).

        Args:
            x, y:        Starting world coordinates.
            character:   Character ID used to load the sprite sheet (e.g. 'goku').
            costume:     Costume/variant string passed to the sprite loader.
            game_config: Optional GameConfig — used to initialise the
                         TransformationSystem and derive stat scaling.
        """
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.shadow_size = 'small'  # 'small' or 'big'

        # Divide by RENDER_SCALE so world-unit speed stays consistent across resolutions
        self.speed = 3 / RENDER_SCALE
        self.run_speed = 6 / RENDER_SCALE

        self.level = 1

        # Max HP/EP are level-driven (see GameConfig.hp_curve_value /
        # ep_curve_value + Player._grow_hp_ep). Without a game_config we
        # fall back to flat 100/100 so the player can still be constructed
        # standalone (e.g. in editor tooling).
        if game_config:
            self.max_hp = game_config.hp_curve_value(self.level)
            self.max_ki = game_config.ep_curve_value(self.level)
        else:
            self.max_hp = 100
            self.max_ki = 100
        self.hp = self.max_hp
        self.ki = self.max_ki

        # Death — set the moment hp first reaches 0 (see take_damage()/
        # die() below). Locks out all control via can_act()/can_move();
        # game.py polls this plus sprite.is_animation_finished() to drive
        # the post-death fade-to-black/game-over sequence (see
        # Game._update_death_sequence).
        self.is_dead = False

        self.exp = 0
        # Lifetime XP earned — unlike self.exp (which resets down to the
        # leftover amount every time level_up() fires), this only ever
        # goes up, so the pause menu can show "total XP collected" rather
        # than just however much is currently banked toward the next level.
        self.total_exp = 0
        self.exp_to_next_level = game_config.get_xp_for_level(self.level) if game_config else 100
        self.stat_points = 0
        self.pending_level_up = False

        # Currency earned from enemy zeni drops (see core/zeni_system.py /
        # Enemy.get_zeni_drop() — credited in game.py's enemy-cleanup loop).
        self.zeni = 0

        # -----------------------------------------------------------------
        # Passive ki regen — like Buu's Fury: a small trickle back every so
        # often, even while just standing around (not tied to combat).
        # -----------------------------------------------------------------
        self.ki_regen_interval = 10.0        # Seconds between regen ticks
        self.ki_regen_percent  = 0.05        # 5% of max_ki per tick
        self.ki_regen_timer    = 0.0

        self.direction = 'down'
        self.inventory = []
        self.is_running = False
        self.is_attacking = False
        self.attack_timer = 0
        self.attack_cooldown = 0

        # Chest-opening pickup pose (see start_pickup_item()) — held for a
        # fixed real-time duration rather than however long pickup_item.png's
        # own frames happen to take, since it's paired with an item icon
        # floating up above the player over the same span (see game.py's
        # _update_chest_pickup/_draw_chest_pickup_icon).
        self.is_picking_up_item = False
        self.pickup_item_timer = 0.0
        self.PICKUP_ITEM_DURATION = 1.0

        # Sprint footsteps — walking is silent, only running plays a sound.
        # Ticked by game.py (which owns dt) right after calling move().
        self.footstep_timer = 0.0
        self.footstep_interval = 0.28  # seconds between footstep sounds while sprinting

        self.draw_layer = DrawLayer.PLAYER
        self.y_sort = True

        # Transformation system — lazy-imported to avoid circular imports
        self.transformation = None
        if game_config:
            from core.transformation_system import TransformationSystem
            self.transformation = TransformationSystem(self, game_config)

        # Base stats — all start at 1; boosted through stat points (see update_derived_stats)
        self.stats = {
            'strength': 1,
            'ki_power': 1,
            'vitality': 1,
            'energy':   1,
            'speed':    1,
            'defense':  1,
            'ki_regen': 30,   # matches character_creator default; drives ki_regen_interval
        }

        # Sprite + animation
        self.sprite = create_character_sprite(character, costume, 32, 32)
        self.character = character          # Kept for character-switching support
        self.costume = costume
        self.current_animation_state = 'idle'

        # Stand-still ("idle wait") timer. After IDLE_WAIT_DELAY seconds of
        # being in the plain 'idle' state, we play idle_transition (the
        # varying-length lead-in frames, once) then settle into idle_wait
        # (the actual looping wait pose) — both always facing down regardless
        # of self.direction. Reset via enter_idle() any time the player
        # returns to normal idle. See update()'s animation-state machine.
        self.idle_timer = 0.0
        self.IDLE_WAIT_DELAY = 15.0

        # -----------------------------------------------------------------
        # Ki attack state
        # -----------------------------------------------------------------
        self.ki_attack_mode = 'blast'       # 'blast' | 'beam' | 'kamekameha' | 'banshee_blast' | 'final_flash' | 'big_bang_kamehameha' | 'burning_attack' | 'flame_kamehameha' | 'energy_punch' | 'transform'

        # Attacks this character is allowed to use, loaded from their JSON config.
        # 'ki_mode_config' mirrors cfg["attacks"]["ki_attack_mode"] and controls
        # which modes the TAB key cycles through ('blast'|'beam'|'both').
        self.equipped_attacks = []
        self.ki_mode_config   = 'blast'     # default until config is applied

        # Whether this character has at least one transformation defined
        # in the character creator (cfg["transformations"]). Gates whether
        # 'transform' shows up as a cyclable ki mode at all — set in
        # Game._reload_attack_config().
        self.has_transformation = False

        self.is_charging_beam = False
        self.beam_charge_time = 0
        # Seconds of charge before auto-fire. This is a fallback/default
        # value only — as soon as a charge starts, start_charging_beam()
        # overwrites it with the actual length of the charge-up animation
        # (KamehamehaChargeEffect.get_total_duration()), so the beam never
        # fires before the sprite has finished playing through all of its
        # frames, no matter how many frames the sheet has.
        self.beam_charge_required = 0.3
        self.is_firing_beam = False
        self.current_beam = None
        self.current_charge_effect = None

        # Kamekameha — exact same charge-then-auto-fire beam as the regular
        # Kamehameha above (same BeamAttack/KamehamehaChargeEffect classes,
        # same 'charge'/'firebeam' player sprite states), just pointed at its
        # own 'kamekameha' attack_name so it loads sprites from
        # assets/sprites/attacks/kamekameha/ instead. Kept as fully separate
        # state (own ki_attack_mode value 'kamekameha') so it can be equipped/
        # cycled to and fired independently of the regular beam.
        self.is_charging_kamekameha = False
        self.kamekameha_charge_time = 0
        self.kamekameha_charge_required = 0.3
        self.is_firing_kamekameha = False
        self.current_kamekameha = None
        self.current_kamekameha_charge_effect = None

        # Banshee Blast — same charge-then-auto-fire beam shape as
        # Kamekameha above (BansheeBlastAttack/BansheeBlastChargeEffect,
        # thin subclasses of BeamAttack/KamehamehaChargeEffect pointed at
        # 'banshee_blast' — see banshee_blast.py), with two differences:
        # the charge effect plays through once and holds on its last frame
        # instead of pulsing (pulse_steps=0, baked into
        # BansheeBlastChargeEffect), and the fired beam's middle tiles all
        # flip between their 3 sprites in sync rather than in a traveling
        # wave (middle_sync_random=True, baked into BansheeBlastAttack).
        # The player sprite also doesn't switch between a charge and a
        # fire animation the way kamekameha's does — see
        # start_charging_banshee_blast()/fire_banshee_blast_auto() below,
        # which both leave current_animation_state at 'banshee_blast'.
        self.is_charging_banshee_blast = False
        self.banshee_blast_charge_time = 0
        self.banshee_blast_charge_required = 0.3
        self.is_firing_banshee_blast = False
        self.current_banshee_blast = None
        self.current_banshee_blast_charge_effect = None

        # Energy Punch — instant close-range strike, no charge-up. Q plays
        # the 'energy_punch' animation once; once it's finished it just sits
        # frozen on its own last frame (same non-looping-animation behavior
        # every finished animation already has) for the rest of a fixed
        # 1-second window, then the player returns to idle. There's no
        # separate attack object (unlike beam/final_flash/kamekameha) —
        # Game._update_energy_punch checks every active enemy's distance to
        # the player directly for the "very near" hit while is_punching is
        # True, passing the player itself in as the attack.
        self.is_punching = False
        self.punch_timer = 0
        self.punch_duration = 1.0
        self.energy_punch_damage = 20
        self.energy_punch_radius = 24        # World px — deliberately tight ("very near")
        self.energy_punch_knockback = 200

        # Block — held via F (see start_blocking/stop_blocking). While
        # active, can_act() (and therefore can_move()) returns False, so
        # blocking is a full stop: no moving, no starting another action.
        # take_damage() halves incoming damage, clamps knockback to a 1px
        # nudge, and skips the hurt animation while this is set.
        self.is_blocking = False

        # Final Flash — same auto-fire-after-charge shape as the beam above
        # (see start_charging_final_flash/update_final_flash_charge/
        # fire_final_flash_auto/stop_final_flash), kept as fully separate
        # state rather than reusing the beam's so both attacks could in
        # theory be mid-charge/decay independently and so each gets its own
        # ki_attack_mode value ('final_flash') for the HUD/mode-cycling to
        # select. final_flash_charge_required is a fallback only, same as
        # beam_charge_required — start_charging_final_flash() overwrites it
        # with FinalFlashChargeEffect.get_total_duration() once charging
        # actually begins.
        self.is_charging_final_flash = False
        self.final_flash_charge_time = 0
        self.final_flash_charge_required = 1
        self.is_firing_final_flash = False
        self.current_final_flash = None
        self.current_final_flash_charge_effect = None

        # Big Bang Kamehameha — same auto-fire-after-charge shape as Final
        # Flash above (see start_charging_big_bang_kamehameha/
        # update_big_bang_kamehameha_charge/fire_big_bang_kamehameha_auto/
        # stop_big_bang_kamehameha), kept as fully separate state for the
        # same reasons final_flash's is. big_bang_kamehameha_charge_required
        # is a fallback only — start_charging_big_bang_kamehameha()
        # overwrites it with BigBangKamehamehaChargeEffect.get_total_duration()
        # once charging actually begins.
        self.is_charging_big_bang_kamehameha = False
        self.big_bang_kamehameha_charge_time = 0
        self.big_bang_kamehameha_charge_required = 1
        self.is_firing_big_bang_kamehameha = False
        self.current_big_bang_kamehameha = None
        self.current_big_bang_kamehameha_charge_effect = None

        # Flame Kamehameha — same charge-then-auto-fire shape as the regular
        # beam (see start_charging_beam/update_beam_charge/fire_beam_auto):
        # holding Q plays the charging_flame_kamehameha charge-up effect for
        # flame_kamehameha_charge_required seconds, then the attack itself
        # spawns automatically. flame_kamehameha_charge_required is a
        # fallback only — start_charging_flame_kamehameha() overwrites it
        # with the charge effect's actual animation length, same convention
        # as beam_charge_required.
        self.is_charging_flame_kamehameha = False
        self.flame_kamehameha_charge_time = 0
        self.flame_kamehameha_charge_required = 0.3
        self.current_flame_kamehameha_charge_effect = None
        self.is_firing_flame_kamehameha = False
        self.current_flame_kamehameha = None
        self.flame_kamehameha_ki_drain = 20  # Ki drained per second while firing, mirrors beam_ki_drain

        # Genkidama state — unlike beam, this doesn't auto-fire: it charges
        # while Q is held and fires whatever state was reached the moment Q
        # is released (see start_charging_genkidama/release_genkidama).
        self.is_charging_genkidama = False
        self.genkidama_charge_effect = None
        # Ki cost scales with the state actually fired at.
        self.genkidama_ki_cost = {1: 15, 2: 25, 3: 40, 4: 60, 5: 90}

        # Brief throw pose shown right after release — reuses the firebeam
        # sprite for a fixed duration rather than looping indefinitely like
        # the actual beam does (see release_genkidama/update()).
        self.is_firing_genkidama = False
        self.genkidama_fire_pose_timer = 0.0
        self.genkidama_fire_pose_duration = 0.3

        # Burning Attack state — charges while the attack button is held (the
        # player freezes on the kiblast wind-up frame, see start_charging_burning),
        # and fires on release like genkidama does, rather than auto-firing like
        # the beam. See start_charging_burning/update_burning_charge/release_burning.
        self.is_charging_burning = False
        self.burning_charge_effect = None
        self.burning_ki_cost = 20
        self.burning_stun_duration = 1.5

        # Instant Transmission state — while targeting, the whole world
        # freezes (driven by Game._update_instant_transmission) and the
        # player aims a screen-space cursor; on release it teleports to
        # each selected enemy in pick order, then back to its starting
        # position. See start_targeting_instant_transmission,
        # begin_teleport_sequence, and update_it_teleport below.
        self.is_targeting_it = False
        self.is_teleporting_it = False
        self.it_original_pos = None
        self._it_pre_direction = None  # facing to restore once the whole IT sequence ends
        self.it_hop_queue = []      # enemies to visit, in pick order
        self.it_hop_index = 0
        # Each hop is: a short flicker burst in place ('pre'), then the
        # actual position change, then a flicker burst at the new spot
        # ('post') — longer at a target (a little flurry) than on the
        # final trip home. See update_it_teleport for the full state
        # machine. it_going_home marks whether the current 'post' burst
        # is the final one (heading back to it_original_pos) so we know
        # to end the sequence instead of starting the next hop's 'pre'.
        self.it_flicker_stage = None      # None | 'pre' | 'post'
        self.it_going_home = False
        # Set True the instant a hop's position actually changes (both
        # "landed on a target" and "landed back home" branches, in
        # update_it_teleport's 'pre' stage below) and consumed once by
        # Game via pop_pending_it_teleport_hop() — same pending-flag
        # pattern as pop_pending_charged_melee_hit() — so teleport.wav
        # plays exactly once per hop, including the final trip home.
        self.it_teleport_hop_occurred = False
        self.it_flicker_showing_alt = False  # False='teleport' shown, True='instant_transmission' shown
        self.it_teleport_show_frame2_next = True  # alternates which teleport.png frame is forced
        self.it_flicker_step = 0
        self.it_flicker_steps_needed = 0
        self.it_ki_cost = 30
        # How fast the teleport/instant_transmission flicker alternates,
        # and how many alternations each flicker burst runs for. Tune to
        # taste — these are an approximation of a captured sequence that
        # wasn't fully consistent between captures, so exact counts here
        # are a best guess rather than a locked spec.
        self.it_flicker_interval = 0.12
        self.it_flicker_timer = 0.0
        self.IT_PRE_FLICKER_ALTERNATIONS = 2         # quick flash before departing
        self.IT_POST_FLICKER_ALTERNATIONS_HOP = 4    # brief flurry once arrived at a target
        self.IT_POST_FLICKER_ALTERNATIONS_HOME = 2   # quick flash arriving back home

        # Delay between landing next to a target and actually applying the
        # hit, so the damage doesn't register in the exact same frame as
        # the teleport-in flicker — gives a beat for the "arrival" to read
        # before the "attack" lands. Counted in real seconds (via dt),
        # independent of the flicker cadence above, so it keeps working
        # even if IT_POST_FLICKER_ALTERNATIONS_HOP or it_flicker_interval
        # are retuned later.
        self.it_hit_delay = 0.15
        self._it_pending_hit_target = None
        self._it_hit_delay_timer = 0.0

        # Masenko state — charges while Q is held (oscillating aim indicator
        # + hold_masenko overlay), and always throws on release rather than
        # auto-firing or escalating through states like beam/genkidama do.
        # No throw-pose state here (unlike genkidama) — throw_masenko()
        # snaps straight back to idle the instant it's released.
        self.is_charging_masenko = False
        self.masenko_indicator   = None   # MasenkoAimIndicator, live only while charging
        self.masenko_hold_effect = None   # MasenkoHoldEffect overlay, live only while charging
        self.masenko_ki_cost     = 25

        # Energy Sword state — unlike the other ki attacks, ki is spent
        # once, up front, the moment the charge successfully starts (the
        # cost of drawing the blade). If the charge finishes, the player
        # automatically spins for a short, FREE duration slashing anything
        # nearby, then returns to idle on its own. Releasing Q early,
        # while still charging, cancels the draw — ki drained so far is
        # not refunded. Once the spin has started it can't be cancelled
        # early and costs no further ki.
        self.is_charging_sword = False
        self.sword_charge_time = 0
        # Fallback only — overwritten with the charge effect's real
        # animation length as soon as a charge starts, same convention as
        # beam_charge_required.
        self.sword_charge_required = 0.4
        self.current_sword_charge_effect = None

        self.is_spinning_sword = False
        self.energy_sword_spin = None
        self.sword_spin_timer = 0.0
        self.sword_spin_duration = 2.0  # seconds the free spin lasts
        # A bit brisker than EnergySwordSpinEffect's own 2.0 default.
        self.sword_spin_rotations_per_second = 3.0
        # Walking while spinning is intentionally slower than normal walk
        # speed (on top of running already being capped out) — swinging a
        # blade around one's whole body isn't exactly nimble footwork.
        # Applied in move() whenever is_spinning_sword is True.
        self.sword_spin_move_speed_mult = 0.5

        # Total ki a full charge costs. Charging drains continuously (like
        # the beam does while firing) rather than being paid as a lump sum
        # up front — energy_sword_ki_drain (ki/second) is derived from this
        # divided by however long the charge actually takes, recomputed in
        # start_charging_sword() once sword_charge_required is known.
        self.energy_sword_ki_cost = 20
        self.energy_sword_ki_drain = 20     # ki/second; overwritten once charge starts
        self.energy_sword_damage = 15

        # Dragon Fist state — instant on press, no charge-up (like
        # energy_punch), but held for as long as Q stays down instead of
        # firing-and-forgetting. The head launches out immediately (see
        # DragonFistAttack), then hands control to movement input once
        # it's fully extended; the player's own sprite stays on the
        # 'dragon_fist' pose for the whole hold regardless of how the
        # head is being steered (see move()'s redirect to
        # _move_dragon_fist_head). Releasing Q starts the head retracting
        # back to the player instead of ending the attack instantly (see
        # stop_dragon_fist()/update_dragon_fist()) — is_using_dragon_fist
        # itself only drops once that retract sweep finishes.
        self.is_using_dragon_fist = False
        self.current_dragon_fist = None
        self.dragon_fist_ki_drain = 20        # ki/second while held, mirrors beam_ki_drain

        # Ghost Kamikaze — see start_ghost_kamikaze()/update_ghost_kamikaze_cast()
        # and attacks/ghost_kamikaze.py's GhostKamikazeAttack for the full
        # creation → hold → attack lifecycle. is_casting covers the 3-loop
        # cast animation; is_holding covers the fixed pose afterward, which
        # can_move() lets the player break out of early (see move()).
        self.is_casting_ghost_kamikaze = False
        self.is_holding_ghost_kamikaze = False
        self.current_ghost_kamikaze = None
        self.ghost_kamikaze_ki_cost = 35
        self.ghost_kamikaze_required_loops = 3
        # Charged per-ghost as it actually spawns (see
        # update_ghost_kamikaze_cast()), rather than the whole
        # ghost_kamikaze_ki_cost being taken in one lump sum up front in
        # start_ghost_kamikaze() — same total cost, just spent gradually
        # across the 3 loops instead of one big chunk disappearing the
        # instant the attack is pressed. Derived from ghost_kamikaze_ki_cost
        # rather than a separately-tuned number so the two can't drift out
        # of sync — start_ghost_kamikaze()'s affordability gate still checks
        # the full ghost_kamikaze_ki_cost (the player still needs enough ki
        # for the *entire* attack to begin at all), this only changes when
        # each piece of it actually leaves the ki bar.
        self.ghost_kamikaze_ki_cost_per_ghost = (
            self.ghost_kamikaze_ki_cost / self.ghost_kamikaze_required_loops
        )
        self.ghost_kamikaze_loop_count = 0
        self.ghost_kamikaze_prev_frame_index = 0
        # Each loop's ghost spawns once the cast animation reaches this
        # frame within that loop (0-indexed, so 2 = the 3rd frame) rather
        # than at the very start of the loop — see
        # update_ghost_kamikaze_cast(). No separate "already spawned this
        # loop" bookkeeping is needed here: GhostKamikazeAttack.
        # spawn_next_ghost() itself won't spawn a new ghost while the
        # previous one hasn't cleared the spawn point yet, so calling it
        # repeatedly every frame past this threshold is safe.
        self.ghost_kamikaze_spawn_frame_index = 2

        # Big Bang Attack — see start_charging_big_bang_attack()/
        # update_big_bang_charge()/release_big_bang_attack() and
        # attacks/big_bang_attack.py for the charge effect and the
        # thrown blast. Unlike Genkidama's 5-tier charge, there's only
        # one power level here — is_charging just gates whether Q is
        # currently held down through the fixed intro sequence (see
        # BigBangAttackChargeEffect), not which tier gets thrown.
        self.is_charging_big_bang_attack = False
        self.current_big_bang_charge = None
        self.big_bang_attack_ki_cost = 30
        # Brief throw pose after release, same shape as Genkidama's own
        # fire pose (is_firing_genkidama/genkidama_fire_pose_timer) —
        # held just long enough to read as a throw before returning to
        # idle (see release_big_bang_attack()/update()'s
        # 'big_bang_attack_fire' dispatch).
        self.is_firing_big_bang_attack = False
        self.big_bang_attack_fire_pose_timer = 0.0
        self.big_bang_attack_fire_pose_duration = 0.3

        # Per-input-frame speed the player's movement input steers the
        # head at during the 'controlled' phase — not multiplied by dt,
        # matching move()'s own dx/dy * current_speed convention (one
        # move() call = one frame's worth of motion already).
        self.dragon_fist_head_speed = 4
        # Opening lunge: on throw, the player is carried forward along the
        # throw direction automatically (no input required/accepted) for
        # dragon_fist_lunge_duration seconds, world-units/sec speed given
        # by dragon_fist_follow_speed, with the fist assembly translated
        # along for the ride each frame (see _advance_dragon_fist_lunge)
        # so the whole thing moves as one unit. After the timer runs out,
        # control hands off to the normal head-steering scheme.
        self.dragon_fist_follow_speed = 20
        self.dragon_fist_lunge_duration = 1.5   # seconds
        self.dragon_fist_lunge_timer = 0.0
        self.is_dragon_fist_lunging = False

        # Armed on press, resolved once the dragon_fist animation reaches
        # its release frame — see update_dragon_fist(). Same "arm now,
        # resolve on a later frame" shape as pending_blast below, except
        # DragonFistAttack isn't handed off to game.py, since it isn't
        # added to any external list the way Projectile is.
        self.pending_dragon_fist = None

        # Attack costs
        self.blast_ki_cost = 10
        self.beam_ki_drain = 20             # Ki drained per second while firing
        self.kamekameha_ki_drain = 20       # Ki drained per second while firing, mirrors beam_ki_drain
        self.banshee_blast_ki_drain = 20    # Ki drained per second while firing, mirrors beam_ki_drain
        self.final_flash_ki_drain = 20      # Ki drained per second while firing — tune independently of the beam
        self.big_bang_kamehameha_ki_drain = 20  # Ki drained per second while firing — tune independently of the beam
        self.melee_duration = 0.3

        # Charged Melee — holding E (rather than tapping it) rolls the
        # normal melee swing into a wind-up once it finishes: frame 0 of
        # charged_melee.png held while the sprite glows white (see
        # update_charged_melee_charge(), draw()'s flash_white pass), then
        # either a forward lunge (same shape as Dragon Fist's opening
        # lunge — see _advance_charged_melee_lunge) or a rooted spin (no
        # movability, like the energy sword spin — can_act()/can_move()
        # already block movement via is_attacking, so no extra code is
        # needed for that case), depending on charged_melee_style. That's
        # set per-character from the character creator (see
        # Game._reload_attack_config). Whichever style plays, it's the
        # SAME charged_melee.png sheet continuing past frame 0 — no
        # separate charge/action effect objects. Hits during the action
        # are just regular MeleeAttack instances spawned every
        # charged_melee_hit_interval seconds (see
        # pop_pending_charged_melee_hit()), reusing melee's existing
        # collision/sfx/cleanup pipeline in game.py wholesale instead of a
        # bespoke hitbox.
        #
        # The charge itself is release-driven, not a timer: the wind-up
        # animation loops for as long as E stays held, and the white
        # overlay ramps 0 -> full opacity over charged_melee_charge_required
        # seconds. Letting go of E before that overlay has reached full
        # opacity at least once just cancels the charge (same as before).
        # Once it HAS reached full opacity once, charged_melee_ready flips
        # on for good and the overlay starts breathing (full -> 0 -> full,
        # see charged_melee_pulse_period) so it's obvious the charge is
        # "banked" — letting go of E any time after that is what actually
        # triggers the lunge/spin via release_charged_melee(). There is no
        # auto-fire: holding past the first peak just keeps it looping.
        self.is_e_pressed = False
        self.is_charging_melee = False
        # Gate checked by start_charging_melee() below — set True/False at
        # runtime by Game._handle_charged_melee_action() (the 'charged_melee'
        # event action, mode 'add'/'remove'), and reset to True on every
        # character load/switch by Game._reload_attack_config(). Defaulted
        # True here too so a Player used before that reload runs (or in a
        # context that never calls it) still charges normally, matching
        # charged_melee_style's "always available unless told otherwise" default.
        self.charged_melee_enabled = True
        self.charged_melee_charge_time = 0.0
        self.charged_melee_charge_required = 0.2    # seconds to first full-opacity peak
        self.charged_melee_pulse_period = 0.3        # seconds per breathe cycle once ready
        self.charged_melee_flash_amount = 0.0        # 0.0-1.0 current white-overlay opacity
        self.charged_melee_ready = False              # True once the overlay has peaked once — release now fires the attack instead of cancelling
        self.is_charged_melee_active = False         # True through the whole lunge/spin action
        self.charged_melee_style = 'lunge'           # 'lunge' | 'spin'
        self.charged_melee_lunge_duration = 0.3      # seconds
        self.charged_melee_spin_duration = 1.0       # seconds
        self.charged_melee_action_timer = 0.0
        self.charged_melee_follow_speed = 50        # world units/sec while lunging
        self.charged_melee_hit_interval = 0.2        # seconds between hit ticks during the action
        self.charged_melee_hit_timer = 0.0
        self.pending_charged_melee_hit = False

        # Blast is queued here and spawned once the kiblast animation finishes
        self.pending_blast = None

        # Set when Q is tapped again while a blast throw is still mid-animation
        # (can_act() is False then, so shoot_blast() would otherwise just no-op
        # and eat the input). Consumed by _advance_blast_or_idle() alongside
        # is_q_pressed, so rapidly spamming Q chains into the same barrage that
        # holding Q down does, instead of dropping taps that land mid-throw.
        self.blast_input_buffered = False

        # Ultra Volleyball reuses the same kiblast throw animation as a
        # regular blast (see shoot_ultra_volleyball()) but is tracked with
        # its own pending/current fields so it doesn't collide with a
        # regular blast's own pending_blast bookkeeping — see the
        # 'kiblast' branch in update() below, which advances both
        # independently off the same animation frame.
        self.pending_ultra_volleyball = None
        self.ultra_volleyball_ki_cost = 25

        # While Q is held, hold-fire animations alternate frame 2 / frame 1.
        # Reset to 2 so the first hold-fire shot after the initial 0->1 throw
        # always starts on frame 2 (see _advance_blast_or_idle).
        self._blast_hold_frame = 2

        # Set by _advance_blast_or_idle() when chaining into another hold-fire
        # shot. Deliberately NOT the same variable as pending_blast — arming
        # pending_blast immediately would overwrite the 'ready' flag from the
        # shot that just finished before game.py has a chance to read it and
        # spawn its projectile. Consumed one frame later, at the top of update().
        self._queue_next_pending = False

        # -----------------------------------------------------------------
        # Double-tap detection for dashes / special inputs
        # -----------------------------------------------------------------
        self.last_key_press = {}
        self.double_tap_window = 0.3        # Seconds between taps that count as a double-tap

        # -----------------------------------------------------------------
        # Collision hitbox — smaller than the visual sprite
        # -----------------------------------------------------------------
        self.hitbox_width = 18
        self.hitbox_height = 10

        # Add dedicated wall-collision size:
        self.wall_hitbox_width = 18
        self.wall_hitbox_height = 14
        self.wall_hitbox_offset_y = 10

        # Per-direction hitbox offsets so the hitbox sits at the player's feet
        self.hitbox_offsets = {
            'up':    {'x':  0, 'y': -2},
            'down':  {'x':  0, 'y': 14},
            'left':  {'x':  0, 'y': 14},
            'right': {'x':  0, 'y': 14},
        }

        # -----------------------------------------------------------------
        # Damage knockback — physics-based, cleared when the hurt animation ends
        # -----------------------------------------------------------------
        self.is_knocked_back = False
        self.knockback_timer = 0
        self.knockback_duration = 0.4
        self.knockback_velocity_x = 0
        self.knockback_velocity_y = 0

        # Brief invulnerability window after taking a hit (i-frames)
        self.invulnerable = False
        self.invulnerable_timer = 0
        self.invulnerable_duration = 0.5

        # Stores the last damage value that actually landed (after i-frame checks),
        # so game.py can spawn a popup without modifying take_damage's return type
        self.last_damage_taken = 0

        # -----------------------------------------------------------------
        # Collision knockback — triggered when the player walks into a wall
        # at speed (separate from damage knockback so they don't interfere)
        # -----------------------------------------------------------------
        self.is_collision_knockback = False
        self.collision_knockback_timer = 0
        self.collision_knockback_duration = 0.4
        self.collision_knockback_velocity_x = 0
        self.collision_knockback_velocity_y = 0
        self.collision_knockback_strength = 400
        # Cooldown after knockback ends so holding the key doesn't immediately
        # re-trigger another knockback on the very next frame.
        self._knockback_cooldown       = 0.0
        self._knockback_cooldown_dur   = 0.25   # seconds before knockback can fire again
        # Set by move() each frame so game.py knows which axes were blocked
        # by obstacles — used to trigger running knockback correctly.
        self._blocked_x = False
        self._blocked_y = False

        self.last_move_direction = {'dx': 0, 'dy': 0}  # Most recent input vector

        # -----------------------------------------------------------------
        # Boundary/wall bounce tracking for horizontal attacks
        # After 3 consecutive wall bounces the next hit redirects downward
        # to prevent the player getting pinned in a corner.
        # -----------------------------------------------------------------
        self.horizontal_boundary_hits = 0
        self.last_knockback_hit_boundary = False

        # Q is the beam charge/fire button — we track press state directly
        self.is_q_pressed = False

        # Transition lock — set externally during room-change animations
        self.is_transitioning = False

        # -----------------------------------------------------------------
        # World-map jump sequence
        # Started by game.py when the player interacts with a world-map object.
        # Phase 1 (pre_move): the map_jump animation plays from frame 1.
        # Phase 2 (moving):   on frame 2 the sprite freezes and the player
        #                     drifts upward off-screen at map_jump_speed.
        # on_map_jump_exit is called once the player is fully out of view.
        # -----------------------------------------------------------------
        self.is_map_jumping          = False
        self.map_jump_moving         = False   # True once the upward drift begins
        self.map_jump_timer          = 0.0     # Elapsed time since jump started
        # Seconds each frame is shown.  Tune to match the actual frame rate of
        # map_jump.png (default assumes ~6 fps, i.e. 0.18 s/frame).
        self._MAP_JUMP_FRAME_DURATION = 0.18
        self.map_jump_speed           = 160     # World units per second (upward)
        self.on_map_jump_exit         = None   # Callback: fired when fully off-screen
        # Populated by start_map_jump() — raw pygame surfaces, one per frame.
        self._map_jump_frames         = []
        self._map_jump_frame_idx      = 0
        self._map_jump_frame_timer    = 0.0

        # Injected by the room/game system after construction
        self.obstacles = []

        # Updated to the current room's dimensions each time the player moves
        self.current_room_width = WORLD_WIDTH
        self.current_room_height = WORLD_HEIGHT

    # =========================================================================
    # Queries
    # =========================================================================

    def get_sort_key(self):
        """Depth-sort key: draw_layer first, then feet position (y + height/2)."""
        return (self.draw_layer, self.y + self.height // 2)

    def is_transformed(self):
        """True if the TransformationSystem reports we are in a transformed state."""
        return self.transformation and self.transformation.is_transformed

    def has_free_ki(self):
        """True while transformed AND that transformation's charge bar is
        enabled — the signal every attack cost-check/drain below uses to
        skip spending normal ki. A transformation with its charge bar
        disabled (see character_creator.py's "Show Charge Bar" checkbox)
        deliberately does NOT get this perk: the player is meant to read as
        just using their normal ki bar the whole time for that form, so
        attacks cost ki exactly as if untransformed.
        """
        if not self.is_transformed():
            return False
        return getattr(self.transformation, 'current_transform_ki_bar_enabled', True)

    def can_act(self):
        """False while locked in an animation, transitioning, or knocked back."""
        if self.is_dead:
            return False
        if self.is_transitioning:
            return False
        if self.is_map_jumping:
            return False
        if self.is_collision_knockback:
            return False
        if self.transformation and not self.transformation.can_player_act():
            return False
        return not (self.is_attacking or self.is_punching or self.is_charging_beam
                    or self.is_firing_beam or self.is_charging_kamekameha
                    or self.is_firing_kamekameha or self.is_charging_banshee_blast
                    or self.is_firing_banshee_blast or self.is_charging_final_flash
                    or self.is_firing_final_flash or self.is_charging_flame_kamehameha
                    or self.is_firing_flame_kamehameha
                    or self.is_charging_big_bang_kamehameha
                    or self.is_firing_big_bang_kamehameha
                    or self.is_charging_genkidama
                    or self.is_firing_genkidama or self.is_charging_masenko
                    or self.is_charging_sword or self.is_spinning_sword
                    or self.is_using_dragon_fist
                    or self.is_targeting_it or self.is_teleporting_it
                    or self.is_charging_burning
                    or self.is_casting_ghost_kamikaze or self.is_holding_ghost_kamikaze
                    or (self.current_ghost_kamikaze is not None and self.current_ghost_kamikaze.active)
                    or self.is_charging_big_bang_attack or self.is_firing_big_bang_attack
                    or self.is_knocked_back or self.is_blocking
                    or self.is_picking_up_item)

    def can_move(self):
        """False during collision knockback or whenever can_act() returns False.

        Energy sword spin and Dragon Fist are deliberate exceptions:
        can_act() is False during both (you can't start another attack
        mid-spin or mid-fist), but the player can still move — for the
        sword that means walking around at reduced speed; for Dragon
        Fist, move() redirects the input entirely into steering the fist's
        head instead of moving the player (see _move_dragon_fist_head).
        """
        if self.is_dead:
            return False
        if self.is_transitioning:
            return False
        if self.is_map_jumping:
            return False
        if self.is_collision_knockback:
            return False
        if self.is_spinning_sword:
            return not self.is_knocked_back
        if self.is_using_dragon_fist:
            return not self.is_knocked_back
        if self.is_casting_ghost_kamikaze:
            # Deliberate exception, same shape as sword-spin/dragon-fist
            # above: can_act() is False during the cast (can't start
            # another attack mid-cast), but moving is allowed and cancels
            # the attack outright instead of letting it keep forming up
            # (see move()'s cancel() call below).
            return not self.is_knocked_back
        if self.is_holding_ghost_kamikaze:
            # Deliberate exception, same shape as sword-spin/dragon-fist
            # above: can_act() is False during the hold (can't start
            # another attack), but moving is allowed and — per the
            # spec — cuts the hold short, launching the ghosts
            # immediately (see move()'s launch_now() call below).
            return not self.is_knocked_back
        if (self.current_ghost_kamikaze is not None and self.current_ghost_kamikaze.active
                and not self.is_casting_ghost_kamikaze and not self.is_holding_ghost_kamikaze):
            # Same shape as the exceptions above: the ghosts are still
            # resolving in the background (traveling to a target, or
            # playing out their no-target end), which blocks can_act()
            # from starting a new attack, but the player isn't locked
            # into anything themselves and should still be able to walk.
            return not self.is_knocked_back
        return self.can_act()

    def get_current_ki_cost(self):
        """Ki cost for the current attack (0 while transformed — free attacks)."""
        return 0 if self.has_free_ki() else self.blast_ki_cost

    def enter_idle(self):
        """Return to standing idle and (re)start the stand-still timer.

        Centralized so every place that snaps back to idle also resets the
        idle-wait clock — otherwise stopping right as the wait animation was
        about to trigger would carry a stale timer into the next stand-still.
        """
        self.sprite.set_animation('idle', self.direction)
        self.current_animation_state = 'idle'
        self.idle_timer = 0.0

    def die(self):
        """Enter the death state: lose all control and play death.png once,
        then hold on its last frame (same as any other non-looping animation
        — see e.g. energy_punch). Called from take_damage() the instant hp
        first reaches 0; is_dead being True is what makes can_act()/
        can_move() start returning False. game.py owns everything after
        that (the hold, the fade-to-black, and the game-over box) — see
        Game._update_death_sequence.
        """
        if self.is_dead:
            return
        self.is_dead = True

        # Cancel any in-progress knockback/attack state so death always
        # renders cleanly regardless of what the player was doing when hit.
        self.is_knocked_back          = False
        self.is_collision_knockback   = False
        self.is_attacking             = False
        self.is_blocking              = False

        self.sprite.set_animation('death', self.direction)
        self.current_animation_state = 'death'

    def start_pickup_item(self):
        """Enter the item-pickup pose (pickup_item.png) — used when opening
        a chest, held for PICKUP_ITEM_DURATION seconds before automatically
        returning to idle (see the 'pickup_item' branch in update()).
        can_act() is False the whole time via is_picking_up_item, same
        lockout shape as melee/hurt/etc.

        pickup_item.png only has a down-facing pose (no left/right/up
        variants), so this always plays the 'down' row regardless of
        self.direction — but self.direction itself is left untouched, so
        enter_idle() (called once the pose finishes) resumes facing
        whichever way the player was actually facing before the chest was
        opened, not down.

        game.py pairs this with an item icon floating up above the player;
        it polls is_picking_up_item to know when the pose (and therefore
        the float) has finished, so it can then grant the loot and show the
        reward dialogue — see _handle_interact's chest branch and
        _update_chest_pickup.
        """
        self.is_picking_up_item = True
        self.pickup_item_timer = 0.0
        if self.sprite.has_animation('pickup_item', 'down'):
            self.sprite.set_animation('pickup_item', 'down')
        self.current_animation_state = 'pickup_item'

    # =========================================================================
    # Collision helpers
    # =========================================================================

    def get_collision_rect(self):
        """Return the player's directional hitbox in world coordinates."""
        offset = self.hitbox_offsets.get(self.direction, {'x': 0, 'y': 0})
        left = self.x + offset['x'] - self.hitbox_width // 2
        top  = self.y + offset['y'] - self.hitbox_height // 2
        return pygame.Rect(left, top, self.hitbox_width, self.hitbox_height)

    @property
    def obstacles(self):
        return self._obstacles

    # See Enemy._OBSTACLE_GRID_CELL — same grid, same reasoning, kept as its
    # own copy here since Player and Enemy don't share a base class.
    _OBSTACLE_GRID_CELL = 128

    @obstacles.setter
    def obstacles(self, value):
        """game.py hands the player the same shared obstacle list once per
        room load (see Game._assign_obstacles) — not per frame. Classifying
        each obstacle (figuring out which of the three collision "kinds" it
        is, plus an approximate center/radius for a broad-phase reject) used
        to happen from scratch on every single check_collision_with_obstacles
        call; doing it once here instead — same approach already used by
        Enemy.obstacles, see its docstring — lets the hot path below reject
        far-away obstacles with one cheap squared-distance compare before
        touching hasattr(), Rect construction, or colliderect at all. With a
        heavy room's obstacle list running into the hundreds and this called
        several times a frame (move(), charged melee, dragon fist, ghost
        kamikaze, beam knockback...), this is what keeps it affordable.

        Also buckets obstacles into a uniform spatial grid (self._obstacle_grid)
        for the same reason Enemy does — see Enemy.obstacles' docstring. This
        is what keeps a heavy room's several-hundred-obstacle list from being
        scanned in full on every single call; see
        _nearby_prepared_obstacles/check_collision_with_obstacles below.
        """
        self._obstacles = value
        self._prepared_obstacles = [self._classify_obstacle(o) for o in value]

        cell = self._OBSTACLE_GRID_CELL
        grid = {}
        for entry in self._prepared_obstacles:
            _, kind, approx_x, approx_y, reject_radius = entry
            if kind == 'skip':
                continue
            min_cx = int((approx_x - reject_radius) // cell)
            max_cx = int((approx_x + reject_radius) // cell)
            min_cy = int((approx_y - reject_radius) // cell)
            max_cy = int((approx_y + reject_radius) // cell)
            for gx in range(min_cx, max_cx + 1):
                for gy in range(min_cy, max_cy + 1):
                    grid.setdefault((gx, gy), []).append(entry)
        self._obstacle_grid = grid

    @staticmethod
    def _classify_obstacle(obstacle):
        """One-time per-obstacle setup — see Enemy._classify_obstacle, which
        this mirrors exactly (same obstacle kinds, same precedence, same
        approximate center/radius). Positions are assumed static for the
        lifetime of a room's obstacle list — true for walls, stones, gates,
        chests, and decorations alike."""
        if hasattr(obstacle, 'id') and obstacle.id == 'collision_wall':
            cx = obstacle.x + obstacle.width / 2
            cy = obstacle.y + obstacle.height / 2
            radius = math.hypot(obstacle.width / 2, obstacle.height / 2)
            return (obstacle, 'wall', cx, cy, radius)

        if hasattr(obstacle, 'solid') and hasattr(obstacle, 'active'):
            radius = math.hypot(obstacle.width / 2, obstacle.height / 2)
            return (obstacle, 'stone', obstacle.x, obstacle.y, radius)

        if hasattr(obstacle, 'get_collision_rect'):
            rect = obstacle.get_collision_rect()
            if rect is not None:
                cx, cy = rect.centerx, rect.centery
                radius = math.hypot(rect.width / 2, rect.height / 2)
            else:
                # Not currently solid (e.g. inactive) — fall back to the
                # object's own position with a generous placeholder radius
                # so a future non-None rect still gets caught by the
                # broad-phase check rather than silently ignored.
                cx = getattr(obstacle, 'x', 0)
                cy = getattr(obstacle, 'y', 0)
                radius = 64
            return (obstacle, 'generic', cx, cy, radius)

        return (obstacle, 'skip', 0, 0, 0)

    def _nearby_prepared_obstacles(self, cx, cy, radius):
        """Return the prepared-obstacle entries whose bounding box could
        possibly reach a query centered at (cx, cy) with the given radius —
        see Enemy._nearby_prepared_obstacles, which this mirrors exactly.
        """
        grid = getattr(self, '_obstacle_grid', None)
        if not grid:
            return self._prepared_obstacles

        cell = self._OBSTACLE_GRID_CELL
        min_cx = int((cx - radius) // cell)
        max_cx = int((cx + radius) // cell)
        min_cy = int((cy - radius) // cell)
        max_cy = int((cy + radius) // cell)

        if min_cx == max_cx and min_cy == max_cy:
            return grid.get((min_cx, min_cy), ())

        seen_ids = set()
        result = []
        for gx in range(min_cx, max_cx + 1):
            for gy in range(min_cy, max_cy + 1):
                for entry in grid.get((gx, gy), ()):
                    obstacle_id = id(entry[0])
                    if obstacle_id not in seen_ids:
                        seen_ids.add(obstacle_id)
                        result.append(entry)
        return result

    def check_collision_with_obstacles(self, new_x, new_y):
        """True if the wall hitbox at (new_x, new_y) overlaps any obstacle.

        Broad-phase first (mirrors Enemy.check_collision_with_obstacles):
        each obstacle was pre-classified with an approximate center/radius
        once, in the obstacles setter above, so a cheap squared-distance
        compare rejects anything clearly too far away before paying for
        hasattr(), Rect construction, or colliderect. Candidates now come
        from the spatial grid (_nearby_prepared_obstacles) instead of the
        room's full obstacle list, for the same reason as Enemy's version —
        see that method's docstring.
        """
        half_w = self.wall_hitbox_width // 2
        half_h = self.wall_hitbox_height // 2
        self_reject_radius = math.hypot(half_w, half_h)

        query_y = new_y + self.wall_hitbox_offset_y
        temp_rect = None  # built lazily, only once we have a real candidate

        candidates = self._nearby_prepared_obstacles(new_x, query_y, self_reject_radius)
        for obstacle, kind, approx_x, approx_y, reject_radius in candidates:
            dx = approx_x - new_x
            dy = approx_y - (new_y + self.wall_hitbox_offset_y)
            max_dist = self_reject_radius + reject_radius
            if dx * dx + dy * dy > max_dist * max_dist:
                continue

            if kind == 'wall':
                if not getattr(obstacle, 'active', True):
                    continue
                obstacle_rect = pygame.Rect(obstacle.x, obstacle.y, obstacle.width, obstacle.height)

            elif kind == 'stone':
                if not obstacle.active or not obstacle.solid:
                    continue
                obstacle_rect = pygame.Rect(
                    obstacle.x - obstacle.width // 2,
                    obstacle.y - obstacle.height // 2,
                    obstacle.width,
                    obstacle.height,
                )

            elif kind == 'generic':
                obstacle_rect = obstacle.get_collision_rect()
                if obstacle_rect is None:
                    continue

            else:
                continue

            if temp_rect is None:
                temp_rect = pygame.Rect(
                    new_x - self.wall_hitbox_width // 2,
                    new_y + self.wall_hitbox_offset_y - self.wall_hitbox_height // 2,
                    self.wall_hitbox_width,
                    self.wall_hitbox_height,
                )

            if temp_rect.colliderect(obstacle_rect):
                return True

        return False

    def _get_spawn_offset(self):
        """Return (offset_x, offset_y) for projectile/beam spawn based on facing direction."""
        return _DIRECTION_SPAWN_OFFSETS.get(self.direction, (0, 0))

    # =========================================================================
    # Movement
    # =========================================================================

    def move(self, dx, dy, is_running, world_width, world_height):
        """Apply one frame of directional input.

        dx/dy are the raw -1/0/1 input axes (not yet scaled by speed). Movement
        is resolved per-axis so the player slides along walls on a diagonal
        instead of being fully stopped by a corner. Updates facing direction
        (diagonals keep the last cardinal facing to avoid sprite flicker),
        picks walk/run animation, and sets self._blocked_x/_blocked_y so
        game.py can trigger collision knockback when a real wall stops motion.
        No-ops entirely if can_move() is False (mid-attack, knocked back, etc.).
        """
        if not self.can_move():
            return

        if self.is_casting_ghost_kamikaze:
            # Player moved mid-cast — cancels the attack outright rather
            # than letting it keep forming up (see GhostKamikazeAttack.
            # cancel(), which destroys every ghost created so far,
            # however many loops have completed). stop_ghost_kamikaze()
            # handles resetting this player's own casting flags/
            # animation state back to idle; it deliberately doesn't touch
            # current_ghost_kamikaze itself, which is exactly why cancel()
            # needs to be called on it here first. Falls through to
            # ordinary movement below rather than returning, same as the
            # is_holding_ghost_kamikaze case right below.
            if self.current_ghost_kamikaze:
                self.current_ghost_kamikaze.cancel()
            self.stop_ghost_kamikaze()

        if self.is_holding_ghost_kamikaze:
            # Player moved during the hold — per the spec, this cuts the
            # wait short and sends the ghosts off immediately (same
            # resolution GhostKamikazeAttack would reach on its own once
            # hold_duration elapses — see its launch_now()). Falls
            # through to ordinary movement below rather than returning,
            # unlike Dragon Fist's redirect, since there's nothing left
            # for this input to steer.
            if self.current_ghost_kamikaze:
                self.current_ghost_kamikaze.launch_now()
            self.is_holding_ghost_kamikaze = False

        # Cache room dimensions so knockback bounds checks stay in sync
        self.current_room_width = world_width
        self.current_room_height = world_height

        # Track the most recent input vector for collision-knockback direction
        self.last_move_direction['dx'] = dx
        self.last_move_direction['dy'] = dy

        if self.is_using_dragon_fist:
            # Movement input steers the fist's head instead of the player
            # (see _move_dragon_fist_head) — the player's own position is
            # dragged along separately, slowly, and horizontally-only, in
            # update_dragon_fist(). Facing/sprite/animation are
            # deliberately left untouched here: the player stays on the
            # 'dragon_fist' pose for the whole hold no matter which way
            # the head gets steered.
            self._move_dragon_fist_head(dx, dy)
            return

        if dx != 0 or dy != 0:
            if dx != 0 and dy == 0:
                # Pure horizontal input — update facing direction
                self.direction = 'right' if dx > 0 else 'left'
            elif dy != 0 and dx == 0:
                # Pure vertical input — update facing direction
                self.direction = 'down' if dy > 0 else 'up'
            else:
                # Diagonal: keep the current facing to avoid sprite flicker,
                # but ONLY if that facing still corresponds to one of the two
                # axes currently held. Otherwise the player can end up
                # sprinting in a direction that has nothing to do with their
                # old facing (e.g. release left, tap up+right, and keep
                # running to the upper-right while still visually facing/
                # animating left). When the old facing no longer matches
                # either held axis, re-derive it — horizontal wins ties,
                # consistent with how horizontal is treated as the primary
                # facing axis elsewhere (flips, spawn offsets, _OCTANT_TO_CARDINAL).
                facing_matches_input = (
                    (self.direction == 'left'  and dx < 0) or
                    (self.direction == 'right' and dx > 0) or
                    (self.direction == 'up'    and dy < 0) or
                    (self.direction == 'down'  and dy > 0)
                )
                if not facing_matches_input:
                    self.direction = 'right' if dx > 0 else 'left'

        self.is_running = is_running
        if self.is_spinning_sword:
            # Slower than a normal walk, and deliberately ignores is_running —
            # spinning a blade around yourself isn't compatible with sprinting,
            # on top of already being capped out of running by game.py.
            current_speed = self.speed * self.sword_spin_move_speed_mult
        else:
            current_speed = self.run_speed if is_running else self.speed

        # Reset per-frame block flags — game.py reads these to trigger knockback.
        self._blocked_x = False
        self._blocked_y = False

        # Apply X and Y axes independently so the player slides along walls
        # instead of either tunnelling through corners or being fully blocked
        # when moving diagonally.  Each axis is only committed if it doesn't
        # produce a new obstacle overlap.
        if dx != 0:
            new_x = self.x + dx * current_speed
            new_x = max(self.width // 2, min(new_x, world_width - self.width // 2))
            if not self.check_collision_with_obstacles(new_x, self.y):
                self.x = new_x
            else:
                self._blocked_x = True

        if dy != 0:
            new_y = self.y + dy * current_speed
            new_y = max(self.height // 2, min(new_y, world_height - self.height // 2))
            if not self.check_collision_with_obstacles(self.x, new_y):
                self.y = new_y
            else:
                self._blocked_y = True

        anim = 'run' if is_running else 'walk'
        self.sprite.set_animation(anim, self.direction)
        self.current_animation_state = anim

    def _move_dragon_fist_head(self, dx, dy):
        """Steer the Dragon Fist head by one frame of raw input, then clamp
        it back into its current leash box — recomputed every call since
        the box's "back" edge tracks the player's own position (see
        DragonFistAttack.clamp_head_to_leash / ._leash_bounds).

        No-ops during the initial 'shooting' launch or the release
        'retracting' sweep — the player only gets manual control once the
        head has fully extended and is waiting on input.
        """
        fist = self.current_dragon_fist
        if not fist or fist.state != 'controlled':
            return
        fist.head_x += dx * self.dragon_fist_head_speed
        fist.head_y += dy * self.dragon_fist_head_speed
        fist.clamp_head_to_leash(self.x, self.y)

    def tick_footsteps(self, dt):
        """Advance the sprint-footstep timer. Returns True the frame a footstep should play.

        Walking is intentionally silent — only sprinting (is_running) ticks
        this at all. Resets whenever the player stops running so the first
        footstep after starting a new sprint fires immediately. Also silent
        during collision knockback, since a wall-bounce isn't a footstep.
        """
        if not self.is_running or self.is_collision_knockback:
            self.footstep_timer = 0.0
            return False

        self.footstep_timer -= dt
        if self.footstep_timer <= 0:
            self.footstep_timer = self.footstep_interval
            return True
        return False

    def start_collision_knockback(self, collision_direction_x, collision_direction_y):
        """Bounce the player back after walking into a solid obstacle at speed."""
        self.is_collision_knockback = True
        self.collision_knockback_timer = self.collision_knockback_duration

        # Push in the opposite direction of travel
        self.collision_knockback_velocity_x = -collision_direction_x * self.collision_knockback_strength
        self.collision_knockback_velocity_y = -collision_direction_y * self.collision_knockback_strength

        # If an attack was in progress, cancel it — the hurt animation is about
        # to overwrite 'melee'/'kiblast', so is_attacking would never be cleared
        # by the animation-state machine and the player would be stuck forever.
        self.is_attacking = False
        self.pending_blast = None
        self.blast_input_buffered = False
        self.pending_ultra_volleyball = None
        self.is_charging_burning = False
        self.burning_charge_effect = None
        self.is_charging_melee = False
        self.is_charged_melee_active = False
        self.charged_melee_flash_amount = 0.0
        self.charged_melee_ready = False

        self.sprite.set_animation('hurt', self.direction)
        self.current_animation_state = 'hurt'

    # =========================================================================
    # Combat — attacking
    # =========================================================================

    def melee_attack(self):
        """Swing a melee attack. Returns a MeleeAttack object, or None if blocked."""
        if not self.can_act() or self.attack_cooldown > 0:
            return None

        self.is_attacking = True
        # No added cooldown here — is_attacking (via can_act()) already blocks
        # a new attack for the whole swing, so a melee-specific cooldown on
        # top of that just delays the *next* swing past when this one visibly
        # finishes. Leaving it at 0 lets melee -> melee chain back-to-back the
        # instant the animation completes, matching the original game.
        self.attack_cooldown = 0
        self.sprite.set_animation('melee', self.direction)
        self.current_animation_state = 'melee'

        melee = MeleeAttack(self.x, self.y, self.direction)
        melee.owner = self
        return melee

    def start_charging_melee(self):
        """Begin the charged-melee wind-up: hold frame 0 of
        charged_melee.png, looping, for as long as E stays held, while the
        white overlay ramps up (see update_charged_melee_charge()).
        Letting go of E is what triggers release_charged_melee() — but
        only once the overlay has reached full opacity at least once (see
        charged_melee_ready); letting go earlier cancels instead. is_attacking
        stays True the whole time — same can_act()/can_move() lockout as
        the regular melee swing, just longer.

        Called from the 'melee' animation-state branch in update() when
        the normal swing finishes with E still held (see is_e_pressed).

        Gated on charged_melee_enabled (see the 'charged_melee' event
        action / Game._handle_charged_melee_action()) — if a trigger box
        has revoked the ability, this falls back to exactly what the
        'melee' branch does when E isn't held: end the swing and return
        to idle, rather than silently doing nothing and leaving
        is_attacking stuck True.
        """
        if not self.charged_melee_enabled:
            self.is_attacking = False
            self.enter_idle()
            return

        self.is_charging_melee = True
        self.charged_melee_charge_time = 0.0
        self.charged_melee_flash_amount = 0.0
        self.charged_melee_ready = False

        if self.sprite.has_animation('charged_melee_hold', self.direction):
            self.sprite.set_animation('charged_melee_hold', self.direction)
        self.current_animation_state = 'charged_melee_charge'

    def update_charged_melee_charge(self, dt):
        """Advance the white-overlay ramp/pulse and react to E's state.

        E released:
          - if the overlay has reached full opacity at least once
            (charged_melee_ready) -> release into the lunge/spin.
          - otherwise -> cancel back to idle early (mirrors the energy
            sword charge's early-release-cancels behavior).

        E still held: keep looping the wind-up animation and advance the
        overlay — ramping 0 -> 1 the first time, then breathing 1 -> 0 -> 1
        on repeat once ready, with no auto-fire either way.
        """
        if not self.is_e_pressed:
            if self.charged_melee_ready:
                self.release_charged_melee()
            else:
                self.cancel_charging_melee()
            return

        self.charged_melee_charge_time += dt

        if not self.charged_melee_ready:
            # First ramp: 0 -> 1 opacity over charged_melee_charge_required
            # seconds. Reaching 1.0 here is the ONE-TIME "fully charged"
            # moment — from here on, letting go of E fires the attack.
            progress = self.charged_melee_charge_time / self.charged_melee_charge_required
            if progress >= 1.0:
                self.charged_melee_flash_amount = 1.0
                self.charged_melee_ready = True
            else:
                self.charged_melee_flash_amount = progress
        else:
            # Already peaked once — keep the overlay breathing (full -> 0 ->
            # full) so it stays obvious the charge is ready while E is
            # still held, for however long the player keeps holding it.
            period = self.charged_melee_pulse_period
            half = period / 2
            phase = (self.charged_melee_charge_time - self.charged_melee_charge_required) % period
            if phase <= half:
                self.charged_melee_flash_amount = 1.0 - (phase / half)
            else:
                self.charged_melee_flash_amount = (phase - half) / half

    def cancel_charging_melee(self):
        """E released before the overlay ever reached full opacity — drop
        the charge and return to idle."""
        self.is_charging_melee = False
        self.is_attacking = False
        self.charged_melee_flash_amount = 0.0
        self.charged_melee_ready = False
        if self.current_animation_state == 'charged_melee_charge':
            self.enter_idle()

    def release_charged_melee(self):
        """E released after the overlay reached full opacity at least once
        — play the rest of charged_melee.png (frame 1 onward) while either
        lunging forward or spinning in place, per charged_melee_style. No
        movement code is needed for the spin case: is_attacking stays
        True, so can_act()/can_move() already block the player from
        moving on their own — same "rooted" feel as the energy sword spin
        has without needing a can_move() exception, unlike Dragon
        Fist/the sword spin which explicitly opt back INTO movement.
        """
        self.is_charging_melee = False
        self.is_charged_melee_active = True
        self.charged_melee_flash_amount = 0.0
        self.charged_melee_ready = False
        self.charged_melee_hit_timer = 0.0

        fallback_duration = (
            self.charged_melee_lunge_duration if self.charged_melee_style == 'lunge'
            else self.charged_melee_spin_duration
        )

        if self.sprite.has_animation('charged_melee_action', self.direction):
            self.sprite.set_animation('charged_melee_action', self.direction)
            # Size the action to however long charged_melee_action actually
            # takes to play through, rather than the flat lunge/spin
            # constants above — those are a fallback only, same convention
            # as beam_charge_required/sword_charge_required/etc. Without
            # this, a character whose charged_melee.png plays longer than
            # the fallback gets cut off and snapped back to idle mid-swing.
            self.charged_melee_action_timer = self.sprite.get_animation_duration(
                'charged_melee_action', self.direction) or fallback_duration
        else:
            self.charged_melee_action_timer = fallback_duration
        self.current_animation_state = 'charged_melee_action'

    def update_charged_melee_action(self, dt):
        """Advance the lunge/spin for its fixed duration, ticking off
        periodic melee hits along the way (see pop_pending_charged_melee_hit)."""
        if self.charged_melee_style == 'lunge':
            self._advance_charged_melee_lunge(dt)

        self.charged_melee_hit_timer += dt
        if self.charged_melee_hit_timer >= self.charged_melee_hit_interval:
            self.charged_melee_hit_timer -= self.charged_melee_hit_interval
            self.pending_charged_melee_hit = True

        self.charged_melee_action_timer -= dt
        if self.charged_melee_action_timer <= 0:
            self.stop_charged_melee()

    def _advance_charged_melee_lunge(self, dt):
        """Carry the player forward along their facing at
        charged_melee_follow_speed, respecting world bounds/obstacles —
        same shape as _advance_dragon_fist_lunge, just without a fist
        assembly that needs to be translated along for the ride."""
        dxu, dyu = _DRAGON_FIST_DIRECTION_UNIT.get(self.direction, (0, 0))
        step = self.charged_melee_follow_speed * dt

        if dxu != 0:
            new_x = self.x + dxu * step
            new_x = max(self.width // 2, min(new_x, self.current_room_width - self.width // 2))
            if not self.check_collision_with_obstacles(new_x, self.y):
                self.x = new_x
        if dyu != 0:
            new_y = self.y + dyu * step
            new_y = max(self.height // 2, min(new_y, self.current_room_height - self.height // 2))
            if not self.check_collision_with_obstacles(self.x, new_y):
                self.y = new_y

    def stop_charged_melee(self):
        """End the lunge/spin (naturally, on timeout — or externally, e.g.
        we got hit or killed mid-attack) and return to idle."""
        self.is_charging_melee = False
        self.is_charged_melee_active = False
        self.is_attacking = False
        self.charged_melee_flash_amount = 0.0
        self.charged_melee_ready = False
        if self.current_animation_state == 'charged_melee_action':
            self.enter_idle()

    def pop_pending_charged_melee_hit(self):
        """Consume and return a fresh MeleeAttack at the player's current
        position/facing if a charged-melee hit tick fired this frame (see
        update_charged_melee_action()), or None otherwise.

        Reuses MeleeAttack wholesale — same collision/sfx/cleanup pipeline
        in game.py as a regular tap-melee swing — rather than a bespoke
        persistent hitbox, since a periodic instant check is all either
        the lunge or the spin actually needs.
        """
        if not self.pending_charged_melee_hit:
            return None
        self.pending_charged_melee_hit = False
        hit = MeleeAttack(self.x, self.y, self.direction)
        hit.owner = self
        hit.hit_something = False
        return hit

    def shoot_blast(self):
        """Queue a ki blast — the projectile spawns once the kiblast animation finishes."""
        if not self.can_act() or self.attack_cooldown > 0:
            # Still mid-throw from a previous blast? Buffer this tap so a
            # rapid spam of Q keeps the barrage going once the current throw
            # finishes — mirrors the continue-firing behaviour
            # _advance_blast_or_idle() already gives when Q is simply held.
            if (self.is_attacking and self.ki_attack_mode == 'blast'
                    and self.current_animation_state in ('kiblast', 'kiblast_hold')):
                self.blast_input_buffered = True
            return

        ki_cost = self.get_current_ki_cost()
        if self.ki >= ki_cost:
            self.ki -= ki_cost
            self.is_attacking = True
            self.attack_cooldown = 0.5
            self.sprite.set_animation('kiblast', self.direction)
            self.current_animation_state = 'kiblast'
            self.pending_blast = True  # Checked in update(); set to 'ready' when animation ends

    def shoot_ultra_volleyball(self):
        """Queue an Ultra Volleyball — reuses the exact same kiblast
        wind-up/throw animation as a regular blast (see shoot_blast()
        above); the fixed 3-segment UltraVolleyballAttack spawns once
        frame 1 of that animation plays, same release-frame timing a
        regular blast uses, tracked separately via
        pending_ultra_volleyball so the two don't stomp each other."""
        if not self.can_act() or self.attack_cooldown > 0:
            return

        if self.ki >= self.ultra_volleyball_ki_cost:
            self.ki -= self.ultra_volleyball_ki_cost
            self.is_attacking = True
            self.attack_cooldown = 0.5
            self.sprite.set_animation('kiblast', self.direction)
            self.current_animation_state = 'kiblast'
            self.pending_ultra_volleyball = True  # Checked in update(); set to 'ready' on frame 1

    def get_blast_spawn_position(self):
        """Return (x, y) world position where the blast projectile should appear."""
        ox, oy = self._get_spawn_offset()
        return self.x + ox, self.y + oy

    def _can_continue_blast_hold(self):
        """Like can_act(), but ignores is_attacking — we're evaluating this from
        inside the attack's own finish handler, so is_attacking is still True."""
        if self.is_transitioning or self.is_map_jumping or self.is_collision_knockback:
            return False
        if self.transformation and not self.transformation.can_player_act():
            return False
        return not self.is_knocked_back

    def _advance_blast_or_idle(self):
        """Called when a kiblast (or kiblast-hold) throw animation finishes.

        If Q is still held, we're in blast mode, and there's enough ki for
        another shot, keep firing — alternating the hold animation between
        frame 2 and frame 1 each time, spawning a blast on every switch.
        Otherwise, drop back to idle.
        """
        ki_cost = self.get_current_ki_cost()

        # Keep the barrage going either because Q is still physically held
        # down, or because it was tapped again mid-throw (spammed) and that
        # tap got buffered by shoot_blast() since can_act() was False at the
        # time. Consume the buffered tap now — it's a one-shot request.
        keep_firing = self.is_q_pressed or self.blast_input_buffered
        self.blast_input_buffered = False

        if (keep_firing and self._can_continue_blast_hold()
                and self.ki_attack_mode == 'blast' and self.ki >= ki_cost):
            # Use the CURRENT hold frame for this shot (2 on the first hold-fire
            # shot), then flip it for next time: 2, 1, 2, 1, ...
            next_frame = self._blast_hold_frame
            hold_anim = f'kiblast_hold{next_frame}'

            # Safety net: if this animation somehow isn't loaded, switching to it
            # would silently no-op and leave the sprite stuck on the already-
            # finished previous animation — which would retrigger this method
            # every single frame instead of once per cycle. Bail to idle instead.
            if not self.sprite.has_animation(hold_anim, self.direction):
                self.is_attacking = False
                self._blast_hold_frame = 2
                self.enter_idle()
                return

            self.ki -= ki_cost
            self.is_attacking = True
            self.attack_cooldown = 0.5
            self._blast_hold_frame = 1 if next_frame == 2 else 2
            self.sprite.set_animation(hold_anim, self.direction)
            self.current_animation_state = 'kiblast_hold'

            # pending_blast was already consumed by game.py (set 'ready' on frame 1
            # of the kiblast animation, before is_animation_finished() fired here),
            # so it's safe to arm the next blast immediately.
            self.pending_blast = True
        else:
            self.is_attacking = False
            self._blast_hold_frame = 2  # Reset so the next fresh press starts on frame 2
            self.enter_idle()

    def start_charging_beam(self):
        """Begin the beam charge animation. Returns True on success."""
        if not self.can_act():
            return False

        if self.ki > 0 or self.has_free_ki():
            self.is_charging_beam = True
            self.beam_charge_time = 0
            self.is_q_pressed = True
            self.sprite.set_animation('charge', self.direction)
            self.current_animation_state = 'charge'
            self.current_charge_effect = KamehamehaChargeEffect(self)
            # Sync the auto-fire threshold to however long the charge-up
            # sprite actually takes to play through all of its frames, so
            # the beam can never fire before that animation has finished.
            self.beam_charge_required = self.current_charge_effect.get_total_duration()
            return True

        return False

    def update_beam_charge(self, dt):
        """Advance the beam charge timer and auto-fire when fully charged."""
        if self.is_charging_beam:
            if self.current_charge_effect:
                self.current_charge_effect.update(dt)
            self.beam_charge_time += dt
            if self.beam_charge_time >= self.beam_charge_required and not self.is_firing_beam:
                self.fire_beam_auto()

    def fire_beam_auto(self):
        """Transition from charging to firing once the charge threshold is met."""
        if not (self.is_charging_beam and self.beam_charge_time >= self.beam_charge_required):
            return None

        self.is_charging_beam = False
        self.is_firing_beam = True
        self.beam_charge_time = 0
        self.current_charge_effect = None
        self.sprite.set_animation('firebeam', self.direction)
        self.current_animation_state = 'firebeam'

        # Spawn the beam slightly in front of the player based on facing direction
        ox, oy = self._get_spawn_offset()
        self.current_beam = BeamAttack(self.x + ox, self.y + oy, self.direction)
        return self.current_beam

    def stop_beam(self):
        """Cancel beam charging, or hand a firing beam off to its decay
        sweep instead of instantly removing it.

        current_beam is deliberately NOT cleared here when a beam is
        firing — it stays assigned (and keeps getting updated/drawn
        normally by whatever owns the render loop) until the beam's own
        decay sweep finishes and it marks itself inactive; update() below
        is what actually drops the reference at that point. Charging
        (no beam spawned yet) still clears immediately since there's
        nothing to decay.
        """
        self.is_charging_beam = False
        self.is_firing_beam = False
        self.beam_charge_time = 0
        self.is_q_pressed = False
        self.current_charge_effect = None

        if self.current_beam:
            self.current_beam.start_decay()

        if self.current_animation_state in ('charge', 'kiblast', 'firebeam'):
            self.enter_idle()

    def start_charging_kamekameha(self):
        """Begin the Kamekameha charge animation. Returns True on success.

        Mirrors start_charging_beam() exactly — see that method for the
        reasoning — just against Kamekameha's own state and pointed at the
        'kamekameha' attack_name so KamehamehaChargeEffect/BeamAttack load
        sprites from assets/sprites/attacks/kamekameha/ instead of the
        regular kamehameha folder. Reuses the same 'charge'/'firebeam'
        player sprite states since visually the charge/fire pose is
        identical to the regular beam.
        """
        if not self.can_act():
            return False

        if self.ki > 0 or self.has_free_ki():
            self.is_charging_kamekameha = True
            self.kamekameha_charge_time = 0
            self.is_q_pressed = True
            self.sprite.set_animation('charge', self.direction)
            self.current_animation_state = 'kamekameha_charge'
            self.current_kamekameha_charge_effect = KamehamehaChargeEffect(
                self, attack_name='kamekameha'
            )
            # Sync the auto-fire threshold to however long the charge-up
            # sprite actually takes to play through all of its frames, so
            # the beam can never fire before that animation has finished —
            # same convention as beam_charge_required.
            self.kamekameha_charge_required = \
                self.current_kamekameha_charge_effect.get_total_duration()
            return True

        return False

    def update_kamekameha_charge(self, dt):
        """Advance the Kamekameha charge timer and auto-fire when fully
        charged. Mirrors update_beam_charge() exactly."""
        if self.is_charging_kamekameha:
            if self.current_kamekameha_charge_effect:
                self.current_kamekameha_charge_effect.update(dt)
            self.kamekameha_charge_time += dt
            if (self.kamekameha_charge_time >= self.kamekameha_charge_required
                    and not self.is_firing_kamekameha):
                self.fire_kamekameha_auto()

    def fire_kamekameha_auto(self):
        """Transition from charging to firing once the charge threshold is
        met. Mirrors fire_beam_auto() exactly."""
        if not (self.is_charging_kamekameha
                and self.kamekameha_charge_time >= self.kamekameha_charge_required):
            return None

        self.is_charging_kamekameha = False
        self.is_firing_kamekameha = True
        self.kamekameha_charge_time = 0
        self.current_kamekameha_charge_effect = None
        self.sprite.set_animation('firebeam', self.direction)
        self.current_animation_state = 'kamekameha_fire'

        # Spawn slightly in front of the player based on facing direction,
        # same offset table the beam uses.
        ox, oy = self._get_spawn_offset()
        self.current_kamekameha = BeamAttack(
            self.x + ox, self.y + oy, self.direction, attack_name='kamekameha'
        )
        return self.current_kamekameha

    def stop_kamekameha(self):
        """Cancel Kamekameha charging, or hand a firing one off to its decay
        sweep instead of instantly removing it. Mirrors stop_beam() exactly
        — see that method for the current_kamekameha-not-cleared reasoning.
        """
        self.is_charging_kamekameha = False
        self.is_firing_kamekameha = False
        self.kamekameha_charge_time = 0
        self.is_q_pressed = False
        self.current_kamekameha_charge_effect = None

        if self.current_kamekameha:
            self.current_kamekameha.start_decay()

        if self.current_animation_state in ('kamekameha_charge', 'kamekameha_fire'):
            self.enter_idle()

    def start_charging_banshee_blast(self):
        """Begin the Banshee Blast charge animation. Returns True on success.

        Mirrors start_charging_kamekameha() exactly, with one deliberate
        difference: the player sprite uses a single 'banshee_blast'
        animation key for both charging and firing (see
        fire_banshee_blast_auto(), which never re-calls set_animation),
        instead of switching from 'charge' to 'firebeam' the way beam/
        kamekameha do. current_animation_state still moves from
        'banshee_blast_charge' to 'banshee_blast_fire' though — that's
        just this method's own internal state-machine label, unrelated to
        which sprite animation is actually playing (see the dispatch in
        update() below and _tick_banshee_blast_ki_drain()).
        """
        if not self.can_act():
            return False

        if self.ki > 0 or self.has_free_ki():
            self.is_charging_banshee_blast = True
            self.banshee_blast_charge_time = 0
            self.is_q_pressed = True
            self.sprite.set_animation('banshee_blast', self.direction)
            self.current_animation_state = 'banshee_blast_charge'
            self.current_banshee_blast_charge_effect = BansheeBlastChargeEffect(self)
            # Sync the auto-fire threshold to however long the charge-up
            # sprite actually takes to play through all of its frames, so
            # the beam can never fire before that animation has finished —
            # same convention as beam_charge_required/kamekameha_charge_required.
            self.banshee_blast_charge_required = \
                self.current_banshee_blast_charge_effect.get_total_duration()
            return True

        return False

    def update_banshee_blast_charge(self, dt):
        """Advance the Banshee Blast charge timer and auto-fire when fully
        charged. Mirrors update_kamekameha_charge() exactly."""
        if self.is_charging_banshee_blast:
            if self.current_banshee_blast_charge_effect:
                self.current_banshee_blast_charge_effect.update(dt)
            self.banshee_blast_charge_time += dt
            if (self.banshee_blast_charge_time >= self.banshee_blast_charge_required
                    and not self.is_firing_banshee_blast):
                self.fire_banshee_blast_auto()

    def fire_banshee_blast_auto(self):
        """Transition from charging to firing once the charge threshold is
        met. Mirrors fire_kamekameha_auto() exactly, except the player
        sprite is left alone — it stays on the same 'banshee_blast'
        animation it's already playing rather than switching to a
        separate fire pose."""
        if not (self.is_charging_banshee_blast
                and self.banshee_blast_charge_time >= self.banshee_blast_charge_required):
            return None

        self.is_charging_banshee_blast = False
        self.is_firing_banshee_blast = True
        self.banshee_blast_charge_time = 0
        self.current_banshee_blast_charge_effect = None
        self.current_animation_state = 'banshee_blast_fire'

        # Spawn exactly where the charge sprite was drawn — NOT the
        # generic _get_spawn_offset()/_DIRECTION_SPAWN_OFFSETS table every
        # other beam uses. BansheeBlastChargeEffect.draw() positions
        # itself at (player.x + offset_x, player.y - player.height/2 +
        # offset_y) using BANSHEE_BLAST_CHARGE_OFFSETS (see banshee_blast.
        # py) — mirror that same formula here so the fired beam's begin
        # sprite starts flush with wherever the charge orb was sitting,
        # instead of at a different, unrelated spawn point.
        ox, oy = BANSHEE_BLAST_CHARGE_OFFSETS.get(self.direction, (0, 0))
        self.current_banshee_blast = BansheeBlastAttack(
            self.x + ox, self.y - self.height / 2 + oy, self.direction
        )
        return self.current_banshee_blast

    def stop_banshee_blast(self):
        """Cancel Banshee Blast charging, or hand a firing one off to its
        decay sweep instead of instantly removing it. Mirrors
        stop_kamekameha() exactly.
        """
        self.is_charging_banshee_blast = False
        self.is_firing_banshee_blast = False
        self.banshee_blast_charge_time = 0
        self.is_q_pressed = False
        self.current_banshee_blast_charge_effect = None

        if self.current_banshee_blast:
            self.current_banshee_blast.start_decay()

        if self.current_animation_state in ('banshee_blast_charge', 'banshee_blast_fire'):
            self.enter_idle()

    def energy_punch(self):
        """Throw an instant close-range Energy Punch. Returns True on
        success. Unlike beam/kamekameha/final_flash there's nothing to
        charge — pressing Q just plays the attack animation once, holds on
        its last frame for the rest of a fixed 1-second window (see the
        'energy_punch' branch in update()), then returns to idle. The actual
        "did this hit anyone" check happens every frame in
        Game._update_energy_punch while is_punching is True.
        """
        if not self.can_act():
            return False

        self.is_punching = True
        self.punch_timer = 0
        self.sprite.set_animation('energy_punch', self.direction)
        self.current_animation_state = 'energy_punch'
        return True

    def start_charging_final_flash(self):
        """Begin the Final Flash charge animation. Returns True on success.

        Mirrors start_charging_beam() exactly — see that method for the
        reasoning — just against Final Flash's own state and a
        'charge_final_flash' animation instead of the shared 'charge' one,
        since Final Flash's charge pose is its own sprite, not a reskin of
        the Kamehameha's.
        """
        if not self.can_act():
            return False

        if self.ki > 0 or self.has_free_ki():
            self.is_charging_final_flash = True
            self.final_flash_charge_time = 0
            self.is_q_pressed = True
            self.sprite.set_animation('charge_final_flash', self.direction)
            self.current_animation_state = 'final_flash_charge'
            self.current_final_flash_charge_effect = FinalFlashChargeEffect(self)
            # Sync the auto-fire threshold to however long the charge-up
            # sprite actually takes (1 second by default — see
            # FinalFlashChargeEffect), same convention as the beam.
            self.final_flash_charge_required = self.current_final_flash_charge_effect.get_total_duration()
            return True

        return False

    def update_final_flash_charge(self, dt):
        """Advance the Final Flash charge timer and auto-fire when fully charged."""
        if self.is_charging_final_flash:
            if self.current_final_flash_charge_effect:
                self.current_final_flash_charge_effect.update(dt)
            self.final_flash_charge_time += dt
            if (self.final_flash_charge_time >= self.final_flash_charge_required
                    and not self.is_firing_final_flash):
                self.fire_final_flash_auto()

    def fire_final_flash_auto(self):
        """Transition from charging to firing once the charge threshold is met."""
        if not (self.is_charging_final_flash
                and self.final_flash_charge_time >= self.final_flash_charge_required):
            return None

        self.is_charging_final_flash = False
        self.is_firing_final_flash = True
        self.final_flash_charge_time = 0
        self.current_final_flash_charge_effect = None
        self.sprite.set_animation('firebeam', self.direction)
        self.current_animation_state = 'final_flash_fire'

        # Spawn slightly in front of the player based on facing direction,
        # same offset table the beam uses.
        ox, oy = self._get_spawn_offset()
        self.current_final_flash = FinalFlashAttack(self.x + ox, self.y + oy, self.direction)
        return self.current_final_flash

    def stop_final_flash(self):
        """Cancel Final Flash charging, or hand a firing one off to its
        decay sweep instead of instantly removing it. Mirrors stop_beam()
        exactly — see that method for the current_final_flash-not-cleared
        reasoning.
        """
        self.is_charging_final_flash = False
        self.is_firing_final_flash = False
        self.final_flash_charge_time = 0
        self.is_q_pressed = False
        self.current_final_flash_charge_effect = None

        if self.current_final_flash:
            self.current_final_flash.start_decay()

        if self.current_animation_state in ('final_flash_charge', 'final_flash_fire'):
            self.enter_idle()

    def start_charging_big_bang_kamehameha(self):
        """Begin the Big Bang Kamehameha charge animation. Returns True on
        success.

        Mirrors start_charging_final_flash() exactly — see that method for
        the reasoning — except the charge pose reuses the shared 'charge'
        animation (same as beam/kamekameha) rather than a dedicated one:
        Big Bang Kamehameha's charge-up is the plain Kamehameha's own
        charging sprite/position, not unique art (see
        BigBangKamehamehaChargeEffect's docstring in
        attacks/big_bang_kamehameha.py).
        """
        if not self.can_act():
            return False

        if self.ki > 0 or self.has_free_ki():
            self.is_charging_big_bang_kamehameha = True
            self.big_bang_kamehameha_charge_time = 0
            self.is_q_pressed = True
            self.sprite.set_animation('charge', self.direction)
            self.current_animation_state = 'big_bang_kamehameha_charge'
            self.current_big_bang_kamehameha_charge_effect = BigBangKamehamehaChargeEffect(self)
            # Sync the auto-fire threshold to however long the charge-up
            # sprite actually takes, same convention as final_flash/beam.
            self.big_bang_kamehameha_charge_required = self.current_big_bang_kamehameha_charge_effect.get_total_duration()
            return True

        return False

    def update_big_bang_kamehameha_charge(self, dt):
        """Advance the Big Bang Kamehameha charge timer and auto-fire when
        fully charged."""
        if self.is_charging_big_bang_kamehameha:
            if self.current_big_bang_kamehameha_charge_effect:
                self.current_big_bang_kamehameha_charge_effect.update(dt)
            self.big_bang_kamehameha_charge_time += dt
            if (self.big_bang_kamehameha_charge_time >= self.big_bang_kamehameha_charge_required
                    and not self.is_firing_big_bang_kamehameha):
                self.fire_big_bang_kamehameha_auto()

    def fire_big_bang_kamehameha_auto(self):
        """Transition from charging to firing once the charge threshold is met."""
        if not (self.is_charging_big_bang_kamehameha
                and self.big_bang_kamehameha_charge_time >= self.big_bang_kamehameha_charge_required):
            return None

        self.is_charging_big_bang_kamehameha = False
        self.is_firing_big_bang_kamehameha = True
        self.big_bang_kamehameha_charge_time = 0
        self.current_big_bang_kamehameha_charge_effect = None
        self.sprite.set_animation('firebeam', self.direction)
        self.current_animation_state = 'big_bang_kamehameha_fire'

        # Spawn slightly in front of the player based on facing direction,
        # same offset table the beam/final_flash use.
        ox, oy = self._get_spawn_offset()
        self.current_big_bang_kamehameha = BigBangKamehamehaAttack(self.x + ox, self.y + oy, self.direction)
        return self.current_big_bang_kamehameha

    def stop_big_bang_kamehameha(self):
        """Cancel Big Bang Kamehameha charging, or hand a firing one off to
        its decay sweep instead of instantly removing it. Mirrors
        stop_final_flash()/stop_beam() exactly — see stop_beam() for the
        current_big_bang_kamehameha-not-cleared reasoning.
        """
        self.is_charging_big_bang_kamehameha = False
        self.is_firing_big_bang_kamehameha = False
        self.big_bang_kamehameha_charge_time = 0
        self.is_q_pressed = False
        self.current_big_bang_kamehameha_charge_effect = None

        if self.current_big_bang_kamehameha:
            self.current_big_bang_kamehameha.start_decay()

        if self.current_animation_state in ('big_bang_kamehameha_charge', 'big_bang_kamehameha_fire'):
            self.enter_idle()

    def start_charging_flame_kamehameha(self):
        """Begin the Flame Kamehameha charge animation. Returns True on
        success. Mirrors start_charging_beam() exactly — see that method
        for the reasoning — just against Flame Kamehameha's own state and
        pointed at the 'flame_kamehameha' attack_name so
        KamehamehaChargeEffect loads charging_flame_kamehameha.png instead
        of the regular charging_kamehameha.png.
        """
        if not self.can_act():
            return False

        if self.ki > 0 or self.has_free_ki():
            self.is_charging_flame_kamehameha = True
            self.flame_kamehameha_charge_time = 0
            self.is_q_pressed = True
            self.sprite.set_animation('charge', self.direction)
            self.current_animation_state = 'flame_kamehameha_charge'
            self.current_flame_kamehameha_charge_effect = KamehamehaChargeEffect(
                self, attack_name='flame_kamehameha'
            )
            # Sync the auto-fire threshold to however long the charge-up
            # sprite actually takes to play through all of its frames, so
            # the attack can never fire before that animation has finished
            # — same convention as beam_charge_required.
            self.flame_kamehameha_charge_required = \
                self.current_flame_kamehameha_charge_effect.get_total_duration()
            return True

        return False

    def update_flame_kamehameha_charge(self, dt):
        """Advance the Flame Kamehameha charge timer and auto-fire when
        fully charged. Mirrors update_beam_charge() exactly."""
        if self.is_charging_flame_kamehameha:
            if self.current_flame_kamehameha_charge_effect:
                self.current_flame_kamehameha_charge_effect.update(dt)
            self.flame_kamehameha_charge_time += dt
            if (self.flame_kamehameha_charge_time >= self.flame_kamehameha_charge_required
                    and not self.is_firing_flame_kamehameha):
                self.fire_flame_kamehameha_auto()

    def fire_flame_kamehameha_auto(self):
        """Transition from charging to firing once the charge threshold is
        met. Mirrors fire_beam_auto(); once fired, FlameKamehamehaAttack
        itself becomes fully active instantly and just holds/oscillates
        (see its own docstring) — the charge delay lives entirely here,
        before it's even constructed.
        """
        if not (self.is_charging_flame_kamehameha
                and self.flame_kamehameha_charge_time >= self.flame_kamehameha_charge_required):
            return None

        self.is_charging_flame_kamehameha = False
        self.is_firing_flame_kamehameha = True
        self.flame_kamehameha_charge_time = 0
        self.current_flame_kamehameha_charge_effect = None
        self.sprite.set_animation('firebeam', self.direction)
        self.current_animation_state = 'flame_kamehameha_fire'

        ox, oy = self._get_spawn_offset()
        self.current_flame_kamehameha = FlameKamehamehaAttack(self.x + ox, self.y + oy, self.direction)
        return self.current_flame_kamehameha

    def start_flame_kamehameha(self):
        """Back-compat alias for start_charging_flame_kamehameha().

        The attack used to fire the instant Q went down; it now charges
        first like the regular beam, so this just forwards to the charge
        starter under the old name in case anything still calls it.
        """
        return self.start_charging_flame_kamehameha()

    def stop_flame_kamehameha(self):
        """Cancel Flame Kamehameha charging, or end a firing one outright.

        Mirrors stop_beam(), except (per FlameKamehamehaAttack's own
        docstring) there's no decay sweep to hand a firing attack off to —
        current_flame_kamehameha IS cleared here immediately rather than
        kept around for a decay animation to keep playing through.
        """
        self.is_charging_flame_kamehameha = False
        self.is_firing_flame_kamehameha = False
        self.flame_kamehameha_charge_time = 0
        self.is_q_pressed = False
        self.current_flame_kamehameha_charge_effect = None

        if self.current_flame_kamehameha:
            self.current_flame_kamehameha.stop()
        self.current_flame_kamehameha = None

        if self.current_animation_state in ('flame_kamehameha_charge', 'flame_kamehameha_fire'):
            self.enter_idle()

    def start_charging_genkidama(self):
        """Begin the genkidama charge. Returns True on success.

        Unlike the beam, this never auto-fires — it just keeps escalating
        through its 5 states for as long as Q is held, and release_genkidama()
        fires whatever state was reached when Q comes back up."""
        if not self.can_act():
            return False

        if self.ki < self.genkidama_ki_cost[1] and not self.has_free_ki():
            return False

        self.is_charging_genkidama = True
        self.is_q_pressed = True
        self.sprite.set_animation('charge_genkidama', self.direction)
        self.current_animation_state = 'genkidama_charge'
        self.genkidama_charge_effect = GenkidamaChargeEffect(self)
        return True

    def update_genkidama_charge(self, dt):
        """Advance the genkidama charge timer/state and its visual effect."""
        if self.is_charging_genkidama and self.genkidama_charge_effect:
            self.genkidama_charge_effect.update(dt)

    def release_genkidama(self):
        """Called when the charge key is released. Fires whatever state was
        reached, and returns the GenkidamaBlast to spawn — or None if there
        was nothing to fire (e.g. not enough ki, or charging was never
        actually active)."""
        if not self.is_charging_genkidama or not self.genkidama_charge_effect:
            return None

        state = self.genkidama_charge_effect.state
        cost = self.genkidama_ki_cost.get(state, self.genkidama_ki_cost[1])

        if self.ki < cost and not self.has_free_ki():
            # Not enough ki to actually let it off — cancel silently rather
            # than firing for free.
            self.stop_genkidama()
            return None

        if not self.has_free_ki():
            self.ki -= cost

        sprite = self.genkidama_charge_effect.get_state_sprite(state)
        ox, oy = self._get_spawn_offset()
        blast = GenkidamaBlast(self.x + ox, self.y + oy, self.direction, state, sprite=sprite)

        # Done charging — clear charge state directly (not via stop_genkidama(),
        # which would snap straight to idle) so we can show a brief throw pose
        # first instead.
        self.is_charging_genkidama = False
        self.is_q_pressed = False
        self.genkidama_charge_effect = None

        self.is_firing_genkidama = True
        self.genkidama_fire_pose_timer = self.genkidama_fire_pose_duration
        self.sprite.set_animation('firebeam', self.direction)
        self.current_animation_state = 'genkidama_fire'

        return blast

    def stop_genkidama(self):
        """Cancel genkidama charging (without firing) and clear its state."""
        self.is_charging_genkidama = False
        self.is_q_pressed = False
        self.genkidama_charge_effect = None

        if self.current_animation_state == 'genkidama_charge':
            self.enter_idle()

    def start_charging_big_bang_attack(self):
        """Begin the Big Bang Attack charge. Returns True on success.

        Unlike Genkidama, there's nothing to escalate here — the charge
        effect just plays its fixed charge1 -> charge2 -> state1 ->
        (flicker) charge2 -> state1 intro (see BigBangAttackChargeEffect)
        and then holds there, regardless of how much longer Q stays down
        after that. Releasing always throws the exact same single blast
        (see release_big_bang_attack()) no matter which point in that
        intro release happens to land on — there's no partial-charge,
        weaker version the way an early Genkidama release gives one."""
        if not self.can_act():
            return False

        if self.ki < self.big_bang_attack_ki_cost and not self.has_free_ki():
            return False

        self.is_charging_big_bang_attack = True
        self.is_q_pressed = True
        self.sprite.set_animation('big_bang_attack', self.direction)
        self.current_animation_state = 'big_bang_attack_charge'
        self.current_big_bang_charge = BigBangAttackChargeEffect(self)
        return True

    def update_big_bang_charge(self, dt):
        """Advance the Big Bang Attack charge's fixed intro sequence."""
        if self.is_charging_big_bang_attack and self.current_big_bang_charge:
            self.current_big_bang_charge.update(dt)

    def release_big_bang_attack(self):
        """Called when the charge key is released. Always throws the
        one single blast regardless of which intro phase happened to be
        showing at the exact moment of release (see
        BigBangAttackChargeEffect.get_fire_sprite()) — returns the
        BigBangAttackBlast to spawn, or None if there was nothing to
        fire (e.g. not enough ki, or charging was never actually
        active)."""
        if not self.is_charging_big_bang_attack or not self.current_big_bang_charge:
            return None

        if self.ki < self.big_bang_attack_ki_cost and not self.has_free_ki():
            # Not enough ki to actually let it off — cancel silently
            # rather than firing for free.
            self.stop_big_bang_attack()
            return None

        if not self.has_free_ki():
            self.ki -= self.big_bang_attack_ki_cost

        sprite = self.current_big_bang_charge.get_fire_sprite()
        ox, oy = self._get_spawn_offset()
        blast = BigBangAttackBlast(self.x + ox, self.y + oy, self.direction, sprite=sprite)

        # Done charging — unlike genkidama, there's no separate throw
        # pose to hold first; releasing the attack snaps the player
        # straight back to idle the same instant it fires.
        self.is_charging_big_bang_attack = False
        self.is_q_pressed = False
        self.current_big_bang_charge = None
        self.enter_idle()

        return blast

    def stop_big_bang_attack(self):
        """Cancel Big Bang Attack charging (without firing) and clear
        its state."""
        self.is_charging_big_bang_attack = False
        self.is_q_pressed = False
        self.current_big_bang_charge = None

        if self.current_animation_state == 'big_bang_attack_charge':
            self.enter_idle()

    def start_charging_burning(self):
        """Begin charging the burning attack. Returns True on success.

        Unlike the beam/final-flash charges (their own dedicated 'charge'
        animation), the burning attack's charge pose is just the kiblast
        wind-up held on frame 0 — see update_burning_charge(), which re-pins
        the frame every tick since self.sprite.update(dt) in update() would
        otherwise advance it. BurningChargeEffect draws the extra charging
        sprite next to the player; game.py should call release_burning() on
        button-up (mirroring release_genkidama()).
        """
        if not self.can_act() or self.ki < self.burning_ki_cost:
            return False

        self.is_charging_burning = True
        self.is_q_pressed = True
        self.sprite.set_animation('kiblast', self.direction)
        self.current_animation_state = 'burning_charge'
        self.burning_charge_effect = BurningChargeEffect(self)
        return True

    def update_burning_charge(self, dt):
        """Advance the charge visual and keep the player pinned on kiblast frame 0."""
        if not self.is_charging_burning:
            return
        # Re-apply the animation every tick so the wind-up frame doesn't
        # advance past frame 0 while sprite.update(dt) ticks it forward.
        # NOTE: set_animation() only resets when the animation key actually
        # changes, so calling it here every tick while already on 'kiblast'
        # was a no-op — the animation kept advancing under it and got stuck
        # on frame 1 (the throw pose) after ~0.4s. restart_animation() forces
        # the reset unconditionally, which is what this actually needs.
        self.sprite.restart_animation('kiblast', self.direction)
        if self.burning_charge_effect:
            self.burning_charge_effect.update(dt)

    def release_burning(self):
        """Fire the burning attack. Returns the BurningAttack to spawn, or
        None if not enough ki / not actually charging — mirrors
        release_genkidama()'s shape so game.py can handle both the same way.
        """
        if not self.is_charging_burning:
            return None

        if self.ki < self.burning_ki_cost:
            self.stop_burning()
            return None

        self.ki -= self.burning_ki_cost

        ox, oy = self._get_spawn_offset()
        attack = BurningAttack(self.x + ox, self.y + oy, self.direction,
                                self.burning_stun_duration)
        attack.owner = self

        self.is_charging_burning = False
        self.burning_charge_effect = None
        self.is_q_pressed = False

        # Play the throw pose now that we're releasing. Reuses the existing
        # kiblast_hold1 animation (a single-frame animation pinned to the
        # sheet's 2nd frame / index 1, the right-hand throw pose) so the
        # release reads as an instant, distinct snap rather than playing
        # through the normal kiblast wind-up (0) -> throw (1) sequence —
        # the wind-up already happened during the charge.
        self.is_attacking = True
        self.attack_cooldown = 0.5
        self.sprite.set_animation('kiblast_hold1', self.direction)
        self.current_animation_state = 'kiblast_hold'
        # BurningAttack already spawned above (unlike a normal blast, which
        # waits on pending_blast for the throw frame) — leave pending_blast
        # untouched so the 'kiblast_hold' state's own finish handler
        # (_advance_blast_or_idle) just returns the player to idle instead
        # of chaining into another hold-fire shot; it only continues firing
        # when ki_attack_mode == 'blast', which burning attack never is.

        return attack

    def stop_burning(self):
        """Cancel burning charging (without firing) and clear its state."""
        self.is_charging_burning = False
        self.is_q_pressed = False
        self.burning_charge_effect = None

        if self.current_animation_state == 'burning_charge':
            self.enter_idle()

    def start_targeting_instant_transmission(self):
        """Begin Instant Transmission targeting. Returns True on success.

        While active, the whole world freezes (Game._update_instant_transmission
        drives that) and a screen-space cursor can be aimed at enemies to
        mark them — see Game for cursor movement/hover-select, and
        begin_teleport_sequence below for what happens on release."""
        if not self.can_act():
            return False

        if self.ki < self.it_ki_cost and not self.has_free_ki():
            return False

        self.is_targeting_it = True
        self.is_q_pressed = True
        self.it_original_pos = (self.x, self.y)
        self.sprite.set_animation('instant_transmission', self.direction)
        self.current_animation_state = 'it_targeting'
        return True

    def stop_targeting_instant_transmission(self):
        """Cancel targeting without teleporting anywhere (e.g. nothing was
        selected when the key was released)."""
        self.is_targeting_it = False
        self.is_q_pressed = False

        if self.current_animation_state == 'it_targeting':
            self.enter_idle()

    def begin_teleport_sequence(self, targets):
        """Called by Game when the charge key is released, with `targets` =
        the enemies selected during targeting, in pick order. Kicks off the
        hop sequence: each target in turn, then a final hop back to
        it_original_pos. Returns False (and cancels back to idle) if
        nothing was selected."""
        self.is_targeting_it = False
        self.is_q_pressed = False

        if not targets:
            self.enter_idle()
            return False

        if not self.has_free_ki():
            self.ki -= self.it_ki_cost

        # Only now — actually starting the teleport hops, not just aiming —
        # does the character snap to face 'up' for the teleport/
        # instant_transmission frames. Remember the pre-teleport direction
        # so it can be restored once the whole hop sequence finishes.
        self._it_pre_direction = self.direction
        self.direction = 'up'

        self.it_hop_queue = list(targets)
        self.it_hop_index = 0
        self.it_flicker_stage = 'pre'
        self.it_going_home = False
        self.it_flicker_showing_alt = False
        self.it_teleport_show_frame2_next = True
        self.it_flicker_step = 0
        self.it_flicker_steps_needed = self.IT_PRE_FLICKER_ALTERNATIONS
        self.it_flicker_timer = 0.0
        self.is_teleporting_it = True
        self._show_teleport_flicker_frame()
        self.current_animation_state = 'it_teleport'
        return True

    def _show_teleport_flicker_frame(self):
        """Show the 'teleport' animation for one flicker tick, forcing it
        to the correct frame directly rather than trusting its own
        internal timing.

        The flicker switches away from 'teleport' every it_flicker_interval
        (0.12s), but 'teleport' is registered with a much longer
        frame_duration (0.3s) — so left to its own timing, it would never
        accumulate enough time to advance past frame 0 before we switch to
        'instant_transmission' and back. set_animation() also always
        resets to frame 0 whenever the animation key actually changes,
        which it does every single tick here. Net effect: frame 2 (index 1)
        would never appear. Instead, alternate it manually: frame 2 first,
        then frame 1, then frame 2 again, matching the captured order.
        """
        self.sprite.set_animation('teleport', self.direction)
        anim = self.sprite.animations.get(f'teleport_{self.direction}')
        if anim is not None and not isinstance(anim, list) and anim.frames:
            frame_index = 1 if self.it_teleport_show_frame2_next else 0
            anim.current_frame = frame_index % len(anim.frames)
            anim.time_elapsed = 0
            anim.finished = False
        self.it_teleport_show_frame2_next = not self.it_teleport_show_frame2_next

    def update_it_teleport(self, dt):
        """Advance the teleport hop state machine by one frame. Must be
        called every frame while is_teleporting_it is True — normally from
        Game's frozen-world branch, since Player has no access to the
        enemy list on its own. Returns the enemy just arrived at this
        frame (Game should apply damage to it), or None on every other
        frame.

        Approximates how this plays in the original game: rather than one
        clean flash per hop, the sprite flickers back and forth between
        'teleport' and 'instant_transmission' a few times in place, then
        the position changes, then it flickers a few more times at the
        new spot — a short flicker before/after each target hop, and a
        slightly longer flurry once actually arrived at a target (this is
        also when the melee-range damage check happens). The exact
        alternation counts here are a best-guess approximation — the
        original capture wasn't fully consistent between takes — so treat
        IT_PRE_FLICKER_ALTERNATIONS / IT_POST_FLICKER_ALTERNATIONS_HOP /
        IT_POST_FLICKER_ALTERNATIONS_HOME as tunable, not exact.
        """
        if not self.is_teleporting_it:
            return None

        # A hit is queued from the moment we land next to a target (see the
        # 'pre' branch below) — count it down in real time, independent of
        # the flicker gate just below, so the attack lands a beat after the
        # arrival flicker starts rather than on the very same frame.
        if self._it_pending_hit_target is not None:
            self._it_hit_delay_timer += dt
            if self._it_hit_delay_timer >= self.it_hit_delay:
                target = self._it_pending_hit_target
                self._it_pending_hit_target = None
                self._it_hit_delay_timer = 0.0
                return target

        self.it_flicker_timer += dt
        if self.it_flicker_timer < self.it_flicker_interval:
            return None

        self.it_flicker_timer = 0.0

        # Alternate the displayed animation every tick of the flicker.
        self.it_flicker_showing_alt = not self.it_flicker_showing_alt
        if self.it_flicker_showing_alt:
            self.sprite.set_animation('instant_transmission', self.direction)
        else:
            self._show_teleport_flicker_frame()

        self.it_flicker_step += 1
        if self.it_flicker_step < self.it_flicker_steps_needed:
            # Still flickering in place — nothing else to do this tick.
            return None

        self.it_flicker_step = 0

        if self.it_flicker_stage == 'pre':
            # The pre-move flicker just finished — jump to wherever this
            # hop is headed, then start the (longer) post-move flicker
            # burst there.
            if self.it_hop_index < len(self.it_hop_queue):
                # Hop to the next selected target, landing just below it
                # instead of dead-center on top of it, so the player
                # doesn't visually overlap/occupy the exact same spot as
                # the enemy. Offset by half the enemy's own height plus
                # half the player's, with a small gap, falling back to a
                # fixed offset if either size isn't available on the object.
                target = self.it_hop_queue[self.it_hop_index]
                enemy_half_h = getattr(target, 'height', 32) / 2
                player_half_h = getattr(self, 'height', 32) / 2
                gap = 4
                self.x = target.x
                self.y = target.y + enemy_half_h + player_half_h + gap
                self.it_hop_index += 1
                self.it_teleport_hop_occurred = True

                self.it_going_home = False
                self.it_flicker_stage = 'post'
                self.it_flicker_steps_needed = self.IT_POST_FLICKER_ALTERNATIONS_HOP
                # Damage is attempted once per hop, but not on this very
                # frame — queue it and let it_hit_delay (checked at the top
                # of this method) deliver it a beat into the post-arrival
                # flicker burst instead.
                self._it_pending_hit_target = target
                self._it_hit_delay_timer = 0.0
                return None
            else:
                # Past the last target — this hop is the trip back home.
                self.x, self.y = self.it_original_pos
                self.it_teleport_hop_occurred = True
                self.it_going_home = True
                self.it_flicker_stage = 'post'
                self.it_flicker_steps_needed = self.IT_POST_FLICKER_ALTERNATIONS_HOME
                return None

        # stage == 'post', and that arrival flicker burst just finished.
        if self.it_going_home:
            # Home-arrival burst complete — sequence is over.
            self.is_teleporting_it = False
            self.it_hop_queue = []
            self.it_hop_index = 0
            self.it_flicker_stage = None
            if hasattr(self, '_it_pre_direction'):
                self.direction = self._it_pre_direction
            self.enter_idle()
            return None

        # More hops remain — flicker again in place before the next jump.
        self.it_flicker_stage = 'pre'
        self.it_flicker_steps_needed = self.IT_PRE_FLICKER_ALTERNATIONS
        return None

    def pop_pending_it_teleport_hop(self):
        """Consume and return True exactly once if update_it_teleport()
        actually changed position this frame (a hop landing on a target,
        or the final hop back home), False otherwise. Game calls this
        right after update_it_teleport() to play teleport.wav once per
        hop — same pending-flag/consume pattern as
        pop_pending_charged_melee_hit()."""
        if not self.it_teleport_hop_occurred:
            return False
        self.it_teleport_hop_occurred = False
        return True

    def start_charging_masenko(self):
        """Begin charging Masenko. Returns True on success.

        Like genkidama, this never auto-fires — the oscillating aim
        indicator and hold_masenko overlay run for as long as Q is held,
        and throw_masenko() always throws at whatever position the
        indicator was at the moment Q comes back up."""
        if not self.can_act():
            return False

        if self.ki <= 0 and not self.has_free_ki():
            return False

        self.is_charging_masenko = True
        self.is_q_pressed = True
        self.sprite.set_animation('hold_masenko', self.direction)
        self.current_animation_state = 'masenko_hold'
        self.masenko_indicator   = MasenkoAimIndicator(self)
        self.masenko_hold_effect = MasenkoHoldEffect(self, mode='hold')
        return True

    def update_masenko_charge(self, dt):
        """Advance the aim indicator and hold overlay while charging."""
        if self.is_charging_masenko:
            if self.masenko_indicator:
                self.masenko_indicator.update(dt)
            if self.masenko_hold_effect:
                self.masenko_hold_effect.update(dt)

    def throw_masenko(self):
        """Called when the charge key is released. Always throws (unlike
        beam's auto-fire or genkidama's escalating states) — returns the
        MasenkoProjectile to spawn, or None if nothing was charging or
        there wasn't enough ki to let it off."""
        if not self.is_charging_masenko or not self.masenko_indicator:
            return None

        if self.ki < self.masenko_ki_cost and not self.has_free_ki():
            # Not enough ki to actually let it off — cancel silently rather
            # than throwing for free.
            self.stop_masenko()
            return None

        if not self.has_free_ki():
            self.ki -= self.masenko_ki_cost

        target_x, target_y = self.masenko_indicator.get_target_position()
        # Spawn from the exact same spot the hold_masenko overlay was sitting
        # at (above the player's head) — the ball should read as that same
        # charge simply being let go, not as a separate projectile spawning
        # from wherever bombs/beam launch from.
        hold_ox, hold_oy = 0, 0
        if self.masenko_hold_effect:
            hold_ox, hold_oy = self.masenko_hold_effect.direction_offsets.get(
                self.direction, (0, 0))
        masenko = MasenkoProjectile(self.x + hold_ox, self.y + hold_oy, target_x, target_y,
                                     direction=self.direction)

        # Done charging — clear all charge/fire state and snap straight back
        # to idle. Unlike genkidama, masenko doesn't hold a throw pose — the
        # hold overlay disappears immediately too, since once the ball is
        # thrown there's nothing left charging above the player's head.
        self.is_charging_masenko = False
        self.is_q_pressed = False
        self.masenko_indicator = None
        self.masenko_hold_effect = None
        self.enter_idle()

        return masenko

    def stop_masenko(self):
        """Cancel masenko charging (without throwing) and clear its state."""
        self.is_charging_masenko = False
        self.is_q_pressed = False
        self.masenko_indicator   = None
        self.masenko_hold_effect = None

        if self.current_animation_state == 'masenko_hold':
            self.enter_idle()

    def start_charging_sword(self):
        """Begin drawing the energy sword. Returns True on success.

        Ki is drained continuously over the charge, the same way the beam
        drains while firing — not paid as a lump sum up front and not paid
        on release (masenko/genkidama). If the charge finishes,
        start_sword_spin() fires automatically and free.
        """
        if not self.can_act():
            return False

        if self.ki <= 0 and not self.has_free_ki():
            return False

        self.is_charging_sword = True
        self.sword_charge_time = 0
        self.is_q_pressed = True

        # No dedicated charge_sword pose for up/down yet — reuse the left
        # pose (and its glow offset) for both rather than letting the
        # sprite system's own fallback pick inconsistently.
        charge_pose_direction = 'left' if self.direction in ('up', 'down') else self.direction
        # Remembered so start_sword_spin() can pick the spin's rotation
        # direction from whichever side the player actually charged on.
        self._sword_charge_pose_direction = charge_pose_direction

        self.sprite.set_animation('charge_sword', charge_pose_direction)
        self.current_animation_state = 'sword_charge'
        self.current_sword_charge_effect = EnergySwordChargeEffect(self, facing=charge_pose_direction)
        # Sync to however long the charge-up sprite actually takes to play
        # through all of its frames, same convention as beam_charge_required.
        self.sword_charge_required = self.current_sword_charge_effect.get_total_duration()
        # Spread energy_sword_ki_cost evenly over that duration so a full
        # charge still costs the same total either way, whether it takes
        # 0.4s or 3s to complete.
        self.energy_sword_ki_drain = (
            self.energy_sword_ki_cost / self.sword_charge_required
            if self.sword_charge_required > 0 else self.energy_sword_ki_cost
        )
        return True

    def _tick_sword_ki_drain(self, dt):
        """Drain Ki while the sword is charging, mirroring _tick_beam_ki_drain.

        Transformed state skips the Ki drain but still checks for Q release.
        Called every tick from update_sword_charge()."""
        if not self.has_free_ki():
            self.ki = max(0.0, self.ki - self.energy_sword_ki_drain * dt)
            if self.ki <= 0 or not self.is_q_pressed:
                self.stop_charging_sword()
        elif not self.is_q_pressed:
            self.stop_charging_sword()

    def update_sword_charge(self, dt):
        """Advance the sword charge timer and auto-spin once fully charged."""
        if self.is_charging_sword:
            self._tick_sword_ki_drain(dt)
            if not self.is_charging_sword:
                # Ran out of Ki or Q was released mid-charge — already
                # stopped by _tick_sword_ki_drain, nothing further to do.
                return
            if self.current_sword_charge_effect:
                self.current_sword_charge_effect.update(dt)
            self.sword_charge_time += dt
            if self.sword_charge_time >= self.sword_charge_required:
                self.start_sword_spin()

    def start_sword_spin(self):
        """Transition from charging to the free spin once the charge threshold is met."""
        if not (self.is_charging_sword and self.sword_charge_time >= self.sword_charge_required):
            return

        self.is_charging_sword = False
        self.is_spinning_sword = True
        self.sword_charge_time = 0
        self.current_sword_charge_effect = None
        self.sword_spin_timer = self.sword_spin_duration
        # Spin direction mirrors the charge pose: charged facing left ->
        # clockwise, charged facing right -> counter-clockwise (up/down
        # charges resolve to 'left' in start_charging_sword, so they spin
        # clockwise too, consistent with reusing the left pose/art there).
        spin_clockwise = getattr(self, '_sword_charge_pose_direction', 'left') != 'right'
        self.energy_sword_spin = EnergySwordSpinEffect(
            self, damage=self.energy_sword_damage,
            clockwise=spin_clockwise,
            rotations_per_second=self.sword_spin_rotations_per_second,
        )

        # Clockwise and counter-clockwise spins are two separate hand-drawn
        # sheets (sword_spin_cw / sword_spin_ccw), not mirrors of each
        # other, so which animation name to use is picked once here and
        # reused in update_sword_spin() for the rest of the spin.
        self._sword_spin_anim_name = 'sword_spin_cw' if spin_clockwise else 'sword_spin_ccw'

        # Optional dedicated standing-spin pose — falls back gracefully
        # (stays on whatever the sprite was already showing) if a
        # character's sheet doesn't have one yet. Moving during the spin
        # will override this with the normal walk/run animation anyway,
        # via player.move().
        if self.sprite.has_animation(self._sword_spin_anim_name, self.direction):
            self.sprite.set_animation(self._sword_spin_anim_name, self.direction)
        self.current_animation_state = 'sword_spin'

    def update_sword_spin(self, dt):
        """Advance the spin effect and its free-duration timer.

        Called unconditionally every frame while is_spinning_sword is
        True (see Player.update()) rather than being gated on
        current_animation_state — moving during the spin flips
        current_animation_state to 'walk'/'run' via move(), but the spin
        itself (and its hitbox/timer) needs to keep ticking regardless.
        """
        if not self.is_spinning_sword:
            return
        if self.energy_sword_spin:
            self.energy_sword_spin.update(dt)
            self.energy_sword_spin.tick_cooldowns(dt)

            # Keep the player's own body pose in step with the sword's
            # current octant (up/up_right/right/.../up_left) rather than
            # the 4-directional facing it had when the spin started —
            # sword_spin_cw/ccw are loaded 8-directionally (see
            # sprite_system.py) specifically so this can track all 8
            # steps. Falls back to holding whatever pose was already
            # showing if the sheet isn't loaded for this octant yet.
            anim_name = getattr(self, '_sword_spin_anim_name', 'sword_spin_cw')
            octant = self.energy_sword_spin.current_octant()
            if self.sprite.has_animation(anim_name, octant):
                self.sprite.set_animation(anim_name, octant)

        self.sword_spin_timer -= dt
        if self.sword_spin_timer <= 0:
            self.stop_sword_spin()

    def stop_sword_spin(self):
        """End the spin (naturally, on timeout — or externally, e.g. we
        got hit or killed mid-spin) and return to idle."""
        # If the player stood still the whole spin (current_animation_state
        # never got flipped to 'walk'/'run' by move()), self.direction is
        # still whatever it was from the pre-charge pose — snap it instead
        # to whichever way the blade was actually pointing when the spin
        # stopped, so idle doesn't face some stale direction the player
        # never actually ended up facing.
        if self.current_animation_state == 'sword_spin' and self.energy_sword_spin:
            octant = self.energy_sword_spin.current_octant()
            self.direction = _OCTANT_TO_CARDINAL.get(octant, self.direction)

        self.is_spinning_sword = False
        self.energy_sword_spin = None
        if self.current_animation_state in ('sword_spin', 'walk', 'run'):
            self.enter_idle()

    def stop_charging_sword(self):
        """Cancel an in-progress charge (Q released before it finished).
        Ki already spent when the charge started is NOT refunded."""
        self.is_charging_sword = False
        self.sword_charge_time = 0
        self.is_q_pressed = False
        self.current_sword_charge_effect = None

        if self.current_animation_state == 'sword_charge':
            self.enter_idle()

    def start_dragon_fist(self):
        """Begin the Dragon Fist attack. No charge-up (same shape as the
        energy punch) — Q press immediately locks the player into the
        dragon_fist pose and starts draining Ki, but the head itself
        doesn't launch on press anymore. It waits for the punch animation
        to reach its release frame (frame 3 of 4) — see
        update_dragon_fist()'s pending_dragon_fist handling — then held
        for as long as Q stays down after that: the head launches out,
        then hands control to movement input once it's fully extended.

        Returns True on success, False if the player can't currently act
        or doesn't have the ki for it.
        """
        if not self.can_act():
            return False
        if self.ki <= 0 and not self.has_free_ki():
            return False

        self.is_using_dragon_fist = True
        self.is_q_pressed = True
        self.pending_dragon_fist = True

        self.sprite.set_animation('dragon_fist', self.direction)
        self.current_animation_state = 'dragon_fist'
        return True

    def stop_dragon_fist(self):
        """Q released — hands the fist off to its retract sweep instead of
        ending the attack instantly (mirrors stop_beam()'s decay hand-off).
        is_using_dragon_fist itself doesn't drop until that retract
        finishes (see update_dragon_fist()), so the player stays locked
        into the dragon-fist pose/control scheme for the whole retract,
        same as a beam staying 'active' through its own decay sweep.

        If Q (or Ki) gives out before the wind-up ever resolved — i.e.
        pending_dragon_fist is still True — nothing has spawned yet, so
        there's no fist to hand off to a retract sweep. Cancel outright
        instead.
        """
        self.is_q_pressed = False
        # Mirrors the head's own retract behavior (stays exactly where it
        # was, no sweep) — releasing early stops the forward carry in
        # place too, rather than letting the lunge run out its full
        # duration after the player's already let go.
        self.is_dragon_fist_lunging = False
        if self.current_dragon_fist:
            self.current_dragon_fist.start_retract()
        elif self.pending_dragon_fist:
            self.pending_dragon_fist = False
            self.is_using_dragon_fist = False

    def update_dragon_fist(self, dt):
        """Advance the fist itself (shoot-out / chain-follow / retract),
        carry the player through the opening lunge if it's still running,
        and drain Ki — stopping the attack (via stop_dragon_fist(), which
        starts the retract) when Ki runs out or Q is released, mirroring
        _tick_beam_ki_drain. Once the retract sweep finishes, ends the
        attack for real and returns to idle.

        Outside of the lunge window, the player's own position is left
        untouched here — no continuous drag toward the head. (An earlier
        version of this did drag self.x toward the head's x every frame,
        hardcoded to the x-axis regardless of throw direction, which fired
        constantly for left/right throws and incorrectly during lateral
        steering on up/down throws, and — since the camera re-centers on
        the player every frame — made the head appear to slide back
        toward the middle of the screen on its own even though its world
        position hadn't changed. That's gone; the head is
        player-authoritative once 'controlled' and just stays wherever
        control/the leash puts it.)
        """
        if self.pending_dragon_fist:
            # Wind-up only: hold the head launch until the dragon_fist
            # animation reaches its release frame (index 2 of the 4-frame
            # sheet, i.e. frame 3) instead of spawning DragonFistAttack the
            # instant Q is pressed. Same "arm on press, resolve once a
            # frame threshold is crossed" shape as pending_blast /
            # pending_ultra_volleyball, just resolved entirely here rather
            # than handed off to game.py's update loop.
            if not self.has_free_ki():
                self.ki = max(0.0, self.ki - self.dragon_fist_ki_drain * dt)
            if (not self.has_free_ki() and self.ki <= 0) or not self.is_q_pressed:
                # Ran out of Ki or Q was released before the wind-up
                # finished — nothing's spawned yet, so cancel outright
                # (see the pending_dragon_fist branch in stop_dragon_fist).
                self.stop_dragon_fist()
                return
            if self.sprite.get_current_frame_index() < 2:
                return

            self.pending_dragon_fist = False
            self.is_dragon_fist_lunging = True
            self.dragon_fist_lunge_timer = self.dragon_fist_lunge_duration
            self.current_dragon_fist = DragonFistAttack(
                self.x, self.y, self.direction, scale=RENDER_SCALE
            )
            return

        if self.is_dragon_fist_lunging:
            self._advance_dragon_fist_lunge(dt)

        if self.current_dragon_fist:
            self.current_dragon_fist.update(dt, self.x, self.y)

        if not self.has_free_ki():
            self.ki = max(0.0, self.ki - self.dragon_fist_ki_drain * dt)
            if self.ki <= 0 or not self.is_q_pressed:
                self.stop_dragon_fist()
        elif not self.is_q_pressed:
            self.stop_dragon_fist()

        # Deliberately no "if fist finished retracting, clean up here" —
        # that's handled the same way every other attack's current_X
        # reference is: the top-of-update() cleanup nulls current_dragon_fist
        # and is_using_dragon_fist once DragonFistAttack marks itself
        # inactive, and the 'dragon_fist' animation-state branch's own
        # else clause (is_using_dragon_fist now False) calls enter_idle()
        # the following frame — same one-frame-later shape as
        # current_beam/current_final_flash/etc.

    def _advance_dragon_fist_lunge(self, dt):
        """One frame of the opening Dragon Fist lunge: carry the player
        forward along the throw direction at dragon_fist_follow_speed,
        respecting world bounds and obstacles the same way move() does,
        then translate the fist assembly by the exact same delta so head
        and chain ride along with the player instead of getting left
        behind. Input is deliberately not read here — the lunge runs on
        its own timer regardless of what the player's pressing (movement
        input is otherwise redirected into head-steering by move(), which
        is itself a no-op during 'shooting' anyway — see
        _move_dragon_fist_head).

        Stops itself once dragon_fist_lunge_timer runs out; stop_dragon_fist()
        also cancels it early if Q is released first.
        """
        dxu, dyu = _DRAGON_FIST_DIRECTION_UNIT.get(self.direction, (0, 0))
        step = self.dragon_fist_follow_speed * dt

        moved_x = 0
        moved_y = 0
        if dxu != 0:
            new_x = self.x + dxu * step
            new_x = max(self.width // 2, min(new_x, self.current_room_width - self.width // 2))
            if not self.check_collision_with_obstacles(new_x, self.y):
                moved_x = new_x - self.x
                self.x = new_x
        if dyu != 0:
            new_y = self.y + dyu * step
            new_y = max(self.height // 2, min(new_y, self.current_room_height - self.height // 2))
            if not self.check_collision_with_obstacles(self.x, new_y):
                moved_y = new_y - self.y
                self.y = new_y

        if self.current_dragon_fist and (moved_x or moved_y):
            self.current_dragon_fist.translate(moved_x, moved_y)

        self.dragon_fist_lunge_timer -= dt
        if self.dragon_fist_lunge_timer <= 0:
            self.is_dragon_fist_lunging = False

    def start_ghost_kamikaze(self):
        """Begin the Ghost Kamikaze Attack. Returns the GhostKamikazeAttack
        instance to spawn, or None.

        Instant on press, no charge-up, but not instant-fire either: the
        player loops its cast animation ghost_kamikaze_required_loops (3)
        times — spawning one ghost per loop, left then right then
        middle, each one popping in once that loop's frame index reaches
        ghost_kamikaze_spawn_frame_index (see
        update_ghost_kamikaze_cast()) rather than at the very start of
        the loop — then holds a
        fixed pose (ghost_kamikaze_hold) until GhostKamikazeAttack
        resolves, either from its own hold timer or from the player
        moving early (see can_move()/move()). Everything after the
        initial spawn (targeting, homing, impact) is then driven by
        GhostKamikazeAttack itself, ticked centrally by Game (see that
        class's docstring) rather than from here.

        Each ghost appears right in front of the player (self.direction
        at the moment of the press — see get_ghost_kamikaze_spawn_offset()
        in ghost_kamikaze_attack.py), then moves
        out to its actual left/right/middle formation slot afterward; the
        player's facing at launch also fixes which way "left"/"right"
        fan out and which sprite direction every ghost keeps for its
        entire lifetime, homing included — see GhostKamikazeAttack's
        docstring for both.

        The full ghost_kamikaze_ki_cost has to be affordable to even
        start (checked here), but isn't actually taken here — it's
        charged in ghost_kamikaze_ki_cost_per_ghost pieces as each ghost
        actually spawns (see update_ghost_kamikaze_cast()), so the ki bar
        drains gradually over the 3 loops rather than dropping in one
        chunk the instant this is pressed.
        """
        if not self.can_act():
            return None
        if self.ki < self.ghost_kamikaze_ki_cost and not self.has_free_ki():
            return None

        self.is_casting_ghost_kamikaze = True
        self.is_holding_ghost_kamikaze = False
        self.ghost_kamikaze_loop_count = 0
        self.ghost_kamikaze_prev_frame_index = 0
        self.sprite.set_animation('ghost_kamikaze_cast', self.direction)
        self.current_animation_state = 'ghost_kamikaze_cast'

        # Ghost kamikaze uses its own spawn offset (see
        # get_ghost_kamikaze_spawn_offset() in ghost_kamikaze_attack.py)
        # rather than the shared _get_spawn_offset()/_DIRECTION_SPAWN_OFFSETS
        # every other attack (beam, kamehameha, etc.) draws from, so its
        # spawn point can be tuned without affecting them.
        ox, oy = get_ghost_kamikaze_spawn_offset(self.direction)
        self.current_ghost_kamikaze = GhostKamikazeAttack(self.x + ox, self.y + oy, self.direction)
        # The first ghost no longer spawns immediately here — it spawns
        # once the cast animation reaches its
        # ghost_kamikaze_spawn_frame_index-th frame (see
        # update_ghost_kamikaze_cast()), same as every ghost after it.
        return self.current_ghost_kamikaze

    def update_ghost_kamikaze_cast(self, dt):
        """Count completed loops of the cast animation by watching for
        the sprite's frame index wrapping back down (mirrors how
        genkidama/kiblast track frame-index thresholds, just repeated
        across multiple loops instead of once).

        Each wrap = one full loop just finished, which is exactly the
        signal to end that loop's ghost creation animation (it's been
        looping simultaneously with the cast sprite the whole time — see
        _Ghost's 'spawning' state) and hand it off to its idle sprite via
        finish_current_ghost_spawn().

        The next ghost isn't spawned right at the wrap, though — every
        frame once the current loop's frame index reaches
        ghost_kamikaze_spawn_frame_index (the 3rd frame, 0-indexed as 2),
        this calls spawn_next_ghost(), so the ghost pops in partway
        through the cast animation instead of right at the start of the
        loop. Calling it repeatedly like this (rather than once per loop)
        is deliberately safe: GhostKamikazeAttack.spawn_next_ghost()
        itself won't spawn a new ghost while the previous one hasn't
        cleared the spawn point yet (see its docstring), so this
        naturally keeps retrying every frame until that clears rather
        than needing separate once-per-loop bookkeeping here.

        Only hands off to the held pose once BOTH
        ghost_kamikaze_required_loops (3) have completed AND every ghost
        has actually spawned — not just the loop count on its own. If a
        ghost's spawn got pushed later than its nominal loop (because
        the previous one was slow to clear — see spawn_next_ghost()),
        this keeps the cast animation looping a little longer rather
        than cutting over to the held pose short a ghost.
        """
        idx = self.sprite.get_current_frame_index()
        if idx < self.ghost_kamikaze_prev_frame_index:
            self.ghost_kamikaze_loop_count += 1
            if self.current_ghost_kamikaze:
                self.current_ghost_kamikaze.finish_current_ghost_spawn()
        self.ghost_kamikaze_prev_frame_index = idx

        if idx >= self.ghost_kamikaze_spawn_frame_index and self.current_ghost_kamikaze:
            # spawn_next_ghost() only returns True the frame it actually
            # appends a new ghost (it's a no-op every other frame it's
            # called on, including while the previous ghost hasn't
            # cleared the spawn point yet — see its own docstring), so
            # gating the ki deduction on that return value is what
            # charges ghost_kamikaze_ki_cost_per_ghost exactly once per
            # ghost rather than once per frame this branch runs.
            if self.current_ghost_kamikaze.spawn_next_ghost() and not self.has_free_ki():
                self.ki -= self.ghost_kamikaze_ki_cost_per_ghost

        all_ghosts_spawned = (
            self.current_ghost_kamikaze is not None
            and len(self.current_ghost_kamikaze.ghosts) >= self.current_ghost_kamikaze.num_ghosts
        )
        if self.ghost_kamikaze_loop_count >= self.ghost_kamikaze_required_loops and all_ghosts_spawned:
            self.is_casting_ghost_kamikaze = False
            self.is_holding_ghost_kamikaze = True
            self.sprite.set_animation('ghost_kamikaze_hold', self.direction)
            self.current_animation_state = 'ghost_kamikaze_hold'
            if self.current_ghost_kamikaze:
                self.current_ghost_kamikaze.finish_creation()

    def stop_ghost_kamikaze(self):
        """Cancel the cast/hold pose outright (e.g. the player got hit
        mid-cast). Deliberately leaves current_ghost_kamikaze itself
        alone — any ghosts already spawned keep playing out on their own
        (Game keeps ticking/drawing it until it goes inactive), same as
        how stop_dragon_fist() lets its retract sweep finish rather than
        yanking the object out from under the render loop.
        """
        self.is_casting_ghost_kamikaze = False
        self.is_holding_ghost_kamikaze = False
        if self.current_animation_state in ('ghost_kamikaze_cast', 'ghost_kamikaze_hold'):
            self.enter_idle()

    def start_transform_animation(self):
        """Begin the transform animation — always faces down regardless of current direction."""
        self.direction = 'down'
        self.sprite.set_animation('transform', 'down')
        self.current_animation_state = 'transform'

    def start_untransform_animation(self):
        """Begin the untransform animation — always faces down."""
        self.direction = 'down'
        self.sprite.set_animation('untransform', 'down')
        self.current_animation_state = 'untransform'

    def start_map_jump(self):
        """Begin the world-map jump sequence.

        Loads map_jump.png directly from the current form's character folder
        (bypassing the sprite system, which only knows its registered animation
        names).  The sheet is assumed to be a horizontal strip of frames each
        as wide as self.width.  Frame 1 plays once, then the sprite freezes on
        frame 2 while the player drifts upward off the screen.
        """
        if self.is_map_jumping:
            return

        # Cancel any ongoing combat state so nothing conflicts mid-sequence.
        self.is_attacking      = False
        self.is_charging_beam  = False
        self.is_firing_beam    = False
        self.pending_blast     = None
        self.blast_input_buffered = False
        self.is_q_pressed      = False
        self.current_beam      = None
        self.current_charge_effect = None
        self.is_punching = False
        self.punch_timer = 0
        self.is_charging_kamekameha = False
        self.is_firing_kamekameha = False
        self.current_kamekameha = None
        self.current_kamekameha_charge_effect = None
        self.is_charging_banshee_blast = False
        self.is_firing_banshee_blast = False
        self.current_banshee_blast = None
        self.current_banshee_blast_charge_effect = None
        self.is_charging_final_flash = False
        self.is_firing_final_flash = False
        self.current_final_flash = None
        self.current_final_flash_charge_effect = None
        self.is_charging_big_bang_kamehameha = False
        self.is_firing_big_bang_kamehameha = False
        self.current_big_bang_kamehameha = None
        self.current_big_bang_kamehameha_charge_effect = None
        self.is_charging_flame_kamehameha = False
        self.is_firing_flame_kamehameha = False
        self.flame_kamehameha_charge_time = 0
        self.current_flame_kamehameha_charge_effect = None
        self.current_flame_kamehameha = None
        self.is_charging_genkidama = False
        self.genkidama_charge_effect = None
        self.is_firing_genkidama = False
        self.is_charging_burning = False
        self.burning_charge_effect = None
        self.is_charging_masenko = False
        self.masenko_indicator = None
        self.masenko_hold_effect = None
        self.is_charging_sword = False
        self.current_sword_charge_effect = None
        self.is_spinning_sword = False
        self.energy_sword_spin = None
        self.is_using_dragon_fist = False
        self.current_dragon_fist = None
        self.is_dragon_fist_lunging = False
        self.is_casting_ghost_kamikaze = False
        self.is_holding_ghost_kamikaze = False
        self.current_ghost_kamikaze = None
        self.is_charging_big_bang_attack = False
        self.is_firing_big_bang_attack = False
        self.current_big_bang_charge = None
        self.is_targeting_it = False
        self.is_teleporting_it = False
        self.it_hop_queue = []
        self._it_pending_hit_target = None
        self._it_hit_delay_timer = 0.0
        # Use self.sprite.base_path so this always matches wherever
        # CharacterSpriteLoader put the rest of the sprites.
        path = f'{self.sprite.base_path}/map_jump.png'

        self._map_jump_frames      = []
        self._map_jump_frame_idx   = 0
        self._map_jump_frame_timer = 0.0
        # (Pre-scaled-frame cache removed here — draw() no longer pre-scales,
        # see GPU MIGRATION note at the end of this file.)

        try:
            sheet      = pygame.image.load(path).convert_alpha()
            frame_w    = self.width   # 32 px per frame (horizontal strip)
            frame_h    = self.height  # 32 px per frame (one row per direction)
            num_frames = max(1, sheet.get_width() // frame_w)
            # Match the standard 4-dir row layout: down=0, left=1, right=2, up=3
            direction_row = {'down': 0, 'left': 1, 'right': 2, 'up': 3}.get(self.direction, 0)
            row_y = direction_row * frame_h
            self._map_jump_frames = [
                sheet.subsurface(pygame.Rect(i * frame_w, row_y, frame_w, frame_h))
                for i in range(num_frames)
            ]
        except Exception as e:
            # Sheet not found — sequence still runs (player just drifts up
            # without a sprite change so nothing hard-crashes).
            print(f'[map_jump] could not load {path}: {e}')

        self.is_map_jumping  = True
        self.map_jump_moving = False
        self.map_jump_timer  = 0.0

        # Keep current_animation_state consistent so any external check that
        # reads it sees a meaningful value.  Direction is intentionally left
        # unchanged so the player faces whichever way they were looking.
        self.current_animation_state = 'map_jump'

    # =========================================================================
    # Combat — taking damage
    # =========================================================================

    def start_blocking(self):
        """Raise guard on F held down. Returns True on success.

        Refused via the same can_act() guard as every other action-start
        (start_charging_beam, etc.) — can't raise guard mid-attack or
        mid-knockback.
        """
        if not self.can_act():
            return False
        self.is_blocking = True
        self.sprite.set_animation('blocking', self.direction)
        self.current_animation_state = 'blocking'
        return True

    def stop_blocking(self):
        """Lower guard on F release."""
        if not self.is_blocking:
            return
        self.is_blocking = False
        if not self.is_knocked_back and not self.is_collision_knockback:
            self.enter_idle()

    def take_damage(self, damage, knockback_x, knockback_y,
                    ignore_invulnerability=False, no_knockback=False):
        """Apply damage and knockback from an enemy hit.

        Args:
            damage:                 HP to subtract.
            knockback_x/y:          Unit direction of the knockback vector.
            ignore_invulnerability: Bypass i-frames (e.g. for DoT effects).
            no_knockback:           Grant i-frames only — no physics knockback.
        """
        if self.is_dead:
            # Already dead — nothing can hurt a corpse. Without this,
            # enemies still swinging at the player mid-death-sequence would
            # keep calling this every frame; die() below already no-ops on
            # a repeat call, but this also skips the i-frame/transform/
            # knockback bookkeeping and (more importantly) last_damage_taken,
            # so game.py never spawns another damage number or hurt-tint
            # flash for a hit that landed after the player was already down.
            return

        if self.invulnerable and not ignore_invulnerability:
            return

        # Interrupt a transform-in-progress; untransform cannot be interrupted
        if self.transformation:
            if self.transformation.is_transforming:
                self.transformation.is_transforming = False
                self.transformation.progress = 0.0
            elif self.transformation.is_untransforming:
                return

        if self.is_blocking:
            damage = round(damage / 2)

        self.hp = max(0, self.hp - damage)
        self.last_damage_taken = damage  # Stored so game.py can spawn a popup

        if self.hp <= 0:
            # Dying takes over instead of any of the usual hurt/knockback
            # handling below — see die() for what it locks down.
            self.die()
            return

        if no_knockback:
            # Just grant i-frames — the caller owns the visual feedback
            self.invulnerable = True
            self.invulnerable_timer = self.invulnerable_duration
            return

        if self.is_blocking:
            # Guard absorbs the hit: token 1px nudge instead of full
            # knockback physics, i-frames still granted, but no hurt
            # animation/direction-snap and (see call sites) no hurt_tint —
            # the block animation keeps playing straight through.
            nudge_x = self.x + knockback_x
            nudge_y = self.y + knockback_y
            if not self.check_collision_with_obstacles(nudge_x, self.y):
                self.x = nudge_x
            if not self.check_collision_with_obstacles(self.x, nudge_y):
                self.y = nudge_y
            self.invulnerable = True
            self.invulnerable_timer = self.invulnerable_duration
            return

        # Determine if the hit came from the horizontal or vertical axis
        is_horizontal = abs(knockback_x) > abs(knockback_y)

        if is_horizontal and hasattr(self, 'last_knockback_hit_boundary'):
            # Accumulate wall-bounce counter for horizontal hits
            if self.last_knockback_hit_boundary:
                self.horizontal_boundary_hits += 1

            # After 3 wall bounces, redirect the next hit downward to break the loop
            if self.horizontal_boundary_hits >= 3:
                knockback_x = 0.0
                knockback_y = 1.0
                self.horizontal_boundary_hits = 0
        elif not is_horizontal:
            # Vertical hit resets the horizontal bounce counter
            self.horizontal_boundary_hits = 0

        # Apply physics knockback
        self.is_knocked_back = True
        self.knockback_timer = self.knockback_duration
        self.knockback_velocity_x = knockback_x * 190
        self.knockback_velocity_y = knockback_y * 190

        # Start i-frames
        self.invulnerable = True
        self.invulnerable_timer = self.invulnerable_duration

        # Face toward the attacker (opposite of knockback direction)
        if is_horizontal:
            self.direction = 'right' if knockback_x < 0 else 'left'
        else:
            self.direction = 'down' if knockback_y < 0 else 'up'

        # Cancel any ongoing attacks so we don't fire mid-stagger
        self.is_attacking = False
        self.is_charging_beam = False
        self.is_firing_beam = False
        self.pending_blast = None
        self.blast_input_buffered = False
        self.pending_ultra_volleyball = None
        self.is_q_pressed = False
        self.current_beam = None
        self.current_charge_effect = None
        self.is_punching = False
        self.punch_timer = 0
        # Charged melee (charge-up glow/pulse or lunge/spin) — without this,
        # is_charging_melee/is_charged_melee_active stay True after we set
        # current_animation_state = 'hurt' below, and the safety-fallback
        # at the end of update() (which exists precisely to keep these
        # ticking if current_animation_state ever drifts away from
        # 'charged_melee_charge'/'charged_melee_action') immediately calls
        # update_charged_melee_charge()/update_charged_melee_action() again
        # this same frame — which can release/cancel the charge or continue
        # the lunge and stomp 'hurt' right back to 'charged_melee_action'
        # before the hurt animation ever gets a chance to show.
        self.is_charging_melee = False
        self.is_charged_melee_active = False
        self.charged_melee_flash_amount = 0.0
        self.charged_melee_ready = False
        self.is_charging_kamekameha = False
        self.is_firing_kamekameha = False
        self.current_kamekameha = None
        self.current_kamekameha_charge_effect = None
        self.is_charging_banshee_blast = False
        self.is_firing_banshee_blast = False
        self.current_banshee_blast = None
        self.current_banshee_blast_charge_effect = None
        self.is_charging_final_flash = False
        self.is_firing_final_flash = False
        self.current_final_flash = None
        self.current_final_flash_charge_effect = None
        self.is_charging_big_bang_kamehameha = False
        self.is_firing_big_bang_kamehameha = False
        self.current_big_bang_kamehameha = None
        self.current_big_bang_kamehameha_charge_effect = None
        self.is_charging_flame_kamehameha = False
        self.is_firing_flame_kamehameha = False
        self.flame_kamehameha_charge_time = 0
        self.current_flame_kamehameha_charge_effect = None
        self.current_flame_kamehameha = None
        self.is_charging_genkidama = False
        self.genkidama_charge_effect = None
        self.is_firing_genkidama = False
        self.is_charging_burning = False
        self.burning_charge_effect = None
        self.is_charging_masenko = False
        self.masenko_indicator = None
        self.masenko_hold_effect = None
        self.is_charging_sword = False
        self.current_sword_charge_effect = None
        self.is_spinning_sword = False
        self.energy_sword_spin = None
        self.is_using_dragon_fist = False
        self.current_dragon_fist = None
        self.is_dragon_fist_lunging = False
        self.is_casting_ghost_kamikaze = False
        self.is_holding_ghost_kamikaze = False
        self.current_ghost_kamikaze = None
        self.is_charging_big_bang_attack = False
        self.is_firing_big_bang_attack = False
        self.current_big_bang_charge = None
        self.is_targeting_it = False
        self.is_teleporting_it = False
        self.it_hop_queue = []
        self._it_pending_hit_target = None
        self._it_hit_delay_timer = 0.0

        self.sprite.set_animation('hurt', self.direction)
        self.current_animation_state = 'hurt'

    # =========================================================================
    # XP and levelling
    # =========================================================================

    def gain_exp(self, amount, game_config):
        """Add XP and trigger as many level-ups as the new total allows."""
        self.exp += amount
        self.total_exp += amount  # Never decremented — lifetime total, see __init__.
        while self.exp >= self.exp_to_next_level and self.level < game_config.max_level:
            self.level_up(game_config)

    def level_up(self, game_config):
        """Consume one level's worth of XP and apply the level-up rewards."""
        self.exp -= self.exp_to_next_level
        self.level += 1
        self.stat_points += game_config.stat_points_per_level
        self.pending_level_up = True
        self.exp_to_next_level = game_config.get_xp_for_level(self.level)

        self._grow_hp_ep(game_config)

        # Fully restore HP and Ki on level-up
        self.hp = self.max_hp
        self.ki = self.max_ki

    def _grow_hp_ep(self, game_config):
        """Grow max_hp/max_ki by this level's curve increment, with a small
        random roll so two playthroughs diverge over time — mirroring the
        Buu's Fury data, where two characters both reaching the same level
        ended up with different max HP.

        Uses an *increment* off the reference curve (curve_value(level) -
        curve_value(level - 1)) rather than recomputing max_hp from the
        curve directly, so the roll from each past level-up persists and
        compounds instead of being overwritten.
        """
        hp_increment = game_config.hp_curve_value(self.level) - game_config.hp_curve_value(self.level - 1)
        ep_increment = game_config.ep_curve_value(self.level) - game_config.ep_curve_value(self.level - 1)

        hp_increment = max(hp_increment, game_config.hp_min_gain())
        ep_increment = max(ep_increment, game_config.ep_min_gain())

        hp_roll = random.uniform(1 - game_config.hp_variance, 1 + game_config.hp_variance)
        ep_roll = random.uniform(1 - game_config.ep_variance, 1 + game_config.ep_variance)

        cap = game_config.hp_ep_cap
        self.max_hp = min(cap, self.max_hp + max(0.0, hp_increment) * hp_roll)
        self.max_ki = min(cap, self.max_ki + max(0.0, ep_increment) * ep_roll)

    # =========================================================================
    # Per-character progress (level/XP/HP/KI/stats) — used by
    # Game._switch_character() so each playable character keeps its own
    # independent progression instead of all characters sharing one set of
    # numbers. Zeni, inventory, equipment, and play_time stay global/shared
    # across characters (see Game._switch_character / _write_save_slot) —
    # only the fields below are considered "per-character".
    # =========================================================================

    # Field names captured/restored as one character's progress. Kept as a
    # single tuple so snapshot_progress()/restore_progress()/the save-file
    # round-trip in game.py can't silently drift out of sync with each other.
    PROGRESS_FIELDS = (
        'level', 'exp', 'total_exp', 'exp_to_next_level', 'stat_points',
        'pending_level_up', 'hp', 'max_hp', 'ki', 'max_ki',
    )

    def snapshot_progress(self):
        """Return a JSON-serializable dict of this player's current
        level/XP/HP/KI/stats — i.e. everything that's tracked per-character
        rather than shared across the whole save file."""
        snap = {field: getattr(self, field) for field in self.PROGRESS_FIELDS}
        snap['stats'] = dict(self.stats)
        return snap

    def restore_progress(self, snapshot):
        """Apply a dict previously returned by snapshot_progress()."""
        for field in self.PROGRESS_FIELDS:
            if field in snapshot:
                setattr(self, field, snapshot[field])
        if 'stats' in snapshot:
            self.stats = dict(snapshot['stats'])

    @staticmethod
    def fresh_progress_for_character(char_id, game_config=None):
        """Build a level-1 progress snapshot (same shape as
        snapshot_progress()) for a character that's never been played
        before, seeded from that character's character-creator config
        (assets/characters/{char_id}.json) instead of made-up numbers."""
        from dev_tools import character_creator
        try:
            cfg = character_creator.load_config(char_id)
        except Exception:
            cfg = {}
        cstats = cfg.get('stats', {})

        max_hp = cstats.get('max_hp', 100)
        max_ki = cstats.get('max_ki', 100)

        stats = {
            'strength': cstats.get('power', 1),
            'ki_power': cstats.get('ki_power', 1),
            'vitality': cstats.get('vitality', 1),
            'energy':   1,
            'speed':    cstats.get('speed', 1),
            'defense':  cstats.get('defense', 1),
            'ki_regen': cstats.get('ki_regen', 30),
        }

        return {
            'level':             1,
            'exp':               0,
            'total_exp':         0,
            'exp_to_next_level': game_config.get_xp_for_level(1) if game_config else 100,
            'stat_points':       0,
            'pending_level_up':  False,
            'hp':                max_hp,
            'max_hp':            max_hp,
            'ki':                max_ki,
            'max_ki':            max_ki,
            'stats':             stats,
        }

    def get_melee_damage(self, game_config, target=None, target_end=None):
        """Roll one melee hit's damage using this player's STR against a
        target's END (defense).

        Args:
            game_config: GameConfig — supplies the STR/END curve (see
                         GameConfig.roll_melee_damage).
            target:      Optional object to read target_end from —
                         Player-shaped (has .stats['defense']) or
                         Enemy-shaped (has a plain .defense attribute).
                         Ignored if target_end is given explicitly.
            target_end:  Explicit defender END, overrides *target* when
                         both are given. Defaults to 0 if neither is given.

        Returns (damage: int, is_crit: bool) — same shape as
        GameConfig.roll_melee_damage(). Wire this into wherever melee hits
        are currently resolved (e.g. MeleeAttack's collision handling in
        game.py) — it's not called automatically anywhere yet.
        """
        if target_end is None:
            if target is None:
                target_end = 0
            elif hasattr(target, 'stats'):
                # Player-shaped target — END lives under 'vitality' (or
                # 'end'), the actual key the pause menu's END allocator
                # writes to — see PauseMenu._stat_key_for. NOT 'defense'.
                target_end = target.stats.get('vitality', target.stats.get('end', 0))
            else:
                # Enemy-shaped target — defense is a plain attribute.
                target_end = getattr(target, 'defense', 0)
        strength = self.stats.get('strength', 1)
        return game_config.roll_melee_damage(strength, target_end)

    def get_incoming_melee_damage(self, raw_damage, game_config):
        """Mitigate a flat incoming melee hit by this player's END.

        Reads 'vitality' (falling back to 'end') from self.stats — the
        same key the pause menu's END allocator actually writes to (see
        PauseMenu._stat_key_for), NOT 'defense'. Call this on an enemy's
        raw attack_damage/shooter_melee_damage before passing the result
        to take_damage().
        """
        end_value = self.stats.get('vitality', self.stats.get('end', 0))
        return game_config.apply_incoming_melee_mitigation(raw_damage, end_value)

    def get_ki_blast_damage(self, game_config, target=None, target_end=None):
        """Roll one ki blast hit's damage using this player's POW against
        a target's END (defense). Mirrors get_melee_damage() — see there
        for the target/target_end resolution rules — but has no crit
        (ki_blast has none, per game_config.py's notes) and returns a
        plain int rather than a (damage, is_crit) pair.

        Wire this into wherever ki blast projectiles resolve a hit
        (currently Enemy.check_collision_with_attack's 'projectile'
        branch uses a flat 20 unless the projectile carries an
        owner/get_ki_blast_damage — same pattern as MeleeAttack.owner).
        """
        if target_end is None:
            if target is None:
                target_end = 0
            elif hasattr(target, 'stats'):
                target_end = target.stats.get('vitality', target.stats.get('end', 0))
            else:
                target_end = getattr(target, 'defense', 0)
        pow_stat = self.stats.get('ki_power', 1)
        return game_config.roll_ki_blast_damage(pow_stat, target_end)

    def apply_stat_point(self, stat_name, game_config):
        """Spend one stat point on stat_name. Returns True if the point was spent."""
        if self.stat_points > 0 and self.stats[stat_name] < game_config.max_stat_value:
            self.stats[stat_name] += 1
            self.stat_points -= 1
            self.update_derived_stats()
            return True
        return False

    def update_derived_stats(self):
        """Recalculate run_speed and ki_regen from the current stat block.

        Called whenever a stat point is spent. ki_regen (1–255) maps to a
        regen interval of 30s (slow) → 1s (fast), matching the
        character_creator's default of 30 → 10s.

        max_hp/max_ki are NOT derived here anymore — they're level-driven
        (see GameConfig.hp_curve_value/ep_curve_value + _grow_hp_ep).
        Vitality and energy no longer feed HP/EP directly; they're free to
        be repurposed for other bonuses (e.g. defense, ki cost reduction).

        The Speed stat intentionally has no effect here — movement speed
        (self.speed / self.run_speed) is fixed and not derived from stats.
        """
        self.speed     = 5  / RENDER_SCALE
        self.run_speed = 10 / RENDER_SCALE

        # ki_regen 1 → 30s interval, ki_regen 255 → 1s interval (linear interpolation)
        regen_stat = max(1, self.stats.get('ki_regen', 30))
        self.ki_regen_interval = 30.0 - (regen_stat - 1) * (29.0 / 254.0)

    # =========================================================================
    # Private helpers
    # =========================================================================

    def _tick_beam_ki_drain(self, dt):
        """Drain Ki while the beam is firing, and stop it when Ki runs out or Q is released.

        Transformed state skips the Ki drain but still checks for Q release.
        Called from update() in both the 'firebeam' animation branch and the
        safety fallback below it.
        """
        if not self.has_free_ki():
            self.ki = max(0.0, self.ki - self.beam_ki_drain * dt)
            if self.ki <= 0 or not self.is_q_pressed:
                self.stop_beam()
        elif not self.is_q_pressed:
            self.stop_beam()

    def _tick_kamekameha_ki_drain(self, dt):
        """Drain Ki while the Kamekameha is firing, and stop it when Ki runs
        out or Q is released. Mirrors _tick_beam_ki_drain exactly."""
        if not self.has_free_ki():
            self.ki = max(0.0, self.ki - self.kamekameha_ki_drain * dt)
            if self.ki <= 0 or not self.is_q_pressed:
                self.stop_kamekameha()
        elif not self.is_q_pressed:
            self.stop_kamekameha()

    def _tick_banshee_blast_ki_drain(self, dt):
        """Drain Ki while the Banshee Blast is firing, and stop it when Ki
        runs out or Q is released. Mirrors _tick_kamekameha_ki_drain exactly."""
        if not self.has_free_ki():
            self.ki = max(0.0, self.ki - self.banshee_blast_ki_drain * dt)
            if self.ki <= 0 or not self.is_q_pressed:
                self.stop_banshee_blast()
        elif not self.is_q_pressed:
            self.stop_banshee_blast()

    def _tick_final_flash_ki_drain(self, dt):
        """Drain Ki while Final Flash is firing, and stop it when Ki runs out
        or Q is released. Mirrors _tick_beam_ki_drain exactly — see that
        method for the transformed-state reasoning."""
        if not self.has_free_ki():
            self.ki = max(0.0, self.ki - self.final_flash_ki_drain * dt)
            if self.ki <= 0 or not self.is_q_pressed:
                self.stop_final_flash()
        elif not self.is_q_pressed:
            self.stop_final_flash()

    def _tick_big_bang_kamehameha_ki_drain(self, dt):
        """Drain Ki while Big Bang Kamehameha is firing, and stop it when Ki
        runs out or Q is released. Mirrors _tick_final_flash_ki_drain
        exactly — see that method for the transformed-state reasoning."""
        if not self.has_free_ki():
            self.ki = max(0.0, self.ki - self.big_bang_kamehameha_ki_drain * dt)
            if self.ki <= 0 or not self.is_q_pressed:
                self.stop_big_bang_kamehameha()
        elif not self.is_q_pressed:
            self.stop_big_bang_kamehameha()

    def _tick_flame_kamehameha_ki_drain(self, dt):
        """Drain Ki while Flame Kamehameha is firing, and stop it when Ki
        runs out or Q is released. Mirrors _tick_beam_ki_drain exactly —
        see that method for the transformed-state reasoning."""
        if not self.has_free_ki():
            self.ki = max(0.0, self.ki - self.flame_kamehameha_ki_drain * dt)
            if self.ki <= 0 or not self.is_q_pressed:
                self.stop_flame_kamehameha()
        elif not self.is_q_pressed:
            self.stop_flame_kamehameha()

    # =========================================================================
    # Main update loop
    # =========================================================================

    def update(self, dt):
        """Advance timers, physics, and animation state for one frame."""

        # Reset per-frame block flags here (not just inside move()) so they
        # are always False during knockback frames when move() is never called.
        self._blocked_x = False
        self._blocked_y = False

        # Drop the beam reference once its decay sweep has fully consumed
        # it (BeamAttack marks itself inactive when that happens). This is
        # unconditional — independent of animation/map-jump state — since
        # whatever owns the render loop (game.py) is still calling
        # current_beam.update()/draw() every frame based purely on this
        # reference being set, and needs it cleared once there's nothing
        # left to show.
        if self.current_beam and not self.current_beam.active:
            self.current_beam = None
        if self.current_kamekameha and not self.current_kamekameha.active:
            self.current_kamekameha = None
        if self.current_banshee_blast and not self.current_banshee_blast.active:
            self.current_banshee_blast = None
        if self.current_final_flash and not self.current_final_flash.active:
            self.current_final_flash = None
        if self.current_big_bang_kamehameha and not self.current_big_bang_kamehameha.active:
            self.current_big_bang_kamehameha = None
        if self.current_flame_kamehameha and not self.current_flame_kamehameha.active:
            self.current_flame_kamehameha = None
        if self.current_dragon_fist and not self.current_dragon_fist.active:
            self.current_dragon_fist = None
            self.is_using_dragon_fist = False
            self.is_dragon_fist_lunging = False

        # ------------------------------------------------------------------
        # Death — runs exclusively for the player; only the sprite itself
        # keeps animating. Everything ELSE in the world keeps simulating
        # normally though — Game.update() doesn't freeze for this, only
        # Player.update() does (see Game._update_death_sequence, called
        # every frame alongside the normal gameplay update, not instead of
        # it — enemies/projectiles/damage numbers/hurt-tint decay all keep
        # running while the player sits here dead).
        # ------------------------------------------------------------------
        # Lets death.png play out (and then just sit on its last frame, same
        # as any other finished non-looping animation) while every other
        # *player* system — movement, knockback, i-frames, ki regen, charge
        # states — stays frozen. game.py drives the hold/fade/game-over-box
        # sequence that follows once the animation itself is done, using the
        # normal per-frame call to this same update() (see
        # Game._update_death_sequence) purely to keep advancing the sprite.
        if self.is_dead:
            self.sprite.update(dt)
            return

        # ------------------------------------------------------------------
        # World-map jump sequence — runs exclusively; all other state frozen
        # ------------------------------------------------------------------
        if self.is_map_jumping:
            self.map_jump_timer += dt

            if not self.map_jump_moving:
                # Phase 1 — advance frames normally until we reach frame 2
                # (index 1).  Once there, lock into moving phase.
                self._map_jump_frame_timer += dt
                if self._map_jump_frame_timer >= self._MAP_JUMP_FRAME_DURATION:
                    self._map_jump_frame_timer = 0.0
                    next_idx = self._map_jump_frame_idx + 1
                    if next_idx < len(self._map_jump_frames):
                        self._map_jump_frame_idx = next_idx
                    # Frame 2 reached (index 1) — begin moving upward.
                    if self._map_jump_frame_idx >= 1:
                        self.map_jump_moving = True
            else:
                # Phase 2 — sprite frozen on frame 2, player drifts upward.
                # Do NOT advance _map_jump_frame_idx here.
                self.y -= self.map_jump_speed * dt

                # Fully off the top of the screen → fire exit callback.
                if self.y + self.height < 0:
                    self.is_map_jumping  = False
                    self.map_jump_moving = False
                    if callable(self.on_map_jump_exit):
                        self.on_map_jump_exit()

            return  # Skip all other update logic during the jump sequence

        # ------------------------------------------------------------------
        # Collision knockback (wall-bounce) — runs independently of damage knockback
        # ------------------------------------------------------------------
        if self.is_collision_knockback:
            self.collision_knockback_timer -= dt

            new_x = self.x + self.collision_knockback_velocity_x * dt
            new_y = self.y + self.collision_knockback_velocity_y * dt

            # Per-axis check so the player slides along walls instead of stopping dead
            if not self.check_collision_with_obstacles(new_x, self.y):
                self.x = new_x
            else:
                self.collision_knockback_velocity_x = 0

            if not self.check_collision_with_obstacles(self.x, new_y):
                self.y = new_y
            else:
                self.collision_knockback_velocity_y = 0

            # Clamp to the current room (not the global world bounds)
            self.x = max(self.width // 2,  min(self.x, self.current_room_width  - self.width // 2))
            self.y = max(self.height // 2, min(self.y, self.current_room_height - self.height // 2))

            # Friction
            self.collision_knockback_velocity_x *= 0.85
            self.collision_knockback_velocity_y *= 0.85

            if self.collision_knockback_timer <= 0:
                self.is_collision_knockback = False
                self.collision_knockback_velocity_x = 0
                self.collision_knockback_velocity_y = 0
                self._knockback_cooldown = self._knockback_cooldown_dur  # prevent immediate re-trigger
                # Only snap to idle if regular damage knockback has also finished.
                # If both triggered at once, let the damage-knockback path handle it.
                if not self.is_knocked_back:
                    self.enter_idle()
                return  # Skip the rest of update while we're mid-bounce

        # Tick the post-knockback cooldown so repeated wall-running doesn't
        # chain infinite knockbacks while the key is held.
        if self._knockback_cooldown > 0:
            self._knockback_cooldown = max(0.0, self._knockback_cooldown - dt)

        # ------------------------------------------------------------------
        # Damage knockback — applied by take_damage(); clears on timer expiry
        # ------------------------------------------------------------------
        if self.is_knocked_back:
            self.knockback_timer -= dt

            new_x = self.x + self.knockback_velocity_x * dt
            new_y = self.y + self.knockback_velocity_y * dt

            hit_collision = False  # Tracks whether this frame hit a wall or obstacle

            if not self.check_collision_with_obstacles(new_x, self.y):
                self.x = new_x
            else:
                self.knockback_velocity_x = 0
                hit_collision = True

            if not self.check_collision_with_obstacles(self.x, new_y):
                self.y = new_y
            else:
                self.knockback_velocity_y = 0
                hit_collision = True

            # Clamp to room and detect boundary hits in the same pass
            clamped_x = max(self.width // 2,  min(self.x, self.current_room_width  - self.width // 2))
            clamped_y = max(self.height // 2, min(self.y, self.current_room_height - self.height // 2))

            if clamped_x != self.x or clamped_y != self.y:
                hit_collision = True

            self.x = clamped_x
            self.y = clamped_y
            self.last_knockback_hit_boundary = hit_collision

            # Friction
            self.knockback_velocity_x *= 0.85
            self.knockback_velocity_y *= 0.85

            if self.knockback_timer <= 0:
                self.is_knocked_back = False
                self.knockback_velocity_x = 0
                self.knockback_velocity_y = 0
                # If the hurt animation already finished while knockback was running,
                # we missed the transition window — force idle now.
                if self.current_animation_state == 'hurt' and not self.is_collision_knockback:
                    self.enter_idle()

        # ------------------------------------------------------------------
        # I-frame timer
        # ------------------------------------------------------------------
        if self.invulnerable:
            self.invulnerable_timer -= dt
            if self.invulnerable_timer <= 0:
                self.invulnerable = False

        # ------------------------------------------------------------------
        # Arm pending_blast for a queued hold-fire shot. Deferred by one frame
        # from _advance_blast_or_idle() so it doesn't overwrite the 'ready'
        # flag from the previous shot before game.py has spawned it.
        # ------------------------------------------------------------------
        if self._queue_next_pending:
            self._queue_next_pending = False
            self.pending_blast = True

        # Attack cooldown
        if self.attack_cooldown > 0:
            self.attack_cooldown -= dt

        # ------------------------------------------------------------------
        # Passive ki regen — ticks continuously regardless of what else the
        # player is doing (uses a running timer so it's independent of
        # attack_cooldown and doesn't get reset by combat).
        # ------------------------------------------------------------------
        if self.ki < self.max_ki:
            self.ki_regen_timer += dt
            if self.ki_regen_timer >= self.ki_regen_interval:
                self.ki_regen_timer -= self.ki_regen_interval
                self.ki = min(self.max_ki, self.ki + self.max_ki * self.ki_regen_percent)
        else:
            # Don't let the timer build up while already at full ki, so regen
            # doesn't "fast forward" the moment a blast is fired.
            self.ki_regen_timer = 0.0

        # ------------------------------------------------------------------
        # Sprite animation tick — must happen before the animation-state checks
        # ------------------------------------------------------------------
        self.sprite.update(dt)

        # ------------------------------------------------------------------
        # Animation-state machine — respond to finished animations
        # ------------------------------------------------------------------

        # Transform/untransform always face down; guard against external starters
        # that bypassed start_transform_animation / start_untransform_animation.
        if self.current_animation_state in ('transform', 'untransform') and self.direction != 'down':
            self.direction = 'down'
            self.sprite.set_animation(self.current_animation_state, 'down')

        if self.current_animation_state == 'transform':
            if self.sprite.is_animation_finished():
                if self.transformation and self.transformation.is_transforming:
                    self.transformation.complete_transform()
                    # Landing back in 'idle' should start the stand-still
                    # clock fresh — without this, transforming while already
                    # past IDLE_WAIT_DELAY (mid idle_transition/idle_wait)
                    # left the stale timer in place, so the very next frame
                    # snapped straight back into idle_transition instead of
                    # giving a full new idle period.
                    self.enter_idle()

        elif self.current_animation_state == 'untransform':
            if self.sprite.is_animation_finished():
                if self.transformation and self.transformation.is_untransforming:
                    self.transformation.complete_untransform()
                    # Same reasoning as the transform branch above — detransforming
                    # back from idle_transition/idle_wait should reset the timer
                    # rather than instantly re-triggering the wait animation.
                    self.enter_idle()

        elif self.current_animation_state == 'melee':
            if self.sprite.is_animation_finished():
                if self.is_e_pressed:
                    # Still holding E once the normal swing finished — roll
                    # straight into the charged-melee wind-up instead of
                    # returning to idle (see start_charging_melee()).
                    self.start_charging_melee()
                else:
                    self.is_attacking = False
                    self.enter_idle()

        elif self.current_animation_state == 'charged_melee_charge':
            if self.is_charging_melee:
                self.update_charged_melee_charge(dt)
            else:
                # Stopped externally (e.g. enemy killed us mid-charge).
                self.enter_idle()

        elif self.current_animation_state == 'charged_melee_action':
            if self.is_charged_melee_active:
                self.update_charged_melee_action(dt)
            else:
                # Stopped externally (e.g. enemy killed us mid-attack).
                self.enter_idle()

        elif self.current_animation_state == 'kiblast':
            # Frame 0 = wind-up, frame 1 = throw. Spawn the blast on the release
            # frame rather than waiting for the full animation to finish.
            if self.pending_blast is True and self.sprite.get_current_frame_index() >= 1:
                self.pending_blast = 'ready'
            # Ultra Volleyball rides the same wind-up/throw animation and
            # release frame as a regular blast (see shoot_ultra_volleyball())
            # — tracked independently so firing one never marks the other ready.
            if self.pending_ultra_volleyball is True and self.sprite.get_current_frame_index() >= 1:
                self.pending_ultra_volleyball = 'ready'
            if self.sprite.is_animation_finished():
                self._advance_blast_or_idle()

        elif self.current_animation_state == 'kiblast_hold':
            # Single-frame animation — fire once as soon as pending_blast is armed.
            if self.pending_blast is True:
                self.pending_blast = 'ready'
            if self.sprite.is_animation_finished():
                self._advance_blast_or_idle()

        elif self.current_animation_state == 'hurt':
            if self.sprite.is_animation_finished():
                # Don't snap to idle until both knockback types have cleared
                if not self.is_knocked_back and not self.is_collision_knockback:
                    self.enter_idle()

        elif self.current_animation_state == 'pickup_item':
            # Real-time timer, not sprite.is_animation_finished() — the pose
            # is meant to hold for a fixed ~1s regardless of pickup_item.png's
            # own frame count (see start_pickup_item()).
            self.pickup_item_timer += dt
            if self.pickup_item_timer >= self.PICKUP_ITEM_DURATION:
                self.is_picking_up_item = False
                self.enter_idle()

        elif self.current_animation_state == 'idle':
            # Stand-still timer — after IDLE_WAIT_DELAY seconds of plain idle,
            # kick off the wait animation. idle_transition plays its (variable-
            # length) lead-in frames once, then hands off to the looping
            # idle_wait pose. Both are hardcoded to 'down' below regardless of
            # self.direction, so a character idling while facing left/right/up
            # still turns to face the camera for the wait.
            self.idle_timer += dt
            if self.idle_timer >= self.IDLE_WAIT_DELAY and self.sprite.has_animation('idle_transition', 'down'):
                self.sprite.set_animation('idle_transition', 'down')
                self.current_animation_state = 'idle_transition'

        elif self.current_animation_state == 'idle_transition':
            if self.sprite.is_animation_finished():
                self.sprite.set_animation('idle_wait', 'down')
                self.current_animation_state = 'idle_wait'

        elif self.current_animation_state == 'idle_wait':
            pass  # Loops in place until movement/an action interrupts it elsewhere

        elif self.current_animation_state == 'charge':
            if self.is_charging_beam and not self.is_q_pressed:
                self.stop_beam()
            elif self.is_charging_beam:
                self.update_beam_charge(dt)

        elif self.current_animation_state == 'kamekameha_charge':
            if self.is_charging_kamekameha and not self.is_q_pressed:
                self.stop_kamekameha()
            elif self.is_charging_kamekameha:
                self.update_kamekameha_charge(dt)

        elif self.current_animation_state == 'kamekameha_fire':
            if self.is_firing_kamekameha:
                self._tick_kamekameha_ki_drain(dt)
            else:
                # Stopped externally (e.g. enemy killed us mid-fire)
                self.enter_idle()

        elif self.current_animation_state == 'banshee_blast_charge':
            if self.is_charging_banshee_blast and not self.is_q_pressed:
                self.stop_banshee_blast()
            elif self.is_charging_banshee_blast:
                self.update_banshee_blast_charge(dt)

        elif self.current_animation_state == 'banshee_blast_fire':
            if self.is_firing_banshee_blast:
                self._tick_banshee_blast_ki_drain(dt)
            else:
                # Stopped externally (e.g. enemy killed us mid-fire)
                self.enter_idle()

        elif self.current_animation_state == 'energy_punch':
            # Runs the fixed 1s window itself, independent of however long the
            # animation sheet actually is — once is_animation_finished() goes
            # True the sprite just naturally holds on its last frame (same as
            # every other non-looping animation), so no explicit freeze call
            # is needed here, just letting the timer keep running.
            self.punch_timer += dt
            if self.punch_timer >= self.punch_duration:
                self.is_punching = False
                self.enter_idle()

        elif self.current_animation_state == 'final_flash_charge':
            if self.is_charging_final_flash and not self.is_q_pressed:
                self.stop_final_flash()
            elif self.is_charging_final_flash:
                self.update_final_flash_charge(dt)

        elif self.current_animation_state == 'final_flash_fire':
            if self.is_firing_final_flash:
                self._tick_final_flash_ki_drain(dt)
            else:
                # Stopped externally (e.g. enemy killed us mid-fire)
                self.enter_idle()

        elif self.current_animation_state == 'big_bang_kamehameha_charge':
            if self.is_charging_big_bang_kamehameha and not self.is_q_pressed:
                self.stop_big_bang_kamehameha()
            elif self.is_charging_big_bang_kamehameha:
                self.update_big_bang_kamehameha_charge(dt)

        elif self.current_animation_state == 'big_bang_kamehameha_fire':
            if self.is_firing_big_bang_kamehameha:
                self._tick_big_bang_kamehameha_ki_drain(dt)
            else:
                # Stopped externally (e.g. enemy killed us mid-fire)
                self.enter_idle()

        elif self.current_animation_state == 'genkidama_charge':
            if self.is_charging_genkidama:
                self.update_genkidama_charge(dt)
            else:
                # Charge stopped externally (e.g. enemy killed us mid-charge)
                self.enter_idle()

        elif self.current_animation_state == 'burning_charge':
            if self.is_charging_burning:
                self.update_burning_charge(dt)
            else:
                # Charge stopped externally (e.g. enemy killed us mid-charge)
                self.enter_idle()

        elif self.current_animation_state == 'genkidama_fire':
            if self.is_firing_genkidama:
                self.genkidama_fire_pose_timer -= dt
                if self.genkidama_fire_pose_timer <= 0:
                    self.is_firing_genkidama = False
                    self.enter_idle()
            else:
                # Stopped externally (e.g. enemy killed us mid-throw)
                self.enter_idle()

        elif self.current_animation_state == 'big_bang_attack_charge':
            if self.is_charging_big_bang_attack:
                self.update_big_bang_charge(dt)
            else:
                # Charge stopped externally (e.g. enemy killed us mid-charge)
                self.enter_idle()

        elif self.current_animation_state == 'big_bang_attack_fire':
            if self.is_firing_big_bang_attack:
                self.big_bang_attack_fire_pose_timer -= dt
                if self.big_bang_attack_fire_pose_timer <= 0:
                    self.is_firing_big_bang_attack = False
                    self.enter_idle()
            else:
                # Stopped externally (e.g. enemy killed us mid-throw)
                self.enter_idle()

        elif self.current_animation_state == 'it_targeting':
            if not self.is_targeting_it:
                # Stopped externally (e.g. interrupted mid-target)
                self.enter_idle()
            # Otherwise: just hold the pose here. Cursor movement and
            # enemy hover-selection are driven externally by Game every
            # frame (see Game._update_instant_transmission), since Player
            # doesn't have access to the enemy list or camera on its own.

        elif self.current_animation_state == 'it_teleport':
            if not self.is_teleporting_it:
                # Stopped externally mid-sequence.
                self.enter_idle()
            # Otherwise: the hop state machine itself is advanced externally
            # via update_it_teleport(dt), also called from Game's frozen-
            # world branch — nothing to do here beyond holding state.

        elif self.current_animation_state == 'masenko_hold':
            if self.is_charging_masenko:
                self.update_masenko_charge(dt)
            else:
                # Charge stopped externally (e.g. enemy killed us mid-charge)
                self.enter_idle()

        elif self.current_animation_state == 'firebeam':
            if self.is_firing_beam:
                self._tick_beam_ki_drain(dt)
            else:
                # Beam stopped externally (e.g. enemy killed us mid-fire)
                self.enter_idle()

        elif self.current_animation_state == 'flame_kamehameha_charge':
            if self.is_charging_flame_kamehameha and not self.is_q_pressed:
                self.stop_flame_kamehameha()
            elif self.is_charging_flame_kamehameha:
                self.update_flame_kamehameha_charge(dt)

        elif self.current_animation_state == 'flame_kamehameha_fire':
            if self.is_firing_flame_kamehameha:
                self._tick_flame_kamehameha_ki_drain(dt)
            else:
                # Stopped externally (e.g. enemy killed us mid-fire)
                self.enter_idle()

        elif self.current_animation_state == 'sword_charge':
            if self.is_charging_sword and not self.is_q_pressed:
                self.stop_charging_sword()
            elif self.is_charging_sword:
                self.update_sword_charge(dt)

        elif self.current_animation_state == 'dragon_fist':
            if self.is_using_dragon_fist:
                self.update_dragon_fist(dt)
            else:
                # Stopped externally (e.g. enemy killed us mid-attack), or
                # the retract sweep finished last frame and got cleaned up
                # by the top-of-update() cleanup — either way, back to idle.
                self.enter_idle()

        elif self.current_animation_state == 'ghost_kamikaze_cast':
            if self.is_casting_ghost_kamikaze:
                self.update_ghost_kamikaze_cast(dt)
            else:
                # Stopped externally mid-cast (e.g. enemy hit us).
                self.enter_idle()

        elif self.current_animation_state == 'ghost_kamikaze_hold':
            if not self.is_holding_ghost_kamikaze:
                # Stopped externally mid-hold.
                self.enter_idle()
            elif self.current_ghost_kamikaze is None or self.current_ghost_kamikaze.phase not in ('creating', 'holding'):
                # GhostKamikazeAttack has resolved (hold timer ran out,
                # or the player already moved and launch_now() fired) —
                # the player's part is done; the ghosts carry on
                # independently from here (see Game._update_ghost_kamikaze).
                self.is_holding_ghost_kamikaze = False
                self.enter_idle()
            # Otherwise: just hold the pose. GhostKamikazeAttack's own
            # hold_timer is ticked centrally by Game (see that class's
            # docstring), not here.

        # Safety fallback — if the beam is still firing but the animation state
        # drifted out of 'firebeam' somehow, drain Ki and check for stop.
        if self.is_firing_beam and self.current_animation_state != 'firebeam':
            self._tick_beam_ki_drain(dt)

        # Same safety fallback for Kamekameha.
        if self.is_firing_kamekameha and self.current_animation_state != 'kamekameha_fire':
            self._tick_kamekameha_ki_drain(dt)

        # Same safety fallback for Banshee Blast.
        if self.is_firing_banshee_blast and self.current_animation_state != 'banshee_blast_fire':
            self._tick_banshee_blast_ki_drain(dt)

        # Same safety fallback for Final Flash.
        if self.is_firing_final_flash and self.current_animation_state != 'final_flash_fire':
            self._tick_final_flash_ki_drain(dt)

        # Same safety fallback for Big Bang Kamehameha.
        if self.is_firing_big_bang_kamehameha and self.current_animation_state != 'big_bang_kamehameha_fire':
            self._tick_big_bang_kamehameha_ki_drain(dt)

        # Same safety fallback for Flame Kamehameha.
        if self.is_firing_flame_kamehameha and self.current_animation_state != 'flame_kamehameha_fire':
            self._tick_flame_kamehameha_ki_drain(dt)

        # Same safety fallback for Dragon Fist — shouldn't normally drift
        # since move() no longer flips current_animation_state to
        # 'walk'/'run' while is_using_dragon_fist is True (unlike the sword
        # spin), but kept for the same robustness reasons as the others.
        if self.is_using_dragon_fist and self.current_animation_state != 'dragon_fist':
            self.update_dragon_fist(dt)

        # Sword spin ticks every frame regardless of current_animation_state —
        # see update_sword_spin()'s docstring for why (moving during the
        # spin flips current_animation_state to 'walk'/'run' via move()).
        if self.is_spinning_sword:
            self.update_sword_spin(dt)

        # Same safety fallback for the charged-melee charge/action —
        # shouldn't normally drift since neither phase lets move() flip
        # current_animation_state (the spin is rooted, the lunge is forced
        # movement rather than move()-driven), but kept for the same
        # robustness reasons as dragon fist's fallback above.
        if self.is_charging_melee and self.current_animation_state != 'charged_melee_charge':
            self.update_charged_melee_charge(dt)
        if self.is_charged_melee_active and self.current_animation_state != 'charged_melee_action':
            self.update_charged_melee_action(dt)

    # =========================================================================
    # Input helpers
    # =========================================================================

    def check_double_tap(self, key):
        """Return True if *key* was pressed twice within double_tap_window seconds."""
        current_time = time.time()
        if key in self.last_key_press:
            if current_time - self.last_key_press[key] < self.double_tap_window:
                self.last_key_press[key] = 0  # Reset so a third tap doesn't count
                return True
        self.last_key_press[key] = current_time
        return False

    # =========================================================================
    # Rendering
    # =========================================================================

    def draw(self, screen, camera, colors):
        """Draw the player sprite with the current hurt tint applied.

        NOTE (GPU render pass): self.sprite.draw(...) at the bottom
        delegates to core/sprite_system.py (shared by player/enemy/NPC),
        not yet converted -- see MIGRATION note at the end of this file.
        """
        if self.is_map_jumping and self._map_jump_frames:
            idx    = self._map_jump_frame_idx
            sx     = int(self.x * RENDER_SCALE - camera.x)
            sy     = int(self.y * RENDER_SCALE - camera.y)
            w      = int(self.width * RENDER_SCALE)
            h      = int(self._map_jump_frames[idx].get_height() * RENDER_SCALE)

            # The scaled-frame cache this used to need (_map_jump_scaled_cache)
            # is gone: that cache existed purely to avoid re-running
            # pygame.transform.scale -- a CPU pixel resample -- on every frame
            # of the ascent. blit_scaled() draws the ORIGINAL, unscaled frame
            # and lets the GPU stretch it to the dest rect as part of the
            # copy, so there's nothing left to cache; recomputing `dest_rect`
            # every call is just arithmetic, not a resample.
            frame = self._map_jump_frames[idx]
            dest_rect = frame.get_rect(center=(sx, sy))
            dest_rect.width, dest_rect.height = w, h
            screen.blit_scaled(frame, dest_rect)
            return

        tint = getattr(self, 'hurt_tint', 0.0)
        flash_white = getattr(self, 'charged_melee_flash_amount', 0.0)
        self.sprite.draw(screen, self.x, self.y, camera, scale=RENDER_SCALE,
                         hurt_tint=tint, flash_white=flash_white)


# ── GPU MIGRATION STATUS (this file) ────────────────────────────────────────
# Done: the map-jump ascent draw (the only place this file itself scaled and
# blitted a frame). Deleted _map_jump_scaled_cache and its reset-on-
# start_map_jump() wiring -- if start_map_jump() explicitly cleared that
# cache anywhere else in this file, that clear call is now dead code and
# can be deleted too (I don't have that method in view to confirm/remove it
# myself -- grep this file for `_map_jump_scaled_cache` to check).
# Not this file's problem: self.sprite.draw(...) -- core/sprite_system.py,
# not uploaded yet. That module is the highest-value remaining target: it's
# the shared code path for every character's on-screen body (player, every
# enemy, every NPC), so converting it once fixes rendering cost for all of
# them at once, unlike this file/enemy.py/npc.py which each only had one or
# two isolated call sites.