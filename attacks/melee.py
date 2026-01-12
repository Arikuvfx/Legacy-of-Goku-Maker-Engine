import pygame
from config.settings import WHITE
from core.draw_layers import DrawLayer

class MeleeAttack:
    def __init__(self, x, y, direction):
        self.x = x
        self.y = y
        self.direction = direction
        self.duration = 0.2  # seconds
        self.timer = 0
        self.active = True
        self.size = 40

        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self.y_sort = False

    def get_sort_key(self):
        return (self.draw_layer, 0)
        
    def update(self, dt):
        self.timer += dt
        if self.timer >= self.duration:
            self.active = False
    
    def draw(self, screen, camera, colors):
        if self.active:
            screen_x = self.x - camera.x
            screen_y = self.y - camera.y
            
            # Calculate swish position based on direction
            offset = 35
            if self.direction == 'up':
                start_x = screen_x - self.size // 2
                start_y = screen_y - offset
                end_x = screen_x + self.size // 2
                end_y = screen_y - offset - self.size
            elif self.direction == 'down':
                start_x = screen_x - self.size // 2
                start_y = screen_y + offset
                end_x = screen_x + self.size // 2
                end_y = screen_y + offset + self.size
            elif self.direction == 'left':
                start_x = screen_x - offset
                start_y = screen_y - self.size // 2
                end_x = screen_x - offset - self.size
                end_y = screen_y + self.size // 2
            elif self.direction == 'right':
                start_x = screen_x + offset
                start_y = screen_y - self.size // 2
                end_x = screen_x + offset + self.size
                end_y = screen_y + self.size // 2
            
            # Draw swish effect (arc)
            progress = self.timer / self.duration
            alpha = int(255 * (1 - progress))
            
            # Draw multiple lines for swish effect
            for i in range(5):
                color = (255, 255, 255, alpha) if i == 2 else (200, 200, 200, alpha // 2)
                offset_ratio = (i - 2) * 0.15
                
                if self.direction in ['up', 'down']:
                    sx = start_x + offset_ratio * self.size
                    ex = end_x + offset_ratio * self.size
                    sy = start_y
                    ey = end_y
                else:
                    sx = start_x
                    ex = end_x
                    sy = start_y + offset_ratio * self.size
                    ey = end_y + offset_ratio * self.size
                
                pygame.draw.line(screen, colors['WHITE'], (int(sx), int(sy)), (int(ex), int(ey)), 3)
