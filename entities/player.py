import pygame
import time
from config.settings import (
    WORLD_WIDTH, WORLD_HEIGHT,
    WHITE, GRAY, PURPLE, BLUE, RED, YELLOW, BLACK,
    RENDER_SCALE,
)
from attacks import Projectile, BeamAttack, MeleeAttack
from core.sprite_system import create_character_sprite
from core.draw_layers import DrawLayer


# ---------------------------------------------------------------------------
# Per-direction spawn offsets used by both blast and beam.
# (offset_x, offset_y) in world units, relative to the player centre.
# ---------------------------------------------------------------------------
_DIRECTION_SPAWN_OFFSETS = {
    'up':    (0,   -15),
    'down':  (0,    15),
    'left':  (-15,   5),
    'right': (15,    5),
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
        self.speed = 5 / RENDER_SCALE
        self.run_speed = 10 / RENDER_SCALE

        self.hp = 100
        self.max_hp = 100
        self.ki = 100
        self.max_ki = 100
        self.level = 60
        self.exp = 0
        self.exp_to_next_level = 100
        self.stat_points = 0
        self.pending_level_up = False

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
        self.IDLE_WAIT_DELAY = 5.0

        # -----------------------------------------------------------------
        # Ki attack state
        # -----------------------------------------------------------------
        self.ki_attack_mode = 'blast'       # 'blast' | 'beam' | 'transform'

        # Attacks this character is allowed to use, loaded from their JSON config.
        # 'ki_mode_config' mirrors cfg["attacks"]["ki_attack_mode"] and controls
        # which modes the TAB key cycles through ('blast'|'beam'|'both').
        self.equipped_attacks = []
        self.ki_mode_config   = 'blast'     # default until config is applied

        self.is_charging_beam = False
        self.beam_charge_time = 0
        self.beam_charge_required = 1.5     # Seconds of charge before auto-fire
        self.is_firing_beam = False
        self.current_beam = None

        # Attack costs
        self.blast_ki_cost = 10
        self.beam_ki_drain = 20             # Ki drained per second while firing
        self.melee_duration = 0.3

        # Blast is queued here and spawned once the kiblast animation finishes
        self.pending_blast = None

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

    def can_act(self):
        """False while locked in an animation, transitioning, or knocked back."""
        if self.is_transitioning:
            return False
        if self.is_map_jumping:
            return False
        if self.is_collision_knockback:
            return False
        if self.transformation and not self.transformation.can_player_act():
            return False
        return not (self.is_attacking or self.is_charging_beam
                    or self.is_firing_beam or self.is_knocked_back)

    def can_move(self):
        """False during collision knockback or whenever can_act() returns False."""
        if self.is_transitioning:
            return False
        if self.is_map_jumping:
            return False
        if self.is_collision_knockback:
            return False
        return self.can_act()

    def get_current_ki_cost(self):
        """Ki cost for the current attack (0 while transformed — free attacks)."""
        return 0 if self.is_transformed() else self.blast_ki_cost

    def enter_idle(self):
        """Return to standing idle and (re)start the stand-still timer.

        Centralized so every place that snaps back to idle also resets the
        idle-wait clock — otherwise stopping right as the wait animation was
        about to trigger would carry a stale timer into the next stand-still.
        """
        self.sprite.set_animation('idle', self.direction)
        self.current_animation_state = 'idle'
        self.idle_timer = 0.0

    # =========================================================================
    # Collision helpers
    # =========================================================================

    def get_collision_rect(self):
        """Return the player's directional hitbox in world coordinates."""
        offset = self.hitbox_offsets.get(self.direction, {'x': 0, 'y': 0})
        left = self.x + offset['x'] - self.hitbox_width // 2
        top  = self.y + offset['y'] - self.hitbox_height // 2
        return pygame.Rect(left, top, self.hitbox_width, self.hitbox_height)

    def check_collision_with_obstacles(self, new_x, new_y):
        """True if the wall hitbox at (new_x, new_y) overlaps any obstacle."""
        temp_rect = pygame.Rect(
            new_x - self.wall_hitbox_width // 2,
            new_y + self.wall_hitbox_offset_y - self.wall_hitbox_height // 2,
            self.wall_hitbox_width,
            self.wall_hitbox_height,
        )
        for obstacle in self.obstacles:
            if hasattr(obstacle, 'get_collision_rect'):
                rect = obstacle.get_collision_rect()
                if rect is not None and temp_rect.colliderect(rect):
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

        # Cache room dimensions so knockback bounds checks stay in sync
        self.current_room_width = world_width
        self.current_room_height = world_height

        # Track the most recent input vector for collision-knockback direction
        self.last_move_direction['dx'] = dx
        self.last_move_direction['dy'] = dy

        if dx != 0 or dy != 0:
            if dx != 0 and dy == 0:
                # Pure horizontal input — update facing direction
                self.direction = 'right' if dx > 0 else 'left'
            elif dy != 0 and dx == 0:
                # Pure vertical input — update facing direction
                self.direction = 'down' if dy > 0 else 'up'
            # Diagonal: keep the current direction to avoid sprite flipping

        self.is_running = is_running
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
        self.attack_cooldown = 0.4
        self.sprite.set_animation('melee', self.direction)
        self.current_animation_state = 'melee'

        melee = MeleeAttack(self.x, self.y, self.direction)
        melee.owner = self
        return melee

    def shoot_blast(self):
        """Queue a ki blast — the projectile spawns once the kiblast animation finishes."""
        if not self.can_act() or self.attack_cooldown > 0:
            return

        ki_cost = self.get_current_ki_cost()
        if self.ki >= ki_cost:
            self.ki -= ki_cost
            self.is_attacking = True
            self.attack_cooldown = 0.5
            self.sprite.set_animation('kiblast', self.direction)
            self.current_animation_state = 'kiblast'
            self.pending_blast = True  # Checked in update(); set to 'ready' when animation ends

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

        if (self.is_q_pressed and self._can_continue_blast_hold()
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

            # Don't touch pending_blast here — it may still be 'ready' from the
            # shot that just finished, awaiting game.py's spawn check this same
            # frame. Queue it for arming next frame instead (see update()).
            self._queue_next_pending = True
        else:
            self.is_attacking = False
            self._blast_hold_frame = 2  # Reset so the next fresh press starts on frame 2
            self.enter_idle()

    def start_charging_beam(self):
        """Begin the beam charge animation. Returns True on success."""
        if not self.can_act():
            return False

        if self.ki > 0 or self.is_transformed():
            self.is_charging_beam = True
            self.beam_charge_time = 0
            self.is_q_pressed = True
            self.sprite.set_animation('charge', self.direction)
            self.current_animation_state = 'charge'
            return True

        return False

    def update_beam_charge(self, dt):
        """Advance the beam charge timer and auto-fire when fully charged."""
        if self.is_charging_beam:
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
        self.sprite.set_animation('firebeam', self.direction)
        self.current_animation_state = 'firebeam'

        # Spawn the beam slightly in front of the player based on facing direction
        ox, oy = self._get_spawn_offset()
        self.current_beam = BeamAttack(self.x + ox, self.y + oy, self.direction)
        return self.current_beam

    def stop_beam(self):
        """Cancel beam charging or firing and return to idle."""
        self.is_charging_beam = False
        self.is_firing_beam = False
        self.beam_charge_time = 0
        self.is_q_pressed = False
        self.current_beam = None

        if self.current_animation_state in ('charge', 'kiblast', 'firebeam'):
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
        self.is_q_pressed      = False
        self.current_beam      = None

        # Derive the correct folder (base or transformed form).
        # Use self.sprite.base_path so this always matches wherever
        # CharacterSpriteLoader put the rest of the sprites.
        path = f'{self.sprite.base_path}/map_jump.png'

        self._map_jump_frames      = []
        self._map_jump_frame_idx   = 0
        self._map_jump_frame_timer = 0.0
        # Pre-scaled-frame cache for draw() — reset here since a fresh load
        # means new source Surface objects (stale entries would point at
        # frames that no longer match this direction/sheet).
        self._map_jump_scaled_cache = {}

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

    def take_damage(self, damage, knockback_x, knockback_y,
                    ignore_invulnerability=False, no_knockback=False):
        """Apply damage and knockback from an enemy hit.

        Args:
            damage:                 HP to subtract.
            knockback_x/y:          Unit direction of the knockback vector.
            ignore_invulnerability: Bypass i-frames (e.g. for DoT effects).
            no_knockback:           Grant i-frames only — no physics knockback.
        """
        if self.invulnerable and not ignore_invulnerability:
            return

        # Interrupt a transform-in-progress; untransform cannot be interrupted
        if self.transformation:
            if self.transformation.is_transforming:
                self.transformation.is_transforming = False
                self.transformation.progress = 0.0
            elif self.transformation.is_untransforming:
                return

        self.hp = max(0, self.hp - damage)
        self.last_damage_taken = damage  # Stored so game.py can spawn a popup

        if no_knockback:
            # Just grant i-frames — the caller owns the visual feedback
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
        self.is_q_pressed = False
        self.current_beam = None

        self.sprite.set_animation('hurt', self.direction)
        self.current_animation_state = 'hurt'

    # =========================================================================
    # XP and levelling
    # =========================================================================

    def gain_exp(self, amount, game_config):
        """Add XP and trigger as many level-ups as the new total allows."""
        self.exp += amount
        while self.exp >= self.exp_to_next_level and self.level < game_config.max_level:
            self.level_up(game_config)

    def level_up(self, game_config):
        """Consume one level's worth of XP and apply the level-up rewards."""
        self.exp -= self.exp_to_next_level
        self.level += 1
        self.stat_points += game_config.stat_points_per_level
        self.pending_level_up = True
        self.exp_to_next_level = game_config.get_xp_for_level(self.level)
        # Fully restore HP and Ki on level-up
        self.hp = self.max_hp
        self.ki = self.max_ki

    def apply_stat_point(self, stat_name, game_config):
        """Spend one stat point on stat_name. Returns True if the point was spent."""
        if self.stat_points > 0 and self.stats[stat_name] < game_config.max_stat_value:
            self.stats[stat_name] += 1
            self.stat_points -= 1
            self.update_derived_stats()
            return True
        return False

    def update_derived_stats(self):
        """Recalculate max_hp, max_ki, speed, and run_speed from the current stat block.

        Called whenever a stat point is spent. Vitality → HP, Energy → Ki,
        Speed → movement speeds. Each stat point above 1 adds a fixed increment.
        """
        self.max_hp = 100 + (self.stats['vitality'] - 1) * 10
        self.max_ki = 100 + (self.stats['energy'] - 1) * 5

        speed_mult = 1 + (self.stats['speed'] - 1) * 0.05
        self.speed     = (5  / RENDER_SCALE) * speed_mult
        self.run_speed = (10 / RENDER_SCALE) * speed_mult

    # =========================================================================
    # Private helpers
    # =========================================================================

    def _tick_beam_ki_drain(self, dt):
        """Drain Ki while the beam is firing, and stop it when Ki runs out or Q is released.

        Transformed state skips the Ki drain but still checks for Q release.
        Called from update() in both the 'firebeam' animation branch and the
        safety fallback below it.
        """
        if not self.is_transformed():
            self.ki = max(0.0, self.ki - self.beam_ki_drain * dt)
            if self.ki <= 0 or not self.is_q_pressed:
                self.stop_beam()
        elif not self.is_q_pressed:
            self.stop_beam()

    # =========================================================================
    # Main update loop
    # =========================================================================

    def update(self, dt):
        """Advance timers, physics, and animation state for one frame."""

        # Reset per-frame block flags here (not just inside move()) so they
        # are always False during knockback frames when move() is never called.
        self._blocked_x = False
        self._blocked_y = False

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

        elif self.current_animation_state == 'untransform':
            if self.sprite.is_animation_finished():
                if self.transformation and self.transformation.is_untransforming:
                    self.transformation.complete_untransform()

        elif self.current_animation_state == 'melee':
            if self.sprite.is_animation_finished():
                self.is_attacking = False
                self.enter_idle()

        elif self.current_animation_state == 'kiblast':
            if self.sprite.is_animation_finished():
                # Raise the 'ready' flag so the game loop knows it can spawn the projectile now
                if self.pending_blast:
                    self.pending_blast = 'ready'
                self._advance_blast_or_idle()

        elif self.current_animation_state == 'kiblast_hold':
            if self.sprite.is_animation_finished():
                if self.pending_blast:
                    self.pending_blast = 'ready'
                self._advance_blast_or_idle()

        elif self.current_animation_state == 'hurt':
            if self.sprite.is_animation_finished():
                # Don't snap to idle until both knockback types have cleared
                if not self.is_knocked_back and not self.is_collision_knockback:
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

        elif self.current_animation_state == 'firebeam':
            if self.is_firing_beam:
                self._tick_beam_ki_drain(dt)
            else:
                # Beam stopped externally (e.g. enemy killed us mid-fire)
                self.enter_idle()

        # Safety fallback — if the beam is still firing but the animation state
        # drifted out of 'firebeam' somehow, drain Ki and check for stop.
        if self.is_firing_beam and self.current_animation_state != 'firebeam':
            self._tick_beam_ki_drain(dt)

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
        """Draw the player sprite with the current hurt tint applied."""
        if self.is_map_jumping and self._map_jump_frames:
            idx    = self._map_jump_frame_idx
            sx     = int(self.x * RENDER_SCALE - camera.x)
            sy     = int(self.y * RENDER_SCALE - camera.y)
            w      = int(self.width * RENDER_SCALE)
            h      = int(self._map_jump_frames[idx].get_height() * RENDER_SCALE)

            # Scaling is deterministic per frame index (same source frame,
            # same target size every time), so cache it instead of re-scaling
            # on every single frame of the ascent. Cache is reset in
            # start_map_jump() whenever a new jump sequence starts.
            cache = getattr(self, '_map_jump_scaled_cache', None)
            if cache is None:
                cache = self._map_jump_scaled_cache = {}
            scaled = cache.get(idx)
            if scaled is None:
                scaled = pygame.transform.scale(self._map_jump_frames[idx], (w, h))
                cache[idx] = scaled

            screen.blit(scaled, scaled.get_rect(center=(sx, sy)))
            return

        tint = getattr(self, 'hurt_tint', 0.0)
        self.sprite.draw(screen, self.x, self.y, camera, scale=RENDER_SCALE, hurt_tint=tint)