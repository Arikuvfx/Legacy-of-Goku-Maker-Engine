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
from ui.damage_number import DamageNumberManager
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
from ui.pause_menu import PauseMenu
from dev_tools.room_editor.room_editor_tools.mission_manager import MissionManager
from dev_tools.cutscene_editor import CutsceneEditor


# Tell Windows this process is DPI-aware so it reports the true screen resolution.
# Must run before pygame.init() or the window will render at the wrong scale on
# high-DPI displays (e.g. Surface devices, 4K monitors with 150 % scaling).
if sys.platform == 'win32':
    try:
        import ctypes as _ctypes
        _ctypes.windll.shcore.SetProcessDpiAwareness(1)   # Per-Monitor DPI awareness
    except Exception:
        try:
            _ctypes.windll.user32.SetProcessDPIAware()    # Legacy fallback
        except Exception:
            pass   # If both calls fail, just carry on — it's cosmetic only


class Game:
    """
    Top-level controller. Owns every subsystem (rendering, audio, rooms, input),
    drives the main loop, and routes communication between them.
    """

    def __init__(self):
        pygame.init()

        # Whitelist only the events we actually handle.
        # All other event types are blocked so pygame's queue never fills up
        # with noise (joystick axis spam, timer ticks we never requested, etc.).
        ALLOWED = [
            pygame.QUIT,
            pygame.KEYDOWN, pygame.KEYUP,
            pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP,
            pygame.MOUSEMOTION, pygame.MOUSEWHEEL,
            pygame.WINDOWRESIZED, pygame.WINDOWFOCUSGAINED, pygame.WINDOWFOCUSLOST,
            pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP,
            pygame.JOYAXISMOTION, pygame.JOYHATMOTION,
            pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED,
            pygame.USEREVENT,
        ]
        pygame.event.set_blocked(None)
        for ev_type in ALLOWED:
            pygame.event.set_allowed(ev_type)

        # SCALED mode: pygame handles window-resize scaling in hardware for free,
        # eliminating the per-frame pygame.transform.scale() call on the full surface.
        self.screen = pygame.display.set_mode(
            (SCREEN_WIDTH, SCREEN_HEIGHT), pygame.RESIZABLE | pygame.SCALED
        )
        pygame.display.set_caption("Legacy of Goku Style Engine")

        # With SCALED mode the screen IS the logical surface — no extra blit needed.
        self.logical_surface = self.screen
        self.clock   = pygame.time.Clock()
        self.running = True
        self.colors  = get_colors()

        # ── Rendering ─────────────────────────────────────────────────────────
        self.layer_manager = LayerManager()
        self.dmg_numbers   = DamageNumberManager()  # Floating hit-number popups

        # ── Core systems ──────────────────────────────────────────────────────
        self.game_config   = GameConfig()
        self.sound_engine  = SoundEngine()
        self.sound_manager = SoundManager(self.sound_engine)
        AudioAssetLoader.load_from_directory(self.sound_engine)

        # ── Player ────────────────────────────────────────────────────────────
        self.player = Player(WORLD_WIDTH // 2, WORLD_HEIGHT // 2, game_config=self.game_config)
        self.player.update_derived_stats()
        self.player.transformation = TransformationSystem(self.player, self.game_config)
        self.player.in_transition  = False

        # Defensive defaults — some older save files may not have these attributes.
        if not hasattr(self.player, 'hurt_tint'):
            self.player.hurt_tint = 0.0
        if not hasattr(self.player, 'hurt_tint_duration'):
            self.player.hurt_tint_duration = 0.45

        # ── Camera and UI ─────────────────────────────────────────────────────
        self.camera                = Camera(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.ui                    = UI(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.sprite_hud            = SpriteHUD(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.dialogue_box          = DialogueBox(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.dialogue_box.set_player(self.player)
        self.level_up_notification = LevelUpNotification(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.flying_controller     = FlyingController(SCREEN_WIDTH, SCREEN_HEIGHT)

        # ── Room system ───────────────────────────────────────────────────────
        self.room_manager = RoomManager()
        self.current_room = None
        self.room_editor  = RoomEditor(self.room_manager, SCREEN_WIDTH, SCREEN_HEIGHT)
        # Let the editor share the game's baked-tile blitting path.
        self.room_editor.blit_tiles_callback = self.blit_room_tiles
        # Let the editor flush the baked-surface cache before each draw so
        # deleted/painted tiles are visible immediately without leaving the editor.
        self.room_editor.flush_tile_cache_callback = self._flush_dirty_tile_rooms

        # ── Dev tools ─────────────────────────────────────────────────────────
        self.dev_menu             = DevMenu(self.game_config, SCREEN_WIDTH, SCREEN_HEIGHT, self.sound_manager)
        self.npc_config_menu      = NPCConfigMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.sprite_editor        = SpriteEditor(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.transition_config_menu = TransitionConfigMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.cutscene_editor = CutsceneEditor(
            self.room_manager,
            self.room_editor,
            SCREEN_WIDTH,
            SCREEN_HEIGHT,
            dialogue_box=self.dialogue_box,
        )

        # ── Active cutscene runtime ───────────────────────────────────────────
        # Set when a CutsceneTrigger fires; None the rest of the time.
        self.active_cutscene_runtime = None
        self.dt = 0.0

        # Cutscene transition fade state machine.
        #   _csf_state:   None | 'fade_out' | 'start' | 'fade_in'
        #   _csf_alpha:   current overlay alpha (0 = transparent, 255 = full black)
        #   _csf_pending: cutscene data dict waiting to launch after fade-out
        _FADE_DUR         = 0.4                    # seconds for each half of the fade
        self._csf_state   = None
        self._csf_alpha   = 0.0
        self._csf_speed   = 255.0 / _FADE_DUR      # alpha units per second
        self._csf_pending = None

        # ── Active game-object lists ──────────────────────────────────────────
        self.projectiles          = []
        self.melee_attacks        = []
        self.bombs                = []   # BombProjectiles from Shooter enemies
        self.enemy_bullets        = []   # bullet_projectiles from Gunner enemies
        self.enemy_rockets        = []   # rocket_projectiles from RocketLauncher enemies
        self.enemy_kiblasts       = []   # Projectiles from kiblast-style enemies (e.g. Android 17/18)
        self.explosions           = []   # Active ExplosionEffect instances
        self.enemies              = []
        self.npcs                 = []
        self.destructible_stones  = []
        self.collision_objects    = []
        self.room_transitions     = []
        self.level_gates          = []
        self.flying_pads          = []

        # ── Performance caches ────────────────────────────────────────────────
        # key: (room_name, is_background) → pre-baked tile Surface
        self._room_tile_surfaces: dict = {}
        self._dirty_tile_rooms:   set  = set()   # rooms pending a surface rebuild
        # key: font_size → pygame.Font  (avoids allocating a Font every frame)
        self._font_cache: dict = {}

        # ── Save point system ─────────────────────────────────────────────────
        self.save_points           = []
        self.save_point_manager    = SavePointManager()
        self.save_point_menu       = SavePointMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.character_switch_menu = CharacterSwitchMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.pause_menu            = PauseMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.play_time             = 0.0   # total seconds spent in gameplay
        self.nearby_save_point     = None

        # ── Test mode ─────────────────────────────────────────────────────────
        # Prevents save operations while a room is being previewed live.
        self.is_test_mode           = False
        self.test_room_backup       = None
        self._test_mission_snapshot = None

        # ── Editor callbacks ──────────────────────────────────────────────────
        # Ensure the object editor exists before we try to hook into it.
        if self.room_editor.object_editor is None:
            from dev_tools.room_editor.room_editor_tools.object_editor import ObjectEditor
            self.room_editor.object_editor = ObjectEditor(
                SCREEN_WIDTH, SCREEN_HEIGHT, self.room_manager
            )

        # Wire callbacks so the active game lists stay in sync with editor changes.
        oe = self.room_editor.object_editor
        oe.on_collision_deleted        = self._on_collision_deleted
        oe.on_stone_deleted            = self._on_stone_deleted
        oe.on_gate_deleted             = self._on_gate_deleted
        oe.on_transition_placed        = self._on_transition_placed
        oe.on_transition_deleted       = self._on_transition_deleted
        oe.on_flying_pad_deleted       = self._on_flying_pad_deleted
        oe.on_flying_pad_placed        = self._on_flying_pad_placed
        oe.on_save_point_placed        = self._on_save_point_placed
        oe.on_save_point_deleted       = self._on_save_point_deleted
        oe.on_cutscene_trigger_placed  = self._on_cutscene_trigger_placed
        oe.on_cutscene_trigger_deleted = self._on_cutscene_trigger_deleted

        # Tile-change hook is installed lazily in draw() once tileset_editor exists.
        self._tile_change_hook_installed = False

        # Sync editor manager dicts with any rooms already loaded from disk.
        self._sync_spawn_manager_with_rooms()

        # ── Miscellaneous game state ──────────────────────────────────────────
        self.pending_npc_position        = None
        self.nearby_npc                  = None
        self.pending_transition_position = None
        self.last_time                   = time.time()

        # ── Mission system ────────────────────────────────────────────────────
        self.mission_manager          = MissionManager()
        self._active_mission_dialogue = None   # tracks current mission convo state
        self.pause_menu.set_mission_manager(self.mission_manager)

        # Create the default starting room (a fresh transient "green dev" room).
        self._create_default_room()

        # Scan all rooms for missions defined on placed NPCs.
        self.mission_manager.scan_rooms_for_missions(self.room_manager)

        # ── Flying controller callbacks ───────────────────────────────────────
        self.flying_controller.on_room_transition = self._handle_flying_room_transition
        self.flying_controller.on_flight_complete = self._handle_flying_complete

        # ── Transition / fade system ──────────────────────────────────────────
        self.transition_controller = TransitionController(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.flying_controller.set_transition_controller(self.transition_controller)

        self.sound_manager.set_context('exploration')

    # ── Initialisation helpers ────────────────────────────────────────────────

    def _create_default_room(self):
        """Pick the starting room on boot.

        Always starts the player in a fresh transient room (the green dev room).
        Saved rooms are loaded into the room manager for the editor but are never
        auto-selected as the player's starting location — that would render their
        tiles into the dev room on startup.
        """
        room = self.room_manager.create_transient_room(
            "Default Room", WORLD_WIDTH, WORLD_HEIGHT, "Default"
        )
        self.room_manager.current_room = room
        self.current_room = room

    # ── Event handling ────────────────────────────────────────────────────────

    def _get_logical_mouse_pos(self):
        """Translate the real window mouse position to logical resolution coords."""
        mx, my = pygame.mouse.get_pos()
        wx, wy = self.screen.get_size()
        return (int(mx * SCREEN_WIDTH / wx), int(my * SCREEN_HEIGHT / wy))

    def _rescale_event(self, event):
        """Scale a mouse event's position from real window coords to logical resolution.

        Everything runs in logical space (SCREEN_WIDTH × SCREEN_HEIGHT), so raw
        window mouse positions need to be mapped before they reach any subsystem.
        """
        if event.type not in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
            return event

        wx, wy  = self.screen.get_size()
        ox, oy  = event.pos
        new_pos = (int(ox * SCREEN_WIDTH / wx), int(oy * SCREEN_HEIGHT / wy))

        if event.type == pygame.MOUSEMOTION:
            return pygame.event.Event(event.type,
                                      pos=new_pos,
                                      rel=event.rel,
                                      buttons=event.buttons)

        d = {'pos': new_pos, 'button': event.button}
        if hasattr(event, 'buttons'):
            d['buttons'] = event.buttons
        return pygame.event.Event(event.type, **d)

    def handle_events(self):
        """
        Process all pending pygame events for the current frame.

        Priority order for overlays (highest to lowest):
          1. Character-switch menu
          2. Save-point menu
          3. NPC config menu
          4. Room-transition config menu
          5. Sprite editor
          6. Room editor
          7. Dev menu
          8. Normal gameplay input
        """
        while True:
            try:
                event = pygame.event.poll()
            except (KeyError, SystemError):
                continue   # skip only the broken event, keep going
            if event.type == pygame.NOEVENT:
                break      # queue is empty, done for this frame

            event = self._rescale_event(event)

            if event.type == pygame.QUIT:
                self.running = False

            # ── Overlay priority pass ─────────────────────────────────────────
            # Each active overlay grabs the event and issues continue so nothing
            # below it processes the same input.

            if self.character_switch_menu.active:
                result = self.character_switch_menu.handle_input(event)
                if result and result != 'close':
                    self._switch_character(result)
                continue

            if self.pause_menu.active:
                self.pause_menu.handle_input(event)
                # 'open_skills' stub — wire up when the skills menu is ready
                continue

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

            if self.npc_config_menu.active:
                result = self.npc_config_menu.handle_input(event)
                if result and result != 'cancel' and self.pending_npc_position:
                    x, y         = self.pending_npc_position
                    npc          = NPC(x, y, result)
                    npc.npc_type = result['npc_type']
                    self.npcs.append(npc)
                    self.pending_npc_position = None
                elif result == 'cancel':
                    self.pending_npc_position = None
                continue

            if self.transition_config_menu.active:
                result = self.transition_config_menu.handle_input(event)
                if result and result != 'cancel' and self.pending_transition_position:
                    x, y                       = self.pending_transition_position
                    transition                 = RoomTransition(x, y, result['width'], result['height'])
                    transition.target_room     = result['target_room']
                    transition.exit_direction  = result['exit_direction']
                    transition.entry_direction = result['entry_direction']
                    transition.spawn_x         = result['spawn_x']
                    transition.spawn_y         = result['spawn_y']
                    self.room_transitions.append(transition)
                    self.pending_transition_position = None
                elif result == 'cancel':
                    self.pending_transition_position = None
                continue

            if self.sprite_editor.active:
                self.sprite_editor.handle_input(event)
                continue

            if self.cutscene_editor.active:
                self.cutscene_editor.handle_input(event)
                continue

            if self.room_editor.active:
                result = self.room_editor.handle_input(event)
                if result and result.startswith('test_room:'):
                    self._handle_test_room(result)
                # Rescan missions whenever the editor closes (NPCs may have changed).
                if not self.room_editor.active:
                    self.mission_manager.scan_rooms_for_missions(self.room_manager)
                continue

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
                elif result == 'open_cutscene_editor':
                    self.dev_menu.active = False
                    self.cutscene_editor.toggle()
                continue

            # ── Normal gameplay input ─────────────────────────────────────────
            # Only reached when no overlay is active.
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
                    # Release the beam charge when Q is lifted without having fired.
                    if event.key == pygame.K_q:
                        self.player.is_q_pressed = False
                        if self.player.is_charging_beam and not self.player.is_firing_beam:
                            self.player.stop_beam()

    def _handle_game_keydown(self, event):
        """Key-down events during normal gameplay."""
        if event.key == pygame.K_F1:
            self.dev_menu.toggle()

        elif event.key == pygame.K_F2:
            # F2 exits test mode and drops back into the room editor.
            if self.is_test_mode:
                self._exit_test_mode()
                self.room_editor.active       = True
                self.room_editor.current_view = 'view_room'

        elif event.key == pygame.K_ESCAPE:
            self.pause_menu.open(self.player)

        elif event.key == pygame.K_q:
            # Q fires a ki blast or begins charging a beam, depending on the current mode.
            if self.player.ki_attack_mode == 'blast':
                self.player.shoot_blast()
            elif self.player.ki_attack_mode == 'beam':
                self.player.start_charging_beam()
                self.player.is_q_pressed = True

        elif event.key == pygame.K_e:
            self._handle_interact()

        elif event.key == pygame.K_TAB:
            # Cycle through ki attack modes: blast → beam → transform → blast …
            modes = ('blast', 'beam', 'transform')
            idx = modes.index(self.player.ki_attack_mode) if self.player.ki_attack_mode in modes else 0
            self.player.ki_attack_mode = modes[(idx + 1) % len(modes)]

        elif event.key == pygame.K_x:
            # X triggers a transformation when the system is charged and ready.
            if (self.player.ki_attack_mode == 'transform'
                    and self.player.transformation
                    and self.player.transformation.is_ready):
                self.player.transformation.start_transform()

        elif event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
            # Double-tapping a direction key starts a run.
            if self.player.check_double_tap(event.key):
                self.player.is_running = True

    def _handle_interact(self):
        """
        Handle the E (interact) key press.

        Priority:
          1. Nearby save point.
          2. Nearby NPC — start / advance dialogue, with mission branching.
          3. Flying pad.
          4. Default melee attack.
        """
        # Don't allow interact while a flying sequence is in progress.
        if self.flying_controller.is_active():
            return

        # Save point takes top priority.
        if self.nearby_save_point and not self.dialogue_box.active and not self.save_point_menu.active:
            if self.nearby_save_point.variant == 'big':
                self.save_point_menu.open()
            return

        # Begin talking to a nearby NPC.
        if self.nearby_npc and not self.dialogue_box.active:
            self._start_npc_dialogue(self.nearby_npc)
            return

        # Advance an already-open dialogue box.
        if self.dialogue_box.active and self.dialogue_box._state != 'closing':
            self._advance_npc_dialogue()
            return

        # Check if the player is standing on a flying pad with waypoints.
        nearby_pad = next(
            (pad for pad in self.flying_pads
             if pad.active and pad.check_collision_with_player(self.player)),
            None
        )
        if nearby_pad and len(nearby_pad.waypoints) > 0:
            self.flying_controller.start_flight(self.player, nearby_pad)
        else:
            # Default: throw a melee punch.
            melee = self.player.melee_attack()
            if melee:
                self.melee_attacks.append(melee)
                self.sound_manager.play_sfx('punch')

    def _start_npc_dialogue(self, npc):
        """Begin a conversation with an NPC, routing through the mission system."""
        # Hide the NPC's own indicator the moment dialogue starts.
        npc.is_talking = True
        iid   = getattr(npc, 'instance_id', '')
        state = self.mission_manager.get_npc_dialogue_state(iid) if iid else None

        if state == 'offer':
            mission = self.mission_manager.get_mission_for_npc(iid)
            lines   = mission.get('dialogues', {}).get('offer', []) if mission else []
            if isinstance(lines, str):
                lines = [lines]
            lines = lines or npc.dialogue_config.get('dialogues', ["Hello, traveler!"])
            self._active_mission_dialogue = {
                'npc':     npc,
                'lines':   lines,
                'index':   0,
                'phase':   'offer',
                'mission': mission,
            }
            self._show_mission_line()

        elif state == 'active':
            # Fire the talk_to_npc hook so mission tracking stays up to date.
            self.mission_manager.on_npc_talked(iid)
            mission = self.mission_manager.get_mission_for_npc(iid)
            lines   = mission.get('dialogues', {}).get('active', []) if mission else []
            if isinstance(lines, str):
                lines = [lines]
            lines = lines or ["Come back when you're done."]
            self._active_mission_dialogue = {
                'npc':     npc,
                'lines':   lines,
                'index':   0,
                'phase':   'active',
                'mission': mission,
            }
            self._show_mission_line()

        elif state == 'completed':
            # All objectives done — fire the hook then offer the reward dialogue.
            self.mission_manager.on_npc_talked(iid)
            mission = self.mission_manager.get_mission_for_npc(iid)
            # Check bring_item objective before finalising the mission.
            if mission:
                self.mission_manager.check_bring_item(mission['id'], getattr(self.player, 'inventory', []))
            lines = mission.get('dialogues', {}).get('completed', []) if mission else []
            if isinstance(lines, str):
                lines = [lines]
            lines = lines or ["Well done! Here is your reward."]
            self._active_mission_dialogue = {
                'npc':     npc,
                'lines':   lines,
                'index':   0,
                'phase':   'claim_reward',
                'mission': mission,
            }
            self._show_mission_line()

        elif state == 'rewarded':
            mission = self.mission_manager.get_mission_for_npc(iid)
            lines   = mission.get('dialogues', {}).get('rewarded', []) if mission else []
            if isinstance(lines, str):
                lines = [lines]
            lines = lines or ["Thanks again."]
            self._active_mission_dialogue = {
                'npc':     npc,
                'lines':   lines,
                'index':   0,
                'phase':   'rewarded',
                'mission': mission,
            }
            self._show_mission_line()

        else:
            # Plain NPC — use the standard dialogue system with no mission routing.
            self._active_mission_dialogue = None
            text, is_final, item = npc.start_dialogue()
            if text:
                portrait_key = self._npc_portrait_key(npc)
                self.dialogue_box.show(text, "NPC", is_final, item, portrait_key=portrait_key)
                if item:
                    self.player.inventory.append(item)

    def _advance_npc_dialogue(self):
        """Advance or close the active NPC dialogue box on player input."""
        # If text is still typing out, snap it to fully visible on the first press.
        if self.dialogue_box._chars_shown < len(self.dialogue_box.current_text):
            self.dialogue_box._chars_shown = len(self.dialogue_box.current_text)
            return

        # ── Plain NPC flow (no mission) ───────────────────────────────────────
        if self._active_mission_dialogue is None:
            if not self.nearby_npc:
                self.dialogue_box.hide()
                return
            if self.dialogue_box.is_final:
                self.dialogue_box.hide()
                self.nearby_npc.end_dialogue()
            else:
                text, is_final, item = self.nearby_npc.start_dialogue()
                if text:
                    portrait_key = self._npc_portrait_key(self.nearby_npc)
                    self.dialogue_box.show(text, "NPC", is_final, item, portrait_key=portrait_key)
                    if item:
                        self.player.inventory.append(item)
            return

        # ── Mission dialogue flow ─────────────────────────────────────────────
        md = self._active_mission_dialogue
        md['index'] += 1

        if md['index'] < len(md['lines']):
            # More lines remain in this phase — just advance.
            self._show_mission_line()
        else:
            # All lines shown — resolve the current dialogue phase.
            npc = md['npc']
            npc.is_talking             = False
            npc.current_dialogue_index = 0

            phase   = md.get('phase', '')
            mission = md.get('mission')

            if phase == 'offer' and mission:
                self.mission_manager.accept_mission(mission['id'])
                # Show 'accepted' confirmation lines if the designer configured any.
                accepted_lines = mission.get('dialogues', {}).get('accepted', '')
                if isinstance(accepted_lines, str):
                    accepted_lines = [accepted_lines] if accepted_lines.strip() else []
                if accepted_lines:
                    # Don't hide first — transition directly so the box stays open
                    # and show() can restart cleanly from the 'open' state.
                    self._active_mission_dialogue = {
                        'npc':     npc,
                        'lines':   accepted_lines,
                        'index':   0,
                        'phase':   'accepted',
                        'mission': mission,
                    }
                    self._show_mission_line()
                    return   # don't clear yet — player still has lines to advance
                self.dialogue_box.hide()
                self._active_mission_dialogue = None

            elif phase == 'claim_reward' and mission:
                rewards = self.mission_manager.claim_reward(mission['id'])
                self._apply_mission_rewards(rewards)
                self.dialogue_box.hide()
                self._active_mission_dialogue = None

            else:
                self.dialogue_box.hide()
                self._active_mission_dialogue = None

    def _show_mission_line(self):
        """Show the current line from the active mission dialogue."""
        md       = self._active_mission_dialogue
        line     = md['lines'][md['index']]
        npc      = md['npc']
        is_final = (md['index'] >= len(md['lines']) - 1)
        npc.is_talking = True
        portrait_key   = self._npc_portrait_key(npc)
        self.dialogue_box.show(line, "NPC", is_final, None, portrait_key=portrait_key)

    def _apply_mission_rewards(self, rewards: dict):
        """Credit XP and items from a completed mission to the player."""
        if not rewards:
            return
        xp = rewards.get('xp', 0)
        if xp:
            self.player.gain_exp(xp, self.game_config)
            if self.player.pending_level_up:
                self.level_up_notification.show(self.player.level, self.player.stat_points)
                self.player.pending_level_up = False
        for item_entry in rewards.get('items', []):
            item_id = item_entry.get('item_id', '')
            count   = int(item_entry.get('count', 1))
            for _ in range(count):
                self.player.inventory.append(item_id)

    def _handle_menu_keydown(self, event):
        """Key-down events while the main menu is open."""
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
        """Enter test mode for the room named in result ('test_room:<name>').

        Backs up all room data, copies objects into the active game lists,
        and spawns the player at the room's designated spawn point.
        """
        room_name = result.split(':', 1)[1]
        room      = self.room_manager.get_room_by_name(room_name)
        if not room:
            return

        # Clear all active entities and projectiles before entering test mode.
        self._clear_active_entities()

        self.is_test_mode                = True
        self._create_comprehensive_test_backup()
        self._test_mission_snapshot      = self.mission_manager.snapshot()
        self.mission_manager.block_saves = True   # prevent test progress reaching disk

        self.room_manager.current_room = room
        self.current_room              = room

        # Sync tiles from the editor into the room object before testing starts.
        if self.room_editor.tileset_editor and room_name in self.room_editor.tileset_editor.room_tiles:
            room.tiles = self.room_editor.tileset_editor.room_tiles[room_name][:]
        elif not hasattr(room, 'tiles'):
            room.tiles = []

        self._load_room_objects_as_copies(room)

        # Determine where to place the player — prefer the room's spawn point.
        if hasattr(room, 'spawn_points') and room.spawn_points:
            spawn_pos = (room.spawn_points[0].x, room.spawn_points[0].y)
        elif room.spawn_point:
            spawn_pos = room.spawn_point
        else:
            spawn_pos = (room.width // 2, room.height // 2)

        self.player.x = spawn_pos[0]
        self.player.y = spawn_pos[1]

        # Centre the camera on the spawn position.
        self.camera.x = max(0, self.player.x - self.camera.screen_width  // 2)
        self.camera.y = max(0, self.player.y - self.camera.screen_height // 2)

        self.room_editor.active = False

    def _create_comprehensive_test_backup(self):
        """Snapshot every room's objects so they can be restored when test mode exits.

        Covers: collision objects, flying pads, destructible stones, level gates.
        """
        self.test_room_backup = {}

        for room in self.room_manager.rooms:
            room_backup = {
                'room_name':           room.name,
                'collision_objects':   [],
                'destructible_stones': [],
                'level_gates':         [],
                'flying_pads':         [],
            }

            if hasattr(room, 'collision_objects'):
                for obj in room.collision_objects:
                    room_backup['collision_objects'].append({
                        'x': obj.x, 'y': obj.y,
                        'width': obj.width, 'height': obj.height,
                        'collision_type': getattr(obj, 'collision_type', 'wall'),
                    })

            if hasattr(room, 'flying_pads'):
                for pad in room.flying_pads:
                    room_backup['flying_pads'].append({
                        'x':             pad.x,
                        'y':             pad.y,
                        'pad_type':      pad.pad_type,
                        'waypoints':     [wp.to_dict() for wp in pad.waypoints],
                        'is_return_pad': pad.is_return_pad,
                        'linked_pad_id': pad.linked_pad_id,
                        'width':         pad.width,
                        'height':        pad.height,
                    })

            if hasattr(room, 'destructible_stones'):
                for stone in room.destructible_stones:
                    room_backup['destructible_stones'].append({
                        'x':          stone.x,
                        'y':          stone.y,
                        'stone_type': stone.stone_type,
                        'max_health': stone.max_health,
                        'health':     stone.health,
                        'width':      stone.width,
                        'height':     stone.height,
                    })

            if hasattr(room, 'level_gates'):
                for gate in room.level_gates:
                    room_backup['level_gates'].append({
                        'x':              gate.x,
                        'y':              gate.y,
                        'gate_type':      gate.gate_type,
                        'required_level': gate.required_level,
                        'max_health':     gate.max_health,
                        'health':         gate.health,
                        'width':          gate.width,
                        'height':         gate.height,
                    })

            self.test_room_backup[room.name] = room_backup

    def _load_room_objects_as_copies(self, room):
        """Spawn independent copies of all room objects into the active game lists.

        Used during test mode so the originals on the Room aren't mutated.
        Also adds invisible boundary walls around the room perimeter to catch
        any knockback that would otherwise send entities off-screen.
        """
        if not room:
            return

        # Discard any baked tile surface for this room so it is rebuilt fresh.
        self.invalidate_tile_cache(room.name)

        # Flying pads — deep-copy so test-mode flights don't touch the editor data.
        self.flying_pads = []
        if hasattr(room, 'flying_pads') and room.flying_pads:
            from objects.flying_pad import FlyingPad, FlyingPadWaypoint
            for pad in room.flying_pads:
                copy_pad               = FlyingPad(pad.x, pad.y, pad.pad_type)
                copy_pad.waypoints     = [FlyingPadWaypoint.from_dict(wp.to_dict()) for wp in pad.waypoints]
                copy_pad.is_return_pad = pad.is_return_pad
                copy_pad.linked_pad_id = pad.linked_pad_id
                copy_pad.source_room   = pad.source_room
                copy_pad.current_room  = room.name
                copy_pad.width         = pad.width
                copy_pad.height        = pad.height
                copy_pad.active        = True
                self.flying_pads.append(copy_pad)

        # Collision objects
        self.collision_objects = []
        if hasattr(room, 'collision_objects') and room.collision_objects:
            from objects.collision_object import CollisionObject
            for obj in room.collision_objects:
                copy_obj                = CollisionObject(obj.x, obj.y, obj.width, obj.height)
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
                copy_t                 = RoomTransition(transition.x, transition.y,
                                                        transition.width, transition.height)
                copy_t.target_room     = transition.target_room
                copy_t.exit_direction  = transition.exit_direction
                copy_t.entry_direction = transition.entry_direction
                copy_t.spawn_x         = transition.spawn_x
                copy_t.spawn_y         = transition.spawn_y
                copy_t.active          = True
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
        """Restore all rooms to their pre-test state and clear test entities."""
        if not self.is_test_mode or not self.test_room_backup:
            return

        for room_name, backup in self.test_room_backup.items():
            room = self.room_manager.get_room_by_name(room_name)
            if not room:
                continue

            # Rebuild collision objects from the snapshot.
            from objects.collision_object import CollisionObject
            room.collision_objects = []
            for d in backup['collision_objects']:
                obj                = CollisionObject(d['x'], d['y'], d['width'], d['height'])
                obj.collision_type = d['collision_type']
                room.collision_objects.append(obj)

            # Rebuild flying pads from the snapshot.
            from objects.flying_pad import FlyingPad, FlyingPadWaypoint
            room.flying_pads = []
            for d in backup.get('flying_pads', []):
                pad               = FlyingPad(d['x'], d['y'], d['pad_type'])
                pad.width         = d['width']
                pad.height        = d['height']
                pad.waypoints     = [FlyingPadWaypoint.from_dict(wp) for wp in d['waypoints']]
                pad.is_return_pad = d['is_return_pad']
                pad.linked_pad_id = d['linked_pad_id']
                room.flying_pads.append(pad)

            if self.room_editor.object_editor:
                self.room_editor.object_editor.flying_pad_manager.flying_pads[room.name] = room.flying_pads

            # Rebuild destructible stones from the snapshot.
            from objects.destructible_stone import DestructibleStone
            room.destructible_stones = []
            for d in backup['destructible_stones']:
                stone            = DestructibleStone(d['x'], d['y'], d['stone_type'])
                stone.max_health = d['max_health']
                stone.health     = d['health']
                stone.width      = d['width']
                stone.height     = d['height']
                room.destructible_stones.append(stone)

            # Rebuild level gates from the snapshot.
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

        # Restore mission progress to pre-test state and overwrite the save file
        # so test-mode progress never survives a game restart.
        self.mission_manager.block_saves = False
        if self._test_mission_snapshot is not None:
            self.mission_manager.restore(self._test_mission_snapshot)
            self.mission_manager.save()
            self._test_mission_snapshot = None

        self._clear_active_entities()
        self.destructible_stones = []
        self.collision_objects   = []
        self.level_gates         = []

    def _load_room_objects(self, room):
        """Load all room objects directly (no copies) into the active game lists.

        Used during normal gameplay room transitions. Adds boundary walls and
        rebuilds the shared obstacle list for the player and enemies.
        """
        if not room:
            return

        # Discard any baked tile surface for this room so it is rebuilt fresh.
        self.invalidate_tile_cache(room.name)

        # Flying pads — shallow copy; current_room is set on each pad.
        self.flying_pads = []
        if hasattr(room, 'flying_pads') and room.flying_pads:
            self.flying_pads = room.flying_pads[:]
            for pad in self.flying_pads:
                pad.current_room = room.name

        # Collision objects — shallow copy.
        self.collision_objects = []
        if hasattr(room, 'collision_objects') and room.collision_objects:
            self.collision_objects = room.collision_objects[:]

        # Invisible boundary walls to contain knockback inside the room.
        self._add_room_boundary_walls(room)

        # One-liner copies for the rest of the room object types.
        self.destructible_stones = room.destructible_stones[:] if hasattr(room, 'destructible_stones') and room.destructible_stones else []
        self.level_gates         = room.level_gates[:]         if hasattr(room, 'level_gates')         and room.level_gates         else []
        self.room_transitions    = room.room_transitions[:]    if hasattr(room, 'room_transitions')    and room.room_transitions    else []
        self.save_points         = room.save_points[:]         if hasattr(room, 'save_points')         and room.save_points         else []

        # Spawn entities.
        self.enemies = []
        self.npcs    = []
        self._spawn_room_entities(room)

        # Clear in-flight projectiles left over from the previous room.
        self._clear_projectiles()

        # Rebuild obstacle lists so knockback resolves against the new room's geometry.
        self._assign_obstacles()

    # ── Private room-loading utilities ────────────────────────────────────────

    def _add_room_boundary_walls(self, room):
        """Add four invisible collision walls along the room edges.

        These keep knockback from sending entities off the map.
        """
        from objects.collision_object import CollisionObject

        thickness = 10  # Wall thickness in world units.

        # Build one wall per edge: top, bottom, left, right.
        walls = [
            CollisionObject(room.width // 2,             -thickness // 2,               room.width, thickness),   # Top
            CollisionObject(room.width // 2,              room.height + thickness // 2,  room.width, thickness),   # Bottom
            CollisionObject(-thickness // 2,              room.height // 2,              thickness,  room.height),  # Left
            CollisionObject(room.width + thickness // 2,  room.height // 2,              thickness,  room.height),  # Right
        ]
        for wall in walls:
            wall.collision_type   = 'wall'
            wall.is_room_boundary = True
            self.collision_objects.append(wall)

    def _push_out_of_obstacles(self, entity, obstacles, max_iterations=20):
        """Nudge entity outward until it no longer overlaps any solid obstacle.

        Runs up to max_iterations passes so it resolves even in tight corners.
        Does nothing when there is no overlap to begin with.
        """
        import pygame

        def get_entity_rect(e):
            return pygame.Rect(e.x - e.width // 2, e.y - e.height // 2, e.width, e.height)

        def get_obstacle_rect(obs):
            """Return a pygame.Rect for the obstacle, or None if it's non-solid."""
            if not getattr(obs, 'active', True):
                return None
            if hasattr(obs, 'id') and obs.id == 'collision_wall':
                return pygame.Rect(obs.x, obs.y, obs.width, obs.height)
            if hasattr(obs, 'solid') and not obs.solid:
                return None
            if hasattr(obs, 'get_rect'):
                return obs.get_rect()
            if hasattr(obs, 'x') and hasattr(obs, 'width'):
                return pygame.Rect(
                    obs.x - obs.width  // 2,
                    obs.y - obs.height // 2,
                    obs.width, obs.height,
                )
            return None

        for _ in range(max_iterations):
            ent_rect = get_entity_rect(entity)
            pushed   = False
            for obs in obstacles:
                obs_rect = get_obstacle_rect(obs)
                if obs_rect is None or not ent_rect.colliderect(obs_rect):
                    continue

                # Push along the axis with the smallest overlap.
                overlap_left  = ent_rect.right  - obs_rect.left
                overlap_right = obs_rect.right  - ent_rect.left
                overlap_up    = ent_rect.bottom - obs_rect.top
                overlap_down  = obs_rect.bottom - ent_rect.top

                min_overlap = min(overlap_left, overlap_right, overlap_up, overlap_down)
                if min_overlap == overlap_left:
                    entity.x -= overlap_left  + 1
                elif min_overlap == overlap_right:
                    entity.x += overlap_right + 1
                elif min_overlap == overlap_up:
                    entity.y -= overlap_up    + 1
                else:
                    entity.y += overlap_down  + 1

                pushed = True
                break   # Re-check all obstacles after each nudge.

            if not pushed:
                break   # Entity is fully clear of all obstacles.

    def _spawn_room_entities(self, room):
        """Instantiate enemies and NPCs from the room's entity data.

        Reads variant_type to determine AI category and shooter style
        so the correct projectile system fires for each enemy type.
        """
        if not hasattr(room, 'entities') or not room.entities:
            return

        from entities.enemy import Enemy
        from entities.boss_enemy import BossEnemy
        from entities.npc import NPC

        # Build the obstacle list used to resolve spawn-time collisions (Fail-safe 2).
        spawn_obstacles = (
            self.collision_objects
            + self.destructible_stones
            + self.level_gates
            + self.room_transitions
        )

        for data in room.entities:
            entity_type  = data.get('entity_type', 'enemy')
            x            = data.get('x', 0)
            y            = data.get('y', 0)
            enemy_id     = data.get('id', 'tiger_bandit')
            variant_type = data.get('variant_type', 'default')

            if entity_type == 'npc':
                npc                  = NPC(x, y, data.get('dialogue_config', None))
                npc.active           = True
                npc.npc_type         = data.get('npc_mode',   'static')
                npc.facing_direction = data.get('npc_facing', 'down')
                npc.variant          = data.get('variant_type', 'default')
                npc.npc_id           = data.get('id', 'generic')
                npc.instance_id      = data.get('instance_id', '')

                # Register the mission if this NPC has one defined inline.
                if data.get('mission') and npc.instance_id:
                    self.mission_manager.register_mission(data['mission'])

                # Load the NPC sprite via the sprite system.
                import os
                npc_id       = data.get('id', 'generic')
                variant_type = data.get('variant_type', 'default')
                try:
                    from core.sprite_system import create_npc_sprite
                    npc.sprite     = create_npc_sprite(npc_id, variant_type, npc.width, npc.height)
                    npc.has_sprite = npc.sprite is not None
                except Exception:
                    npc.has_sprite = False
                self.npcs.append(npc)

            elif entity_type == 'boss':
                boss        = BossEnemy(x, y, boss_id=enemy_id, variant=variant_type)
                boss.active = True
                self._push_out_of_obstacles(boss, spawn_obstacles)
                self.enemies.append(boss)

            elif entity_type == 'enemy':
                ai_type        = data.get('ai_type', 'easy')
                enemy_category = data.get('enemy_category', 'melee')

                # Map variant to the correct projectile type for shooter enemies.
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
                self._push_out_of_obstacles(enemy, spawn_obstacles)
                self.enemies.append(enemy)

    def _assign_obstacles(self):
        """Build a shared obstacle list and hand it to the player and every enemy.

        Used to prevent knockback from clipping entities through walls, stones,
        gates, and transitions.
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
        self.enemies = []
        self.npcs    = []
        self._clear_projectiles()

    def _clear_projectiles(self):
        """Remove all in-flight projectiles and visual effects."""
        self.projectiles   = []
        self.melee_attacks = []
        self.bombs         = []
        self.enemy_bullets = []
        self.enemy_rockets = []
        self.enemy_kiblasts = []
        self.explosions    = []
        self.dmg_numbers.clear()  # Wipe leftover popups on room transition

    # ── Editor callbacks ──────────────────────────────────────────────────────
    # These keep the live game lists in sync when the designer places or removes
    # objects in the room editor without leaving play mode.

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

    def _on_cutscene_trigger_placed(self, trigger, room_name):
        """Persist the room whenever a cutscene trigger is placed in the editor."""
        room = self.room_manager.get_room_by_name(room_name)
        if room:
            self.room_manager.save_room(room)

    def _on_cutscene_trigger_deleted(self, trigger, room_name):
        """Persist the room whenever a cutscene trigger is deleted in the editor."""
        room = self.room_manager.get_room_by_name(room_name)
        if room:
            self.room_manager.save_room(room)

    def _sync_player_from_cutscene(self, runtime):
        """After a cutscene ends, move the player to the final position and
        direction of the actor that shares the same character as the current player.

        Matching priority:
          1. type == 'player'  AND  character == self.player.character  (exact match)
          2. type == 'player'  with no 'character' field set            (untagged fallback)
        The first exact match wins; if none is found, the first untagged player
        actor is used so cutscenes that don't specify a character still work.
        """
        player_character = getattr(self.player, 'character', None)
        exact_match    = None   # actor_def with matching character field
        fallback_match = None   # first player actor with no character field

        for actor_def in runtime.data.get('actors', []):
            if actor_def.get('type') != 'player':
                continue
            actor_character = actor_def.get('character')
            if actor_character is not None:
                if actor_character == player_character and exact_match is None:
                    exact_match = actor_def
            else:
                if fallback_match is None:
                    fallback_match = actor_def

        chosen = exact_match or fallback_match
        if chosen is None:
            return   # no player actor in this cutscene — nothing to sync

        actor_id   = chosen.get('id')
        live_actor = runtime.actors.get(actor_id)
        if live_actor and hasattr(live_actor, 'entity'):
            entity         = live_actor.entity
            self.player.x  = entity.x
            self.player.y  = entity.y

            # Mirror the actor's final facing direction onto the real player so
            # they don't snap back to their pre-cutscene direction on resume.
            final_direction = getattr(entity, 'direction', None)
            if final_direction:
                self.player.direction = final_direction
                # Also push the direction into the sprite so the idle frame shown
                # immediately after the cutscene matches the new direction.
                if hasattr(self.player, 'sprite') and self.player.sprite:
                    current_anim = getattr(self.player, 'current_animation_state', 'idle')
                    if hasattr(self.player.sprite, 'set_animation'):
                        self.player.sprite.set_animation(current_anim, final_direction)

    def _update_cutscene_triggers(self, dt):
        """Tick the active cutscene runtime, or check for new trigger fires.

        Fade state machine
        ──────────────────
        'fade_out' → alpha rises to 255 (screen goes black)
                   → on reaching 255, instantiate the runtime and switch to 'start'
        'start'    → one frame at full black so the first cutscene frame is hidden
                   → immediately enter 'fade_in'
        'fade_in'  → alpha falls to 0 (screen becomes clear)
                   → on reaching 0, clear _csf_state; cutscene runs normally
        None       → normal cutscene playback, or no cutscene active

        When the cutscene finishes we kick off the fade-in leg.
        """
        # ── Start frame: launch runtime immediately (no fade) ─────────────────
        if self._csf_state == 'start':
            data              = self._csf_pending
            self._csf_pending = None
            self._csf_state   = None
            self._csf_alpha   = 0.0
            if data is not None:
                try:
                    from core.cutscene_runtime import CutsceneRuntime
                    self.active_cutscene_runtime = CutsceneRuntime(
                        data,
                        self.camera,
                        self.cutscene_editor._entity_factory,
                        dialogue_box=self.dialogue_box,
                    )
                    self.active_cutscene_runtime.seek(0.0)

                    # After seek() resets actor positions to their scripted spawn,
                    # snap the player actor to the real player's world position so
                    # any move_to tweens start from where the player actually is.
                    _pc = getattr(self.player, 'character', None)
                    for _adef in data.get('actors', []):
                        if _adef.get('type') != 'player':
                            continue
                        _ac = _adef.get('character')
                        if _ac is not None and _ac != _pc:
                            continue
                        _live = self.active_cutscene_runtime.actors.get(_adef['id'])
                        if _live and hasattr(_live, 'entity'):
                            _live.entity.x = self.player.x
                            _live.entity.y = self.player.y
                            # Also fix the tween's baked start position so it
                            # smoothly moves FROM the real player position.
                            if _live._tween is not None:
                                _live._tween.start_x = self.player.x
                                _live._tween.start_y = self.player.y
                            # Bug fix: _entity_factory always creates the actor with
                            # costume='base'. If the real player is transformed, swap
                            # the actor's sprite to the SSJ sheet so the cutscene
                            # doesn't look like an untransform the moment it starts.
                            _ts = self.player.transformation
                            if _ts and _ts.is_transformed:
                                from core.sprite_system import create_character_sprite
                                _char = getattr(self.player, 'character', 'goku')
                                _live.entity.sprite = create_character_sprite(
                                    _char, 'ssj', 32, 32)
                                _live.entity.sprite.set_animation(
                                    getattr(self.player, 'current_animation_state', 'idle'),
                                    _live.entity.direction,
                                )
                        break

                    # Snapshot the player's full transformation state so that
                    # player.update() running during the cutscene (e.g. completing
                    # an in-progress untransform animation) can't corrupt it.
                    # Restored in _update_cutscene_triggers when the cutscene ends.
                    self._pre_cutscene_transform = None
                    if self.player.transformation:
                        _ts = self.player.transformation
                        self._pre_cutscene_transform = {
                            'is_transformed':    _ts.is_transformed,
                            'is_transforming':   _ts.is_transforming,
                            'is_untransforming': _ts.is_untransforming,
                            'transformed_ki':    _ts.transformed_ki,
                            'sprite':            self.player.sprite,
                            'anim_state':        self.player.current_animation_state,
                        }

                    # Rebase camera_target so the camera travels from the player's
                    # current world position directly to each tween's destination.
                    #
                    # seek(0.0) may have fired snap_to or pan_to(start_x=…) which
                    # placed _base_x/y at the scripted start position.  Without this
                    # fixup the physical camera would first lerp to that scripted
                    # start then follow the tween — a double movement the player sees.
                    #
                    # Each tween stores a delta (not an absolute position), so we
                    # compute the absolute destination, reset the base to the player's
                    # world position, then rewrite the delta to preserve the destination.
                    _ct     = self.active_cutscene_runtime.camera_target
                    _px, _py = self.player.x, self.player.y
                    if _ct._tweens:
                        for _tw in _ct._tweens:
                            _abs_x         = _ct._base_x + _tw['delta_x']
                            _abs_y         = _ct._base_y + _tw['delta_y']
                            _tw['delta_x'] = _abs_x - _px
                            _tw['delta_y'] = _abs_y - _py
                        _ct._base_x = _px
                        _ct._base_y = _py
                        _ct._recompute_xy()
                        self.camera._lerp_active = True
                    elif (_ct.x != _px or _ct.y != _py):
                        # snap_to with no subsequent pan — lerp the physical camera
                        # to the snapped position (camera_target is already there).
                        self.camera._lerp_active = True

                    self.sprite_hud._hud_slide_out = True
                    self.sprite_hud._hud_slide_in  = False
                except Exception as e:
                    print(f'[Game] failed to start cutscene: {e}')
                    import traceback; traceback.print_exc()
                    self.active_cutscene_runtime = None
            return

        # ── Normal cutscene playback ──────────────────────────────────────────
        if self.active_cutscene_runtime:
            # Freeze the cutscene while the pause menu is open; don't tick time
            # or animations so the scene resumes exactly where it was paused.
            if self.pause_menu.active:
                return
            w = self.current_room.width  if self.current_room else 10000
            h = self.current_room.height if self.current_room else 10000
            self.active_cutscene_runtime.update(dt, w, h)
            if self.dialogue_box:
                self.dialogue_box.update(dt)
            if self.active_cutscene_runtime.finished:
                self._sync_player_from_cutscene(self.active_cutscene_runtime)
                # Restore the transformation state that was snapshotted at cutscene
                # start. This undoes any side-effects from player.update() running
                # during the cutscene (e.g. completing an in-progress untransform).
                snap = getattr(self, '_pre_cutscene_transform', None)
                if snap and self.player.transformation:
                    _ts = self.player.transformation
                    _ts.is_transformed    = snap['is_transformed']
                    _ts.is_transforming   = snap['is_transforming']
                    _ts.is_untransforming = snap['is_untransforming']
                    _ts.transformed_ki    = snap['transformed_ki']
                    self.player.sprite    = snap['sprite']
                    self.player.current_animation_state = snap['anim_state']
                    # Re-sync the sprite to the restored animation state so the
                    # first frame after the cutscene looks correct.
                    if hasattr(self.player.sprite, 'set_animation'):
                        self.player.sprite.set_animation(
                            snap['anim_state'], self.player.direction)
                self._pre_cutscene_transform       = None
                self.active_cutscene_runtime       = None
                self.sprite_hud._hud_slide_out     = False
                self.sprite_hud._hud_slide_in      = True
                self.camera._lerp_active           = True
            return

        # ── No cutscene running — check trigger zones ─────────────────────────
        oe = getattr(self.room_editor, 'object_editor', None)
        if not oe or not self.current_room:
            return

        trigger_mgr = getattr(oe, 'cutscene_trigger_manager', None)
        if not trigger_mgr:
            return

        # Tick per-trigger cooldowns so a fired trigger can't re-fire immediately.
        trigger_mgr.update(self.current_room.name, dt)

        # Check whether the player is standing in any trigger zone.
        fired_id = trigger_mgr.check_player(self.current_room.name, self.player)
        if not fired_id:
            return

        # Load the cutscene JSON from disk and queue it to launch immediately.
        import os, json
        cutscene_path = os.path.join('data', 'cutscenes', f'{fired_id}.json')
        if not os.path.exists(cutscene_path):
            print(f'[Game] cutscene file not found: {cutscene_path}')
            return
        try:
            with open(cutscene_path) as f:
                cutscene_data = json.load(f)
        except Exception as e:
            print(f'[Game] failed to load cutscene "{fired_id}": {e}')
            import traceback; traceback.print_exc()
            return

        # Start the cutscene on the next frame (no fade-out, direct launch).
        self._csf_pending = cutscene_data
        self._csf_alpha   = 0.0
        self._csf_state   = 'start'

    def _draw_cutscene_fade(self, surface):
        """Draw the cutscene black-fade overlay if a fade is in progress.

        Called from draw() after transition_controller.draw() so it sits on top
        of the whole scene (including any room-transition wipes) but under dev
        tools.  When _csf_state is None and _csf_alpha is 0 this is a no-op.
        """
        alpha = int(self._csf_alpha)
        if alpha <= 0:
            return
        w, h = surface.get_size()
        # Lazily create / resize the overlay surface as needed.
        if (not hasattr(self, '_csf_surf') or self._csf_surf.get_size() != (w, h)):
            self._csf_surf = pygame.Surface((w, h))
            self._csf_surf.fill((0, 0, 0))
        self._csf_surf.set_alpha(min(255, alpha))
        surface.blit(self._csf_surf, (0, 0))

    # ── Character switching ───────────────────────────────────────────────────

    def _switch_character(self, character_id):
        """Swap the player's sprite to character_id while keeping all gameplay state intact."""
        from core.sprite_system import create_character_sprite

        # Snapshot current state before the swap.
        state = {
            'x':         self.player.x,
            'y':         self.player.y,
            'hp':        self.player.hp,
            'ki':        self.player.ki,
            'level':     self.player.level,
            'stats':     self.player.stats.copy(),
            'inventory': self.player.inventory.copy(),
            'direction': self.player.direction,
        }

        self.player.character = character_id
        self.player.sprite    = create_character_sprite(character_id, 'base', 32, 32)

        # Restore state so the swap is completely seamless to the player.
        for key, value in state.items():
            setattr(self.player, key, value)

    # ── Flying-controller callbacks ───────────────────────────────────────────

    def _handle_flying_room_transition(self, target_room_name, spawn_x, spawn_y):
        """Swap the active room mid-flight.

        Called by FlyingController at the midpoint of a boundary-waypoint transition.
        Loads room objects in the right mode and re-anchors the camera at the spawn.
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

        # Reposition and clamp the camera to the new room's spawn location.
        self.camera.x = max(0, (spawn_x * RENDER_SCALE) - self.camera.screen_width  // 2)
        self.camera.y = max(0, (spawn_y * RENDER_SCALE) - self.camera.screen_height // 2)
        self.camera.x = max(0, min(self.camera.x, target_room.width  * RENDER_SCALE - SCREEN_WIDTH))
        self.camera.y = max(0, min(self.camera.y, target_room.height * RENDER_SCALE - SCREEN_HEIGHT))
        self.mission_manager.on_room_entered(target_room_name)

    def _handle_flying_complete(self):
        """Called when the flying sequence ends.
        Player control is already restored by FlyingController.
        """
        pass

    # ── Main update ───────────────────────────────────────────────────────────

    def update(self):
        """Advance all systems by one frame: input, movement, collision, projectiles,
        enemy AI, item pickups, save points, UI notifications, and dev overlays.
        """
        current_time   = time.time()
        dt             = current_time - self.last_time
        self.last_time = current_time
        self.dt        = dt

        # When the room editor is open, skip all game simulation — only tick the editor.
        if self.room_editor.active:
            self.room_editor.update(dt, self._get_logical_mouse_pos())
            return

        enemies_defeated_this_frame = 0

        # Always tick UI overlays even when gameplay is paused.
        self.character_switch_menu.update(dt)
        self.save_point_menu.update(dt)
        self.pause_menu.update(dt)

        if self.ui.current_screen == 'game':
            # Accumulate play time only while unpaused and no overlay is blocking.
            if not self.save_point_menu.active and not self.character_switch_menu.active \
                    and not self.pause_menu.active:
                self.play_time += dt

            # Player movement is also suppressed during cutscenes.
            if not self.save_point_menu.active and not self.character_switch_menu.active \
                    and not self.pause_menu.active and not self.active_cutscene_runtime:
                self._update_player_movement(dt)

            self.player.update(dt)

            # Fade the damage tint back to neutral each frame.
            if self.player.hurt_tint > 0:
                self.player.hurt_tint = max(0.0, self.player.hurt_tint - dt / self.player.hurt_tint_duration)

            # Only follow the player when no cutscene is driving the camera.
            # During a cutscene the runtime calls camera.update() itself; calling it
            # again here with the player as target would yank the camera away.
            if not self.active_cutscene_runtime:
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
                # Tick the camera here too unless we're mid-room-swap or in a cutscene.
                if not self.flying_controller.is_transitioning_rooms and not self.active_cutscene_runtime:
                    self.camera.update(self.player, self.current_room.width,
                                       self.current_room.height, dt)

            # Cutscene trigger detection and runtime tick.
            self._update_cutscene_triggers(dt)

            # Walk-based room transition detection (skip during fade).
            if not self.transition_controller.is_transitioning():
                self._check_room_transitions()

            self.level_up_notification.update(dt)

            # Beam charge and auto-fire mechanics.
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

            # Enemy AI, combat resolution, and defeat handling.
            enemies_defeated_this_frame = self._update_enemies(dt)

            # Enemy projectile systems.
            self._update_bombs(dt)
            self._update_enemy_bullets(dt)
            self._update_enemy_rockets(dt)
            self._update_enemy_kiblasts(dt)

            # Tick damage number popups.
            self.dmg_numbers.update(dt)

            # Explosion visuals.
            for explosion in self.explosions[:]:
                explosion.update(dt)
                if not explosion.active:
                    self.explosions.remove(explosion)

            # NPC interaction detection.
            self._update_npcs(dt)

            # Dialogue box animation.
            self.dialogue_box.update(dt)

            # Save point proximity detection.
            self._update_save_points(dt)

            # Destructible stones.
            self._update_stones(dt)

            # Level gates.
            self._update_gates(dt)

            # Transformation system (tracks energy and applies power-up state).
            # Frozen during cutscenes so ki doesn't drain while the player is
            # locked into a scripted sequence.
            if self.player.transformation and not self.active_cutscene_runtime:
                self.player.transformation.update(dt, enemies_defeated_this_frame)

            # Adaptive music — switch between exploration and battle tracks.
            self.sound_manager.update_battle_state(dt, len(self.enemies) > 0)

            # Dev-tool overlays pause simulation when active — bail out early.
            if self.cutscene_editor.active:
                self.cutscene_editor.update(dt)
                return
            if self.dev_menu.active:
                self.dev_menu.update(dt)
                return
            if self.sprite_editor.active:
                self.sprite_editor.update(dt)
                return
            if self.room_editor.active:
                self.room_editor.update(dt, self._get_logical_mouse_pos())
                return

        if self.cutscene_editor.active:
            self.cutscene_editor.update(dt)
            return

    # ── Update sub-routines ───────────────────────────────────────────────────

    def _update_player_movement(self, dt):
        """Read directional input, move the player, and resolve collisions.

        Movement is suppressed while a flying sequence is active.
        Collision order: stones → gates → walls → NPCs.
        """
        keys       = pygame.key.get_pressed()
        dx = dy    = 0
        is_running = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT] or self.player.is_running

        # Build a movement direction vector from the arrow keys.
        if keys[pygame.K_LEFT]  and not keys[pygame.K_RIGHT]: dx = -1
        elif keys[pygame.K_RIGHT] and not keys[pygame.K_LEFT]: dx = 1
        if keys[pygame.K_UP]    and not keys[pygame.K_DOWN]:  dy = -1
        elif keys[pygame.K_DOWN] and not keys[pygame.K_UP]:   dy = 1

        if dx == 0 and dy == 0:
            # No directional input — snap back to idle.
            self.player.is_running = False
            if not self.player.is_transitioning:
                if self.player.current_animation_state in ('walk', 'run'):
                    self.player.sprite.set_animation('idle', self.player.direction)
                    self.player.current_animation_state = 'idle'

        if (dx != 0 or dy != 0) and not self.flying_controller.is_active():
            old_x = self.player.x
            old_y = self.player.y
            self.player.move(dx, dy, is_running, self.current_room.width, self.current_room.height)

            # Resolve collisions in priority order: stones → gates → walls → NPCs.
            # On a hit we restore the pre-move position; running causes knockback.
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
                        collision = True
                        break

            if not collision:
                # NPC collision — use a slim hitbox at the NPC's feet to feel natural.
                import pygame as _pg
                player_rect = self.player.get_collision_rect()
                for npc in self.npcs:
                    if not npc.active or npc.is_moving:
                        continue
                    npc_hw   = npc.width  // 4
                    npc_hh   = max(2, npc.height // 8)
                    npc_rect = _pg.Rect(
                        npc.x - npc_hw,
                        npc.y + npc.height // 2 - npc_hh,
                        npc_hw * 2, npc_hh * 2,
                    )
                    if player_rect.colliderect(npc_rect):
                        self.player.x = old_x
                        self.player.y = old_y
                        break

    def _check_room_transitions(self):
        """Detect when the player walks into a room-transition zone and start
        the fade/teleport sequence.
        """
        for transition in self.room_transitions:
            if transition.active and transition.check_collision(self.player):

                def complete_transition(target_room_name, spawn_x, spawn_y):
                    """Swap rooms and load objects when the transition fade completes."""
                    target_room = self.room_manager.get_room_by_name(target_room_name)
                    if target_room:
                        self.room_manager.current_room = target_room
                        self.current_room              = target_room
                        if self.is_test_mode:
                            self._load_room_objects_as_copies(target_room)
                        else:
                            self._load_room_objects(target_room)
                        self.player.is_transitioning = False
                        self.mission_manager.on_room_entered(target_room_name)

                self.player.is_transitioning = True
                self.transition_controller.start_transition(
                    self.player, transition, complete_transition
                )
                break

    def _update_beam(self, dt):
        """Tick beam charging and auto-fire once fully charged."""
        if self.player.is_charging_beam:
            self.player.update_beam_charge(dt)

        if (not self.player.is_firing_beam
                and self.player.beam_charge_time >= self.player.beam_charge_required):
            beam = self.player.fire_beam_auto()
            if beam:
                self.player.current_beam = beam
                self.sound_manager.play_sfx('beam')

        # Expire the beam when the player stops firing.
        if self.player.current_beam:
            self.player.current_beam.update(dt)
            if not self.player.is_firing_beam:
                self.player.current_beam = None

    def _update_enemies(self, dt):
        """Run AI for all active enemies, resolve combat, and remove the dead.

        Returns the number of enemies defeated this frame (used for XP and transformation).
        """
        defeated = 0

        for enemy in self.enemies[:]:
            # Reset before update so we can detect a fresh player hit this frame
            self.player.last_damage_taken = 0
            enemy.update(dt, self.player, self.current_room.width, self.current_room.height)

            # If the enemy's AI called player.take_damage() during update, spawn a popup
            if self.player.last_damage_taken > 0:
                self.dmg_numbers.spawn(
                    self.player.x, self.player.y - self.player.height // 2,
                    self.player.last_damage_taken, variant='player',
                )

            for melee in self.melee_attacks:
                if melee.active and enemy.check_collision_with_attack(melee, 'melee'):
                    self.sound_manager.play_sfx('enemy_hit')
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            for projectile in self.projectiles:
                if projectile.active and enemy.check_collision_with_attack(projectile, 'projectile'):
                    projectile.active = False
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            if self.player.current_beam:
                if enemy.check_collision_with_attack(self.player.current_beam, 'beam'):
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            if not enemy.active:
                defeated   += 1
                xp_reward   = enemy.get_xp_reward(self.game_config)
                self.player.gain_exp(xp_reward, self.game_config)

                if self.player.pending_level_up:
                    self.level_up_notification.show(self.player.level, self.player.stat_points)
                    self.player.pending_level_up = False

                # Notify the mission manager of the kill with enemy type and room.
                enemy_id  = getattr(enemy, 'enemy_type', getattr(enemy, 'boss_id', ''))
                room_name = self.current_room.name if self.current_room else ''
                self.mission_manager.on_enemy_killed(enemy_id, room_name)

                self.enemies.remove(enemy)

        return defeated

    def _update_bombs(self, dt):
        """Poll shooter enemies for new projectile spawns, tick all in-flight bombs,
        and collect explosion effects when a bomb detonates.
        """
        for enemy in self.enemies:
            if not (hasattr(enemy, 'enemy_category') and enemy.enemy_category == 'shooter'):
                continue

            # Bombs
            bomb_data = enemy.get_bomb_spawn_data()
            if bomb_data:
                self.bombs.append(BombProjectile(
                    start_x=bomb_data['start_x'],   start_y=bomb_data['start_y'],
                    target_x=bomb_data['target_x'], target_y=bomb_data['target_y'],
                    damage=bomb_data['damage'],      flight_time=bomb_data['flight_time'],
                    player=self.player,
                ))

            # Bullets
            bullet_data = enemy.get_bullet_spawn_data()
            if bullet_data:
                self.enemy_bullets.append(bullet_projectile(
                    x=bullet_data['x'],   y=bullet_data['y'],
                    dx=bullet_data['dx'], dy=bullet_data['dy'],
                    speed=bullet_data['speed'], damage=bullet_data['damage'],
                    direction=bullet_data['direction'],
                ))

            # Rockets
            rocket_data = enemy.get_rocket_spawn_data()
            if rocket_data:
                self.enemy_rockets.append(rocket_projectile(
                    x=rocket_data['x'],   y=rocket_data['y'],
                    dx=rocket_data['dx'], dy=rocket_data['dy'],
                    speed=rocket_data['speed'], damage=rocket_data['damage'],
                    direction=rocket_data['direction'],
                ))

            # Ki-blasts (e.g. Android 17/18) — use the same Projectile class as the player
            kiblast_data = enemy.get_kiblast_spawn_data()
            if kiblast_data:
                blast = Projectile(kiblast_data['x'], kiblast_data['y'], kiblast_data['direction'])
                blast.damage = kiblast_data['damage']  # Attach damage so the update method can use it
                self.enemy_kiblasts.append(blast)

        for bomb in self.bombs[:]:
            bomb.update(dt, self.player)

            # If the bomb has queued an explosion effect, add it to the live list.
            if bomb.pending_explosion is not None and bomb.pending_explosion not in self.explosions:
                self.explosions.append(bomb.pending_explosion)

            if not bomb.active:
                self.bombs.remove(bomb)

    def _update_enemy_bullets(self, dt):
        """Tick all Gunner bullets, check player collision, and prune spent ones."""
        for bullet in self.enemy_bullets[:]:
            bullet.update(self.current_room.width, self.current_room.height, dt)

            if bullet.check_collision_with_player(self.player):
                # Compute knock direction away from the bullet's origin.
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
                bullet.active         = False
                self.dmg_numbers.spawn(
                    self.player.x, self.player.y - self.player.height // 2,
                    self.player.last_damage_taken, variant='player',
                )

            if not bullet.active:
                self.enemy_bullets.remove(bullet)

    def _update_enemy_rockets(self, dt):
        """Tick all RocketLauncher rockets, check player collision, and prune spent ones."""
        for rocket in self.enemy_rockets[:]:
            rocket.update(self.current_room.width, self.current_room.height, dt)

            if rocket.check_collision_with_player(self.player):
                self.player.hurt_tint = 1.0

            if not rocket.active:
                self.enemy_rockets.remove(rocket)

    def _update_enemy_kiblasts(self, dt):
        """Tick all enemy ki-blasts, check player collision, and prune spent ones."""
        for blast in self.enemy_kiblasts[:]:
            blast.update(self.current_room.width, self.current_room.height, dt)

            if blast.active:
                r = blast.radius
                blast_rect = pygame.Rect(blast.x - r, blast.y - r, r * 2, r * 2)
                player_rect = self.player.get_collision_rect()
                if blast_rect.colliderect(player_rect):
                    damage = getattr(blast, 'damage', 14)
                    dx = self.player.x - blast.x
                    dy = self.player.y - blast.y
                    dist = math.sqrt(dx * dx + dy * dy)
                    if dist > 0:
                        dx /= dist
                        dy /= dist
                    self.player.take_damage(damage, dx, dy)
                    self.player.hurt_tint = 1.0
                    blast.active = False
                    self.dmg_numbers.spawn(
                        self.player.x, self.player.y - self.player.height // 2,
                        self.player.last_damage_taken, variant='player',
                    )

            if not blast.active:
                self.enemy_kiblasts.remove(blast)



    @staticmethod
    def _npc_portrait_key(npc):
        """Map an NPC's variant to a portrait filename key.

        default  → npc_generic
        variant1 → npc_generic2
        variant2 → npc_generic3, and so on.
        """
        npc_id  = getattr(npc, 'npc_id',  'generic')
        variant = getattr(npc, 'variant', 'default')

        # Assign numeric suffix based on variant name.
        # 'default' (or empty) gets no suffix; others get an incremented digit.
        if variant in (None, '', 'default'):
            suffix = ''
        else:
            import re
            m      = re.search(r'(\d+)$', variant)
            suffix = str(int(m.group(1)) + 1) if m else '2'

        return f"npc_{npc_id}{suffix}"

    def _update_npcs(self, dt):
        """Tick all NPCs and record whichever one is currently in interaction range."""
        self.nearby_npc = None
        for npc in self.npcs[:]:
            npc.update(dt, self.player, self.current_room.width, self.current_room.height)
            if npc.can_interact(self.player):
                self.nearby_npc = npc

    def _update_save_points(self, dt):
        """Tick save points and record whichever one the player is standing near."""
        for sp in self.save_points:
            sp.update(dt, self.player)

        self.nearby_save_point = next(
            (sp for sp in self.save_points if sp.is_player_nearby and sp.active),
            None
        )

    def _update_stones(self, dt):
        """Tick destructible stones, check melee hits, and remove anything destroyed."""
        for stone in self.destructible_stones[:]:
            stone.update(dt)
            for melee in self.melee_attacks:
                if melee.active and stone.check_collision_with_attack(melee, 'melee'):
                    self.sound_manager.play_sfx('punch')
            if not stone.active:
                self.destructible_stones.remove(stone)

    def _update_gates(self, dt):
        """Tick level gates, enforce level requirements, check attack hits, and remove destroyed ones."""
        for gate in self.level_gates[:]:
            gate.update(dt)

            # Push the player back if they don't meet the gate's level requirement.
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
        """Render a full frame in order:
          1. Background fill + grid
          2. Room boundary outline
          3. Flying pads (+ path preview in dev mode)
          4. Save points
          5. Background tile layer
          6. Layered game objects (player, enemies, projectiles, etc.)
          7. Foreground tile layer
          8. UI overlays (HUD, dialogue, menus, dev tools)
        """
        # Hand off completely to the room editor when it's the active view.
        if self.room_editor.active and self.room_editor.current_view == 'view_room':
            self.room_editor.draw(self.logical_surface)
            pygame.display.flip()
            return

        # Flush any stale baked tile surfaces once per frame before anything
        # tries to access them — keeps rebuilds to one per frame regardless of
        # how many on_tile_changed calls arrived during event processing.
        self._flush_dirty_tile_rooms()

        # Fill with the default "green dev room" background colour.
        self.logical_surface.fill((34, 139, 34))

        # Compute the world-space tile range that is currently on screen.
        visible_x_start = self.camera.x // RENDER_SCALE
        visible_y_start = self.camera.y // RENDER_SCALE
        visible_x_end   = (self.camera.x + SCREEN_WIDTH)  // RENDER_SCALE
        visible_y_end   = (self.camera.y + SCREEN_HEIGHT) // RENDER_SCALE

        # Draw a subtle tile grid so the designer can see the grid while editing.
        first_grid_x = (visible_x_start // TILE_SIZE) * TILE_SIZE
        first_grid_y = (visible_y_start // TILE_SIZE) * TILE_SIZE

        x = first_grid_x
        while x <= visible_x_end:
            screen_x = (x * RENDER_SCALE) - self.camera.x
            if -50 <= screen_x <= SCREEN_WIDTH + 50:
                pygame.draw.line(self.logical_surface, (44, 149, 44),
                                 (int(screen_x), 0), (int(screen_x), SCREEN_HEIGHT), 1)
            x += TILE_SIZE

        y = first_grid_y
        while y <= visible_y_end:
            screen_y = (y * RENDER_SCALE) - self.camera.y
            if -50 <= screen_y <= SCREEN_HEIGHT + 50:
                pygame.draw.line(self.logical_surface, (44, 149, 44),
                                 (0, int(screen_y)), (SCREEN_WIDTH, int(screen_y)), 1)
            y += TILE_SIZE

        # Red outline marks the hard boundary of the current room.
        pygame.draw.rect(self.logical_surface, self.colors['RED'], (
            (0 * RENDER_SCALE) - self.camera.x,
            (0 * RENDER_SCALE) - self.camera.y,
            self.current_room.width  * RENDER_SCALE,
            self.current_room.height * RENDER_SCALE,
        ), 3)

        # Draw the spawn-point sprite if one has been placed in the editor.
        if hasattr(self, 'room_editor') and self.room_editor and self.room_editor.object_editor:
            spawn_obj = self.room_editor.object_editor.spawn_manager.get_spawn_point(self.current_room.name)
            if spawn_obj:
                sx = (spawn_obj.x * RENDER_SCALE) - self.camera.x
                sy = (spawn_obj.y * RENDER_SCALE) - self.camera.y
                if spawn_obj.sprite:
                    sw     = int(spawn_obj.width  * RENDER_SCALE)
                    sh     = int(spawn_obj.height * RENDER_SCALE)
                    scaled = pygame.transform.scale(spawn_obj.sprite, (sw, sh))
                    self.logical_surface.blit(scaled, (int(sx - sw // 2), int(sy - sh // 2)))

        # Flying pads are registered with the layer manager so they y-sort correctly
        # alongside the player, NPCs, and enemies.  Path previews are drawn separately.

        # "E to Fly" indicator when the player stands on an active pad.
        if not self.flying_controller.is_active():
            nearby_pad = next(
                (pad for pad in self.flying_pads
                 if pad.active and pad.check_collision_with_player(self.player)),
                None
            )
            if nearby_pad:
                px    = (nearby_pad.x * RENDER_SCALE) - self.camera.x
                py    = ((nearby_pad.y - 25) * RENDER_SCALE) - self.camera.y
                font  = self._get_font(20)
                text  = font.render("E to Fly", True, self.colors['YELLOW'])
                trect = text.get_rect(center=(px, py))
                bg    = pygame.Surface((trect.width + 10, trect.height + 5), pygame.SRCALPHA)
                bg.fill((0, 0, 0, 180))
                self.logical_surface.blit(bg,   trect.inflate(10, 5).topleft)
                self.logical_surface.blit(text, trect)

        # Save points
        for sp in self.save_points:
            if sp.active:
                sp.draw(self.logical_surface, self.camera, self.colors, RENDER_SCALE)

        # Wire the tile-change hook lazily (tileset_editor may not exist yet in __init__).
        self._install_tile_change_hook()

        # Background tile layer — both editor and game mode go through the baked-surface
        # path so the per-tile loop only runs when the cache is cold (after a tile edit or
        # room load), not on every frame.
        self._draw_room_tiles(bg=True)

        # Layered game objects (y-sorted) — hidden while a cutscene is running (it draws
        # its own actor layer separately).
        if not self.active_cutscene_runtime:
            self.layer_manager.clear()
            for obj in (self.projectiles + [self.player] + self.enemies + self.npcs
                        + self.destructible_stones + self.level_gates
                        + self.bombs + self.explosions + self.flying_pads):
                self.layer_manager.add_object(obj)
            for melee in self.melee_attacks:
                self.layer_manager.add_object(melee)
            if self.player.current_beam:
                self.layer_manager.add_object(self.player.current_beam)

            # Enemy bullets, rockets, and ki-blasts are not y-sorted — draw them directly.
            for bullet in self.enemy_bullets:
                bullet.draw(self.logical_surface, self.camera, self.colors)
            for rocket in self.enemy_rockets:
                rocket.draw(self.logical_surface, self.camera, self.colors)
            for blast in self.enemy_kiblasts:
                blast.draw(self.logical_surface, self.camera, self.colors)

            self.layer_manager.draw_all(self.logical_surface, self.camera, self.colors, RENDER_SCALE)

            # Damage number popups — drawn on top of sprites but below the HUD
            self.dmg_numbers.draw(self.logical_surface, self.camera, RENDER_SCALE)

        # Cutscene actors are drawn here — BEFORE the foreground tile layer — so that
        # foreground tiles (trees, buildings, tile.layer >= 0) correctly occlude actors
        # standing behind them, matching the layering seen in the cutscene editor.
        elif self.active_cutscene_runtime and not self.pause_menu.active:
            _cs_colors = {'WHITE': (255, 255, 255), 'RED': (220, 60, 60)}
            self.active_cutscene_runtime.draw_actors(self.logical_surface, self.camera, _cs_colors)

        # Flying pad path previews — editor-only overlay drawn after the layer pass.
        if self.dev_menu.active or self.room_editor.active:
            for pad in self.flying_pads:
                if pad.active:
                    pad.draw_path_preview(self.logical_surface, self.camera, RENDER_SCALE)

        # Foreground tile layer (same baked path as background).
        self._draw_room_tiles(bg=False)

        # Test-mode indicator banner — drawn after foreground tiles so it's always on top.
        if self.is_test_mode:
            test_font = self._get_font(32)
            test_text = test_font.render("TEST MODE — Press F2 to return to editor", True, (255, 255, 0))
            test_bg   = pygame.Surface((test_text.get_width() + 20, test_text.get_height() + 10), pygame.SRCALPHA)
            test_bg.fill((0, 0, 0, 180))
            bg_x = (SCREEN_WIDTH - test_text.get_width()) // 2 - 10
            self.logical_surface.blit(test_bg,   (bg_x, 10))
            self.logical_surface.blit(test_text, ((SCREEN_WIDTH - test_text.get_width()) // 2, 15))

        # HUD, menus, and dev overlays.
        # Weather is drawn first (below the dialogue box), then the UI layer which
        # includes the dialogue box, then the colour/invert overlay on top of everything.
        if self.active_cutscene_runtime and not self.pause_menu.active:
            w, h = self.logical_surface.get_size()
            self.active_cutscene_runtime.draw_weather(self.logical_surface, w, h)

        if not self.dev_menu.active:
            self._draw_ui(self.dt)

        # Cutscene colour/invert overlay — must sit on top of everything (tiles, actors,
        # weather, dialogue) so fades and flash effects cover the full screen.
        # draw_actors() was already called before the foreground tile layer above.
        if self.active_cutscene_runtime and not self.pause_menu.active:
            w, h = self.logical_surface.get_size()
            self.active_cutscene_runtime.draw_overlay(self.logical_surface, w, h)

        # Dev tools — always drawn last so they sit on top of everything.
        self.sprite_editor.draw(self.logical_surface)
        self.room_editor.draw(self.logical_surface)
        self.dev_menu.draw(self.logical_surface)
        self.cutscene_editor.draw(self.logical_surface)

        # Collision and transition outlines in editor view mode.
        if self.room_editor.active and self.room_editor.current_view == 'view_room':
            from objects.collision_object import draw_collision_object
            for obj in self.collision_objects:
                draw_collision_object(self.logical_surface, obj, self.camera.x, self.camera.y,
                                      RENDER_SCALE, dev_mode=True, selected=False)
            for transition in self.room_transitions:
                transition.draw(self.logical_surface, self.camera, RENDER_SCALE,
                                dev_mode=True, selected=False)

        # With pygame.SCALED the display handles window-resize scaling in hardware —
        # just flip; no manual surface scale needed.
        pygame.display.flip()

    def _get_font(self, size: int) -> pygame.font.Font:
        """Cached font lookup — avoids allocating a new Font object every frame."""
        if size not in self._font_cache:
            self._font_cache[size] = pygame.font.Font(None, size)
        return self._font_cache[size]

    def _install_tile_change_hook(self):
        """Wire on_tile_changed onto tileset_editor once it has been created.

        tileset_editor is instantiated lazily inside RoomEditor, so this hook
        can't be set in __init__. Called from draw() every frame; the flag
        makes it a no-op after the first successful install.

        The callback simply invalidates the baked tile surface for the affected
        room so _draw_room_tiles() rebuilds it on the next frame.
        """
        if self._tile_change_hook_installed:
            return
        te = getattr(self.room_editor, 'tileset_editor', None)
        if te is None:
            return
        te.on_tile_changed             = lambda room_name=None: self.invalidate_tile_cache(room_name)
        self._tile_change_hook_installed = True

    def invalidate_tile_cache(self, room_name: str = None):
        """Mark a room's baked surface as stale.

        Actual eviction happens once per frame in _flush_dirty_tile_rooms(),
        not immediately, so multiple on_tile_changed calls within the same
        frame only trigger one rebuild instead of one per mouse-motion event.
        """
        self._dirty_tile_rooms.add(room_name)  # None = flush everything

    def _flush_dirty_tile_rooms(self):
        """Evict stale baked surfaces exactly once per frame.

        Call this at the top of the draw loop before any tile surface is
        accessed so every draw pass works with fresh data without rebuilding
        more than once regardless of how many events arrived this frame.
        """
        if not self._dirty_tile_rooms:
            return
        if None in self._dirty_tile_rooms:
            self._room_tile_surfaces.clear()
        else:
            for room in self._dirty_tile_rooms:
                self._room_tile_surfaces.pop((room, True),  None)
                self._room_tile_surfaces.pop((room, False), None)
        self._dirty_tile_rooms.clear()

    def _build_room_tile_surface(self, room_name: str, bg: bool) -> pygame.Surface:
        """Pre-render all tiles for one layer into a single static Surface.

        bg=True  → background layer (tile.layer < 0)
        bg=False → foreground layer (tile.layer >= 0)

        The resulting surface is in scaled-world-space (world_units × RENDER_SCALE),
        so each frame only needs a single camera-offset blit — no per-tile work.

        Tile source priority
        ────────────────────
        1. tileset_editor.room_tiles[room_name]  — editor's live list (always up-to-date;
           used in both editor and game mode so freshly painted tiles appear immediately
           after an invalidate).
        2. room.tiles                            — fallback when the editor has not loaded
           this room yet (e.g. game mode before any editor was opened).
        """
        room = self.room_manager.get_room_by_name(room_name)
        if not room:
            return pygame.Surface((1, 1), pygame.SRCALPHA)

        if not self.room_editor.tileset_editor:
            return pygame.Surface((1, 1), pygame.SRCALPHA)

        # Prefer the editor's live tile list so painted tiles appear immediately.
        te = self.room_editor.tileset_editor
        if room_name in getattr(te, 'room_tiles', {}):
            tiles = te.room_tiles[room_name]
        else:
            tiles = getattr(room, 'tiles', None) or []

        if not tiles:
            return pygame.Surface((1, 1), pygame.SRCALPHA)

        # Allocate a surface the size of the whole room in render-scale space.
        surf = pygame.Surface(
            (int(room.width * RENDER_SCALE), int(room.height * RENDER_SCALE)),
            pygame.SRCALPHA,
        )

        tileset_mgr = te.tileset_manager
        for tile in tiles:
            is_bg_tile = tile.layer < 0
            if bg != is_bg_tile:
                continue
            tileset = tileset_mgr.get_tileset(tile.tileset_name)
            if not tileset or not tileset.image:
                continue
            scaled = tileset.get_scaled_tile_surface(tile.tile_x, tile.tile_y, RENDER_SCALE)
            if scaled:
                surf.blit(scaled, (int(tile.x * RENDER_SCALE), int(tile.y * RENDER_SCALE)))

        return surf

    def _draw_room_tiles(self, bg: bool):
        """Blit the baked tile surface for the current room.

        Builds and caches on first call per room/layer combo. After that it's
        a single blit per frame — O(1) regardless of tile count.

        Works in both game mode and editor mode: the baked surface is rebuilt
        from tileset_editor.room_tiles (live editor list) when available, so
        freshly painted tiles appear on the very next frame after invalidation.
        """
        if not self.current_room:
            return

        room_name = self.current_room.name

        # Accept the room as drawable if either room.tiles or the editor's live
        # tile list has content for this room.
        te       = getattr(self.room_editor, 'tileset_editor', None)
        has_tiles = (
            getattr(self.current_room, 'tiles', None)
            or (te and room_name in getattr(te, 'room_tiles', {}))
        )
        if not has_tiles:
            return

        key = (room_name, bg)
        if key not in self._room_tile_surfaces:
            self._room_tile_surfaces[key] = self._build_room_tile_surface(room_name, bg)
        surf = self._room_tile_surfaces[key]

        self.logical_surface.blit(surf, (-int(self.camera.x), -int(self.camera.y)))

    def blit_room_tiles(self, screen: 'pygame.Surface', room_name: str,
                        camera_x: int, camera_y: int, bg: bool):
        """Public helper used by the room editor to blit the baked tile surface
        onto an arbitrary surface with an arbitrary camera offset.

        Lets the editor share the same O(1) baked-surface path instead of
        calling pygame.transform.scale() on every tile every frame.
        """
        # Don't attempt to build until the tileset_editor is fully initialised.
        # If we cached a blank surface here it would persist and tiles would
        # never appear for the rest of the session.
        if not getattr(self.room_editor, 'tileset_editor', None):
            return
        key = (room_name, bg)
        if key not in self._room_tile_surfaces:
            self._room_tile_surfaces[key] = self._build_room_tile_surface(room_name, bg)
        surf = self._room_tile_surfaces.get(key)
        if surf:
            screen.blit(surf, (-camera_x, -camera_y))

    def _draw_tile(self, tile):
        """Blit a single tile at its world position. Mostly used for one-off debug draws."""
        tileset = self.room_editor.tileset_editor.tileset_manager.get_tileset(tile.tileset_name)
        if not tileset or not tileset.image:
            return
        tile_surface = tileset.get_tile_surface(tile.tile_x, tile.tile_y)
        if not tile_surface:
            return
        screen_x = (tile.x * RENDER_SCALE) - self.camera.x
        screen_y = (tile.y * RENDER_SCALE) - self.camera.y
        scaled   = pygame.transform.scale(
            tile_surface,
            (tileset.tile_width * RENDER_SCALE, tileset.tile_height * RENDER_SCALE),
        )
        self.logical_surface.blit(scaled, (int(screen_x), int(screen_y)))

    def _draw_ui(self, dt):
        """Draw all UI elements that appear on top of the game world."""
        self.npc_config_menu.draw(self.logical_surface, self.colors)
        self.dialogue_box.draw(self.logical_surface, self.colors)
        self.save_point_menu.draw(self.logical_surface)
        self.character_switch_menu.draw(self.logical_surface)
        self.pause_menu.draw(self.logical_surface, self.player, self.play_time)
        self.level_up_notification.draw(self.logical_surface, self.colors)

        self.transition_config_menu.draw(self.logical_surface)
        self.transition_controller.draw(self.logical_surface)
        self._draw_cutscene_fade(self.logical_surface)

        # HUD slide animation — slides in/out when entering/leaving game mode.
        if self.ui.current_screen == 'game' and not self.character_switch_menu.active \
                and not self.pause_menu.active:
            _HUD_SLIDE_SPEED = 400
            # Derive the hidden position from the actual scaled frame height so
            # the entire HUD (including the boss bar) clears the top of the screen.
            _scaled_frame_h  = int(self.sprite_hud.config['frame']['h'] * self.sprite_hud.scale)
            _hud_hidden_y    = -(self.sprite_hud.hud_y + _scaled_frame_h + 10)
            if self.sprite_hud._hud_slide_out:
                self.sprite_hud.hud_offset_y = max(
                    _hud_hidden_y,
                    self.sprite_hud.hud_offset_y - _HUD_SLIDE_SPEED * dt,
                )
            elif self.sprite_hud._hud_slide_in:
                self.sprite_hud.hud_offset_y = min(
                    0.0,
                    self.sprite_hud.hud_offset_y + _HUD_SLIDE_SPEED * dt,
                )
                if self.sprite_hud.hud_offset_y >= 0.0:
                    self.sprite_hud._hud_slide_in = False
            self.sprite_hud.draw(self.logical_surface, self.player, enemies=self.enemies, dt=dt)

        # Full-screen overlays for main menu and sub-screens.
        if self.ui.current_screen == 'main_menu':
            self.ui.draw_main_menu(self.logical_surface, self.colors)
        elif self.ui.current_screen == 'status':
            self.ui.draw_status_screen(self.logical_surface, self.player, self.game_config, self.colors)
        elif self.ui.current_screen == 'inventory':
            self.ui.draw_inventory_screen(self.logical_surface, self.player, self.colors)
        elif self.ui.current_screen == 'options':
            self.ui.draw_options_screen(self.logical_surface, self.colors)

    # ── Editor / room sync ────────────────────────────────────────────────────

    def _sync_spawn_manager_with_rooms(self):
        """Point all editor manager dicts directly at the lists on each Room object.

        This way editor changes are immediately visible in-game (no reload needed),
        and any data already on the rooms is visible to the editor on startup.
        """
        if not hasattr(self.room_editor, 'object_editor') or not self.room_editor.object_editor:
            return

        oe                 = self.room_editor.object_editor
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
        """Flush all editor and room data to disk before quitting.

        Exits test mode first so temporary test state never gets saved.
        """
        if self.is_test_mode:
            self._exit_test_mode()

        # Flush the cutscene editor — catches crashes and abrupt window closes.
        if hasattr(self, 'cutscene_editor') and self.cutscene_editor:
            ce = self.cutscene_editor
            if ce.view == 'edit' and ce.cutscene_data:
                if ce.unsaved:
                    ce._save_cutscene()
                else:
                    ce._save_viewport_state()

        if hasattr(self, 'room_editor') and self.room_editor:
            self.room_editor.save_all_editor_data_to_rooms()

        if hasattr(self, 'room_manager'):
            self.room_manager.save_all_rooms()

    def run(self):
        """Main loop — runs until self.running is False or an unhandled exception occurs."""
        self.last_time = time.time()
        try:
            while self.running:
                self.handle_events()
                self.update()
                self.draw()
                self.clock.tick(FPS)
        except Exception:
            import traceback
            traceback.print_exc()   # print the real crash reason before exiting
            raise                   # re-raise so the OS exit code becomes 1
        finally:
            self.cleanup()
            pygame.quit()
            sys.exit()


if __name__ == "__main__":
    game = Game()
    game.run()