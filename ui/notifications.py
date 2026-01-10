import pygame

class LevelUpNotification:
    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.font_large = pygame.font.Font(None, 48)
        self.font_medium = pygame.font.Font(None, 32)
        self.active = False
        self.timer = 0
        self.duration = 3.0  # seconds
        self.level = 1
        self.stat_points = 0
    
    def show(self, level, stat_points):
        self.active = True
        self.timer = self.duration
        self.level = level
        self.stat_points = stat_points
    
    def update(self, dt):
        if self.active:
            self.timer -= dt
            if self.timer <= 0:
                self.active = False
    
    def draw(self, screen, colors):
        if not self.active:
            return
        
        # Fade effect
        alpha = min(255, int((self.timer / self.duration) * 255))
        
        # Create surface for text
        level_text = self.font_large.render("LEVEL UP!", True, colors['YELLOW'])
        details_text = self.font_medium.render(f"Level {self.level} | +{self.stat_points} Stat Points", True, colors['WHITE'])
        
        # Center on screen
        level_rect = level_text.get_rect(center=(self.screen_width // 2, self.screen_height // 2 - 40))
        details_rect = details_text.get_rect(center=(self.screen_width // 2, self.screen_height // 2 + 20))
        
        # Draw background
        bg_rect = pygame.Rect(level_rect.left - 20, level_rect.top - 20, 
                             max(level_rect.width, details_rect.width) + 40, 120)
        pygame.draw.rect(screen, colors['BLACK'], bg_rect)
        pygame.draw.rect(screen, colors['YELLOW'], bg_rect, 3)
        
        # Draw text
        screen.blit(level_text, level_rect)
        screen.blit(details_text, details_rect)
