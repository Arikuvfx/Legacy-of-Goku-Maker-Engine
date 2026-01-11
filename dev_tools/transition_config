import pygame

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (50, 50, 50)
DARK_GRAY = (30, 30, 30)
LIGHT_GRAY = (200, 200, 200)
CYAN = (0, 255, 255)
YELLOW = (255, 255, 0)

class TransitionConfigMenu:
    """Menu for configuring room transitions"""
    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.font_small = pygame.font.Font(None, 20)
        self.font_medium = pygame.font.Font(None, 24)
        self.active = False
        
        # Configuration
        self.target_room = None
        self.exit_direction = 'up'
        self.entry_direction = 'down'
        self.spawn_x = 400
        self.spawn_y = 300
        self.width = 64
        self.height = 64
        
        # Available rooms (will be populated from room manager)
        self.available_rooms = []
        
        # UI state
        self.selected_field = 0
        self.fields = ['target_room', 'exit_direction', 'entry_direction', 
                       'spawn_x', 'spawn_y', 'width', 'height', 'confirm', 'cancel']
        self.editing_text = False
        self.text_input = ""
        self.text_field = ""
        
    def reset(self):
        """Reset configuration to defaults"""
        self.target_room = None
        self.exit_direction = 'up'
        self.entry_direction = 'down'
        self.spawn_x = 400
        self.spawn_y = 300
        self.width = 64
        self.height = 64
        self.selected_field = 0
        self.editing_text = False
    
    def toggle(self, available_rooms=None):
        """Toggle menu visibility"""
        self.active = not self.active
        if self.active:
            self.reset()
            if available_rooms:
                self.available_rooms = available_rooms
    
    def cycle_direction(self, field):
        """Cycle through direction options"""
        directions = ['up', 'down', 'left', 'right']
        current = getattr(self, field)
        current_index = directions.index(current)
        new_index = (current_index + 1) % len(directions)
        setattr(self, field, directions[new_index])
    
    def cycle_room(self, direction):
        """Cycle through available rooms"""
        if not self.available_rooms:
            return
        
        if self.target_room is None:
            self.target_room = self.available_rooms[0]
        else:
            try:
                current_index = self.available_rooms.index(self.target_room)
                new_index = (current_index + direction) % len(self.available_rooms)
                self.target_room = self.available_rooms[new_index]
            except ValueError:
                self.target_room = self.available_rooms[0]
    
    def handle_input(self, event):
        """Handle input events"""
        if not self.active:
            return None
        
        # Handle text input
        if self.editing_text:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    # Save value
                    try:
                        value = float(self.text_input)
                        setattr(self, self.text_field, value)
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
                    if len(self.text_input) < 10:
                        self.text_input += event.unicode
            return None
        
        # Handle menu navigation
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_UP:
                self.selected_field = (self.selected_field - 1) % len(self.fields)
            elif event.key == pygame.K_DOWN:
                self.selected_field = (self.selected_field + 1) % len(self.fields)
            elif event.key == pygame.K_RETURN or event.key == pygame.K_SPACE:
                field = self.fields[self.selected_field]
                
                if field == 'target_room':
                    self.cycle_room(1)
                elif field == 'exit_direction':
                    self.cycle_direction('exit_direction')
                elif field == 'entry_direction':
                    self.cycle_direction('entry_direction')
                elif field in ['spawn_x', 'spawn_y', 'width', 'height']:
                    self.editing_text = True
                    self.text_field = field
                    self.text_input = str(int(getattr(self, field)))
                elif field == 'confirm':
                    # Return configuration
                    if self.target_room is None:
                        return None  # Must select a room
                    
                    config = {
                        'target_room': self.target_room,
                        'exit_direction': self.exit_direction,
                        'entry_direction': self.entry_direction,
                        'spawn_x': self.spawn_x,
                        'spawn_y': self.spawn_y,
                        'width': self.width,
                        'height': self.height
                    }
                    self.active = False
                    return config
                elif field == 'cancel':
                    self.active = False
                    return 'cancel'
            elif event.key == pygame.K_LEFT:
                field = self.fields[self.selected_field]
                if field == 'target_room':
                    self.cycle_room(-1)
                elif field == 'exit_direction':
                    self.cycle_direction('exit_direction')
                elif field == 'entry_direction':
                    self.cycle_direction('entry_direction')
            elif event.key == pygame.K_RIGHT:
                field = self.fields[self.selected_field]
                if field == 'target_room':
                    self.cycle_room(1)
                elif field == 'exit_direction':
                    self.cycle_direction('exit_direction')
                elif field == 'entry_direction':
                    self.cycle_direction('entry_direction')
            elif event.key == pygame.K_ESCAPE:
                self.active = False
                return 'cancel'
        
        return None
    
    def draw(self, screen):
        """Draw the configuration menu"""
        if not self.active:
            return
        
        # Semi-transparent overlay
        overlay = pygame.Surface((self.screen_width, self.screen_height))
        overlay.set_alpha(180)
        overlay.fill(BLACK)
        screen.blit(overlay, (0, 0))
        
        # Menu box
        menu_width = 500
        menu_height = 480
        menu_x = (self.screen_width - menu_width) // 2
        menu_y = (self.screen_height - menu_height) // 2
        
        menu_rect = pygame.Rect(menu_x, menu_y, menu_width, menu_height)
        pygame.draw.rect(screen, DARK_GRAY, menu_rect)
        pygame.draw.rect(screen, CYAN, menu_rect, 3)
        
        # Title
        title = self.font_medium.render("ROOM TRANSITION CONFIG", True, CYAN)
        title_rect = title.get_rect(center=(self.screen_width // 2, menu_y + 20))
        screen.blit(title, title_rect)
        
        y_offset = menu_y + 60
        
        # Draw fields
        for i, field in enumerate(self.fields):
            is_selected = (i == self.selected_field)
            color = YELLOW if is_selected else WHITE
            prefix = "> " if is_selected else "  "
            
            if field == 'target_room':
                room_name = self.target_room if self.target_room else "None"
                text = f"{prefix}Target Room: {room_name}"
            elif field == 'exit_direction':
                text = f"{prefix}Exit Direction: {self.exit_direction.upper()}"
            elif field == 'entry_direction':
                text = f"{prefix}Entry Direction: {self.entry_direction.upper()}"
            elif field == 'spawn_x':
                text = f"{prefix}Spawn X: {int(self.spawn_x)}"
            elif field == 'spawn_y':
                text = f"{prefix}Spawn Y: {int(self.spawn_y)}"
            elif field == 'width':
                text = f"{prefix}Width: {int(self.width)}"
            elif field == 'height':
                text = f"{prefix}Height: {int(self.height)}"
            elif field == 'confirm':
                text = f"{prefix}CONFIRM AND PLACE"
            elif field == 'cancel':
                text = f"{prefix}CANCEL"
            
            text_surface = self.font_small.render(text, True, color)
            screen.blit(text_surface, (menu_x + 20, y_offset))
            y_offset += 38
        
        # Text input overlay
        if self.editing_text:
            input_y = menu_y + menu_height - 80
            input_rect = pygame.Rect(menu_x + 20, input_y, menu_width - 40, 40)
            pygame.draw.rect(screen, BLACK, input_rect)
            pygame.draw.rect(screen, YELLOW, input_rect, 2)
            
            input_text = self.font_small.render(self.text_input + "_", True, WHITE)
            screen.blit(input_text, (menu_x + 25, input_y + 10))
            
            prompt = self.font_small.render("Type number and press ENTER", True, LIGHT_GRAY)
            screen.blit(prompt, (menu_x + 25, input_y + 45))
        else:
            # Instructions
            instructions = [
                "UP/DOWN: Navigate | ENTER/SPACE: Select/Cycle",
                "LEFT/RIGHT: Cycle options",
                "ESC: Cancel"
            ]
            
            y_offset = menu_y + menu_height - 80
            for instruction in instructions:
                inst_text = self.font_small.render(instruction, True, LIGHT_GRAY)
                inst_rect = inst_text.get_rect(center=(self.screen_width // 2, y_offset))
                screen.blit(inst_text, inst_rect)
                y_offset += 22
