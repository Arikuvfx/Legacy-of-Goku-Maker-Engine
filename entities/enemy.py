import pygame
import random
from config.settings import WORLD_WIDTH, WORLD_HEIGHT, RED, ORANGE, BLACK, GREEN, WHITE, YELLOW

class Enemy:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.speed = 2
        self.hp = 50
        self.max_hp = 50
        self.active = True
        
        # AI States
        self.state = 'idle'
        self.awareness_range = 200
        self.forget_range = 350
        
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
        
        # Combat system
        self.is_attacking = False
        self.attack_timer = 0
        self.attack_duration = 0.4
        self.attack_cooldown = 0
        self.attack_cooldown_time = 1.5
        self.attack_range = 45
        self.attack_damage = 10
        
        # Knockback
        self.is_knocked_back = False
        self.knockback_timer = 0
        self.knockback_duration = 0.3
        self.knockback_velocity_x = 0
        self.knockback_velocity_y = 0
        
    def distance_to(self, x, y):
        dx = self.x - x
        dy = self.y - y
        return (dx * dx + dy * dy) ** 0.5
    
    def update(self, dt, player, world_width, world_height, obstacles=None):
        if not self.active:
            return
        
        # Update attack cooldown
        if self.attack_cooldown > 0:
            self.attack_cooldown -= dt
        
        # Handle knockback
        if self.is_knocked_back:
            self.knockback_timer -= dt
            
            # Apply knockback movement
            self.x += self.knockback_velocity_x * dt
            self.y += self.knockback_velocity_y * dt
            
            # Keep in bounds
            self.x = max(0, min(self.x, world_width))
            self.y = max(0, min(self.y, world_height))
            
            # Reduce velocity
            self.knockback_velocity_x *= 0.9
            self.knockback_velocity_y *= 0.9
            
            if self.knockback_timer <= 0:
                self.is_knocked_back = False
                self.knockback_velocity_x = 0
                self.knockback_velocity_y = 0
            return
        
        # Handle attacking
        if self.is_attacking:
            self.attack_timer -= dt
            if self.attack_timer <= 0:
                self.is_attacking = False
            else:
                # Deal damage at the start of attack
                if self.attack_timer > (self.attack_duration - 0.1):
                    self.perform_attack(player)
            return
        
        player_distance = self.distance_to(player.x, player.y)
        
        # State management
        if self.state == 'idle':
            if player_distance < self.awareness_range:
                self.state = 'chase'
        elif self.state == 'chase':
            if player_distance > self.forget_range:
                self.state = 'idle'
                self.idle_timer = 0
        
        # Try to attack if close enough
        if self.state == 'chase':
            self.try_attack(player)
        
        # Behavior based on state (only if not attacking)
        if not self.is_attacking:
            if self.state == 'idle':
                self.idle_behavior(dt, world_width, world_height)
            elif self.state == 'chase':
                self.chase_behavior(dt, player, obstacles)
    
    def idle_behavior(self, dt, world_width, world_height):
        if self.is_idle_moving:
            # Continue current movement
            self.idle_move_timer -= dt
            
            self.x += self.move_velocity_x * dt
            self.y += self.move_velocity_y * dt
            
            # Keep in world bounds
            self.x = max(0, min(self.x, world_width))
            self.y = max(0, min(self.y, world_height))
            
            if self.idle_move_timer <= 0:
                self.is_idle_moving = False
                self.move_velocity_x = 0
                self.move_velocity_y = 0
                self.idle_timer = self.idle_wait_time
        else:
            # Wait before next movement
            self.idle_timer -= dt
            
            if self.idle_timer <= 0:
                # Choose random direction
                directions = ['up', 'down', 'left', 'right']
                
                # Try to find a valid direction
                valid_directions = []
                for direction in directions:
                    move_distance = self.speed * self.idle_move_duration * 60
                    test_x = self.x
                    test_y = self.y
                    
                    if direction == 'up':
                        test_y -= move_distance
                    elif direction == 'down':
                        test_y += move_distance
                    elif direction == 'left':
                        test_x -= move_distance
                    elif direction == 'right':
                        test_x += move_distance
                    
                    # Check if still within idle range and world bounds
                    if (self.distance_to_spawn(test_x, test_y) < self.max_idle_distance and
                        test_x > 0 and test_x < world_width and
                        test_y > 0 and test_y < world_height):
                        valid_directions.append(direction)
                
                if valid_directions:
                    import random
                    self.idle_direction = random.choice(valid_directions)
                    
                    # Set velocity for smooth movement
                    if self.idle_direction == 'up':
                        self.move_velocity_x = 0
                        self.move_velocity_y = -self.speed * 25
                    elif self.idle_direction == 'down':
                        self.move_velocity_x = 0
                        self.move_velocity_y = self.speed * 25
                    elif self.idle_direction == 'left':
                        self.move_velocity_x = -self.speed * 25
                        self.move_velocity_y = 0
                    elif self.idle_direction == 'right':
                        self.move_velocity_x = self.speed * 25
                        self.move_velocity_y = 0
                    
                    self.is_idle_moving = True
                    self.idle_move_timer = self.idle_move_duration
                else:
                    # No valid direction, just wait
                    self.idle_timer = self.idle_wait_time
    
    def distance_to_spawn(self, x, y):
        dx = x - self.spawn_x
        dy = y - self.spawn_y
        return (dx * dx + dy * dy) ** 0.5
    
    def chase_behavior(self, dt, player, obstacles):
        dx = player.x - self.x
        dy = player.y - self.y
        distance = (dx * dx + dy * dy) ** 0.5
        
        if distance > 40:
            if distance > 0:
                dx /= distance
                dy /= distance
            
            new_x = self.x + dx * self.speed
            new_y = self.y + dy * self.speed
            
            if new_x > 0 and new_x < WORLD_WIDTH:
                self.x = new_x
            if new_y > 0 and new_y < WORLD_HEIGHT:
                self.y = new_y
    
    def try_attack(self, player):
        if self.is_attacking or self.is_knocked_back or self.attack_cooldown > 0:
            return False
        
        distance = self.distance_to(player.x, player.y)
        if distance < self.attack_range:
            self.is_attacking = True
            self.attack_timer = self.attack_duration
            self.attack_cooldown = self.attack_cooldown_time
            return True
        return False
    
    def perform_attack(self, player):
        if not self.is_attacking:
            return
        
        distance = self.distance_to(player.x, player.y)
        if distance < self.attack_range:
            dx = player.x - self.x
            dy = player.y - self.y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > 0:
                dx /= dist
                dy /= dist
            
            player.take_damage(self.attack_damage, dx, dy)
    
    def apply_knockback(self, dx, dy, force=200):
        self.is_knocked_back = True
        self.knockback_timer = self.knockback_duration
        self.knockback_velocity_x = dx * force
        self.knockback_velocity_y = dy * force
    
    def take_damage(self, damage):
        self.hp -= damage
        if self.hp <= 0:
            self.hp = 0
            self.active = False
            
    def get_xp_reward(self, game_config):
        return game_config.basic_enemy_xp
    
    def check_collision_with_attack(self, attack, attack_type):
        if not self.active or self.is_knocked_back:
            return False
    
        if attack_type == 'melee':
            offset = 35
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
            enemy_rect = pygame.Rect(self.x - self.width//2, self.y - self.height//2, 
                                 self.width, self.height)
            if attack_rect.colliderect(enemy_rect):
                self.take_damage(15)
            
                dx = self.x - melee_x
                dy = self.y - melee_y
                dist = (dx * dx + dy * dy) ** 0.5
                if dist > 0:
                    dx /= dist
                    dy /= dist
                self.apply_knockback(dx, dy, 150)
                return True
        
        elif attack_type == 'beam':
            if attack.length > 0:
                if attack.direction == 'up':
                    beam_rect = pygame.Rect(attack.x - attack.width//2, 
                                           attack.y - attack.length,
                                           attack.width, attack.length)
                elif attack.direction == 'down':
                    beam_rect = pygame.Rect(attack.x - attack.width//2, 
                                           attack.y,
                                           attack.width, attack.length)
                elif attack.direction == 'left':
                    beam_rect = pygame.Rect(attack.x - attack.length, 
                                           attack.y - attack.width//2,
                                           attack.length, attack.width)
                elif attack.direction == 'right':
                    beam_rect = pygame.Rect(attack.x, 
                                           attack.y - attack.width//2,
                                           attack.length, attack.width)
                
                enemy_rect = pygame.Rect(self.x - self.width//2, self.y - self.height//2,
                                        self.width, self.height)
                if beam_rect.colliderect(enemy_rect):
                    self.take_damage(5)
                    return True
        
        return False
    
    def draw(self, screen, camera):
        if not self.active:
            return
        
        screen_x = self.x - camera.x
        screen_y = self.y - camera.y
        
        if self.is_attacking:
            color = (255, 0, 255)
        elif self.is_knocked_back:
            color = (100, 100, 100)
        elif self.state == 'chase':
            color = RED
        else:
            color = ORANGE
            
        pygame.draw.rect(screen, color, 
                        (screen_x - self.width//2, screen_y - self.height//2, 
                         self.width, self.height))
        
        if self.is_attacking:
            pygame.draw.circle(screen, RED, (int(screen_x), int(screen_y)), 
                             int(self.attack_range), 2)
        
        # HP bar
        bar_width = 32
        bar_height = 4
        bar_x = screen_x - bar_width // 2
        bar_y = screen_y - self.height // 2 - 10
        
        pygame.draw.rect(screen, BLACK, (bar_x, bar_y, bar_width, bar_height))
        hp_width = int((self.hp / self.max_hp) * bar_width)
        pygame.draw.rect(screen, GREEN, (bar_x, bar_y, hp_width, bar_height))
        pygame.draw.rect(screen, WHITE, (bar_x, bar_y, bar_width, bar_height), 1)
        
        if self.state == 'chase' and not self.is_attacking:
            pygame.draw.circle(screen, YELLOW, (int(screen_x), int(screen_y)), 5)
