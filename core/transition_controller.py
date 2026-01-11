import pygame

class TransitionController:
    """Handles room transition animations"""
    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        
        # Transition states: 'idle', 'fade_out', 'walking_out', 'switching', 'walking_in', 'fade_in'
        self.state = 'idle'
        self.active = False
        
        # Fade effect
        self.fade_alpha = 0
        self.fade_speed = 255 / 0.5  # Fade to black in 0.5 seconds
        
        # Walking animation
        self.walk_distance = 50  # How far player walks during transition
        self.walk_speed = 50  # Pixels per second
        self.walk_progress = 0
        
        # Transition data
        self.transition_data = None
        self.player_original_pos = None
        self.exit_direction = None
        self.entry_direction = None
        
        # Callbacks
        self.on_transition_complete = None
        
    def start_transition(self, player, transition_obj, on_complete):
        """Start a room transition"""
        if self.active:
            return False
        
        self.active = True
        self.state = 'walking_out'
        self.fade_alpha = 0
        self.walk_progress = 0
        
        # Store transition data
        self.transition_data = {
            'target_room': transition_obj.target_room,
            'spawn_x': transition_obj.spawn_x,
            'spawn_y': transition_obj.spawn_y,
            'entry_direction': transition_obj.entry_direction
        }
        
        self.player_original_pos = (player.x, player.y)
        self.exit_direction = transition_obj.exit_direction
        self.entry_direction = transition_obj.entry_direction
        self.on_transition_complete = on_complete
        
        # Lock player controls
        player.direction = self.exit_direction
        
        return True
    
    def update(self, dt, player):
        """Update transition animation"""
        if not self.active:
            return
        
        if self.state == 'walking_out':
            # Player walks in exit direction
            self.walk_progress += self.walk_speed * dt
            
            # Move player
            if self.exit_direction == 'up':
                player.y -= self.walk_speed * dt
            elif self.exit_direction == 'down':
                player.y += self.walk_speed * dt
            elif self.exit_direction == 'left':
                player.x -= self.walk_speed * dt
            elif self.exit_direction == 'right':
                player.x += self.walk_speed * dt
            
            # Start fading halfway through walk
            if self.walk_progress > self.walk_distance / 2:
                self.fade_alpha += self.fade_speed * dt
                if self.fade_alpha >= 255:
                    self.fade_alpha = 255
            
            # Transition to next state when walk is complete
            if self.walk_progress >= self.walk_distance:
                self.state = 'switching'
                self.fade_alpha = 255
                
        elif self.state == 'switching':
            # Switch rooms (instant)
            if self.on_transition_complete:
                self.on_transition_complete(
                    self.transition_data['target_room'],
                    self.transition_data['spawn_x'],
                    self.transition_data['spawn_y']
                )
            
            # Set player position
            player.x = self.transition_data['spawn_x']
            player.y = self.transition_data['spawn_y']
            player.direction = self.entry_direction
            
            self.state = 'walking_in'
            self.walk_progress = 0
            
        elif self.state == 'walking_in':
            # Player walks in entry direction
            self.walk_progress += self.walk_speed * dt
            
            # Move player
            if self.entry_direction == 'up':
                player.y -= self.walk_speed * dt
            elif self.entry_direction == 'down':
                player.y += self.walk_speed * dt
            elif self.entry_direction == 'left':
                player.x -= self.walk_speed * dt
            elif self.entry_direction == 'right':
                player.x += self.walk_speed * dt
            
            # Start fading in halfway through walk
            if self.walk_progress > self.walk_distance / 2:
                self.fade_alpha -= self.fade_speed * dt
                if self.fade_alpha <= 0:
                    self.fade_alpha = 0
            
            # Complete transition
            if self.walk_progress >= self.walk_distance:
                self.state = 'idle'
                self.active = False
                self.fade_alpha = 0
    
    def draw(self, screen):
        """Draw fade overlay"""
        if not self.active or self.fade_alpha <= 0:
            return
        
        overlay = pygame.Surface((self.screen_width, self.screen_height))
        overlay.set_alpha(int(self.fade_alpha))
        overlay.fill((0, 0, 0))
        screen.blit(overlay, (0, 0))
    
    def is_transitioning(self):
        """Check if currently transitioning"""
        return self.active
