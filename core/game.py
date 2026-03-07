import sys
import math
import time
from attacks import Projectile
from config.settings import *
from core.camera import Camera
from core.game_config import GameConfig
from core.transformation_system import TransformationSystem
from core.transition_controller import TransitionController
from dev_tools.dev_menu import DevMenu
from dev_tools.npc_config import NPCConfigMenu
from dev_tools.transition_config import TransitionConfigMenu
from entities.enemy import Enemy
from attacks.bomb_projectile import BombProjectile, ExplosionEffect
from attacks.bullet_projectile import bullet_projectile
from attacks.rocket_projectile import rocket_projectile
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
from objects.level_gate import LevelGate
from objects.flying_pad import FlyingPad
from core.flypad_controller import FlyingController
from objects.save_point import SavePoint, SavePointMenu, SavePointManager
from ui.character_switch_menu import CharacterSwitchMenu


class Game:
    """
    Top-level game controller.

    Owns all subsystems (rendering, audio, room management, input, etc.),
    drives the main loop, and coordinates communication between systems.
    """

    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("Legacy of Goku Style Engine")
        self.clock   = pygame.time.Clock()
        self.running = True
        self.colors  = get_colors()

        # Rendering
        self.layer_manager = LayerManager()

        # Core systems
        self.game_config   = GameConfig()
        self.sound_engine  = SoundEngine()
        self.sound_manager = SoundManager(self.sound_engine)
        AudioAssetLoader.load_from_directory(self.sound_engine)

        # Player
        self.player = Player(WORLD_WIDTH // 2, WORLD_HEIGHT // 2, game_config=self.game_config)
        self.player.update_derived_stats()
        self.player.transformation = TransformationSystem(self.player, self.game_config)
        self.player.in_transition  = False
        if not hasattr(self.player, 'hurt_tint'):
            self.player.hurt_tint = 0.0
        if not hasattr(self.player, 'hurt_tint_duration'):
            self.player.hurt_tint_duration = 0.45

        # Camera and UI
        self.camera                 = Camera(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.ui                     = UI(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.sprite_hud             = SpriteHUD(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.dialogue_box           = DialogueBox(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.level_up_notification  = LevelUpNotification(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.flying_controller      = FlyingController(SCREEN_WIDTH, SCREEN_HEIGHT)

        # Room system
        self.room_manager  = RoomManager()
        self.current_room  = None
        self.room_editor   = RoomEditor(self.room_manager, SCREEN_WIDTH, SCREEN_HEIGHT)

        # Dev tools
        self.dev_menu              = DevMenu(self.game_config, SCREEN_WIDTH, SCREEN_HEIGHT, self.sound_manager)
        self.npc_config_menu       = NPCConfigMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.sprite_editor         = SpriteEditor(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.transition_config_menu = TransitionConfigMenu(SCREEN_WIDTH, SCREEN_HEIGHT)

        # Active game-object lists
        self.projectiles        = []
        self.melee_attacks      = []
        self.bombs              = []   # BombProjectiles from Shooter enemies.
        self.enemy_bullets      = []   # bullet_projectiles from Gunner enemies.
        self.enemy_rockets      = []   # rocket_projectiles from RocketLauncher enemies.
        self.explosions         = []   # Active ExplosionEffect instances.
        self.enemies            = []
        self.npcs               = []
        self.destructible_stones = []
        self.collision_objects  = []
        self.room_transitions   = []
        self.level_gates        = []
        self.flying_pads        = []

        # Save point system
        self.save_points          = []
        self.save_point_manager   = SavePointManager()
        self.save_point_menu      = SavePointMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.character_switch_menu = CharacterSwitchMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.nearby_save_point    = None

        # Test mode — prevents save operations while a room is being previewed.
        self.is_test_mode     = False
        self.test_room_backup = None

        # Ensure the object editor exists before hooking up callbacks.
        if self.room_editor.object_editor is None:
            from dev_tools.room_editor.room_editor_tools.object_editor import ObjectEditor
            self.room_editor.object_editor = ObjectEditor(
                SCREEN_WIDTH, SCREEN_HEIGHT, self.room_manager
            )

        # Editor callbacks — keep game lists in sync with editor changes.
        self.room_editor.object_editor.on_collision_deleted  = self._on_collision_deleted
        self.room_editor.object_editor.on_stone_deleted      = self._on_stone_deleted
        self.room_editor.object_editor.on_gate_deleted       = self._on_gate_deleted
        self.room_editor.object_editor.on_transition_placed  = self._on_transition_placed
        self.room_editor.object_editor.on_transition_deleted = self._on_transition_deleted
        self.room_editor.object_editor.on_flying_pad_deleted = self._on_flying_pad_deleted
        self.room_editor.object_editor.on_flying_pad_placed  = self._on_flying_pad_placed
        self.room_editor.object_editor.on_save_point_placed  = self._on_save_point_placed
        self.room_editor.object_editor.on_save_point_deleted = self._on_save_point_deleted

        # Sync editor managers with any rooms already loaded from disk.
        self._sync_spawn_manager_with_rooms()

        # Miscellaneous game state
        self.pending_npc_position        = None
        self.nearby_npc                  = None
        self.pending_transition_position = None
        self.last_time                   = time.time()

        # Create the default starting room.
        self._create_default_room()

        # Wire up the flying controller callbacks.
        self.flying_controller.on_room_transition = self._handle_flying_room_transition
        self.flying_controller.on_flight_complete = self._handle_flying_complete

        # Transition / fade system.
        self.transition_controller = TransitionController(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.flying_controller.set_transition_controller(self.transition_controller)

        self.sound_manager.set_context('exploration')

    # ── Initialisation helpers ────────────────────────────────────────────────

    def _create_default_room(self):
        """Create and activate the initial room when the game starts."""
        room = self.room_manager.create_room("Default Room", WORLD_WIDTH, WORLD_HEIGHT, "Default")
        self.room_manager.current_room = room
        self.current_room = room

    # ── Event handling ────────────────────────────────────────────────────────

    def handle_events(self):
        """
        Process all pending pygame events for the current frame.

        Priority order for overlays:
          1. Character-switch menu
          2. Save-point menu
          3. NPC config menu
          4. Room-transition config menu
          5. Sprite editor
          6. Room editor
          7. Dev menu
          8. Normal gameplay input
        """
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            # Character-switch menu (highest overlay priority).
            if self.character_switch_menu.active:
                result = self.character_switch_menu.handle_input(event)
                if result and result != 'close':
                    self._switch_character(result)
                continue

            # Save-point menu.
            if self.save_point_menu.active:
                result = self.save_point_menu.handle_input(event)
                if result == 'save':
                    # TODO: implement save functionality
                    self.save_point_menu.close()
                elif result == 'switch_characters':
                    self.save_point_menu.close()
                    current_character = getattr(self.player, 'character', 'goku')
                    self.character_switch_menu.open(current_character)
                continue

            # NPC config menu.
            if self.npc_config_menu.active:
                result = self.npc_config_menu.handle_input(event)
                if result and result != 'cancel' and self.pending_npc_position:
                    x, y = self.pending_npc_position
                    npc          = NPC(x, y, result)
                    npc.npc_type = result['npc_type']
                    self.npcs.append(npc)
                    self.pending_npc_position = None
                elif result == 'cancel':
                    self.pending_npc_position = None
                continue

            # Room-transition placement config.
            if self.transition_config_menu.active:
                result = self.transition_config_menu.handle_input(event)
                if result and result != 'cancel' and self.pending_transition_position:
                    x, y = self.pending_transition_position
                    transition                = RoomTransition(x, y, result['width'], result['height'])
                    transition.target_room    = result['target_room']
                    transition.exit_direction = result['exit_direction']
                    transition.entry_direction = result['entry_direction']
                    transition.spawn_x        = result['spawn_x']
                    transition.spawn_y        = result['spawn_y']
                    self.room_transitions.append(transition)
                    self.pending_transition_position = None
                elif result == 'cancel':
                    self.pending_transition_position = None
                continue

            # Sprite editor.
            if self.sprite_editor.active:
                self.sprite_editor.handle_input(event)
                continue

            # Room editor.
            if self.room_editor.active:
                result = self.room_editor.handle_input(event)
                if result and result.startswith('test_room:'):
                    self._handle_test_room(result)
                continue

            # Dev menu.
            if self.dev_menu.active:
                result = self.dev_menu.handle_input(event)
                if result == 'open_room_editor':
                    self.dev_menu.active = False
                    if self.is_test_mode:
                        self._exit_test_mode()
                    self.room_editor.toggle()
                elif result == 'open_sprite_editor':
                    self.dev_menu.active = False
                    self.sprite_editor.toggle()
                continue

            # Normal gameplay key events.
            elif event.type == pygame.KEYDOWN:
                if self.ui.current_screen == 'game':
                    self._handle_game_keydown(event)
                elif self.ui.current_screen == 'main_menu':
                    self._handle_menu_keydown(event)
                elif self.ui.current_screen in ('status', 'inventory', 'options'):
                    if event.key == pygame.K_ESCAPE:
                        self.ui.current_screen = 'main_menu'

            elif event.type == pygame.KEYUP:
                if self.ui.current_screen == 'game':
                    if event.key == pygame.K_q:
                        self.player.is_q_pressed = False
                        if self.player.is_charging_beam and not self.player.is_firing_beam:
                            self.player.stop_beam()

    def _handle_game_keydown(self, event):
        """
        Handle key-down events during normal gameplay.

        Args:
            event: The pygame KEYDOWN event.
        """
        if event.key == pygame.K_F1:
            self.dev_menu.toggle()

        elif event.key == pygame.K_ESCAPE:
            if self.is_test_mode:
                self._exit_test_mode()
                self.room_editor.active      = True
                self.room_editor.current_view = 'view_room'
            else:
                self.ui.current_screen      = 'main_menu'
                self.ui.selected_menu_item  = 0

        elif event.key == pygame.K_q:
            if self.player.ki_attack_mode == 'blast':
                self.player.shoot_blast()
            elif self.player.ki_attack_mode == 'beam':
                self.player.start_charging_beam()
                self.player.is_q_pressed = True

        elif event.key == pygame.K_e:
            self._handle_interact()

        elif event.key == pygame.K_TAB:
            # Cycle through ki attack modes.
            modes = ('blast', 'beam', 'transform')
            idx = modes.index(self.player.ki_attack_mode) if self.player.ki_attack_mode in modes else 0
            self.player.ki_attack_mode = modes[(idx + 1) % len(modes)]

        elif event.key == pygame.K_x:
            if (self.player.ki_attack_mode == 'transform'
                    and self.player.transformation
                    and self.player.transformation.is_ready):
                self.player.transformation.start_transform()

        elif event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
            if self.player.check_double_tap(event.key):
                self.player.is_running = True

    def _handle_interact(self):
        """
        Handle the E (interact) key press.

        Priority:
          1. Nearby save point — open the save-point menu (big variant) or
             trigger a quick save (small variant).
          2. Nearby NPC — start or advance dialogue.
          3. Flying pad — begin the flying sequence.
          4. Default — perform a melee attack.
        """
        if self.nearby_save_point and not self.dialogue_box.active and not self.save_point_menu.active:
            if self.nearby_save_point.variant == 'big':
                self.save_point_menu.open()
            else:
                # Quick save (not yet implemented)
                pass
            return

        if self.nearby_npc and not self.dialogue_box.active:
            text, is_final, item = self.nearby_npc.start_dialogue()
            if text:
                self.dialogue_box.show(text, "NPC", is_final, item)
                if item:
                    self.player.inventory.append(item)
            return

        if self.dialogue_box.active:
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
            return

        # Check for a nearby flying pad.
        nearby_pad = next(
            (pad for pad in self.flying_pads
             if pad.active and pad.check_collision_with_player(self.player)),
            None
        )
        if nearby_pad and len(nearby_pad.waypoints) > 0:
            self.flying_controller.start_flight(self.player, nearby_pad)
        else:
            melee = self.player.melee_attack()
            if melee:
                self.melee_attacks.append(melee)
                self.sound_manager.play_sfx('punch')

    def _handle_menu_keydown(self, event):
        """
        Handle key-down events while the main menu is open.

        Args:
            event: The pygame KEYDOWN event.
        """
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

    # ── Test-mode helpers ─────────────────────────────────────────────────────

    def _handle_test_room(self, result):
        """
        Enter test mode for the room named in *result*.

        Backs up all room data, loads object copies into the game lists, and
        spawns the player at the room's designated spawn point.

        Args:
            result: String of the form 'test_room:<room_name>'.
        """
        room_name = result.split(':', 1)[1]
        room      = self.room_manager.get_room_by_name(room_name)
        if not room:
            return

        # Clear all active entities and projectiles before entering test mode.
        self._clear_active_entities()

        self.is_test_mode = True
        self._create_comprehensive_test_backup()

        self.room_manager.current_room = room
        self.current_room              = room

        # Sync tiles from the editor into the room before testing.
        if self.room_editor.tileset_editor and room_name in self.room_editor.tileset_editor.room_tiles:
            room.tiles = self.room_editor.tileset_editor.room_tiles[room_name][:]
        elif not hasattr(room, 'tiles'):
            room.tiles = []

        self._load_room_objects_as_copies(room)

        # Place the player at the room's spawn point.
        if hasattr(room, 'spawn_points') and room.spawn_points:
            spawn_pos = (room.spawn_points[0].x, room.spawn_points[0].y)
        elif room.spawn_point:
            spawn_pos = room.spawn_point
        else:
            spawn_pos = (room.width // 2, room.height // 2)

        self.player.x = spawn_pos[0]
        self.player.y = spawn_pos[1]

        self.camera.x = max(0, self.player.x - self.camera.screen_width  // 2)
        self.camera.y = max(0, self.player.y - self.camera.screen_height // 2)

        self.room_editor.active = False

    def _create_comprehensive_test_backup(self):
        """
        Snapshot the current state of every room so the editor can restore
        them when test mode ends.

        Backs up: collision objects, flying pads, destructible stones, and
        level gates for all rooms tracked by the room manager.
        """
        self.test_room_backup = {}

        for room in self.room_manager.rooms:
            room_backup = {
                'room_name':          room.name,
                'collision_objects':  [],
                'destructible_stones': [],
                'level_gates':        [],
                'flying_pads':        [],
            }

            if hasattr(room, 'collision_objects'):
                for obj in room.collision_objects:
                    room_backup['collision_objects'].append({
                        'x': obj.x, 'y': obj.y,
                        'width': obj.width, 'height': obj.height,
                        'collision_type': getattr(obj, 'collision_type', 'wall')
                    })

            if hasattr(room, 'flying_pads'):
                for pad in room.flying_pads:
                    room_backup['flying_pads'].append({
                        'x': pad.x, 'y': pad.y,
                        'pad_type':       pad.pad_type,
                        'waypoints':      [wp.to_dict() for wp in pad.waypoints],
                        'is_return_pad':  pad.is_return_pad,
                        'linked_pad_id':  pad.linked_pad_id,
                        'width':          pad.width,
                        'height':         pad.height,
                    })

            if hasattr(room, 'destructible_stones'):
                for stone in room.destructible_stones:
                    room_backup['destructible_stones'].append({
                        'x': stone.x, 'y': stone.y,
                        'stone_type': stone.stone_type,
                        'max_health': stone.max_health,
                        'health':     stone.health,
                        'width':      stone.width,
                        'height':     stone.height,
                    })

            if hasattr(room, 'level_gates'):
                for gate in room.level_gates:
                    room_backup['level_gates'].append({
                        'x': gate.x, 'y': gate.y,
                        'gate_type':      gate.gate_type,
                        'required_level': gate.required_level,
                        'max_health':     gate.max_health,
                        'health':         gate.health,
                        'width':          gate.width,
                        'height':         gate.height,
                    })

            self.test_room_backup[room.name] = room_backup

    def _load_room_objects_as_copies(self, room):
        """
        Instantiate independent copies of all room objects into the active
        game lists.  Used during test mode so the originals are not mutated.

        Also creates invisible boundary-wall collision objects around the
        room perimeter to prevent knockback from sending entities off-screen.

        Args:
            room: The room whose objects should be copied.
        """
        if not room:
            return

        # Flying pads
        self.flying_pads = []
        if hasattr(room, 'flying_pads') and room.flying_pads:
            from objects.flying_pad import FlyingPad, FlyingPadWaypoint
            for pad in room.flying_pads:
                copy_pad              = FlyingPad(pad.x, pad.y, pad.pad_type)
                copy_pad.waypoints    = [FlyingPadWaypoint.from_dict(wp.to_dict()) for wp in pad.waypoints]
                copy_pad.is_return_pad = pad.is_return_pad
                copy_pad.linked_pad_id = pad.linked_pad_id
                copy_pad.source_room  = pad.source_room
                copy_pad.current_room = room.name
                copy_pad.width        = pad.width
                copy_pad.height       = pad.height
                copy_pad.active       = True
                self.flying_pads.append(copy_pad)

        # Collision objects
        self.collision_objects = []
        if hasattr(room, 'collision_objects') and room.collision_objects:
            from objects.collision_object import CollisionObject
            for obj in room.collision_objects:
                copy_obj = CollisionObject(obj.x, obj.y, obj.width, obj.height)
                copy_obj.collision_type = getattr(obj, 'collision_type', 'wall')
                self.collision_objects.append(copy_obj)

        # Invisible boundary walls to contain knockback inside the room.
        self._add_room_boundary_walls(room)

        # Destructible stones
        self.destructible_stones = []
        if hasattr(room, 'destructible_stones') and room.destructible_stones:
            from objects.destructible_stone import DestructibleStone
            for stone in room.destructible_stones:
                copy_stone            = DestructibleStone(stone.x, stone.y, stone.stone_type)
                copy_stone.max_health = stone.max_health
                copy_stone.health     = stone.health
                copy_stone.width      = stone.width
                copy_stone.height     = stone.height
                copy_stone.active     = True
                self.destructible_stones.append(copy_stone)

        # Level gates
        self.level_gates = []
        if hasattr(room, 'level_gates') and room.level_gates:
            from objects.level_gate import LevelGate
            for gate in room.level_gates:
                copy_gate            = LevelGate(gate.x, gate.y, gate.gate_type, gate.required_level)
                copy_gate.max_health = gate.max_health
                copy_gate.health     = gate.health
                copy_gate.width      = gate.width
                copy_gate.height     = gate.height
                copy_gate.active     = True
                self.level_gates.append(copy_gate)

        # Room transitions
        self.room_transitions = []
        if hasattr(room, 'room_transitions') and room.room_transitions:
            from objects.room_transition import RoomTransition
            for transition in room.room_transitions:
                copy_t                  = RoomTransition(transition.x, transition.y,
                                                         transition.width, transition.height)
                copy_t.target_room      = transition.target_room
                copy_t.exit_direction   = transition.exit_direction
                copy_t.entry_direction  = transition.entry_direction
                copy_t.spawn_x          = transition.spawn_x
                copy_t.spawn_y          = transition.spawn_y
                copy_t.active           = True
                self.room_transitions.append(copy_t)

        # Save points
        self.save_points = []
        if hasattr(room, 'save_points') and room.save_points:
            from objects.save_point import SavePoint
            for sp in room.save_points:
                copy_sp        = SavePoint(sp.x, sp.y, sp.variant)
                copy_sp.active = True
                self.save_points.append(copy_sp)

        # Entities (enemies and NPCs)
        self.enemies = []
        self.npcs    = []
        self._spawn_room_entities(room)

        # Clear all in-flight projectiles and attacks.
        self._clear_projectiles()

        # Give the player and all enemies a shared obstacle list for knockback.
        self._assign_obstacles()

    def _exit_test_mode(self):
        """
        Restore all rooms to their pre-test state using the backup created
        when test mode was entered, then clear all test entities.
        """
        if not self.is_test_mode or not self.test_room_backup:
            return

        for room_name, backup in self.test_room_backup.items():
            room = self.room_manager.get_room_by_name(room_name)
            if not room:
                continue

            # Rebuild collision objects.
            from objects.collision_object import CollisionObject
            room.collision_objects = []
            for d in backup['collision_objects']:
                obj = CollisionObject(d['x'], d['y'], d['width'], d['height'])
                obj.collision_type = d['collision_type']
                room.collision_objects.append(obj)

            # Rebuild flying pads.
            from objects.flying_pad import FlyingPad, FlyingPadWaypoint
            room.flying_pads = []
            for d in backup.get('flying_pads', []):
                pad            = FlyingPad(d['x'], d['y'], d['pad_type'])
                pad.width      = d['width']
                pad.height     = d['height']
                pad.waypoints  = [FlyingPadWaypoint.from_dict(wp) for wp in d['waypoints']]
                pad.is_return_pad = d['is_return_pad']
                pad.linked_pad_id = d['linked_pad_id']
                room.flying_pads.append(pad)

            if self.room_editor.object_editor:
                self.room_editor.object_editor.flying_pad_manager.flying_pads[room.name] = room.flying_pads

            # Rebuild destructible stones.
            from objects.destructible_stone import DestructibleStone
            room.destructible_stones = []
            for d in backup['destructible_stones']:
                stone            = DestructibleStone(d['x'], d['y'], d['stone_type'])
                stone.max_health = d['max_health']
                stone.health     = d['health']
                stone.width      = d['width']
                stone.height     = d['height']
                room.destructible_stones.append(stone)

            # Rebuild level gates.
            from objects.level_gate import LevelGate
            room.level_gates = []
            for d in backup['level_gates']:
                gate            = LevelGate(d['x'], d['y'], d['gate_type'], d['required_level'])
                gate.max_health = d['max_health']
                gate.health     = d['health']
                gate.width      = d['width']
                gate.height     = d['height']
                room.level_gates.append(gate)

            if self.room_editor.object_editor:
                self.room_editor.object_editor.gate_manager.gates[room.name] = room.level_gates

        self.is_test_mode     = False
        self.test_room_backup = None

        self._clear_active_entities()
        self.destructible_stones = []
        self.collision_objects   = []
        self.level_gates         = []

    def _load_room_objects(self, room):
        """
        Load all room objects directly (no copies) into the active game lists.
        Used during normal gameplay room transitions.

        Also creates invisible boundary walls and rebuilds the obstacle lists
        for the player and all enemies.

        Args:
            room: The room to load.
        """
        if not room:
            return

        # Flying pads
        self.flying_pads = []
        if hasattr(room, 'flying_pads') and room.flying_pads:
            self.flying_pads = room.flying_pads[:]
            for pad in self.flying_pads:
                pad.current_room = room.name

        # Collision objects
        self.collision_objects = []
        if hasattr(room, 'collision_objects') and room.collision_objects:
            self.collision_objects = room.collision_objects[:]

        # Invisible boundary walls to contain knockback inside the room.
        self._add_room_boundary_walls(room)

        # Destructible stones, gates, transitions, save points.
        self.destructible_stones = room.destructible_stones[:] if hasattr(room, 'destructible_stones') and room.destructible_stones else []
        self.level_gates         = room.level_gates[:]         if hasattr(room, 'level_gates')         and room.level_gates         else []
        self.room_transitions    = room.room_transitions[:]    if hasattr(room, 'room_transitions')    and room.room_transitions    else []
        self.save_points         = room.save_points[:]         if hasattr(room, 'save_points')         and room.save_points         else []

        # Spawn entities.
        self.enemies = []
        self.npcs    = []
        self._spawn_room_entities(room)

        # Clear in-flight projectiles from the previous room.
        self._clear_projectiles()

        # Rebuild obstacle lists.
        self._assign_obstacles()

    # ── Private room-loading utilities ────────────────────────────────────────

    def _add_room_boundary_walls(self, room):
        """
        Append four invisible wall collision objects that line the room edges.

        These prevent entities from being knocked outside the room bounds.

        Args:
            room: The room whose dimensions define the boundary.
        """
        from objects.collision_object import CollisionObject

        thickness = 10  # Wall thickness in world units.

        walls = [
            CollisionObject(room.width // 2,              -thickness // 2,              room.width, thickness),  # Top
            CollisionObject(room.width // 2,               room.height + thickness // 2, room.width, thickness),  # Bottom
            CollisionObject(-thickness // 2,               room.height // 2,             thickness,  room.height), # Left
            CollisionObject(room.width + thickness // 2,   room.height // 2,             thickness,  room.height), # Right
        ]
        for wall in walls:
            wall.collision_type    = 'wall'
            wall.is_room_boundary  = True
            self.collision_objects.append(wall)

    def _spawn_room_entities(self, room):
        """
        Instantiate enemies and NPCs from the room's entity data.

        Determines the correct enemy category and shooter style from the
        entity's variant_type so AI and projectile systems behave correctly.

        Args:
            room: The room whose entities list should be spawned.
        """
        if not hasattr(room, 'entities') or not room.entities:
            return

        from entities.enemy import Enemy
        from entities.boss_enemy import BossEnemy
        from entities.npc import NPC

        for data in room.entities:
            entity_type  = data.get('entity_type', 'enemy')
            x            = data.get('x', 0)
            y            = data.get('y', 0)
            enemy_id     = data.get('id', 'tiger_bandit')
            variant_type = data.get('variant_type', 'default')

            if entity_type == 'npc':
                npc        = NPC(x, y)
                npc.active = True
                self.npcs.append(npc)

            elif entity_type == 'boss':
                boss        = BossEnemy(x, y, boss_id=enemy_id, variant=variant_type)
                boss.active = True
                self.enemies.append(boss)

            elif entity_type == 'enemy':
                ai_type        = data.get('ai_type', 'easy')
                enemy_category = data.get('enemy_category', 'melee')

                if variant_type == 'gunner':
                    shooter_style = 'bullet'
                elif variant_type == 'rocketlauncher':
                    shooter_style = 'rocket'
                else:
                    shooter_style = 'bomb'

                enemy        = Enemy(x, y, enemy_type=enemy_id, variant=variant_type,
                                     ai_type=ai_type, enemy_category=enemy_category,
                                     shooter_style=shooter_style)
                enemy.active = True
                self.enemies.append(enemy)

    def _assign_obstacles(self):
        """
        Build a shared obstacle list from all solid objects in the current
        room and assign it to the player and every enemy.

        Obstacles are used to prevent knockback from pushing entities through
        walls, stones, gates, and transitions.
        """
        obstacles = (
            self.collision_objects
            + self.destructible_stones
            + self.level_gates
            + self.room_transitions
        )
        self.player.obstacles = obstacles
        for enemy in self.enemies:
            enemy.obstacles     = obstacles
            enemy.other_enemies = [e for e in self.enemies if e is not enemy]

    def _clear_active_entities(self):
        """Clear enemies, NPCs, and all projectile/attack lists."""
        self.enemies    = []
        self.npcs       = []
        self._clear_projectiles()

    def _clear_projectiles(self):
        """Remove all in-flight projectiles and visual effects."""
        self.projectiles   = []
        self.melee_attacks = []
        self.bombs         = []
        self.enemy_bullets = []
        self.enemy_rockets = []
        self.explosions    = []

    # ── Editor callbacks ──────────────────────────────────────────────────────

    def _on_collision_deleted(self, collision_obj, room_name):
        """Sync game list when a collision object is removed in the editor."""
        if self.is_test_mode:
            return
        if self.current_room and self.current_room.name == room_name:
            if collision_obj in self.collision_objects:
                self.collision_objects.remove(collision_obj)

    def _on_stone_deleted(self, stone, room_name):
        """Sync game list when a destructible stone is removed in the editor."""
        if self.is_test_mode:
            return
        if self.current_room and self.current_room.name == room_name:
            if stone in self.destructible_stones:
                self.destructible_stones.remove(stone)

    def _on_gate_deleted(self, gate, room_name):
        """Sync game list when a level gate is removed in the editor."""
        if self.is_test_mode:
            return
        if self.current_room and self.current_room.name == room_name:
            if gate in self.level_gates:
                self.level_gates.remove(gate)

    def _on_transition_placed(self, transition, room_name):
        """Sync game list when a room transition is placed in the editor."""
        if self.is_test_mode:
            return
        if self.current_room and self.current_room.name == room_name:
            if transition not in self.room_transitions:
                self.room_transitions.append(transition)

    def _on_transition_deleted(self, transition, room_name):
        """Sync game list when a room transition is removed in the editor."""
        if self.is_test_mode:
            return
        if self.current_room and self.current_room.name == room_name:
            if transition in self.room_transitions:
                self.room_transitions.remove(transition)

    def _on_flying_pad_deleted(self, pad, room_name):
        """Sync game list when a flying pad is removed in the editor."""
        if self.is_test_mode:
            return
        if self.current_room and self.current_room.name == room_name:
            if pad in self.flying_pads:
                self.flying_pads.remove(pad)

    def _on_flying_pad_placed(self, pad, room_name):
        """Sync game list when a flying pad is placed in the editor."""
        if self.is_test_mode:
            return
        if self.current_room and self.current_room.name == room_name:
            if pad not in self.flying_pads:
                self.flying_pads.append(pad)

    def _on_save_point_placed(self, save_point):
        """Sync game list when a save point is placed in the editor."""
        if save_point not in self.save_points:
            self.save_points.append(save_point)

    def _on_save_point_deleted(self, save_point):
        """Sync game list when a save point is removed in the editor."""
        if save_point in self.save_points:
            self.save_points.remove(save_point)

    # ── Character switching ───────────────────────────────────────────────────

    def _switch_character(self, character_id):
        """
        Swap the player's visual sprite to *character_id* while preserving
        all gameplay state (position, stats, inventory, etc.).

        Args:
            character_id: String identifier for the target character.
        """
        from core.sprite_system import create_character_sprite

        # Snapshot current state.
        state = {
            'x': self.player.x, 'y': self.player.y,
            'hp': self.player.hp, 'ki': self.player.ki,
            'level': self.player.level,
            'stats': self.player.stats.copy(),
            'inventory': self.player.inventory.copy(),
            'direction': self.player.direction,
        }

        self.player.character = character_id
        self.player.sprite    = create_character_sprite(character_id, 'base', 32, 32)

        # Restore state so the swap is seamless.
        for key, value in state.items():
            setattr(self.player, key, value)

    # ── Flying-controller callbacks ───────────────────────────────────────────

    def _handle_flying_room_transition(self, target_room_name, spawn_x, spawn_y):
        """
        Swap the active room mid-flight.

        Called by FlyingController at the midpoint of a boundary-waypoint
        transition.  Loads room objects in the appropriate mode (copy vs
        direct) and repositions the camera at the spawn location.

        Args:
            target_room_name: Name of the destination room.
            spawn_x:          Player spawn X in the destination room (world units).
            spawn_y:          Player spawn Y in the destination room (world units).
        """
        target_room = self.room_manager.get_room_by_name(target_room_name)
        if not target_room:
            return

        self.room_manager.current_room = target_room
        self.current_room              = target_room

        if self.is_test_mode:
            self._load_room_objects_as_copies(target_room)
        else:
            self._load_room_objects(target_room)

        # Reposition camera at the new spawn location and clamp to room bounds.
        self.camera.x = max(0, (spawn_x * RENDER_SCALE) - self.camera.screen_width  // 2)
        self.camera.y = max(0, (spawn_y * RENDER_SCALE) - self.camera.screen_height // 2)
        self.camera.x = max(0, min(self.camera.x, target_room.width  * RENDER_SCALE - SCREEN_WIDTH))
        self.camera.y = max(0, min(self.camera.y, target_room.height * RENDER_SCALE - SCREEN_HEIGHT))

    def _handle_flying_complete(self):
        """
        Called when the flying sequence ends.
        Player control is already restored by FlyingController.
        """
        pass

    # ── Main update ───────────────────────────────────────────────────────────

    def update(self):
        """
        Advance all game systems by one frame.

        Covers: input/movement, collision, projectiles, enemy AI, item
        pickups, save points, UI notifications, and dev-tool overlays.
        """
        current_time = time.time()
        dt           = current_time - self.last_time
        self.last_time = current_time

        enemies_defeated_this_frame = 0

        self.character_switch_menu.update(dt)
        self.save_point_menu.update(dt)

        if self.ui.current_screen == 'game':
            if not self.save_point_menu.active and not self.character_switch_menu.active:
                self._update_player_movement(dt)

            self.player.update(dt)
            if self.player.hurt_tint > 0:
                self.player.hurt_tint = max(0.0, self.player.hurt_tint - dt / self.player.hurt_tint_duration)

            self.camera.update(self.player, self.current_room.width, self.current_room.height, dt)

            # Spawn the player's blast projectile when the throw animation completes.
            if self.player.pending_blast == 'ready':
                spawn_x, spawn_y = self.player.get_blast_spawn_position()
                self.projectiles.append(Projectile(spawn_x, spawn_y, self.player.direction))
                self.sound_manager.play_sfx('blast')
                self.player.pending_blast = None

            # Screen transition update.
            if self.transition_controller.is_transitioning():
                self.transition_controller.update(dt, self.player)

            # Flying controller update.
            if self.flying_controller.is_active():
                self.flying_controller.update(dt)
                if not self.flying_controller.is_transitioning_rooms:
                    self.camera.update(self.player, self.current_room.width,
                                       self.current_room.height, dt)

            # Walk-based room transition detection.
            if not self.transition_controller.is_transitioning():
                self._check_room_transitions()

            self.level_up_notification.update(dt)

            # Beam mechanics.
            self._update_beam(dt)

            # Player projectiles.
            for projectile in self.projectiles[:]:
                projectile.update(self.current_room.width, self.current_room.height, dt)
                if not projectile.active:
                    self.projectiles.remove(projectile)

            # Melee attacks.
            for melee in self.melee_attacks[:]:
                melee.update(dt)
                if not melee.active:
                    self.melee_attacks.remove(melee)

            # Enemy AI, combat, and defeat handling.
            enemies_defeated_this_frame = self._update_enemies(dt)

            # Enemy projectile systems.
            self._update_bombs(dt)
            self._update_enemy_bullets(dt)
            self._update_enemy_rockets(dt)

            # Explosion visuals.
            for explosion in self.explosions[:]:
                explosion.update(dt)
                if not explosion.active:
                    self.explosions.remove(explosion)

            # NPC interaction detection.
            self._update_npcs(dt)

            # Save point proximity detection.
            self._update_save_points(dt)

            # Destructible stones.
            self._update_stones(dt)

            # Level gates.
            self._update_gates(dt)

            # Transformation system.
            if self.player.transformation:
                self.player.transformation.update(dt, enemies_defeated_this_frame)

            # Adaptive music.
            self.sound_manager.update_battle_state(dt, len(self.enemies) > 0)

            # Dev-tool overlays pause simulation when active.
            if self.dev_menu.active:
                self.dev_menu.update(dt)
                return
            if self.sprite_editor.active:
                self.sprite_editor.update(dt)
                return
            if self.room_editor.active:
                self.room_editor.update(dt)
                return

    # ── Update sub-routines ───────────────────────────────────────────────────

    def _update_player_movement(self, dt):
        """
        Read directional input and move the player, resolving collisions
        with walls, stones, and gates.

        Movement is suppressed while a flying sequence is active.

        Args:
            dt: Delta time in seconds.
        """
        keys       = pygame.key.get_pressed()
        dx = dy    = 0
        is_running = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT] or self.player.is_running

        if keys[pygame.K_LEFT]  and not keys[pygame.K_RIGHT]: dx = -1
        elif keys[pygame.K_RIGHT] and not keys[pygame.K_LEFT]: dx = 1
        if keys[pygame.K_UP]    and not keys[pygame.K_DOWN]:  dy = -1
        elif keys[pygame.K_DOWN] and not keys[pygame.K_UP]:   dy = 1

        if dx == 0 and dy == 0:
            self.player.is_running = False
            if not self.player.is_transitioning:
                if self.player.current_animation_state in ('walk', 'run'):
                    self.player.sprite.set_animation('idle', self.player.direction)
                    self.player.current_animation_state = 'idle'

        if (dx != 0 or dy != 0) and not self.flying_controller.is_active():
            old_x = self.player.x
            old_y = self.player.y
            self.player.move(dx, dy, is_running, self.current_room.width, self.current_room.height)

            # Resolve collisions (stones → gates → walls).
            collision = False
            for stone in self.destructible_stones:
                if stone.check_collision_with_player(self.player):
                    self.player.x = old_x
                    self.player.y = old_y
                    if is_running:
                        self.player.start_collision_knockback(dx, dy)
                        self.camera.start_shake(intensity=15, duration=0.3)
                    collision = True
                    break

            if not collision:
                for gate in self.level_gates:
                    if gate.active and gate.check_collision_with_player(self.player):
                        self.player.x = old_x
                        self.player.y = old_y
                        if is_running:
                            self.player.start_collision_knockback(dx, dy)
                            self.camera.start_shake(intensity=15, duration=0.3)
                        collision = True
                        break

            if not collision:
                for obj in self.collision_objects:
                    if obj.check_collision_with_player(self.player):
                        self.player.x = old_x
                        self.player.y = old_y
                        if is_running:
                            self.player.start_collision_knockback(dx, dy)
                            self.camera.start_shake(intensity=15, duration=0.3)
                        break

    def _check_room_transitions(self):
        """
        Detect when the player walks into a room-transition zone and start
        the fade/teleport sequence.
        """
        for transition in self.room_transitions:
            if transition.active and transition.check_collision(self.player):

                def complete_transition(target_room_name, spawn_x, spawn_y):
                    target_room = self.room_manager.get_room_by_name(target_room_name)
                    if target_room:
                        self.room_manager.current_room = target_room
                        self.current_room              = target_room
                        if self.is_test_mode:
                            self._load_room_objects_as_copies(target_room)
                        else:
                            self._load_room_objects(target_room)
                        self.player.is_transitioning = False

                self.player.is_transitioning = True
                self.transition_controller.start_transition(
                    self.player, transition, complete_transition
                )
                break

    def _update_beam(self, dt):
        """
        Handle beam charging and auto-fire when fully charged.

        Args:
            dt: Delta time in seconds.
        """
        if self.player.is_charging_beam:
            self.player.update_beam_charge(dt)

        if (not self.player.is_firing_beam
                and self.player.beam_charge_time >= self.player.beam_charge_required):
            beam = self.player.fire_beam_auto()
            if beam:
                self.player.current_beam = beam
                self.sound_manager.play_sfx('beam')

        if self.player.current_beam:
            self.player.current_beam.update(dt)
            if not self.player.is_firing_beam:
                self.player.current_beam = None

    def _update_enemies(self, dt):
        """
        Run AI for all active enemies, check combat interactions, and remove
        defeated enemies.

        Returns:
            Number of enemies defeated this frame (used for XP/transformation).
        """
        defeated = 0

        for enemy in self.enemies[:]:
            enemy.update(dt, self.player, self.current_room.width, self.current_room.height)

            for melee in self.melee_attacks:
                if melee.active:
                    enemy.check_collision_with_attack(melee, 'melee')
                    self.sound_manager.play_sfx('enemy_hit')

            for projectile in self.projectiles:
                if projectile.active and enemy.check_collision_with_attack(projectile, 'projectile'):
                    projectile.active = False

            if self.player.current_beam:
                enemy.check_collision_with_attack(self.player.current_beam, 'beam')

            if not enemy.active:
                defeated  += 1
                xp_reward  = enemy.get_xp_reward(self.game_config)
                self.player.gain_exp(xp_reward, self.game_config)

                if self.player.pending_level_up:
                    self.level_up_notification.show(self.player.level, self.player.stat_points)
                    self.player.pending_level_up = False

                self.enemies.remove(enemy)

        return defeated

    def _update_bombs(self, dt):
        """
        Poll Shooter enemies for new bomb spawns, advance all active bombs,
        and collect explosion effects when a bomb detonates.

        Args:
            dt: Delta time in seconds.
        """
        for enemy in self.enemies:
            if not (hasattr(enemy, 'enemy_category') and enemy.enemy_category == 'shooter'):
                continue

            bomb_data = enemy.get_bomb_spawn_data()
            if bomb_data:
                self.bombs.append(BombProjectile(
                    start_x=bomb_data['start_x'], start_y=bomb_data['start_y'],
                    target_x=bomb_data['target_x'], target_y=bomb_data['target_y'],
                    damage=bomb_data['damage'], flight_time=bomb_data['flight_time'],
                    player=self.player
                ))

            bullet_data = enemy.get_bullet_spawn_data()
            if bullet_data:
                self.enemy_bullets.append(bullet_projectile(
                    x=bullet_data['x'], y=bullet_data['y'],
                    dx=bullet_data['dx'], dy=bullet_data['dy'],
                    speed=bullet_data['speed'], damage=bullet_data['damage'],
                    direction=bullet_data['direction']
                ))

            rocket_data = enemy.get_rocket_spawn_data()
            if rocket_data:
                self.enemy_rockets.append(rocket_projectile(
                    x=rocket_data['x'], y=rocket_data['y'],
                    dx=rocket_data['dx'], dy=rocket_data['dy'],
                    speed=rocket_data['speed'], damage=rocket_data['damage'],
                    direction=rocket_data['direction']
                ))

        for bomb in self.bombs[:]:
            bomb.update(dt, self.player)

            if bomb.pending_explosion is not None and bomb.pending_explosion not in self.explosions:
                self.explosions.append(bomb.pending_explosion)

            if not bomb.active:
                self.bombs.remove(bomb)

    def _update_enemy_bullets(self, dt):
        """
        Advance all Gunner bullets, check player collision, and remove
        spent bullets.

        Args:
            dt: Delta time in seconds.
        """
        for bullet in self.enemy_bullets[:]:
            bullet.update(self.current_room.width, self.current_room.height, dt)

            if bullet.check_collision_with_player(self.player):
                # Compute knock direction away from the bullet.
                dx   = self.player.x - bullet.x
                dy   = self.player.y - bullet.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0:
                    dx /= dist
                    dy /= dist
                else:
                    dx, dy = bullet.dx, bullet.dy

                self.player.take_damage(bullet.damage, dx, dy)
                self.player.hurt_tint = 1.0
                bullet.active = False

            if not bullet.active:
                self.enemy_bullets.remove(bullet)

    def _update_enemy_rockets(self, dt):
        """
        Advance all RocketLauncher rockets, check player collision, and remove
        spent rockets.

        Args:
            dt: Delta time in seconds.
        """
        for rocket in self.enemy_rockets[:]:
            rocket.update(self.current_room.width, self.current_room.height, dt)

            if rocket.check_collision_with_player(self.player):
                self.player.hurt_tint = 1.0

            if not rocket.active:
                self.enemy_rockets.remove(rocket)

    def _update_npcs(self, dt):
        """
        Tick all NPCs and track which one (if any) is in interaction range.

        Args:
            dt: Delta time in seconds.
        """
        self.nearby_npc = None
        for npc in self.npcs[:]:
            npc.update(dt, self.player, self.current_room.width, self.current_room.height)
            if npc.can_interact(self.player):
                self.nearby_npc = npc

    def _update_save_points(self, dt):
        """
        Tick all save points and track which one (if any) is closest to
        the player.

        Args:
            dt: Delta time in seconds.
        """
        for sp in self.save_points:
            sp.update(dt, self.player)

        self.nearby_save_point = next(
            (sp for sp in self.save_points if sp.is_player_nearby and sp.active),
            None
        )

    def _update_stones(self, dt):
        """
        Tick all destructible stones, check melee attack collisions, and
        remove any that have been destroyed.

        Args:
            dt: Delta time in seconds.
        """
        for stone in self.destructible_stones[:]:
            stone.update(dt)
            for melee in self.melee_attacks:
                if melee.active and stone.check_collision_with_attack(melee, 'melee'):
                    self.sound_manager.play_sfx('punch')
            if not stone.active:
                self.destructible_stones.remove(stone)

    def _update_gates(self, dt):
        """
        Tick all level gates, enforce level requirements, check attack
        collisions, and remove any that have been destroyed.

        Args:
            dt: Delta time in seconds.
        """
        for gate in self.level_gates[:]:
            gate.update(dt)

            # Push the player back if they don't meet the level requirement.
            if gate.active and gate.check_collision_with_player(self.player):
                if not gate.can_be_destroyed_by(self.player):
                    dx       = self.player.x - gate.x
                    dy       = self.player.y - gate.y
                    distance = (dx ** 2 + dy ** 2) ** 0.5
                    if distance > 0:
                        self.player.x += (dx / distance) * 2
                        self.player.y += (dy / distance) * 2

            for melee in self.melee_attacks:
                if melee.active and gate.check_collision_with_attack(melee, 'melee', self.player):
                    self.sound_manager.play_sfx('punch')

            for projectile in self.projectiles:
                if projectile.active:
                    gate.check_collision_with_attack(projectile, 'projectile')

            if self.player.current_beam:
                gate.check_collision_with_attack(self.player.current_beam, 'beam')

            if not gate.active:
                self.level_gates.remove(gate)

    # ── Draw ──────────────────────────────────────────────────────────────────

    def draw(self):
        """
        Render the entire frame:
          1. Background fill and grid
          2. Room boundary indicator
          3. Flying pad sprites (and path preview in dev mode)
          4. Save points
          5. Background tiles
          6. Layered game objects (player, enemies, projectiles, etc.)
          7. Foreground tiles
          8. UI overlays (HUD, dialogue, menus, dev tools)
        """
        self.screen.fill((34, 139, 34))

        visible_x_start = self.camera.x // RENDER_SCALE
        visible_y_start = self.camera.y // RENDER_SCALE
        visible_x_end   = (self.camera.x + SCREEN_WIDTH)  // RENDER_SCALE
        visible_y_end   = (self.camera.y + SCREEN_HEIGHT) // RENDER_SCALE

        # Grid lines
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

        # Room boundary outline
        pygame.draw.rect(self.screen, self.colors['RED'], (
            (0 * RENDER_SCALE) - self.camera.x,
            (0 * RENDER_SCALE) - self.camera.y,
            self.current_room.width  * RENDER_SCALE,
            self.current_room.height * RENDER_SCALE
        ), 3)

        # Test-mode indicator banner
        if self.is_test_mode:
            test_font = pygame.font.Font(None, 32)
            test_text = test_font.render("TEST MODE — Press ESC to return to editor", True, (255, 255, 0))
            test_bg   = pygame.Surface((test_text.get_width() + 20, test_text.get_height() + 10), pygame.SRCALPHA)
            test_bg.fill((0, 0, 0, 180))
            bg_x = (SCREEN_WIDTH - test_text.get_width()) // 2 - 10
            self.screen.blit(test_bg,   (bg_x, 10))
            self.screen.blit(test_text, ((SCREEN_WIDTH - test_text.get_width()) // 2, 15))

        # Spawn point sprite
        if hasattr(self, 'room_editor') and self.room_editor and self.room_editor.object_editor:
            spawn_obj = self.room_editor.object_editor.spawn_manager.get_spawn_point(self.current_room.name)
            if spawn_obj:
                sx = (spawn_obj.x * RENDER_SCALE) - self.camera.x
                sy = (spawn_obj.y * RENDER_SCALE) - self.camera.y
                if spawn_obj.sprite:
                    sw = int(spawn_obj.width  * RENDER_SCALE)
                    sh = int(spawn_obj.height * RENDER_SCALE)
                    scaled = pygame.transform.scale(spawn_obj.sprite, (sw, sh))
                    self.screen.blit(scaled, (int(sx - sw // 2), int(sy - sh // 2)))

        # Flying pads
        for pad in self.flying_pads:
            if pad.active:
                pad.draw(self.screen, self.camera, self.colors, RENDER_SCALE)
                if self.dev_menu.active or self.room_editor.active:
                    pad.draw_path_preview(self.screen, self.camera, RENDER_SCALE)

        # "E to Fly" indicator when player stands on a pad
        if not self.flying_controller.is_active():
            nearby_pad = next(
                (pad for pad in self.flying_pads
                 if pad.active and pad.check_collision_with_player(self.player)),
                None
            )
            if nearby_pad:
                px = (nearby_pad.x * RENDER_SCALE) - self.camera.x
                py = ((nearby_pad.y - 25) * RENDER_SCALE) - self.camera.y

                font  = pygame.font.Font(None, 20)
                text  = font.render("E to Fly", True, self.colors['YELLOW'])
                trect = text.get_rect(center=(px, py))
                bg    = pygame.Surface((trect.width + 10, trect.height + 5), pygame.SRCALPHA)
                bg.fill((0, 0, 0, 180))
                self.screen.blit(bg,   trect.inflate(10, 5).topleft)
                self.screen.blit(text, trect)

        # Save points
        for sp in self.save_points:
            if sp.active:
                sp.draw(self.screen, self.camera, self.colors, RENDER_SCALE)

        # Background tile layer
        if self.room_editor.active and self.room_editor.tileset_editor:
            self.room_editor.tileset_editor.draw_tiles(
                self.screen, int(self.camera.x), int(self.camera.y),
                self.current_room.name, layer='background'
            )
        elif hasattr(self.current_room, 'tiles') and self.current_room.tiles:
            for tile in self.current_room.tiles:
                if tile.layer < 0:
                    self._draw_tile(tile)

        # Layered game objects (y-sorted)
        self.layer_manager.clear()
        for obj in self.projectiles + [self.player] + self.enemies + self.npcs \
                + self.destructible_stones + self.level_gates + self.bombs + self.explosions:
            self.layer_manager.add_object(obj)
        for melee in self.melee_attacks:
            self.layer_manager.add_object(melee)
        if self.player.current_beam:
            self.layer_manager.add_object(self.player.current_beam)

        # Collision debug outlines (dev mode only)
        if self.dev_menu.active or self.room_editor.active:
            from objects.collision_object import draw_collision_object
            for obj in self.collision_objects:
                draw_collision_object(self.screen, obj, self.camera.x, self.camera.y,
                                      RENDER_SCALE, dev_mode=True, selected=False)

        # Enemy bullets and rockets (not y-sorted)
        for bullet in self.enemy_bullets:
            bullet.draw(self.screen, self.camera, self.colors)
        for rocket in self.enemy_rockets:
            rocket.draw(self.screen, self.camera, self.colors)

        self.layer_manager.draw_all(self.screen, self.camera, self.colors, RENDER_SCALE)

        # Player hurt tint — redraw player sprite with a red overlay
        if self.player.hurt_tint > 0 and hasattr(self.player, 'sprite') and self.player.sprite:
            self.player.sprite.draw(self.screen, self.player.x, self.player.y,
                                    self.camera, RENDER_SCALE, self.player.hurt_tint)

        # Foreground tile layer
        if self.room_editor.active and self.room_editor.tileset_editor:
            self.room_editor.tileset_editor.draw_tiles(
                self.screen, int(self.camera.x), int(self.camera.y),
                self.current_room.name, layer='foreground'
            )
        elif hasattr(self.current_room, 'tiles') and self.current_room.tiles:
            for tile in self.current_room.tiles:
                if tile.layer >= 0:
                    self._draw_tile(tile)

        # HUD, menus, and dev overlays
        if not self.dev_menu.active:
            self._draw_ui()

        self.sprite_editor.draw(self.screen)
        self.room_editor.draw(self.screen)
        self.dev_menu.draw(self.screen)

        pygame.display.flip()

    def _draw_tile(self, tile):
        """
        Blit a single tile onto the screen at its world position.

        Args:
            tile: Tile object with tileset_name, tile_x, tile_y, x, y attributes.
        """
        tileset = self.room_editor.tileset_editor.tileset_manager.get_tileset(tile.tileset_name)
        if not tileset or not tileset.image:
            return
        tile_surface = tileset.get_tile_surface(tile.tile_x, tile.tile_y)
        if not tile_surface:
            return
        screen_x    = (tile.x * RENDER_SCALE) - self.camera.x
        screen_y    = (tile.y * RENDER_SCALE) - self.camera.y
        scaled      = pygame.transform.scale(
            tile_surface,
            (tileset.tile_width * RENDER_SCALE, tileset.tile_height * RENDER_SCALE)
        )
        self.screen.blit(scaled, (int(screen_x), int(screen_y)))

    def _draw_ui(self):
        """Draw all UI elements that appear on top of the game world."""
        # NPC interaction indicator
        if self.nearby_npc and not self.dialogue_box.active:
            sx = (self.nearby_npc.x * RENDER_SCALE) - self.camera.x
            sy = ((self.nearby_npc.y - 20) * RENDER_SCALE) - self.camera.y
            r  = 6 * RENDER_SCALE
            pygame.draw.circle(self.screen, self.colors['YELLOW'], (int(sx), int(sy)), r)
            pygame.draw.circle(self.screen, self.colors['WHITE'],  (int(sx), int(sy)), r, 1)

        # Save-point interaction indicator
        if self.nearby_save_point and not self.save_point_menu.active and not self.character_switch_menu.active:
            sx = (self.nearby_save_point.x * RENDER_SCALE) - self.camera.x
            sy = ((self.nearby_save_point.y - 25) * RENDER_SCALE) - self.camera.y
            r  = 6 * RENDER_SCALE
            pygame.draw.circle(self.screen, self.colors['YELLOW'], (int(sx), int(sy)), r)
            pygame.draw.circle(self.screen, self.colors['WHITE'],  (int(sx), int(sy)), r, 1)

        self.npc_config_menu.draw(self.screen, self.colors)
        self.dialogue_box.draw(self.screen, self.colors)
        self.save_point_menu.draw(self.screen)
        self.character_switch_menu.draw(self.screen)
        self.level_up_notification.draw(self.screen, self.colors)

        # Room-transition zone outlines (hidden in test mode)
        if not self.is_test_mode:
            for transition in self.room_transitions:
                transition.draw(self.screen, self.camera, RENDER_SCALE,
                                dev_mode=self.dev_menu.active, selected=False)

        self.transition_config_menu.draw(self.screen)
        self.transition_controller.draw(self.screen)

        if self.ui.current_screen == 'game' and not self.character_switch_menu.active:
            self.sprite_hud.draw(self.screen, self.player)

        if self.ui.current_screen == 'main_menu':
            self.ui.draw_main_menu(self.screen, self.colors)
        elif self.ui.current_screen == 'status':
            self.ui.draw_status_screen(self.screen, self.player, self.game_config, self.colors)
        elif self.ui.current_screen == 'inventory':
            self.ui.draw_inventory_screen(self.screen, self.player, self.colors)
        elif self.ui.current_screen == 'options':
            self.ui.draw_options_screen(self.screen, self.colors)

    # ── Editor / room sync ────────────────────────────────────────────────────

    def _sync_spawn_manager_with_rooms(self):
        """
        Point all editor manager dictionaries directly at the corresponding
        lists on each Room object.

        This ensures that changes made in the editor are immediately
        reflected in the game without requiring a reload, and that any data
        already on the rooms is visible to the editor on startup.
        """
        if not hasattr(self.room_editor, 'object_editor') or not self.room_editor.object_editor:
            return

        oe = self.room_editor.object_editor
        spawn_manager      = oe.spawn_manager
        collision_manager  = oe.collision_manager
        transition_manager = oe.transition_manager
        gate_manager       = oe.gate_manager
        flying_pad_manager = oe.flying_pad_manager

        for room in self.room_manager.rooms:
            if hasattr(room, 'spawn_points') and room.spawn_points:
                for spawn in room.spawn_points:
                    spawn_manager.spawn_points[room.name] = spawn

            if not hasattr(room, 'collision_objects'):
                room.collision_objects = []
            collision_manager.collision_objects[room.name] = room.collision_objects

            if not hasattr(room, 'flying_pads'):
                room.flying_pads = []
            flying_pad_manager.flying_pads[room.name] = room.flying_pads

            if not hasattr(room, 'room_transitions'):
                room.room_transitions = []
            transition_manager.transitions[room.name] = room.room_transitions

            if not hasattr(room, 'level_gates'):
                room.level_gates = []
            gate_manager.gates[room.name] = room.level_gates

            if not hasattr(room, 'save_points'):
                room.save_points = []
            self.save_point_manager.save_points[room.name] = room.save_points

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def cleanup(self):
        """
        Persist all editor changes and room data to disk before quitting.
        Exits test mode first to avoid saving temporary test state.
        """
        if self.is_test_mode:
            self._exit_test_mode()

        if hasattr(self, 'room_editor') and self.room_editor:
            self.room_editor.save_all_editor_data_to_rooms()

        if hasattr(self, 'room_manager'):
            self.room_manager.save_all_rooms()

    def run(self):
        """Start the main game loop. Calls cleanup() before exiting."""
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