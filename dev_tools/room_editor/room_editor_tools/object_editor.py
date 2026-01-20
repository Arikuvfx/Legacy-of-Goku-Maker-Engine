import pygame
import pygame.gfxdraw
from config.settings import RENDER_SCALE, TILE_SIZE
from objects.spawn_object import SpawnObject, SpawnObjectManager
from objects.collision_object import CollisionObject, CollisionObjectManager, draw_collision_object


class ObjectEditor:
    """Editor for placing game objects like spawn points, collision walls, and decorations"""

    def __init__(self, screen_width, screen_height, room_manager=None):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False
        self.room_manager = room_manager

        # Set up fonts
        self.font_small = pygame.font.Font(None, 16)
        self.font_medium = pygame.font.Font(None, 20)
        self.font_large = pygame.font.Font(None, 24)

        # Color scheme
        self.colors = {
            'bg': (20, 20, 30),
            'bg_transparent': (20, 20, 30, 230),
            'panel': (35, 35, 55),
            'panel_light': (45, 45, 65),
            'accent': (255, 215, 0),
            'accent_dim': (200, 170, 0),
            'text': (255, 255, 255),
            'text_dim': (180, 180, 200),
            'text_dark': (120, 120, 140),
            'grid': (60, 60, 80),
            'success': (100, 255, 100),
            'preview': (255, 255, 255, 100),
            'snap_guide': (255, 215, 0, 150),
            'disabled': (100, 100, 100),
            'delete': (255, 50, 50),
            'delete_hover': (255, 100, 100)
        }

        # Palette layout (matches tileset editor)
        self.palette_width = 600
        self.palette_x = screen_width - self.palette_width
        self.palette_y = 100
        self.palette_height = 940
        self.palette_padding = 10
        self.item_size = 80
        self.items_per_row = 3
        self.scroll_offset = 0
        self.max_scroll = 0

        # Object managers
        self.spawn_manager = SpawnObjectManager()
        self.collision_manager = CollisionObjectManager()

        # Collision placement tracking
        self.placing_collision = False
        self.collision_start_x = 0
        self.collision_start_y = 0
        self.preview_collision = None

        # Callbacks for game sync
        self.on_stone_placed = None

        # Object under cursor (for deletion)
        self.hovered_object = None
        self.hovered_object_type = None

        # Available objects organized by category
        self.categories = {
            'System': [],
            'Decorations': [
                {'id': 'stone_small', 'name': 'Small Stone', 'sprite': None, 'width': 16, 'height': 16,
                 'object_type': 'destructible_stone', 'stone_type': 'small'},
                {'id': 'stone_medium', 'name': 'Medium Stone', 'sprite': None, 'width': 24, 'height': 24,
                 'object_type': 'destructible_stone', 'stone_type': 'medium'},
                {'id': 'stone_big', 'name': 'Big Stone', 'sprite': None, 'width': 32, 'height': 32,
                 'object_type': 'destructible_stone', 'stone_type': 'big'},
            ],
            'Structures': [
                {'id': 'house_1', 'name': 'Small House', 'sprite': None, 'width': 64, 'height': 64},
                {'id': 'fence_1', 'name': 'Fence', 'sprite': None, 'width': 16, 'height': 16},
                {'id': 'sign_1', 'name': 'Sign Post', 'sprite': None, 'width': 16, 'height': 24},
                {'id': 'well_1', 'name': 'Well', 'sprite': None, 'width': 32, 'height': 32},
            ],
            'Interactive': [
                {'id': 'chest_1', 'name': 'Treasure Chest', 'sprite': None, 'width': 24, 'height': 20},
                {'id': 'door_1', 'name': 'Door', 'sprite': None, 'width': 16, 'height': 32},
                {'id': 'switch_1', 'name': 'Switch', 'sprite': None, 'width': 16, 'height': 16},
            ]
        }

        # Add spawn point to System category
        spawn_obj = SpawnObject(0, 0, "")
        self.categories['System'].append({
            'id': 'spawn_point',
            'name': 'Spawn Point',
            'sprite': spawn_obj.sprite,
            'width': spawn_obj.width,
            'height': spawn_obj.height,
            'is_spawn': True
        })

        # Add collision wall to System category
        collision_sprite = pygame.Surface((32, 32), pygame.SRCALPHA)
        collision_sprite.fill((255, 0, 0, 100))
        pygame.draw.rect(collision_sprite, (255, 0, 0), (0, 0, 32, 32), 2)
        for i in range(0, 48, 8):
            pygame.draw.line(collision_sprite, (200, 0, 0, 120), (i, 0), (i - 32, 32), 1)

        self.categories['System'].append({
            'id': 'collision_wall',
            'name': 'Collision Wall',
            'sprite': collision_sprite,
            'width': 32,
            'height': 32,
            'is_collision': True
        })

        # Create sprites for objects that don't have them
        self._generate_placeholder_sprites()

        # Editor state
        self.current_category = list(self.categories.keys())[0]
        self.selected_object = None
        self.hover_object = None
        self.current_room_name = ""

        # Placement options
        self.grid_snap = True
        self.show_grid = True

        # Mouse tracking
        self.mouse_world_x = 0
        self.mouse_world_y = 0
        self.preview_x = 0
        self.preview_y = 0

        # Animations
        self.anim_timer = 0
        self.category_hover = {cat: 0.0 for cat in self.categories.keys()}
        self.object_hover = {}

        # UI click detection
        self.ui_rects = {}

    def _generate_placeholder_sprites(self):
        """Create visual sprites for objects that don't have real art yet"""
        for category, objects in self.categories.items():
            for obj in objects:
                if obj.get('sprite') is not None:
                    continue

                if obj.get('is_spawn', False) or obj.get('is_collision', False):
                    continue

                # Try loading real stone sprites
                if obj.get('object_type') == 'destructible_stone':
                    try:
                        stone_type = obj.get('stone_type', 'small')
                        sprite_path = f'assets/objects/{stone_type}_stone.png'
                        sprite = pygame.image.load(sprite_path).convert_alpha()
                        sprite = pygame.transform.scale(sprite, (obj['width'], obj['height']))
                        obj['sprite'] = sprite
                        continue
                    except:
                        pass

                # Make a simple placeholder
                sprite = pygame.Surface((obj['width'], obj['height']), pygame.SRCALPHA)

                # Pick a color based on category
                if category == 'Decorations':
                    base_color = (34, 139, 34)
                elif category == 'Structures':
                    base_color = (139, 69, 19)
                elif category == 'Interactive':
                    base_color = (255, 215, 0)
                else:
                    base_color = (128, 128, 128)

                pygame.draw.rect(sprite, base_color, (0, 0, obj['width'], obj['height']))
                pygame.draw.rect(sprite, (0, 0, 0), (0, 0, obj['width'], obj['height']), 2)

                # Add simple icons based on object type
                if 'tree' in obj['id']:
                    pygame.draw.rect(sprite, (101, 67, 33),
                                     (obj['width'] // 2 - 2, obj['height'] // 2, 4, obj['height'] // 2))
                    pygame.draw.circle(sprite, (34, 139, 34),
                                       (obj['width'] // 2, obj['height'] // 3), obj['width'] // 3)
                elif 'rock' in obj['id']:
                    pygame.draw.circle(sprite, (100, 100, 100),
                                       (obj['width'] // 2, obj['height'] // 2), min(obj['width'], obj['height']) // 2)
                elif 'house' in obj['id']:
                    pygame.draw.rect(sprite, (160, 82, 45),
                                     (4, obj['height'] // 2, obj['width'] - 8, obj['height'] // 2 - 4))
                    points = [(obj['width'] // 2, 4), (4, obj['height'] // 2), (obj['width'] - 4, obj['height'] // 2)]
                    pygame.draw.polygon(sprite, (139, 0, 0), points)
                elif 'chest' in obj['id']:
                    pygame.draw.rect(sprite, (139, 69, 19), (2, 6, obj['width'] - 4, obj['height'] - 8))
                    pygame.draw.rect(sprite, (255, 215, 0), (obj['width'] // 2 - 2, 8, 4, 4))

                obj['sprite'] = sprite

    def toggle(self):
        """Open or close the object editor"""
        self.active = not self.active
        if self.active:
            self.selected_object = None
            self.scroll_offset = 0
            self.placing_collision = False
            self.preview_collision = None

    def _check_object_at_position(self, world_x, world_y):
        """See if there's an object at this position (for deletion)"""
        # Check spawn point
        spawn = self.spawn_manager.get_spawn_point(self.current_room_name)
        if spawn:
            distance = ((spawn.x - world_x) ** 2 + (spawn.y - world_y) ** 2) ** 0.5
            if distance < max(spawn.width, spawn.height) / 2:
                return spawn, 'spawn'

        # Check collision walls
        collision_objs = self.collision_manager.get_collision_objects(self.current_room_name)
        for collision_obj in collision_objs:
            if (collision_obj.x <= world_x <= collision_obj.x + collision_obj.width and
                    collision_obj.y <= world_y <= collision_obj.y + collision_obj.height):
                return collision_obj, 'collision'

        # Check destructible stones
        if self.room_manager:
            room = self.room_manager.get_room_by_name(self.current_room_name)
            if room and hasattr(room, 'destructible_stones'):
                for stone in room.destructible_stones:
                    distance = ((stone.x - world_x) ** 2 + (stone.y - world_y) ** 2) ** 0.5
                    if distance < max(stone.width, stone.height) / 2:
                        return stone, 'stone'

        return None, None

    def _delete_object(self, obj, obj_type):
        """Remove an object from the room"""
        if obj_type == 'spawn':
            self.spawn_manager.remove_spawn_point(self.current_room_name)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room:
                    room.spawn_point = None
                    room.spawn_points = []
                    self.room_manager.save_room(room)

        elif obj_type == 'collision':
            self.collision_manager.remove_collision_object(obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'collision_objects'):
                    if obj in room.collision_objects:
                        room.collision_objects.remove(obj)

            # Let the game know this was deleted
            if hasattr(self, 'on_collision_deleted') and self.on_collision_deleted:
                self.on_collision_deleted(obj, self.current_room_name)

        elif obj_type == 'stone':
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'destructible_stones'):
                    if obj in room.destructible_stones:
                        room.destructible_stones.remove(obj)

            # Let the game know this was deleted
            if hasattr(self, 'on_stone_deleted') and self.on_stone_deleted:
                self.on_stone_deleted(obj, self.current_room_name)

    def _is_object_disabled(self, obj) -> bool:
        """Check if we can't place this object (e.g. spawn already exists)"""
        if obj.get('is_spawn', False):
            return self.spawn_manager.has_spawn_point(self.current_room_name)
        return False

    def handle_input(self, event, camera_x, camera_y, room_name):
        """Process input events"""
        if not self.active:
            return

        self.current_room_name = room_name
        mouse_pos = pygame.mouse.get_pos()

        # Scroll through palette
        if event.type == pygame.MOUSEWHEEL and self._is_in_palette(mouse_pos[0], mouse_pos[1]):
            self.scroll_offset -= event.y * 30
            self.scroll_offset = max(0, min(self.scroll_offset, self.max_scroll))

        # Right-click to delete objects
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 3:
                if not self._is_in_palette(mouse_pos[0], mouse_pos[1]):
                    if self.hovered_object and self.hovered_object_type:
                        self._delete_object(self.hovered_object, self.hovered_object_type)
                        self.hovered_object = None
                        self.hovered_object_type = None
                return

        # Left-click to place or select
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            # Finish placing collision wall if we're in the middle of it
            if self.placing_collision:
                self._finalize_collision_placement(room_name)
                self.placing_collision = False
                return

            # Click in palette to select object
            if self._is_in_palette(mouse_pos[0], mouse_pos[1]):
                self._handle_palette_click(mouse_pos)
            else:
                # Click in world to place object
                if self.selected_object and not self._is_object_disabled(self.selected_object):
                    if self.selected_object.get('is_collision', False):
                        # Start dragging out a collision wall
                        self.placing_collision = True
                        self.collision_start_x = self.preview_x
                        self.collision_start_y = self.preview_y
                    else:
                        self._place_object(camera_x, camera_y, room_name)

        # Keyboard shortcuts
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_g:
                self.grid_snap = not self.grid_snap
            elif event.key == pygame.K_h:
                self.show_grid = not self.show_grid
            elif event.key == pygame.K_ESCAPE or event.key == pygame.K_F3:
                if self.placing_collision:
                    self.placing_collision = False
                    self.preview_collision = None
                else:
                    self.active = False

    def _is_in_palette(self, mouse_x, mouse_y):
        """Check if the mouse is hovering over the palette"""
        return (mouse_x >= self.palette_x and
                mouse_y >= self.palette_y and
                mouse_y <= self.palette_y + self.palette_height)

    def _handle_palette_click(self, mouse_pos):
        """Handle clicks inside the palette"""
        category_start_y = self.palette_y + 45

        # Check if we clicked a category tab
        for i, category in enumerate(self.categories.keys()):
            category_rect = pygame.Rect(
                self.palette_x + self.palette_padding,
                category_start_y + i * 40,
                self.palette_width - self.palette_padding * 2,
                30
            )
            if category_rect.collidepoint(mouse_pos):
                self.current_category = category
                self.scroll_offset = 0
                return

        # Check if we clicked an object
        objects = self.categories[self.current_category]
        objects_start_y = category_start_y + len(self.categories) * 40 + 20 - self.scroll_offset

        for i, obj in enumerate(objects):
            row = i // self.items_per_row
            col = i % self.items_per_row

            item_x = self.palette_x + self.palette_padding + col * (self.item_size + 10)
            item_y = objects_start_y + row * (self.item_size + 10)

            item_rect = pygame.Rect(item_x, item_y, self.item_size, self.item_size)
            if item_rect.collidepoint(mouse_pos):
                if not self._is_object_disabled(obj):
                    self.selected_object = obj
                return

    def _place_object(self, camera_x, camera_y, room_name):
        """Actually place the selected object in the world"""
        if self.selected_object.get('is_spawn', False):
            # Place spawn point
            spawn_obj = self.spawn_manager.place_spawn_point(
                int(self.preview_x),
                int(self.preview_y),
                room_name
            )

            # Sync with the room object too
            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    room.spawn_point = (int(self.preview_x), int(self.preview_y))

        elif self.selected_object.get('is_collision', False):
            # Collision walls use the drag system
            pass

        elif self.selected_object.get('object_type') == 'destructible_stone':
            # Create a destructible stone
            from objects.destructible_stone import DestructibleStone
            stone_type = self.selected_object.get('stone_type', 'small')
            stone = DestructibleStone(
                int(self.preview_x),
                int(self.preview_y),
                stone_type
            )

            # Add to room
            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    if not hasattr(room, 'destructible_stones'):
                        room.destructible_stones = []
                    room.destructible_stones.append(stone)

                    # Let the game know we placed a stone
                    if hasattr(self, 'on_stone_placed') and self.on_stone_placed:
                        self.on_stone_placed(stone, room_name)

    def _draw_delete_highlight(self, screen, camera_x, camera_y):
        """Draw red outline around object that's about to be deleted"""
        obj = self.hovered_object
        obj_type = self.hovered_object_type

        if obj_type == 'spawn':
            screen_x = (obj.x * RENDER_SCALE) - camera_x
            screen_y = (obj.y * RENDER_SCALE) - camera_y
            scaled_width = int(obj.width * RENDER_SCALE)

            # Pulsing red circle
            pulse = int(20 + 10 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
            pygame.draw.circle(screen, self.colors['delete'],
                               (int(screen_x), int(screen_y)),
                               scaled_width // 2 + pulse, 3)

        elif obj_type == 'collision':
            screen_x = (obj.x * RENDER_SCALE) - camera_x
            screen_y = (obj.y * RENDER_SCALE) - camera_y
            scaled_width = int(obj.width * RENDER_SCALE)
            scaled_height = int(obj.height * RENDER_SCALE)

            # Pulsing red rectangle
            pulse = int(3 + 2 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
            pygame.draw.rect(screen, self.colors['delete'],
                             (int(screen_x), int(screen_y), int(scaled_width), int(scaled_height)),
                             pulse)

        elif obj_type == 'stone':
            screen_x = (obj.x * RENDER_SCALE) - camera_x
            screen_y = (obj.y * RENDER_SCALE) - camera_y
            scaled_width = int(obj.width * RENDER_SCALE)

            # Pulsing red circle
            pulse = int(20 + 10 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
            pygame.draw.circle(screen, self.colors['delete'],
                               (int(screen_x), int(screen_y)),
                               scaled_width // 2 + pulse, 3)

        # Draw X over cursor
        mouse_pos = pygame.mouse.get_pos()
        pygame.draw.line(screen, self.colors['delete'],
                         (mouse_pos[0] - 10, mouse_pos[1] - 10),
                         (mouse_pos[0] + 10, mouse_pos[1] + 10), 3)
        pygame.draw.line(screen, self.colors['delete'],
                         (mouse_pos[0] + 10, mouse_pos[1] - 10),
                         (mouse_pos[0] - 10, mouse_pos[1] + 10), 3)

    def _finalize_collision_placement(self, room_name):
        """Finish placing a collision wall after dragging"""
        if not self.preview_collision:
            return

        # Create the collision object
        collision_obj = CollisionObject(
            int(self.preview_collision.x),
            int(self.preview_collision.y),
            int(self.preview_collision.width),
            int(self.preview_collision.height),
            room_name
        )

        # Add to room (this is the source of truth)
        if self.room_manager:
            room = self.room_manager.get_room_by_name(room_name)
            if room:
                if not hasattr(room, 'collision_objects'):
                    room.collision_objects = []

                room.collision_objects.append(collision_obj)

                # Make sure manager points to the room's list (not a copy)
                self.collision_manager.collision_objects[room_name] = room.collision_objects

        self.preview_collision = None

    def update(self, dt, mouse_pos, camera_x, camera_y):
        """Update animations and preview positions"""
        if not self.active:
            return

        self.anim_timer += dt

        # Smooth category hover animations
        for category in self.categories.keys():
            target = 1.0 if category == self.current_category else 0.0
            self.category_hover[category] += (target - self.category_hover[category]) * dt * 10

        # Convert mouse to world coordinates
        self.mouse_world_x = (mouse_pos[0] + camera_x) / RENDER_SCALE
        self.mouse_world_y = (mouse_pos[1] + camera_y) / RENDER_SCALE

        # Check if cursor is over an object (for deletion)
        if not self._is_in_palette(mouse_pos[0], mouse_pos[1]):
            self.hovered_object, self.hovered_object_type = self._check_object_at_position(
                self.mouse_world_x, self.mouse_world_y
            )

        # Calculate where the preview should show
        if self.selected_object:
            if self.grid_snap:
                # Snap to grid center
                grid_x = int(self.mouse_world_x / TILE_SIZE) * TILE_SIZE + TILE_SIZE // 2
                grid_y = int(self.mouse_world_y / TILE_SIZE) * TILE_SIZE + TILE_SIZE // 2
                self.preview_x = grid_x
                self.preview_y = grid_y
            else:
                # Free placement
                self.preview_x = self.mouse_world_x
                self.preview_y = self.mouse_world_y

        # Update collision wall preview while dragging
        if self.placing_collision:
            end_x = self.mouse_world_x
            end_y = self.mouse_world_y

            min_x = min(self.collision_start_x, end_x)
            min_y = min(self.collision_start_y, end_y)
            max_x = max(self.collision_start_x, end_x)
            max_y = max(self.collision_start_y, end_y)

            width = max(16, max_x - min_x)
            height = max(16, max_y - min_y)

            self.preview_collision = CollisionObject(
                int(min_x),
                int(min_y),
                int(width),
                int(height),
                self.current_room_name
            )

        # Check what object we're hovering in palette
        self.hover_object = None
        if self._is_in_palette(mouse_pos[0], mouse_pos[1]):
            objects = self.categories[self.current_category]
            category_start_y = self.palette_y + 45
            objects_start_y = category_start_y + len(self.categories) * 40 + 20 - self.scroll_offset

            for i, obj in enumerate(objects):
                row = i // self.items_per_row
                col = i % self.items_per_row

                item_x = self.palette_x + self.palette_padding + col * (self.item_size + 10)
                item_y = objects_start_y + row * (self.item_size + 10)

                item_rect = pygame.Rect(item_x, item_y, self.item_size, self.item_size)
                if item_rect.collidepoint(mouse_pos):
                    self.hover_object = obj
                    break

        # Calculate scrollable area
        objects = self.categories[self.current_category]
        rows = (len(objects) + self.items_per_row - 1) // self.items_per_row
        total_height = rows * (self.item_size + 10)
        category_section_height = len(self.categories) * 40 + 20
        available_height = self.palette_height - (45 + category_section_height + 140)
        self.max_scroll = max(0, total_height - available_height)

    def draw_preview(self, screen, camera_x, camera_y):
        """Draw placement preview"""
        if not self.active:
            return

        # Show collision wall being dragged
        if self.placing_collision and self.preview_collision:
            draw_collision_object(screen, self.preview_collision, camera_x, camera_y,
                                  RENDER_SCALE, dev_mode=True, selected=True)
            return

        # Show delete highlight if hovering over object
        if self.hovered_object and self.hovered_object_type and not self.selected_object:
            self._draw_delete_highlight(screen, camera_x, camera_y)
            return

        if not self.selected_object:
            return

        if self._is_object_disabled(self.selected_object):
            return

        mouse_pos = pygame.mouse.get_pos()
        if self._is_in_palette(mouse_pos[0], mouse_pos[1]):
            return

        screen_x = (self.preview_x * RENDER_SCALE) - camera_x
        screen_y = (self.preview_y * RENDER_SCALE) - camera_y

        # Draw grid snap guide
        if self.grid_snap:
            grid_screen_x = int(self.mouse_world_x / TILE_SIZE) * TILE_SIZE * RENDER_SCALE - camera_x
            grid_screen_y = int(self.mouse_world_y / TILE_SIZE) * TILE_SIZE * RENDER_SCALE - camera_y

            guide_surf = pygame.Surface((TILE_SIZE * RENDER_SCALE, TILE_SIZE * RENDER_SCALE), pygame.SRCALPHA)
            pygame.draw.rect(guide_surf, self.colors['snap_guide'],
                             (0, 0, TILE_SIZE * RENDER_SCALE, TILE_SIZE * RENDER_SCALE), 2)

            # Crosshair at center
            center_x = TILE_SIZE * RENDER_SCALE // 2
            center_y = TILE_SIZE * RENDER_SCALE // 2
            pygame.draw.line(guide_surf, self.colors['snap_guide'],
                             (center_x - 5, center_y), (center_x + 5, center_y), 2)
            pygame.draw.line(guide_surf, self.colors['snap_guide'],
                             (center_x, center_y - 5), (center_x, center_y + 5), 2)
            screen.blit(guide_surf, (int(grid_screen_x), int(grid_screen_y)))

        # Draw semi-transparent object preview
        obj_sprite = self.selected_object['sprite']
        if obj_sprite:
            scaled_width = int(self.selected_object['width'] * RENDER_SCALE)
            scaled_height = int(self.selected_object['height'] * RENDER_SCALE)
            scaled_sprite = pygame.transform.scale(obj_sprite, (scaled_width, scaled_height))

            preview_surf = scaled_sprite.copy()
            preview_surf.set_alpha(100)

            preview_x = int(screen_x - scaled_width // 2)
            preview_y = int(screen_y - scaled_height // 2)

            screen.blit(preview_surf, (preview_x, preview_y))

            # Position marker
            pygame.draw.circle(screen, self.colors['accent'], (int(screen_x), int(screen_y)), 3)
            pygame.draw.circle(screen, self.colors['text'], (int(screen_x), int(screen_y)), 1)

    def draw_collision_objects(self, screen, camera_x, camera_y):
        """Draw all collision walls in the current room"""
        if not self.current_room_name:
            return

        collision_objs = self.collision_manager.get_collision_objects(self.current_room_name)

        for collision_obj in collision_objs:
            draw_collision_object(screen, collision_obj, camera_x, camera_y,
                                  RENDER_SCALE, dev_mode=True, selected=False)

    def draw_spawn_points(self, screen, camera_x, camera_y):
        """Draw spawn points in the current room"""
        if not self.current_room_name:
            return

        spawn = self.spawn_manager.get_spawn_point(self.current_room_name)
        if spawn:
            screen_x = (spawn.x * RENDER_SCALE) - camera_x
            screen_y = (spawn.y * RENDER_SCALE) - camera_y

            if spawn.sprite:
                scaled_width = int(spawn.width * RENDER_SCALE)
                scaled_height = int(spawn.height * RENDER_SCALE)
                scaled_sprite = pygame.transform.scale(spawn.sprite, (scaled_width, scaled_height))

                sprite_x = int(screen_x - scaled_width // 2)
                sprite_y = int(screen_y - scaled_height // 2)
                screen.blit(scaled_sprite, (sprite_x, sprite_y))

    def draw_palette(self, screen):
        """Draw the object selection palette"""
        if not self.active:
            return

        # Background panel
        palette_rect = pygame.Rect(self.palette_x, self.palette_y, self.palette_width, self.palette_height)
        palette_bg = pygame.Surface((self.palette_width, self.palette_height), pygame.SRCALPHA)
        palette_bg.fill(self.colors['bg_transparent'])
        screen.blit(palette_bg, (self.palette_x, self.palette_y))
        pygame.draw.rect(screen, self.colors['accent'], palette_rect, 2)

        y_pos = self.palette_y + 10

        # Title
        title = self.font_medium.render("Object Palette", True, self.colors['text'])
        screen.blit(title, (self.palette_x + 20, y_pos))
        y_pos += 35

        # Category tabs
        for i, category in enumerate(self.categories.keys()):
            is_selected = category == self.current_category
            hover = self.category_hover[category]

            category_rect = pygame.Rect(
                self.palette_x + self.palette_padding,
                y_pos,
                self.palette_width - self.palette_padding * 2,
                30
            )

            # Hover glow effect
            bg_color = self.colors['panel_light'] if is_selected else self.colors['panel']
            if hover > 0:
                glow_surf = pygame.Surface((category_rect.width + 4, category_rect.height + 4), pygame.SRCALPHA)
                glow_alpha = int(hover * 100)
                pygame.draw.rect(glow_surf, (*self.colors['accent'], glow_alpha),
                                 (0, 0, category_rect.width + 4, category_rect.height + 4), border_radius=5)
                screen.blit(glow_surf, (category_rect.x - 2, category_rect.y - 2))

            pygame.draw.rect(screen, bg_color, category_rect, border_radius=5)
            border_color = self.colors['accent'] if is_selected else self.colors['grid']
            pygame.draw.rect(screen, border_color, category_rect, 2, border_radius=5)

            text_color = self.colors['text'] if is_selected else self.colors['text_dim']
            cat_text = self.font_medium.render(category, True, text_color)
            text_rect = cat_text.get_rect(center=category_rect.center)
            screen.blit(cat_text, text_rect)

            y_pos += 40

        # Divider line
        pygame.draw.line(screen, self.colors['accent'],
                         (self.palette_x + self.palette_padding, y_pos),
                         (self.palette_x + self.palette_width - self.palette_padding, y_pos), 1)
        y_pos += 10

        # Object grid with scrolling
        objects_start_y = y_pos
        objects_content_height = self.palette_height - (y_pos - self.palette_y) - 140

        clip_rect = pygame.Rect(self.palette_x, objects_start_y, self.palette_width, objects_content_height)
        screen.set_clip(clip_rect)

        objects = self.categories[self.current_category]
        current_y = objects_start_y - self.scroll_offset

        for i, obj in enumerate(objects):
            row = i // self.items_per_row
            col = i % self.items_per_row

            item_x = self.palette_x + self.palette_padding + col * (self.item_size + 10)
            item_y = current_y + row * (self.item_size + 10)

            # Skip if not visible
            if item_y + self.item_size < objects_start_y or item_y > objects_start_y + objects_content_height:
                continue

            self._draw_object_item(screen, obj, item_x, item_y)

        screen.set_clip(None)

        # Settings at bottom
        self._draw_settings_panel(screen)

    def _draw_object_item(self, screen, obj, x, y):
        """Draw a single object in the palette"""
        item_rect = pygame.Rect(x, y, self.item_size, self.item_size)

        is_selected = self.selected_object == obj
        is_hover = self.hover_object == obj
        is_disabled = self._is_object_disabled(obj)

        # Glow for selected/hover
        if is_selected or is_hover:
            if not is_disabled:
                glow_surf = pygame.Surface((self.item_size + 4, self.item_size + 4), pygame.SRCALPHA)
                glow_alpha = 150 if is_selected else 80
                pygame.draw.rect(glow_surf, (*self.colors['accent'], glow_alpha),
                                 (0, 0, self.item_size + 4, self.item_size + 4), border_radius=5)
                screen.blit(glow_surf, (x - 2, y - 2))

        bg_color = self.colors['panel_light'] if (is_selected and not is_disabled) else self.colors['panel']
        pygame.draw.rect(screen, bg_color, item_rect, border_radius=5)

        border_color = self.colors['accent'] if (is_selected and not is_disabled) else self.colors['grid']
        border_width = 2 if is_selected else 1
        pygame.draw.rect(screen, border_color, item_rect, border_width, border_radius=5)

        # Object sprite
        if obj['sprite']:
            sprite = obj['sprite'].copy()

            # Grey out if disabled
            if is_disabled:
                sprite.fill((100, 100, 100, 150), special_flags=pygame.BLEND_RGBA_MULT)

            sprite_rect = sprite.get_rect(center=item_rect.center)
            screen.blit(sprite, sprite_rect)

        # Object name
        name_color = self.colors['disabled'] if is_disabled else self.colors['text_dim']
        name_text = self.font_small.render(obj['name'], True, name_color)
        name_rect = name_text.get_rect(centerx=item_rect.centerx, top=item_rect.bottom + 2)
        screen.blit(name_text, name_rect)

        # "PLACED" indicator for disabled spawn
        if is_disabled and obj.get('is_spawn', False):
            placed_text = self.font_small.render("PLACED", True, self.colors['disabled'])
            placed_rect = placed_text.get_rect(centerx=item_rect.centerx, centery=item_rect.centery)

            bg_surf = pygame.Surface((placed_rect.width + 8, placed_rect.height + 4), pygame.SRCALPHA)
            bg_surf.fill((0, 0, 0, 180))
            screen.blit(bg_surf, (placed_rect.x - 4, placed_rect.y - 2))

            screen.blit(placed_text, placed_rect)

    def _draw_settings_panel(self, screen):
        """Draw controls and settings at the bottom of the palette"""
        panel_height = 140
        panel_y = self.palette_y + self.palette_height - panel_height

        panel_rect = pygame.Rect(self.palette_x, panel_y, self.palette_width, panel_height)
        pygame.draw.rect(screen, self.colors['bg'], panel_rect)
        pygame.draw.line(screen, self.colors['accent'],
                         (self.palette_x, panel_y),
                         (self.palette_x + self.palette_width, panel_y), 2)

        y_pos = panel_y + 10

        # Grid snap toggle
        snap_text = f"Grid Snap: {'ON' if self.grid_snap else 'OFF'}"
        snap_color = self.colors['success'] if self.grid_snap else self.colors['text_dim']
        snap_surf = self.font_medium.render(snap_text, True, snap_color)
        screen.blit(snap_surf, (self.palette_x + self.palette_padding, y_pos))

        hint = self.font_small.render("(Press G)", True, self.colors['text_dim'])
        screen.blit(hint, (self.palette_x + self.palette_padding + 120, y_pos + 3))
        y_pos += 25

        # Grid visibility toggle
        grid_text = f"Show Grid: {'ON' if self.show_grid else 'OFF'}"
        grid_color = self.colors['success'] if self.show_grid else self.colors['text_dim']
        grid_surf = self.font_medium.render(grid_text, True, grid_color)
        screen.blit(grid_surf, (self.palette_x + self.palette_padding, y_pos))

        hint = self.font_small.render("(Press H)", True, self.colors['text_dim'])
        screen.blit(hint, (self.palette_x + self.palette_padding + 120, y_pos + 3))
        y_pos += 30

        # Control hints
        instructions = [
            "Click: Select Object",
            "Click World: Place",
            "Right-Click: Delete",
            "ESC/F3: Close"
        ]

        for inst in instructions:
            inst_surf = self.font_small.render(inst, True, self.colors['text_dim'])
            screen.blit(inst_surf, (self.palette_x + self.palette_padding, y_pos))
            y_pos += 18