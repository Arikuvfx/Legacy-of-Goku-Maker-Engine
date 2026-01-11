import pygame

class SpawnMenu:
    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.font_small = pygame.font.Font(None, 24)
        self.font_medium = pygame.font.Font(None, 32)
        self.active = False
        self.categories = ['Enemies', 'Objects', 'NPCs']
        self.current_category = 0
        
        # Define spawn options for each category
        self.spawn_options = {
            'Enemies': ['Basic Enemy', 'Strong Enemy', 'Boss Enemy'],
            'Objects': ['Tree', 'Rock', 'Chest'],
            'NPCs': ['Villager', 'Merchant', 'Quest Giver']
            'Room Transitions': ['Room Transition']  # Add this line
        }
        self.selected_item = 0
    
    def toggle(self):
        self.active = not self.active
        if self.active:
            self.selected_item = 0
    
    def navigate_category(self, direction):
        self.current_category = (self.current_category + direction) % len(self.categories)
        self.selected_item = 0
    
    def navigate_item(self, direction):
        category = self.categories[self.current_category]
        items = self.spawn_options[category]
        self.selected_item = (self.selected_item + direction) % len(items)
    
    def get_selected_spawn(self):
        category = self.categories[self.current_category]
        items = self.spawn_options[category]
        return category, items[self.selected_item]
    
    def draw(self, screen, colors):
        if not self.active:
            return
        
        # Semi-transparent overlay
        overlay = pygame.Surface((self.screen_width, self.screen_height))
        overlay.set_alpha(150)
        overlay.fill(colors['BLACK'])
        screen.blit(overlay, (0, 0))
        
        # Menu box
        menu_width = 400
        menu_height = 450
        menu_x = (self.screen_width - menu_width) // 2
        menu_y = (self.screen_height - menu_height) // 2
        
        menu_rect = pygame.Rect(menu_x, menu_y, menu_width, menu_height)
        pygame.draw.rect(screen, colors['DARK_GRAY'], menu_rect)
        pygame.draw.rect(screen, colors['CYAN'], menu_rect, 3)
        
        # Title
        title = self.font_medium.render("SPAWN MENU", True, colors['CYAN'])
        title_rect = title.get_rect(center=(self.screen_width // 2, menu_y + 30))
        screen.blit(title, title_rect)
        
        # Category tabs
        tab_y = menu_y + 70
        tab_width = menu_width // len(self.categories)
        for i, category in enumerate(self.categories):
            tab_x = menu_x + i * tab_width
            tab_color = colors['YELLOW'] if i == self.current_category else colors['GRAY']
            pygame.draw.rect(screen, tab_color, (tab_x, tab_y, tab_width, 40))
            pygame.draw.rect(screen, colors['WHITE'], (tab_x, tab_y, tab_width, 40), 2)
            
            cat_text = self.font_small.render(category, True, colors['BLACK'] if i == self.current_category else colors['WHITE'])
            cat_rect = cat_text.get_rect(center=(tab_x + tab_width // 2, tab_y + 20))
            screen.blit(cat_text, cat_rect)
        
        # Items list
        category = self.categories[self.current_category]
        items = self.spawn_options[category]
        
        y_offset = menu_y + 140
        for i, item in enumerate(items):
            if i == self.selected_item:
                item_text = self.font_medium.render(f"> {item} <", True, colors['YELLOW'])
            else:
                item_text = self.font_small.render(item, True, colors['WHITE'])
            
            item_rect = item_text.get_rect(center=(self.screen_width // 2, y_offset))
            screen.blit(item_text, item_rect)
            y_offset += 45
        
        # Instructions
        instructions = [
            "A/D: Switch Category",
            "W/S: Select Item",
            "ENTER: Spawn at Mouse",
            "F1: Close Menu"
        ]
        
        y_offset = menu_y + menu_height - 120
        for instruction in instructions:
            inst_text = self.font_small.render(instruction, True, colors['LIGHT_GRAY'])
            inst_rect = inst_text.get_rect(center=(self.screen_width // 2, y_offset))
            screen.blit(inst_text, inst_rect)
            y_offset += 25
