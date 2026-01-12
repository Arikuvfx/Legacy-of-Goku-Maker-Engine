import pygame
import random
from core.draw_layers import DrawLayer

class NPC:
    def __init__(self, x, y, dialogue_config=None):
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.speed = 1.5
        self.active = True
        self.npc_type = 'static'  # 'static' or 'moving'
        
        # Movement for moving NPCs
        self.idle_timer = 0
        self.idle_wait_time = 2.0  # seconds between moves
        self.idle_move_timer = 0
        self.idle_move_duration = 1.5  # how long to move
        self.idle_direction = None
        self.spawn_x = x
        self.spawn_y = y
        self.max_idle_distance = 120
        self.is_moving = False
        self.move_velocity_x = 0
        self.move_velocity_y = 0
        
        # Dialogue system
        self.dialogue_config = dialogue_config or {
            'dialogues': ["Hello, traveler!"],
            'trigger_limit': -1,  # -1 = unlimited
            'triggers_used': 0,
            'after_limit_text': "I have nothing more to say.",
            'random_order': False,
            'give_item': None,
            'item_given': False
        }
        self.current_dialogue_index = 0
        self.is_talking = False
        self.facing_direction = 'down'
        
        # Interaction
        self.interaction_range = 50

        self.draw_layer = DrawLayer.NPCS
        self.y_sort = True  # NPCs use Y-sorting for depth

    def get_sort_key(self):
        return (self.draw_layer, self.y)
        
    def distance_to(self, x, y):
        dx = self.x - x
        dy = self.y - y
        return (dx * dx + dy * dy) ** 0.5
    
    def update(self, dt, player, world_width, world_height):
        if not self.active:
            return
        
        # Check if player is in interaction range
        player_distance = self.distance_to(player.x, player.y)
        
        # If talking, face the player and don't move
        if self.is_talking:
            dx = player.x - self.x
            dy = player.y - self.y
            
            if abs(dx) > abs(dy):
                self.facing_direction = 'right' if dx > 0 else 'left'
            else:
                self.facing_direction = 'down' if dy > 0 else 'up'
            return
        
        # Moving NPC behavior
        if self.npc_type == 'moving':
            self.idle_behavior(dt, world_width, world_height)
    
    def idle_behavior(self, dt, world_width, world_height):
        if self.is_moving:
            # Continue current movement
            self.idle_move_timer -= dt
            
            self.x += self.move_velocity_x * dt
            self.y += self.move_velocity_y * dt
            
            # Keep in world bounds
            self.x = max(0, min(self.x, world_width))
            self.y = max(0, min(self.y, world_height))
            
            # Update facing direction based on movement
            if abs(self.move_velocity_x) > abs(self.move_velocity_y):
                self.facing_direction = 'right' if self.move_velocity_x > 0 else 'left'
            else:
                self.facing_direction = 'down' if self.move_velocity_y > 0 else 'up'
            
            if self.idle_move_timer <= 0:
                self.is_moving = False
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
                    self.idle_direction = random.choice(valid_directions)
                    
                    # Set velocity for smooth movement
                    if self.idle_direction == 'up':
                        self.move_velocity_x = 0
                        self.move_velocity_y = -self.speed * 40
                    elif self.idle_direction == 'down':
                        self.move_velocity_x = 0
                        self.move_velocity_y = self.speed * 40
                    elif self.idle_direction == 'left':
                        self.move_velocity_x = -self.speed * 40
                        self.move_velocity_y = 0
                    elif self.idle_direction == 'right':
                        self.move_velocity_x = self.speed * 40
                        self.move_velocity_y = 0
                    
                    self.is_moving = True
                    self.idle_move_timer = self.idle_move_duration
                else:
                    # No valid direction, just wait
                    self.idle_timer = self.idle_wait_time
    
    def distance_to_spawn(self, x, y):
        dx = x - self.spawn_x
        dy = y - self.spawn_y
        return (dx * dx + dy * dy) ** 0.5
    
    def can_interact(self, player):
        distance = self.distance_to(player.x, player.y)
        return distance < self.interaction_range
    
    def start_dialogue(self):
        """Start or continue dialogue"""
        config = self.dialogue_config
        
        # Check if trigger limit reached
        if config['trigger_limit'] != -1 and config['triggers_used'] >= config['trigger_limit']:
            return config['after_limit_text'], True, None  # text, is_final, item
        
        # First interaction
        if self.current_dialogue_index == 0 and not self.is_talking:
            self.is_talking = True
            config['triggers_used'] += 1
        
        # Get dialogues
        dialogues = config['dialogues']
        
        # Random order
        if config['random_order'] and self.current_dialogue_index == 0:
            dialogues = dialogues.copy()
            random.shuffle(dialogues)
            config['_shuffled_dialogues'] = dialogues
        elif config['random_order']:
            dialogues = config.get('_shuffled_dialogues', dialogues)
        
        # Get current dialogue
        if self.current_dialogue_index < len(dialogues):
            text = dialogues[self.current_dialogue_index]
            self.current_dialogue_index += 1
            
            # Check if this is the last dialogue
            if self.current_dialogue_index >= len(dialogues):
                is_final = True
                item = config['give_item'] if not config['item_given'] else None
                if item:
                    config['item_given'] = True
            else:
                is_final = False
                item = None
            
            return text, is_final, item
        
        return None, True, None
    
    def end_dialogue(self):
        """Reset dialogue state"""
        self.is_talking = False
        self.current_dialogue_index = 0
        if '_shuffled_dialogues' in self.dialogue_config:
            del self.dialogue_config['_shuffled_dialogues']
    
    def draw(self, screen, camera, colors):
        if not self.active:
            return
        
        screen_x = self.x - camera.x
        screen_y = self.y - camera.y
        
        # NPC body - different color than player/enemy
        npc_color = (100, 200, 100) if self.npc_type == 'moving' else (50, 150, 200)
        if self.is_talking:
            npc_color = (200, 200, 50)  # Yellow when talking
        
        pygame.draw.rect(screen, npc_color, 
                        (screen_x - self.width//2, screen_y - self.height//2, 
                         self.width, self.height))
        
        # Draw facing indicator
        indicator_color = colors['WHITE']
        offset = 5
        if self.facing_direction == 'up':
            pygame.draw.circle(screen, indicator_color, 
                             (int(screen_x), int(screen_y - self.height // 2 + offset)), 3)
        elif self.facing_direction == 'down':
            pygame.draw.circle(screen, indicator_color, 
                             (int(screen_x), int(screen_y + self.height // 2 - offset)), 3)
        elif self.facing_direction == 'left':
            pygame.draw.circle(screen, indicator_color, 
                             (int(screen_x - self.width // 2 + offset), int(screen_y)), 3)
        elif self.facing_direction == 'right':
            pygame.draw.circle(screen, indicator_color, 
                             (int(screen_x + self.width // 2 - offset), int(screen_y)), 3)
        
        # Draw speech bubble indicator when talking
        if self.is_talking:
            bubble_y = screen_y - self.height // 2 - 15
            pygame.draw.circle(screen, colors['WHITE'], (int(screen_x), int(bubble_y)), 8)
            pygame.draw.circle(screen, colors['WHITE'], (int(screen_x - 3), int(bubble_y + 5)), 4)
            pygame.draw.circle(screen, colors['WHITE'], (int(screen_x - 5), int(bubble_y + 9)), 2)
