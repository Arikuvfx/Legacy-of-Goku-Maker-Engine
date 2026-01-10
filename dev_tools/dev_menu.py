import pygame

class DevMenu:
    def __init__(self, game_config, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.font_small = pygame.font.Font(None, 20)
        self.font_medium = pygame.font.Font(None, 24)
        self.active = False
        self.config = game_config
        
        self.selected_field = 0
        self.fields = ['spawn_menu', 'room_editor', 'config', 'close']
        self.current_submenu = None
        self.editing_text = False
        self.text_input = ""
        self.text_field = ""
    
    def toggle(self):
        self.active = not self.active
    
    def handle_input(self, event):
        if not self.active:
            return None
        
        if self.editing_text:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    # Save value
                    try:
                        value = float(self.text_input)
                        setattr(self.config, self.text_field, value)
                    except:
                        pass
                    
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
                    if len(self.text_input) < 20:
                        self.text_input += event.unicode
            return None
        
        if event.type == pygame.KEYDOWN:
            # Handle back navigation
            if event.key == pygame.K_ESCAPE:
                if self.current_submenu == 'xp_config':
                    self.current_submenu = 'config'
                    self.selected_field = 0
                elif self.current_submenu == 'config':
                    self.current_submenu = None
                    self.selected_field = 0
                else:
                    self.active = False
                return None
            
            # Get current fields based on submenu
            if self.current_submenu == 'config':
                fields = ['xp_config', 'back']
            elif self.current_submenu == 'xp_config':
                fields = ['max_level', 'base_xp_requirement', 'xp_scaling_factor', 
                         'stat_points_per_level', 'back']
            else:
                fields = self.fields
            
            if event.key == pygame.K_UP:
                self.selected_field = (self.selected_field - 1) % len(fields)
            elif event.key == pygame.K_DOWN:
                self.selected_field = (self.selected_field + 1) % len(fields)
            elif event.key == pygame.K_RETURN or event.key == pygame.K_SPACE:
                field = fields[self.selected_field]
                
                if field == 'spawn_menu':
                    return 'open_spawn_menu'
                elif field == 'room_editor':
                    return 'open_room_editor'
                elif field == 'config':
                    self.current_submenu = 'config'
                    self.selected_field = 0
                elif field == 'xp_config':
                    self.current_submenu = 'xp_config'
                    self.selected_field = 0
                elif field == 'back':
                    if self.current_submenu == 'xp_config':
                        self.current_submenu = 'config'
                        self.selected_field = 0
                    elif self.current_submenu == 'config':
                        self.current_submenu = None
                        self.selected_field = 0
                elif field == 'close':
                    self.active = False
                
                # Handle XP config fields
                elif field in ['max_level', 'base_xp_requirement', 'xp_scaling_factor', 'stat_points_per_level']:
                    self.editing_text = True
                    self.text_field = field
                    self.text_input = str(getattr(self.config, field))
        
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
        menu_width = 500
        menu_height = 480
        menu_x = (self.screen_width - menu_width) // 2
        menu_y = (self.screen_height - menu_height) // 2
        
        menu_rect = pygame.Rect(menu_x, menu_y, menu_width, menu_height)
        pygame.draw.rect(screen, colors['DARK_GRAY'], menu_rect)
        pygame.draw.rect(screen, colors['CYAN'], menu_rect, 3)
        
        # Title based on current submenu
        if self.current_submenu == 'config':
            title_text = "CONFIG MENU"
        elif self.current_submenu == 'xp_config':
            title_text = "XP CONFIGURATION"
        else:
            title_text = "DEVELOPER MENU"
        
        title = self.font_medium.render(title_text, True, colors['CYAN'])
        title_rect = title.get_rect(center=(self.screen_width // 2, menu_y + 20))
        screen.blit(title, title_rect)
        
        y_offset = menu_y + 55
        
        # Determine which fields to display
        if self.current_submenu == 'config':
            fields = ['xp_config', 'back']
            field_labels = {
                'xp_config': 'XP Configuration',
                'back': 'BACK'
            }
        elif self.current_submenu == 'xp_config':
            fields = ['max_level', 'base_xp_requirement', 'xp_scaling_factor', 
                     'stat_points_per_level', 'back']
            field_labels = {
                'max_level': 'Max Level',
                'base_xp_requirement': 'Base XP Requirement',
                'xp_scaling_factor': 'XP Scaling Factor',
                'stat_points_per_level': 'Stat Points Per Level',
                'back': 'BACK'
            }
        else:
            fields = self.fields
            field_labels = {
                'spawn_menu': 'Open Spawn Menu',
                'config': 'Configuration',
                'room_editor': 'Room Editor',
                'close': 'CLOSE MENU'
            }
        
        # Draw fields
        for i, field in enumerate(fields):
            is_selected = (i == self.selected_field)
            color = colors['YELLOW'] if is_selected else colors['WHITE']
            prefix = "> " if is_selected else "  "
            
            if field in ['close', 'back', 'spawn_menu', 'config', 'xp_config', 'room_editor']:
                text = f"{prefix}{field_labels[field]}"
            else:
                value = getattr(self.config, field)
                text = f"{prefix}{field_labels[field]}: {value}"
            
            text_surface = self.font_small.render(text, True, color)
            screen.blit(text_surface, (menu_x + 20, y_offset))
            y_offset += 35
        
        # Text input overlay
        if self.editing_text:
            input_y = menu_y + menu_height - 60
            input_rect = pygame.Rect(menu_x + 20, input_y, menu_width - 40, 30)
            pygame.draw.rect(screen, colors['BLACK'], input_rect)
            pygame.draw.rect(screen, colors['YELLOW'], input_rect, 2)
            
            input_text = self.font_small.render(self.text_input + "_", True, colors['WHITE'])
            screen.blit(input_text, (menu_x + 25, input_y + 5))
        else:
            # Instructions
            instructions = "UP/DOWN: Navigate | ENTER: Select/Edit | ESC: Back/Close"
            inst_text = self.font_small.render(instructions, True, colors['LIGHT_GRAY'])
            inst_rect = inst_text.get_rect(center=(self.screen_width // 2, menu_y + menu_height - 30))
            screen.blit(inst_text, inst_rect)
