import pygame

class DialogueBox:
    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.font_small = pygame.font.Font(None, 24)
        self.font_medium = pygame.font.Font(None, 28)
        self.active = False
        self.current_text = ""
        self.npc_name = "NPC"
        self.is_final = False
        self.received_item = None
        
    def show(self, text, npc_name="NPC", is_final=False, item=None):
        self.active = True
        self.current_text = text
        self.npc_name = npc_name
        self.is_final = is_final
        self.received_item = item
    
    def hide(self):
        self.active = False
        self.current_text = ""
        self.received_item = None
    
    def draw(self, screen, colors):
        if not self.active:
            return
        
        # Dialogue box at bottom
        box_height = 150
        box_y = self.screen_height - box_height - 110  # Above HUD
        box_rect = pygame.Rect(20, box_y, self.screen_width - 40, box_height)
        
        # Draw box with border
        pygame.draw.rect(screen, colors['DARK_GRAY'], box_rect)
        pygame.draw.rect(screen, colors['CYAN'], box_rect, 3)
        
        # Draw NPC name
        name_text = self.font_medium.render(self.npc_name, True, colors['CYAN'])
        screen.blit(name_text, (35, box_y + 10))
        
        # Draw dialogue text (word wrap)
        words = self.current_text.split(' ')
        lines = []
        current_line = []
        max_width = self.screen_width - 80
        
        for word in words:
            test_line = ' '.join(current_line + [word])
            test_surface = self.font_small.render(test_line, True, colors['WHITE'])
            if test_surface.get_width() <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
        
        if current_line:
            lines.append(' '.join(current_line))
        
        # Draw lines
        y_offset = box_y + 45
        for line in lines[:4]:  # Max 4 lines
            line_surface = self.font_small.render(line, True, colors['WHITE'])
            screen.blit(line_surface, (35, y_offset))
            y_offset += 28
        
        # Draw continue prompt
        if self.received_item:
            prompt = self.font_small.render(f"Received: {self.received_item}! Press E to close", True, colors['YELLOW'])
        elif self.is_final:
            prompt = self.font_small.render("Press E to close", True, colors['LIGHT_GRAY'])
        else:
            prompt = self.font_small.render("Press E to continue...", True, colors['LIGHT_GRAY'])
        
        prompt_rect = prompt.get_rect(right=self.screen_width - 40, bottom=box_y + box_height - 10)
        screen.blit(prompt, prompt_rect)
