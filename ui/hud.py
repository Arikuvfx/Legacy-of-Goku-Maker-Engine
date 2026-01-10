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
    
    def draw_main_menu(self, screen, colors):
        overlay = pygame.Surface((self.screen_width, self.screen_height))
        overlay.set_alpha(200)
        overlay.fill(colors['BLACK'])
        screen.blit(overlay, (0, 0))
        
        menu_width = 300
        menu_height = 400
        menu_x = (self.screen_width - menu_width) // 2
        menu_y = (self.screen_height - menu_height) // 2
        
        menu_rect = pygame.Rect(menu_x, menu_y, menu_width, menu_height)
        pygame.draw.rect(screen, colors['DARK_GRAY'], menu_rect)
        pygame.draw.rect(screen, colors['YELLOW'], menu_rect, 3)
        
        title = self.font_large.render("MENU", True, colors['YELLOW'])
        title_rect = title.get_rect(center=(self.screen_width // 2, menu_y + 40))
        screen.blit(title, title_rect)
        
        y_offset = menu_y + 100
        for i, item in enumerate(self.menu_items):
            if i == self.selected_menu_item:
                item_text = self.font_medium.render(f"> {item} <", True, colors['YELLOW'])
            else:
                item_text = self.font_medium.render(item, True, colors['WHITE'])
            
            item_rect = item_text.get_rect(center=(self.screen_width // 2, y_offset))
            screen.blit(item_text, item_rect)
            y_offset += 55
        
        hint = self.font_small.render("Up/Down: Select | Enter: Confirm", True, colors['LIGHT_GRAY'])
        hint_rect = hint.get_rect(center=(self.screen_width // 2, menu_y + menu_height - 30))
        screen.blit(hint, hint_rect)
    
    def draw_status_screen(self, screen, player, game_config, colors):
        overlay = pygame.Surface((self.screen_width, self.screen_height))
        overlay.set_alpha(200)
        overlay.fill(colors['BLACK'])
        screen.blit(overlay, (0, 0))
        
        menu_width = 500
        menu_height = 550
        menu_x = (self.screen_width - menu_width) // 2
        menu_y = (self.screen_height - menu_height) // 2
        
        menu_rect = pygame.Rect(menu_x, menu_y, menu_width, menu_height)
        pygame.draw.rect(screen, colors['DARK_GRAY'], menu_rect)
        pygame.draw.rect(screen, colors['YELLOW'], menu_rect, 3)
        
        title = self.font_large.render("CHARACTER STATUS", True, colors['YELLOW'])
        title_rect = title.get_rect(center=(self.screen_width // 2, menu_y + 25))
        screen.blit(title, title_rect)
        
        y_offset = menu_y + 60
        
        # Level and XP
        level_text = self.font_medium.render(f"Level: {player.level}", True, colors['CYAN'])
        screen.blit(level_text, (menu_x + 30, y_offset))
        y_offset += 35
        
        # XP Bar
        xp_text = self.font_small.render(f"EXP: {player.exp}/{player.exp_to_next_level}", True, colors['WHITE'])
        screen.blit(xp_text, (menu_x + 30, y_offset))
        y_offset += 25
        
        xp_bar_width = menu_width - 60
        xp_bar_height = 20
        xp_bar_x = menu_x + 30
        xp_bar_y = y_offset
        
        pygame.draw.rect(screen, colors['BLACK'], (xp_bar_x, xp_bar_y, xp_bar_width, xp_bar_height))
        
        if player.exp_to_next_level > 0:
            xp_progress = min(player.exp / player.exp_to_next_level, 1.0)
            xp_fill_width = int(xp_bar_width * xp_progress)
            pygame.draw.rect(screen, colors['CYAN'], (xp_bar_x, xp_bar_y, xp_fill_width, xp_bar_height))
        
        pygame.draw.rect(screen, colors['WHITE'], (xp_bar_x, xp_bar_y, xp_bar_width, xp_bar_height), 2)
        y_offset += 35
        
        # Stat Points Available
        if player.stat_points > 0:
            stat_points_text = self.font_medium.render(f"Stat Points: {player.stat_points}", True, colors['GREEN'])
            screen.blit(stat_points_text, (menu_x + 30, y_offset))
            y_offset += 30
        
        # Stats
        stats_title = self.font_medium.render("STATS", True, colors['YELLOW'])
        screen.blit(stats_title, (menu_x + 30, y_offset))
        y_offset += 30
        
        stat_names = {
            'strength': 'Strength (Melee Damage)',
            'ki_power': 'Ki Power (Ki Damage)',
            'vitality': 'Vitality (Max HP)',
            'energy': 'Energy (Max KI)',
            'speed': 'Speed (Movement)',
            'defense': 'Defense (Damage Reduction)'
        }
        
        for stat_key, stat_label in stat_names.items():
            stat_value = player.stats[stat_key]
            stat_text = self.font_small.render(f"{stat_label}: {stat_value}", True, colors['WHITE'])
            screen.blit(stat_text, (menu_x + 30, y_offset))
            y_offset += 28
        
        y_offset += 10
        
        # Basic Info
        basic_stats = [
            f"HP: {player.hp} / {player.max_hp}",
            f"KI: {int(player.ki)} / {player.max_ki}",
        ]
        
        for stat in basic_stats:
            stat_text = self.font_small.render(stat, True, colors['WHITE'])
            screen.blit(stat_text, (menu_x + 30, y_offset))
            y_offset += 28
        
        close_text = self.font_small.render("Press ESC to return", True, colors['LIGHT_GRAY'])
        close_rect = close_text.get_rect(center=(self.screen_width // 2, menu_y + menu_height - 20))
        screen.blit(close_text, close_rect)
    
    def draw_inventory_screen(self, screen, player, colors):
        overlay = pygame.Surface((self.screen_width, self.screen_height))
        overlay.set_alpha(200)
        overlay.fill(colors['BLACK'])
        screen.blit(overlay, (0, 0))
        
        menu_width = 500
        menu_height = 450
        menu_x = (self.screen_width - menu_width) // 2
        menu_y = (self.screen_height - menu_height) // 2
        
        menu_rect = pygame.Rect(menu_x, menu_y, menu_width, menu_height)
        pygame.draw.rect(screen, colors['DARK_GRAY'], menu_rect)
        pygame.draw.rect(screen, colors['YELLOW'], menu_rect, 3)
        
        title = self.font_large.render("INVENTORY", True, colors['YELLOW'])
        title_rect = title.get_rect(center=(self.screen_width // 2, menu_y + 30))
        screen.blit(title, title_rect)
        
        y_offset = menu_y + 100
        if len(player.inventory) == 0:
            empty_text = self.font_medium.render("Your inventory is empty", True, colors['LIGHT_GRAY'])
            empty_rect = empty_text.get_rect(center=(self.screen_width // 2, menu_y + 200))
            screen.blit(empty_text, empty_rect)
        else:
            for item in player.inventory:
                item_text = self.font_small.render(f"- {item}", True, colors['WHITE'])
                screen.blit(item_text, (menu_x + 30, y_offset))
                y_offset += 35
        
        close_text = self.font_small.render("Press ESC to return", True, colors['LIGHT_GRAY'])
        close_rect = close_text.get_rect(center=(self.screen_width // 2, menu_y + menu_height - 30))
        screen.blit(close_text, close_rect)
    
    def draw_options_screen(self, screen, colors):
        overlay = pygame.Surface((self.screen_width, self.screen_height))
        overlay.set_alpha(200)
        overlay.fill(colors['BLACK'])
        screen.blit(overlay, (0, 0))
        
        menu_width = 400
        menu_height = 350
        menu_x = (self.screen_width - menu_width) // 2
        menu_y = (self.screen_height - menu_height) // 2
        
        menu_rect = pygame.Rect(menu_x, menu_y, menu_width, menu_height)
        pygame.draw.rect(screen, colors['DARK_GRAY'], menu_rect)
        pygame.draw.rect(screen, colors['YELLOW'], menu_rect, 3)
        
        title = self.font_large.render("OPTIONS", True, colors['YELLOW'])
        title_rect = title.get_rect(center=(self.screen_width // 2, menu_y + 30))
        screen.blit(title, title_rect)
        
        y_offset = menu_y + 100
        options = [
            "Sound: ON",
            "Music: ON",
            "Difficulty: Normal",
            "",
            "(Options functionality coming soon)"
        ]
        
        for option in options:
            option_text = self.font_small.render(option, True, colors['WHITE'])
            option_rect = option_text.get_rect(center=(self.screen_width // 2, y_offset))
            screen.blit(option_text, option_rect)
            y_offset += 40
        
        close_text = self.font_small.render("Press ESC to return", True, colors['LIGHT_GRAY'])
        close_rect = close_text.get_rect(center=(self.screen_width // 2, menu_y + menu_height - 30))
        screen.blit(close_text, close_rect)
