import sys
import time
from attacks import Projectile
from config.settings import *
from core.camera import Camera
from core.game_config import GameConfig
from core.transformation_system import TransformationSystem
from core.transition_controller import TransitionController
from dev_tools.dev_menu import DevMenu
from dev_tools.npc_config import NPCConfigMenu
from dev_tools.spawn_menu import SpawnMenu
from dev_tools.transition_config import TransitionConfigMenu
from entities.enemy import Enemy
from entities.npc import NPC
from entities.player import Player
from objects.room_transition import RoomTransition
from rooms.room_manager import RoomManager
from ui.dialogue import DialogueBox
from ui.hud import UI
from ui.notifications import LevelUpNotification
from core.sound_engine import SoundEngine, SoundManager, AudioAssetLoader
from ui.sprite_hud import SpriteHUD
from core.draw_layers import LayerManager
from dev_tools.sprite_editor import SpriteEditor
from dev_tools.room_editor.room_editor import RoomEditor
from objects.collision_object import CollisionObjectManager


class Game:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("Legacy of Goku Style Engine")
        self.clock = pygame.time.Clock()
        self.running = True
        self.colors = get_colors()

        # Initialize rendering system
        self.layer_manager = LayerManager()

        # Core systems setup
        self.game_config = GameConfig()
        self.sound_engine = SoundEngine()
        self.sound_manager = SoundManager(self.sound_engine)
        AudioAssetLoader.load_from_directory(self.sound_engine)

        # Player initialization
        self.player = Player(WORLD_WIDTH // 2, WORLD_HEIGHT // 2, game_config=self.game_config)
        self.player.update_derived_stats()
        self.player.transformation = TransformationSystem(self.player, self.game_config)

        # Camera and UI
        self.camera = Camera(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.ui = UI(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.sprite_hud = SpriteHUD(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.dialogue_box = DialogueBox(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.level_up_notification = LevelUpNotification(SCREEN_WIDTH, SCREEN_HEIGHT)

        # Room system
        self.room_manager = RoomManager()
        self.current_room = None
        self.room_editor = RoomEditor(self.room_manager, SCREEN_WIDTH, SCREEN_HEIGHT)

        # Dev tools
        self.spawn_menu = SpawnMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.dev_menu = DevMenu(self.game_config, SCREEN_WIDTH, SCREEN_HEIGHT, self.sound_manager)
        self.npc_config_menu = NPCConfigMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.sprite_editor = SpriteEditor(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.transition_config_menu = TransitionConfigMenu(SCREEN_WIDTH, SCREEN_HEIGHT)

        # Game objects
        self.projectiles = []
        self.melee_attacks = []
        self.enemies = []
        self.npcs = []
        self.destructible_stones = []
        self.collision_objects = []
        self.room_transitions = []

        # Test mode tracking - prevents saving changes made during testing
        self.is_test_mode = False
        self.test_room_backup = None

        # Make sure object editor exists before we try to use it
        if self.room_editor.object_editor is None:
            from dev_tools.room_editor.room_editor_tools.object_editor import ObjectEditor
            self.room_editor.object_editor = ObjectEditor(
                SCREEN_WIDTH,
                SCREEN_HEIGHT,
                self.room_manager
            )

        # Hook up deletion callbacks so changes in editor reflect in game
        self.room_editor.object_editor.on_collision_deleted = self._on_collision_deleted
        self.room_editor.object_editor.on_stone_deleted = self._on_stone_deleted

        # Sync up spawn points and collision data from loaded rooms
        self._sync_spawn_manager_with_rooms()

        # Game state
        self.pending_npc_position = None
        self.nearby_npc = None
        self.pending_transition_position = None
        self.last_time = time.time()

        # Create the starting room
        self._create_default_room()

        # Transition system
        self.transition_controller = TransitionController(SCREEN_WIDTH, SCREEN_HEIGHT)

        # Start background music
        self.sound_manager.set_context('exploration')

    def _create_default_room(self):
        """Set up the initial room when game starts"""
        room = self.room_manager.create_room("Default Room", WORLD_WIDTH, WORLD_HEIGHT, "Default")
        self.room_manager.current_room = room
        self.current_room = room

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            # NPC configuration menu has priority when active
            if self.npc_config_menu.active:
                result = self.npc_config_menu.handle_input(event)
                if result and result != 'cancel' and self.pending_npc_position:
                    x, y = self.pending_npc_position
                    npc = NPC(x, y, result)
                    npc.npc_type = result['npc_type']
                    self.npcs.append(npc)
                    self.pending_npc_position = None
                elif result == 'cancel':
                    self.pending_npc_position = None
                continue

            # Handle room transition config
            if self.transition_config_menu.active:
                available_rooms = self.room_manager.get_room_names() if hasattr(self, 'room_manager') else []
                result = self.transition_config_menu.handle_input(event)
                if result and result != 'cancel' and self.pending_transition_position:
                    x, y = self.pending_transition_position
                    transition = RoomTransition(x, y, result['width'], result['height'])
                    transition.target_room = result['target_room']
                    transition.exit_direction = result['exit_direction']
                    transition.entry_direction = result['entry_direction']
                    transition.spawn_x = result['spawn_x']
                    transition.spawn_y = result['spawn_y']
                    self.room_transitions.append(transition)
                    self.pending_transition_position = None
                elif result == 'cancel':
                    self.pending_transition_position = None
                continue

            # Sprite editor mode
            if self.sprite_editor.active:
                self.sprite_editor.handle_input(event)
                continue

            # Room editor mode
            if self.room_editor.active:
                result = self.room_editor.handle_input(event)
                if result and result.startswith('test_room:'):
                    self._handle_test_room(result)
                continue

            # Dev menu handling
            if self.dev_menu.active:
                result = self.dev_menu.handle_input(event)
                if result == 'open_spawn_menu':
                    self.dev_menu.active = False
                    self.spawn_menu.toggle()
                elif result == 'open_room_editor':
                    self.dev_menu.active = False
                    # Exit test mode when opening editor
                    if self.is_test_mode:
                        self._exit_test_mode()
                    self.room_editor.toggle()
                elif result == 'open_sprite_editor':
                    self.dev_menu.active = False
                    self.sprite_editor.toggle()
                continue

            elif event.type == pygame.KEYDOWN:
                if self.ui.current_screen == 'game':
                    if event.key == pygame.K_F1:
                        self.dev_menu.toggle()

                    # Spawn menu controls
                    if self.spawn_menu.active:
                        if event.key == pygame.K_a:
                            self.spawn_menu.navigate_category(-1)
                        elif event.key == pygame.K_d:
                            self.spawn_menu.navigate_category(1)
                        elif event.key == pygame.K_w:
                            self.spawn_menu.navigate_item(-1)
                        elif event.key == pygame.K_s:
                            self.spawn_menu.navigate_item(1)

                    # Normal game controls when menus are closed
                    elif not self.spawn_menu.active:
                        if event.key == pygame.K_ESCAPE:
                            # If in test mode, exit it and return to editor
                            if self.is_test_mode:
                                self._exit_test_mode()
                                self.room_editor.active = True
                                self.room_editor.current_view = 'view_room'
                            else:
                                self.ui.current_screen = 'main_menu'
                                self.ui.selected_menu_item = 0

                        elif event.key == pygame.K_q:
                            if self.player.ki_attack_mode == 'blast':
                                self.player.shoot_blast()
                            elif self.player.ki_attack_mode == 'beam':
                                self.player.start_charging_beam()
                                self.player.is_q_pressed = True

                        elif event.key == pygame.K_e:
                            # Try to interact with nearby NPC first
                            if self.nearby_npc and not self.dialogue_box.active:
                                text, is_final, item = self.nearby_npc.start_dialogue()
                                if text:
                                    self.dialogue_box.show(text, "NPC", is_final, item)
                                    if item:
                                        self.player.inventory.append(item)

                            # Continue dialogue if it's already active
                            elif self.dialogue_box.active:
                                if self.dialogue_box.is_final:
                                    self.dialogue_box.hide()
                                    if self.nearby_npc:
                                        self.nearby_npc.end_dialogue()
                                else:
                                    text, is_final, item = self.nearby_npc.start_dialogue()
                                    if text:
                                        self.dialogue_box.show(text, "NPC", is_final, item)
                                        if item:
                                            self.player.inventory.append(item)
                            else:
                                # Default to melee attack
                                melee = self.player.melee_attack()
                                if melee:
                                    self.melee_attacks.append(melee)
                                    self.sound_manager.play_sfx('punch')

                        elif event.key == pygame.K_TAB:
                            # Cycle through attack modes
                            if self.player.ki_attack_mode == 'blast':
                                self.player.ki_attack_mode = 'beam'
                            elif self.player.ki_attack_mode == 'beam':
                                self.player.ki_attack_mode = 'transform'
                            else:
                                self.player.ki_attack_mode = 'blast'

                        elif event.key == pygame.K_x:
                            # Trigger transformation if we're in transform mode
                            if self.player.ki_attack_mode == 'transform':
                                if self.player.transformation and self.player.transformation.is_ready:
                                    self.player.transformation.start_transform()

                        elif event.key in [pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN]:
                            if self.player.check_double_tap(event.key):
                                self.player.is_running = True

                elif self.ui.current_screen == 'main_menu':
                    if event.key == pygame.K_UP:
                        self.ui.selected_menu_item = (self.ui.selected_menu_item - 1) % len(self.ui.menu_items)
                        self.sound_manager.play_sfx('menu_select')
                    elif event.key == pygame.K_DOWN:
                        self.ui.selected_menu_item = (self.ui.selected_menu_item + 1) % len(self.ui.menu_items)
                        self.sound_manager.play_sfx('menu_select')
                    elif event.key == pygame.K_RETURN:
                        selected = self.ui.menu_items[self.ui.selected_menu_item]
                        if selected == 'Continue':
                            self.ui.current_screen = 'game'
                        elif selected == 'Status':
                            self.ui.current_screen = 'status'
                        elif selected == 'Inventory':
                            self.ui.current_screen = 'inventory'
                        elif selected == 'Options':
                            self.ui.current_screen = 'options'
                        elif selected == 'Quit':
                            self.running = False
                elif event.key == pygame.K_ESCAPE:
                    self.ui.current_screen = 'game'

                elif self.ui.current_screen in ['status', 'inventory', 'options']:
                    if event.key == pygame.K_ESCAPE:
                        self.ui.current_screen = 'main_menu'

            elif event.type == pygame.KEYUP:
                if self.ui.current_screen == 'game':
                    if event.key == pygame.K_q:
                        self.player.is_q_pressed = False
                        if self.player.is_charging_beam and not self.player.is_firing_beam:
                            self.player.stop_beam()

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if self.spawn_menu.active and event.button == 1:
                    mouse_x, mouse_y = event.pos
                    # Convert screen space to world space
                    world_x = (mouse_x + self.camera.x) // RENDER_SCALE
                    world_y = (mouse_y + self.camera.y) // RENDER_SCALE
                    category, item = self.spawn_menu.get_selected_spawn()

                    if 0 < world_x < self.current_room.width and 0 < world_y < self.current_room.height:
                        if category == 'Enemies':
                            self.enemies.append(Enemy(world_x, world_y))
                        elif category == 'Objects':
                            if item == 'Room Transition':
                                self.pending_transition_position = (world_x, world_y)
                                available_rooms = self.room_manager.get_room_names() if hasattr(self,
                                                                                                'room_manager') else []
                                self.transition_config_menu.toggle(available_rooms)
                                self.spawn_menu.toggle()
                        elif category == 'NPCs':
                            self.pending_npc_position = (world_x, world_y)
                            self.npc_config_menu.toggle()
                            self.spawn_menu.toggle()

    def _handle_test_room(self, result):
        """Switch to a room for testing from the editor"""
        room_name = result.split(':', 1)[1]
        room = self.room_manager.get_room_by_name(room_name)
        if not room:
            return

        # Enter test mode - create backup of room state
        self.is_test_mode = True
        self._create_test_backup(room)

        # Make this room the active one
        self.room_manager.current_room = room
        self.current_room = room

        # Sync tiles to tileset editor so they render properly
        if self.room_editor.tileset_editor:
            if room_name not in self.room_editor.tileset_editor.room_tiles or not self.room_editor.tileset_editor.room_tiles[room_name]:
                if hasattr(room, 'tiles') and room.tiles:
                    self.room_editor.tileset_editor.room_tiles[room_name] = room.tiles[:]
                else:
                    self.room_editor.tileset_editor.room_tiles[room_name] = []

        # Create COPIES of collision objects so changes don't affect the originals
        self.collision_objects = []
        if hasattr(room, 'collision_objects') and room.collision_objects:
            from objects.collision_object import CollisionObject
            for obj in room.collision_objects:
                copy_obj = CollisionObject(obj.x, obj.y, obj.width, obj.height)
                copy_obj.collision_type = getattr(obj, 'collision_type', 'wall')
                self.collision_objects.append(copy_obj)

        # Create COPIES of destructible stones so destroying them doesn't affect originals
        self.destructible_stones = []
        if hasattr(room, 'destructible_stones') and room.destructible_stones:
            from objects.destructible_stone import DestructibleStone
            for stone in room.destructible_stones:
                copy_stone = DestructibleStone(stone.x, stone.y, stone.stone_type)
                copy_stone.max_health = stone.max_health
                copy_stone.health = stone.health
                copy_stone.width = stone.width
                copy_stone.height = stone.height
                copy_stone.active = True
                self.destructible_stones.append(copy_stone)

        # Figure out where to spawn the player
        if hasattr(room, 'spawn_points') and room.spawn_points:
            spawn_pos = (room.spawn_points[0].x, room.spawn_points[0].y)
        elif room.spawn_point:
            spawn_pos = room.spawn_point
        else:
            spawn_pos = (room.width // 2, room.height // 2)

        # Move player to spawn point
        self.player.x = spawn_pos[0]
        self.player.y = spawn_pos[1]

        # Center camera on player
        self.camera.x = max(0, self.player.x - self.camera.screen_width // 2)
        self.camera.y = max(0, self.player.y - self.camera.screen_height // 2)

        # Clear any test enemies/NPCs
        self.enemies = []
        self.npcs = []
        self.projectiles = []
        self.melee_attacks = []

        self.room_editor.active = False

    def _create_test_backup(self, room):
        """Create a backup of the room state before testing"""
        # Store simple data about each object so we can restore later
        collision_backup = []
        if hasattr(room, 'collision_objects'):
            from objects.collision_object import CollisionObject
            for obj in room.collision_objects:
                collision_backup.append({
                    'x': obj.x,
                    'y': obj.y,
                    'width': obj.width,
                    'height': obj.height,
                    'collision_type': getattr(obj, 'collision_type', 'wall')
                })

        stones_backup = []
        if hasattr(room, 'destructible_stones'):
            for stone in room.destructible_stones:
                stones_backup.append({
                    'x': stone.x,
                    'y': stone.y,
                    'stone_type': stone.stone_type,
                    'max_health': stone.max_health,
                    'health': stone.health,
                    'width': stone.width,
                    'height': stone.height
                })

        self.test_room_backup = {
            'room_name': room.name,
            'collision_objects': collision_backup,
            'destructible_stones': stones_backup,
        }

    def _exit_test_mode(self):
        """Exit test mode and restore original room state"""
        if not self.is_test_mode or not self.test_room_backup:
            return

        # Restore the original room state from backup
        room = self.room_manager.get_room_by_name(self.test_room_backup['room_name'])
        if room:
            # Rebuild collision objects from backup data
            from objects.collision_object import CollisionObject
            room.collision_objects = []
            for obj_data in self.test_room_backup['collision_objects']:
                obj = CollisionObject(
                    obj_data['x'],
                    obj_data['y'],
                    obj_data['width'],
                    obj_data['height']
                )
                obj.collision_type = obj_data['collision_type']
                room.collision_objects.append(obj)

            # Rebuild stones from backup data
            from objects.destructible_stone import DestructibleStone
            room.destructible_stones = []
            for stone_data in self.test_room_backup['destructible_stones']:
                stone = DestructibleStone(
                    stone_data['x'],
                    stone_data['y'],
                    stone_data['stone_type']
                )
                stone.max_health = stone_data['max_health']
                stone.health = stone_data['health']
                stone.width = stone_data['width']
                stone.height = stone_data['height']
                room.destructible_stones.append(stone)

            # Update editor managers to point to restored data
            if self.room_editor.object_editor:
                self.room_editor.object_editor.collision_manager.collision_objects[room.name] = room.collision_objects

        # Clear test mode flags
        self.is_test_mode = False
        self.test_room_backup = None

        # Clear test entities
        self.enemies = []
        self.npcs = []
        self.projectiles = []
        self.melee_attacks = []
        self.destructible_stones = []
        self.collision_objects = []

    def _on_collision_deleted(self, collision_obj, room_name):
        """Called when collision object is removed in editor"""
        # Don't process deletions during test mode
        if self.is_test_mode:
            return

        if self.current_room and self.current_room.name == room_name:
            if collision_obj in self.collision_objects:
                self.collision_objects.remove(collision_obj)

    def _on_stone_deleted(self, stone, room_name):
        """Called when stone is removed in editor"""
        # Don't process deletions during test mode
        if self.is_test_mode:
            return

        if self.current_room and self.current_room.name == room_name:
            if stone in self.destructible_stones:
                self.destructible_stones.remove(stone)

    def update(self):
        current_time = time.time()
        dt = current_time - self.last_time
        self.last_time = current_time

        enemies_defeated_this_frame = 0

        if self.ui.current_screen == 'game':
            keys = pygame.key.get_pressed()
            dx = dy = 0

            is_running = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT] or self.player.is_running

            if keys[pygame.K_LEFT]:
                dx = -1
            if keys[pygame.K_RIGHT]:
                dx = 1
            if keys[pygame.K_UP]:
                dy = -1
            if keys[pygame.K_DOWN]:
                dy = 1

            # Reset running state when not moving
            if dx == 0 and dy == 0:
                self.player.is_running = False
                if self.player.current_animation_state in ['walk', 'run']:
                    self.player.sprite.set_animation('idle', self.player.direction)
                    self.player.current_animation_state = 'idle'

            # Handle movement with collision detection
            if dx != 0 or dy != 0:
                old_x = self.player.x
                old_y = self.player.y
                self.player.move(dx, dy, is_running, self.current_room.width, self.current_room.height)

                # Check for collision with stones
                collision_occurred = False
                for stone in self.destructible_stones:
                    if stone.check_collision_with_player(self.player):
                        self.player.x = old_x
                        self.player.y = old_y
                        if is_running:
                            self.player.start_collision_knockback(dx, dy)
                            self.camera.start_shake(intensity=15, duration=0.3)
                        collision_occurred = True
                        break

                # Check for collision with walls
                if not collision_occurred:
                    for collision_obj in self.collision_objects:
                        if collision_obj.check_collision_with_player(self.player):
                            self.player.x = old_x
                            self.player.y = old_y
                            if is_running:
                                self.player.start_collision_knockback(dx, dy)
                                self.camera.start_shake(intensity=15, duration=0.3)
                            break

            self.player.update(dt)
            self.camera.update(self.player, self.current_room.width, self.current_room.height, dt)

            # Spawn blast projectile after animation completes
            if self.player.pending_blast == 'ready':
                spawn_x, spawn_y = self.player.get_blast_spawn_position()
                projectile = Projectile(spawn_x, spawn_y, self.player.direction)
                self.projectiles.append(projectile)
                self.sound_manager.play_sfx('blast')
                self.player.pending_blast = None

            # Update screen transitions
            if self.transition_controller.is_transitioning():
                self.transition_controller.update(dt, self.player)

            # Handle room transitions when player walks into them
            if not self.transition_controller.is_transitioning():
                for transition in self.room_transitions:
                    if transition.active and transition.check_collision(self.player):
                        def complete_transition(target_room_name, spawn_x, spawn_y):
                            target_room = self.room_manager.get_room_by_name(target_room_name)
                            if target_room:
                                self.room_manager.current_room = target_room
                                self.current_room = target_room

                        self.transition_controller.start_transition(
                            self.player,
                            transition,
                            complete_transition
                        )
                        break

            self.level_up_notification.update(dt)

            # Beam charging mechanics
            if self.player.is_charging_beam:
                self.player.update_beam_charge(dt)

            # Auto-fire beam when fully charged
            if not self.player.is_firing_beam and self.player.beam_charge_time >= self.player.beam_charge_required:
                beam = self.player.fire_beam_auto()
                if beam:
                    self.player.current_beam = beam
                    self.sound_manager.play_sfx('beam')

            # Keep beam alive while firing
            if self.player.current_beam:
                self.player.current_beam.update(dt)
                if not self.player.is_firing_beam:
                    self.player.current_beam = None

            # Update all projectiles
            for projectile in self.projectiles[:]:
                projectile.update(self.current_room.width, self.current_room.height, dt)
                if not projectile.active:
                    self.projectiles.remove(projectile)

            # Update melee attacks
            for melee in self.melee_attacks[:]:
                melee.update(dt)
                if not melee.active:
                    self.melee_attacks.remove(melee)

            # Enemy AI and combat
            for enemy in self.enemies[:]:
                enemy.update(dt, self.player, self.current_room.width, self.current_room.height)

                # Check if any attacks hit this enemy
                for melee in self.melee_attacks:
                    if melee.active:
                        enemy.check_collision_with_attack(melee, 'melee')
                        self.sound_manager.play_sfx('enemy_hit')

                for projectile in self.projectiles:
                    if projectile.active and enemy.check_collision_with_attack(projectile, 'projectile'):
                        projectile.active = False

                if self.player.current_beam:
                    enemy.check_collision_with_attack(self.player.current_beam, 'beam')

                # Handle enemy defeat
                if not enemy.active:
                    enemies_defeated_this_frame += 1
                    xp_reward = enemy.get_xp_reward(self.game_config)
                    self.player.gain_exp(xp_reward, self.game_config)

                    if self.player.pending_level_up:
                        self.level_up_notification.show(self.player.level, self.player.stat_points)
                        self.player.pending_level_up = False

                    self.enemies.remove(enemy)

            # NPC interaction detection
            self.nearby_npc = None
            for npc in self.npcs[:]:
                npc.update(dt, self.player, self.current_room.width, self.current_room.height)
                if npc.can_interact(self.player):
                    self.nearby_npc = npc

            # Destructible stone mechanics
            for stone in self.destructible_stones[:]:
                stone.update(dt)

                for melee in self.melee_attacks:
                    if melee.active:
                        if stone.check_collision_with_attack(melee, 'melee'):
                            self.sound_manager.play_sfx('punch')

                if not stone.active:
                    self.destructible_stones.remove(stone)

            # Transformation system
            if self.player.transformation:
                self.player.transformation.update(dt, enemies_defeated_this_frame)

            # Dynamic music based on combat
            self.sound_manager.update_battle_state(dt, len(self.enemies) > 0)

            # Don't update game when dev tools are active
            if self.dev_menu.active:
                self.dev_menu.update(dt)
                return

            if self.sprite_editor.active:
                self.sprite_editor.update(dt)
                return

            if self.room_editor.active:
                self.room_editor.update(dt)
                return

    def draw(self):
        self.screen.fill((34, 139, 34))

        # Calculate visible area for culling
        visible_x_start = self.camera.x // RENDER_SCALE
        visible_y_start = self.camera.y // RENDER_SCALE
        visible_x_end = (self.camera.x + SCREEN_WIDTH) // RENDER_SCALE
        visible_y_end = (self.camera.y + SCREEN_HEIGHT) // RENDER_SCALE

        # Draw grid overlay
        first_grid_x = (visible_x_start // TILE_SIZE) * TILE_SIZE
        first_grid_y = (visible_y_start // TILE_SIZE) * TILE_SIZE

        x = first_grid_x
        while x <= visible_x_end:
            screen_x = (x * RENDER_SCALE) - self.camera.x
            if -50 <= screen_x <= SCREEN_WIDTH + 50:
                pygame.draw.line(self.screen, (44, 149, 44),
                                 (int(screen_x), 0), (int(screen_x), SCREEN_HEIGHT), 1)
            x += TILE_SIZE

        y = first_grid_y
        while y <= visible_y_end:
            screen_y = (y * RENDER_SCALE) - self.camera.y
            if -50 <= screen_y <= SCREEN_HEIGHT + 50:
                pygame.draw.line(self.screen, (44, 149, 44),
                                 (0, int(screen_y)), (SCREEN_WIDTH, int(screen_y)), 1)
            y += TILE_SIZE

        # Draw world boundary
        world_rect_x = (0 * RENDER_SCALE) - self.camera.x
        world_rect_y = (0 * RENDER_SCALE) - self.camera.y
        world_width = self.current_room.width * RENDER_SCALE
        world_height = self.current_room.height * RENDER_SCALE
        pygame.draw.rect(self.screen, self.colors['RED'],
                         (world_rect_x, world_rect_y, world_width, world_height), 3)

        # Show test mode indicator
        if self.is_test_mode:
            test_font = pygame.font.Font(None, 32)
            test_text = test_font.render("TEST MODE - Press ESC to return to editor", True, (255, 255, 0))
            test_bg = pygame.Surface((test_text.get_width() + 20, test_text.get_height() + 10), pygame.SRCALPHA)
            test_bg.fill((0, 0, 0, 180))
            bg_x = (SCREEN_WIDTH - test_text.get_width()) // 2 - 10
            self.screen.blit(test_bg, (bg_x, 10))
            self.screen.blit(test_text, ((SCREEN_WIDTH - test_text.get_width()) // 2, 15))

        # Draw spawn points
        if hasattr(self, 'room_editor') and self.room_editor and self.room_editor.object_editor:
            spawn_obj = self.room_editor.object_editor.spawn_manager.get_spawn_point(self.current_room.name)
            if spawn_obj:
                screen_x = (spawn_obj.x * RENDER_SCALE) - self.camera.x
                screen_y = (spawn_obj.y * RENDER_SCALE) - self.camera.y

                if spawn_obj.sprite:
                    scaled_width = int(spawn_obj.width * RENDER_SCALE)
                    scaled_height = int(spawn_obj.height * RENDER_SCALE)
                    scaled_sprite = pygame.transform.scale(spawn_obj.sprite, (scaled_width, scaled_height))
                    sprite_x = int(screen_x - scaled_width // 2)
                    sprite_y = int(screen_y - scaled_height // 2)
                    self.screen.blit(scaled_sprite, (sprite_x, sprite_y))

        # Render background tiles
        if self.current_room and self.room_editor.tileset_editor:
            self.room_editor.tileset_editor.draw_tiles(
                self.screen,
                int(self.camera.x),
                int(self.camera.y),
                self.current_room.name,
                layer='background'
            )

        if hasattr(self.current_room, 'tiles') and self.current_room.tiles:
            for tile in self.current_room.tiles:
                if tile.layer < 0:
                    tileset = self.room_editor.tileset_editor.tileset_manager.get_tileset(tile.tileset_name)
                    if tileset and tileset.image:
                        tile_surface = tileset.get_tile_surface(tile.tile_x, tile.tile_y)
                        if tile_surface:
                            screen_x = (tile.x * RENDER_SCALE) - self.camera.x
                            screen_y = (tile.y * RENDER_SCALE) - self.camera.y
                            scaled_width = tileset.tile_width * RENDER_SCALE
                            scaled_height = tileset.tile_height * RENDER_SCALE
                            scaled_tile = pygame.transform.scale(tile_surface, (scaled_width, scaled_height))
                            self.screen.blit(scaled_tile, (int(screen_x), int(screen_y)))

        # Prepare objects for layered rendering
        self.layer_manager.clear()

        for projectile in self.projectiles:
            self.layer_manager.add_object(projectile)

        if self.player.current_beam:
            self.layer_manager.add_object(self.player.current_beam)

        for melee in self.melee_attacks:
            self.layer_manager.add_object(melee)

        self.layer_manager.add_object(self.player)

        for enemy in self.enemies:
            self.layer_manager.add_object(enemy)

        for npc in self.npcs:
            self.layer_manager.add_object(npc)

        for stone in self.destructible_stones:
            self.layer_manager.add_object(stone)

        # Show collision boxes in dev mode
        if self.dev_menu.active or self.spawn_menu.active or self.room_editor.active:
            from objects.collision_object import draw_collision_object
            for collision_obj in self.collision_objects:
                draw_collision_object(self.screen, collision_obj, self.camera.x, self.camera.y,
                                      RENDER_SCALE, dev_mode=True, selected=False)

        # Draw all game objects in correct depth order
        self.layer_manager.draw_all(self.screen, self.camera, self.colors, RENDER_SCALE)

        # Render foreground tiles
        if hasattr(self.current_room, 'tiles') and self.current_room.tiles:
            for tile in self.current_room.tiles:
                if tile.layer >= 0:
                    tileset = self.room_editor.tileset_editor.tileset_manager.get_tileset(tile.tileset_name)
                    if tileset and tileset.image:
                        tile_surface = tileset.get_tile_surface(tile.tile_x, tile.tile_y)
                        if tile_surface:
                            screen_x = (tile.x * RENDER_SCALE) - self.camera.x
                            screen_y = (tile.y * RENDER_SCALE) - self.camera.y
                            scaled_width = tileset.tile_width * RENDER_SCALE
                            scaled_height = tileset.tile_height * RENDER_SCALE
                            scaled_tile = pygame.transform.scale(tile_surface, (scaled_width, scaled_height))
                            self.screen.blit(scaled_tile, (int(screen_x), int(screen_y)))

        # Only draw UI when not in dev menu
        if not self.dev_menu.active:
            # NPC interaction indicator
            if self.nearby_npc and not self.dialogue_box.active:
                npc_screen_x = (self.nearby_npc.x * RENDER_SCALE) - self.camera.x
                npc_screen_y = ((self.nearby_npc.y - 20) * RENDER_SCALE) - self.camera.y
                indicator_size = 6 * RENDER_SCALE
                pygame.draw.circle(self.screen, self.colors['YELLOW'],
                                   (int(npc_screen_x), int(npc_screen_y)), indicator_size)
                pygame.draw.circle(self.screen, self.colors['WHITE'],
                                   (int(npc_screen_x), int(npc_screen_y)), indicator_size, 1)

            self.spawn_menu.draw(self.screen, self.colors)

            # Draw spawn crosshair
            if self.spawn_menu.active:
                mouse_x, mouse_y = pygame.mouse.get_pos()
                pygame.draw.line(self.screen, self.colors['CYAN'], (mouse_x - 10, mouse_y), (mouse_x + 10, mouse_y), 2)
                pygame.draw.line(self.screen, self.colors['CYAN'], (mouse_x, mouse_y - 10), (mouse_x, mouse_y + 10), 2)
                pygame.draw.circle(self.screen, self.colors['CYAN'], (mouse_x, mouse_y), 20, 2)

            self.npc_config_menu.draw(self.screen, self.colors)
            self.dialogue_box.draw(self.screen, self.colors)
            self.level_up_notification.draw(self.screen, self.colors)

            # Room transitions
            dev_mode = self.spawn_menu.active or self.dev_menu.active
            for transition in self.room_transitions:
                transition.draw(self.screen, self.camera, dev_mode, RENDER_SCALE)

            self.transition_config_menu.draw(self.screen)
            self.transition_controller.draw(self.screen)

            # Player HUD
            if self.ui.current_screen == 'game':
                self.sprite_hud.draw(self.screen, self.player)

            # Menu screens
            if self.ui.current_screen == 'main_menu':
                self.ui.draw_main_menu(self.screen, self.colors)
            elif self.ui.current_screen == 'status':
                self.ui.draw_status_screen(self.screen, self.player, self.game_config, self.colors)
            elif self.ui.current_screen == 'inventory':
                self.ui.draw_inventory_screen(self.screen, self.player, self.colors)
            elif self.ui.current_screen == 'options':
                self.ui.draw_options_screen(self.screen, self.colors)

        self.sprite_editor.draw(self.screen)
        self.room_editor.draw(self.screen)
        self.dev_menu.draw(self.screen)

        pygame.display.flip()

    def _sync_spawn_manager_with_rooms(self):
        """Make sure spawn points and collision data match what's in the saved rooms"""
        if not hasattr(self.room_editor, 'object_editor') or not self.room_editor.object_editor:
            return

        spawn_manager = self.room_editor.object_editor.spawn_manager
        collision_manager = self.room_editor.object_editor.collision_manager

        for room in self.room_manager.rooms:
            # Sync spawn points
            if hasattr(room, 'spawn_points') and room.spawn_points:
                for spawn in room.spawn_points:
                    spawn_manager.spawn_points[room.name] = spawn

            # Point manager's collision list directly at room's list (no copying)
            if hasattr(room, 'collision_objects'):
                collision_manager.collision_objects[room.name] = room.collision_objects
            else:
                room.collision_objects = []
                collision_manager.collision_objects[room.name] = room.collision_objects

    def cleanup(self):
        """Save everything before closing"""
        # Don't save if we're in test mode - exit it first
        if self.is_test_mode:
            self._exit_test_mode()

        # Transfer editor data back to rooms
        if hasattr(self, 'room_editor') and self.room_editor:
            self.room_editor.save_all_editor_data_to_rooms()

        # Write all rooms to disk
        if hasattr(self, 'room_manager'):
            self.room_manager.save_all_rooms()

    def run(self):
        self.last_time = time.time()

        try:
            while self.running:
                self.handle_events()
                self.update()
                self.draw()
                self.clock.tick(FPS)

        finally:
            self.cleanup()
            pygame.quit()
            sys.exit()


if __name__ == "__main__":
    game = Game()
    game.run()