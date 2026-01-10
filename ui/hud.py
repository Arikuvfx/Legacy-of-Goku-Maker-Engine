import pygame

class UI:
    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.font_small = pygame.font.Font(None, 24)
        self.font_medium = pygame.font.Font(None, 32)
        self.font_large = pygame.font.Font(None, 36)
        self.current_screen = 'game'
        self.selected_menu_item = 0
        self.menu_items = ['Continue', 'Status', 'Inventory', 'Options', 'Quit']
        
    def draw_hud(self, screen, player, colors):
        hud_rect = pygame.Rect(0, self.screen_height - 100, self.screen_width, 100)
        pygame.draw.rect(screen, colors['GRAY'], hud_rect)
        pygame.draw.rect(screen, colors['WHITE'], hud_rect, 2)
        
        # HP Bar
        hp_text = self.font_small.render("HP", True, colors['WHITE'])
        screen.blit(hp_text, (20, self.screen_height - 80))
        
        hp_bar_bg = pygame.Rect(60, self.screen_height - 75, 200, 20)
        pygame.draw.rect(screen, colors['BLACK'], hp_bar_bg)
        
        hp_width = int((player.hp / player.max_hp) * 200)
        hp_bar = pygame.Rect(60, self.screen_height - 75, hp_width, 20)
        pygame.draw.rect(screen, colors['RED'], hp_bar)
        pygame.draw.rect(screen, colors['WHITE'], hp_bar_bg, 2)
        
        hp_value = self.font_small.render(f"{player.hp}/{player.max_hp}", True, colors['WHITE'])
        screen.blit(hp_value, (270, self.screen_height - 80))
        
        # KI Bar
        ki_text = self.font_small.render("KI", True, colors['WHITE'])
        screen.blit(ki_text, (20, self.screen_height - 50))
        
        ki_bar_bg = pygame.Rect(60, self.screen_height - 45, 200, 20)
        pygame.draw.rect(screen, colors['BLACK'], ki_bar_bg)
        
        ki_width = int((player.ki / player.max_ki) * 200)
        ki_bar = pygame.Rect(60, self.screen_height - 45, ki_width, 20)
        pygame.draw.rect(screen, colors['GREEN'], ki_bar)
        pygame.draw.rect(screen, colors['WHITE'], ki_bar_bg, 2)
        
        ki_value = self.font_small.render(f"{int(player.ki)}/{player.max_ki}", True, colors['WHITE'])
        screen.blit(ki_value, (270, self.screen_height - 50))
        
        # Level and EXP
        level_text = self.font_small.render(f"LV: {player.level}", True, colors['YELLOW'])
        screen.blit(level_text, (380, self.screen_height - 80))
        
        exp_text = self.font_small.render(f"EXP: {player.exp}", True, colors['WHITE'])
        screen.blit(exp_text, (380, self.screen_height - 50))
        
        # Attack mode
        mode_color = colors['CYAN'] if player.ki_attack_mode == 'blast' else colors['PURPLE']
        mode_text = self.font_small.render(f"Mode: {player.ki_attack_mode.upper()}", True, mode_color)
        screen.blit(mode_text, (380, self.screen_height - 20))
        
        # Controls hint
        controls = self.font_small.render("Q: Ki | E: Melee | TAB: Switch | ESC: Menu", True, colors['LIGHT_GRAY'])
        screen.blit(controls, (20, self.screen_height - 20))
