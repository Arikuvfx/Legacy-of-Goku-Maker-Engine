import pygame
import time
from config.settings import WORLD_WIDTH, WORLD_HEIGHT, WHITE, GRAY, PURPLE, BLUE, RED, YELLOW, BLACK
from attacks import Projectile, BeamAttack, MeleeAttack
from core.sprite_system import create_character_sprite
from core.draw_layers import DrawLayer
from config.settings import RENDER_SCALE


class Player:
    def __init__(self, x, y, character='goku', costume='base', game_config=None):
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        # Adjust base speeds to account for RENDER_SCALE
        self.speed = 3 / RENDER_SCALE
        self.run_speed = 6 / RENDER_SCALE
        self.hp = 100
        self.max_hp = 100
        self.ki = 100
        self.max_ki = 100
        self.level = 1
        self.exp = 0
        self.direction = 'down'
        self.inventory = []
        self.is_running = False
        self.is_attacking = False
        self.attack_timer = 0
        self.attack_cooldown = 0
        self.exp_to_next_level = 100
        self.stat_points = 0
        self.pending_level_up = False

        # Add layer properties - ENABLE Y-SORTING for depth with objects
        self.draw_layer = DrawLayer.PLAYER
        self.y_sort = True  # Changed to True for Y-based depth sorting

        # Transformation system (will be initialized after game_config is set)
        self.transformation = None
        if game_config:
            from core.transformation_system import TransformationSystem
            self.transformation = TransformationSystem(self, game_config)

        # Player Stats
        self.stats = {
            'strength': 1,
            'ki_power': 1,
            'vitality': 1,
            'energy': 1,
            'speed': 1,
            'defense': 1
        }

        # Sprite system
        self.sprite = create_character_sprite(character, costume, 32, 32)

        # Animation state
        self.current_animation_state = 'idle'

        # Ki attack modes ('blast', 'beam', 'transform')
        self.ki_attack_mode = 'blast'
        self.is_charging_beam = False
        self.beam_charge_time = 0
        self.beam_charge_required = 1.5
        self.is_firing_beam = False
        self.current_beam = None

        # Attack costs and settings
        self.blast_ki_cost = 10
        self.beam_ki_drain = 20
        self.melee_duration = 0.3

        # Pending blast projectile (spawns after animation)
        self.pending_blast = None

        # For double tap detection
        self.last_key_press = {}
        self.double_tap_window = 0.3

        # Damage and knockback
        self.is_knocked_back = False
        self.knockback_timer = 0
        self.knockback_duration = 0.4
        self.knockback_velocity_x = 0
        self.knockback_velocity_y = 0
        self.invulnerable = False
        self.invulnerable_timer = 0
        self.invulnerable_duration = 0.5

        # Collision knockback properties
        self.is_collision_knockback = False
        self.collision_knockback_timer = 0
        self.collision_knockback_duration = 0.4  # Adjustable knockback duration
        self.collision_knockback_velocity_x = 0
        self.collision_knockback_velocity_y = 0
        self.collision_knockback_strength = 400  # Adjustable knockback strength
        self.last_move_direction = {'dx': 0, 'dy': 0}  # Track last movement direction

        # Input tracking
        self.is_q_pressed = False

    def get_sort_key(self):
        # Return Y position for proper depth sorting
        return (self.draw_layer, self.y)

    def can_act(self):
        """Check if player can perform actions (not locked in animation)"""
        # Can't act during transformation states
        if self.transformation and not self.transformation.can_player_act():
            return False

        return not (self.is_attacking or self.is_charging_beam or self.is_firing_beam or self.is_knocked_back)

    def can_move(self):
        """Check if player can move"""
        if self.is_collision_knockback:
            return False
        return self.can_act()

    def start_collision_knockback(self, collision_direction_x, collision_direction_y):
        """
        Start collision knockback animation
        collision_direction_x/y: Direction player was moving when collision occurred
        """
        self.is_collision_knockback = True
        self.collision_knockback_timer = self.collision_knockback_duration

        # Knockback in opposite direction of movement
        self.collision_knockback_velocity_x = -collision_direction_x * self.collision_knockback_strength
        self.collision_knockback_velocity_y = -collision_direction_y * self.collision_knockback_strength

        # Play hurt/knockback animation
        self.sprite.set_animation('hurt', self.direction)
        self.current_animation_state = 'hurt'

    def is_transformed(self):
        """Check if player is currently in transformed state"""
        return self.transformation and self.transformation.is_transformed

    def move(self, dx, dy, is_running, world_width, world_height):
        if not self.can_move():
            return

        # Store movement direction for collision detection
        self.last_move_direction['dx'] = dx
        self.last_move_direction['dy'] = dy

        # UPDATE DIRECTION BASED ON MOVEMENT - FIXED VERSION
        if dx != 0 or dy != 0:
            # Priority: If there's horizontal movement, use horizontal direction
            if dx != 0:
                if dx > 0:
                    self.direction = 'right'
                else:
                    self.direction = 'left'
            # Otherwise use vertical direction
            elif dy != 0:
                if dy > 0:
                    self.direction = 'down'
                else:
                    self.direction = 'up'

        current_speed = self.run_speed if is_running else self.speed
        self.x += dx * current_speed
        self.y += dy * current_speed

        self.x = max(self.width // 2, min(self.x, world_width - self.width // 2))
        self.y = max(self.height // 2, min(self.y, world_height - self.height // 2))

        anim = 'run' if is_running else 'walk'
        self.sprite.set_animation(anim, self.direction)
        self.current_animation_state = anim


    def get_current_ki_cost(self):
        """Get Ki cost for attacks (0 if transformed)"""
        if self.is_transformed():
            return 0
        return self.blast_ki_cost

    def shoot_blast(self):
        """Start blast animation - projectile spawns when animation finishes"""
        if not self.can_act() or self.attack_cooldown > 0:
            return

        ki_cost = self.get_current_ki_cost()
        if self.ki >= ki_cost:
            self.ki -= ki_cost
            self.is_attacking = True
            self.attack_cooldown = 0.5

            # Set blast animation
            self.sprite.set_animation('kiblast', self.direction)
            self.current_animation_state = 'kiblast'

            # Mark that we need to spawn blast when animation ends
            self.pending_blast = True

    def get_blast_spawn_position(self):
        """Calculate where the blast should spawn based on direction"""
        spawn_x = self.x
        spawn_y = self.y

        if self.direction == 'up':
            spawn_y -= self.height // 2
        elif self.direction == 'down':
            spawn_y += self.height // 2
        elif self.direction == 'left':
            spawn_x -= self.width // 2
        elif self.direction == 'right':
            spawn_x += self.width // 2

        return spawn_x, spawn_y

    def start_charging_beam(self):
        if not self.can_act():
            return False

        if self.ki > 0 or self.is_transformed():
            self.is_charging_beam = True
            self.beam_charge_time = 0
            self.sprite.set_animation('charge', self.direction)
            self.current_animation_state = 'charge'
            self.is_q_pressed = True
            return True
        return False

    def update_beam_charge(self, dt):
        if self.is_charging_beam:
            self.beam_charge_time += dt

            # Check if charge is complete and auto-fire
            if self.beam_charge_time >= self.beam_charge_required and not self.is_firing_beam:
                self.fire_beam_auto()

    def fire_beam_auto(self):
        """Fire beam automatically when charge is complete"""
        if self.is_charging_beam and self.beam_charge_time >= self.beam_charge_required:
            self.is_charging_beam = False
            self.is_firing_beam = True
            self.beam_charge_time = 0

            # Switch to firing animation
            self.sprite.set_animation('firebeam', self.direction)
            self.current_animation_state = 'firebeam'

            spawn_x = self.x
            spawn_y = self.y

            if self.direction == 'up':
                spawn_y -= 15
            elif self.direction == 'down':
                spawn_y += 15
            elif self.direction == 'left':
                spawn_x -= 15
                spawn_y += 5
            elif self.direction == 'right':
                spawn_x += 15
                spawn_y += 5

            self.current_beam = BeamAttack(spawn_x, spawn_y, self.direction, scale=2.0)
            return self.current_beam
        return None

    def stop_beam(self):
        """Stop beam charging or firing and return to idle"""
        self.is_charging_beam = False
        self.is_firing_beam = False
        self.beam_charge_time = 0
        self.is_q_pressed = False
        self.current_beam = None

        # Return to idle animation
        if self.current_animation_state in ['charge', 'kiblast', 'firebeam']:
            self.sprite.set_animation('idle', self.direction)
            self.current_animation_state = 'idle'

    def melee_attack(self):
        if not self.can_act() or self.attack_cooldown > 0:
            return None

        self.is_attacking = True
        self.attack_cooldown = 0.4

        self.sprite.set_animation('melee', self.direction)
        self.current_animation_state = 'melee'

        return MeleeAttack(self.x, self.y, self.direction)

    def update(self, dt):

        # Handle collision knockback
        if self.is_collision_knockback:
            self.collision_knockback_timer -= dt

            # Apply knockback velocity
            self.x += self.collision_knockback_velocity_x * dt
            self.y += self.collision_knockback_velocity_y * dt

            # Clamp to world bounds
            self.x = max(self.width // 2, min(self.x, WORLD_WIDTH - self.width // 2))
            self.y = max(self.height // 2, min(self.y, WORLD_HEIGHT - self.height // 2))

            # Reduce velocity over time (friction)
            self.collision_knockback_velocity_x *= 0.85
            self.collision_knockback_velocity_y *= 0.85

            # End knockback when timer expires
            if self.collision_knockback_timer <= 0:
                self.is_collision_knockback = False
                self.collision_knockback_velocity_x = 0
                self.collision_knockback_velocity_y = 0
                # Return to idle animation
                self.sprite.set_animation('idle', self.direction)
                self.current_animation_state = 'idle'
                return  # Skip rest of update during transition

        # Handle regular knockback (damage-based)
        if self.is_knocked_back:
            self.knockback_timer -= dt

            self.x += self.knockback_velocity_x * dt
            self.y += self.knockback_velocity_y * dt

            self.x = max(self.width // 2, min(self.x, WORLD_WIDTH - self.width // 2))
            self.y = max(self.height // 2, min(self.y, WORLD_HEIGHT - self.height // 2))

            self.knockback_velocity_x *= 0.85
            self.knockback_velocity_y *= 0.85

            if self.knockback_timer <= 0:
                self.is_knocked_back = False
                self.knockback_velocity_x = 0
                self.knockback_velocity_y = 0

        # Handle knockback
        if self.is_knocked_back:
            self.knockback_timer -= dt

            self.x += self.knockback_velocity_x * dt
            self.y += self.knockback_velocity_y * dt

            self.x = max(self.width // 2, min(self.x, WORLD_WIDTH - self.width // 2))
            self.y = max(self.height // 2, min(self.y, WORLD_HEIGHT - self.height // 2))

            self.knockback_velocity_x *= 0.85
            self.knockback_velocity_y *= 0.85

            if self.knockback_timer <= 0:
                self.is_knocked_back = False
                self.knockback_velocity_x = 0
                self.knockback_velocity_y = 0

        # Handle invulnerability
        if self.invulnerable:
            self.invulnerable_timer -= dt
            if self.invulnerable_timer <= 0:
                self.invulnerable = False

        # Handle attack cooldown
        if self.attack_cooldown > 0:
            self.attack_cooldown -= dt

        # Update sprite animation FIRST
        self.sprite.update(dt)

        # THEN check if animations finished
        # Check transformation animations
        if self.current_animation_state == 'transform':
            if self.sprite.is_animation_finished():
                if self.transformation:
                    self.transformation.complete_transform()

        elif self.current_animation_state == 'untransform':
            if self.sprite.is_animation_finished():
                if self.transformation:
                    self.transformation.complete_untransform()

        # Check if melee animation finished
        elif self.current_animation_state == 'melee':
            if self.sprite.is_animation_finished():
                self.is_attacking = False
                self.sprite.set_animation('idle', self.direction)
                self.current_animation_state = 'idle'

        # Check if blast animation finished
        elif self.current_animation_state == 'kiblast':
            if self.sprite.is_animation_finished():
                self.is_attacking = False

                # Signal that blast is ready to spawn NOW
                if self.pending_blast:
                    self.pending_blast = 'ready'

                self.sprite.set_animation('idle', self.direction)
                self.current_animation_state = 'idle'

        # Check if charge animation should continue
        elif self.current_animation_state == 'charge':
            # If we're charging but Q is no longer pressed, stop charging
            if self.is_charging_beam and not self.is_q_pressed:
                self.stop_beam()
            # Continue charging animation while charging
            elif self.is_charging_beam:
                # Update beam charge
                self.update_beam_charge(dt)

        # Handle firing beam animation
        elif self.current_animation_state == 'firebeam':
            # Keep playing the firing animation while beam is active
            if self.is_firing_beam:
                # Only drain ki if not transformed
                if not self.is_transformed():
                    ki_drain = self.beam_ki_drain * dt
                    self.ki -= ki_drain
                    if self.ki <= 0:
                        self.ki = 0
                        self.stop_beam()
                    # If Q is released while firing, stop the beam
                    elif not self.is_q_pressed:
                        self.stop_beam()
                # If transformed, just check for Q release
                elif not self.is_q_pressed:
                    self.stop_beam()
            else:
                # If not firing anymore, return to idle
                self.sprite.set_animation('idle', self.direction)
                self.current_animation_state = 'idle'

        # Legacy ki drain code (kept for compatibility)
        if self.is_firing_beam and self.current_animation_state != 'firebeam':
            if not self.is_transformed():
                ki_drain = self.beam_ki_drain * dt
                self.ki -= ki_drain
                if self.ki <= 0:
                    self.ki = 0
                    self.stop_beam()
                elif not self.is_q_pressed:
                    self.stop_beam()
            elif not self.is_q_pressed:
                self.stop_beam()

    def check_double_tap(self, key):
        current_time = time.time()
        if key in self.last_key_press:
            if current_time - self.last_key_press[key] < self.double_tap_window:
                self.last_key_press[key] = 0
                return True
        self.last_key_press[key] = current_time
        return False

    def take_damage(self, damage, knockback_x, knockback_y):
        if self.invulnerable:
            return

        # If transforming or untransforming, interrupt the animation
        if self.transformation:
            if self.transformation.is_transforming:
                self.transformation.is_transforming = False
                self.transformation.progress = 0.0  # Reset progress
            elif self.transformation.is_untransforming:
                # Can't be interrupted during untransform
                return

        self.hp -= damage
        if self.hp < 0:
            self.hp = 0

        self.is_knocked_back = True
        self.knockback_timer = self.knockback_duration
        self.knockback_velocity_x = knockback_x * 300
        self.knockback_velocity_y = knockback_y * 300

        self.invulnerable = True
        self.invulnerable_timer = self.invulnerable_duration

        # Cancel any ongoing attacks
        self.is_attacking = False
        self.is_charging_beam = False
        self.is_firing_beam = False
        self.pending_blast = None
        self.is_q_pressed = False
        if self.current_beam:
            self.current_beam = None

        self.sprite.set_animation('hurt', self.direction)
        self.current_animation_state = 'hurt'

    def gain_exp(self, amount, game_config):
        self.exp += amount
        while self.exp >= self.exp_to_next_level and self.level < game_config.max_level:
            self.level_up(game_config)

    def level_up(self, game_config):
        self.exp -= self.exp_to_next_level
        self.level += 1
        self.stat_points += game_config.stat_points_per_level
        self.pending_level_up = True
        self.exp_to_next_level = game_config.get_xp_for_level(self.level)
        self.hp = self.max_hp
        self.ki = self.max_ki

    def apply_stat_point(self, stat_name, game_config):
        if self.stat_points > 0 and self.stats[stat_name] < game_config.max_stat_value:
            self.stats[stat_name] += 1
            self.stat_points -= 1
            self.update_derived_stats()
            return True
        return False

    def update_derived_stats(self):
        self.max_hp = 100 + (self.stats['vitality'] - 1) * 10
        self.max_ki = 100 + (self.stats['energy'] - 1) * 5
        base_speed = 3 / RENDER_SCALE
        base_run = 6 / RENDER_SCALE
        speed_multiplier = 1 + (self.stats['speed'] - 1) * 0.05
        self.speed = base_speed * speed_multiplier
        self.run_speed = base_run * speed_multiplier

    def draw(self, screen, camera, colors):
        self.sprite.draw(screen, self.x, self.y, camera, scale=RENDER_SCALE)

        # Convert WORLD coordinates to SCREEN coordinates for indicators
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        indicator_color = colors['RED'] if (self.is_attacking or self.is_charging_beam or self.is_firing_beam) else \
        colors['YELLOW']

        # Direction indicator positions (scaled)
        offset = (self.height // 2) * RENDER_SCALE
        indicator_radius = 4

        if self.direction == 'up':
            pygame.draw.circle(screen, indicator_color, (int(screen_x), int(screen_y - offset + 5)), indicator_radius)
        elif self.direction == 'down':
            pygame.draw.circle(screen, indicator_color, (int(screen_x), int(screen_y + offset - 5)), indicator_radius)
        elif self.direction == 'left':
            pygame.draw.circle(screen, indicator_color, (int(screen_x - offset + 5), int(screen_y)), indicator_radius)
        elif self.direction == 'right':
            pygame.draw.circle(screen, indicator_color, (int(screen_x + offset - 5), int(screen_y)), indicator_radius)

        # Running indicator
        if self.is_running:
            pygame.draw.circle(screen, colors['WHITE'], (int(screen_x), int(screen_y - offset - 10)), 3)

        # Beam charge bar
        if self.is_charging_beam:
            charge_progress = min(self.beam_charge_time / self.beam_charge_required, 1.0)
            bar_width = 40
            bar_height = 5
            bar_x = screen_x - bar_width // 2
            bar_y = screen_y - offset - 20
            pygame.draw.rect(screen, colors['BLACK'], (bar_x, bar_y, bar_width, bar_height))
            pygame.draw.rect(screen, colors['YELLOW'], (bar_x, bar_y, int(bar_width * charge_progress), bar_height))
            pygame.draw.rect(screen, colors['WHITE'], (bar_x, bar_y, bar_width, bar_height), 1)