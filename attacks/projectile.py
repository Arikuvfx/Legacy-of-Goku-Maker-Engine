import pygame
from config.settings import WORLD_WIDTH, WORLD_HEIGHT, CYAN, YELLOW

class Projectile:
    def __init__(self, x, y, direction):
        self.x = x
        self.y = y
        self.direction = direction
        self.speed = 8
        self.radius = 8
        self.active = True
        
    def update(self):
        if self.direction == 'up':
            self.y -= self.speed
        elif self.direction == 'down':
            self.y += self.speed
        elif self.direction == 'left':
            self.x -= self.speed
        elif self.direction == 'right':
            self.x += self.speed
        
        # Deactivate if out of world bounds
        if self.x < 0 or self.x > WORLD_WIDTH or self.y < 0 or self.y > WORLD_HEIGHT:
            self.active = False
    
    def draw(self, screen, camera):
        if self.active:
            screen_x = self.x - camera.x
            screen_y = self.y - camera.y
            pygame.draw.circle(screen, CYAN, (int(screen_x), int(screen_y)), self.radius)
            pygame.draw.circle(screen, YELLOW, (int(screen_x), int(screen_y)), self.radius - 3)
