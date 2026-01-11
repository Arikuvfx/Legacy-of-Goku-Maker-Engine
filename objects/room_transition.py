import pygame

class RoomTransition:
    """Invisible object that triggers room transitions"""
    def __init__(self, x, y, width=64, height=64):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.active = True
        
        # Transition configuration
        self.target_room = None  # Which room to transition to
        self.entry_direction = 'down'  # Direction player walks when entering new room
        self.exit_direction = 'up'  # Direction player walks when leaving current room
        self.spawn_x = 0  # X position in target room
        self.spawn_y = 0  # Y position in target room
        
        # Visual (only visible in dev mode)
        self.show_in_dev_mode = True
        
    def get_rect(self):
        """Get collision rectangle"""
        return pygame.Rect(self.x - self.width // 2, 
                          self.y - self.height // 2, 
                          self.width, self.height)
    
    def check_collision(self, player):
        """Check if player is touching this transition"""
        player_rect = pygame.Rect(player.x - player.width // 2,
                                  player.y - player.height // 2,
                                  player.width, player.height)
        return self.get_rect().colliderect(player_rect)
    
    def draw(self, screen, camera, dev_mode=False):
        """Draw transition zone (only visible in dev mode)"""
        if not self.active or not dev_mode:
            return
        
        screen_x = self.x - camera.x
        screen_y = self.y - camera.y
        
        # Draw semi-transparent rectangle
        surface = pygame.Surface((self.width, self.height))
        surface.set_alpha(100)
        surface.fill((255, 0, 255))  # Magenta
        screen.blit(surface, (screen_x - self.width // 2, screen_y - self.height // 2))
        
        # Draw border
        rect = pygame.Rect(screen_x - self.width // 2, 
                          screen_y - self.height // 2, 
                          self.width, self.height)
        pygame.draw.rect(screen, (255, 0, 255), rect, 2)
        
        # Draw direction arrow
        arrow_length = 20
        arrow_x = screen_x
        arrow_y = screen_y
        
        if self.exit_direction == 'up':
            pygame.draw.line(screen, (255, 255, 0), 
                           (arrow_x, arrow_y), 
                           (arrow_x, arrow_y - arrow_length), 3)
            pygame.draw.polygon(screen, (255, 255, 0), [
                (arrow_x, arrow_y - arrow_length),
                (arrow_x - 5, arrow_y - arrow_length + 10),
                (arrow_x + 5, arrow_y - arrow_length + 10)
            ])
        elif self.exit_direction == 'down':
            pygame.draw.line(screen, (255, 255, 0), 
                           (arrow_x, arrow_y), 
                           (arrow_x, arrow_y + arrow_length), 3)
            pygame.draw.polygon(screen, (255, 255, 0), [
                (arrow_x, arrow_y + arrow_length),
                (arrow_x - 5, arrow_y + arrow_length - 10),
                (arrow_x + 5, arrow_y + arrow_length - 10)
            ])
        elif self.exit_direction == 'left':
            pygame.draw.line(screen, (255, 255, 0), 
                           (arrow_x, arrow_y), 
                           (arrow_x - arrow_length, arrow_y), 3)
            pygame.draw.polygon(screen, (255, 255, 0), [
                (arrow_x - arrow_length, arrow_y),
                (arrow_x - arrow_length + 10, arrow_y - 5),
                (arrow_x - arrow_length + 10, arrow_y + 5)
            ])
        elif self.exit_direction == 'right':
            pygame.draw.line(screen, (255, 255, 0), 
                           (arrow_x, arrow_y), 
                           (arrow_x + arrow_length, arrow_y), 3)
            pygame.draw.polygon(screen, (255, 255, 0), [
                (arrow_x + arrow_length, arrow_y),
                (arrow_x + arrow_length - 10, arrow_y - 5),
                (arrow_x + arrow_length - 10, arrow_y + 5)
            ])
        
        # Draw target room name if set
        if self.target_room:
            font = pygame.font.Font(None, 20)
            text = font.render(f"→ {self.target_room}", True, (255, 255, 255))
            screen.blit(text, (screen_x - text.get_width() // 2, screen_y + self.height // 2 + 5))
