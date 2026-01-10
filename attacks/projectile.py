import pygame

class Projectile:
    def __init__(self, x, y, direction):
        self.x = x
        self.y = y
        self.direction = direction
        self.speed = 8
        self.radius = 8
        self.active = True
        
    def update(self, world_width, world_height):
        if self.direction == 'up':
            self.y -= self.speed
        elif self.direction == 'down':
            self.y += self.speed
        elif self.direction == 'left':
            self.x -= self.speed
        elif self.direction == 'right':
            self.x += self.speed
        
        # Deactivate if out of world bounds
        if self.x < 0 or self.x > world_width or self.y < 0 or self.y > world_height:
            self.active = False
    
    def draw(self, screen, camera, colors):
        if self.active:
            screen_x = self.x - camera.x
            screen_y = self.y - camera.y
            pygame.draw.circle(screen, colors['CYAN'], (int(screen_x), int(screen_y)), self.radius)
            pygame.draw.circle(screen, colors['YELLOW'], (int(screen_x), int(screen_y)), self.radius - 3)
