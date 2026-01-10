import pygame
import time
from config.settings import WORLD_WIDTH, WORLD_HEIGHT, WHITE, GRAY, PURPLE, BLUE, RED, YELLOW, BLACK
from attacks import Projectile, BeamAttack, MeleeAttack

class Player:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.speed = 3
        self.run_speed = 6
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
        
        # Player Stats
        self.stats = {
            'strength': 1,
            'ki_power': 1,
            'vitality': 1,
            'energy': 1,
            'speed': 1,
            'defense': 1
        }
        
        # Ki attack modes
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
        
    def move(self, dx, dy, is_running, world_width, world_height):
        if self.is_attacking or self.is_charging_beam or self.is_firing_beam or self.is_knocked_back:
            return
        
        current_speed = self.run_speed if is_running else self.speed
        self.x += dx * current_speed
        self.y += dy * current_speed
        
        # Keep player within world bounds
        self.x = max(self.width // 2, min(self.x, world_width - self.width // 2))
        self.y = max(self.height // 2, min(self.y, world_height - self.height // 2))
        
        # Update direction
        if dx > 0:
            self.direction = 'right'
        elif dx < 0:
            self.direction = 'left'
        elif dy > 0:
            self.direction = 'down'
        elif dy < 0:
            self.direction = 'up'
    
    def shoot_blast(self):
        if self.ki >= self.blast_ki_cost and not self.is_attacking and self.attack_cooldown <= 0:
            self.ki -= self.blast_ki_cost
            self.is_attacking = True
            self.attack_timer = 0.3
            self.attack_cooldown = 0.5
            
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
            
            return Projectile(spawn_x, spawn_y, self.direction)
        return None
    
    def start_charging_beam(self):
        if self.ki > 0 and not self.is_attacking and not self.is_charging_beam and not self.is_firing_beam:
            self.is_charging_beam = True
            self.beam_charge_time = 0
    
    def update_beam_charge(self, dt):
        if self.is_charging_beam:
            self.beam_charge_time += dt
    
    def fire_beam(self):
        if self.is_charging_beam and self.beam_charge_time >= self.beam_charge_required:
            self.is_charging_beam = False
            self.is_firing_beam = True
            self.beam_charge_time = 0
            
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
            
            return BeamAttack(spawn_x, spawn_y, self.direction)
        return None
    
    def stop_beam(self):
        self.is_charging_beam = False
        self.is_firing_beam = False
        self.beam_charge_time = 0
        self.current_beam = None
    
    def melee_attack(self):
        if not self.is_attacking and self.attack_cooldown <= 0:
            self.is_attacking = True
            self.attack_timer = self.melee_duration
            self.attack_cooldown = 0.4
            return MeleeAttack(self.x, self.y, self.direction)
        return None
    
    def update(self, dt):
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
        
        if self.is_attacking:
            self.attack_timer -= dt
            if self.attack_timer <= 0:
                self.is_attacking = False
        
        if self.attack_cooldown > 0:
            self.attack_cooldown -= dt
        
        # Drain ki while firing beam
        if self.is_firing_beam:
            ki_drain = self.beam_ki_drain * dt
            self.ki -= ki_drain
            if self.ki <= 0:
                self.ki = 0
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
        
        self.hp -= damage
        if self.hp < 0:
            self.hp = 0
        
        self.is_knocked_back = True
        self.knockback_timer = self.knockback_duration
        self.knockback_velocity_x = knockback_x * 300
        self.knockback_velocity_y = knockback_y * 300
        
        self.invulnerable = True
        self.invulnerable_timer = self.invulnerable_duration
        
        self.is_charging_beam = False
        self.is_firing_beam = False
        if self.current_beam:
            self.current_beam = None
            
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
        
        base_speed = 3
        base_run = 6
        speed_multiplier = 1 + (self.stats['speed'] - 1) * 0.05
        self.speed = base_speed * speed_multiplier
        self.run_speed = base_run * speed_multiplier
    
    def draw(self, screen, camera, colors):
        screen_x = self.x - camera.x
        screen_y = self.y - camera.y
        
        # Character body
        if self.invulnerable and int(self.invulnerable_timer * 10) % 2 == 0:
            body_color = colors['WHITE']
        elif self.is_knocked_back:
            body_color = colors['GRAY']
        elif self.is_charging_beam or self.is_firing_beam:
            body_color = colors['PURPLE']
        else:
            body_color = colors['BLUE']
            
        pygame.draw.rect(screen, body_color, 
                        (screen_x - self.width // 2, screen_y - self.height // 2, 
                         self.width, self.height))
        
        # Direction indicator
        indicator_color = colors['RED'] if (self.is_attacking or self.is_charging_beam or self.is_firing_beam) else colors['YELLOW']
        if self.direction == 'up':
            pygame.draw.circle(screen, indicator_color, (int(screen_x), int(screen_y - self.height // 2 + 5)), 4)
        elif self.direction == 'down':
            pygame.draw.circle(screen, indicator_color, (int(screen_x), int(screen_y + self.height // 2 - 5)), 4)
        elif self.direction == 'left':
            pygame.draw.circle(screen, indicator_color, (int(screen_x - self.width // 2 + 5), int(screen_y)), 4)
        elif self.direction == 'right':
            pygame.draw.circle(screen, indicator_color, (int(screen_x + self.width // 2 - 5), int(screen_y)), 4)
        
        # Running indicator
        if self.is_running:
            pygame.draw.circle(screen, colors['WHITE'], (int(screen_x), int(screen_y - self.height // 2 - 10)), 3)
        
        # Charging indicator
        if self.is_charging_beam:
            charge_progress = min(self.beam_charge_time / self.beam_charge_required, 1.0)
            bar_width = 40
            bar_height = 5
            bar_x = screen_x - bar_width // 2
            bar_y = screen_y - self.height // 2 - 20
            
            pygame.draw.rect(screen, colors['BLACK'], (bar_x, bar_y, bar_width, bar_height))
            pygame.draw.rect(screen, colors['YELLOW'], (bar_x, bar_y, int(bar_width * charge_progress), bar_height))
            pygame.draw.rect(screen, colors['WHITE'], (bar_x, bar_y, bar_width, bar_height), 1)
