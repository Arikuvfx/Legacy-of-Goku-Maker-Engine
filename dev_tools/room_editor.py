import pygame

class RoomEditorMenu:
    def __init__(self, room_manager, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.font_small = pygame.font.Font(None, 20)
        self.font_medium = pygame.font.Font(None, 24)
        self.active = False
        self.room_manager = room_manager
        
        # UI state
        self.selected_field = 0
        self.current_view = 'main'
        self.editing_text = False
        self.text_input = ""
        self.text_field = ""
        
        # Create room state
        self.new_room_name = "New Room"
        self.new_room_width = 2400
        self.new_room_height = 1800
        self.new_room_group = "Default"
        
        # List view state
        self.selected_room_index = 0
        self.selected_group_index = 0
        
    def toggle(self):
        self.active = not self.active
        if self.active:
            self.current_view = 'main'
            self.selected_field = 0
    
    def handle_input(self, event):
        if not self.active:
            return None
        
        if self.editing_text:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    # Save text input
                    if self.text_field == 'room_name':
                        self.new_room_name = self.text_input
                    elif self.text_field == 'room_width':
                        try:
                            self.new_room_width = int(self.text_input)
                        except:
                            self.new_room_width = 2400
                    elif self.text_field == 'room_height':
                        try:
                            self.new_room_height = int(self.text_input)
                        except:
                            self.new_room_height = 1800
                    elif self.text_field == 'group_name':
                        if self.text_input:
                            self.room_manager.create_group(self.text_input)
                    
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
                    if len(self.text_input) < 50:
                        self.text_input += event.unicode
            return None
        
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if self.current_view == 'main':
                    self.active = False
                else:
                    self.current_view = 'main'
                    self.selected_field = 0
                return None
            
            # Main menu navigation
            if self.current_view == 'main':
                fields = ['create_room', 'list_rooms', 'manage_groups', 'back']
                
                if event.key == pygame.K_UP:
                    self.selected_field = (self.selected_field - 1) % len(fields)
                elif event.key == pygame.K_DOWN:
                    self.selected_field = (self.selected_field + 1) % len(fields)
                elif event.key == pygame.K_RETURN:
                    field = fields[self.selected_field]
                    if field == 'create_room':
                        self.current_view = 'create'
                        self.selected_field = 0
                    elif field == 'list_rooms':
                        self.current_view = 'list'
                        self.selected_field = 0
                        self.selected_room_index = 0
                    elif field == 'manage_groups':
                        self.current_view = 'groups'
                        self.selected_field = 0
                        self.selected_group_index = 0
                    elif field == 'back':
                        self.active = False
            
            # Create room view
            elif self.current_view == 'create':
                fields = ['room_name', 'room_width', 'room_height', 'select_group', 'confirm', 'cancel']
                
                if event.key == pygame.K_UP:
                    self.selected_field = (self.selected_field - 1) % len(fields)
                elif event.key == pygame.K_DOWN:
                    self.selected_field = (self.selected_field + 1) % len(fields)
                elif event.key == pygame.K_RETURN:
                    field = fields[self.selected_field]
                    if field == 'room_name':
                        self.editing_text = True
                        self.text_field = 'room_name'
                        self.text_input = self.new_room_name
                    elif field == 'room_width':
                        self.editing_text = True
                        self.text_field = 'room_width'
                        self.text_input = str(self.new_room_width)
                    elif field == 'room_height':
                        self.editing_text = True
                        self.text_field = 'room_height'
                        self.text_input = str(self.new_room_height)
                    elif field == 'select_group':
                        # Cycle through groups
                        current_idx = self.room_manager.groups.index(self.new_room_group)
                        next_idx = (current_idx + 1) % len(self.room_manager.groups)
                        self.new_room_group = self.room_manager.groups[next_idx]
                    elif field == 'confirm':
                        # Create the room
                        room = self.room_manager.create_room(
                            self.new_room_name,
                            self.new_room_width,
                            self.new_room_height,
                            self.new_room_group
                        )
                        self.current_view = 'main'
                        self.selected_field = 0
                        return {'action': 'room_created', 'room': room}
                    elif field == 'cancel':
                        self.current_view = 'main'
                        self.selected_field = 0
            
            # List rooms view
            elif self.current_view == 'list':
                if len(self.room_manager.rooms) == 0:
                    if event.key == pygame.K_RETURN:
                        self.current_view = 'main'
                        self.selected_field = 0
                else:
                    if event.key == pygame.K_UP:
                        self.selected_room_index = (self.selected_room_index - 1) % len(self.room_manager.rooms)
                    elif event.key == pygame.K_DOWN:
                        self.selected_room_index = (self.selected_room_index + 1) % len(self.room_manager.rooms)
                    elif event.key == pygame.K_RETURN:
                        # Enter/view the selected room
                        room = self.room_manager.rooms[self.selected_room_index]
                        return {'action': 'enter_room', 'room': room}
                    elif event.key == pygame.K_DELETE:
                        # Delete selected room
                        room = self.room_manager.rooms[self.selected_room_index]
                        self.room_manager.delete_room(room)
                        if len(self.room_manager.rooms) > 0:
                            self.selected_room_index = min(self.selected_room_index, len(self.room_manager.rooms) - 1)
            
            # Manage groups view
            elif self.current_view == 'groups':
                fields = ['create_group', 'delete_group', 'back']
                
                if event.key == pygame.K_UP:
                    self.selected_field = (self.selected_field - 1) % len(fields)
                elif event.key == pygame.K_DOWN:
                    self.selected_field = (self.selected_field + 1) % len(fields)
                elif event.key == pygame.K_RETURN:
                    field = fields[self.selected_field]
                    if field == 'create_group':
                        self.editing_text = True
                        self.text_field = 'group_name'
                        self.text_input = ""
                    elif field == 'delete_group':
                        if len(self.room_manager.groups) > 1:
                            if self.selected_group_index < len(self.room_manager.groups):
                                group = self.room_manager.groups[self.selected_group_index]
                                self.room_manager.delete_group(group)
                                self.selected_group_index = min(self.selected_group_index, len(self.room_manager.groups) - 1)
                    elif field == 'back':
                        self.current_view = 'main'
                        self.selected_field = 0
                elif event.key == pygame.K_LEFT and self.selected_field == 1:
                    self.selected_group_index = (self.selected_group_index - 1) % len(self.room_manager.groups)
                elif event.key == pygame.K_RIGHT and self.selected_field == 1:
                    self.selected_group_index = (self.selected_group_index + 1) % len(self.room_manager.groups)
        
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
        menu_width = 550
        menu_height = 500
        menu_x = (self.screen_width - menu_width) // 2
        menu_y = (self.screen_height - menu_height) // 2
        
        menu_rect = pygame.Rect(menu_x, menu_y, menu_width, menu_height)
        pygame.draw.rect(screen, colors['DARK_GRAY'], menu_rect)
        pygame.draw.rect(screen, colors['PURPLE'], menu_rect, 3)
        
        # Title based on view
        if self.current_view == 'main':
            title_text = "ROOM EDITOR"
        elif self.current_view == 'create':
            title_text = "CREATE NEW ROOM"
        elif self.current_view == 'list':
            title_text = "ROOM LIST"
        elif self.current_view == 'groups':
            title_text = "MANAGE GROUPS"
        
        title = self.font_medium.render(title_text, True, colors['PURPLE'])
        title_rect = title.get_rect(center=(self.screen_width // 2, menu_y + 20))
        screen.blit(title, title_rect)
        
        y_offset = menu_y + 55
        
        # Draw based on current view
        if self.current_view == 'main':
            fields = ['create_room', 'list_rooms', 'manage_groups', 'back']
            labels = {
                'create_room': 'Create New Room',
                'list_rooms': f'List Rooms ({len(self.room_manager.rooms)})',
                'manage_groups': f'Manage Groups ({len(self.room_manager.groups)})',
                'back': 'Back to Dev Menu'
            }
            
            for i, field in enumerate(fields):
                is_selected = (i == self.selected_field)
                color = colors['YELLOW'] if is_selected else colors['WHITE']
                prefix = "> " if is_selected else "  "
                text = f"{prefix}{labels[field]}"
                text_surface = self.font_small.render(text, True, color)
                screen.blit(text_surface, (menu_x + 20, y_offset))
                y_offset += 40
        
        elif self.current_view == 'create':
            fields = ['room_name', 'room_width', 'room_height', 'select_group', 'confirm', 'cancel']
            
            for i, field in enumerate(fields):
                is_selected = (i == self.selected_field)
                color = colors['YELLOW'] if is_selected else colors['WHITE']
                prefix = "> " if is_selected else "  "
                
                if field == 'room_name':
                    text = f"{prefix}Name: {self.new_room_name}"
                elif field == 'room_width':
                    text = f"{prefix}Width: {self.new_room_width}"
                elif field == 'room_height':
                    text = f"{prefix}Height: {self.new_room_height}"
                elif field == 'select_group':
                    text = f"{prefix}Group: {self.new_room_group} (click to cycle)"
                elif field == 'confirm':
                    text = f"{prefix}CREATE ROOM"
                elif field == 'cancel':
                    text = f"{prefix}Cancel"
                
                text_surface = self.font_small.render(text, True, color)
                screen.blit(text_surface, (menu_x + 20, y_offset))
                y_offset += 40
        
        elif self.current_view == 'list':
            if len(self.room_manager.rooms) == 0:
                empty_text = self.font_medium.render("No rooms created yet", True, colors['LIGHT_GRAY'])
                empty_rect = empty_text.get_rect(center=(self.screen_width // 2, menu_y + 200))
                screen.blit(empty_text, empty_rect)
                
                hint_text = self.font_small.render("Press ENTER to go back", True, colors['LIGHT_GRAY'])
                hint_rect = hint_text.get_rect(center=(self.screen_width // 2, menu_y + 250))
                screen.blit(hint_text, hint_rect)
            else:
                # Group rooms by group
                for group in self.room_manager.groups:
                    rooms_in_group = self.room_manager.get_rooms_in_group(group)
                    if rooms_in_group:
                        # Group header
                        group_text = self.font_small.render(f"[{group}]", True, colors['CYAN'])
                        screen.blit(group_text, (menu_x + 20, y_offset))
                        y_offset += 25
                        
                        for room in rooms_in_group:
                            room_idx = self.room_manager.rooms.index(room)
                            is_selected = (room_idx == self.selected_room_index)
                            color = colors['YELLOW'] if is_selected else colors['WHITE']
                            prefix = "  > " if is_selected else "    "
                            
                            text = f"{prefix}{room.name} ({room.width}x{room.height})"
                            text_surface = self.font_small.render(text, True, color)
                            screen.blit(text_surface, (menu_x + 20, y_offset))
                            y_offset += 25
                        
                        y_offset += 10
                
                # Instructions
                inst_text = self.font_small.render("ENTER: Enter Room | DELETE: Delete Room", True, colors['LIGHT_GRAY'])
                inst_rect = inst_text.get_rect(center=(self.screen_width // 2, menu_y + menu_height - 30))
                screen.blit(inst_text, inst_rect)
        
        elif self.current_view == 'groups':
            fields = ['create_group', 'delete_group', 'back']
            
            for i, field in enumerate(fields):
                is_selected = (i == self.selected_field)
                color = colors['YELLOW'] if is_selected else colors['WHITE']
                prefix = "> " if is_selected else "  "
                
                if field == 'create_group':
                    text = f"{prefix}Create New Group"
                elif field == 'delete_group':
                    if len(self.room_manager.groups) > 0:
                        selected_group = self.room_manager.groups[self.selected_group_index]
                        text = f"{prefix}Delete Group: [{selected_group}] (LEFT/RIGHT to select)"
                    else:
                        text = f"{prefix}Delete Group: (none)"
                elif field == 'back':
                    text = f"{prefix}Back"
                
                text_surface = self.font_small.render(text, True, color)
                screen.blit(text_surface, (menu_x + 20, y_offset))
                y_offset += 40
            
            # Show all groups
            y_offset += 20
            groups_title = self.font_small.render("Existing Groups:", True, colors['CYAN'])
            screen.blit(groups_title, (menu_x + 20, y_offset))
            y_offset += 25
            
            for i, group in enumerate(self.room_manager.groups):
                group_color = colors['YELLOW'] if i == self.selected_group_index else colors['WHITE']
                group_text = self.font_small.render(f"  - {group}", True, group_color)
                screen.blit(group_text, (menu_x + 20, y_offset))
                y_offset += 22
        
        # Text input overlay
        if self.editing_text:
            input_y = menu_y + menu_height - 60
            input_rect = pygame.Rect(menu_x + 20, input_y, menu_width - 40, 30)
            pygame.draw.rect(screen, colors['BLACK'], input_rect)
            pygame.draw.rect(screen, colors['YELLOW'], input_rect, 2)
            
            input_text = self.font_small.render(self.text_input + "_", True, colors['WHITE'])
            screen.blit(input_text, (menu_x + 25, input_y + 5))
        else:
            # General instructions
            inst_text = self.font_small.render("UP/DOWN: Navigate | ENTER: Select | ESC: Back", True, colors['LIGHT_GRAY'])
            inst_rect = inst_text.get_rect(center=(self.screen_width // 2, menu_y + menu_height - 30))
            screen.blit(inst_text, inst_rect)
