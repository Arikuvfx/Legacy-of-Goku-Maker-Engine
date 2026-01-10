import pygame

class BeamAttack:
    def __init__(self, x, y, direction):
        self.x = x
        self.y = y
        self.direction = direction
        self.width = 30
        self.length = 0
        self.max_length = 400
        self.grow_speed = 600  # pixels per second
        self.active = True
        
    def update(self, dt):
        if self.length < self.max_length:
            self.length += self.grow_speed * dt
            if self.length > self.max_length:
                self.length = self.max_length
    
    def draw(self, screen, camera, colors):
        if self.active and self.length > 0:
            screen_x = self.x - camera.x
            screen_y = self.y - camera.y
            
            # Draw beam based on direction
            if self.direction == 'up':
                # Core beam
                pygame.draw.rect(screen, colors['CYAN'], (screen_x - self.width // 2, screen_y - self.length, self.width, self.length))
                # Outer glow
                pygame.draw.rect(screen, colors['YELLOW'], (screen_x - self.width // 2 - 5, screen_y - self.length, self.width + 10, self.length), 3)
            elif self.direction == 'down':
                pygame.draw.rect(screen, colors['CYAN'], (screen_x - self.width // 2, screen_y, self.width, self.length))
                pygame.draw.rect(screen, colors['YELLOW'], (screen_x - self.width // 2 - 5, screen_y, self.width + 10, self.length), 3)
            elif self.direction == 'left':
                pygame.draw.rect(screen, colors['CYAN'], (screen_x - self.length, screen_y - self.width // 2, self.length, self.width))
                pygame.draw.rect(screen, colors['YELLOW'], (screen_x - self.length, screen_y - self.width // 2 - 5, self.length, self.width + 10), 3)
            elif self.direction == 'right':
                pygame.draw.rect(screen, colors['CYAN'], (screen_x, screen_y - self.width // 2, self.length, self.width))
                pygame.draw.rect(screen, colors['YELLOW'], (screen_x, screen_y - self.width // 2 - 5, self.length, self.width + 10), 3)
