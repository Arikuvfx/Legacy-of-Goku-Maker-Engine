import pygame
import random
import math
import time
from config.settings import WORLD_WIDTH, WORLD_HEIGHT, RED, ORANGE, BLACK, GREEN, WHITE, YELLOW, RENDER_SCALE
from core.draw_layers import DrawLayer
from core.sprite_system import create_enemy_sprite
from core.zeni_system import roll_zeni_drop


# ---------------------------------------------------------------------------
# Shooter stat presets — each key maps to (attack_duration, cooldown_time,
# attack_range, preferred_distance, attack_damage, projectile_speed).
# Add new shooter types here without touching __init__.
# ---------------------------------------------------------------------------
_SHOOTER_PRESETS = {
    'bullet': (
        0.35,   # attack_duration  — snappy fire animation
        1.2,    # attack_cooldown_time — fires frequently
        200,    # attack_range
        160,    # preferred_distance — keeps its distance
        10,     # attack_damage — lower per-shot, compensated by fire rate
        350,    # projectile_speed — fast bullet
    ),
    'rocket': (
        0.5,    # attack_duration  — slightly longer fire animation
        3.5,    # attack_cooldown_time — slow reload
        220,    # attack_range
        170,    # preferred_distance
        30,     # attack_damage — high damage per shot
        220,    # projectile_speed — slower than bullet
    ),
    'bomb': (
        0.6,    # attack_duration
        2.0,    # attack_cooldown_time
        150,    # attack_range
        120,    # preferred_distance
        15,     # attack_damage
        200,    # projectile_speed
    ),
    'kiblast': (
        0.6,    # attack_duration  — time for the ki charge animation
        2.5,    # attack_cooldown_time
        200,    # attack_range
        160,    # preferred_distance — keeps distance like a gunner
        14,     # attack_damage
        300,    # projectile_speed — fast cardinal shot
    ),
}

# Cardinal knockback vectors keyed by facing direction
_KNOCKBACK_VECTORS = {
    'up':    (0.0, -1.0),
    'down':  (0.0,  1.0),
    'left':  (-1.0, 0.0),
    'right': (1.0,  0.0),
}


class EncasementOverlay:
    """Small self-contained animated overlay drawn in place of the enemy
    sprite while it's encased (see Enemy.encase()/is_encased). Holds TWO
    separate spritesheets/modes:

      - 'casing' mode (self.frames): ultra_volleyball_attack_ball.png —
        the stationary ball. Plays forward ONCE via play(), then holds on
        its last frame. Used twice: the encase intro (ball forming around
        the enemy) and the release outro.
      - 'roll' mode: ultra_volleyball_attack_ball_rolling.png — a
        continuously LOOPING spin, direction-aware. The sheet has 2 rows:
        row 0 = left/right movement, drawn right-facing (mirrored
        horizontally for 'left'); row 1 = up/down movement, drawn
        down-facing/"standard" (mirrored vertically for 'up'). All four
        cardinal variants are precomputed once at load time into
        roll_frames_by_direction rather than flipped every frame.
        Started via play_loop(direction) whenever try_trigger_roll()
        actually starts a roll, and switched back to 'casing' mode
        automatically by the next play() call (see
        Enemy._release_from_encasement()).

    Standalone rather than folded into Enemy's own sprite/animation system
    since it needs to render ON TOP of (in place of) whatever the enemy's
    own AnimatedSprite is doing, not as one more state in that system.
    """

    def __init__(self, casing_asset, roll_asset=None,
                 frame_width=32, frame_height=32, frame_duration=0.08):
        self.frame_duration = frame_duration
        self.frame_timer = 0.0
        self.frame_index = 0
        self.mode = 'casing'  # 'casing' | 'roll'
        self.roll_direction = 'down'
        self.playing = False
        self.finished = True

        self.frames = self._load_row(casing_asset, frame_width, frame_height, row=0)

        self.roll_frames_by_direction = {}
        if roll_asset:
            horizontal_row = self._load_row(roll_asset, frame_width, frame_height, row=0)
            vertical_row = self._load_row(roll_asset, frame_width, frame_height, row=1)
            if horizontal_row or vertical_row:
                self.roll_frames_by_direction = {
                    'right': horizontal_row,
                    'left':  [pygame.transform.flip(f, True, False) for f in horizontal_row],
                    'down':  vertical_row,
                    'up':    [pygame.transform.flip(f, False, True) for f in vertical_row],
                }

    def _load_row(self, asset_path, frame_width, frame_height, row=0):
        try:
            sheet = pygame.image.load(asset_path).convert_alpha()
            frames_per_row = sheet.get_width() // frame_width
            y = row * frame_height
            frames = []
            for i in range(frames_per_row):
                x = i * frame_width
                frames.append(sheet.subsurface(pygame.Rect(x, y, frame_width, frame_height)))
            return frames
        except Exception as e:
            print(f"Error loading encasement overlay sprite ({asset_path}, row {row}): {e}")
            return []

    def play(self):
        """(Re)start the one-shot casing/ball animation from frame 0.
        Also switches mode back to 'casing' — the way a roll ending always
        returns to showing the ball art for its release outro, even if
        the overlay was mid-loop in 'roll' mode a moment before."""
        self.mode = 'casing'
        self.frame_index = 0
        self.frame_timer = 0.0
        self.playing = True
        self.finished = not self.frames

    def play_loop(self, direction='down'):
        """Start the continuously-looping spin animation, oriented for
        *direction* — called by Enemy.try_trigger_roll() the instant a
        roll actually starts."""
        self.mode = 'roll'
        self.roll_direction = direction
        self.frame_index = 0
        self.frame_timer = 0.0
        self.playing = True
        self.finished = False  # a loop is never "finished" on its own

    def _current_frames(self):
        if self.mode == 'roll':
            return self.roll_frames_by_direction.get(self.roll_direction, [])
        return self.frames

    def update(self, dt):
        frames = self._current_frames()
        if not self.playing or not frames:
            return
        self.frame_timer += dt
        if self.frame_timer >= self.frame_duration:
            self.frame_timer -= self.frame_duration
            self.frame_index += 1
            if self.frame_index >= len(frames):
                if self.mode == 'roll':
                    self.frame_index = 0  # loop forever while rolling
                else:
                    self.frame_index = len(frames) - 1  # hold on last frame
                    self.playing = False
                    self.finished = True

    def draw(self, screen, x, y, camera, render_scale):
        frames = self._current_frames()
        if not frames:
            return
        frame = frames[self.frame_index % len(frames)]
        size = frame.get_width()
        scaled = pygame.transform.scale(frame, (int(size * render_scale), int(size * render_scale)))
        screen_x = (x * render_scale) - camera.x
        screen_y = (y * render_scale) - camera.y
        rect = scaled.get_rect(center=(screen_x, screen_y))
        screen.blit(scaled, rect)


