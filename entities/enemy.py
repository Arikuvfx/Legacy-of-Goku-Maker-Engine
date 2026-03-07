import pygame
import random
import math
import time
from config.settings import WORLD_WIDTH, WORLD_HEIGHT, RED, ORANGE, BLACK, GREEN, WHITE, YELLOW, RENDER_SCALE
from core.draw_layers import DrawLayer
from core.sprite_system import create_enemy_sprite


class Enemy:
    def __init__(self, x, y, enemy_type='tiger_bandit', variant='default', ai_type='easy', enemy_category='melee', shooter_style='bomb'):
        """Create an enemy at world position (*x*, *y*).

        Args:
            x, y: Starting world coordinates.
            enemy_type: Sprite folder key (e.g. 'tiger_bandit').
            variant: Colour/skin variant used by the sprite loader.
            ai_type: 'easy' for basic movement or 'advanced' for retreat/feint/rush.
            enemy_category: 'melee' for close-range or 'shooter' for ranged attacks.
            shooter_style: 'bomb', 'bullet', or 'rocket' — only used when
                           enemy_category is 'shooter'.
        """
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.speed = 1
        self.hp = 50
        self.max_hp = 50
        self.active = True

        # Enemy type and variant for sprite loading
        self.enemy_type = enemy_type
        self.variant = variant

        # AI TYPE SYSTEM - Different AI behaviors can be selected
        # 'easy' = basic movement, 'advanced' = retreats, feints, etc.
        self.ai_type = ai_type  # 'easy', 'advanced', etc.

        # ENEMY CATEGORY SYSTEM - Different combat styles
        # 'melee' = close-range attacks, 'shooter' = ranged projectile attacks
        self.enemy_category = enemy_category

        # Load sprite (will fall back to placeholder if not found)
        self.sprite = create_enemy_sprite(enemy_type, variant, self.width, self.height)
        self.has_sprite = self.sprite is not None

        # AI States
        self.state = 'idle'
        self.awareness_range = 200
        self.forget_range = 350

        # Track facing direction for directional attacks and sprites
        self.direction = 'down'  # 'up', 'down', 'left', 'right'

        # Idle movement
        self.idle_timer = 0
        self.idle_wait_time = 1.5
        self.idle_move_timer = 0
        self.idle_move_duration = 1.5
        self.idle_direction = None
        self.spawn_x = x
        self.spawn_y = y
        self.max_idle_distance = 100
        self.is_idle_moving = False
        self.move_velocity_x = 0
        self.move_velocity_y = 0

        self.target_x = x
        self.target_y = y

        # Combat system - configure based on category
        self.is_attacking = False
        self.attack_timer = 0
        self.attack_cooldown = 0

        if self.enemy_category == 'shooter':
            # SHOOTER CONFIGURATION - shared across all shooter styles
            self.shooter_style = shooter_style  # 'bomb' = parabolic throw, 'bullet' = straight shot

            if self.shooter_style == 'bullet':
                # GUNNER CONFIGURATION - faster, longer range, lighter damage per shot
                self.attack_duration = 0.35       # Snappy fire animation
                self.attack_cooldown_time = 1.2   # Fires more frequently than bomb thrower
                self.attack_range = 200           # Longer effective range
                self.preferred_distance = 160     # Keep more distance
                self.attack_damage = 10           # Lower per-shot damage (compensated by fire rate)
                self.projectile_speed = 350       # Fast bullet
            elif self.shooter_style == 'rocket':
                # ROCKET LAUNCHER CONFIGURATION - slow fire rate, high damage, moderate speed
                self.attack_duration = 0.5        # Slightly longer fire animation
                self.attack_cooldown_time = 3.5   # Slow reload
                self.attack_range = 220           # Long range
                self.preferred_distance = 170     # Keep distance
                self.attack_damage = 30           # High damage per shot
                self.projectile_speed = 220       # Slower than bullet
            else:
                # BOMB THROWER CONFIGURATION (original)
                self.attack_duration = 0.6
                self.attack_cooldown_time = 2.0
                self.attack_range = 150
                self.preferred_distance = 120
                self.attack_damage = 15
                self.projectile_speed = 200

            self.projectiles = []  # List of active projectiles

            # ADVANCED AI: Shooter melee rush properties
            self.is_doing_melee_rush = False          # Shooter is closing in for melee
            self.is_shooter_melee_attack = False       # Current attack is a melee (not ranged)
            self.shooter_melee_range = 18             # Close-range threshold for melee hit
            self.shooter_melee_damage = 12            # Damage for shooter melee strike
            self.melee_rush_chance = 0.55             # 55% chance to rush when eligible
            self.last_melee_rush_attempt = 0
            self.melee_rush_check_interval = random.uniform(4.0, 7.0)
            self.melee_rush_cooldown = 0
            self.melee_rush_cooldown_time = random.uniform(5.0, 9.0)
            self.melee_rush_timer = 0                 # Timeout so the rush doesn't last forever
            self.melee_rush_max_duration = 3.0        # Abort rush after 3 seconds if no hit
            self.melee_rush_swung = False             # Has already swung in this rush

            # Bomb spawning flags (bomb thrower)
            self.should_spawn_bomb = False
            self.bomb_spawned_this_attack = False
            self.bomb_target_x = 0
            self.bomb_target_y = 0
            self._pending_bomb_player = None  # player ref saved at throw time

            # Live bomb / explosion objects owned by this enemy.
            # The game loop should include enemy.get_bomb_drawables() in its
            # y-sorted draw list each frame so bombs depth-sort with the player.
            self.active_bombs = []

            # Bullet spawning flags (gunner)
            self.should_spawn_bullet = False
            self.bullet_spawned_this_attack = False
            self.bullet_dx = 0.0
            self.bullet_dy = 0.0

            # Rocket spawning flags (rocketlauncher)
            self.should_spawn_rocket = False
            self.rocket_spawned_this_attack = False
            self.rocket_dx = 0.0
            self.rocket_dy = 0.0
        else:
            # MELEE CONFIGURATION (default)
            self.attack_duration = 0.4
            self.attack_cooldown_time = 1.1
            self.attack_range = 15  # Close range
            self.preferred_distance = 0  # Get close to player
            self.attack_damage = 10

        # Wait after attack before approaching again
        self.wait_after_attack = 0
        self.wait_after_attack_duration = 0.4

        # Knockback - animation-based
        self.is_knocked_back = False
        self.knockback_velocity_x = 0
        self.knockback_velocity_y = 0

        # Hurt tint - red flash that fades after taking damage
        self.hurt_tint = 0.0            # 1.0 = full red, 0.0 = no tint
        self.hurt_tint_duration = 0.45  # seconds to fully fade

        self.draw_layer = DrawLayer.ENEMIES
        self.y_sort = True

        # Store reference to obstacles for collision checking during knockback
        self.obstacles = []

        # Store reference to other enemies for collision avoidance
        self.other_enemies = []

        # Stuck detection
        self.stuck_timer = 0
        self.last_x = x
        self.last_y = y
        self.movement_threshold = 0.5

        # Preferred angle around player for positioning
        self.preferred_angle = random.uniform(0, math.pi * 2)

        # Separation force for enemy avoidance
        self.separation_force = 0
        self.separation_strength = 0.7
        self.min_separation_distance = 16  # Minimum horizontal distance
        self.min_vertical_separation = 16  # Minimum vertical distance

        # PATHFINDING FIX: Store current pathfinding direction and commitment timer
        self.pathfind_direction = None  # Stores (dx, dy) when navigating around obstacle
        self.pathfind_commit_timer = 0  # How long to commit to current path
        self.pathfind_commit_duration = 1  # Commit to a path for at least this long

        # ADVANCED AI: Retreat and breather mechanics
        self.low_health_threshold = 0.3  # Retreat when below 30% HP
        self.consecutive_hits = 0  # Track hits received in quick succession
        self.last_hit_time = 0  # When last hit occurred
        self.hit_combo_window = 2.0  # Reset combo if no hit for 2 seconds
        self.hit_combo_threshold = 3  # Retreat after 3 consecutive hits

        # Retreat chance and cooldown
        self.retreat_chance = 0.35  # 35% chance to retreat when conditions met
        self.last_retreat_attempt = 0  # Last time we checked for retreat
        # Randomize initial check cooldown so enemies don't all check at once
        self.retreat_check_cooldown = random.uniform(1.5, 3.0)  # Check every 1.5-3 seconds
        self.retreat_check_interval = random.uniform(1.5, 3.0)  # Store the interval for this enemy
        self.retreat_cooldown = 0  # Cooldown after finishing a retreat cycle
        self.retreat_cooldown_time = random.uniform(3.0, 5.0)  # Can't retreat again for 3-5 seconds

        # Retreat state
        self.is_retreating = False
        self.retreat_timer = 0
        self.retreat_duration = 2.0  # Retreat for 2 seconds
        self.retreat_distance = 150  # Try to get this far from player
        self.retreat_target_x = 0
        self.retreat_target_y = 0

        # Breather state (rest after retreat)
        self.is_breathing = False
        self.breather_timer = 0
        self.breather_duration = 1.5  # Take a breather for 1.5 seconds

        # ADVANCED AI: Feinting behavior - backing away while facing player
        self.is_feinting = False
        self.feint_timer = 0
        self.feint_duration = random.uniform(0.6, 1.2)  # Feint for 0.6-1.2 seconds (shorter)
        self.feint_distance = 18  # Distance to move during feints (closer)

        # Pause after feinting (sometimes wait after backing away)
        self.is_pausing_after_feint = False
        self.pause_after_feint_timer = 0
        self.pause_after_feint_chance = 0.5  # 50% chance to pause after feinting
        self.pause_after_feint_duration = random.uniform(0.4, 0.8)  # Short pause

        # Feint chance and cooldown - only triggers after attacking
        self.feint_chance = 0.20  # 20% chance to feint after attacking
        self.last_feint_attempt = 0
        self.feint_check_cooldown = 0.5  # Check shortly after attack
        self.feint_cooldown = 0
        self.feint_cooldown_time = random.uniform(5.0, 7.0)  # Can't feint again for 5-7 seconds (shorter)

    def is_standing_still(self):
        """Check if this enemy is standing still (attacking, waiting after attack, breathing, feinting, or pausing after feint)"""
        return self.is_attacking or self.wait_after_attack > 0 or self.is_breathing or self.is_feinting or self.is_pausing_after_feint

    def set_direction_from_movement(self, dx, dy, threshold=0.2):
        """
        Set direction based on movement vector with hysteresis to prevent rapid switching.
        threshold: How much larger one component must be to choose that direction.
        """
        abs_dx = abs(dx)
        abs_dy = abs(dy)

        # Check which component is dominant with a threshold
        if abs_dx > abs_dy + threshold:
            # Horizontal movement is clearly dominant
            self.direction = 'right' if dx > 0 else 'left'
        elif abs_dy > abs_dx + threshold:
            # Vertical movement is clearly dominant
            self.direction = 'down' if dy > 0 else 'up'
        else:
            # Diagonal movement - maintain current direction if possible
            # Only change if we're moving strongly in a new direction
            current_is_horizontal = self.direction in ('left', 'right')
            current_is_vertical = self.direction in ('up', 'down')

            if current_is_horizontal and abs_dx > 0.05:
                # Keep horizontal if already moving horizontally and still have horizontal motion
                self.direction = 'right' if dx > 0 else 'left'
            elif current_is_vertical and abs_dy > 0.05:
                # Keep vertical if already moving vertically and still have vertical motion
                self.direction = 'down' if dy > 0 else 'up'
            else:
                # Default to the most dominant component
                if abs_dx >= abs_dy:
                    self.direction = 'right' if dx > 0 else 'left'
                else:
                    self.direction = 'down' if dy > 0 else 'up'

    def get_sort_key(self):
        """Return draw-layer sort key using feet position for correct depth ordering.

        Sorts by self.y + height//2 (bottom edge of sprite) so depth ordering
        is consistent with the player, which also sorts by feet.
        """
        return (self.draw_layer, self.y + self.height // 2)

    def distance_to(self, x, y):
        """Return Euclidean distance from this enemy's centre to (*x*, *y*)."""
        dx = self.x - x
        dy = self.y - y
        return (dx * dx + dy * dy) ** 0.5

    def get_collision_rect(self):
        """Get enemy collision rectangle"""
        return pygame.Rect(
            self.x - self.width // 2,
            self.y - self.height // 2,
            self.width,
            self.height
        )

    def check_collision_with_obstacles(self, new_x, new_y):
        """
        Check if a position collides with any obstacles.
        Returns True if collision detected, False otherwise.
        """
        temp_rect = pygame.Rect(
            new_x - self.width // 2,
            new_y - self.height // 2,
            self.width,
            self.height
        )

        for obstacle in self.obstacles:
            # Check if it's a CollisionObject
            if hasattr(obstacle, 'id') and obstacle.id == 'collision_wall':
                if hasattr(obstacle, 'active') and not obstacle.active:
                    continue
                obstacle_rect = pygame.Rect(obstacle.x, obstacle.y, obstacle.width, obstacle.height)
                if temp_rect.colliderect(obstacle_rect):
                    return True

            # Check if it's a DestructibleStone
            elif hasattr(obstacle, 'solid') and hasattr(obstacle, 'active'):
                if not obstacle.active or not obstacle.solid:
                    continue
                stone_rect = pygame.Rect(
                    obstacle.x - obstacle.width // 2,
                    obstacle.y - obstacle.height // 2,
                    obstacle.width,
                    obstacle.height
                )
                if temp_rect.colliderect(stone_rect):
                    return True

            # Fallback for objects with get_collision_rect method
            elif hasattr(obstacle, 'get_collision_rect'):
                if temp_rect.colliderect(obstacle.get_collision_rect()):
                    return True

        return False

    def is_path_blocked_by_enemy(self, target_x, target_y):
        """
        Check if the direct path to target is blocked by another enemy.
        Returns True if blocked, False if clear.
        """
        # Calculate direction to target
        dx = target_x - self.x
        dy = target_y - self.y
        distance_to_target = math.sqrt(dx * dx + dy * dy)

        if distance_to_target < 0.1:
            return False

        # Normalize direction
        dx /= distance_to_target
        dy /= distance_to_target

        # Check if any enemy is in our path
        for other_enemy in self.other_enemies:
            if other_enemy is self or not other_enemy.active:
                continue

            # Vector from us to the other enemy
            to_enemy_x = other_enemy.x - self.x
            to_enemy_y = other_enemy.y - self.y
            dist_to_enemy = math.sqrt(to_enemy_x * to_enemy_x + to_enemy_y * to_enemy_y)

            if dist_to_enemy < 0.1:
                continue

            # Check if enemy is roughly in our path to target
            # Project the vector to the enemy onto our direction vector
            dot_product = (to_enemy_x * dx + to_enemy_y * dy)

            # Enemy must be ahead of us (positive projection)
            if dot_product <= 0:
                continue

            # Enemy must be closer than target
            if dot_product > distance_to_target:
                continue

            # Calculate perpendicular distance from our path to the enemy
            proj_x = dx * dot_product
            proj_y = dy * dot_product
            perp_x = to_enemy_x - proj_x
            perp_y = to_enemy_y - proj_y
            perp_distance = math.sqrt(perp_x * perp_x + perp_y * perp_y)

            # If enemy is close enough to our path, consider it blocking
            blocking_distance = 40
            if perp_distance < blocking_distance:
                return True

        return False

    def calculate_separation_force(self):
        """
        Calculate separation force to avoid overlapping with other enemies.
        Standing enemies (attacking or waiting) should push moving enemies away more strongly.
        """
        separation_x = 0
        separation_y = 0
        count = 0

        for other_enemy in self.other_enemies:
            if other_enemy is self or not other_enemy.active:
                continue

            # Calculate distance to other enemy
            dx = other_enemy.x - self.x
            dy = other_enemy.y - self.y
            distance = math.sqrt(dx * dx + dy * dy)

            if distance < 0.1:
                continue

            # Calculate minimum separation based on direction
            min_distance = self.min_separation_distance

            # Adjust minimum distance based on relative positions
            if abs(dx) > abs(dy):  # More horizontal than vertical
                # For enemies in same row (horizontal alignment), use width
                min_distance = self.min_separation_distance
            else:  # More vertical than horizontal
                # For enemies in same column (vertical alignment), use height/2
                min_distance = self.min_vertical_separation

            # If too close, apply separation force
            if distance < min_distance:
                # Normalize direction away from other enemy
                if distance > 0:
                    dx /= distance
                    dy /= distance

                # Calculate force strength based on how close they are
                force = (min_distance - distance) / min_distance

                # STANDING ENEMY FIX: Increase separation force if other enemy is standing still
                # This makes moving enemies avoid standing ones more strongly
                if other_enemy.is_standing_still():
                    force *= 2.0  # Double the force to push away from standing enemies

                # Apply force away from other enemy
                separation_x -= dx * force
                separation_y -= dy * force
                count += 1

        # Average the separation force
        if count > 0:
            separation_x /= count
            separation_y /= count

            # Normalize and apply strength
            sep_mag = math.sqrt(separation_x * separation_x + separation_y * separation_y)
            if sep_mag > 0:
                separation_x /= sep_mag
                separation_y /= sep_mag

            separation_x *= self.separation_strength
            separation_y *= self.separation_strength

        return separation_x, separation_y

    def find_open_angle_around_player(self, player):
        """
        Find an open angle around the player to approach from.
        Divides the circle around the player into sectors and finds the least crowded one.
        Returns (target_x, target_y) position to move toward.
        """
        num_sectors = 8
        sector_counts = [0] * num_sectors

        # Count how many enemies are in each sector around the player
        for other_enemy in self.other_enemies:
            if other_enemy is self or not other_enemy.active:
                continue

            # Only consider enemies near the player
            dist_to_player = other_enemy.distance_to(player.x, player.y)
            if dist_to_player > 80:  # Only count nearby enemies
                continue

            # Calculate which sector this enemy is in
            angle = math.atan2(other_enemy.y - player.y, other_enemy.x - player.x)
            sector = int((angle + math.pi) / (2 * math.pi / num_sectors)) % num_sectors
            sector_counts[sector] += 1

        # Find the least crowded sector
        min_count = min(sector_counts)
        open_sectors = [i for i, count in enumerate(sector_counts) if count == min_count]

        # Choose the sector closest to our current angle
        current_angle = math.atan2(self.y - player.y, self.x - player.x)
        current_sector = int((current_angle + math.pi) / (2 * math.pi / num_sectors)) % num_sectors

        # Find the closest open sector
        best_sector = min(open_sectors,
                          key=lambda s: min(abs(s - current_sector), num_sectors - abs(s - current_sector)))

        # Calculate target position at this angle around the player
        target_angle = (best_sector * 2 * math.pi / num_sectors) - math.pi
        attack_distance = self.attack_range * 0.8  # Get close enough to attack

        target_x = player.x + math.cos(target_angle) * attack_distance
        target_y = player.y + math.sin(target_angle) * attack_distance

        return target_x, target_y

    def find_clear_path_around_obstacle(self, target_x, target_y, move_speed):
        """
        Find a clear path around obstacles when stuck.
        Samples 8 directions and chooses the clearest path toward target.
        Returns (dx, dy) direction vector, or None if completely blocked.
        """
        num_samples = 8
        best_direction = None
        best_score = -999999

        # Calculate direct angle to target
        target_dx = target_x - self.x
        target_dy = target_y - self.y
        target_angle = math.atan2(target_dy, target_dx)

        for i in range(num_samples):
            # Calculate test angle
            angle = (i * 2 * math.pi / num_samples)

            # Calculate test direction
            test_dx = math.cos(angle)
            test_dy = math.sin(angle)

            # Calculate test position
            test_distance = move_speed * 2
            test_x = self.x + test_dx * test_distance
            test_y = self.y + test_dy * test_distance

            # Check if this direction is clear of obstacles
            if self.check_collision_with_obstacles(test_x, test_y):
                continue

            # Score this direction based on how close it is to target direction
            angle_to_target = math.atan2(test_dy, test_dx)
            angle_diff = abs(angle_to_target - target_angle)

            # Normalize angle difference to [0, pi]
            if angle_diff > math.pi:
                angle_diff = 2 * math.pi - angle_diff

            # Score: prefer directions closer to target
            score = -angle_diff

            # Bonus for making progress toward target
            dot_product = test_dx * target_dx + test_dy * target_dy
            score += dot_product * 0.5

            if score > best_score:
                best_score = score
                best_direction = (test_dx, test_dy)

        return best_direction

    def update_stuck_detection(self, dt):
        """Detect if enemy is stuck and not making progress"""
        movement = math.sqrt((self.x - self.last_x) ** 2 + (self.y - self.last_y) ** 2)

        if movement < self.movement_threshold:
            self.stuck_timer += dt
        else:
            self.stuck_timer = max(0, self.stuck_timer - dt * 2)

        self.last_x = self.x
        self.last_y = self.y

    def update(self, dt, player, world_width, world_height, obstacles=None):
        """Advance the enemy AI state machine by *dt* seconds.

        Handles sprite animation, bomb ticks, cooldown timers, knockback
        physics, attack resolution, and all AI behaviour branches (idle,
        chase, retreat, feint, melee rush).
        """
        if not self.active:
            return

        # Update sprite animation
        if self.has_sprite:
            self.sprite.update(dt)

        # Tick all owned bombs — passes player so detonation always has a reference
        if self.enemy_category == 'shooter' and self.active_bombs:
            for bomb in self.active_bombs:
                bomb.update(dt, player)
            # Purge fully spent bombs (exploded AND explosion animation done)
            self.active_bombs = [
                b for b in self.active_bombs
                if not (b.state == b.STATE_EXPLODED and
                        (b.pending_explosion is None or not b.pending_explosion.active))
            ]

        # Update attack cooldown
        if self.attack_cooldown > 0:
            self.attack_cooldown -= dt

        # Update wait timer
        if self.wait_after_attack > 0:
            self.wait_after_attack -= dt

        # ADVANCED AI: Update retreat cooldown
        if self.retreat_cooldown > 0:
            self.retreat_cooldown -= dt

        # ADVANCED AI: Update feint cooldown
        if self.feint_cooldown > 0:
            self.feint_cooldown -= dt

        # ADVANCED AI: Update shooter melee rush cooldown
        if self.enemy_category == 'shooter' and self.melee_rush_cooldown > 0:
            self.melee_rush_cooldown -= dt

        # Fade out hurt tint each frame
        if self.hurt_tint > 0:
            self.hurt_tint = max(0.0, self.hurt_tint - dt / self.hurt_tint_duration)

        # Update stuck detection
        self.update_stuck_detection(dt)

        # PATHFINDING FIX: Update pathfinding commitment timer
        if self.pathfind_commit_timer > 0:
            self.pathfind_commit_timer -= dt
            if self.pathfind_commit_timer <= 0:
                self.pathfind_direction = None

        # Handle knockback
        if self.is_knocked_back:
            self.stuck_timer = 0
            # Clear pathfinding when knocked back
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0

            # Calculate new position
            new_x = self.x + self.knockback_velocity_x * dt
            new_y = self.y + self.knockback_velocity_y * dt

            # Check for collisions and only move if no collision
            if not self.check_collision_with_obstacles(new_x, self.y):
                self.x = new_x
            else:
                self.knockback_velocity_x = 0

            if not self.check_collision_with_obstacles(self.x, new_y):
                self.y = new_y
            else:
                self.knockback_velocity_y = 0

            # Clamp to room bounds
            self.x = max(0, min(self.x, world_width))
            self.y = max(0, min(self.y, world_height))

            self.knockback_velocity_x *= 0.9
            self.knockback_velocity_y *= 0.9

            if self.has_sprite and self.sprite.is_animation_finished():
                self.is_knocked_back = False
                self.knockback_velocity_x = 0
                self.knockback_velocity_y = 0
                self.sprite.set_animation('idle', self.direction)
            elif not self.has_sprite:
                if abs(self.knockback_velocity_x) < 1 and abs(self.knockback_velocity_y) < 1:
                    self.is_knocked_back = False
                    self.knockback_velocity_x = 0
                    self.knockback_velocity_y = 0
            return

        # Handle attacking
        if self.is_attacking:
            self.attack_timer -= dt
            if self.attack_timer <= 0:
                self.is_attacking = False
                self.wait_after_attack = self.wait_after_attack_duration
                # End melee rush after the swing completes
                if getattr(self, 'is_shooter_melee_attack', False):
                    self.is_shooter_melee_attack = False
                    self.is_doing_melee_rush = False
                    self.melee_rush_timer = 0
                    self.melee_rush_swung = False
                    self.melee_rush_cooldown = self.melee_rush_cooldown_time
                if self.has_sprite:
                    self.sprite.set_animation('idle', self.direction)
            else:
                # Call perform_attack at the END of the animation (last 0.1 seconds)
                # This is when the bomb should be thrown
                if self.attack_timer <= 0.4:
                    self.perform_attack(player)
            return

        player_distance = self.distance_to(player.x, player.y)

        # State management
        if self.state == 'idle':
            if player_distance < self.awareness_range:
                self.state = 'chase'
                self.stuck_timer = 0
                # Clear pathfinding when entering chase
                self.pathfind_direction = None
                self.pathfind_commit_timer = 0
        elif self.state == 'chase':
            if player_distance > self.forget_range:
                self.state = 'idle'
                self.idle_timer = 0
                self.stuck_timer = 0
                # Clear pathfinding when leaving chase
                self.pathfind_direction = None
                self.pathfind_commit_timer = 0
                if self.has_sprite:
                    self.sprite.set_animation('idle', self.direction)

        # ADVANCED AI: Check for retreat conditions (only for advanced AI)
        if self.ai_type == 'advanced' and not self.is_attacking and self.state == 'chase':
            current_time = time.time()
            health_percent = self.hp / self.max_hp

            # Only check for retreat periodically to prevent spam
            time_since_last_check = current_time - self.last_retreat_attempt
            can_check_retreat = time_since_last_check >= self.retreat_check_cooldown

            # Check if we're off cooldown from a previous retreat
            can_retreat = self.retreat_cooldown <= 0

            # Trigger retreat if low health OR too many consecutive hits
            should_retreat = (
                    (health_percent < self.low_health_threshold) or
                    (self.consecutive_hits >= self.hit_combo_threshold)
            )

            # Attempt retreat with random chance when conditions are met
            if should_retreat and can_check_retreat and can_retreat:
                self.last_retreat_attempt = current_time
                # Randomize the next check interval to stagger retreats among enemies
                self.retreat_check_cooldown = random.uniform(1.5, 3.0)

                # Roll for retreat chance
                if random.random() < self.retreat_chance:
                    # Start retreat if not already retreating/breathing
                    if not self.is_retreating and not self.is_breathing:
                        self.is_retreating = True
                        self.retreat_timer = self.retreat_duration
                        # Clear pathfinding when starting retreat
                        self.pathfind_direction = None
                        self.pathfind_commit_timer = 0

        # ADVANCED AI: Check for shooter melee rush (only advanced AI shooters, not already rushing)
        if self.ai_type == 'advanced' and self.enemy_category == 'shooter' and not self.is_attacking and self.state == 'chase':
            if not self.is_doing_melee_rush and self.melee_rush_cooldown <= 0 and self.wait_after_attack <= 0:
                current_time = time.time()
                if current_time - self.last_melee_rush_attempt >= self.melee_rush_check_interval:
                    self.last_melee_rush_attempt = current_time
                    self.melee_rush_check_interval = random.uniform(4.0, 7.0)
                    if random.random() < self.melee_rush_chance:
                        self.is_doing_melee_rush = True
                        self.melee_rush_timer = 0
                        self.melee_rush_swung = False
                        self.pathfind_direction = None
                        self.pathfind_commit_timer = 0

        # ADVANCED AI: Check for feinting conditions (only for advanced AI and MELEE enemies)
        # ONLY feint right after attacking during the wait_after_attack period
        if self.ai_type == 'advanced' and self.enemy_category == 'melee' and not self.is_attacking and self.state == 'chase':
            # Only check if we're in the wait period after an attack
            if self.wait_after_attack > 0:
                # Don't feint if already retreating, breathing, feinting, or pausing after feint
                if not self.is_retreating and not self.is_breathing and not self.is_feinting and not self.is_pausing_after_feint:
                    current_time = time.time()

                    # Check periodically (short interval since we're already in wait period)
                    time_since_last_check = current_time - self.last_feint_attempt
                    can_check_feint = time_since_last_check >= self.feint_check_cooldown

                    # Check if off cooldown
                    can_feint = self.feint_cooldown <= 0

                    # Attempt feint after attack
                    if can_check_feint and can_feint:
                        self.last_feint_attempt = current_time

                        # Roll for feint chance
                        if random.random() < self.feint_chance:
                            # Start feinting
                            self.is_feinting = True
                            self.feint_timer = random.uniform(0.6, 1.2)
                            # Clear wait_after_attack to start feinting immediately
                            self.wait_after_attack = 0
                            # Clear pathfinding when starting feint
                            self.pathfind_direction = None
                            self.pathfind_commit_timer = 0

        # Behavior based on state
        if not self.is_attacking:
            # ADVANCED AI: Handle retreat/breather behavior
            if self.is_retreating or self.is_breathing:
                self.retreat_behavior(dt, player, world_width, world_height)
            # ADVANCED AI: Handle shooter melee rush (shooter enemies only)
            elif self.enemy_category == 'shooter' and getattr(self, 'is_doing_melee_rush', False):
                self.shooter_melee_rush_behavior(dt, player, world_width, world_height)
            # ADVANCED AI: Handle feinting behavior
            elif self.is_feinting:
                self.feint_behavior(dt, player, world_width, world_height)
            # ADVANCED AI: Handle pause after feint
            elif self.is_pausing_after_feint:
                self.pause_after_feint_behavior(dt, player)
            elif self.state == 'idle':
                self.idle_behavior(dt, world_width, world_height)
            elif self.state == 'chase':
                self.chase_and_attack(dt, player, world_width, world_height)

    def apply_separation_force(self, dt):
        """
        Apply separation force to avoid overlapping with other enemies.
        STANDING ENEMY FIX: Enemies that are standing still (attacking or waiting)
        do NOT move due to separation force. Only moving enemies get pushed away.
        """
        # Don't apply separation if we're standing still (attacking or waiting after attack)
        # or if we're knocked back
        if self.is_knocked_back or self.is_standing_still():
            return

        sep_x, sep_y = self.calculate_separation_force()

        if abs(sep_x) > 0.1 or abs(sep_y) > 0.1:
            # Apply separation movement
            move_speed = self.speed * 30 * dt

            # Calculate new position with separation
            new_x = self.x + sep_x * move_speed
            new_y = self.y + sep_y * move_speed

            # Only move if not colliding with obstacles
            if not self.check_collision_with_obstacles(new_x, self.y):
                self.x = new_x

            if not self.check_collision_with_obstacles(self.x, new_y):
                self.y = new_y

    def shooter_melee_rush_behavior(self, dt, player, world_width, world_height):
        """
        ADVANCED AI: Shooter closes in on the player to land a melee hit.
        Charges like a melee enemy; once in range, swings with the 'melee' animation.
        After the hit completes the rush ends and the shooter backs off to normal range.
        """
        # Advance the rush timeout - abort if it takes too long
        self.melee_rush_timer += dt
        if self.melee_rush_timer >= self.melee_rush_max_duration:
            self.is_doing_melee_rush = False
            self.melee_rush_timer = 0
            self.melee_rush_swung = False
            self.melee_rush_cooldown = self.melee_rush_cooldown_time
            return

        distance = self.distance_to(player.x, player.y)

        # If close enough and haven't swung yet, trigger the melee attack.
        # Intentionally bypasses ranged attack_cooldown - the rush has its own cooldown.
        if distance < self.shooter_melee_range and not self.melee_rush_swung:
            self.melee_rush_swung = True
            self.is_shooter_melee_attack = True
            self.is_attacking = True
            self.attack_timer = 0.4          # Melee swing duration

            # Face the player
            dx = player.x - self.x
            dy = player.y - self.y
            self.set_direction_from_movement(dx, dy)

            if self.has_sprite:
                self.sprite.set_animation('melee', self.direction)
            return

        # Not yet in range - charge toward the player
        dx = player.x - self.x
        dy = player.y - self.y
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > 0.1:
            dx /= dist
            dy /= dist

        self.set_direction_from_movement(dx, dy)
        if self.has_sprite:
            self.sprite.set_animation('walk', self.direction)

        move_speed = self.speed * 70 * dt   # Slightly faster than normal chase

        # Pathfinding around obstacles
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

        final_x = self.x + dx * move_speed
        final_y = self.y + dy * move_speed

        if not self.check_collision_with_obstacles(final_x, self.y):
            if 0 < final_x < world_width:
                self.x = final_x
        if not self.check_collision_with_obstacles(self.x, final_y):
            if 0 < final_y < world_height:
                self.y = final_y

    def chase_and_attack(self, dt, player, world_width, world_height):
        """
        Chase player and attack. If direct path is blocked by another enemy,
        find an open angle around the player to attack from instead.
        SHOOTERS maintain their preferred distance instead of closing in.
        """
        distance = self.distance_to(player.x, player.y)

        # If waiting after attack, just face the player and don't move
        if self.wait_after_attack > 0:
            if self.has_sprite:
                self.sprite.set_animation('idle', self.direction)

            dx = player.x - self.x
            dy = player.y - self.y
            # Use the stable direction setting here too
            self.set_direction_from_movement(dx, dy)
            return

        # Attack if in range
        if distance < self.attack_range and self.attack_cooldown <= 0:
            # SHOOTER: Attack when in a wide range
            if self.enemy_category == 'shooter':
                min_attack_distance = 50  # Very permissive minimum

                # GUNNER / ROCKETLAUNCHER: Only fire when player is cardinally aligned
                if self.shooter_style in ('bullet', 'rocket'):
                    raw_dx = player.x - self.x
                    raw_dy = player.y - self.y
                    alignment_tolerance = 20  # World units of leeway
                    cardinally_aligned = abs(raw_dx) < alignment_tolerance or abs(raw_dy) < alignment_tolerance
                    if not cardinally_aligned:
                        # Not aligned - fall through to movement so gunner strafes into alignment
                        pass
                    elif distance >= min_attack_distance:
                        # Snap direction BEFORE calling try_attack so the animation faces correctly
                        if abs(raw_dx) >= abs(raw_dy):
                            self.direction = 'right' if raw_dx > 0 else 'left'
                        else:
                            self.direction = 'down' if raw_dy > 0 else 'up'
                        self.try_attack(player)
                        return
                elif distance >= min_attack_distance:
                    self.try_attack(player)
                    return
            else:
                # MELEE: Attack whenever in range
                self.try_attack(player)
                return
        elif self.enemy_category == 'shooter':
            pass  # Out of range or on cooldown — fall through to movement

        # SHOOTER BEHAVIOR: Maintain preferred distance
        if self.enemy_category == 'shooter':
            # Too close - advanced AI retaliates with a melee rush; easy AI backs away
            if distance < self.preferred_distance * 0.9:
                if (self.ai_type == 'advanced'
                        and not self.is_doing_melee_rush
                        and self.melee_rush_cooldown <= 0
                        and not self.is_attacking):
                    # Player invaded personal space - charge them
                    self.is_doing_melee_rush = True
                    self.melee_rush_timer = 0
                    self.melee_rush_swung = False
                    self.pathfind_direction = None
                    self.pathfind_commit_timer = 0
                    return

                # Calculate direction AWAY from player
                dx = self.x - player.x
                dy = self.y - player.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0.1:
                    dx /= dist
                    dy /= dist

                # Set walking animation
                if self.has_sprite:
                    self.sprite.set_animation('walk', self.direction)

                # Update facing direction toward player (walk backward)
                face_dx = player.x - self.x
                face_dy = player.y - self.y
                self.set_direction_from_movement(face_dx, face_dy)

                # Move away
                move_speed = self.speed * 50 * dt

            # At good distance AND within attack range
            elif distance <= self.preferred_distance * 1.3 and distance < self.attack_range:  # Wider zone for stability
                # GUNNER: if not aligned, move toward the nearest alignment point
                if self.shooter_style in ('bullet', 'rocket'):
                    raw_dx = player.x - self.x
                    raw_dy = player.y - self.y
                    alignment_tolerance = 20
                    cardinally_aligned = abs(raw_dx) < alignment_tolerance or abs(raw_dy) < alignment_tolerance
                    if not cardinally_aligned:
                        # Pick the alignment target that requires the least movement:
                        # either slide to player.x (same column) or slide to player.y (same row)
                        if abs(raw_dx) < abs(raw_dy):
                            # Moving horizontally to share the player's column is shorter
                            target_x = player.x
                            target_y = self.y
                        else:
                            # Moving vertically to share the player's row is shorter
                            target_x = self.x
                            target_y = player.y
                        move_dx = target_x - self.x
                        move_dy = target_y - self.y
                        move_dist = math.sqrt(move_dx * move_dx + move_dy * move_dy)
                        if move_dist > 0.1:
                            move_dx /= move_dist
                            move_dy /= move_dist
                        self.set_direction_from_movement(move_dx, move_dy)  # Face the way we're actually walking
                        if self.has_sprite:
                            self.sprite.set_animation('walk', self.direction)
                        move_speed = self.speed * 60 * dt
                        new_x = self.x + move_dx * move_speed
                        new_y = self.y + move_dy * move_speed
                        if not self.check_collision_with_obstacles(new_x, self.y):
                            self.x = new_x
                        if not self.check_collision_with_obstacles(self.x, new_y):
                            self.y = new_y
                        return

                # Aligned (or non-gunner shooter) - stand still and face player
                if self.has_sprite:
                    self.sprite.set_animation('idle', self.direction)

                dx = player.x - self.x
                dy = player.y - self.y
                self.set_direction_from_movement(dx, dy)
                return

            # Too far - move closer
            else:
                dx = player.x - self.x
                dy = player.y - self.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0.1:
                    dx /= dist
                    dy /= dist

                # Set walking animation
                if self.has_sprite:
                    self.sprite.set_animation('walk', self.direction)

                # Update facing direction
                self.set_direction_from_movement(dx, dy)

                # Move closer
                move_speed = self.speed * 60 * dt
        else:
            # MELEE BEHAVIOR: Get as close as possible
            # Determine target position
            target_x = player.x
            target_y = player.y

            # If direct path is blocked by another enemy, find an open angle
            if self.is_path_blocked_by_enemy(player.x, player.y):
                target_x, target_y = self.find_open_angle_around_player(player)

            # Set walking animation
            if self.has_sprite:
                self.sprite.set_animation('walk', self.direction)

            # Calculate approach vector toward target
            dx = target_x - self.x
            dy = target_y - self.y
            approach_dist = math.sqrt(dx * dx + dy * dy)

            if approach_dist > 0.1:
                dx /= approach_dist
                dy /= approach_dist
            else:
                # We're at the target position, just face the player
                dx = player.x - self.x
                dy = player.y - self.y
                dist_to_player = math.sqrt(dx * dx + dy * dy)
                if dist_to_player > 0.1:
                    dx /= dist_to_player
                    dy /= dist_to_player

            # Calculate movement - reduce speed when very close to maintain spacing
            move_speed = self.speed * 60 * dt
            if distance < self.min_separation_distance * 2:
                # Slow down when close to maintain better formation
                move_speed *= 0.7

        # First, check if we need pathfinding around obstacles
        new_x = self.x + dx * move_speed
        new_y = self.y + dy * move_speed
        is_blocked = self.check_collision_with_obstacles(new_x, new_y)

        # PATHFINDING FIX: Use committed direction if still valid, or find new one if needed
        if self.pathfind_commit_timer > 0 and self.pathfind_direction is not None:
            # We're committed to a pathfinding direction, use it
            dx, dy = self.pathfind_direction
        elif is_blocked or self.stuck_timer > 0.3:
            # Need to find a new path around obstacle
            target_x = player.x if self.enemy_category == 'melee' else self.x + dx * self.preferred_distance
            target_y = player.y if self.enemy_category == 'melee' else self.y + dy * self.preferred_distance
            clear_path = self.find_clear_path_around_obstacle(target_x, target_y, move_speed)

            if clear_path is not None:
                # Found a clear path, commit to it for a duration
                self.pathfind_direction = clear_path
                self.pathfind_commit_timer = self.pathfind_commit_duration
                dx, dy = clear_path
        else:
            # Not blocked and not stuck - clear any previous pathfinding
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0

        # Update facing direction with stability (for melee, already done for shooters)
        if self.enemy_category == 'melee':
            self.set_direction_from_movement(dx, dy)

        # NOW calculate final movement with separation force
        # Start with base chase/pathfinding movement
        final_dx = dx * move_speed
        final_dy = dy * move_speed

        # Add separation force to prevent overlapping
        sep_x, sep_y = self.calculate_separation_force()
        separation_strength = move_speed * 0.6  # Strong separation to maintain spacing
        final_dx += sep_x * separation_strength
        final_dy += sep_y * separation_strength

        # Apply final movement with collision checking (only check obstacles, not other enemies)
        test_x = self.x + final_dx
        if not self.check_collision_with_obstacles(test_x, self.y):
            if 0 < test_x < world_width:
                self.x = test_x

        test_y = self.y + final_dy
        if not self.check_collision_with_obstacles(self.x, test_y):
            if 0 < test_y < world_height:
                self.y = test_y

    def retreat_behavior(self, dt, player, world_width, world_height):
        """
        ADVANCED AI: Retreat from player to gain distance and take a breather.
        Used when low on health or after taking many consecutive hits.
        """
        # Handle breather state (resting after reaching safe distance)
        if self.is_breathing:
            self.breather_timer -= dt

            # Just stand and face the player during breather
            if self.has_sprite:
                self.sprite.set_animation('idle', self.direction)

            # Update direction to face player
            dx = player.x - self.x
            dy = player.y - self.y
            self.set_direction_from_movement(dx, dy)

            # End breather when timer expires
            if self.breather_timer <= 0:
                self.is_breathing = False
                self.is_retreating = False
                self.consecutive_hits = 0  # Reset combo counter after recovery

                # Return to chase state and start retreat cooldown with randomized time
                self.state = 'chase'
                self.retreat_cooldown = random.uniform(3.0, 5.0)  # Randomize cooldown

                # Clear pathfinding for fresh start
                self.pathfind_direction = None
                self.pathfind_commit_timer = 0
            return

        # Handle retreat movement
        self.retreat_timer -= dt

        # Calculate direction AWAY from player
        dx = self.x - player.x
        dy = self.y - player.y
        dist_to_player = math.sqrt(dx * dx + dy * dy)

        # Normalize direction
        if dist_to_player > 0.1:
            dx /= dist_to_player
            dy /= dist_to_player

        # Set retreat animation
        if self.has_sprite:
            self.sprite.set_animation('walk', self.direction)

        # Update facing direction (still face away from player while retreating)
        self.set_direction_from_movement(dx, dy)

        # Move away from player
        move_speed = self.speed * 70 * dt  # Slightly faster when retreating

        # Calculate movement with pathfinding around obstacles
        new_x = self.x + dx * move_speed
        new_y = self.y + dy * move_speed
        is_blocked = self.check_collision_with_obstacles(new_x, new_y)

        # Use pathfinding if blocked
        if self.pathfind_commit_timer > 0 and self.pathfind_direction is not None:
            dx, dy = self.pathfind_direction
        elif is_blocked or self.stuck_timer > 0.3:
            # Calculate a retreat target position
            retreat_target_x = self.x + dx * self.retreat_distance
            retreat_target_y = self.y + dy * self.retreat_distance

            clear_path = self.find_clear_path_around_obstacle(retreat_target_x, retreat_target_y, move_speed)
            if clear_path is not None:
                self.pathfind_direction = clear_path
                self.pathfind_commit_timer = self.pathfind_commit_duration
                dx, dy = clear_path

        # Apply movement with collision checking
        final_x = self.x + dx * move_speed
        final_y = self.y + dy * move_speed

        if not self.check_collision_with_obstacles(final_x, self.y):
            if 0 < final_x < world_width:
                self.x = final_x

        if not self.check_collision_with_obstacles(self.x, final_y):
            if 0 < final_y < world_height:
                self.y = final_y

        # Check if we've retreated far enough or timer expired
        if dist_to_player >= self.retreat_distance or self.retreat_timer <= 0:
            # Start breather phase
            self.is_breathing = True
            self.breather_timer = self.breather_duration
            return

    def feint_behavior(self, dt, player, world_width, world_height):
        """
        ADVANCED AI: Feint behavior - back away from player while keeping eyes on them.
        Creates tactical spacing after attacking.
        """
        # Update timer
        self.feint_timer -= dt

        # Calculate distance to player
        distance = self.distance_to(player.x, player.y)

        # ALWAYS calculate direction TO player for facing
        face_dx = player.x - self.x
        face_dy = player.y - self.y
        face_dist = math.sqrt(face_dx * face_dx + face_dy * face_dy)
        if face_dist > 0.1:
            face_dx /= face_dist
            face_dy /= face_dist

        # ALWAYS move AWAY from player during feint
        move_dx = self.x - player.x
        move_dy = self.y - player.y
        move_dist = math.sqrt(move_dx * move_dx + move_dy * move_dy)

        # Normalize movement direction
        if move_dist > 0.1:
            move_dx /= move_dist
            move_dy /= move_dist

        # Update facing direction - ALWAYS face toward player during feints
        self.set_direction_from_movement(face_dx, face_dy)

        # Set walking animation
        if self.has_sprite:
            self.sprite.set_animation('walk', self.direction)

        # Move with medium speed (backing away)
        move_speed = self.speed * 55 * dt

        # Calculate movement with pathfinding around obstacles
        new_x = self.x + move_dx * move_speed
        new_y = self.y + move_dy * move_speed
        is_blocked = self.check_collision_with_obstacles(new_x, new_y)

        # Use pathfinding if blocked
        if self.pathfind_commit_timer > 0 and self.pathfind_direction is not None:
            move_dx, move_dy = self.pathfind_direction
        elif is_blocked or self.stuck_timer > 0.3:
            # Calculate a target position for pathfinding
            target_x = self.x + move_dx * self.feint_distance
            target_y = self.y + move_dy * self.feint_distance

            clear_path = self.find_clear_path_around_obstacle(target_x, target_y, move_speed)
            if clear_path is not None:
                self.pathfind_direction = clear_path
                self.pathfind_commit_timer = self.pathfind_commit_duration
                move_dx, move_dy = clear_path

        # Apply movement with collision checking
        final_x = self.x + move_dx * move_speed
        final_y = self.y + move_dy * move_speed

        if not self.check_collision_with_obstacles(final_x, self.y):
            if 0 < final_x < world_width:
                self.x = final_x

        if not self.check_collision_with_obstacles(self.x, final_y):
            if 0 < final_y < world_height:
                self.y = final_y

        # End feinting when timer expires
        if self.feint_timer <= 0:
            self.is_feinting = False
            # Start feint cooldown with randomized time
            self.feint_cooldown = random.uniform(5.0, 7.0)

            # Sometimes pause after feinting, sometimes return to chase immediately
            if random.random() < self.pause_after_feint_chance:
                # Start pause
                self.is_pausing_after_feint = True
                self.pause_after_feint_timer = random.uniform(0.4, 0.8)
            else:
                # Return to chase state immediately
                self.state = 'chase'

            # Clear pathfinding for fresh start
            self.pathfind_direction = None
            self.pathfind_commit_timer = 0
            return

    def pause_after_feint_behavior(self, dt, player):
        """
        ADVANCED AI: Pause after feinting - stand still and watch player briefly.
        Creates a moment of tension before re-engaging.
        """
        # Update timer
        self.pause_after_feint_timer -= dt

        # Just stand and face the player during pause
        if self.has_sprite:
            self.sprite.set_animation('idle', self.direction)

        # Update direction to face player
        dx = player.x - self.x
        dy = player.y - self.y
        self.set_direction_from_movement(dx, dy)

        # End pause when timer expires
        if self.pause_after_feint_timer <= 0:
            self.is_pausing_after_feint = False
            # Return to chase state
            self.state = 'chase'
            return

    def idle_behavior(self, dt, world_width, world_height):
        """Idle behavior: random wandering or return to spawn if too far"""
        distance_from_spawn = self.distance_to_spawn(self.x, self.y)

        # If too far from spawn, return to spawn point
        if distance_from_spawn > self.max_idle_distance:
            dx = self.spawn_x - self.x
            dy = self.spawn_y - self.y
            dist = math.sqrt(dx * dx + dy * dy)

            if dist > 0:
                dx /= dist
                dy /= dist

                move_speed = self.speed * 60 * dt
                new_x = self.x + dx * move_speed
                new_y = self.y + dy * move_speed

                # OBSTACLE AVOIDANCE
                is_blocked = self.check_collision_with_obstacles(new_x, new_y)

                if is_blocked or self.stuck_timer > 0.3:
                    clear_path = self.find_clear_path_around_obstacle(self.spawn_x, self.spawn_y, move_speed)

                    if clear_path is not None:
                        dx, dy = clear_path
                        new_x = self.x + dx * move_speed
                        new_y = self.y + dy * move_speed

                # Update facing direction
                self.set_direction_from_movement(dx, dy)

                # Set walking animation
                if self.has_sprite:
                    self.sprite.set_animation('walk', self.direction)

                # Move with collision checking
                if not self.check_collision_with_obstacles(new_x, self.y):
                    if 0 < new_x < world_width:
                        self.x = new_x

                if not self.check_collision_with_obstacles(self.x, new_y):
                    if 0 < new_y < world_height:
                        self.y = new_y

            return

        # Random wandering when not returning to spawn
        if not self.is_idle_moving:
            self.idle_timer += dt
            if self.idle_timer >= self.idle_wait_time:
                self.idle_timer = 0
                self.is_idle_moving = True
                self.idle_move_timer = 0

                # Choose random direction
                angle = random.uniform(0, 2 * math.pi)
                self.idle_direction = (math.cos(angle), math.sin(angle))

                # Calculate new target position
                move_distance = random.uniform(20, 60)
                self.target_x = self.x + self.idle_direction[0] * move_distance
                self.target_y = self.y + self.idle_direction[1] * move_distance

                # Clamp to spawn radius
                dx = self.target_x - self.spawn_x
                dy = self.target_y - self.spawn_y
                dist_from_spawn = math.sqrt(dx * dx + dy * dy)

                if dist_from_spawn > self.max_idle_distance:
                    dx /= dist_from_spawn
                    dy /= dist_from_spawn
                    self.target_x = self.spawn_x + dx * self.max_idle_distance
                    self.target_y = self.spawn_y + dy * self.max_idle_distance

                # Update facing direction
                self.set_direction_from_movement(self.idle_direction[0], self.idle_direction[1])

                # Set walking animation
                if self.has_sprite:
                    self.sprite.set_animation('walk', self.direction)
        else:
            self.idle_move_timer += dt

            # Check if reached target or timed out
            dist_to_target = math.sqrt((self.x - self.target_x) ** 2 + (self.y - self.target_y) ** 2)

            if dist_to_target < 5 or self.idle_move_timer >= self.idle_move_duration:
                self.is_idle_moving = False
                self.idle_timer = 0
                if self.has_sprite:
                    self.sprite.set_animation('idle', self.direction)
            else:
                # Move toward target
                move_speed = self.speed * 20 * dt
                new_x = self.x + self.idle_direction[0] * move_speed
                new_y = self.y + self.idle_direction[1] * move_speed

                if not self.check_collision_with_obstacles(new_x, self.y):
                    if 0 < new_x < world_width:
                        self.x = new_x

                if not self.check_collision_with_obstacles(self.x, new_y):
                    if 0 < new_y < world_height:
                        self.y = new_y

    def distance_to_spawn(self, x, y):
        dx = x - self.spawn_x
        dy = y - self.spawn_y
        return (dx * dx + dy * dy) ** 0.5

    def try_attack(self, player):
        """Attempt to start an attack animation.

        Returns True if the attack was started, False if blocked by cooldown,
        knockback, or being out of range.
        """
        if self.is_attacking or self.is_knocked_back or self.attack_cooldown > 0:
            return False

        distance = self.distance_to(player.x, player.y)

        if distance < self.attack_range:
            self.is_attacking = True
            self.attack_timer = self.attack_duration
            self.attack_cooldown = self.attack_cooldown_time

            # Reset spawn flags for new attack (shooters only)
            if self.enemy_category == 'shooter':
                self.bomb_spawned_this_attack = False
                self.bullet_spawned_this_attack = False
                self.rocket_spawned_this_attack = False

            if self.has_sprite:
                # Melee enemies use 'melee' animation; shooters use 'attack' for ranged
                animation_name = 'melee' if self.enemy_category == 'melee' else 'attack'
                self.sprite.set_animation(animation_name, self.direction)

            return True

        return False

    def perform_attack(self, player):
        if not self.is_attacking:
            return

        distance = self.distance_to(player.x, player.y)

        if self.enemy_category == 'shooter':
            # ADVANCED AI: Shooter performing a melee hit instead of ranged
            if getattr(self, 'is_shooter_melee_attack', False):
                if distance < self.shooter_melee_range:
                    if self.direction == 'up':
                        knockback_x, knockback_y = 0.0, -1.0
                    elif self.direction == 'down':
                        knockback_x, knockback_y = 0.0, 1.0
                    elif self.direction == 'left':
                        knockback_x, knockback_y = -1.0, 0.0
                    else:
                        knockback_x, knockback_y = 1.0, 0.0
                    player.take_damage(self.shooter_melee_damage, knockback_x, knockback_y)
                    player.hurt_tint = 1.0
                return

            if self.shooter_style == 'bullet':
                # GUNNER: Fire a straight bullet (only once per attack)
                if not self.bullet_spawned_this_attack:
                    raw_dx = player.x - self.x
                    raw_dy = player.y - self.y
                    # Snap to cardinal axis (whichever offset is larger wins)
                    if abs(raw_dx) >= abs(raw_dy):
                        dx = 1.0 if raw_dx > 0 else -1.0
                        dy = 0.0
                        self.direction = 'right' if raw_dx > 0 else 'left'
                    else:
                        dx = 0.0
                        dy = 1.0 if raw_dy > 0 else -1.0
                        self.direction = 'down' if raw_dy > 0 else 'up'
                    self.should_spawn_bullet = True
                    self.bullet_dx = dx
                    self.bullet_dy = dy
                    self.bullet_spawned_this_attack = True
            elif self.shooter_style == 'rocket':
                # ROCKET LAUNCHER: Fire a rocket (cardinal aligned, only once per attack)
                if not self.rocket_spawned_this_attack:
                    raw_dx = player.x - self.x
                    raw_dy = player.y - self.y
                    if abs(raw_dx) >= abs(raw_dy):
                        dx = 1.0 if raw_dx > 0 else -1.0
                        dy = 0.0
                        self.direction = 'right' if raw_dx > 0 else 'left'
                    else:
                        dx = 0.0
                        dy = 1.0 if raw_dy > 0 else -1.0
                        self.direction = 'down' if raw_dy > 0 else 'up'
                    self.should_spawn_rocket = True
                    self.rocket_dx = dx
                    self.rocket_dy = dy
                    self.rocket_spawned_this_attack = True
            else:
                # BOMB THROWER: Lob a bomb toward player's position (only once per attack)
                if not self.bomb_spawned_this_attack:
                    self.should_spawn_bomb = True
                    self.bomb_target_x = player.x
                    self.bomb_target_y = player.y
                    self._pending_bomb_player = player   # saved so spawn data can carry it
                    self.bomb_spawned_this_attack = True
        else:
            # MELEE: Deal damage if in range
            if distance < self.attack_range:
                # KNOCKBACK BASED ON FACING DIRECTION
                if self.direction == 'up':
                    knockback_x = 0.0
                    knockback_y = -1.0
                elif self.direction == 'down':
                    knockback_x = 0.0
                    knockback_y = 1.0
                elif self.direction == 'left':
                    knockback_x = -1.0
                    knockback_y = 0.0
                elif self.direction == 'right':
                    knockback_x = 1.0
                    knockback_y = 0.0
                else:
                    knockback_x = 0.0
                    knockback_y = 1.0

                player.take_damage(self.attack_damage, knockback_x, knockback_y)
                player.hurt_tint = 1.0

    def get_bomb_spawn_data(self):
        """
        Get data needed to spawn a bomb projectile for shooter enemies.
        Returns a dictionary with bomb parameters, or None if not ready to spawn.
        Called by the game system when should_spawn_bomb flag is True.
        """
        if not self.should_spawn_bomb:
            return None

        # Clear the flag so we don't spawn multiple bombs
        self.should_spawn_bomb = False

        # Return bomb spawn data
        return {
            'start_x': self.x,
            'start_y': self.y,
            'target_x': self.bomb_target_x,
            'target_y': self.bomb_target_y,
            'damage': self.attack_damage,
            'flight_time': 1.0,  # Bomb takes 1 second to reach target
            'player': self._pending_bomb_player,  # needed so bomb can deal damage on detonation
            'owner': self
        }

    def register_bomb(self, bomb):
        """
        Call this immediately after creating a BombProjectile from get_bomb_spawn_data().
        The enemy owns the bomb so it can update it with the player each frame and expose
        it to the y-sorted draw list via get_bomb_drawables().

        Example in your game loop:
            data = enemy.get_bomb_spawn_data()
            bomb = BombProjectile(data['start_x'], data['start_y'],
                                  data['target_x'], data['target_y'],
                                  data['damage'], data['flight_time'],
                                  player=data['player'])
            enemy.register_bomb(bomb)
            # Do NOT add it to a separate bomb list — enemy manages it from here.
        """
        self.active_bombs.append(bomb)

    def get_bomb_drawables(self):
        """
        Returns all drawable bomb/explosion objects owned by this enemy.
        Add these to your y-sorted draw list each frame so they depth-sort
        correctly with the player and other entities.

        Example in your game loop (inside draw preparation):
            for enemy in enemies:
                drawables.extend(enemy.get_bomb_drawables())
        """
        result = []
        for bomb in self.active_bombs:
            # The bomb itself while flying/landed
            if bomb.state != bomb.STATE_EXPLODED:
                result.append(bomb)
            # The explosion effect while it's still playing
            if bomb.pending_explosion is not None and bomb.pending_explosion.active:
                result.append(bomb.pending_explosion)
        return result

    def get_bullet_spawn_data(self):
        """
        Get data needed to spawn a straight bullet for gunner enemies.
        Returns a dictionary with bullet parameters, or None if not ready to spawn.
        Called by the game system when should_spawn_bullet flag is True.
        """
        if not self.should_spawn_bullet:
            return None

        # Clear the flag so we don't spawn multiple bullets
        self.should_spawn_bullet = False

        return {
            'x': self.x,
            'y': self.y,
            'dx': self.bullet_dx,
            'dy': self.bullet_dy,
            'speed': self.projectile_speed,
            'damage': self.attack_damage,
            'direction': self.direction,
            'owner': self
        }

    def get_rocket_spawn_data(self):
        """
        Get data needed to spawn a rocket for rocketlauncher enemies.
        Returns a dictionary with rocket parameters, or None if not ready.
        """
        if not self.should_spawn_rocket:
            return None
        self.should_spawn_rocket = False
        return {
            'x': self.x,
            'y': self.y,
            'dx': self.rocket_dx,
            'dy': self.rocket_dy,
            'speed': self.projectile_speed,
            'damage': self.attack_damage,
            'direction': self.direction,
            'owner': self
        }

    def get_projectile_spawn_data(self, player):
        """
        Get data needed to spawn a projectile for shooter enemies.
        Returns a dictionary with projectile parameters, or None if not a shooter or not attacking.
        Called by the game system during the attack animation.
        """
        if self.enemy_category != 'shooter' or not self.is_attacking:
            return None

        # Calculate direction toward player
        dx = player.x - self.x
        dy = player.y - self.y
        dist = math.sqrt(dx * dx + dy * dy)

        if dist > 0.1:
            dx /= dist
            dy /= dist

        # Return projectile spawn data
        return {
            'x': self.x,
            'y': self.y,
            'dx': dx,
            'dy': dy,
            'speed': self.projectile_speed,
            'damage': self.attack_damage,
            'direction': self.direction,
            'owner': self
        }

    def apply_knockback(self, dx, dy, force=200):
        """Apply knockback that lasts for the duration of the hurt animation"""
        self.is_knocked_back = True
        self.knockback_velocity_x = dx * force
        self.knockback_velocity_y = dy * force
        self.hurt_tint = 1.0

        if self.has_sprite:
            self.sprite.set_animation('hurt', self.direction)

    def take_damage(self, damage):
        self.hp -= damage
        self.hurt_tint = 1.0

        # ADVANCED AI: Track consecutive hits for combo detection
        if self.ai_type == 'advanced':
            current_time = time.time()

            # Check if this hit is part of a combo (within the combo window)
            if current_time - self.last_hit_time < self.hit_combo_window:
                self.consecutive_hits += 1
            else:
                # Too much time passed, reset combo
                self.consecutive_hits = 1

            self.last_hit_time = current_time

        if self.hp <= 0:
            self.hp = 0
            self.active = False
            if self.has_sprite:
                self.sprite.set_animation('death', self.direction)

    def get_xp_reward(self, game_config):
        return game_config.basic_enemy_xp

    def check_collision_with_attack(self, attack, attack_type):
        if not self.active or self.is_knocked_back:
            return False

        if attack_type == 'melee':
            offset = 25
            melee_x = attack.x
            melee_y = attack.y

            if attack.direction == 'up':
                melee_y -= offset + attack.size // 2
            elif attack.direction == 'down':
                melee_y += offset + attack.size // 2
            elif attack.direction == 'left':
                melee_x -= offset + attack.size // 2
            elif attack.direction == 'right':
                melee_x += offset + attack.size // 2

            attack_rect = pygame.Rect(melee_x - attack.size // 2, melee_y - attack.size // 2,
                                      attack.size, attack.size)
            enemy_rect = pygame.Rect(self.x - self.width // 2, self.y - self.height // 2,
                                     self.width, self.height)
            if attack_rect.colliderect(enemy_rect):
                self.take_damage(15)

                dx = self.x - attack.x
                dy = self.y - attack.y
                dist = (dx * dx + dy * dy) ** 0.5
                if dist > 0:
                    dx /= dist
                    dy /= dist
                self.apply_knockback(dx, dy, 150)
                return True

        elif attack_type == 'projectile':
            projectile_radius = attack.radius
            projectile_rect = pygame.Rect(
                attack.x - projectile_radius,
                attack.y - projectile_radius,
                projectile_radius * 2,
                projectile_radius * 2
            )

            enemy_rect = pygame.Rect(
                self.x - self.width // 2,
                self.y - self.height // 2,
                self.width,
                self.height
            )

            if projectile_rect.colliderect(enemy_rect):
                dx = self.x - attack.x
                dy = self.y - attack.y
                dist = (dx * dx + dy * dy) ** 0.5
                if dist > 0:
                    dx /= dist
                    dy /= dist

                self.take_damage(20)
                self.apply_knockback(dx, dy, 250)
                return True

        elif attack_type == 'beam':
            if attack.length > 0:
                if attack.direction == 'up':
                    beam_rect = pygame.Rect(attack.x - attack.width // 2,
                                            attack.y - attack.length,
                                            attack.width, attack.length)
                elif attack.direction == 'down':
                    beam_rect = pygame.Rect(attack.x - attack.width // 2,
                                            attack.y,
                                            attack.width, attack.length)
                elif attack.direction == 'left':
                    beam_rect = pygame.Rect(attack.x - attack.length,
                                            attack.y - attack.width // 2,
                                            attack.length, attack.width)
                elif attack.direction == 'right':
                    beam_rect = pygame.Rect(attack.x,
                                            attack.y - attack.width // 2,
                                            attack.length, attack.width)

                enemy_rect = pygame.Rect(self.x - self.width // 2, self.y - self.height // 2,
                                         self.width, self.height)
                if beam_rect.colliderect(enemy_rect):
                    self.take_damage(5)
                    return True

        return False

    def draw(self, screen, camera, colors):
        if not self.active:
            return

        if self.has_sprite:
            self.sprite.draw(screen, self.x, self.y, camera, RENDER_SCALE, self.hurt_tint)
        else:
            screen_x = (self.x * RENDER_SCALE) - camera.x
            screen_y = (self.y * RENDER_SCALE) - camera.y

            if self.is_attacking:
                color = (255, 0, 255)
            elif self.is_knocked_back:
                color = (100, 100, 100)
            elif self.state == 'chase':
                if self.stuck_timer > 1.0:
                    color = YELLOW
                else:
                    color = RED
            else:
                color = ORANGE

            scaled_width = self.width * RENDER_SCALE
            scaled_height = self.height * RENDER_SCALE

            pygame.draw.rect(screen, color,
                             (screen_x - scaled_width // 2, screen_y - scaled_height // 2,
                              scaled_width, scaled_height))

        # HP bar
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        bar_width = 32 * RENDER_SCALE
        bar_height = 4 * RENDER_SCALE
        scaled_height = self.height * RENDER_SCALE
        bar_x = screen_x - bar_width // 2
        bar_y = screen_y - scaled_height // 2 - (10 * RENDER_SCALE)

        pygame.draw.rect(screen, BLACK, (bar_x, bar_y, bar_width, bar_height))
        hp_width = int((self.hp / self.max_hp) * bar_width)
        pygame.draw.rect(screen, GREEN, (bar_x, bar_y, hp_width, bar_height))
        pygame.draw.rect(screen, WHITE, (bar_x, bar_y, bar_width, bar_height), 1)