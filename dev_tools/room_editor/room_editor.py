import pygame
import pygame.gfxdraw
import math
from core.camera import Camera
from config.settings import RENDER_SCALE, TILE_SIZE
from dev_tools.room_editor.room_editor_tools.object_editor import ObjectEditor


class RoomEditor:
    """Fullscreen interface for creating and managing game rooms"""

    def __init__(self, room_manager, screen_width, screen_height):
        self.room_manager = room_manager
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False

        # Set up fonts
        self.font_title = pygame.font.Font(None, 48)
        self.font_large = pygame.font.Font(None, 32)
        self.font_medium = pygame.font.Font(None, 24)
        self.font_small = pygame.font.Font(None, 18)

        # Track which view we're in
        self.current_view = 'groups'
        self.selected_index = 0
        self.scroll_offset = 0
        self.selected_group = None

        # Room viewing with camera
        self.viewing_room = None
        self.camera = Camera(screen_width, screen_height)
        self.camera_speed = 300
        self.camera_fast_speed = 600

        # Editor tools
        self.tileset_editor = None
        self.object_editor = None

        # Toolbar setup
        from dev_tools.room_editor.room_editor_tools.editor_toolbar import EditorToolbar
        self.toolbar = EditorToolbar(screen_width, screen_height)

        # Text input handling
        self.editing_field = None
        self.text_input = ""
        self.cursor_blink = 0

        # Form for creating new rooms
        self.create_form = {
            'name': '',
            'width': '2400',
            'height': '1800',
            'group': 'Default'
        }
        self.create_form_fields = ['name', 'width', 'height', 'group', 'create', 'cancel']

        # Currently editing room
        self.editing_room = None

        # Animation timers
        self.anim_timer = 0
        self.hover_anim = [0.0] * 20

        # Color scheme
        self.colors = {
            'bg': (15, 15, 25),
            'panel': (25, 25, 40),
            'panel_light': (35, 35, 55),
            'accent': (255, 215, 0),
            'accent_dim': (200, 170, 0),
            'text': (255, 255, 255),
            'text_dim': (180, 180, 200),
            'text_dark': (120, 120, 140),
            'success': (100, 255, 100),
            'danger': (255, 100, 100),
            'grid': (40, 40, 60)
        }

        # Layout dimensions
        self.sidebar_width = 280
        self.header_height = 80
        self.item_height = 60
        self.padding = 20

    def toggle(self):
        """Open or close the room editor"""
        self.active = not self.active
        if self.active:
            self.current_view = 'groups'
            self.selected_index = 0
            self.scroll_offset = 0
            self.editing_field = None
            self.selected_group = None

            # Load up the tileset editor if we haven't yet
            if self.tileset_editor is None:
                from dev_tools.room_editor.room_editor_tools.tileset_editor import TilesetEditor
                self.tileset_editor = TilesetEditor(self.screen_width, self.screen_height)

            # Same for object editor
            if self.object_editor is None:
                self.object_editor = ObjectEditor(
                    self.screen_width,
                    self.screen_height,
                    self.room_manager
                )

    def handle_input(self, event):
        """Process input events"""
        if not self.active:
            return None

        # Handle typing into text fields
        if self.editing_field is not None:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    self._finish_text_input()
                elif event.key == pygame.K_ESCAPE:
                    self.editing_field = None
                    self.text_input = ""
                elif event.key == pygame.K_BACKSPACE:
                    self.text_input = self.text_input[:-1]
                elif event.key == pygame.K_TAB:
                    self._finish_text_input()
                    self._next_form_field()
                else:
                    if len(self.text_input) < 50 and event.unicode.isprintable():
                        self.text_input += event.unicode
            return None

        # Room viewing mode gets special treatment (includes mouse)
        if self.current_view == 'view_room':
            return self._handle_view_room_input(event)

        # Regular navigation (keyboard only)
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if self.current_view == 'groups':
                    self.active = False
                    return 'close'
                elif self.current_view == 'rooms':
                    self.current_view = 'groups'
                    self.selected_index = 0
                    self.selected_group = None
                else:
                    if self.selected_group:
                        self.current_view = 'rooms'
                    else:
                        self.current_view = 'groups'
                    self.selected_index = 0
                return None

            if self.current_view == 'groups':
                return self._handle_groups_input(event)
            elif self.current_view == 'rooms':
                return self._handle_rooms_input(event)
            elif self.current_view == 'create':
                return self._handle_create_input(event)
            elif self.current_view == 'edit':
                return self._handle_edit_input(event)

        return None

    def _handle_groups_input(self, event):
        """Navigate through groups"""
        total_items = len(self.room_manager.groups) + 2

        if event.key in (pygame.K_UP, pygame.K_w):
            self.selected_index = (self.selected_index - 1) % total_items
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.selected_index = (self.selected_index + 1) % total_items
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
            if self.selected_index < len(self.room_manager.groups):
                self.selected_group = self.room_manager.groups[self.selected_index]
                self.current_view = 'rooms'
                self.selected_index = 0
            elif self.selected_index == len(self.room_manager.groups):
                self.editing_field = 'new_group'
                self.text_input = ""
            elif self.selected_index == len(self.room_manager.groups) + 1:
                self.active = False
                return 'close'
        elif event.key == pygame.K_DELETE:
            if 0 <= self.selected_index < len(self.room_manager.groups):
                group_name = self.room_manager.groups[self.selected_index]
                if group_name != "Default":
                    self.room_manager.delete_group(group_name)
                    self.selected_index = min(self.selected_index, len(self.room_manager.groups))

        return None

    def _handle_rooms_input(self, event):
        """Navigate through rooms in the selected group"""
        if not self.selected_group:
            return None

        rooms_in_group = self.room_manager.get_rooms_in_group(self.selected_group)
        total_items = len(rooms_in_group) + 2

        if event.key in (pygame.K_UP, pygame.K_w):
            self.selected_index = (self.selected_index - 1) % total_items
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.selected_index = (self.selected_index + 1) % total_items
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
            if self.selected_index < len(rooms_in_group):
                self.editing_room = rooms_in_group[self.selected_index]
                self.current_view = 'edit'
                self.selected_index = 0
            elif self.selected_index == len(rooms_in_group):
                self.current_view = 'create'
                self.selected_index = 0
                self.create_form = {
                    'name': '',
                    'width': '2400',
                    'height': '1800',
                    'group': self.selected_group
                }
            elif self.selected_index == len(rooms_in_group) + 1:
                self.current_view = 'groups'
                self.selected_index = 0
                self.selected_group = None
        elif event.key == pygame.K_v:
            # Enter the room viewer
            if 0 <= self.selected_index < len(rooms_in_group):
                self.viewing_room = rooms_in_group[self.selected_index]
                room_name = self.viewing_room.name

                # Load tiles from the room if the editor doesn't have them yet
                if self.tileset_editor:
                    if room_name not in self.tileset_editor.room_tiles or not self.tileset_editor.room_tiles[room_name]:
                        self.tileset_editor.room_tiles[room_name] = self.viewing_room.tiles[:]

                # Sync collision objects from room data
                if self.object_editor and hasattr(self.object_editor, 'collision_manager'):
                    self.object_editor.collision_manager.collision_objects[room_name] = []

                    if not hasattr(self.viewing_room, 'collision_objects'):
                        self.viewing_room.collision_objects = []

                    self.object_editor.collision_manager.collision_objects[room_name] = self.viewing_room.collision_objects

                # Make sure destructible stones list exists
                if not hasattr(self.viewing_room, 'destructible_stones'):
                    self.viewing_room.destructible_stones = []

                # Center the camera
                center_x = (self.viewing_room.width - self.screen_width) // 2
                center_y = (self.viewing_room.height - self.screen_height) // 2
                self.current_view = 'view_room'
        elif event.key == pygame.K_DELETE:
            if 0 <= self.selected_index < len(rooms_in_group):
                room_to_delete = rooms_in_group[self.selected_index]
                self.room_manager.delete_room(room_to_delete)
                self.selected_index = min(self.selected_index, len(rooms_in_group))

        return None

    def _handle_create_input(self, event):
        """Handle form inputs for creating a room"""
        if event.key in (pygame.K_UP, pygame.K_w):
            self.selected_index = (self.selected_index - 1) % len(self.create_form_fields)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.selected_index = (self.selected_index + 1) % len(self.create_form_fields)
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
            field = self.create_form_fields[self.selected_index]
            if field == 'create':
                self._create_room()
            elif field == 'cancel':
                self.current_view = 'rooms'
                self.selected_index = 0
            elif field == 'group':
                # Cycle through groups
                current_group = self.create_form['group']
                groups = self.room_manager.groups
                current_idx = groups.index(current_group) if current_group in groups else 0
                next_idx = (current_idx + 1) % len(groups)
                self.create_form['group'] = groups[next_idx]
            else:
                self.editing_field = field
                self.text_input = self.create_form[field]

        return None

    def _handle_edit_input(self, event):
        """Handle form inputs for editing a room"""
        edit_fields = ['name', 'width', 'height', 'group', 'save', 'delete', 'cancel']

        if event.key in (pygame.K_UP, pygame.K_w):
            self.selected_index = (self.selected_index - 1) % len(edit_fields)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.selected_index = (self.selected_index + 1) % len(edit_fields)
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
            field = edit_fields[self.selected_index]
            if field == 'save':
                self.current_view = 'rooms'
                self.selected_index = 0
            elif field == 'delete':
                self.room_manager.delete_room(self.editing_room)
                self.current_view = 'rooms'
                self.selected_index = 0
            elif field == 'cancel':
                self.current_view = 'rooms'
                self.selected_index = 0
            elif field == 'group':
                groups = self.room_manager.groups
                current_idx = groups.index(self.editing_room.group) if self.editing_room.group in groups else 0
                next_idx = (current_idx + 1) % len(groups)
                self.editing_room.group = groups[next_idx]
            else:
                self.editing_field = field
                if field == 'name':
                    self.text_input = self.editing_room.name
                elif field == 'width':
                    self.text_input = str(self.editing_room.width)
                elif field == 'height':
                    self.text_input = str(self.editing_room.height)

        return None

    def _handle_view_room_input(self, event):
        """Handle inputs while viewing/editing a room"""
        # Check for toolbar clicks
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            result = self.toolbar.handle_click(event.pos)
            if result:
                if result == 'tiles':
                    if self.tileset_editor:
                        self.tileset_editor.toggle()
                        if self.object_editor and self.object_editor.active:
                            self.object_editor.toggle()
                elif result == 'objects':
                    if self.object_editor:
                        self.object_editor.toggle()
                        if self.tileset_editor and self.tileset_editor.active:
                            self.tileset_editor.toggle()
                elif result == 'settings':
                    self.editing_room = self.viewing_room
                    self.current_view = 'edit'
                    self.selected_index = 0
                elif result == 'action_test':
                    self.active = False
                    return f'test_room:{self.viewing_room.name}'
                elif result == 'action_save':
                    self._save_current_room()
                return None

        # Toggle editors with function keys
        if event.type == pygame.KEYDOWN and event.key == pygame.K_F2:
            if self.tileset_editor:
                self.tileset_editor.toggle()
                if self.object_editor and self.object_editor.active:
                    self.object_editor.toggle()
            return None

        if event.type == pygame.KEYDOWN and event.key == pygame.K_F3:
            if self.object_editor:
                self.object_editor.toggle()
                if self.tileset_editor and self.tileset_editor.active:
                    self.tileset_editor.toggle()
            return None

        # Pass input to active editor
        if self.tileset_editor and self.tileset_editor.active:
            self.tileset_editor.handle_input(
                event,
                int(self.camera.x),
                int(self.camera.y),
                self.viewing_room.name if self.viewing_room else ""
            )
            return None

        if self.object_editor and self.object_editor.active:
            self.object_editor.handle_input(
                event,
                int(self.camera.x),
                int(self.camera.y),
                self.viewing_room.name if self.viewing_room else ""
            )
            return None

        # Exit the room viewer
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._save_current_room()
                self.current_view = 'rooms'
                self.viewing_room = None
                self.selected_index = 0

            return None

    def _finish_text_input(self):
        """Apply the text we just typed"""
        if self.editing_field is None:
            return

        if self.current_view == 'create':
            if self.editing_field in self.create_form:
                self.create_form[self.editing_field] = self.text_input
        elif self.current_view == 'edit':
            if self.editing_field == 'name':
                self.editing_room.name = self.text_input
            elif self.editing_field == 'width':
                try:
                    self.editing_room.width = int(self.text_input)
                except ValueError:
                    pass
            elif self.editing_field == 'height':
                try:
                    self.editing_room.height = int(self.text_input)
                except ValueError:
                    pass
        elif self.current_view == 'groups':
            if self.editing_field == 'new_group' and self.text_input.strip():
                self.room_manager.create_group(self.text_input.strip())

        self.editing_field = None
        self.text_input = ""

    def _next_form_field(self):
        """Jump to the next field in the form"""
        if self.current_view == 'create':
            self.selected_index = (self.selected_index + 1) % len(self.create_form_fields)
            field = self.create_form_fields[self.selected_index]
            if field not in ['create', 'cancel', 'group']:
                self.editing_field = field
                self.text_input = self.create_form[field]

    def _create_room(self):
        """Actually create the new room"""
        try:
            name = self.create_form['name'].strip()
            if not name:
                return

            width = int(self.create_form['width'])
            height = int(self.create_form['height'])
            group = self.create_form['group']

            self.room_manager.create_room(name, width, height, group)
            self.current_view = 'rooms'
            self.selected_index = 0
        except ValueError:
            pass

    def _save_current_room(self):
        """Save everything in the current room to disk"""
        if not self.viewing_room:
            return

        # Move tiles from editor to room
        if self.tileset_editor and hasattr(self.tileset_editor, 'room_tiles'):
            self.viewing_room.tiles = self.tileset_editor.room_tiles.get(
                self.viewing_room.name, []
            )

        # Move collision objects from editor to room
        if self.object_editor and hasattr(self.object_editor, 'collision_manager'):
            collision_objects = self.object_editor.collision_manager.get_collision_objects(
                self.viewing_room.name
            )
            self.viewing_room.collision_objects = collision_objects
        else:
            if not hasattr(self.viewing_room, 'collision_objects'):
                self.viewing_room.collision_objects = []

        # Move spawn points from editor to room
        if self.object_editor and hasattr(self.object_editor, 'spawn_manager'):
            spawn_obj = self.object_editor.spawn_manager.get_spawn_point(self.viewing_room.name)
            if spawn_obj:
                self.viewing_room.spawn_points = [spawn_obj]
            else:
                self.viewing_room.spawn_points = []

        # Make sure destructible stones exist
        if not hasattr(self.viewing_room, 'destructible_stones'):
            self.viewing_room.destructible_stones = []

        # Write everything to disk
        self.room_manager.save_room(self.viewing_room)

    def save_all_editor_data_to_rooms(self):
        """Move all editor data back to room objects before closing"""
        transferred_count = 0

        # Transfer tiles
        if self.tileset_editor and hasattr(self.tileset_editor, 'room_tiles'):
            for room_name, tiles in self.tileset_editor.room_tiles.items():
                if tiles:
                    room = self.room_manager.get_room_by_name(room_name)
                    if room:
                        room.tiles = tiles
                        transferred_count += 1

        # Transfer objects
        if self.object_editor:
            # Collision objects
            if hasattr(self.object_editor, 'collision_manager'):
                for room in self.room_manager.rooms:
                    objects = self.object_editor.collision_manager.get_collision_objects(room.name)
                    if objects:
                        room.collision_objects = objects
                        transferred_count += 1

            # Destructible stones
            if hasattr(self.object_editor, 'stone_manager'):
                for room in self.room_manager.rooms:
                    stones = self.object_editor.stone_manager.get_stones(room.name)
                    if stones:
                        room.destructible_stones = stones
                        transferred_count += 1

            # Spawn points
            if hasattr(self.object_editor, 'spawn_manager'):
                for room in self.room_manager.rooms:
                    spawn_obj = self.object_editor.spawn_manager.get_spawn_point(room.name)
                    if spawn_obj:
                        room.spawn_points = [spawn_obj]
                        transferred_count += 1

        return transferred_count

    def update(self, dt):
        """Update animations and camera"""
        if not self.active:
            return

        self.anim_timer += dt
        self.cursor_blink += dt

        # Smooth hover animations
        for i in range(len(self.hover_anim)):
            if i == self.selected_index:
                self.hover_anim[i] = min(1.0, self.hover_anim[i] + dt * 5)
            else:
                self.hover_anim[i] = max(0.0, self.hover_anim[i] - dt * 5)

        # Update toolbar in room view
        if self.current_view == 'view_room':
            mouse_pos = pygame.mouse.get_pos()
            self.toolbar.update(dt, mouse_pos)

            if self.object_editor and self.object_editor.active:
                self.object_editor.update(
                    dt,
                    mouse_pos,
                    int(self.camera.x),
                    int(self.camera.y)
                )

        # Handle camera movement
        if self.current_view == 'view_room' and self.viewing_room:
            mouse_pos = pygame.mouse.get_pos()
            mouse_over_palette = False

            # Check if mouse is hovering over an editor palette
            if self.tileset_editor and self.tileset_editor.active:
                mouse_over_palette = self.tileset_editor._is_in_palette(mouse_pos[0], mouse_pos[1])
            elif self.object_editor and self.object_editor.active:
                mouse_over_palette = self.object_editor._is_in_palette(mouse_pos[0], mouse_pos[1])

            # Only move camera if not hovering over palette
            if not mouse_over_palette:
                keys = pygame.key.get_pressed()

                # Faster movement with shift held
                speed = self.camera_fast_speed if (keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]) else self.camera_speed

                if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                    self.camera.x -= speed * dt
                if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                    self.camera.x += speed * dt
                if keys[pygame.K_UP] or keys[pygame.K_w]:
                    self.camera.y -= speed * dt
                if keys[pygame.K_DOWN] or keys[pygame.K_s]:
                    self.camera.y += speed * dt

            # Keep camera inside the room bounds
            self.camera.x = max(0, min(self.camera.x, (self.viewing_room.width * RENDER_SCALE) - self.screen_width))
            self.camera.y = max(0, min(self.camera.y, (self.viewing_room.height * RENDER_SCALE) - self.screen_height))

    def draw(self, screen):
        """Draw the current view"""
        if not self.active:
            return

        # Room view gets special rendering
        if self.current_view == 'view_room':
            self._draw_view_room(screen)
            return

        # Draw menu interface
        self._draw_background(screen)
        self._draw_sidebar(screen)

        if self.current_view == 'groups':
            self._draw_groups_view(screen)
        elif self.current_view == 'rooms':
            self._draw_rooms_view(screen)
        elif self.current_view == 'create':
            self._draw_create_view(screen)
        elif self.current_view == 'edit':
            self._draw_edit_view(screen)

        if self.editing_field is not None:
            self._draw_text_input_overlay(screen)

    def _draw_view_room(self, screen):
        """Render the room with all its layers and editors"""
        if not self.viewing_room:
            return

        screen.fill((34, 139, 34))

        # Background tiles first
        if self.tileset_editor:
            self.tileset_editor.draw_tiles(
                screen,
                int(self.camera.x),
                int(self.camera.y),
                self.viewing_room.name,
                layer='background'
            )

        # Draw the grid
        if self.tileset_editor and self.tileset_editor.active and self.tileset_editor.show_grid:
            self.tileset_editor.draw_grid(
                screen,
                int(self.camera.x),
                int(self.camera.y),
                self.viewing_room.width,
                self.viewing_room.height
            )
        elif self.object_editor and self.object_editor.active and self.object_editor.show_grid:
            self._draw_default_grid(screen)
        else:
            self._draw_default_grid(screen)

        # Room boundary outline
        room_rect_x = (0 * RENDER_SCALE) - self.camera.x
        room_rect_y = (0 * RENDER_SCALE) - self.camera.y
        room_width = self.viewing_room.width * RENDER_SCALE
        room_height = self.viewing_room.height * RENDER_SCALE
        pygame.draw.rect(screen, self.colors['accent'],
                         (int(room_rect_x), int(room_rect_y), int(room_width), int(room_height)), 3)

        # Draw spawn points
        if self.object_editor:
            self.object_editor.draw_spawn_points(
                screen,
                int(self.camera.x),
                int(self.camera.y)
            )

        # Draw collision objects
        if self.object_editor:
            self.object_editor.current_room_name = self.viewing_room.name
            self.object_editor.draw_collision_objects(
                screen,
                int(self.camera.x),
                int(self.camera.y)
            )

        # Draw destructible stones
        if hasattr(self.viewing_room, 'destructible_stones'):
            for stone in self.viewing_room.destructible_stones:
                if stone.active:
                    stone.draw(screen, self.camera, self.colors)

        # Foreground tiles on top
        if self.tileset_editor:
            self.tileset_editor.draw_tiles(
                screen,
                int(self.camera.x),
                int(self.camera.y),
                self.viewing_room.name,
                layer='foreground'
            )

        # Editor previews
        if self.object_editor and self.object_editor.active:
            self.object_editor.draw_preview(
                screen,
                int(self.camera.x),
                int(self.camera.y)
            )

        if self.tileset_editor and self.tileset_editor.active:
            self.tileset_editor.draw_tile_preview(
                screen,
                int(self.camera.x),
                int(self.camera.y)
            )

        # Toolbar and palettes on top of everything
        self.toolbar.draw(screen)

        if self.tileset_editor and self.tileset_editor.active:
            self.tileset_editor.draw_palette(screen)

        if self.object_editor and self.object_editor.active:
            self.object_editor.draw_palette(screen)

    def _draw_default_grid(self, screen):
        """Draw a basic grid when no editor is active"""
        visible_x_start = self.camera.x // RENDER_SCALE
        visible_y_start = self.camera.y // RENDER_SCALE
        visible_x_end = (self.camera.x + self.screen_width) // RENDER_SCALE
        visible_y_end = (self.camera.y + self.screen_height) // RENDER_SCALE

        start_x = int((visible_x_start // TILE_SIZE)) * TILE_SIZE
        end_x = int(visible_x_end + TILE_SIZE)

        for x in range(start_x, end_x, TILE_SIZE):
            screen_x = (x * RENDER_SCALE) - self.camera.x
            if -TILE_SIZE * RENDER_SCALE <= screen_x <= self.screen_width:
                pygame.draw.line(screen, (44, 149, 44),
                                 (int(screen_x), 0),
                                 (int(screen_x), self.screen_height), 1)

        start_y = int((visible_y_start // TILE_SIZE)) * TILE_SIZE
        end_y = int(visible_y_end + TILE_SIZE)

        for y in range(start_y, end_y, TILE_SIZE):
            screen_y = (y * RENDER_SCALE) - self.camera.y
            if -TILE_SIZE * RENDER_SCALE <= screen_y <= self.screen_height:
                pygame.draw.line(screen, (44, 149, 44),
                                 (0, int(screen_y)),
                                 (self.screen_width, int(screen_y)), 1)

    def _draw_background(self, screen):
        """Draw the menu background with gradient and animated grid"""
        # Smooth gradient from top to bottom
        for y in range(self.screen_height):
            progress = y / self.screen_height
            r = int(self.colors['bg'][0] + (self.colors['panel'][0] - self.colors['bg'][0]) * progress)
            g = int(self.colors['bg'][1] + (self.colors['panel'][1] - self.colors['bg'][1]) * progress)
            b = int(self.colors['bg'][2] + (self.colors['panel'][2] - self.colors['bg'][2]) * progress)
            pygame.draw.line(screen, (r, g, b), (0, y), (self.screen_width, y))

        # Animated grid pattern
        offset = int(self.anim_timer * 20) % (TILE_SIZE * 2)
        for x in range(-offset, self.screen_width, TILE_SIZE * 2):
            pygame.draw.line(screen, self.colors['grid'], (x, 0), (x, self.screen_height), 1)
        for y in range(-offset, self.screen_height, TILE_SIZE * 2):
            pygame.draw.line(screen, self.colors['grid'], (0, y), (self.screen_width, y), 1)

    def _draw_sidebar(self, screen):
        """Draw the info sidebar"""
        sidebar_rect = pygame.Rect(0, 0, self.sidebar_width, self.screen_height)
        pygame.draw.rect(screen, self.colors['panel'], sidebar_rect)

        # Nice glow on the right edge
        for i in range(5):
            alpha = 100 - i * 20
            color = (*self.colors['accent'], alpha)
            surf = pygame.Surface((2, self.screen_height), pygame.SRCALPHA)
            surf.fill(color)
            screen.blit(surf, (self.sidebar_width - i, 0))

        y_pos = self.padding

        # Title
        title = self.font_large.render("ROOM EDITOR", True, self.colors['accent'])
        screen.blit(title, (self.padding, y_pos))
        y_pos += 50

        pygame.draw.line(screen, self.colors['accent'],
                         (self.padding, y_pos),
                         (self.sidebar_width - self.padding, y_pos), 2)
        y_pos += 30

        # Quick stats
        stats = [
            ("Total Rooms", len(self.room_manager.rooms)),
            ("Groups", len(self.room_manager.groups)),
            ("Current Room", self.room_manager.current_room.name if self.room_manager.current_room else "None")
        ]

        for label, value in stats:
            label_surf = self.font_small.render(label, True, self.colors['text_dim'])
            screen.blit(label_surf, (self.padding, y_pos))
            y_pos += 20

            value_surf = self.font_medium.render(str(value), True, self.colors['text'])
            screen.blit(value_surf, (self.padding + 10, y_pos))
            y_pos += 35

        # What view are we in?
        y_pos = self.screen_height - 100
        pygame.draw.line(screen, self.colors['accent'],
                         (self.padding, y_pos),
                         (self.sidebar_width - self.padding, y_pos), 2)
        y_pos += 20

        view_text = {
            'groups': 'Select Group',
            'rooms': 'Group Rooms',
            'create': 'Create Room',
            'edit': 'Edit Room',
            'view_room': 'Viewing Room'
        }

        view_label = self.font_small.render("Current View:", True, self.colors['text_dim'])
        screen.blit(view_label, (self.padding, y_pos))
        y_pos += 20

        view_value = self.font_medium.render(view_text[self.current_view], True, self.colors['accent'])
        screen.blit(view_value, (self.padding, y_pos))

    def _draw_groups_view(self, screen):
        """Show the list of groups"""
        content_x = self.sidebar_width + self.padding
        content_y = self.header_height
        content_width = self.screen_width - self.sidebar_width - self.padding * 2

        header = self.font_title.render("Select Group", True, self.colors['text'])
        header_shadow = self.font_title.render("Select Group", True, (0, 0, 0))
        screen.blit(header_shadow, (content_x + 2, self.padding + 2))
        screen.blit(header, (content_x, self.padding))

        y_pos = content_y

        for i, group_name in enumerate(self.room_manager.groups):
            is_selected = (i == self.selected_index)
            self._draw_group_item(screen, group_name, content_x, y_pos, content_width, is_selected, i)
            y_pos += self.item_height + 10

        buttons = [
            ("+ Create New Group", self.colors['success']),
            ("← Back to Menu", self.colors['text_dim'])
        ]

        for j, (label, color) in enumerate(buttons):
            i = len(self.room_manager.groups) + j
            is_selected = (i == self.selected_index)
            self._draw_button(screen, label, content_x, y_pos, content_width, is_selected, color, i)
            y_pos += self.item_height + 10

    def _draw_rooms_view(self, screen):
        """Show the list of rooms in the selected group"""
        if not self.selected_group:
            return

        content_x = self.sidebar_width + self.padding
        content_y = self.header_height
        content_width = self.screen_width - self.sidebar_width - self.padding * 2

        header = self.font_title.render(f"{self.selected_group} - Rooms", True, self.colors['text'])
        header_shadow = self.font_title.render(f"{self.selected_group} - Rooms", True, (0, 0, 0))
        screen.blit(header_shadow, (content_x + 2, self.padding + 2))
        screen.blit(header, (content_x, self.padding))

        rooms_in_group = self.room_manager.get_rooms_in_group(self.selected_group)
        y_pos = content_y

        for i, room in enumerate(rooms_in_group):
            is_selected = (i == self.selected_index)
            self._draw_room_item(screen, room, content_x, y_pos, content_width, is_selected, i)
            y_pos += self.item_height + 10

        buttons = [
            ("+ Create New Room", self.colors['success']),
            ("← Back to Groups", self.colors['text_dim'])
        ]

        for j, (label, color) in enumerate(buttons):
            i = len(rooms_in_group) + j
            is_selected = (i == self.selected_index)
            self._draw_button(screen, label, content_x, y_pos, content_width, is_selected, color, i)
            y_pos += self.item_height + 10

    def _draw_room_item(self, screen, room, x, y, width, selected, index):
        """Draw a single room in the list"""
        panel_rect = pygame.Rect(x, y, width, self.item_height)

        if selected:
            # Pulsing glow effect
            glow_alpha = int(50 + 30 * math.sin(self.anim_timer * 3))
            glow_surf = pygame.Surface((width + 10, self.item_height + 10), pygame.SRCALPHA)
            pygame.draw.rect(glow_surf, (*self.colors['accent'], glow_alpha),
                             (0, 0, width + 10, self.item_height + 10), border_radius=8)
            screen.blit(glow_surf, (x - 5, y - 5))

        color = self.colors['panel_light'] if selected else self.colors['panel']
        pygame.draw.rect(screen, color, panel_rect, border_radius=8)
        pygame.draw.rect(screen, self.colors['accent'] if selected else self.colors['grid'],
                         panel_rect, 2, border_radius=8)

        # Little colored circle for the group
        icon_x = x + 20
        icon_y = y + self.item_height // 2
        icon_radius = 12

        group_hash = hash(room.group) % 360
        icon_color = self._hue_to_rgb(group_hash)

        pygame.gfxdraw.filled_circle(screen, icon_x, icon_y, icon_radius, icon_color)
        pygame.gfxdraw.aacircle(screen, icon_x, icon_y, icon_radius, self.colors['text'])

        # Room name and dimensions
        name_surf = self.font_large.render(room.name, True, self.colors['text'])
        screen.blit(name_surf, (x + 50, y + 8))

        details = f"{room.width}x{room.height}"
        details_surf = self.font_small.render(details, True, self.colors['text_dim'])
        screen.blit(details_surf, (x + 50, y + 35))

        # Show if this is the current room
        if self.room_manager.current_room == room:
            indicator = self.font_small.render("● CURRENT", True, self.colors['success'])
            screen.blit(indicator, (x + width - 100, y + 20))

        # Hint for selected item
        if selected:
            hint = self.font_small.render("V to View | ENTER to Edit", True, self.colors['accent'])
            screen.blit(hint, (x + width - 220, y + 40))

    def _draw_button(self, screen, label, x, y, width, selected, color, index):
        """Draw a button in the menu"""
        panel_rect = pygame.Rect(x, y, width, self.item_height)

        if selected:
            glow_alpha = int(50 + 30 * math.sin(self.anim_timer * 3))
            glow_surf = pygame.Surface((width + 10, self.item_height + 10), pygame.SRCALPHA)
            pygame.draw.rect(glow_surf, (*color, glow_alpha),
                             (0, 0, width + 10, self.item_height + 10), border_radius=8)
            screen.blit(glow_surf, (x - 5, y - 5))

        bg_color = self.colors['panel_light'] if selected else self.colors['panel']
        pygame.draw.rect(screen, bg_color, panel_rect, border_radius=8)
        pygame.draw.rect(screen, color, panel_rect, 2, border_radius=8)

        text_color = color if selected else self.colors['text_dim']
        label_surf = self.font_large.render(label, True, text_color)
        label_rect = label_surf.get_rect(center=(x + width // 2, y + self.item_height // 2))
        screen.blit(label_surf, label_rect)

    def _draw_group_item(self, screen, group_name, x, y, width, selected, index):
        """Draw a single group in the list"""
        panel_rect = pygame.Rect(x, y, width, self.item_height)

        if selected:
            glow_alpha = int(50 + 30 * math.sin(self.anim_timer * 3))
            glow_surf = pygame.Surface((width + 10, self.item_height + 10), pygame.SRCALPHA)
            pygame.draw.rect(glow_surf, (*self.colors['accent'], glow_alpha),
                             (0, 0, width + 10, self.item_height + 10), border_radius=8)
            screen.blit(glow_surf, (x - 5, y - 5))

        color = self.colors['panel_light'] if selected else self.colors['panel']
        pygame.draw.rect(screen, color, panel_rect, border_radius=8)
        pygame.draw.rect(screen, self.colors['accent'] if selected else self.colors['grid'],
                         panel_rect, 2, border_radius=8)

        # Colored icon
        icon_x = x + 20
        icon_y = y + self.item_height // 2
        icon_radius = 12

        group_hash = hash(group_name) % 360
        icon_color = self._hue_to_rgb(group_hash)

        pygame.gfxdraw.filled_circle(screen, icon_x, icon_y, icon_radius, icon_color)
        pygame.gfxdraw.aacircle(screen, icon_x, icon_y, icon_radius, self.colors['text'])

        # Group name
        name_surf = self.font_large.render(group_name, True, self.colors['text'])
        screen.blit(name_surf, (x + 50, y + 8))

        # How many rooms in this group
        room_count = len(self.room_manager.get_rooms_in_group(group_name))
        count_text = f"{room_count} room{'s' if room_count != 1 else ''}"
        count_surf = self.font_small.render(count_text, True, self.colors['text_dim'])
        screen.blit(count_surf, (x + 50, y + 35))

        # Controls hint
        if group_name != "Default" and selected:
            hint = self.font_small.render("DELETE to remove | ENTER to open", True, self.colors['accent'])
            screen.blit(hint, (x + width - 300, y + 20))
        elif selected:
            hint = self.font_small.render("ENTER to open", True, self.colors['accent'])
            screen.blit(hint, (x + width - 150, y + 20))

    def _draw_create_view(self, screen):
        """Show the create room form"""
        content_x = self.sidebar_width + self.padding * 2
        content_y = self.header_height

        header = self.font_title.render("Create New Room", True, self.colors['text'])
        screen.blit(header, (content_x, self.padding))

        y_pos = content_y
        field_width = 500

        for i, field_name in enumerate(self.create_form_fields):
            if field_name in ['create', 'cancel']:
                continue

            is_selected = (i == self.selected_index)

            label = field_name.replace('_', ' ').title()
            label_surf = self.font_medium.render(label, True, self.colors['text_dim'])
            screen.blit(label_surf, (content_x, y_pos))
            y_pos += 30

            field_rect = pygame.Rect(content_x, y_pos, field_width, 40)
            bg_color = self.colors['panel_light'] if is_selected else self.colors['panel']
            border_color = self.colors['accent'] if is_selected else self.colors['grid']

            pygame.draw.rect(screen, bg_color, field_rect, border_radius=5)
            pygame.draw.rect(screen, border_color, field_rect, 2, border_radius=5)

            if field_name == 'group':
                value = self.create_form[field_name]
                hint = " (ENTER to cycle)"
            else:
                value = self.create_form[field_name] if field_name in self.create_form else ""
                hint = " (ENTER to edit)" if is_selected else ""

            value_text = value + hint
            value_surf = self.font_medium.render(value_text, True, self.colors['text'])
            screen.blit(value_surf, (content_x + 10, y_pos + 8))

            y_pos += 60

        y_pos += 20
        buttons = [
            ('create', 'Create Room', self.colors['success']),
            ('cancel', 'Cancel', self.colors['danger'])
        ]

        for j, (btn_id, btn_label, btn_color) in enumerate(buttons):
            is_selected = (self.selected_index == self.create_form_fields.index(btn_id))

            btn_rect = pygame.Rect(content_x + j * 260, y_pos, 250, 50)
            bg_color = btn_color if is_selected else self.colors['panel']

            if is_selected:
                glow_alpha = int(50 + 30 * math.sin(self.anim_timer * 3))
                glow_surf = pygame.Surface((260, 60), pygame.SRCALPHA)
                pygame.draw.rect(glow_surf, (*btn_color, glow_alpha), (0, 0, 260, 60), border_radius=8)
                screen.blit(glow_surf, (content_x + j * 260 - 5, y_pos - 5))

            pygame.draw.rect(screen, bg_color, btn_rect, border_radius=8)
            pygame.draw.rect(screen, btn_color, btn_rect, 2, border_radius=8)

            text_color = self.colors['panel'] if is_selected else btn_color
            btn_surf = self.font_large.render(btn_label, True, text_color)
            btn_text_rect = btn_surf.get_rect(center=btn_rect.center)
            screen.blit(btn_surf, btn_text_rect)

    def _draw_edit_view(self, screen):
        """Show the edit room form"""
        if not self.editing_room:
            return

        content_x = self.sidebar_width + self.padding * 2
        content_y = self.header_height

        header = self.font_title.render(f"Edit: {self.editing_room.name}", True, self.colors['text'])
        screen.blit(header, (content_x, self.padding))

        y_pos = content_y
        field_width = 500

        fields = [
            ('name', 'Room Name', str(self.editing_room.name)),
            ('width', 'Width', str(self.editing_room.width)),
            ('height', 'Height', str(self.editing_room.height)),
            ('group', 'Group', str(self.editing_room.group))
        ]

        for i, (field_id, label, value) in enumerate(fields):
            is_selected = (i == self.selected_index)

            label_surf = self.font_medium.render(label, True, self.colors['text_dim'])
            screen.blit(label_surf, (content_x, y_pos))
            y_pos += 30

            field_rect = pygame.Rect(content_x, y_pos, field_width, 40)
            bg_color = self.colors['panel_light'] if is_selected else self.colors['panel']
            border_color = self.colors['accent'] if is_selected else self.colors['grid']

            pygame.draw.rect(screen, bg_color, field_rect, border_radius=5)
            pygame.draw.rect(screen, border_color, field_rect, 2, border_radius=5)

            hint = " (ENTER to cycle)" if field_id == 'group' else " (ENTER to edit)"
            value_text = value + (hint if is_selected else "")
            value_surf = self.font_medium.render(value_text, True, self.colors['text'])
            screen.blit(value_surf, (content_x + 10, y_pos + 8))

            y_pos += 60

        y_pos += 20
        buttons = [
            (4, 'Save', self.colors['success']),
            (5, 'Delete', self.colors['danger']),
            (6, 'Cancel', self.colors['text_dim'])
        ]

        for j, (btn_index, btn_label, btn_color) in enumerate(buttons):
            is_selected = (self.selected_index == btn_index)

            btn_rect = pygame.Rect(content_x + j * 180, y_pos, 170, 50)
            bg_color = btn_color if is_selected else self.colors['panel']

            if is_selected:
                glow_alpha = int(50 + 30 * math.sin(self.anim_timer * 3))
                glow_surf = pygame.Surface((180, 60), pygame.SRCALPHA)
                pygame.draw.rect(glow_surf, (*btn_color, glow_alpha), (0, 0, 180, 60), border_radius=8)
                screen.blit(glow_surf, (content_x + j * 180 - 5, y_pos - 5))

            pygame.draw.rect(screen, bg_color, btn_rect, border_radius=8)
            pygame.draw.rect(screen, btn_color, btn_rect, 2, border_radius=8)

            text_color = self.colors['panel'] if is_selected else btn_color
            btn_surf = self.font_large.render(btn_label, True, text_color)
            btn_text_rect = btn_surf.get_rect(center=btn_rect.center)
            screen.blit(btn_surf, btn_text_rect)

    def _draw_text_input_overlay(self, screen):
        """Show the text input modal"""
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        screen.blit(overlay, (0, 0))

        box_width = 600
        box_height = 120
        box_x = (self.screen_width - box_width) // 2
        box_y = (self.screen_height - box_height) // 2

        pygame.draw.rect(screen, self.colors['panel'], (box_x, box_y, box_width, box_height), border_radius=10)
        pygame.draw.rect(screen, self.colors['accent'], (box_x, box_y, box_width, box_height), 3, border_radius=10)

        prompt_text = "Enter group name:" if self.editing_field == 'new_group' else "Enter value:"

        prompt = self.font_medium.render(prompt_text, True, self.colors['text_dim'])
        prompt_rect = prompt.get_rect()
        prompt_x = box_x + (box_width - prompt_rect.width) // 2
        screen.blit(prompt, (prompt_x, box_y + 20))

        input_rect = pygame.Rect(box_x + 20, box_y + 50, box_width - 40, 40)
        pygame.draw.rect(screen, self.colors['panel_light'], input_rect, border_radius=5)
        pygame.draw.rect(screen, self.colors['accent'], input_rect, 2, border_radius=5)

        cursor = "_" if int(self.cursor_blink * 2) % 2 == 0 else ""
        input_text = self.font_medium.render(self.text_input + cursor, True, self.colors['text'])
        input_text_rect = input_text.get_rect()
        input_x = box_x + (box_width - input_text_rect.width) // 2
        screen.blit(input_text, (input_x, box_y + 55))

        inst = self.font_small.render("ENTER to confirm | ESC to cancel", True, self.colors['text_dark'])
        inst_rect = inst.get_rect()
        inst_x = box_x + (box_width - inst_rect.width) // 2
        screen.blit(inst, (inst_x, box_y + box_height - 25))

    def _hue_to_rgb(self, hue):
        """Turn a hue value into RGB color"""
        h = hue / 60.0
        c = 0.7 * 0.9
        x = c * (1 - abs(h % 2 - 1))
        m = 0.9 - c

        if 0 <= h < 1:
            r, g, b = c, x, 0
        elif 1 <= h < 2:
            r, g, b = x, c, 0
        elif 2 <= h < 3:
            r, g, b = 0, c, x
        elif 3 <= h < 4:
            r, g, b = 0, x, c
        elif 4 <= h < 5:
            r, g, b = x, 0, c
        else:
            r, g, b = c, 0, x

        return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))