class Enemy:
    def __init__(self, x, y, enemy_type='tiger_bandit', variant='default',
                 ai_type='easy', enemy_category='melee', shooter_style='bomb',
                 zeni_pool='tier1'):
        """Create an enemy at world position (*x*, *y*).

        Args:
            x, y:            Starting world coordinates.
            enemy_type:      Sprite folder key (e.g. 'tiger_bandit').
            variant:         Colour/skin variant used by the sprite loader.
            ai_type:         'easy' for basic movement, 'advanced' for retreat/feint/rush.
            enemy_category:  'melee' for close-range or 'shooter' for ranged attacks.
            shooter_style:   'bomb', 'bullet', or 'rocket' — only used for 'shooter' category.
            zeni_pool:       Which core.zeni_system drop table this enemy rolls
                             against on death (e.g. 'tier1'..'tier4'). Set per
                             placement from the entity editor's Zeni Pool selector.
        """
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.shadow_size = 'small'  # 'small' or 'big' — override per enemy subclass if needed
        # Ground shadow px width — character_creator.py's Shadow Size slider
        # (8-96 step 4), same field BossEnemy already reads from its own cfg.
        # Regular (non-boss) Enemy doesn't load its config itself, so this is
        # just the fallback; game._spawn_room_entities overwrites it with the
        # entity_creator-configured value right after construction.
        self.shadow_width = self.width
        self.speed = 1
        self.hp = 1
        self.max_hp = 150
        self.active = True

        # END/defense — mitigates incoming melee damage, see
        # GameConfig.melee_defense_factor(). Defaults to 20 because that's
        # the Enemy END the STR curve itself was calibrated against (see
        # game_config.py's melee_reference_end) — an untouched enemy takes
        # damage exactly matching the original sampled data until this is
        # tuned per enemy_type/variant.
        self.defense = 20

        self.enemy_type = enemy_type
        self.variant = variant
        self.ai_type = ai_type          # 'easy' = basic movement, 'advanced' = retreats/feints/etc.
        self.enemy_category = enemy_category
        self.zeni_pool = zeni_pool      # Which core.zeni_system pool this enemy drops on death
        self.zeni_drop = None           # Set once in take_damage() on the killing blow — game.py
                                         # consumes and clears it to spawn world pickups immediately,
                                         # instead of waiting for the death animation to finish.

        # Load sprite — falls back to a colored placeholder rect if file is missing
        self.sprite = create_enemy_sprite(enemy_type, variant, self.width, self.height)
        self.has_sprite = self.sprite is not None

        # -----------------------------------------------------------------
        # AI state machine — starts idle, switches to 'chase' on awareness
        # -----------------------------------------------------------------
        self.state = 'idle'
        self.awareness_range = 100   # Distance at which enemy notices the player
        self.forget_range = 210      # Distance at which enemy gives up chasing

        self.direction = 'down'      # Facing direction: 'up' | 'down' | 'left' | 'right'

        # Idle wandering state
        self.idle_timer = 0
        self.idle_wait_time = 1.5           # Seconds to stand still between wanders
        self.idle_move_timer = 0
        self.idle_move_duration = 1.5       # Seconds to walk before stopping again
        self.idle_direction = None
        self.spawn_x = x
        self.spawn_y = y
        self.max_idle_distance = 100        # Won't wander further than this from spawn
        self.is_idle_moving = False
        self.move_velocity_x = 0
        self.move_velocity_y = 0
        self.target_x = x
        self.target_y = y

        # -----------------------------------------------------------------
        # Combat — configure stats based on enemy category
        # -----------------------------------------------------------------
        self.is_attacking = False
        self.attack_timer = 0
        self.attack_cooldown = 0

        # Raw STR/POW values — kept alongside attack_damage (which only
        # ever holds whichever ONE of these applies to this enemy's
        # category) so anything that wants to display both stats at once
        # (see ui/scouter_menu.py's _get_data_stats) has something to
        # read. Overwritten with the real entity_creator-configured
        # values right after construction in game.py's spawn code (and
        # in BossEnemy.__init__ for bosses) — these are just the
        # hardcoded fallback an Enemy is born with before that happens.
        self.strength = 10
        self.power = 10

        if self.enemy_category == 'shooter':
            self.shooter_style = shooter_style  # 'bomb' = parabolic throw, 'bullet'/'rocket' = straight

            # Pull stat block from preset dict; fall back to 'bomb' if style is unrecognised
            preset = _SHOOTER_PRESETS.get(shooter_style, _SHOOTER_PRESETS['bomb'])
            (self.attack_duration,
             self.attack_cooldown_time,
             self.attack_range,
             self.preferred_distance,
             self.attack_damage,
             self.projectile_speed) = preset
            self.power = self.attack_damage

            self.projectiles = []  # Active projectiles owned by this enemy

            # Shooter melee rush — occasionally closes in for a close-range hit
            self.is_doing_melee_rush = False       # Currently charging the player
            self.is_shooter_melee_attack = False   # Current attack is a melee swing (not ranged)
            self.shooter_melee_range = 18          # Distance threshold to trigger the swing
            self.shooter_melee_damage = 12         # Damage dealt on a successful rush hit
            self.melee_rush_chance = 0.55          # 55% chance to rush when eligible
            self.last_melee_rush_attempt = 0
            self.melee_rush_check_interval = random.uniform(4.0, 7.0)
            self.melee_rush_cooldown = 0
            self.melee_rush_cooldown_time = random.uniform(5.0, 9.0)
            self.melee_rush_timer = 0              # Running duration — abort if too long
            self.melee_rush_max_duration = 3.0     # Abort rush after 3 seconds if no hit lands
            self.melee_rush_swung = False          # Prevents multiple swings per rush

            # Bomb spawning flags — game loop polls should_spawn_bomb each frame
            self.should_spawn_bomb = False
            self.bomb_spawned_this_attack = False
            self.bomb_target_x = 0
            self.bomb_target_y = 0
            self._pending_bomb_player = None       # Player ref saved at throw time for detonation

            # Active bombs/explosions owned by this enemy.
            # Include enemy.get_bomb_drawables() in your y-sorted draw list each frame.
            self.active_bombs = []

            # Bullet spawning flags
            self.should_spawn_bullet = False
            self.bullet_spawned_this_attack = False
            self.bullet_dx = 0.0
            self.bullet_dy = 0.0

            # Rocket spawning flags
            self.should_spawn_rocket = False
            self.rocket_spawned_this_attack = False
            self.rocket_dx = 0.0
            self.rocket_dy = 0.0

            # Ki-blast spawning flags — set by perform_attack (or BossEnemy.update for
            # bosses that gate the shot behind an animation), polled by game.py each frame
            self.should_spawn_kiblast = False
            self.kiblast_spawned_this_attack = False
            self.kiblast_dx = 0.0
            self.kiblast_dy = 0.0
        else:
            # Melee defaults
            self.attack_duration = 0.4
            self.attack_cooldown_time = 1.1
            self.attack_range = 15       # Very close range
            self.preferred_distance = 0  # Get as close as possible
            self.attack_damage = 10
            self.strength = self.attack_damage

        # Brief pause after completing an attack before moving again
        self.wait_after_attack = 0
        self.wait_after_attack_duration = 0.4

        # One-shot gate so perform_attack()'s melee damage (plain melee or a
        # shooter's melee-rush) lands exactly once per swing instead of once
        # per frame of the attack window — see try_attack()/perform_attack().
        self.melee_hit_this_attack = False

        # Knockback — driven by the hurt animation length
        self.is_knocked_back = False
        self.knockback_velocity_x = 0
        self.knockback_velocity_y = 0

        # Stun — applied by BurningAttack (or any future stun-capable
        # attack) via stun(). Freezes AI/movement entirely for a duration,
        # with no velocity/physics of its own (unlike knockback). See the
        # stun guard near the top of update()'s state machine, and
        # stun()/_end_stun() below near apply_knockback()/_end_knockback().
        self.is_stunned = False
        self.stun_timer = 0.0

        # World px the enemy is shoved back EACH FRAME a beam (e.g.
        # kamehameha) is touching it — see _push_from_beam() and the 'beam'
        # branch of check_collision_with_attack. A flat per-frame distance,
        # not a rate, since check_collision_with_attack doesn't receive dt.
        # Deliberately NOT routed through apply_knockback()/is_knocked_back:
        # that state machine is built for a single discrete impulse and only
        # clears once its hurt animation reports finished, which made
        # sustained multi-tick beam contact intermittently get stuck unable
        # to land another hit until that animation played all the way out.
        # A beam needs continuous push + continuous damage instead, with no
        # animation-completion gate in the way.
        self.beam_push_force = 3
        # Gates out normal AI movement for a short window after the beam
        # last pushed this enemy (same idea as is_knocked_back, but without
        # any of its animation-driven state), so the enemy's own
        # chase-toward-player logic doesn't just walk back into the push
        # and cancel it out. Re-armed to beam_freeze_grace every frame
        # contact holds; ticks down in update() otherwise, so normal AI
        # only resumes once contact has truly stopped for a beat.
        #
        # This used to be a single-frame flag (set True on a push frame,
        # consumed and cleared on the very next update()). That caused a
        # visible "chasing while being beamed" glitch: each push shoves the
        # enemy a few px further from the beam's origin, which the beam
        # itself only catches back up to once its own growth is applied
        # later that same frame (see _grow_beam() in game.py). That left a
        # 1-frame gap where check_collision_with_attack's world_length
        # check failed to re-confirm contact, the flag stayed cleared, and
        # the AI got exactly one frame to take a step toward the player
        # before the next frame's contact re-armed the freeze — repeating
        # for as long as the beam held. A short grace window survives that
        # single missed frame without leaving a truly-ended beam contact
        # frozen for long.
        self.beam_freeze_grace = 0.1   # seconds a freeze persists without fresh contact
        self.beam_freeze_timer = 0.0

        # Encased / rolling — applied by UltraVolleyballAttack contact (see
        # 'ultra_volleyball' branch of check_collision_with_attack) rather
        # than take_damage/apply_knockback. Same "freeze AI, early-return
        # in update()" shape as is_stunned, but with two sub-phases:
        #   is_encased  — enemy is hidden behind the casing overlay sprite,
        #                 fully immovable and immune to all other damage
        #                 (see the guard at the top of
        #                 check_collision_with_attack), for encased_duration
        #                 seconds unless a melee lands on the casing first
        #                 (see try_trigger_roll()).
        #   is_rolling  — casing is now being shoved in roll_direction
        #                 (whichever side the player struck from) at
        #                 roll_speed world px/sec until it hits a
        #                 collision, at which point the impact damages the
        #                 enemy and releases it — see _end_roll().
        # NOTE: the casing overlay uses dedicated ultra_volleyball_attack
        # art (a stationary ball, plus a separate 2-row rolling sheet —
        # see EncasementOverlay), not DestructibleStone's own destroy
        # animation as originally assumed — that asset never got wired in.
        self.is_encased = False
        self.encased_timer = 0.0
        self.encased_duration = 3.0     # seconds before auto-release if not rolled
        # Third sub-phase: the destroy overlay is playing its release
        # animation a second time (see _release_from_encasement()) — still
        # immovable/frozen, but no longer "encased" (the timer isn't
        # running, a melee can't re-trigger a roll). Ends on its own once
        # encasement_overlay.finished, at which point normal control
        # resumes — see the is_releasing branch in update().
        self.is_releasing = False
        self.encasement_overlay_asset = 'assets/sprites/attacks/ultra_volleyball_attack/ultra_volleyball_attack_ball.png'
        self.rolling_overlay_asset = 'assets/sprites/attacks/ultra_volleyball_attack/ultra_volleyball_attack_ball_rolling.png'
        self.encasement_overlay = None  # set by encase(), a small EncasementOverlay instance
        self.is_rolling = False
        self.roll_direction = 'down'
        self.roll_speed = 260            # world px/sec while rolling
        self.roll_velocity_x = 0.0
        self.roll_velocity_y = 0.0
        self.roll_impact_damage = 25

        # Brief invulnerability window after taking a hit (i-frames) — mirrors
        # the player's take_damage system. Decoupled from is_knocked_back so a
        # hit can land again while the enemy is still mid-stagger, once the
        # i-frame window has elapsed.
        self.invulnerable = False
        self.invulnerable_timer = 0
        self.invulnerable_duration = 0.2

        # Hurt tint — red flash that fades after taking damage
        self.hurt_tint = 0.0            # 1.0 = full red, 0.0 = no tint
        self.hurt_tint_duration = 0.45  # Seconds to fully fade back to normal

        # Tracks the most recent damage value applied so game.py can spawn a
        # damage number popup without needing to change take_damage's return type
        self.last_damage_dealt = 0
        self.last_hit_was_crit = False  # set on melee hits when game_config is passed in — see check_collision_with_attack

        self.draw_layer = DrawLayer.ENEMIES
        self.y_sort = True              # Participates in depth-sorted rendering

        # Injected by the room/game system after construction. Backed by a
        # property (see obstacles.setter below) so the expensive per-obstacle
        # classification/broad-phase-extent work happens once, when game.py
        # hands over the shared list (room load), instead of redoing it on
        # every single collision check.
        self._obstacles = []
        self._prepared_obstacles = []
        self._obstacle_grid = {}
        self.other_enemies = []  # For separation force and path-blocking checks

        # Stuck detection — if we haven't moved enough, increment stuck_timer
        self.stuck_timer = 0
        self.last_x = x
        self.last_y = y
        self.movement_threshold = 0.5

        # Each enemy takes a slightly different angle so they don't all stack on the same side
        self.preferred_angle = random.uniform(0, math.pi * 2)

        # Separation force — pushes overlapping enemies apart
        self.separation_force = 0
        self.separation_strength = 0.7
        self.min_separation_distance = 16   # Minimum horizontal clearance
        self.min_vertical_separation = 16   # Minimum vertical clearance

        # Pathfinding around obstacles — holds an alternate direction for a short commit window
        self.pathfind_direction = None
        self.pathfind_commit_timer = 0
        self.pathfind_commit_duration = 1   # Seconds to stick with the alternate route

        # -----------------------------------------------------------------
        # Advanced AI only — retreat / breather mechanics
        # -----------------------------------------------------------------
        self.low_health_threshold = 0.3     # Retreat when HP drops below 30%
        self.consecutive_hits = 0           # Hits received in quick succession
        self.last_hit_time = 0
        self.hit_combo_window = 2.0         # Reset combo counter after 2 seconds without a hit
        self.hit_combo_threshold = 3        # Trigger retreat after 3 back-to-back hits

        # Retreat chance and cooldown — staggered per instance so enemies don't all retreat at once
        self.retreat_chance = 0.35
        self.last_retreat_attempt = 0
        self.retreat_check_cooldown = random.uniform(1.5, 3.0)
        self.retreat_check_interval = random.uniform(1.5, 3.0)
        self.retreat_cooldown = 0
        self.retreat_cooldown_time = random.uniform(3.0, 5.0)

        # Retreat movement state
        self.is_retreating = False
        self.retreat_timer = 0
        self.retreat_duration = 2.0         # Seconds to actively back away
        self.retreat_distance = 150         # Target distance from player before switching to breather
        self.retreat_target_x = 0
        self.retreat_target_y = 0

        # Breather — brief rest after successfully retreating
        self.is_breathing = False
        self.breather_timer = 0
        self.breather_duration = 1.5        # Seconds to stand and recover

        # Feinting — backs away while still facing the player after an attack
        self.is_feinting = False
        self.feint_timer = 0
        self.feint_duration = random.uniform(0.6, 1.2)
        self.feint_distance = 18            # World units to drift back during the feint
        self.feint_chance = 0.20            # 20% chance to feint right after an attack
        self.last_feint_attempt = 0
        self.feint_check_cooldown = 0.5     # How soon after an attack to check for a feint
        self.feint_cooldown = 0
        self.feint_cooldown_time = random.uniform(5.0, 7.0)

        # Optional pause after a feint — keeps the player guessing
        self.is_pausing_after_feint = False
        self.pause_after_feint_timer = 0
        self.pause_after_feint_chance = 0.5
        self.pause_after_feint_duration = random.uniform(0.4, 0.8)

        # Blocking — advanced AI raises guard to soak an incoming hit.
        # _check_block() rolls a much higher chance while the player is
        # actively swinging nearby, so it reads as reacting to a real
        # attack rather than a random idle pose. Mirrors Player's own
        # block: take_damage()/apply_knockback() halve damage, clamp
        # knockback to a token nudge, and skip hurt tint/animation while
        # is_blocking is set — see those methods.
        self.is_blocking = False
        self.block_timer = 0
        self.block_chance_reactive = 0.6     # ...player mid-swing and close
        self.block_chance_idle = 0.05        # ...otherwise, rarely
        self.last_block_attempt = 0
        self.block_check_cooldown = random.uniform(0.3, 0.6)
        self.block_cooldown = 0
        self.block_cooldown_time = random.uniform(1.5, 3.0)

        # Death animation — plays the brown_destruction spritesheet before
        # self.active is set to False (which signals game.py to award XP / remove)
        self.is_dying = False
        self.death_frames = []
        self.death_frame_index = 0
        self.death_frame_duration = 0.08   # seconds per frame
        self.death_frame_timer = 0.0
        self._load_death_animation()

        # One-shot brown_destruction.png burst — reuses the same death_frames
        # spritesheet, but plays independently of is_dying. Fired alongside
        # the ultra_volleyball ball forming (encase()) and dissolving
        # (_release_from_encasement(), covering both the encased_timer
        # running out and a roll ending in collision) — see
        # _spawn_destruction_effect(). Ticked at the very top of update() so
        # it keeps playing even while the enemy is frozen in the encased/
        # rolling/releasing/dying branches below.
        self.destruction_effect_active = False
        self.destruction_effect_frame_index = 0
        self.destruction_effect_timer = 0.0
        self.destruction_effect_x = 0
        self.destruction_effect_y = 0

    # =========================================================================
    # Utility helpers
    # =========================================================================

    def _load_death_animation(self):
        """Load brown_destruction.png spritesheet as a sequence of 32×32 frames."""
        try:
            sheet = pygame.image.load('assets/objects/brown_destruction.png').convert_alpha()
            frame_w = 32
            frame_h = 32
            num_frames = sheet.get_width() // frame_w
            for i in range(num_frames):
                frame = pygame.Surface((frame_w, frame_h), pygame.SRCALPHA)
                frame.blit(sheet, (0, 0), (i * frame_w, 0, frame_w, frame_h))
                self.death_frames.append(frame)
        except Exception:
            # Fallback: simple shrinking-circle animation
            for i in range(6):
                frame = pygame.Surface((32, 32), pygame.SRCALPHA)
                alpha = int(255 * (1.0 - i / 6))
                radius = int(16 * (1.0 - i / 6 * 0.5))
                pygame.draw.circle(frame, (180, 80, 40, alpha), (16, 16), max(1, radius))
                self.death_frames.append(frame)

    def _spawn_destruction_effect(self):
        """Start (or restart) the one-shot brown_destruction.png burst at
        the enemy's current position. Called from encase() when the ball
        forms and from _release_from_encasement() when it dissolves, so
        the effect plays simultaneously with the casing overlay's own
        intro/outro animation rather than replacing it."""
        self.destruction_effect_active = True
        self.destruction_effect_frame_index = 0
        self.destruction_effect_timer = 0.0
        self.destruction_effect_x = self.x
        self.destruction_effect_y = self.y

    def is_standing_still(self):
        """True while the enemy is locked in an animation and not moving."""
        return (self.is_attacking
                or self.wait_after_attack > 0
                or self.is_breathing
                or self.is_feinting
                or self.is_pausing_after_feint
                or self.is_blocking)

    def distance_to(self, x, y):
        """Euclidean distance from this enemy's centre to (*x*, *y*)."""
        dx = self.x - x
        dy = self.y - y
        return math.sqrt(dx * dx + dy * dy)

    def distance_to_spawn(self, x, y):
        """Euclidean distance from (*x*, *y*) to this enemy's spawn point."""
        dx = x - self.spawn_x
        dy = y - self.spawn_y
        return math.sqrt(dx * dx + dy * dy)

    def get_sort_key(self):
        """Depth-sort key: draw_layer first, then feet position (y + height/2)."""
        return (self.draw_layer, self.y + self.height // 2)

    def get_collision_rect(self):
        """Return a centred pygame.Rect for this enemy."""
        return pygame.Rect(
            self.x - self.width // 2,
            self.y - self.height // 2,
            self.width,
            self.height,
        )

    def _direction_to_vector(self):
        """Return the (dx, dy) unit vector for the current facing direction.

        Used to derive knockback direction from the attacker's facing without
        duplicating the lookup table all over perform_attack and check_collision.
        """
        return _KNOCKBACK_VECTORS.get(self.direction, (0.0, 1.0))

    def _snap_to_cardinal(self, raw_dx, raw_dy):
        """Snap (raw_dx, raw_dy) to the nearest cardinal axis and update self.direction.

        Whichever offset is larger wins. Used by bullet and rocket spawning so
        projectiles always travel in a clean horizontal or vertical line.
        Returns the snapped (dx, dy) unit vector.
        """
        if abs(raw_dx) >= abs(raw_dy):
            dx = 1.0 if raw_dx > 0 else -1.0
            dy = 0.0
            self.direction = 'right' if raw_dx > 0 else 'left'
        else:
            dx = 0.0
            dy = 1.0 if raw_dy > 0 else -1.0
            self.direction = 'down' if raw_dy > 0 else 'up'
        return dx, dy

    def _move_checked(self, dx, dy, speed, world_width, world_height):
        """Apply (dx, dy)*speed with per-axis obstacle and bounds checking.

        Each axis is tested independently so the enemy can slide along walls
        instead of stopping dead when one axis is blocked.
        """
        new_x = self.x + dx * speed
        new_y = self.y + dy * speed
        half_w = self.width // 2
        half_h = self.height // 2

        if not self.check_collision_with_obstacles(new_x, self.y) and half_w <= new_x <= world_width - half_w:
            self.x = new_x
        if not self.check_collision_with_obstacles(self.x, new_y) and half_h <= new_y <= world_height - half_h:
            self.y = new_y

    def set_direction_from_movement(self, dx, dy, threshold=0.2):
        """Set facing direction from a movement vector, with hysteresis.

        The threshold prevents rapid direction-flipping on diagonal movement —
        one component must be clearly larger to cause a direction change.
        """
        abs_dx = abs(dx)
        abs_dy = abs(dy)

        if abs_dx > abs_dy + threshold:
            self.direction = 'right' if dx > 0 else 'left'
        elif abs_dy > abs_dx + threshold:
            self.direction = 'down' if dy > 0 else 'up'
        else:
            # Diagonal movement — try to maintain the current axis to avoid jitter
            current_is_horizontal = self.direction in ('left', 'right')
            current_is_vertical = self.direction in ('up', 'down')

            if current_is_horizontal and abs_dx > 0.05:
                self.direction = 'right' if dx > 0 else 'left'
            elif current_is_vertical and abs_dy > 0.05:
                self.direction = 'down' if dy > 0 else 'up'
            else:
                # No clear winner — fall back to the dominant raw component
                if abs_dx >= abs_dy:
                    self.direction = 'right' if dx > 0 else 'left'
                else:
                    self.direction = 'down' if dy > 0 else 'up'

    # =========================================================================
    # Collision detection
    # =========================================================================

    @property
    def obstacles(self):
        return self._obstacles

    # World-space size of one obstacle-grid cell (see obstacles.setter). Chosen
    # to be a few tiles wide — big enough that a typical entity's query only
    # touches 1-4 cells, small enough that a heavy room's obstacles spread
    # out across many cells instead of all piling into one.
    _OBSTACLE_GRID_CELL = 128

    @obstacles.setter
    def obstacles(self, value):
        """game.py hands every enemy the same shared obstacle list once per
        room load (see Game._assign_obstacles) — not per frame. So the
        classification work check_collision_with_obstacles used to redo on
        every single call (hasattr() branching to figure out what KIND of
        obstacle each one is) happens exactly once here instead, and is
        cached in self._prepared_obstacles alongside a rough broad-phase
        center/radius per obstacle so the hot path can reject far-away
        obstacles with a cheap squared-distance check before touching
        hasattr, Rect construction, or colliderect at all.

        On top of that, obstacles are bucketed into a uniform spatial grid
        (self._obstacle_grid) keyed by cell coordinate. A heavy room can have
        several hundred shared obstacles, and check_collision_with_obstacles
        used to scan every single one of them per call regardless — with
        dozens of enemies each calling it several times a frame (more once
        stuck and probing 8 directions), that per-call cost scales with the
        *whole room's* obstacle count, which is what made unusually large
        rooms specifically tank the framerate. The grid means a call only
        has to look at obstacles actually near the query position — a
        handful of cells' worth — rather than the entire room, so cost
        scales with local obstacle density instead of room size. Each
        obstacle is inserted into every cell its (center, radius) bounding
        box touches, so a query that gathers every obstacle whose bounding
        box could reach the query position is guaranteed not to miss one
        (see check_collision_with_obstacles).
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
        """One-time per-obstacle setup: figure out which of the three
        collision "kinds" it is (same precedence the old inline hasattr
        chain used), plus an approximate (center_x, center_y, radius) for
        the broad-phase distance check. Positions are assumed static for
        the lifetime of a room's obstacle list — true for walls, stones,
        gates, chests, and decorations alike."""
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
        possibly reach a query centered at (cx, cy) with the given radius,
        by only visiting the grid cells that box overlaps — see
        obstacles.setter for how the grid is built and why this can't miss
        an obstacle that's actually in range.
        """
        grid = getattr(self, '_obstacle_grid', None)
        if not grid:
            return self._prepared_obstacles

        cell = self._OBSTACLE_GRID_CELL
        min_cx = int((cx - radius) // cell)
        max_cx = int((cx + radius) // cell)
        min_cy = int((cy - radius) // cell)
        max_cy = int((cy + radius) // cell)

        # Fast path: query touches a single cell (the overwhelmingly common
        # case — entities are small relative to the grid cell size).
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
        """Return True if the given position overlaps any active obstacle.

        Broad-phase first: each obstacle was pre-classified and given an
        approximate center/radius once, in the obstacles setter above, so
        here we can reject anything clearly too far away with a single
        squared-distance compare — no hasattr(), no Rect, no colliderect —
        before paying for the precise check. On top of that, candidates now
        come from the spatial grid (_nearby_prepared_obstacles) instead of
        the room's full obstacle list, so a heavy room's several hundred
        shared obstacles no longer have to be scanned on every single call —
        this is called several times per enemy per frame (plus 8x more
        inside find_clear_path_around_obstacle whenever an enemy is stuck),
        so keeping the per-call cost proportional to nearby obstacles rather
        than total room obstacles is what keeps a heavy room affordable.
        """
        self_half_w = self.width // 2
        self_half_h = self.height // 2
        self_reject_radius = math.hypot(self_half_w, self_half_h)

        temp_rect = None  # built lazily, only once we have a real candidate

        candidates = self._nearby_prepared_obstacles(new_x, new_y, self_reject_radius)
        for obstacle, kind, approx_x, approx_y, reject_radius in candidates:
            dx = approx_x - new_x
            dy = approx_y - new_y
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
                temp_rect = pygame.Rect(new_x - self_half_w, new_y - self_half_h, self.width, self.height)

            if temp_rect.colliderect(obstacle_rect):
                return True

        return False

    def is_path_blocked_by_enemy(self, target_x, target_y):
        """Return True if another enemy is standing directly in the path to (target_x, target_y).

        Uses a perpendicular-distance check rather than a full raycast —
        fast enough for every-frame use and works well in practice.
        """
        dx = target_x - self.x
        dy = target_y - self.y
        distance_to_target = math.sqrt(dx * dx + dy * dy)

        if distance_to_target < 0.1:
            return False

        # Unit vector toward target
        dx /= distance_to_target
        dy /= distance_to_target

        blocking_distance = 40  # How close to the path line an enemy must be to count as blocking

        for other in self.other_enemies:
            if other is self or not other.active:
                continue

            to_x = other.x - self.x
            to_y = other.y - self.y
            dist_to_other = math.sqrt(to_x * to_x + to_y * to_y)

            if dist_to_other < 0.1:
                continue

            # Project the vector-to-other onto the direction-to-target
            dot = to_x * dx + to_y * dy

            # Only count enemies ahead of us and closer than the target
            if dot <= 0 or dot > distance_to_target:
                continue

            # Perpendicular distance from path line to the other enemy
            perp_x = to_x - dx * dot
            perp_y = to_y - dy * dot
            if math.sqrt(perp_x * perp_x + perp_y * perp_y) < blocking_distance:
                return True

        return False

    def calculate_separation_force(self):
        """Return a (sep_x, sep_y) unit vector that pushes this enemy away from neighbours.

        Standing enemies (attacking/waiting) exert extra force so moving enemies
        naturally route around them instead of piling up.
        """
        separation_x = 0.0
        separation_y = 0.0
        count = 0

        for other in self.other_enemies:
            if other is self or not other.active:
                continue

            dx = other.x - self.x
            dy = other.y - self.y
            distance = math.sqrt(dx * dx + dy * dy)

            if distance < 0.1:
                continue

            # Use horizontal or vertical minimum distance based on relative position
            min_distance = (self.min_separation_distance
                            if abs(dx) > abs(dy)
                            else self.min_vertical_separation)

            if distance < min_distance:
                dx /= distance
                dy /= distance

                # Proportional force — closer means stronger push
                force = (min_distance - distance) / min_distance

                # Standing enemies push harder to make movers go around them
                if other.is_standing_still():
                    force *= 2.0

                separation_x -= dx * force
                separation_y -= dy * force
                count += 1

        if count > 0:
            separation_x /= count
            separation_y /= count

            # Normalise and scale by separation_strength
            mag = math.sqrt(separation_x ** 2 + separation_y ** 2)
            if mag > 0:
                separation_x = (separation_x / mag) * self.separation_strength
                separation_y = (separation_y / mag) * self.separation_strength

        return separation_x, separation_y

    def find_open_angle_around_player(self, player):
        """Find the least-crowded sector around the player and return a target position there.

        Divides the circle into 8 sectors, counts enemies in each, then picks
        the emptiest one closest to this enemy's current approach angle.
        Returns (target_x, target_y).
        """
        num_sectors = 8
        sector_counts = [0] * num_sectors

        for other in self.other_enemies:
            if other is self or not other.active:
                continue
            if other.distance_to(player.x, player.y) > 80:
                continue  # Only count enemies already near the player

            angle = math.atan2(other.y - player.y, other.x - player.x)
            sector = int((angle + math.pi) / (2 * math.pi / num_sectors)) % num_sectors
            sector_counts[sector] += 1

        # Find the emptiest sector(s) and pick the one nearest to our current angle
        min_count = min(sector_counts)
        open_sectors = [i for i, c in enumerate(sector_counts) if c == min_count]

        current_angle = math.atan2(self.y - player.y, self.x - player.x)
        current_sector = int((current_angle + math.pi) / (2 * math.pi / num_sectors)) % num_sectors

        best_sector = min(
            open_sectors,
            key=lambda s: min(abs(s - current_sector), num_sectors - abs(s - current_sector))
        )

        target_angle = (best_sector * 2 * math.pi / num_sectors) - math.pi
        attack_distance = self.attack_range * 0.8  # Close enough to attack from this position

        return (player.x + math.cos(target_angle) * attack_distance,
                player.y + math.sin(target_angle) * attack_distance)

    def find_clear_path_around_obstacle(self, target_x, target_y, move_speed):
        """Sample 8 directions and return the one that makes the most progress toward the target.

        Returns a (dx, dy) unit vector, or None if all directions are blocked.
        """
        num_samples = 8
        best_direction = None
        best_score = -999999

        target_dx = target_x - self.x
        target_dy = target_y - self.y
        target_angle = math.atan2(target_dy, target_dx)

        for i in range(num_samples):
            angle = i * 2 * math.pi / num_samples
            test_dx = math.cos(angle)
            test_dy = math.sin(angle)

            test_x = self.x + test_dx * move_speed * 2
            test_y = self.y + test_dy * move_speed * 2

            if self.check_collision_with_obstacles(test_x, test_y):
                continue  # Skip blocked directions entirely

            # Score by angular closeness to target, with a bonus for positive dot product
            angle_diff = abs(math.atan2(test_dy, test_dx) - target_angle)
            if angle_diff > math.pi:
                angle_diff = 2 * math.pi - angle_diff

            score = -angle_diff + (test_dx * target_dx + test_dy * target_dy) * 0.5

            if score > best_score:
                best_score = score
                best_direction = (test_dx, test_dy)

        return best_direction

    def update_stuck_detection(self, dt):
        """Increment stuck_timer when not making meaningful progress; decay it when moving.

        stuck_timer > ~0.3 is used elsewhere to trigger pathfinding.
        """
        moved = math.sqrt((self.x - self.last_x) ** 2 + (self.y - self.last_y) ** 2)

        if moved < self.movement_threshold:
            self.stuck_timer += dt
        else:
            self.stuck_timer = max(0, self.stuck_timer - dt * 2)  # Decay faster than it builds

        self.last_x = self.x
        self.last_y = self.y

    # =========================================================================
    # Main update loop
    # =========================================================================

    def update(self, dt, player, world_width, world_height, obstacles=None, game_config=None):
        """Advance the enemy AI state machine by *dt* seconds.

        Handles sprite animation, bomb ticks, cooldown timers, knockback
        physics, attack resolution, and all AI behaviour branches (idle,
        chase, retreat, feint, melee rush).

        game_config is optional and only used to mitigate this enemy's
        own melee-contact damage against the player by the player's END
        (see perform_attack()/Player.get_incoming_melee_damage) — if not
        passed, that damage falls back to the flat, unmitigated value.
        """
        if not self.active:
            return

        # Brown_destruction.png burst — ticks unconditionally so it plays
        # out fully whether the enemy is dying, encased, rolling, or
        # releasing below (see _spawn_destruction_effect()).
        if self.destruction_effect_active:
            self.destruction_effect_timer += dt
            if self.destruction_effect_timer >= self.death_frame_duration:
                self.destruction_effect_timer -= self.death_frame_duration
                self.destruction_effect_frame_index += 1
                if self.destruction_effect_frame_index >= len(self.death_frames):
                    self.destruction_effect_active = False

        # While the death animation is playing, tick it and skip all AI/physics
        if self.is_dying:
            self.death_frame_timer += dt
            if self.death_frame_timer >= self.death_frame_duration:
                self.death_frame_timer = 0.0
                self.death_frame_index += 1
                if self.death_frame_index >= len(self.death_frames):
                    # Animation complete — signal game.py to award XP and remove
                    self.is_dying = False
                    self.active = False
            return

        # ------------------------------------------------------------------
        # Encased / rolling — same "early return, nothing else runs" shape
        # as is_dying above: while the casing overlay is what's on screen,
        # the enemy's own sprite/AI/knockback/stun never tick.
        # ------------------------------------------------------------------
        if self.is_rolling:
            self.stuck_timer = 0
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0

            new_x = self.x + self.roll_velocity_x * dt
            new_y = self.y + self.roll_velocity_y * dt
            blocked_x = self.check_collision_with_obstacles(new_x, self.y)
            blocked_y = self.check_collision_with_obstacles(self.x, new_y)
            if not blocked_x:
                self.x = new_x
            if not blocked_y:
                self.y = new_y
            self.x = max(self.width // 2, min(self.x, world_width - self.width // 2))
            self.y = max(self.height // 2, min(self.y, world_height - self.height // 2))

            if self.encasement_overlay:
                self.encasement_overlay.update(dt)

            if blocked_x or blocked_y:
                self._end_roll()
            return

        if self.is_encased:
            self.stuck_timer = 0
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0

            if self.encasement_overlay:
                self.encasement_overlay.update(dt)

            self.encased_timer -= dt
            if self.encased_timer <= 0:
                self._release_from_encasement()
            return

        if self.is_releasing:
            self.stuck_timer = 0
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0

            if self.encasement_overlay:
                self.encasement_overlay.update(dt)
                if self.encasement_overlay.finished:
                    self.is_releasing = False
                    self.encasement_overlay = None
                    if self.has_sprite:
                        self.sprite.set_animation('idle', self.direction)
            else:
                self.is_releasing = False
            return

        # Advance sprite animation frame
        if self.has_sprite:
            self.sprite.update(dt)

        # Tick all owned bombs — pass player so detonation always has a reference
        if self.enemy_category == 'shooter' and self.active_bombs:
            for bomb in self.active_bombs:
                bomb.update(dt, player)
            # Purge fully spent bombs (exploded AND explosion animation done)
            self.active_bombs = [
                b for b in self.active_bombs
                if not (b.state == b.STATE_EXPLODED
                        and (b.pending_explosion is None or not b.pending_explosion.active))
            ]

        # Tick down all cooldown timers
        if self.attack_cooldown > 0:
            self.attack_cooldown -= dt
        if self.wait_after_attack > 0:
            self.wait_after_attack -= dt
        if self.retreat_cooldown > 0:
            self.retreat_cooldown -= dt
        if self.feint_cooldown > 0:
            self.feint_cooldown -= dt
        if self.block_cooldown > 0:
            self.block_cooldown -= dt
        if self.enemy_category == 'shooter' and self.melee_rush_cooldown > 0:
            self.melee_rush_cooldown -= dt

        # Fade out the hurt tint each frame
        if self.hurt_tint > 0:
            self.hurt_tint = max(0.0, self.hurt_tint - dt / self.hurt_tint_duration)

        # I-frame timer — ticks regardless of knockback state so the window
        # can fully expire (and the enemy become hittable again) even while
        # still mid-stagger.
        if self.invulnerable:
            self.invulnerable_timer -= dt
            if self.invulnerable_timer <= 0:
                self.invulnerable = False

        self.update_stuck_detection(dt)

        # Let the committed pathfinding direction expire naturally
        if self.pathfind_commit_timer > 0:
            self.pathfind_commit_timer -= dt
            if self.pathfind_commit_timer <= 0:
                self.pathfind_direction = None

        # ------------------------------------------------------------------
        # Knockback — runs physics and returns early; no AI while staggered
        # ------------------------------------------------------------------
        if self.is_knocked_back:
            self.stuck_timer = 0
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0

            new_x = self.x + self.knockback_velocity_x * dt
            new_y = self.y + self.knockback_velocity_y * dt

            # Per-axis collision so the enemy slides along walls
            if not self.check_collision_with_obstacles(new_x, self.y):
                self.x = new_x
            else:
                self.knockback_velocity_x = 0

            if not self.check_collision_with_obstacles(self.x, new_y):
                self.y = new_y
            else:
                self.knockback_velocity_y = 0

            # Clamp to room bounds
            self.x = max(self.width // 2, min(self.x, world_width - self.width // 2))
            self.y = max(self.height // 2, min(self.y, world_height - self.height // 2))

            # Friction — velocity decays quickly
            self.knockback_velocity_x *= 0.9
            self.knockback_velocity_y *= 0.9

            # End knockback when hurt animation finishes (or velocity is negligible)
            if self.has_sprite and self.sprite.is_animation_finished():
                self._end_knockback()
            elif not self.has_sprite:
                if abs(self.knockback_velocity_x) < 1 and abs(self.knockback_velocity_y) < 1:
                    self._end_knockback()
            return

        # ------------------------------------------------------------------
        # Stun — from BurningAttack (or similar). Same "freeze AI, return
        # early" shape as knockback/beam-freeze, but with no physics of its
        # own — the enemy just holds still until the timer runs out. Checked
        # after knockback so a hit that both knocks back AND stuns plays out
        # its knockback stagger first, then stays frozen for whatever's left
        # of the stun once knockback ends.
        # ------------------------------------------------------------------
        if self.is_stunned:
            self.stun_timer -= dt
            self.stuck_timer = 0
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0
            if self.stun_timer <= 0:
                self._end_stun()
            return

        # ------------------------------------------------------------------
        # Beam push — consume last frame's flag and skip normal AI movement
        # for this frame. The actual push happens directly in
        # check_collision_with_attack (called after update() each frame in
        # game.py's loop) via _push_from_beam(), not here — this just stops
        # the enemy's own chase-toward-player logic from immediately
        # walking back into it. Re-armed every frame the beam is still
        # touching; once it stops, nothing sets this again and normal AI
        # resumes on its own next frame.
        # ------------------------------------------------------------------
        if self.beam_freeze_timer > 0:
            self.beam_freeze_timer -= dt
            self.stuck_timer = 0
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0
            return

        # ------------------------------------------------------------------
        # Attack animation — holds control until attack_timer expires
        # ------------------------------------------------------------------
        if self.is_attacking:
            self.attack_timer -= dt
            if self.attack_timer <= 0:
                self.is_attacking = False
                self.wait_after_attack = self.wait_after_attack_duration

                # If this was a shooter melee rush swing, wrap it up
                if getattr(self, 'is_shooter_melee_attack', False):
                    self.is_shooter_melee_attack = False
                    self.is_doing_melee_rush = False
                    self.melee_rush_timer = 0
                    self.melee_rush_swung = False
                    self.melee_rush_cooldown = self.melee_rush_cooldown_time

                if self.has_sprite:
                    self.sprite.set_animation('idle', self.direction)
            else:
                # Trigger the actual hit/spawn during the last 0.4 s of the animation
                if self.attack_timer <= 0.4:
                    self.perform_attack(player, game_config)
            return

        # ------------------------------------------------------------------
        # Dead player — nothing left to fight. Drops straight back to plain
        # idle wandering instead of chasing, retreating, feinting, blocking,
        # or rushing at a corpse. Placed after the is_attacking block above
        # so a swing already mid-animation when the player died finishes out
        # normally (harmless now that Player.take_damage() no-ops while
        # is_dead — see player.py) rather than being cut off mid-frame.
        # ------------------------------------------------------------------
        if getattr(player, 'is_dead', False):
            if self.state != 'idle':
                self.state          = 'idle'
                self.idle_timer     = 0
                # Wipe any wander leg left over from before the chase —
                # otherwise is_idle_moving/target_x/target_y can still be
                # whatever they were pre-chase, and idle_behavior() below
                # will either sit still on a stale 'walk' frame (if it was
                # False, mid-wait) or start sliding toward a long-stale
                # target without re-triggering the 'walk' animation (if it
                # was True) — animation and motion silently fall out of
                # sync. Forcing 'idle' now guarantees the visible frame
                # matches "not moving yet" for at least this frame.
                self.is_idle_moving  = False
                self.idle_move_timer = 0
                if self.has_sprite:
                    self.sprite.set_animation('idle', self.direction)
            self.is_retreating          = False
            self.is_breathing           = False
            self.is_feinting            = False
            self.is_pausing_after_feint = False
            self.is_blocking            = False
            if self.enemy_category == 'shooter':
                self.is_doing_melee_rush = False
            self.idle_behavior(dt, world_width, world_height)
            return

        # ------------------------------------------------------------------
        # State transitions
        # ------------------------------------------------------------------
        player_distance = self.distance_to(player.x, player.y)

        if self.state == 'idle' and player_distance < self.awareness_range:
            self.state = 'chase'
            self.stuck_timer = 0
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0

        elif self.state == 'chase' and player_distance > self.forget_range:
            self.state = 'idle'
            self.idle_timer = 0
            self.stuck_timer = 0
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0
            # Same stale-wander-leg problem as the dead-player branch above:
            # is_idle_moving/target_x/target_y may still hold whatever they
            # were the moment this enemy started chasing, long before it
            # ended up wherever it is now. Clear them so idle_behavior()
            # starts a fresh wait/leg instead of resuming a leg toward a
            # now-meaningless target (which would move the enemy without
            # ever re-arming the 'walk' animation below).
            self.is_idle_moving = False
            self.idle_move_timer = 0
            if self.has_sprite:
                self.sprite.set_animation('idle', self.direction)

        # ------------------------------------------------------------------
        # Advanced AI decision checks (retreat, melee rush, feint)
        # ------------------------------------------------------------------
        if self.ai_type == 'advanced' and not self.is_attacking and self.state == 'chase':
            self._check_retreat(player)
            if self.enemy_category == 'shooter':
                self._check_melee_rush()
            if self.enemy_category == 'melee':
                self._check_feint()
            self._check_block(player)

        # ------------------------------------------------------------------
        # Behaviour dispatch — priority order matters here
        # ------------------------------------------------------------------
        if not self.is_attacking:
            if self.is_retreating or self.is_breathing:
                self.retreat_behavior(dt, player, world_width, world_height)
            elif self.enemy_category == 'shooter' and getattr(self, 'is_doing_melee_rush', False):
                self.shooter_melee_rush_behavior(dt, player, world_width, world_height)
            elif self.is_feinting:
                self.feint_behavior(dt, player, world_width, world_height)
            elif self.is_pausing_after_feint:
                self.pause_after_feint_behavior(dt, player)
            elif self.is_blocking:
                self.block_behavior(dt, player)
            elif self.state == 'idle':
                self.idle_behavior(dt, world_width, world_height)
            elif self.state == 'chase':
                self.chase_and_attack(dt, player, world_width, world_height)

    # =========================================================================
    # Advanced AI checks (called from update)
    # =========================================================================

    def _check_retreat(self, player):
        """Decide whether to start retreating — called once per frame while chasing."""
        current_time = time.time()
        health_pct = self.hp / self.max_hp

        can_check = (current_time - self.last_retreat_attempt) >= self.retreat_check_cooldown
        can_retreat = self.retreat_cooldown <= 0
        should_retreat = (
            health_pct < self.low_health_threshold
            or self.consecutive_hits >= self.hit_combo_threshold
        )

        if should_retreat and can_check and can_retreat:
            self.last_retreat_attempt = current_time
            self.retreat_check_cooldown = random.uniform(1.5, 3.0)  # Stagger future checks

            if random.random() < self.retreat_chance:
                if not self.is_retreating and not self.is_breathing:
                    self.is_retreating = True
                    self.retreat_timer = self.retreat_duration
                    self.pathfind_direction = None
                    self.pathfind_commit_timer = 0

    def _check_melee_rush(self):
        """Decide whether to start a melee rush — called for advanced shooters while chasing."""
        if self.is_doing_melee_rush or self.melee_rush_cooldown > 0 or self.wait_after_attack > 0:
            return

        current_time = time.time()
        if (current_time - self.last_melee_rush_attempt) >= self.melee_rush_check_interval:
            self.last_melee_rush_attempt = current_time
            self.melee_rush_check_interval = random.uniform(4.0, 7.0)

            if random.random() < self.melee_rush_chance:
                self.is_doing_melee_rush = True
                self.melee_rush_timer = 0
                self.melee_rush_swung = False
                self.pathfind_direction = None
                self.pathfind_commit_timer = 0

    def _check_feint(self):
        """Decide whether to start a feint — only valid in the wait window after an attack."""
        if self.wait_after_attack <= 0:
            return  # Feint is only eligible right after an attack
        if self.is_retreating or self.is_breathing or self.is_feinting or self.is_pausing_after_feint:
            return

        current_time = time.time()
        can_check = (current_time - self.last_feint_attempt) >= self.feint_check_cooldown
        can_feint = self.feint_cooldown <= 0

        if can_check and can_feint:
            self.last_feint_attempt = current_time
            if random.random() < self.feint_chance:
                self.is_feinting = True
                self.feint_timer = random.uniform(0.6, 1.2)
                self.wait_after_attack = 0  # Start feinting immediately
                self.pathfind_direction = None
                self.pathfind_commit_timer = 0

    def _check_block(self, player):
        """Decide whether to raise guard — called once per frame while chasing.

        Rolls a much higher chance while the player is actively mid-swing
        and close enough to plausibly be swinging at THIS enemy, so
        blocking reads as reacting to an incoming hit rather than a random
        idle pose. Falls back to a rare idle-chance roll otherwise so
        guarding isn't purely reactive.
        """
        if self.is_blocking or self.block_cooldown > 0:
            return
        if self.is_knocked_back or self.is_stunned:
            return

        current_time = time.time()
        if (current_time - self.last_block_attempt) < self.block_check_cooldown:
            return
        self.last_block_attempt = current_time
        self.block_check_cooldown = random.uniform(0.2, 0.4)

        player_swinging = getattr(player, 'is_attacking', False) or getattr(player, 'is_punching', False)
        close_enough = self.distance_to(player.x, player.y) < self.attack_range * 3

        chance = self.block_chance_reactive if (player_swinging and close_enough) else self.block_chance_idle
        if random.random() < chance:
            self.is_blocking = True
            self.block_timer = random.uniform(0.4, 0.9)
            if self.has_sprite:
                self.sprite.set_animation('blocking', self.direction)

    # =========================================================================
    # Behaviour methods
    # =========================================================================

    def apply_separation_force(self, dt):
        """Nudge this enemy away from neighbours to prevent piling.

        Skipped while the enemy is frozen (attacking/waiting) or being knocked back.
        """
        if self.is_knocked_back or self.is_standing_still():
            return

        sep_x, sep_y = self.calculate_separation_force()

        if abs(sep_x) > 0.1 or abs(sep_y) > 0.1:
            move_speed = self.speed * 30 * dt
            new_x = self.x + sep_x * move_speed
            new_y = self.y + sep_y * move_speed

            if not self.check_collision_with_obstacles(new_x, self.y):
                self.x = new_x
            if not self.check_collision_with_obstacles(self.x, new_y):
                self.y = new_y

    def shooter_melee_rush_behavior(self, dt, player, world_width, world_height):
        """Charge the player and land one melee swing, then return to shooting range.

        The rush aborts automatically if it takes longer than melee_rush_max_duration.
        """
        self.melee_rush_timer += dt

        # Safety valve — abort if the rush drags on too long
        if self.melee_rush_timer >= self.melee_rush_max_duration:
            self.is_doing_melee_rush = False
            self.melee_rush_timer = 0
            self.melee_rush_swung = False
            self.melee_rush_cooldown = self.melee_rush_cooldown_time
            return

        distance = self.distance_to(player.x, player.y)

        # Close enough to swing — trigger melee attack and end the rush
        if distance < self.shooter_melee_range and not self.melee_rush_swung:
            self.melee_rush_swung = True
            self.is_shooter_melee_attack = True
            self.is_attacking = True
            self.attack_timer = 0.4  # Melee swing lasts 0.4 s
            self.melee_hit_this_attack = False

            dx = player.x - self.x
            dy = player.y - self.y
            self.set_direction_from_movement(dx, dy)

            if self.has_sprite:
                self.sprite.set_animation('melee', self.direction)
            return

        # Not yet in range — charge toward the player at a sprint
        dx = player.x - self.x
        dy = player.y - self.y
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > 0.1:
            dx /= dist
            dy /= dist

        self.set_direction_from_movement(dx, dy)
        if self.has_sprite:
            self.sprite.set_animation('walk', self.direction)

        move_speed = self.speed * 70 * dt  # Slightly faster than a normal chase

        # Obstacle pathfinding during the rush
        new_x = self.x + dx * move_speed
        new_y = self.y + dy * move_speed
        is_blocked = self.check_collision_with_obstacles(new_x, new_y)

        if self.pathfind_commit_timer > 0 and self.pathfind_direction is not None:
            dx, dy = self.pathfind_direction
        elif is_blocked or self.stuck_timer > 0.3:
            clear_path = self.find_clear_path_around_obstacle(player.x, player.y, move_speed)
            if clear_path is not None:
                self.pathfind_direction = clear_path
                self.pathfind_commit_timer = self.pathfind_commit_duration
                dx, dy = clear_path

        self._move_checked(dx, dy, move_speed, world_width, world_height)

    def chase_and_attack(self, dt, player, world_width, world_height):
        """Main chase/attack behaviour — used for both melee and shooter enemies.

        Shooters maintain preferred_distance and require cardinal alignment before
        firing; melee enemies close in as fast as possible.
        """
        distance = self.distance_to(player.x, player.y)

        # While in the post-attack wait, stand still and track the player's position
        if self.wait_after_attack > 0:
            if self.has_sprite:
                self.sprite.set_animation('idle', self.direction)
            self.set_direction_from_movement(player.x - self.x, player.y - self.y)
            return

        # ------------------------------------------------------------------
        # Attack opportunity check
        # ------------------------------------------------------------------
        if distance < self.attack_range and self.attack_cooldown <= 0:
            if self.enemy_category == 'shooter':
                min_attack_distance = 50  # Very permissive minimum so close-in shots still fire

                if self.shooter_style in ('bullet', 'rocket', 'kiblast'):
                    raw_dx = player.x - self.x
                    raw_dy = player.y - self.y
                    alignment_tolerance = 20  # World-unit leeway on cardinal alignment
                    cardinally_aligned = (abs(raw_dx) < alignment_tolerance
                                          or abs(raw_dy) < alignment_tolerance)

                    if cardinally_aligned and distance >= min_attack_distance:
                        # Snap facing direction before the animation so it looks correct
                        self._snap_to_cardinal(raw_dx, raw_dy)
                        self.try_attack(player)
                        return
                    # Not aligned — fall through to movement so the gunner strafes into line
                elif distance >= min_attack_distance:
                    self.try_attack(player)
                    return
            else:
                # Melee: attack whenever in range
                self.try_attack(player)
                return

        # ------------------------------------------------------------------
        # Shooter positioning — maintain preferred distance and alignment
        # ------------------------------------------------------------------
        if self.enemy_category == 'shooter':
            if distance < self.preferred_distance * 0.9:
                # Player invaded personal space — advanced AI rushes back; easy AI backs off
                if (self.ai_type == 'advanced'
                        and not self.is_doing_melee_rush
                        and self.melee_rush_cooldown <= 0
                        and not self.is_attacking):
                    self.is_doing_melee_rush = True
                    self.melee_rush_timer = 0
                    self.melee_rush_swung = False
                    self.pathfind_direction = None
                    self.pathfind_commit_timer = 0
                    return

                # Back away — face toward player while moving in reverse
                dx = self.x - player.x
                dy = self.y - player.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0.1:
                    dx /= dist
                    dy /= dist

                self.set_direction_from_movement(player.x - self.x, player.y - self.y)
                if self.has_sprite:
                    self.sprite.set_animation('walk', self.direction)

                move_speed = self.speed * 50 * dt

            elif distance <= self.preferred_distance * 1.3 and distance < self.attack_range:
                # In the sweet spot — strafe to a cardinal alignment if needed
                if self.shooter_style in ('bullet', 'rocket', 'kiblast'):
                    raw_dx = player.x - self.x
                    raw_dy = player.y - self.y
                    alignment_tolerance = 20
                    cardinally_aligned = (abs(raw_dx) < alignment_tolerance
                                          or abs(raw_dy) < alignment_tolerance)

                    if not cardinally_aligned:
                        # Slide toward whichever axis requires the least movement
                        if abs(raw_dx) < abs(raw_dy):
                            target_x, target_y = player.x, self.y   # Line up same column
                        else:
                            target_x, target_y = self.x, player.y   # Line up same row

                        move_dx = target_x - self.x
                        move_dy = target_y - self.y
                        move_dist = math.sqrt(move_dx ** 2 + move_dy ** 2)
                        if move_dist > 0.1:
                            move_dx /= move_dist
                            move_dy /= move_dist

                        self.set_direction_from_movement(move_dx, move_dy)
                        if self.has_sprite:
                            self.sprite.set_animation('walk', self.direction)

                        move_speed = self.speed * 60 * dt
                        self._move_checked(move_dx, move_dy, move_speed, world_width, world_height)
                        return

                # Already aligned (or bomb thrower) — stand still and face player
                if self.has_sprite:
                    self.sprite.set_animation('idle', self.direction)
                self.set_direction_from_movement(player.x - self.x, player.y - self.y)
                return

            else:
                # Too far — close the gap
                dx = player.x - self.x
                dy = player.y - self.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0.1:
                    dx /= dist
                    dy /= dist

                self.set_direction_from_movement(dx, dy)
                if self.has_sprite:
                    self.sprite.set_animation('walk', self.direction)

                move_speed = self.speed * 60 * dt

        # ------------------------------------------------------------------
        # Melee positioning — get as close as possible, route around allies
        # ------------------------------------------------------------------
        else:
            if distance < self.attack_range:
                # Already at striking distance — attack_cooldown must still
                # be active, or the attack-opportunity check above would
                # have already fired try_attack() and returned. Hold ground
                # here instead of continuing to close in on player.x/y —
                # normally the post-hit knockback flings the player back
                # out before this matters, but a blocking player barely
                # moves, so without this the enemy just keeps walking
                # straight through them and out the other side.
                if self.has_sprite:
                    self.sprite.set_animation('idle', self.direction)
                self.set_direction_from_movement(player.x - self.x, player.y - self.y)
                return

            target_x = player.x
            target_y = player.y

            # If a teammate is directly in the way, find an open approach angle
            if self.is_path_blocked_by_enemy(player.x, player.y):
                target_x, target_y = self.find_open_angle_around_player(player)

            if self.has_sprite:
                self.sprite.set_animation('walk', self.direction)

            dx = target_x - self.x
            dy = target_y - self.y
            approach_dist = math.sqrt(dx * dx + dy * dy)

            if approach_dist > 0.1:
                dx /= approach_dist
                dy /= approach_dist
            else:
                # Already at the target spot — just face the player
                dx = player.x - self.x
                dy = player.y - self.y
                d = math.sqrt(dx * dx + dy * dy)
                if d > 0.1:
                    dx /= d
                    dy /= d

            move_speed = self.speed * 60 * dt
            # Slow down slightly when already very close — helps formation look less chaotic
            if distance < self.min_separation_distance * 2:
                move_speed *= 0.7

        # ------------------------------------------------------------------
        # Obstacle pathfinding + separation force, then apply movement
        # ------------------------------------------------------------------
        new_x = self.x + dx * move_speed
        new_y = self.y + dy * move_speed
        is_blocked = self.check_collision_with_obstacles(new_x, new_y)

        if self.pathfind_commit_timer > 0 and self.pathfind_direction is not None:
            dx, dy = self.pathfind_direction
        elif is_blocked or self.stuck_timer > 0.3:
            target_x = (player.x if self.enemy_category == 'melee'
                        else self.x + dx * self.preferred_distance)
            target_y = (player.y if self.enemy_category == 'melee'
                        else self.y + dy * self.preferred_distance)
            clear_path = self.find_clear_path_around_obstacle(target_x, target_y, move_speed)
            if clear_path is not None:
                self.pathfind_direction = clear_path
                self.pathfind_commit_timer = self.pathfind_commit_duration
                dx, dy = clear_path
        else:
            # Clear path — drop any stale pathfinding state
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0

        if self.enemy_category == 'melee':
            self.set_direction_from_movement(dx, dy)

        # Blend in separation force so enemies spread out naturally
        sep_x, sep_y = self.calculate_separation_force()
        sep_strength = move_speed * 0.6
        final_dx = dx * move_speed + sep_x * sep_strength
        final_dy = dy * move_speed + sep_y * sep_strength

        # Apply axis-by-axis with obstacle and bounds check
        half_w = self.width // 2
        half_h = self.height // 2

        test_x = self.x + final_dx
        if not self.check_collision_with_obstacles(test_x, self.y) and half_w <= test_x <= world_width - half_w:
            self.x = test_x

        test_y = self.y + final_dy
        if not self.check_collision_with_obstacles(self.x, test_y) and half_h <= test_y <= world_height - half_h:
            self.y = test_y

    def retreat_behavior(self, dt, player, world_width, world_height):
        """Back away from the player, then hold still briefly before re-engaging.

        Two-phase: 'retreating' (moving away) → 'breathing' (standing still to recover).
        """
        if self.is_breathing:
            # Breather phase — stand still and watch the player
            self.breather_timer -= dt
            if self.has_sprite:
                self.sprite.set_animation('idle', self.direction)
            self.set_direction_from_movement(player.x - self.x, player.y - self.y)

            if self.breather_timer <= 0:
                # Recovery complete — reset combo counter and return to chase
                self.is_breathing = False
                self.is_retreating = False
                self.consecutive_hits = 0
                self.state = 'chase'
                self.retreat_cooldown = random.uniform(3.0, 5.0)
                self.pathfind_direction = None
                self.pathfind_commit_timer = 0
            return

        # Active retreat — move away from the player
        self.retreat_timer -= dt

        dx = self.x - player.x
        dy = self.y - player.y
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > 0.1:
            dx /= dist
            dy /= dist

        self.set_direction_from_movement(dx, dy)
        if self.has_sprite:
            self.sprite.set_animation('walk', self.direction)

        move_speed = self.speed * 70 * dt  # Slightly faster than normal chase

        # Obstacle pathfinding while retreating
        new_x = self.x + dx * move_speed
        new_y = self.y + dy * move_speed
        is_blocked = self.check_collision_with_obstacles(new_x, new_y)

        if self.pathfind_commit_timer > 0 and self.pathfind_direction is not None:
            dx, dy = self.pathfind_direction
        elif is_blocked or self.stuck_timer > 0.3:
            retreat_target_x = self.x + dx * self.retreat_distance
            retreat_target_y = self.y + dy * self.retreat_distance
            clear_path = self.find_clear_path_around_obstacle(retreat_target_x, retreat_target_y, move_speed)
            if clear_path is not None:
                self.pathfind_direction = clear_path
                self.pathfind_commit_timer = self.pathfind_commit_duration
                dx, dy = clear_path

        self._move_checked(dx, dy, move_speed, world_width, world_height)

        # Transition to breather once far enough or time is up
        if dist >= self.retreat_distance or self.retreat_timer <= 0:
            self.is_breathing = True
            self.breather_timer = self.breather_duration

    def feint_behavior(self, dt, player, world_width, world_height):
        """Step backward while still facing the player — sells a fake retreat after attacking."""
        self.feint_timer -= dt

        # Face toward the player the whole time
        face_dx = player.x - self.x
        face_dy = player.y - self.y
        face_dist = math.sqrt(face_dx ** 2 + face_dy ** 2)
        if face_dist > 0.1:
            face_dx /= face_dist
            face_dy /= face_dist

        # Move away from the player
        move_dx = self.x - player.x
        move_dy = self.y - player.y
        move_dist = math.sqrt(move_dx ** 2 + move_dy ** 2)
        if move_dist > 0.1:
            move_dx /= move_dist
            move_dy /= move_dist

        self.set_direction_from_movement(face_dx, face_dy)
        if self.has_sprite:
            self.sprite.set_animation('walk', self.direction)

        move_speed = self.speed * 55 * dt  # Moderate backing-away speed

        # Obstacle pathfinding
        new_x = self.x + move_dx * move_speed
        new_y = self.y + move_dy * move_speed
        is_blocked = self.check_collision_with_obstacles(new_x, new_y)

        if self.pathfind_commit_timer > 0 and self.pathfind_direction is not None:
            move_dx, move_dy = self.pathfind_direction
        elif is_blocked or self.stuck_timer > 0.3:
            target_x = self.x + move_dx * self.feint_distance
            target_y = self.y + move_dy * self.feint_distance
            clear_path = self.find_clear_path_around_obstacle(target_x, target_y, move_speed)
            if clear_path is not None:
                self.pathfind_direction = clear_path
                self.pathfind_commit_timer = self.pathfind_commit_duration
                move_dx, move_dy = clear_path

        self._move_checked(move_dx, move_dy, move_speed, world_width, world_height)

        if self.feint_timer <= 0:
            self.is_feinting = False
            self.feint_cooldown = random.uniform(5.0, 7.0)
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0

            # 50% chance to pause briefly after the feint before re-engaging
            if random.random() < self.pause_after_feint_chance:
                self.is_pausing_after_feint = True
                self.pause_after_feint_timer = random.uniform(0.4, 0.8)
            else:
                self.state = 'chase'

    def pause_after_feint_behavior(self, dt, player):
        """Stand and stare at the player for a beat after a feint — keeps them guessing."""
        self.pause_after_feint_timer -= dt

        if self.has_sprite:
            self.sprite.set_animation('idle', self.direction)
        self.set_direction_from_movement(player.x - self.x, player.y - self.y)

        if self.pause_after_feint_timer <= 0:
            self.is_pausing_after_feint = False
            self.state = 'chase'

    def block_behavior(self, dt, player):
        """Hold guard facing the player until the timer runs out — see
        _check_block() for when this starts. take_damage()/apply_knockback()
        check is_blocking directly, so nothing here needs to react to an
        incoming hit; this just holds the pose and position.
        """
        self.block_timer -= dt
        self.set_direction_from_movement(player.x - self.x, player.y - self.y)
        if self.has_sprite:
            self.sprite.set_animation('blocking', self.direction)

        if self.block_timer <= 0:
            self.is_blocking = False
            self.block_cooldown = self.block_cooldown_time
            self.block_cooldown_time = random.uniform(1.5, 3.0)
            if self.has_sprite:
                self.sprite.set_animation('idle', self.direction)

    def idle_behavior(self, dt, world_width, world_height):
        """Wander randomly near the spawn point, or return home if too far."""
        distance_from_spawn = self.distance_to_spawn(self.x, self.y)

        # Too far from spawn — walk straight back
        if distance_from_spawn > self.max_idle_distance:
            dx = self.spawn_x - self.x
            dy = self.spawn_y - self.y
            dist = math.sqrt(dx * dx + dy * dy)

            if dist > 0:
                dx /= dist
                dy /= dist
                move_speed = self.speed * 60 * dt

                # Route around anything in the way
                new_x = self.x + dx * move_speed
                new_y = self.y + dy * move_speed
                if (self.check_collision_with_obstacles(new_x, new_y)
                        or self.stuck_timer > 0.3):
                    clear_path = self.find_clear_path_around_obstacle(
                        self.spawn_x, self.spawn_y, move_speed
                    )
                    if clear_path is not None:
                        dx, dy = clear_path

                self.set_direction_from_movement(dx, dy)
                if self.has_sprite:
                    self.sprite.set_animation('walk', self.direction)
                self._move_checked(dx, dy, move_speed, world_width, world_height)
            return

        # Random wandering within the spawn radius
        if not self.is_idle_moving:
            self.idle_timer += dt
            if self.idle_timer >= self.idle_wait_time:
                self.idle_timer = 0
                self.is_idle_moving = True
                self.idle_move_timer = 0

                # Pick a random direction and clamp the target to the spawn radius
                angle = random.uniform(0, 2 * math.pi)
                self.idle_direction = (math.cos(angle), math.sin(angle))
                move_distance = random.uniform(20, 60)

                self.target_x = self.x + self.idle_direction[0] * move_distance
                self.target_y = self.y + self.idle_direction[1] * move_distance

                dx = self.target_x - self.spawn_x
                dy = self.target_y - self.spawn_y
                dist_from_spawn = math.sqrt(dx * dx + dy * dy)

                if dist_from_spawn > self.max_idle_distance:
                    dx /= dist_from_spawn
                    dy /= dist_from_spawn
                    self.target_x = self.spawn_x + dx * self.max_idle_distance
                    self.target_y = self.spawn_y + dy * self.max_idle_distance

                self.set_direction_from_movement(self.idle_direction[0], self.idle_direction[1])
                if self.has_sprite:
                    self.sprite.set_animation('walk', self.direction)
        else:
            self.idle_move_timer += dt
            dist_to_target = math.sqrt((self.x - self.target_x) ** 2 + (self.y - self.target_y) ** 2)

            # Stop when close enough or the move timer expires
            if dist_to_target < 5 or self.idle_move_timer >= self.idle_move_duration:
                self.is_idle_moving = False
                self.idle_timer = 0
                if self.has_sprite:
                    self.sprite.set_animation('idle', self.direction)
            else:
                move_speed = self.speed * 20 * dt
                self._move_checked(
                    self.idle_direction[0], self.idle_direction[1],
                    move_speed, world_width, world_height,
                )

    # =========================================================================
    # Combat methods
    # =========================================================================

    def try_attack(self, player):
        """Start an attack animation if off cooldown and in range.

        Returns True if the attack started, False otherwise.
        """
        if self.is_attacking or self.is_knocked_back or self.attack_cooldown > 0:
            return False

        # The player isn't a valid target while blinking between Instant
        # Transmission hops — take_damage() already refuses damage during
        # this window (see player.py), but that alone isn't enough for
        # anything that spawns a projectile/bomb (bullet, rocket, kiblast):
        # those land LATER, once the teleport is long over, and would
        # still connect even though the enemy "attacked" a position the
        # player was never really vulnerable at. Stopping the attack from
        # starting at all — not just stopping its damage — is what actually
        # keeps the enemy from trying in the first place.
        if getattr(player, 'is_teleporting_it', False):
            return False

        # Don't let the player's closing speed during a charged-melee lunge
        # trigger an instant counter-attack the moment they enter range —
        # in the original game enemies didn't try to attack while the
        # player was mid-lunge. take_damage() can still interrupt the lunge
        # if the player was already mid-attack-window when it started, but
        # the lunge itself shouldn't be what causes the enemy to swing.
        if getattr(player, 'is_charged_melee_active', False):
            return False

        if self.distance_to(player.x, player.y) < self.attack_range:
            self.is_attacking = True
            self.attack_timer = self.attack_duration
            self.attack_cooldown = self.attack_cooldown_time

            # Reset per-attack spawn flags so each attack spawns exactly one projectile
            if self.enemy_category == 'shooter':
                self.bomb_spawned_this_attack = False
                self.bullet_spawned_this_attack = False
                self.rocket_spawned_this_attack = False
                self.kiblast_spawned_this_attack = False

            # Same one-shot idea for melee — perform_attack() runs every frame
            # of the attack window (see below), so without this it'd call
            # take_damage() ~24 times per swing instead of once. Normally
            # masked by i-frames + the player getting knocked out of
            # attack_range after the first hit, but a blocking player stays
            # put, so every one of those frames would otherwise connect.
            self.melee_hit_this_attack = False

            if self.has_sprite:
                anim = 'melee' if self.enemy_category == 'melee' else 'attack'
                self.sprite.set_animation(anim, self.direction)

            return True

        return False

    def perform_attack(self, player, game_config=None):
        """Apply hit effects or set projectile spawn flags during the attack window.

        Called at attack_timer <= 0.4 s (the last part of the animation) so the
        impact lands at the visual peak of the swing.

        game_config, if given, mitigates the melee-contact damage below by
        the player's END (see Player.get_incoming_melee_damage) — omitted
        entirely for the projectile/beam spawn branches, which aren't
        melee and were never part of the STR/END data this curve was fit
        against.
        """
        if not self.is_attacking:
            return

        distance = self.distance_to(player.x, player.y)

        if self.enemy_category == 'shooter':
            # Shooter landed a melee rush — use melee damage
            if getattr(self, 'is_shooter_melee_attack', False):
                if distance < self.shooter_melee_range and not self.melee_hit_this_attack:
                    kx, ky = self._direction_to_vector()
                    damage = self.shooter_melee_damage
                    if game_config is not None:
                        damage = player.get_incoming_melee_damage(damage, game_config)
                    player.take_damage(damage, kx, ky)
                    if not player.is_blocking:
                        player.hurt_tint = 1.0
                    self.melee_hit_this_attack = True
                return

            if self.shooter_style == 'bullet':
                # Snap to the nearest cardinal axis and set the bullet-spawn flag
                if not self.bullet_spawned_this_attack:
                    raw_dx = player.x - self.x
                    raw_dy = player.y - self.y
                    dx, dy = self._snap_to_cardinal(raw_dx, raw_dy)
                    self.should_spawn_bullet = True
                    self.bullet_dx = dx
                    self.bullet_dy = dy
                    self.bullet_spawned_this_attack = True

            elif self.shooter_style == 'rocket':
                # Identical axis-snap logic to bullet, different projectile type
                if not self.rocket_spawned_this_attack:
                    raw_dx = player.x - self.x
                    raw_dy = player.y - self.y
                    dx, dy = self._snap_to_cardinal(raw_dx, raw_dy)
                    self.should_spawn_rocket = True
                    self.rocket_dx = dx
                    self.rocket_dy = dy
                    self.rocket_spawned_this_attack = True

            elif self.shooter_style == 'kiblast':
                # Snap direction, store it, then start the charge animation via
                # shoot_blast() (defined on BossEnemy).  The projectile is spawned
                # once BossEnemy.update() detects the animation has finished and
                # sets should_spawn_kiblast = True.
                if not self.kiblast_spawned_this_attack:
                    raw_dx = player.x - self.x
                    raw_dy = player.y - self.y
                    dx, dy = self._snap_to_cardinal(raw_dx, raw_dy)
                    self.kiblast_dx = dx
                    self.kiblast_dy = dy
                    if hasattr(self, 'shoot_blast'):
                        self.shoot_blast()          # BossEnemy triggers the animation gate
                    else:
                        self.should_spawn_kiblast = True  # Fallback for non-boss kiblast enemies
                    self.kiblast_spawned_this_attack = True

            else:
                # Bomb thrower — lob toward the player's current position
                if not self.bomb_spawned_this_attack:
                    self.should_spawn_bomb = True
                    self.bomb_target_x = player.x
                    self.bomb_target_y = player.y
                    self._pending_bomb_player = player
                    self.bomb_spawned_this_attack = True

        else:
            # Melee — deal damage if the player is still in range
            if distance < self.attack_range and not self.melee_hit_this_attack:
                kx, ky = self._direction_to_vector()
                damage = self.attack_damage
                if game_config is not None:
                    damage = player.get_incoming_melee_damage(damage, game_config)
                player.take_damage(damage, kx, ky)
                if not player.is_blocking:
                    player.hurt_tint = 1.0
                self.melee_hit_this_attack = True

    # =========================================================================
    # Damage / knockback
    # =========================================================================

    def apply_knockback(self, dx, dy, force=200):
        """Launch the enemy in direction (dx, dy) with the given force and play the hurt animation."""
        if self.is_blocking:
            # Guard absorbs the hit: token 1px nudge instead of full
            # knockback physics, no hurt animation/tint, guard pose holds
            # straight through — mirrors Player.take_damage's own block
            # branch. dx/dy are already a unit vector (every caller in
            # check_collision_with_attack normalizes before calling this),
            # so this moves at most ~1px.
            nudge_x = self.x + dx
            nudge_y = self.y + dy
            if not self.check_collision_with_obstacles(nudge_x, self.y):
                self.x = nudge_x
            if not self.check_collision_with_obstacles(self.x, nudge_y):
                self.y = nudge_y
            return

        self.is_knocked_back = True
        self.knockback_velocity_x = dx * force
        self.knockback_velocity_y = dy * force
        self.hurt_tint = 1.0

        if self.has_sprite:
            # restart_animation (not set_animation) so every landed hit
            # visibly flinches from frame 0 — set_animation no-ops if
            # 'hurt' is already playing, which made repeated hits (a beam
            # ticking damage, or a fast melee combo) look like they only
            # flinched once and had to wait for that single animation to
            # finish before flinching again. Falls back to set_animation if
            # this sprite object doesn't have restart_animation (e.g. an
            # older sprite_system.py without it) so a mismatched file can't
            # throw here and silently abort whatever called this — it just
            # degrades to the old no-flinch-on-repeat behavior instead of
            # breaking damage/knockback entirely.
            if hasattr(self.sprite, 'restart_animation'):
                self.sprite.restart_animation('hurt', self.direction)
            else:
                self.sprite.set_animation('hurt', self.direction)

    def _end_knockback(self):
        """Clear all knockback state and return to idle animation."""
        self.is_knocked_back = False
        self.knockback_velocity_x = 0
        self.knockback_velocity_y = 0
        if self.has_sprite:
            self.sprite.set_animation('idle', self.direction)

    def stun(self, duration, attack_direction=None):
        """Freeze AI/movement for *duration* seconds — called by game.py's
        collision loop for stun-capable attacks (e.g. BurningAttack), the
        same way apply_knockback() is called for a regular hit.

        Refreshes to the longer of the current remaining stun and
        *duration* rather than adding, so repeated stunning hits don't
        compound into an absurdly long freeze — mirrors how
        invulnerable_timer is reset rather than accumulated on each hit.
        Doesn't touch is_knocked_back/knockback velocity, so a hit that
        both knocks back and stuns still plays its knockback stagger out
        first (see the ordering in update()).

        attack_direction is the *travel* direction of the attack that hit
        (e.g. a BurningAttack's own .direction) — same convention as the
        beam-contact code in check_collision_with_attack(): the enemy turns
        to face back the way the attack came from, not the way it's flying.
        Optional so existing stun(duration)-only callers keep working.
        """
        self.is_stunned = True
        self.stun_timer = max(self.stun_timer, duration)

        if attack_direction is not None:
            self.direction = {
                'up':    'down',
                'down':  'up',
                'left':  'right',
                'right': 'left',
            }.get(attack_direction, self.direction)
            if self.has_sprite:
                self.sprite.set_animation('idle', self.direction)

    def _end_stun(self):
        """Clear stun state and return to idle animation."""
        self.is_stunned = False
        self.stun_timer = 0.0
        if self.has_sprite:
            self.sprite.set_animation('idle', self.direction)

    def encase(self, attack_direction=None):
        """Lock this enemy into the encased state — called from the
        'ultra_volleyball' branch of check_collision_with_attack on
        contact. Hides the enemy behind the casing overlay (playing its
        destroy-style intro animation), makes it fully immovable/immune
        (see the guard at the top of check_collision_with_attack), and
        starts the encased_duration countdown toward automatic release.
        A melee landing on the casing before that timer runs out cancels
        it and starts a roll instead — see try_trigger_roll().
        """
        if self.is_encased or self.is_rolling:
            return

        self.is_encased = True
        self.encased_timer = self.encased_duration
        self.is_knocked_back = False
        self.is_stunned = False

        if attack_direction is not None:
            self.direction = {
                'up':    'down',
                'down':  'up',
                'left':  'right',
                'right': 'left',
            }.get(attack_direction, self.direction)

        self.encasement_overlay = EncasementOverlay(self.encasement_overlay_asset, self.rolling_overlay_asset)
        self.encasement_overlay.play()
        self._spawn_destruction_effect()

    def try_trigger_roll(self, melee):
        """Called by game.py's melee-vs-enemy loop whenever this enemy is
        currently encased, INSTEAD of the normal
        check_collision_with_attack(melee, 'melee') call (which is a no-op
        while encased anyway — see that method's guard). Tests the same
        melee hitbox shape check_collision_with_attack's 'melee' branch
        uses, and — on a hit — cancels the encasement timer and starts
        rolling the casing away from whichever side the player struck
        from (the dominant axis of player-to-enemy, snapped to a cardinal
        direction). Deals no damage itself; only the eventual collision
        impact in _end_roll() does.
        """
        if not self.is_encased or self.is_rolling:
            return False

        offset = 25
        melee_x = melee.x
        melee_y = melee.y
        if melee.direction == 'up':
            melee_y -= offset + melee.size // 2
        elif melee.direction == 'down':
            melee_y += offset + melee.size // 2
        elif melee.direction == 'left':
            melee_x -= offset + melee.size // 2
        elif melee.direction == 'right':
            melee_x += offset + melee.size // 2

        attack_rect = pygame.Rect(
            melee_x - melee.size // 2, melee_y - melee.size // 2,
            melee.size, melee.size,
        )
        if not attack_rect.colliderect(self.get_collision_rect()):
            return False

        dx = self.x - melee.x
        dy = self.y - melee.y
        if abs(dx) >= abs(dy):
            roll_direction = 'right' if dx >= 0 else 'left'
        else:
            roll_direction = 'down' if dy >= 0 else 'up'

        self.is_encased = False
        self.is_rolling = True
        self.roll_direction = roll_direction
        rdx, rdy = _KNOCKBACK_VECTORS.get(roll_direction, (0.0, 0.0))
        self.roll_velocity_x = rdx * self.roll_speed
        self.roll_velocity_y = rdy * self.roll_speed
        self.direction = roll_direction
        if self.encasement_overlay:
            self.encasement_overlay.play_loop(roll_direction)
        return True

    def _end_roll(self):
        """Called from update()'s is_rolling branch once the roll hits a
        collision. Applies the impact damage FIRST — same as the design
        ("takes damage while the same thing happens as if the time just
        ran out") — since a lethal hit here should go straight to the
        death animation (take_damage sets is_dying, which update() checks
        ahead of every encasement sub-state) rather than playing a release
        animation for an enemy that's already dead.
        """
        self.is_rolling = False
        self.roll_velocity_x = 0.0
        self.roll_velocity_y = 0.0
        self.take_damage(self.roll_impact_damage)
        if self.active:
            self._release_from_encasement()

    def _release_from_encasement(self):
        """Shared release path for both the encased_timer running out and
        a roll ending in a non-lethal collision. Plays the destroy overlay
        animation again and holds movement/AI frozen (is_releasing) until
        it finishes playing, THEN clears the casing and hands control back
        — see the is_releasing branch in update()."""
        self.is_encased = False
        self.is_releasing = True
        if self.encasement_overlay:
            self.encasement_overlay.play()
        else:
            self.encasement_overlay = EncasementOverlay(self.encasement_overlay_asset, self.rolling_overlay_asset)
            self.encasement_overlay.play()
        self._spawn_destruction_effect()

    def take_damage(self, damage):
        """Reduce HP and track hit combos for the advanced AI retreat decision.

        Returns False (no-op) if the enemy is still within its post-hit
        i-frame window — same gating the player uses, decoupled from
        is_knocked_back so a hit can land again mid-stagger once i-frames
        have expired.
        """
        if self.invulnerable:
            return False

        if self.is_blocking:
            damage = round(damage / 2)

        self.last_damage_dealt = damage  # Stored so game.py can spawn a popup
        self.hp -= damage
        if not self.is_blocking:
            self.hurt_tint = 1.0

        self.invulnerable = True
        self.invulnerable_timer = self.invulnerable_duration

        if self.ai_type == 'advanced' and not self.is_blocking:
            current_time = time.time()
            # Count consecutive hits within the combo window
            if current_time - self.last_hit_time < self.hit_combo_window:
                self.consecutive_hits += 1
            else:
                self.consecutive_hits = 1  # Too long since last hit — reset combo
            self.last_hit_time = current_time

        if self.hp <= 0:
            self.hp = 0
            if self.has_sprite:
                self.sprite.set_animation('death', self.direction)
            # Delay actual deactivation until the death animation finishes
            self.is_dying = True
            self.death_frame_index = 0
            self.death_frame_timer = 0.0
            self.zeni_drop = self.get_zeni_drop()  # rolled now — see zeni_drop's init comment

        return True

    def get_xp_reward(self, game_config):
        """Return XP granted to the player on kill."""
        return game_config.basic_enemy_xp

    def get_zeni_drop(self):
        """Roll this enemy's zeni drop (see core/zeni_system.py) and return
        a {denomination: count} dict — empty if nothing dropped this kill.

        Called once, from take_damage() at the moment the killing blow lands
        (see self.zeni_drop), so the drop is rolled before the death
        animation even starts rather than waiting for self.active to flip
        false. Not meant to be called again per-enemy after that.
        """
        return roll_zeni_drop(self.zeni_pool)

    # =========================================================================
    # Collision with player attacks
    # =========================================================================

    def check_collision_with_attack(self, attack, attack_type, game_config=None):
        """Test whether a player attack hits this enemy and apply damage/knockback.

        Returns True if a hit was registered. Being mid-knockback no longer
        blocks this outright — take_damage()'s own i-frame window (decoupled
        from is_knocked_back) is what gates repeat hits now, so an enemy can
        be damaged again while still staggered once i-frames expire.

        game_config is required for attack_type == 'melee' or 'projectile'
        to use their STR/POW-based curves — see GameConfig.roll_melee_damage
        / roll_ki_blast_damage. Falls back to old flat values (15 melee,
        20 projectile) if it's not passed, so callers that don't care
        about these attack types aren't forced to pass one.
        """
        if not self.active:
            return False

        # Immune to every normal attack type while encased or rolling —
        # "nothing can damage except the collision when rolling" is
        # enforced entirely by _end_roll() below, not through this method.
        # A melee swing landing on the casing still needs to register (to
        # trigger the roll), but that's a separate interaction handled by
        # try_trigger_roll(), called by game.py alongside this method
        # rather than through it — see the 'ultra_volleyball' collision
        # loop notes in game.py.
        if self.is_encased or self.is_rolling:
            return False

        if attack_type == 'melee':
            # Use the attack's own get_rect() rather than recomputing the
            # box here — this used to reimplement the same offset/size
            # logic with offset = 25 instead of MeleeAttack.get_rect()'s
            # offset = 15 (the value that actually matches the swish arc
            # drawn in MeleeAttack.draw()), so hits were registering ~10
            # world units farther out than the visible swing.
            attack_rect = attack.get_rect()
            if attack_rect.colliderect(self.get_collision_rect()):
                if game_config is not None and hasattr(attack, 'roll_damage'):
                    # STR (attacker) vs. END/defense (this enemy) — see
                    # GameConfig.roll_melee_damage. Falls back to the old
                    # flat 15 if no game_config was passed in, or if this
                    # isn't a real MeleeAttack (e.g. a test double without
                    # roll_damage/owner).
                    damage, is_crit = attack.roll_damage(game_config, self)
                else:
                    damage, is_crit = 15, False
                self.last_hit_was_crit = is_crit  # so game.py can style the popup differently
                if not self.take_damage(damage):
                    return False  # Still in i-frames — swing passes through harmlessly

                dx = self.x - attack.x
                dy = self.y - attack.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0:
                    dx /= dist
                    dy /= dist
                self.apply_knockback(dx, dy, 150)
                return True

        elif attack_type == 'projectile':
            r = attack.radius
            projectile_rect = pygame.Rect(attack.x - r, attack.y - r, r * 2, r * 2)

            if projectile_rect.colliderect(self.get_collision_rect()):
                dx = self.x - attack.x
                dy = self.y - attack.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0:
                    dx /= dist
                    dy /= dist

                owner = getattr(attack, 'owner', None)
                if game_config is not None and owner is not None and hasattr(owner, 'get_ki_blast_damage'):
                    # POW (attacker) vs. END/defense (this enemy) — see
                    # GameConfig.roll_ki_blast_damage. Falls back to the
                    # old flat 20 if no game_config/owner was set (e.g. an
                    # enemy-fired projectile, which carries no POW stat).
                    damage = owner.get_ki_blast_damage(game_config, target=self)
                else:
                    damage = 20

                if not self.take_damage(damage):
                    return False
                self.apply_knockback(dx, dy, 250)
                return True

        elif attack_type in ('beam', 'big_bang_kamehameha'):
            # big_bang_kamehameha rides the same branch as beam rather than
            # getting its own (unlike flame_kamehameha, which genuinely
            # behaves differently — see that branch) because
            # BigBangKamehamehaAttack IS a BeamAttack: same length/width/
            # scale/direction/report_obstruction/push_force contract, just
            # different art (see attacks/big_bang_kamehameha.py). Nothing
            # below this line is kamehameha-specific, so there's nothing to
            # actually duplicate for it.
            if attack.length > 0:
                # attack.length / attack.width are in SCREEN-space pixels
                # (already multiplied by attack.scale for rendering — see
                # BeamAttack.calculate_scaled_dimensions), but self.x/self.y
                # and this beam_rect need to be in WORLD space to compare
                # against the enemy's world-space collision rect — same
                # convention player.py uses (e.g. self.speed = 5 / RENDER_SCALE).
                #
                # world_length must match where the tip sprite is ACTUALLY
                # drawn, not just attack.length — beams with a real
                # ball/circle/beam gap chain (e.g. big_bang_kamehameha)
                # draw their tip at self.length PLUS a fixed spatial
                # offset (see BeamAttack._min_reach()/get_tip_world_length()
                # in beam.py), so comparing against attack.length alone
                # made the visible tip pass through an enemy well before
                # contact/damage registered — a lagging hitbox. Beams
                # without that gap chain (plain kamehameha, etc.) have a
                # near-zero offset, so this is a no-op change for them.
                world_length = (
                    attack.get_tip_world_length() if hasattr(attack, 'get_tip_world_length')
                    else attack.length / attack.scale
                )
                world_width = attack.width / attack.scale

                # Corridor rect extends a fixed, very long distance in the
                # firing direction — NOT just to attack.length — so "is this
                # enemy in the beam's path" never depends on how far the
                # beam has grown so far. Building this from attack.length
                # instead made the test fail for a frame whenever the
                # enemy's already-moved position (this frame's knockback
                # slide happens at the top of enemy.update(), before this
                # check runs) briefly outran the not-yet-regrown length —
                # dropping knockback, letting AI chase back in, then
                # re-triggering it a moment later. That's the "not always
                # connected, has to catch up" effect. Whether the beam has
                # actually reached the enemy yet is checked separately below
                # with a plain number comparison instead.
                REACH = 10000  # world px — effectively "as far as this beam could ever grow"
                if attack.direction == 'up':
                    corridor_rect = pygame.Rect(attack.x - world_width // 2, attack.y - REACH,
                                                world_width, REACH)
                elif attack.direction == 'down':
                    corridor_rect = pygame.Rect(attack.x - world_width // 2, attack.y,
                                                world_width, REACH)
                elif attack.direction == 'left':
                    corridor_rect = pygame.Rect(attack.x - REACH, attack.y - world_width // 2,
                                                REACH, world_width)
                elif attack.direction == 'right':
                    corridor_rect = pygame.Rect(attack.x, attack.y - world_width // 2,
                                                REACH, world_width)
                else:
                    corridor_rect = None

                enemy_rect = self.get_collision_rect()

                if corridor_rect and corridor_rect.colliderect(enemy_rect):
                    # Stop at the enemy's CENTER (self.x/self.y) rather than
                    # its near edge — freezing at the edge left a visible gap
                    # where the beam looked like it stopped short instead of
                    # actually reaching/overlapping the character, which is
                    # how it's supposed to look (the tip visually layers over
                    # the enemy, not just touches its silhouette).
                    if attack.direction == 'up':
                        blocking_distance = attack.y - self.y
                    elif attack.direction == 'down':
                        blocking_distance = self.y - attack.y
                    elif attack.direction == 'left':
                        blocking_distance = attack.x - self.x
                    elif attack.direction == 'right':
                        blocking_distance = self.x - attack.x
                    else:
                        blocking_distance = None

                    if blocking_distance is not None and blocking_distance >= 0:
                        # Always report, regardless of whether the beam has
                        # actually grown out this far yet — this just tells
                        # it "don't grow past here," so it naturally stops
                        # exactly at the enemy instead of overshooting past
                        # it the one frame it finally arrives.
                        if hasattr(attack, 'report_obstruction'):
                            attack.report_obstruction(blocking_distance * attack.scale, source='enemy')

                        # Only actually make contact (damage + push) once the
                        # beam has really grown out to (or past) the enemy —
                        # a plain distance comparison, not a rect overlap
                        # against attack.length, so it can't flicker off from
                        # one frame's growth/movement ordering.
                        #
                        # CONTACT_EPSILON absorbs float-rounding noise: once
                        # the beam is capped exactly at the enemy, world_length
                        # and blocking_distance are SUPPOSED to be equal every
                        # frame, but both round-trip through report_obstruction
                        # (multiply by attack.scale) and back (divide by
                        # attack.scale) in beam.py, so tiny float error could
                        # occasionally leave world_length a hair under
                        # blocking_distance. Without slack, a strict >=
                        # dropped contact for exactly that one frame — long
                        # enough for AI to sneak in and set 'walk'.
                        CONTACT_EPSILON = 0.5  # world px
                        if world_length >= blocking_distance - CONTACT_EPSILON:
                            push_dx, push_dy = {
                                'up':    (0, -1),
                                'down':  (0, 1),
                                'left':  (-1, 0),
                                'right': (1, 0),
                            }.get(attack.direction, (0, 0))

                            # Turn to face whichever way the attack is
                            # actually coming from — nothing previously
                            # updated self.direction here, so a beam hitting
                            # an enemy from the side while it faced 'up'
                            # (mid-walk, etc.) would flinch/push while still
                            # visually facing up.
                            self.direction = {
                                'up':    'down',
                                'down':  'up',
                                'left':  'right',
                                'right': 'left',
                            }.get(attack.direction, self.direction)

                            # Push every single frame contact holds — a
                            # direct positional nudge via _push_from_beam(),
                            # not apply_knockback()'s velocity-integration/
                            # is_knocked_back state machine. That system is
                            # built for a one-off impulse and only releases
                            # once its hurt animation reports finished, which
                            # made sustained beam contact intermittently
                            # unable to land another hit until that
                            # animation played all the way out — a beam needs
                            # continuous push with no animation-completion
                            # gate blocking damage.
                            self.beam_freeze_timer = self.beam_freeze_grace
                            # attack.push_force lets a specific beam (e.g.
                            # FinalFlashAttack) override how hard it shoves
                            # per contact frame; None means "use this
                            # enemy's own default" — every beam did that
                            # before this existed, so nothing changes for
                            # beams that don't set it (e.g. the kamehameha).
                            push_force = getattr(attack, 'push_force', None)
                            if push_force is None:
                                push_force = self.beam_push_force
                            self._push_from_beam(push_dx, push_dy, push_force)

                            # take_damage() is gated by its own 0.2s i-frame
                            # window, independent of any animation state, so
                            # this ticks damage repeatedly the whole time
                            # contact holds rather than just once.
                            hit_landed = self.take_damage(5)
                            if hit_landed:
                                self.hurt_tint = 1.0
                                self._play_hurt_flinch()

                            return hit_landed

        elif attack_type == 'flame_kamehameha':
            # Fixed-length chain whose tip the player steers by hand (see
            # FlameKamehamehaAttack) — unlike 'beam' there's no growth to
            # track frame-to-frame, so this is a single bounding-box test
            # against the chain's current position (attack.get_world_bounds(),
            # which already accounts for the player-controlled whip offset)
            # rather than a growing corridor with report_obstruction(). Once
            # it hits, though, knockback works exactly like 'beam': a
            # continuous per-frame push via _push_from_beam() rather than a
            # one-off apply_knockback() impulse, since sustained contact
            # needs to keep landing every frame it's still touching, not
            # decay out after a single hit.
            if attack.get_world_bounds().colliderect(self.get_collision_rect()):
                push_dx, push_dy = {
                    'up':    (0, -1),
                    'down':  (0, 1),
                    'left':  (-1, 0),
                    'right': (1, 0),
                }.get(attack.direction, (0, 0))

                self.direction = {
                    'up':    'down',
                    'down':  'up',
                    'left':  'right',
                    'right': 'left',
                }.get(attack.direction, self.direction)

                self.beam_freeze_timer = self.beam_freeze_grace
                push_force = getattr(attack, 'push_force', None)
                if push_force is None:
                    push_force = self.beam_push_force
                self._push_from_beam(push_dx, push_dy, push_force)

                hit_landed = self.take_damage(5)
                if hit_landed:
                    self.hurt_tint = 1.0
                    self._play_hurt_flinch()

                return hit_landed

        elif attack_type == 'dragon_fist':
            # Each individual piece — head plus every body segment,
            # including the anchor right in front of the player — is
            # tested as its own hitbox via attack.get_segment_rects(),
            # rather than one bounding box like flame_kamehameha above.
            # The chain bends (each segment eases independently toward a
            # spring-damped target — see dragon_fist.py's _slide_chain),
            # so a single union rect could span empty space between
            # pieces and register hits nothing actually touched.
            #
            # Knockback otherwise works exactly like flame_kamehameha's:
            # the same continuous per-frame push via _push_from_beam()
            # rather than a one-off apply_knockback() impulse, so holding
            # any part of the fist against an enemy keeps landing every
            # frame contact holds instead of decaying out after one hit.
            if any(rect.colliderect(self.get_collision_rect())
                   for rect in attack.get_segment_rects()):
                push_dx, push_dy = {
                    'up':    (0, -1),
                    'down':  (0, 1),
                    'left':  (-1, 0),
                    'right': (1, 0),
                }.get(attack.direction, (0, 0))

                self.direction = {
                    'up':    'down',
                    'down':  'up',
                    'left':  'right',
                    'right': 'left',
                }.get(attack.direction, self.direction)

                self.beam_freeze_timer = self.beam_freeze_grace
                push_force = getattr(attack, 'push_force', None)
                if push_force is None:
                    push_force = self.beam_push_force
                self._push_from_beam(push_dx, push_dy, push_force)

                hit_landed = self.take_damage(5)
                if hit_landed:
                    self.hurt_tint = 1.0
                    self._play_hurt_flinch()

                return hit_landed

        elif attack_type == 'big_bang_attack':
            # BigBangAttackBlast pierces rather than being consumed on a
            # hit (see game.py's dedicated collision block for it, which
            # never sets attack.active = False the way the generic
            # 'projectile' branch above does for a regular ball). Unlike
            # flame_kamehameha/dragon_fist above, this deliberately does
            # NOT push/stagger/freeze the enemy at all — it's meant to
            # fly straight through untouched, just ticking damage on
            # whatever it's overlapping each frame it's in contact (still
            # gated by take_damage()'s own i-frames, same as every other
            # branch here). No apply_knockback(), no _push_from_beam(),
            # no direction flip, no beam_freeze_timer. Hit rect is built
            # the exact same way 'projectile' does it (attack.x/y/radius),
            # since this is just a plain circular ball, not a segmented
            # chain needing per-piece rects the way dragon_fist does.
            r = attack.radius
            blast_rect = pygame.Rect(attack.x - r, attack.y - r, r * 2, r * 2)

            if blast_rect.colliderect(self.get_collision_rect()):
                # damage pulled via getattr with a fallback, same
                # convention masenko/energy_punch/ghost_kamikaze_attack
                # use, rather than hardcoded — BigBangAttackBlast doesn't
                # currently expose it as a tunable attribute, so the
                # fallback is what actually applies today. Fallback
                # matches flame_kamehameha/dragon_fist's own flat 5, not
                # ghost_kamikaze_attack's one-shot 25 — this lands every
                # frame contact holds (gated only by take_damage()'s own
                # i-frames), so it needs to be sized for repeated hits,
                # not a single one.
                hit_landed = self.take_damage(getattr(attack, 'damage', 5))
                if hit_landed:
                    self.hurt_tint = 1.0
                    self._play_hurt_flinch()

                return hit_landed

        elif attack_type == 'ultra_volleyball_attack':
            # UltraVolleyballAttack is a fixed 3-segment chain that TRAVELS
            # (unlike flame_kamehameha's stationary whip-steered chain), but
            # exposes the same get_world_bounds() contract, so this reuses
            # that same bounds-rect hit test. On contact it doesn't damage
            # or push at all — it encases the enemy instead (see encase()
            # below) and game.py deactivates the attack the same frame,
            # same as a regular projectile being consumed on hit.
            if attack.get_world_bounds().colliderect(self.get_collision_rect()):
                self.encase(attack.direction)
                return True

        elif attack_type == 'ghost_kamikaze_attack':
            # `attack` here is a single _Ghost (see ghost_kamikaze_attack.py),
            # passed in per still-homing ghost by game.py's 'Ghost Kamikaze'
            # collision block — one hit test per ghost, each its own small
            # hitbox (get_collision_rect(), already on _Ghost), not one
            # shared hitbox for the whole GhostKamikazeAttack. Plain
            # single-hit rect check, same shape as ultra_volleyball_attack
            # right above, rather than a continuous per-frame push like
            # beam/dragon_fist — a ghost only ever gets the one hit before
            # game.py switches it to its destruction animation.
            #
            # game.py is what calls ghost.trigger_impact() and spawns the
            # damage number on a truthy return — neither happens here, this
            # method only knows about the enemy side of the contact.
            #
            # damage/knockback are pulled via getattr with a fallback,
            # same convention masenko/energy_punch use above, rather than
            # hardcoded — _Ghost/GhostKamikazeAttack don't currently expose
            # either as a tunable attribute, so the fallback is what
            # actually applies today; this just means a future `damage=`/
            # `knockback_force=` added over there is picked up here for
            # free, without another edit to this branch.
            if attack.get_collision_rect().colliderect(self.get_collision_rect()):
                dx = self.x - attack.x
                dy = self.y - attack.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0:
                    dx /= dist
                    dy /= dist

                damage = getattr(attack, 'damage', 25)
                if not self.take_damage(damage):
                    return False  # Still in i-frames from a prior hit

                knockback_force = getattr(attack, 'knockback_force', 200)
                self.apply_knockback(dx, dy, knockback_force)
                return True

        elif attack_type == 'instant_transmission':
            # Always-lands teleport strike — by the time this runs the
            # player has already teleported directly onto the target (see
            # Game._apply_instant_transmission_damage / player.py's
            # update_it_teleport), so this is a generous hitbox check
            # rather than a real thrown/swung attack. Uses attack.damage
            # directly (like masenko) since it's meant to be tunable, and
            # isn't gated behind is_knocked_back the way melee/projectile
            # implicitly are — a hit this deliberate shouldn't be able to
            # whiff just because the enemy happens to be mid-stagger.
            if attack.get_rect().colliderect(self.get_collision_rect()):
                if not self.take_damage(attack.damage):
                    return False  # Still in i-frames from a prior hit this instant

                dx = self.x - attack.x
                dy = self.y - attack.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0:
                    dx /= dist
                    dy /= dist
                self.apply_knockback(dx, dy, 180)
                return True

        elif attack_type == 'masenko':
            # AoE explosion — unlike melee/projectile/beam above, this checks
            # a plain radius around the detonation point rather than a rect,
            # and uses attack.damage directly instead of a hardcoded value,
            # since masenko's damage is meant to be tunable per-throw.
            dx = self.x - attack.x
            dy = self.y - attack.y
            dist = math.sqrt(dx * dx + dy * dy)
            radius = getattr(attack, 'EXPLOSION_RADIUS', 40)

            if dist < radius:
                if dist > 0:
                    dx /= dist
                    dy /= dist

                if not self.take_damage(attack.damage):
                    return False
                self.apply_knockback(dx, dy, 220)
                return True

        elif attack_type == 'energy_punch':
            # Instant close-range punch — no thrown/swung object at all, the
            # player itself is passed in as `attack` (see
            # Game._update_energy_punch), so this is just a plain radius
            # check around the player's own position, same shape as
            # masenko's AoE check above but much tighter ("very near" per
            # the design) and using the player's own tunable
            # energy_punch_damage/_radius/_knockback attributes instead of
            # attack.damage.
            dx = self.x - attack.x
            dy = self.y - attack.y
            dist = math.sqrt(dx * dx + dy * dy)
            radius = getattr(attack, 'energy_punch_radius', 24)

            if dist < radius:
                if dist > 0:
                    dx /= dist
                    dy /= dist

                damage = getattr(attack, 'energy_punch_damage', 20)
                if not self.take_damage(damage):
                    return False  # Still in i-frames from a prior hit

                force = getattr(attack, 'energy_punch_knockback', 200)
                self.apply_knockback(dx, dy, force)
                return True

        return False

    def _push_from_beam(self, dx, dy, distance):
        """Nudge the enemy `distance` world px in direction (dx, dy),
        sliding along obstacles per-axis (same idea as the knockback
        physics), but WITHOUT touching is_knocked_back/knockback_velocity —
        this is continuous beam pressure applied fresh every touching
        frame, not a decaying one-off impulse, so it shouldn't share state
        with (or get interrupted by) the discrete knockback system."""
        new_x = self.x + dx * distance
        new_y = self.y + dy * distance
        if not self.check_collision_with_obstacles(new_x, self.y):
            self.x = new_x
        if not self.check_collision_with_obstacles(self.x, new_y):
            self.y = new_y

    def _play_hurt_flinch(self):
        """Restart the hurt animation for the enemy's current facing
        direction, falling back to whichever direction IS loaded if
        hurt.png doesn't have a row for this one (e.g. a single-row,
        non-directional hurt sheet instead of the expected 4 stacked rows:
        down/left/right/up). Without this fallback, set_animation/
        restart_animation silently no-op when the specific 'hurt_<dir>' key
        isn't in self.sprite.animations — nothing plays and nothing errors,
        so only hurt_tint would ever be visible whenever the enemy wasn't
        facing whichever single direction did load."""
        if not self.has_sprite:
            return

        animations = getattr(self.sprite, 'animations', {})
        direction = self.direction
        if f"hurt_{direction}" not in animations:
            for fallback_dir in ('down', 'left', 'right', 'up'):
                if f"hurt_{fallback_dir}" in animations:
                    direction = fallback_dir
                    break
            else:
                return  # No hurt animation loaded for any direction at all

        if hasattr(self.sprite, 'restart_animation'):
            self.sprite.restart_animation('hurt', direction)
        else:
            self.sprite.set_animation('hurt', direction)

    # =========================================================================
    # Projectile spawn data — polled by the game loop each frame
    # =========================================================================

    def get_bomb_spawn_data(self):
        """Return bomb parameters and clear the spawn flag, or None if not ready.

        Game loop pattern:
            if enemy.should_spawn_bomb:
                data = enemy.get_bomb_spawn_data()
                bomb = BombProjectile(...)
                enemy.register_bomb(bomb)
        """
        if not self.should_spawn_bomb:
            return None

        self.should_spawn_bomb = False  # One bomb per attack window
        return {
            'start_x':   self.x,
            'start_y':   self.y,
            'target_x':  self.bomb_target_x,
            'target_y':  self.bomb_target_y,
            'damage':    self.attack_damage,
            'flight_time': 1.0,             # Seconds until detonation
            'player':    self._pending_bomb_player,
            'owner':     self,
        }

    def register_bomb(self, bomb):
        """Hand a newly created BombProjectile to this enemy for ownership.

        The enemy ticks and draws all owned bombs each frame.
        Do NOT add the bomb to a separate global list — the enemy manages it.
        """
        self.active_bombs.append(bomb)

    def get_bomb_drawables(self):
        """Return all drawable bomb/explosion objects for inclusion in the y-sorted draw list.

        Game loop pattern:
            for enemy in enemies:
                drawables.extend(enemy.get_bomb_drawables())
        """
        result = []
        for bomb in self.active_bombs:
            if bomb.state != bomb.STATE_EXPLODED:
                result.append(bomb)
            if bomb.pending_explosion is not None and bomb.pending_explosion.active:
                result.append(bomb.pending_explosion)
        return result

    def get_bullet_spawn_data(self):
        """Return bullet parameters and clear the spawn flag, or None if not ready."""
        if not self.should_spawn_bullet:
            return None

        self.should_spawn_bullet = False  # One bullet per attack window
        return {
            'x':         self.x,
            'y':         self.y,
            'dx':        self.bullet_dx,
            'dy':        self.bullet_dy,
            'speed':     self.projectile_speed,
            'damage':    self.attack_damage,
            'direction': self.direction,
            'owner':     self,
        }

    def get_kiblast_spawn_data(self):
        """Return ki-blast parameters and clear the spawn flag, or None if not ready.

        For BossEnemy, this flag is set in update() once the charge animation finishes.
        Game loop pattern (same as bullet/rocket)::

            if enemy.should_spawn_kiblast:
                data = enemy.get_kiblast_spawn_data()
                blast = Projectile(data['x'], data['y'], data['direction'])
                enemy.projectiles.append(blast)
        """
        if not self.should_spawn_kiblast:
            return None

        self.should_spawn_kiblast = False  # One blast per attack window
        return {
            'x':         self.x,
            'y':         self.y,
            'dx':        self.kiblast_dx,
            'dy':        self.kiblast_dy,
            'speed':     self.projectile_speed,
            'damage':    self.attack_damage,
            'direction': self.direction,
            'owner':     self,
        }

    def get_rocket_spawn_data(self):
        """Return rocket parameters and clear the spawn flag, or None if not ready."""
        if not self.should_spawn_rocket:
            return None

        self.should_spawn_rocket = False  # One rocket per attack window
        return {
            'x':         self.x,
            'y':         self.y,
            'dx':        self.rocket_dx,
            'dy':        self.rocket_dy,
            'speed':     self.projectile_speed,
            'damage':    self.attack_damage,
            'direction': self.direction,
            'owner':     self,
        }

    def get_projectile_spawn_data(self, player):
        """Generic projectile spawn data for shooter enemies — aimed directly at the player.

        Used for simple free-aim projectiles (not the snapped bullet/rocket types).
        Returns None if this enemy is not a shooter or isn't currently attacking.
        """
        if self.enemy_category != 'shooter' or not self.is_attacking:
            return None

        dx = player.x - self.x
        dy = player.y - self.y
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > 0.1:
            dx /= dist
            dy /= dist

        return {
            'x':         self.x,
            'y':         self.y,
            'dx':        dx,
            'dy':        dy,
            'speed':     self.projectile_speed,
            'damage':    self.attack_damage,
            'direction': self.direction,
            'owner':     self,
        }

    # =========================================================================
    # Rendering
    # =========================================================================

    def draw(self, screen, camera, colors):
        """Draw the enemy sprite (or a debug placeholder rect) plus the HP bar."""
        if not self.active:
            return

        # Death animation — replaces the normal sprite while is_dying is True
        if self.is_dying:
            if self.death_frame_index < len(self.death_frames):
                base_size = 32
                render_size = int(base_size * RENDER_SCALE)
                frame = self.death_frames[self.death_frame_index]
                scaled_frame = pygame.transform.scale(frame, (render_size, render_size))
                screen_x = int((self.x * RENDER_SCALE) - camera.x)
                screen_y = int((self.y * RENDER_SCALE) - camera.y)
                screen.blit(scaled_frame, (screen_x - render_size // 2, screen_y - render_size // 2))
            return

        if (self.is_encased or self.is_rolling or self.is_releasing) and self.encasement_overlay:
            self.encasement_overlay.draw(screen, self.x, self.y, camera, RENDER_SCALE)
        elif self.has_sprite:
            self.sprite.draw(screen, self.x, self.y, camera, RENDER_SCALE, self.hurt_tint)
        else:
            # Debug placeholder — color encodes AI/combat state at a glance
            screen_x = (self.x * RENDER_SCALE) - camera.x
            screen_y = (self.y * RENDER_SCALE) - camera.y

            if self.is_stunned:
                color = YELLOW                  # Yellow while stunned
            elif self.is_attacking:
                color = (255, 0, 255)           # Magenta while attacking
            elif self.is_knocked_back:
                color = (100, 100, 100)         # Grey while staggered
            elif self.state == 'chase':
                color = YELLOW if self.stuck_timer > 1.0 else RED  # Yellow = stuck
            else:
                color = ORANGE                  # Idle / wandering

            sw = self.width * RENDER_SCALE
            sh = self.height * RENDER_SCALE
            pygame.draw.rect(screen, color, (screen_x - sw // 2, screen_y - sh // 2, sw, sh))

        # brown_destruction.png burst — drawn on top of whatever's above,
        # simultaneously with the ball forming/dissolving (see
        # _spawn_destruction_effect() / encase() / _release_from_encasement()).
        if self.destruction_effect_active and self.death_frames:
            frame = self.death_frames[self.destruction_effect_frame_index % len(self.death_frames)]
            render_size = int(32 * RENDER_SCALE)
            scaled = pygame.transform.scale(frame, (render_size, render_size))
            screen_x = int((self.destruction_effect_x * RENDER_SCALE) - camera.x)
            screen_y = int((self.destruction_effect_y * RENDER_SCALE) - camera.y)
            screen.blit(scaled, (screen_x - render_size // 2, screen_y - render_size // 2))