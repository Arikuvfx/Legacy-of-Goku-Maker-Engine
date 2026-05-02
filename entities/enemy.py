import pygame
import random
import math
import time
from config.settings import WORLD_WIDTH, WORLD_HEIGHT, RED, ORANGE, BLACK, GREEN, WHITE, YELLOW, RENDER_SCALE
from core.draw_layers import DrawLayer
from core.sprite_system import create_enemy_sprite


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
}

# Cardinal knockback vectors keyed by facing direction
_KNOCKBACK_VECTORS = {
    'up':    (0.0, -1.0),
    'down':  (0.0,  1.0),
    'left':  (-1.0, 0.0),
    'right': (1.0,  0.0),
}


class Enemy:
    def __init__(self, x, y, enemy_type='tiger_bandit', variant='default',
                 ai_type='easy', enemy_category='melee', shooter_style='bomb'):
        """Create an enemy at world position (*x*, *y*).

        Args:
            x, y:            Starting world coordinates.
            enemy_type:      Sprite folder key (e.g. 'tiger_bandit').
            variant:         Colour/skin variant used by the sprite loader.
            ai_type:         'easy' for basic movement, 'advanced' for retreat/feint/rush.
            enemy_category:  'melee' for close-range or 'shooter' for ranged attacks.
            shooter_style:   'bomb', 'bullet', or 'rocket' — only used for 'shooter' category.
        """
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.shadow_size = 'small'  # 'small' or 'big' — override per enemy subclass if needed
        self.speed = 1
        self.hp = 150
        self.max_hp = 150
        self.active = True

        self.enemy_type = enemy_type
        self.variant = variant
        self.ai_type = ai_type          # 'easy' = basic movement, 'advanced' = retreats/feints/etc.
        self.enemy_category = enemy_category

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
        else:
            # Melee defaults
            self.attack_duration = 0.4
            self.attack_cooldown_time = 1.1
            self.attack_range = 15       # Very close range
            self.preferred_distance = 0  # Get as close as possible
            self.attack_damage = 10

        # Brief pause after completing an attack before moving again
        self.wait_after_attack = 0
        self.wait_after_attack_duration = 0.4

        # Knockback — driven by the hurt animation length
        self.is_knocked_back = False
        self.knockback_velocity_x = 0
        self.knockback_velocity_y = 0

        # Hurt tint — red flash that fades after taking damage
        self.hurt_tint = 0.0            # 1.0 = full red, 0.0 = no tint
        self.hurt_tint_duration = 0.45  # Seconds to fully fade back to normal

        # Tracks the most recent damage value applied so game.py can spawn a
        # damage number popup without needing to change take_damage's return type
        self.last_damage_dealt = 0

        self.draw_layer = DrawLayer.ENEMIES
        self.y_sort = True              # Participates in depth-sorted rendering

        # Injected by the room/game system after construction
        self.obstacles = []      # For collision checking during movement and knockback
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

        # Death animation — plays the brown_destruction spritesheet before
        # self.active is set to False (which signals game.py to award XP / remove)
        self.is_dying = False
        self.death_frames = []
        self.death_frame_index = 0
        self.death_frame_duration = 0.08   # seconds per frame
        self.death_frame_timer = 0.0
        self._load_death_animation()

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

    def is_standing_still(self):
        """True while the enemy is locked in an animation and not moving."""
        return (self.is_attacking
                or self.wait_after_attack > 0
                or self.is_breathing
                or self.is_feinting
                or self.is_pausing_after_feint)

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

        if not self.check_collision_with_obstacles(new_x, self.y) and 0 < new_x < world_width:
            self.x = new_x
        if not self.check_collision_with_obstacles(self.x, new_y) and 0 < new_y < world_height:
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

    def check_collision_with_obstacles(self, new_x, new_y):
        """Return True if the given position overlaps any active obstacle."""
        temp_rect = pygame.Rect(
            new_x - self.width // 2,
            new_y - self.height // 2,
            self.width,
            self.height,
        )

        for obstacle in self.obstacles:
            # Collision wall (e.g. invisible wall object with an 'id' attribute)
            if hasattr(obstacle, 'id') and obstacle.id == 'collision_wall':
                if hasattr(obstacle, 'active') and not obstacle.active:
                    continue
                obstacle_rect = pygame.Rect(obstacle.x, obstacle.y, obstacle.width, obstacle.height)
                if temp_rect.colliderect(obstacle_rect):
                    return True

            # DestructibleStone or similar — must be both active and solid
            elif hasattr(obstacle, 'solid') and hasattr(obstacle, 'active'):
                if not obstacle.active or not obstacle.solid:
                    continue
                stone_rect = pygame.Rect(
                    obstacle.x - obstacle.width // 2,
                    obstacle.y - obstacle.height // 2,
                    obstacle.width,
                    obstacle.height,
                )
                if temp_rect.colliderect(stone_rect):
                    return True

            # Generic fallback for anything with a get_collision_rect() method
            elif hasattr(obstacle, 'get_collision_rect'):
                if temp_rect.colliderect(obstacle.get_collision_rect()):
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

    def update(self, dt, player, world_width, world_height, obstacles=None):
        """Advance the enemy AI state machine by *dt* seconds.

        Handles sprite animation, bomb ticks, cooldown timers, knockback
        physics, attack resolution, and all AI behaviour branches (idle,
        chase, retreat, feint, melee rush).
        """
        if not self.active:
            return

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
        if self.enemy_category == 'shooter' and self.melee_rush_cooldown > 0:
            self.melee_rush_cooldown -= dt

        # Fade out the hurt tint each frame
        if self.hurt_tint > 0:
            self.hurt_tint = max(0.0, self.hurt_tint - dt / self.hurt_tint_duration)

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
            self.x = max(0, min(self.x, world_width))
            self.y = max(0, min(self.y, world_height))

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
                    self.perform_attack(player)
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

                if self.shooter_style in ('bullet', 'rocket'):
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
                if self.shooter_style in ('bullet', 'rocket'):
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
        test_x = self.x + final_dx
        if not self.check_collision_with_obstacles(test_x, self.y) and 0 < test_x < world_width:
            self.x = test_x

        test_y = self.y + final_dy
        if not self.check_collision_with_obstacles(self.x, test_y) and 0 < test_y < world_height:
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

        if self.distance_to(player.x, player.y) < self.attack_range:
            self.is_attacking = True
            self.attack_timer = self.attack_duration
            self.attack_cooldown = self.attack_cooldown_time

            # Reset per-attack spawn flags so each attack spawns exactly one projectile
            if self.enemy_category == 'shooter':
                self.bomb_spawned_this_attack = False
                self.bullet_spawned_this_attack = False
                self.rocket_spawned_this_attack = False

            if self.has_sprite:
                anim = 'melee' if self.enemy_category == 'melee' else 'attack'
                self.sprite.set_animation(anim, self.direction)

            return True

        return False

    def perform_attack(self, player):
        """Apply hit effects or set projectile spawn flags during the attack window.

        Called at attack_timer <= 0.4 s (the last part of the animation) so the
        impact lands at the visual peak of the swing.
        """
        if not self.is_attacking:
            return

        distance = self.distance_to(player.x, player.y)

        if self.enemy_category == 'shooter':
            # Shooter landed a melee rush — use melee damage
            if getattr(self, 'is_shooter_melee_attack', False):
                if distance < self.shooter_melee_range:
                    kx, ky = self._direction_to_vector()
                    player.take_damage(self.shooter_melee_damage, kx, ky)
                    player.hurt_tint = 1.0
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
            if distance < self.attack_range:
                kx, ky = self._direction_to_vector()
                player.take_damage(self.attack_damage, kx, ky)
                player.hurt_tint = 1.0

    # =========================================================================
    # Damage / knockback
    # =========================================================================

    def apply_knockback(self, dx, dy, force=200):
        """Launch the enemy in direction (dx, dy) with the given force and play the hurt animation."""
        self.is_knocked_back = True
        self.knockback_velocity_x = dx * force
        self.knockback_velocity_y = dy * force
        self.hurt_tint = 1.0

        if self.has_sprite:
            self.sprite.set_animation('hurt', self.direction)

    def _end_knockback(self):
        """Clear all knockback state and return to idle animation."""
        self.is_knocked_back = False
        self.knockback_velocity_x = 0
        self.knockback_velocity_y = 0
        if self.has_sprite:
            self.sprite.set_animation('idle', self.direction)

    def take_damage(self, damage):
        """Reduce HP and track hit combos for the advanced AI retreat decision."""
        self.last_damage_dealt = damage  # Stored so game.py can spawn a popup
        self.hp -= damage
        self.hurt_tint = 1.0

        if self.ai_type == 'advanced':
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

    def get_xp_reward(self, game_config):
        """Return XP granted to the player on kill."""
        return game_config.basic_enemy_xp

    # =========================================================================
    # Collision with player attacks
    # =========================================================================

    def check_collision_with_attack(self, attack, attack_type):
        """Test whether a player attack hits this enemy and apply damage/knockback.

        Returns True if a hit was registered.
        """
        if not self.active or self.is_knocked_back:
            return False

        if attack_type == 'melee':
            # Offset the hit box in the direction of the swing
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

            attack_rect = pygame.Rect(
                melee_x - attack.size // 2,
                melee_y - attack.size // 2,
                attack.size, attack.size,
            )
            if attack_rect.colliderect(self.get_collision_rect()):
                self.take_damage(15)

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

                self.take_damage(20)
                self.apply_knockback(dx, dy, 250)
                return True

        elif attack_type == 'beam':
            if attack.length > 0:
                # Build the beam rect based on firing direction
                if attack.direction == 'up':
                    beam_rect = pygame.Rect(attack.x - attack.width // 2, attack.y - attack.length,
                                            attack.width, attack.length)
                elif attack.direction == 'down':
                    beam_rect = pygame.Rect(attack.x - attack.width // 2, attack.y,
                                            attack.width, attack.length)
                elif attack.direction == 'left':
                    beam_rect = pygame.Rect(attack.x - attack.length, attack.y - attack.width // 2,
                                            attack.length, attack.width)
                elif attack.direction == 'right':
                    beam_rect = pygame.Rect(attack.x, attack.y - attack.width // 2,
                                            attack.length, attack.width)

                if beam_rect.colliderect(self.get_collision_rect()):
                    self.take_damage(5)  # Beam ticks low damage continuously
                    return True

        return False

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

        if self.has_sprite:
            self.sprite.draw(screen, self.x, self.y, camera, RENDER_SCALE, self.hurt_tint)
        else:
            # Debug placeholder — color encodes AI/combat state at a glance
            screen_x = (self.x * RENDER_SCALE) - camera.x
            screen_y = (self.y * RENDER_SCALE) - camera.y

            if self.is_attacking:
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