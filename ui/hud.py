import pygame
from config.settings import RENDER_SCALE

# All layout values were designed for 2x scale — dividing by RENDER_SCALE keeps
# them proportional at whatever scale the game runs at.
_S = max(1, RENDER_SCALE)


class UI:
    def __init__(self, screen_width, screen_height):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.font_small  = pygame.font.Font(None, max(12, int(24 / _S)))
        self.font_medium = pygame.font.Font(None, max(14, int(32 / _S)))
        self.font_large  = pygame.font.Font(None, max(16, int(36 / _S)))
        self.current_screen    = 'game'
        self.selected_menu_item = 0
        self.menu_items = ['Continue', 'Status', 'Inventory', 'Options', 'Quit']

    # ── HUD ───────────────────────────────────────────────────────────────────

    def draw_hud(self, screen, player, colors):
        H, W, s = self.screen_height, self.screen_width, _S

        hud_h    = int(100 / s)
        hud_rect = pygame.Rect(0, H - hud_h, W, hud_h)
        pygame.draw.rect(screen, colors['GRAY'],  hud_rect)
        pygame.draw.rect(screen, colors['WHITE'], hud_rect, 2)

        pad_x  = int(20  / s)
        bar_x  = int(60  / s)
        bar_w  = int(200 / s)
        bar_h  = int(20  / s)
        val_x  = int(270 / s)
        row1_y = H - int(80 / s)
        row2_y = H - int(50 / s)
        ctrl_y = H - int(20 / s)

        # HP bar
        screen.blit(self.font_small.render("HP", True, colors['WHITE']), (pad_x, row1_y))
        pygame.draw.rect(screen, colors['BLACK'], (bar_x, row1_y, bar_w, bar_h))
        pygame.draw.rect(screen, colors['RED'],   (bar_x, row1_y, int((player.hp / player.max_hp) * bar_w), bar_h))
        pygame.draw.rect(screen, colors['WHITE'], (bar_x, row1_y, bar_w, bar_h), 2)
        screen.blit(self.font_small.render(f"{player.hp}/{player.max_hp}", True, colors['WHITE']), (val_x, row1_y))

        # KI bar
        screen.blit(self.font_small.render("KI", True, colors['WHITE']), (pad_x, row2_y))
        pygame.draw.rect(screen, colors['BLACK'], (bar_x, row2_y, bar_w, bar_h))
        pygame.draw.rect(screen, colors['GREEN'], (bar_x, row2_y, int((player.ki / player.max_ki) * bar_w), bar_h))
        pygame.draw.rect(screen, colors['WHITE'], (bar_x, row2_y, bar_w, bar_h), 2)
        screen.blit(self.font_small.render(f"{int(player.ki)}/{player.max_ki}", True, colors['WHITE']), (val_x, row2_y))

        # Level / EXP / mode
        info_x = int(380 / s)
        screen.blit(self.font_small.render(f"LV: {player.level}", True, colors['YELLOW']), (info_x, row1_y))
        screen.blit(self.font_small.render(f"EXP: {player.exp}",  True, colors['WHITE']),  (info_x, row2_y))

        mode_color = colors['CYAN'] if player.ki_attack_mode == 'blast' else colors['PURPLE']
        screen.blit(self.font_small.render(f"Mode: {player.ki_attack_mode.upper()}", True, mode_color), (info_x, ctrl_y))

        screen.blit(self.font_small.render("Q: Ki | E: Melee | TAB: Switch | ESC: Menu", True, colors['LIGHT_GRAY']), (pad_x, ctrl_y))

    # ── Menus ─────────────────────────────────────────────────────────────────

    def draw_main_menu(self, screen, colors):
        self._draw_overlay(screen, colors)
        s = _S
        mw = int(300 / s)
        mh = int(400 / s)
        mx = (self.screen_width  - mw) // 2
        my = (self.screen_height - mh) // 2

        pygame.draw.rect(screen, colors['DARK_GRAY'], (mx, my, mw, mh))
        pygame.draw.rect(screen, colors['YELLOW'],    (mx, my, mw, mh), 3)

        title = self.font_large.render("MENU", True, colors['YELLOW'])
        screen.blit(title, title.get_rect(center=(self.screen_width // 2, my + int(40 / s))))

        y_off = my + int(100 / s)
        for i, item in enumerate(self.menu_items):
            label = f"> {item} <" if i == self.selected_menu_item else item
            color = colors['YELLOW'] if i == self.selected_menu_item else colors['WHITE']
            surf  = self.font_medium.render(label, True, color)
            screen.blit(surf, surf.get_rect(center=(self.screen_width // 2, y_off)))
            y_off += int(55 / s)

        hint = self.font_small.render("Up/Down: Select | Enter: Confirm", True, colors['LIGHT_GRAY'])
        screen.blit(hint, hint.get_rect(center=(self.screen_width // 2, my + mh - int(30 / s))))

    def draw_status_screen(self, screen, player, game_config, colors):
        self._draw_overlay(screen, colors)
        s = _S
        mw = int(500 / s)
        mh = int(550 / s)
        mx = (self.screen_width  - mw) // 2
        my = (self.screen_height - mh) // 2
        pad = int(30 / s)

        pygame.draw.rect(screen, colors['DARK_GRAY'], (mx, my, mw, mh))
        pygame.draw.rect(screen, colors['YELLOW'],    (mx, my, mw, mh), 3)

        title = self.font_large.render("CHARACTER STATUS", True, colors['YELLOW'])
        screen.blit(title, title.get_rect(center=(self.screen_width // 2, my + int(25 / s))))

        y = my + int(60 / s)

        screen.blit(self.font_medium.render(f"Level: {player.level}", True, colors['CYAN']), (mx + pad, y))
        y += int(35 / s)
        screen.blit(self.font_small.render(f"EXP: {player.exp}/{player.exp_to_next_level}", True, colors['WHITE']), (mx + pad, y))
        y += int(25 / s)

        xp_w = mw - pad * 2
        xp_h = int(20 / s)
        pygame.draw.rect(screen, colors['BLACK'], (mx + pad, y, xp_w, xp_h))
        if player.exp_to_next_level > 0:
            fill = int(xp_w * min(player.exp / player.exp_to_next_level, 1.0))
            pygame.draw.rect(screen, colors['CYAN'], (mx + pad, y, fill, xp_h))
        pygame.draw.rect(screen, colors['WHITE'], (mx + pad, y, xp_w, xp_h), 2)
        y += int(35 / s)

        if player.stat_points > 0:
            screen.blit(self.font_medium.render(f"Stat Points: {player.stat_points}", True, colors['GREEN']), (mx + pad, y))
            y += int(30 / s)

        screen.blit(self.font_medium.render("STATS", True, colors['YELLOW']), (mx + pad, y))
        y += int(30 / s)

        stat_names = {
            'strength': 'Strength (Melee Damage)',
            'ki_power': 'Ki Power (Ki Damage)',
            'vitality': 'Vitality (Max HP)',
            'energy':   'Energy (Max KI)',
            'speed':    'Speed (Movement)',
            'defense':  'Defense (Damage Reduction)',
        }
        for key, label in stat_names.items():
            screen.blit(self.font_small.render(f"{label}: {player.stats[key]}", True, colors['WHITE']), (mx + pad, y))
            y += int(28 / s)

        y += int(10 / s)
        for text in [f"HP: {player.hp} / {player.max_hp}", f"KI: {int(player.ki)} / {player.max_ki}"]:
            screen.blit(self.font_small.render(text, True, colors['WHITE']), (mx + pad, y))
            y += int(28 / s)

        close = self.font_small.render("Press ESC to return", True, colors['LIGHT_GRAY'])
        screen.blit(close, close.get_rect(center=(self.screen_width // 2, my + mh - int(20 / s))))

    def draw_inventory_screen(self, screen, player, colors):
        self._draw_overlay(screen, colors)
        s = _S
        mw  = int(500 / s)
        mh  = int(450 / s)
        mx  = (self.screen_width  - mw) // 2
        my  = (self.screen_height - mh) // 2
        pad = int(30 / s)

        pygame.draw.rect(screen, colors['DARK_GRAY'], (mx, my, mw, mh))
        pygame.draw.rect(screen, colors['YELLOW'],    (mx, my, mw, mh), 3)

        title = self.font_large.render("INVENTORY", True, colors['YELLOW'])
        screen.blit(title, title.get_rect(center=(self.screen_width // 2, my + int(30 / s))))

        if not player.inventory:
            empty = self.font_medium.render("Your inventory is empty", True, colors['LIGHT_GRAY'])
            screen.blit(empty, empty.get_rect(center=(self.screen_width // 2, my + int(200 / s))))
        else:
            y = my + int(100 / s)
            for item in player.inventory:
                screen.blit(self.font_small.render(f"- {item}", True, colors['WHITE']), (mx + pad, y))
                y += int(35 / s)

        close = self.font_small.render("Press ESC to return", True, colors['LIGHT_GRAY'])
        screen.blit(close, close.get_rect(center=(self.screen_width // 2, my + mh - int(30 / s))))

    def draw_options_screen(self, screen, colors):
        self._draw_overlay(screen, colors)
        s = _S
        mw = int(400 / s)
        mh = int(350 / s)
        mx = (self.screen_width  - mw) // 2
        my = (self.screen_height - mh) // 2

        pygame.draw.rect(screen, colors['DARK_GRAY'], (mx, my, mw, mh))
        pygame.draw.rect(screen, colors['YELLOW'],    (mx, my, mw, mh), 3)

        title = self.font_large.render("OPTIONS", True, colors['YELLOW'])
        screen.blit(title, title.get_rect(center=(self.screen_width // 2, my + int(30 / s))))

        y = my + int(100 / s)
        for line in ["Sound: ON", "Music: ON", "Difficulty: Normal", "", "(Options functionality coming soon)"]:
            surf = self.font_small.render(line, True, colors['WHITE'])
            screen.blit(surf, surf.get_rect(center=(self.screen_width // 2, y)))
            y += int(40 / s)

        close = self.font_small.render("Press ESC to return", True, colors['LIGHT_GRAY'])
        screen.blit(close, close.get_rect(center=(self.screen_width // 2, my + mh - int(20 / s))))

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _draw_overlay(self, screen, colors):
        overlay = pygame.Surface((self.screen_width, self.screen_height))
        overlay.set_alpha(200)
        overlay.fill(colors['BLACK'])
        screen.blit(overlay, (0, 0))