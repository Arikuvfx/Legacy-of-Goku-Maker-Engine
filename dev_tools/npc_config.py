import pygame

class NPCConfigMenu:
    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.font_small = pygame.font.Font(None, 20)
        self.font_medium = pygame.font.Font(None, 24)
        self.active = False
        
        # Configuration
        self.npc_type = 'static'
        self.dialogues = ["Hello, traveler!"]
        self.current_editing = 0
        self.trigger_limit = -1
        self.after_limit_text = "I have nothing more to say."
        self.random_order = False
        self.give_item = None
        
        # UI state
        self.selected_field = 0
        self.fields = ['npc_type', 'edit_dialogue', 'add_dialogue', 'remove_dialogue', 
                       'trigger_limit', 'after_limit_text', 'random_order', 'give_item', 'confirm']
        self.editing_text = False
        self.text_input = ""
        self.text_field = ""
        
    def reset(self):
        self.npc_type = 'static'
        self.dialogues = ["Hello, traveler!"]
        self.current_editing = 0
        self.trigger_limit = -1
        self.after_limit_text = "I have nothing more to say."
        self.random_order = False
        self.give_item = None
        self.selected_field = 0
        self.editing_text = False
    
    def toggle(self):
        self.active = not self.active
        if self.active:
            self.reset()
    
    def handle_input(self, event):
        if not self.active:
            return None
        
        if self.editing_text:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    # Save text input
                    if self.text_field == 'dialogue':
                        self.dialogues[self.current_editing] = self.text_input
                    elif self.text_field == 'after_limit':
                        self.after_limit_text = self.text_input
                    elif self.text_field == 'item':
                        self.give_item = self.text_input if self.text_input else None
                    elif self.text_field == 'trigger_limit':
                        try:
                            self.trigger_limit = int(self.text_input)
                        except:
                            self.trigger_limit = -1
                    
                    self.editing_text = False
                    self.text_input = ""
                    self.text_field = ""
                elif event.key == pygame.K_ESCAPE:
                    self.editing_text = False
                    self.text_input = ""
                    self.text_field = ""
                elif event.key == pygame.K_BACKSPACE:
                    self.text_input = self.text_input[:-1]
                else:
                    if len(self.text_input) < 100:
                        self.text_input += event.unicode
            return None
        
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_UP:
                self.selected_field = (self.selected_field - 1) % len(self.fields)
            elif event.key == pygame.K_DOWN:
                self.selected_field = (self.selected_field + 1) % len(self.fields)
            elif event.key == pygame.K_RETURN or event.key == pygame.K_SPACE:
                field = self.fields[self.selected_field]
                
                if field == 'npc_type':
                    self.npc_type = 'moving' if self.npc_type == 'static' else 'static'
                elif field == 'edit_dialogue':
                    if len(self.dialogues) > 0:
                        self.editing_text = True
                        self.text_field = 'dialogue'
                        self.text_input = self.dialogues[self.current_editing]
                elif field == 'add_dialogue':
                    self.dialogues.append("New dialogue")
                    self.current_editing = len(self.dialogues) - 1
                elif field == 'remove_dialogue':
                    if len(self.dialogues) > 1:
                        self.dialogues.pop(self.current_editing)
                        self.current_editing = max(0, self.current_editing - 1)
                elif field == 'trigger_limit':
                    self.editing_text = True
                    self.text_field = 'trigger_limit'
                    self.text_input = str(self.trigger_limit)
                elif field == 'after_limit_text':
                    self.editing_text = True
                    self.text_field = 'after_limit'
                    self.text_input = self.after_limit_text
                elif field == 'random_order':
                    self.random_order = not self.random_order
                elif field == 'give_item':
                    self.editing_text = True
                    self.text_field = 'item'
                    self.text_input = self.give_item if self.give_item else ""
                elif field == 'confirm':
                    # Return configuration
                    config = {
                        'npc_type': self.npc_type,
                        'dialogues': self.dialogues.copy(),
                        'trigger_limit': self.trigger_limit,
                        'triggers_used': 0,
                        'after_limit_text': self.after_limit_text,
                        'random_order': self.random_order,
                        'give_item': self.give_item,
                        'item_given': False
                    }
                    self.active = False
                    return config
            elif event.key == pygame.K_LEFT:
                if self.fields[self.selected_field] == 'edit_dialogue':
                    self.current_editing = (self.current_editing - 1) % len(self.dialogues)
            elif event.key == pygame.K_RIGHT:
                if self.fields[self.selected_field] == 'edit_dialogue':
                    self.current_editing = (self.current_editing + 1) % len(self.dialogues)
            elif event.key == pygame.K_ESCAPE:
                self.active = False
                return 'cancel'
        
        return None
    
    def draw(self, screen, colors):
        if not self.active:
            return
        
        # Semi-transparent overlay
        overlay = pygame.Surface((self.screen_width, self.screen_height))
        overlay.set_alpha(180)
        overlay.fill(colors['BLACK'])
        screen.blit(overlay, (0, 0))
        
        # Menu box
        menu_width = 600
        menu_height = 520
        menu_x = (self.screen_width - menu_width) // 2
        menu_y = (self.screen_height - menu_height) // 2
        
        menu_rect = pygame.Rect(menu_x, menu_y, menu_width, menu_height)
        pygame.draw.rect(screen, colors['DARK_GRAY'], menu_rect)
        pygame.draw.rect(screen, colors['CYAN'], menu_rect, 3)
        
        # Title
        title = self.font_medium.render("NPC CONFIGURATION", True, colors['CYAN'])
        title_rect = title.get_rect(center=(self.screen_width // 2, menu_y + 20))
        screen.blit(title, title_rect)
        
        y_offset = menu_y + 55
        
        # Draw fields
        for i, field in enumerate(self.fields):
            is_selected = (i == self.selected_field)
            color = colors['YELLOW'] if is_selected else colors['WHITE']
            prefix = "> " if is_selected else "  "
            
            if field == 'npc_type':
                text = f"{prefix}NPC Type: {self.npc_type.upper()}"
            elif field == 'edit_dialogue':
                text = f"{prefix}Edit Dialogue {self.current_editing + 1}/{len(self.dialogues)}: {self.dialogues[self.current_editing][:30]}..."
            elif field == 'add_dialogue':
                text = f"{prefix}Add New Dialogue"
            elif field == 'remove_dialogue':
                text = f"{prefix}Remove Dialogue {self.current_editing + 1}"
            elif field == 'trigger_limit':
                limit_text = "Unlimited" if self.trigger_limit == -1 else str(self.trigger_limit)
                text = f"{prefix}Trigger Limit: {limit_text}"
            elif field == 'after_limit_text':
                text = f"{prefix}After Limit Text: {self.after_limit_text[:25]}..."
            elif field == 'random_order':
                text = f"{prefix}Random Order: {'YES' if self.random_order else 'NO'}"
            elif field == 'give_item':
                item_text = self.give_item if self.give_item else "None"
                text = f"{prefix}Give Item: {item_text}"
            elif field == 'confirm':
                text = f"{prefix}CONFIRM AND PLACE NPC"
            
            text_surface = self.font_small.render(text, True, color)
            screen.blit(text_surface, (menu_x + 20, y_offset))
            y_offset += 35
        
        # Text input overlay
        if self.editing_text:
            input_y = menu_y + menu_height - 80
            input_rect = pygame.Rect(menu_x + 20, input_y, menu_width - 40, 40)
            pygame.draw.rect(screen, colors['BLACK'], input_rect)
            pygame.draw.rect(screen, colors['YELLOW'], input_rect, 2)
            
            input_text = self.font_small.render(self.text_input + "_", True, colors['WHITE'])
            screen.blit(input_text, (menu_x + 25, input_y + 10))
            
            prompt = self.font_small.render("Type and press ENTER to save, ESC to cancel", True, colors['LIGHT_GRAY'])
            screen.blit(prompt, (menu_x + 25, input_y + 45))
        else:
            # Instructions
            instructions = [
                "UP/DOWN: Navigate | ENTER/SPACE: Select",
                "LEFT/RIGHT: Change dialogue (when editing)",
                "ESC: Cancel"
            ]
            
            y_offset = menu_y + menu_height - 80
            for instruction in instructions:
                inst_text = self.font_small.render(instruction, True, colors['LIGHT_GRAY'])
                inst_rect = inst_text.get_rect(center=(self.screen_width // 2, y_offset))
                screen.blit(inst_text, inst_rect)
                y_offset += 20
