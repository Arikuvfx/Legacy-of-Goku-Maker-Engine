"""
game.py — Top-level Game class and entry point.

Coordinates every subsystem: rendering, audio, input, room loading, entity AI,
cutscenes, save/load, and the full dev-tooling layer. Cross-system calls route
through Game rather than between subsystems directly.

Boot sequence:  pygame.init → build all subsystems → create default room → run()
Main loop:      handle_events → update → draw → clock.tick(FPS)
"""

import sys
import math
import time
import random

# ── Game subsystems — import order follows dependency depth ────────────────────
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
from dev_tools.world_map_editor import WorldMapEditor
from dev_tools import character_creator


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
        self.world_map_editor = WorldMapEditor(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.world_map_editor.room_manager = self.room_manager
        self.character_creator = character_creator.CharacterCreator(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.cutscene_editor = CutsceneEditor(
            self.room_manager,
            self.room_editor,
            SCREEN_WIDTH,
            SCREEN_HEIGHT,
            dialogue_box=self.dialogue_box,
            sound_manager=self.sound_manager,
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

        # Map-jump screen fade — begins once the player drifts fully off the top
        # of the camera view.  Separate from _csf_* so cutscenes are unaffected.
        # _mjf_ prefix = ‘map-jump fly’; every flying-sequence variable lives here.
        self._mjf_alpha  = 0.0
        self._mjf_active = False
        self._MJF_SPEED  = 255.0 / 1.5   # full black in 1.5 s

        # World-map flying sequence — activated after the map-jump fade-out.
        #   _mjf_state:  None | 'pending_fade_in' | 'fade_in' | 'flying'
        #   'pending_fade_in' — player has exited; waiting for alpha to hit 255
        #   'fade_in'         — alpha counting back down to 0 over the flying scene
        #   'flying'          — fully visible; player steers the flying sprite
        self._mjf_state              = None
        self._MJF_FADE_IN_SPEED      = 255.0 / 0.8   # fade in over 0.8 s
        self._MJF_FLY_SPEED          = 400            # screen-pixels / second
        self._mjf_fly_x              = 0.0            # screen-space centre X of flying sprite
        self._mjf_fly_y              = 0.0            # screen-space centre Y of flying sprite
        self._mjf_cam_x              = 0.0            # world-space camera X in texture pixels
        self._mjf_cam_y              = 0.0            # world-space camera Y in texture pixels
        self._mjf_cam_angle          = 0.0            # viewing angle in radians (Mode 7 rotation)
        self._mjf_altitude           = 0.5            # normalised altitude: 0.0 = low, 1.0 = high
        self._mjf_flying_frames: list = []            # raw pygame Surfaces (one per frame)
        self._mjf_flying_frame_idx   = 0
        self._mjf_flying_frame_timer = 0.0
        self._MJF_FLY_FRAME_DUR      = 0.12           # seconds per animation frame
        # Focal length for the Mode 7 ground plane.
        # Controls the perceived camera altitude / viewing angle.
        # Lower  → shallower angle, strong perspective (ground rushes toward horizon).
        # Higher → steeper angle, flatter look (more overhead / top-down).
        # The horizon line (horizon_y) is set independently and is never affected.
        self._MJF_FOCAL              = 160   # default ≈ 80 px
        self._MJF_HORIZON_OFFSET     = 2
        # Max upward pixel shift at the horizon (curvature effect). 0 = flat plane.
        self._MJF_CURVATURE          = 100

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
        self.world_map_objects = []
        self.music_objects        = []   # room's placed Music object (0 or 1); invisible, sets BGM only

        # ── Performance caches ────────────────────────────────────────────────
        # key: (room_name, is_background) → pre-baked tile Surface
        self._room_tile_surfaces: dict = {}
        self._dirty_tile_rooms:   set  = set()   # rooms pending a surface rebuild
        # key: font_size → pygame.Font  (avoids allocating a Font every frame)
        self._font_cache: dict = {}
        self._scaled_tile_cache: dict = {}
        # Scrolling background (room.scrolling_bg): key: image filename → scaled Surface
        self._bg_image_cache: dict = {}
        # Per-room autonomous scroll offset accumulators, so each room's background
        # keeps its own phase instead of resetting/jumping when you switch rooms.
        # key: room_name → [offset_x, offset_y]
        self._bg_scroll_accum: dict = {}

        # ── Save point system ─────────────────────────────────────────────────
        self.save_points           = []
        self.save_point_manager    = SavePointManager()
        self.save_point_menu       = SavePointMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.character_switch_menu = CharacterSwitchMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.pause_menu            = PauseMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.pause_menu.set_sound_engine(self.sound_engine)
        self.play_time             = 0.0   # total seconds spent in gameplay
        self.nearby_save_point     = None
        self.nearby_world_map_obj  = None   # WorldMapObject the player can currently interact with

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
        oe.on_music_placed             = self._on_music_placed
        oe.on_music_deleted            = self._on_music_deleted

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
        self.flying_controller.set_sound_manager(self.sound_manager)

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

            if self.world_map_editor.active:
                self.world_map_editor.handle_input(event)
                continue

            if self.character_creator.active:
                self.character_creator.handle_input(event)
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
                elif result == 'open_world_map_editor':
                    self.dev_menu.active = False
                    self.world_map_editor.toggle()
                elif result == 'open_character_creator':
                    self.dev_menu.active = False
                    self.character_creator.toggle()
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
        """Key-down events during normal gameplay.

        Keybindings at a glance:
          F1      — dev menu toggle
          F2      — exit test mode (returns to room editor)
          ESC     — pause menu
          Arrows  — move; double-tap a direction to start running
          Shift   — hold to run
          E       — interact / melee / advance dialogue
          Q       — ki blast (blast mode) or begin beam charge (beam mode)
          TAB     — cycle ki mode: blast → beam → transform → blast
          X       — trigger transformation (transform mode, when fully charged)
        """
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
            # Cycle through only the modes this character is allowed to use.
            modes = self._get_allowed_ki_modes()
            if len(modes) > 1:
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
        if self._mjf_state in ('pending_fade_in', 'fade_in', 'flying',
                               'landing_fade_out', 'landing_fade_in'):
            return

        # Save point takes top priority.
        if self.nearby_save_point and not self.dialogue_box.active and not self.save_point_menu.active:
            if self.nearby_save_point.variant == 'big':
                self.save_point_menu.open()
            return

        # World-map object — start the jump sequence.
        if self.nearby_world_map_obj and self.player.can_act():
            self._start_map_jump()
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
            # Default: throw a melee punch. Whether it plays melee1/melee2
            # (hit) or melee_miss depends on whether it connects with
            # anything — resolved once the swing ends, see the melee-attack
            # cleanup loop in update().
            melee = self.player.melee_attack()
            if melee:
                melee.hit_something = False
                self.melee_attacks.append(melee)

    def _start_map_jump(self):
        """Kick off the world-map jump sequence for the player.

        Determines the correct costume (base or ssj) so the sprite system
        loads map_jump.png from the right character folder, then delegates
        the actual animation / movement to player.start_map_jump().
        The on_map_jump_exit callback is wired here so game.py handles the
        scene transition once the player is fully off-screen.
        """
        # Remember which world map this object points to so the Mode7 renderer
        # can load the correct tile map instead of the default PNG.
        new_map_name = getattr(self.nearby_world_map_obj, 'map_name', '') or ''
        if getattr(self, '_active_world_map_name', None) != new_map_name:
            # Map changed — bust the texture cache so it re-renders from the new map.
            self._active_world_map_name = new_map_name
            for attr in ('_world_map_texture', '_world_map_tex_arr', '_wm_locations',
                         '_wm_entities', '_wm_vehicle_cache'):
                if hasattr(self, attr):
                    delattr(self, attr)
        # Derive the current form so the sprite loader picks the right folder.
        # Uses the path TransformationSystem already resolved (e.g.
        # "base/transformations/ssj") rather than a hardcoded 'ssj', which
        # would point at a non-existent folder now that transformation
        # sprites live nested under the base costume.
        ts      = self.player.transformation
        costume = (ts.current_transform_costume
                   if (ts and ts.is_transformed and ts.current_transform_costume)
                   else 'base')

        # If the player's sprite isn't already on the right costume sheet, swap
        # it now so map_jump.png is loaded from the correct directory.
        if getattr(self.player, 'costume', 'base') != costume:
            from core.sprite_system import create_character_sprite
            self.player.sprite  = create_character_sprite(
                self.player.character, costume, 32, 32)
            self.player.costume = costume

        # Capture the WMO reference NOW — nearby_world_map_obj will be cleared
        # to None by _update_world_map_objects before _on_exit fires, because the
        # player moves away from the object during the jump animation.
        _captured_wmo = self.nearby_world_map_obj
        self._mjf_origin_wmo = _captured_wmo   # also stored on self for the fast-path fade block

        def _on_exit():
            """Called when the player drifts fully off the top of the screen.

            Load the character-specific flying sprite and transition the game
            into the world-map flying sequence.  If the black fade is already
            complete we can start fading back in immediately; otherwise mark
            'pending_fade_in' so the update loop waits for full black first.
            """
            self._load_map_fly_sprite()
            # Place the flying sprite at screen-centre, slightly below mid.
            self._mjf_fly_x = SCREEN_WIDTH  / 2
            self._mjf_fly_y = SCREEN_HEIGHT * 0.65
            # Neutral cam seed — the pin correction below overwrites this.
            # Do NOT derive from the cached texture: on re-entry the texture
            # still exists and tex.width/2 would place the camera at the map
            # centre instead of above the correct location pin.
            self._mjf_cam_x = 0.0
            self._mjf_cam_y = 0.0
            # Always reset to mid-altitude so the player doesn't re-enter at
            # ground level after having descended to land on the previous visit.
            self._mjf_altitude = 0.5
            # Signal that the pin correction must run on the next draw tick.
            # Using a flag (rather than relying on the texture lazy-load block)
            # ensures the correction fires even when the texture is already cached.
            self._mjf_needs_pin_correction = True
            # Record which room we came from so the draw path can reposition
            # the camera to the matching location pin (or entity position).
            self._mjf_origin_room   = self.current_room.name if self.current_room else ''
            # If the player entered this room via an entity collision, use that
            # entity's name for the camera correction — regardless of which WMO
            # they used to leave. _mjf_last_entry_entity is set at landing time
            # when the entity name is definitively known.
            self._mjf_origin_entity = getattr(self, '_mjf_last_entry_entity', '')
            self._apply_world_map_music(new_map_name)
            if self._mjf_alpha >= 255.0:
                self._mjf_state  = 'fade_in'
                self._mjf_active = False
            else:
                self._mjf_state = 'pending_fade_in'
            # Reset the player sprite so it doesn't stay frozen on the
            # map_jump frame while the screen is still visible.
            self.player.sprite.set_animation('idle', self.player.direction)
            self.player.current_animation_state = 'idle'

        self.player.on_map_jump_exit = _on_exit
        self.camera.locked = True
        # Reset the fade so a repeated jump always starts from transparent.
        self._mjf_alpha  = 0.0
        self._mjf_active = False
        self.player.start_map_jump()

    def _start_npc_dialogue(self, npc):
        """Begin a conversation with an NPC, routing through the mission system.

        Mission state routing (via mission_manager.get_npc_dialogue_state):
          'offer'     — quest not accepted yet; show the pitch
          'active'    — mission running; give a status/reminder line
          'completed' — all objectives met; run reward dialogue & claim XP/items
          'rewarded'  — fully done; show post-completion lines
          None        — plain NPC with no mission; use dialogue_config directly
        """
        # Hide the NPC's own indicator the moment dialogue starts.
        npc.is_talking = True

        # Immediately snap the player to idle so walking-into-E never leaves
        # the walk/run animation frozen on screen during the conversation.
        if self.player.current_animation_state in ('walk', 'run'):
            self.player.sprite.set_animation('idle', self.player.direction)
            self.player.current_animation_state = 'idle'
        self.player.is_running = False

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

        # ── Pre-bake tile surfaces so frame 1 has no spike ──────────────────────
        # invalidate_tile_cache was called inside _load_room_objects_as_copies,
        # so flush it now and build both layers immediately rather than lazily
        # on the first rendered frame.
        self._flush_dirty_tile_rooms()
        self._room_tile_surfaces[(room_name, True)] = self._build_room_tile_surface(room_name, True)
        self._room_tile_surfaces[(room_name, False)] = self._build_room_tile_surface(room_name, False)
        # ────────────────────────────────────────────────────────────────────────

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

        # World map objects — copy so test mode doesn't mutate the editor originals
        self.world_map_objects    = []
        self.nearby_world_map_obj = None
        self.camera.locked        = False
        if hasattr(room, 'world_map_objects') and room.world_map_objects:
            from objects.world_map import WorldMapObject
            for obj in room.world_map_objects:
                self.world_map_objects.append(WorldMapObject.from_dict(obj.to_dict()))

        # Music object — copy so test mode doesn't mutate the editor original
        self.music_objects = []
        if hasattr(room, 'music_objects') and room.music_objects:
            from objects.music_object import MusicObject
            for obj in room.music_objects:
                self.music_objects.append(MusicObject.from_dict(obj.to_dict()))
        self._apply_room_music(room)

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

        # Whatever music the test room started (via its Music object, a
        # cutscene's play_music action, etc.) otherwise just keeps playing
        # forever — nothing re-applies a context/room track on exiting test
        # mode. Cut it instantly here so dropping back into the room editor
        # is silent right away, with no fade-out lag.
        self.sound_manager.stop_music(fade_out=False)

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
        self.world_map_objects   = room.world_map_objects[:]   if hasattr(room, 'world_map_objects')   and room.world_map_objects   else []
        self.music_objects       = room.music_objects[:]       if hasattr(room, 'music_objects')       and room.music_objects       else []
        self._apply_room_music(room)


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

        20 iterations handles any realistic tight 3-wall corner;
        fewer leaves enemies visibly clipping through walls after hard knockback.
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
        # Only include WorldMapObjects that have a real collision rect.
        # The plain 'world_map' variant returns None from get_collision_rect(),
        # which crashes enemy collision checks (colliderect(None)).
        _solid_wm = [o for o in self.world_map_objects if o.get_collision_rect() is not None]
        obstacles = (
            self.collision_objects
            + self.destructible_stones
            + self.level_gates
            + self.room_transitions
            + _solid_wm
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

    def _on_music_placed(self, music_obj, room_name):
        """Sync game list when a Music object is placed in the editor, and
        persist so the track choice isn't lost. If it was placed in the room
        currently being viewed/tested, apply it immediately."""
        if not self.is_test_mode and self.current_room and self.current_room.name == room_name:
            self.music_objects = [music_obj]
            self._apply_room_music(self.current_room)

        room = self.room_manager.get_room_by_name(room_name)
        if room:
            self.room_manager.save_room(room)

    def _on_music_deleted(self, music_obj, room_name):
        """Sync game list when a Music object is removed in the editor.

        Per design, removing the room's Music object does NOT stop or change
        whatever is currently playing — it just means this room no longer
        specifies a track for future room entries.
        """
        if not self.is_test_mode and self.current_room and self.current_room.name == room_name:
            if music_obj in self.music_objects:
                self.music_objects.remove(music_obj)

        room = self.room_manager.get_room_by_name(room_name)
        if room:
            self.room_manager.save_room(room)

    def _apply_room_music(self, room):
        """Apply the given room's Music object, if any, to the currently playing track.

        Design rule: if the room has no Music object placed, do nothing —
        whatever track is already playing keeps playing uninterrupted.
        """
        if not room:
            return

        music_objs = getattr(room, 'music_objects', None)
        if not music_objs:
            return  # No Music object placed — keep whatever is already playing

        track = music_objs[0].track
        if not track:
            return  # Music object placed but no track chosen yet — leave music as-is

        if track == self.sound_engine.current_music:
            return  # Already playing this track — avoid restarting it on every room entry

        self.sound_manager.play_music(track)

    def _apply_world_map_music(self, map_name):
        """Apply the given world map's music track, if any, to the currently
        playing track. Mirrors _apply_room_music's behavior/design rule: if the
        map has no track set (or the JSON can't be read), do nothing and leave
        whatever is already playing alone.

        The 'music' field is written by the world map editor's Mode7 Music
        dropdown (see WorldMap.to_dict in dev_tools/world_map_editor.py) and
        stores a track stem, ready to hand straight to play_music().
        """
        if not map_name:
            return

        import json as _json
        import os as _os

        path = _os.path.join('assets', 'world_maps', f'{map_name}.json')
        try:
            with open(path) as f:
                data = _json.load(f)
        except Exception:
            return  # No saved map data yet — leave music as-is

        track = data.get('music', '')
        if not track:
            return  # No track set for this map — keep whatever is already playing

        if track == self.sound_engine.current_music:
            return  # Already playing this track — avoid restarting it on re-entry

        self.sound_manager.play_music(track)

    def _sync_player_from_cutscene(self, runtime):
        """After a cutscene ends, move the player to the final position,
        direction, and character of the actor that shares the same
        character as the current player.

        Matching priority:
          1. type == 'player'  AND  character == self.player.character  (exact match)
          2. type == 'player'  with no 'character' field set            (untagged fallback)
        The first exact match wins; if none is found, the first untagged player
        actor is used so cutscenes that don't specify a character still work.

        If a `set_character` action changed the actor's character mid-cutscene,
        that change is mirrored onto the real player here so transformations
        performed during playback persist afterwards instead of reverting.
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
            final_direction = getattr(entity, 'direction', None) or self.player.direction
            self.player.direction = final_direction

            # Mirror any character change made via a `set_character` cutscene
            # action onto the real player. The cutscene actor is a throwaway
            # entity created by the editor's entity_factory, so without this
            # the transformation only ever existed on that temp object and
            # the player would snap back to their pre-cutscene character the
            # moment playback ends.
            final_character = getattr(entity, 'character', None)
            if final_character and final_character != player_character:
                # Reuse the same swap helper as the in-game character-switch
                # menu so the transformation comes with correctly-updated
                # equipped attacks / ki mode, not just a cosmetic sprite swap.
                self._switch_character(final_character)
            elif hasattr(self.player, 'sprite') and self.player.sprite:
                # No character change — just push the final facing into the
                # existing sprite so the idle frame shown immediately after
                # the cutscene matches the new direction.
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
                        sound_manager=self.sound_manager,
                    )

                    # Wire up the change_room callback. The runtime declares
                    # on_change_room = None and calls it when the action fires,
                    # but game.py must supply the real implementation.
                    def _cutscene_change_room(room_name, _sx, _sy):
                        target_room = self.room_manager.get_room_by_name(room_name)
                        if not target_room:
                            print(f'[Game] change_room: room not found: {room_name}')
                            return

                        # ── Sync editor tiles into the room object (same as test-mode start) ──
                        te = getattr(self.room_editor, 'tileset_editor', None)
                        if te and room_name in getattr(te, 'room_tiles', {}):
                            target_room.tiles = te.room_tiles[room_name][:]
                        elif not hasattr(target_room, 'tiles'):
                            target_room.tiles = []

                        self.room_manager.current_room = target_room
                        self.current_room = target_room
                        if self.is_test_mode:
                            self._load_room_objects_as_copies(target_room)
                        else:
                            self._load_room_objects(target_room)
                        self.mission_manager.on_room_entered(room_name)

                    self.active_cutscene_runtime.on_change_room = _cutscene_change_room
                    self.active_cutscene_runtime.seek(0.0)

                    # After seek() resets actor positions to their scripted spawn,
                    # snap the player actor to the real player's world position so
                    # any move_to tweens start from where the player actually is.
                    _pc = getattr(self.player, 'character', None)
                    for _adef in data.get('actors', []):
                        if _adef.get('type') != 'player':
                            continue

                        _live = self.active_cutscene_runtime.actors.get(_adef['id'])
                        if not (_live and hasattr(_live, 'entity')):
                            continue

                        # Position/direction sync is unconditional: wherever you stood and
                        # whichever way you faced in the room carries into the cutscene,
                        # regardless of what character this actor is scripted to become.
                        _live.entity.x = self.player.x
                        _live.entity.y = self.player.y
                        _live.entity.direction = self.player.direction
                        _live.set_animation(
                            getattr(self.player, 'current_animation_state', 'idle'),
                            self.player.direction,
                        )
                        if _live._tween is not None:
                            _live._tween.start_x = self.player.x
                            _live._tween.start_y = self.player.y

                        # Character/transformation overrides stay gated on the match —
                        # don't force the player's real character onto an actor that's
                        # deliberately authored to start as someone else.
                        _ac = _adef.get('character')
                        if _ac is not None and _ac != _pc:
                            break
                        if _pc:
                            _live.entity.character = _pc

                        _ts = self.player.transformation
                        if _ts and _ts.is_transformed:
                            from core.sprite_system import create_character_sprite
                            _char = getattr(self.player, 'character', 'goku')
                            _live.entity.sprite = create_character_sprite(_char, 'ssj', 32, 32)
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
                _char_before = getattr(self.player, 'character', None)
                self._sync_player_from_cutscene(self.active_cutscene_runtime)
                _char_after = getattr(self.player, 'character', None)
                _char_changed = _char_before != _char_after

                snap = getattr(self, '_pre_cutscene_transform', None)
                if snap and self.player.transformation:
                    _ts = self.player.transformation
                    _ts.is_transformed = snap['is_transformed']
                    _ts.is_transforming = snap['is_transforming']
                    _ts.is_untransforming = snap['is_untransforming']
                    _ts.transformed_ki = snap['transformed_ki']
                    if not _char_changed:  # don't clobber the sprite _switch_character just set
                        self.player.sprite = snap['sprite']
                        self.player.current_animation_state = snap['anim_state']
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

        # Load the cutscene data -- prefer the editor's live in-memory copy when it
        # has this cutscene open, so unsaved edits (e.g. a freshly-added change_room
        # action) are visible immediately during testing without requiring a manual save.
        cutscene_data = None
        ce = getattr(self, 'cutscene_editor', None)
        if (ce
                and getattr(ce, 'cutscene_name', '') == fired_id
                and ce.cutscene_data):
            cutscene_data = ce.cutscene_data

        if cutscene_data is None:
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
        # Use SRCALPHA so the alpha is baked into the fill — set_alpha() does not
        # work correctly when blitting onto a pygame.SCALED display surface.
        if (not hasattr(self, '_csf_surf') or self._csf_surf.get_size() != (w, h)):
            self._csf_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        self._csf_surf.fill((0, 0, 0, min(255, alpha)))
        surface.blit(self._csf_surf, (0, 0))

    def _draw_map_jump_fade(self, surface):
        """Draw the black fade overlay that plays once the player leaves the camera
        during the world-map jump sequence.  No-op when alpha is 0.
        """
        alpha = int(self._mjf_alpha)
        if alpha <= 0:
            return
        w, h = surface.get_size()
        # Use SRCALPHA so the alpha is baked into the fill — set_alpha() does not
        # work correctly when blitting onto a pygame.SCALED display surface.
        if not hasattr(self, '_mjf_surf') or self._mjf_surf.get_size() != (w, h):
            self._mjf_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        self._mjf_surf.fill((0, 0, 0, min(255, alpha)))
        surface.blit(self._mjf_surf, (0, 0))

    # ── World-map flying helpers ──────────────────────────────────────────────

    def _wm_entity_tile_pos(self, entity_name: str):
        """Return the current (tile_x, tile_y) of the named world-map entity.
        Uses the EXACT same path-interpolation logic as the draw loop's _wm_ent_pos
        nested function so the position always matches what is shown on screen.
        """
        import math as _m, os as _o, json as _j

        entities = getattr(self, '_wm_entities', None)
        if not entities:
            _map = getattr(self, '_active_world_map_name', '')
            if not _map:
                return None
            try:
                with open(_o.path.join('assets', 'world_maps', f'{_map}.json')) as _f:
                    self._wm_entities = _j.load(_f).get('entities', [])
                entities = self._wm_entities
            except Exception:
                return None

        ent = next((e for e in entities if e.get('name', '') == entity_name), None)
        if ent is None:
            return None

        path   = ent.get('path', [])
        closed = ent.get('closed', False)
        t      = getattr(self, '_wm_entity_anim_t', 0.0)

        if not path:
            return None
        if len(path) == 1:
            return float(path[0][0]), float(path[0][1])

        # Mirror the draw loop exactly: close the loop by appending path[0]
        pts = list(path)
        if closed:
            pts.append(path[0])

        segs, total = [], 0.0
        for _i in range(len(pts) - 1):
            _d = _m.hypot(pts[_i+1][0] - pts[_i][0], pts[_i+1][1] - pts[_i][1])
            segs.append(_d)
            total += _d

        if total == 0.0:
            return float(path[0][0]), float(path[0][1])

        _SPEED = 2.5   # must match _wm_ent_pos in the draw loop
        if closed:
            dist = (t * _SPEED) % total
        else:
            cycle = total * 2.0
            phase = (t * _SPEED) % cycle
            dist  = phase if phase <= total else cycle - phase

        walked = 0.0
        for _i, _sl in enumerate(segs):
            if walked + _sl >= dist or _i == len(segs) - 1:
                frac = ((dist - walked) / _sl) if _sl > 0 else 0.0
                frac = max(0.0, min(1.0, frac))
                return (pts[_i][0] + frac * (pts[_i+1][0] - pts[_i][0]),
                        pts[_i][1] + frac * (pts[_i+1][1] - pts[_i][1]))
            walked += _sl

        return float(path[-1][0]), float(path[-1][1])

    def _load_map_fly_sprite(self):
        """Load the character-specific flying sprite for the world-map sequence.

        flying.png is a horizontal strip of (player.width × player.height) frames
        with one row per direction — 8 rows total, matching the layout used by
        FlyingController:
          Row 0: down
          Row 1: left
          Row 2: right
          Row 3: up
          Row 4: down_left
          Row 5: down_right
          Row 6: up_left
          Row 7: up_right

        All eight rows are loaded up-front so the active frame set can switch
        instantly when the player changes direction while flying.

        Falls back in order:
          1. Last frame of the map_jump animation (already loaded).
          2. A simple gold disc placeholder so the game never crashes.
        """
        path = f'{self.player.sprite.base_path}/flying.png'
        frame_w = self.player.width
        frame_h = self.player.height
        # Row order must match flying.png — clockwise starting from down.
        _DIR_ROWS = {
            'down':       0,
            'down_left':  1,
            'left':       2,
            'up_left':    3,
            'up':         4,
            'up_right':   5,
            'right':      6,
            'down_right': 7,
        }
        try:
            sheet = pygame.image.load(path).convert_alpha()
            n     = max(1, sheet.get_width() // frame_w)
            # Only slice rows that actually exist in the sheet.
            max_rows = sheet.get_height() // frame_h
            self._mjf_flying_frames_by_dir = {
                direction: [
                    sheet.subsurface(pygame.Rect(i * frame_w, row * frame_h, frame_w, frame_h))
                    for i in range(n)
                ]
                for direction, row in _DIR_ROWS.items()
                if row < max_rows
            }
            # Always face 'up' on the world map so the player's back is shown
            # (the map rotates under the player; the sprite never turns).
            start_dir = 'up' if 'up' in self._mjf_flying_frames_by_dir else 'down'
            self._mjf_flying_frames = self._mjf_flying_frames_by_dir.get(start_dir, list(self._mjf_flying_frames_by_dir.values())[0])
        except Exception as e:
            print(f'[map_fly] could not load {path}: {e}')
            jump_frames = getattr(self.player, '_map_jump_frames', [])
            fallback = [jump_frames[-1]] if jump_frames else None
            if fallback is None:
                s = pygame.Surface((32, 32), pygame.SRCALPHA)
                pygame.draw.circle(s, (255, 220, 0), (16, 16), 14)
                pygame.draw.circle(s, (255, 255, 120), (13, 12), 5)
                fallback = [s]
            # No per-direction data available — use the same fallback for all dirs.
            self._mjf_flying_frames_by_dir = {d: fallback for d in _DIR_ROWS}
            self._mjf_flying_frames = fallback

        self._mjf_flying_frame_idx   = 0
        self._mjf_flying_frame_timer = 0.0
        self._mjf_fly_direction      = 'up'  # always face forward (show player's back)
        self._mjf_cam_angle          = 0.0                    # reset rotation each sequence

    def _load_map_land_sprite(self):
        """Load map_land.png — 32×32 frames, one row per direction, left-to-right.

        Row order mirrors flying.png (clockwise from down).  Only rows that
        actually exist in the sheet are loaded so a 1-row sheet works too.
        """
        _path    = f'{self.player.sprite.base_path}/map_land.png'
        _frame_w = 32
        _frame_h = 32
        _DIR_ROWS = [
            'down', 'down_left', 'left', 'up_left',
            'up',   'up_right',  'right', 'down_right',
        ]
        try:
            sheet  = pygame.image.load(_path).convert_alpha()
            cols   = max(1, sheet.get_width()  // _frame_w)
            rows   = max(1, sheet.get_height() // _frame_h)
            frames_by_dir = {}
            for row_idx, direction in enumerate(_DIR_ROWS[:rows]):
                frames_by_dir[direction] = [
                    sheet.subsurface(pygame.Rect(c * _frame_w, row_idx * _frame_h, _frame_w, _frame_h))
                    for c in range(cols)
                ]
            # Fallback: if only one row, use it for every direction.
            if len(frames_by_dir) == 1:
                only = list(frames_by_dir.values())[0]
                frames_by_dir = {d: only for d in _DIR_ROWS}
        except Exception as e:
            print(f'[map_land] FAILED to load {_path}: {e}')
            s = pygame.Surface((_frame_w, _frame_h), pygame.SRCALPHA)
            frames_by_dir = {d: [s] for d in _DIR_ROWS}

        self._mjf_land_frames_by_dir = frames_by_dir
        self._mjf_land_frames        = frames_by_dir.get(
            self.player.direction, list(frames_by_dir.values())[0])
        self._mjf_land_frame_idx     = 0
        self._mjf_land_frame_timer   = 0.0
        self._mjf_land_elapsed       = 0.0   # total time spent in landing_fade_in state
        self._MJF_LAND_FRAME_DUR     = 0.12  # matches flying animation speed

    def _draw_landing_sprite(self):
        """Blit the current map_land frame at the player's world position.

        Called during 'landing_fade_in' after the layer manager has already
        drawn the player's normal sprite, so the land animation sits on top.
        Frames are scaled by RENDER_SCALE to match every other sprite in the
        game world (the layer manager applies the same scaling).

        The shadow is drawn first (below the sprite) using the same
        layer_manager._draw_shadow() call that every other entity (enemies,
        NPCs, the player during ascent) uses, so it matches perfectly.
        """
        _land_frames = getattr(self, '_mjf_land_frames', [])
        if not _land_frames:
            return
        _frame  = _land_frames[min(self._mjf_land_frame_idx, len(_land_frames) - 1)]
        _sw     = 32 * RENDER_SCALE
        _sh     = 32 * RENDER_SCALE
        _scaled = pygame.transform.scale(_frame, (_sw, _sh))
        _cx = int(self.player.x * RENDER_SCALE - self.camera.x)
        _cy = int(self.player.y * RENDER_SCALE - self.camera.y)
        _sx = _cx - _sw // 2
        _sy = _cy - _sh // 2

        # ── Shadow — use the standard layer_manager shadow (same as all entities) ──
        self.layer_manager._draw_shadow(self.logical_surface, self.player, self.camera)
        # ── End shadow ────────────────────────────────────────────────────────

        self.logical_surface.blit(_scaled, (_sx, _sy))

    def _update_map_flying(self, dt):
        """Tick the world-map flying sequence.

        State machine:
          'pending_fade_in' — alpha still rising; wait for full black.
          'fade_in'         — alpha counting back to 0 while flying scene is shown.
          'flying'          — fully visible; arrow keys move the flying sprite.
        """
        if self._mjf_state == 'pending_fade_in':
            # Keep ramping alpha until black, then flip to fade-in.
            self._mjf_alpha = min(255.0, self._mjf_alpha + self._MJF_SPEED * dt)
            if self._mjf_alpha >= 255.0:
                self._mjf_state  = 'fade_in'
                self._mjf_active = False

        elif self._mjf_state == 'fade_in':
            self._mjf_alpha = max(0.0, self._mjf_alpha - self._MJF_FADE_IN_SPEED * dt)
            if self._mjf_alpha <= 0.0:
                self._mjf_state = 'flying'
                self.camera.locked = False   # safe to release now

        elif self._mjf_state == 'flying':
            keys = pygame.key.get_pressed()
            spd  = self._MJF_FLY_SPEED * dt

            left  = keys[pygame.K_LEFT]
            right = keys[pygame.K_RIGHT]
            up    = keys[pygame.K_UP]
            down  = keys[pygame.K_DOWN]

            # Direction determines sprite row:
            # up/down = forward/backward, left/right = banking turn.
            # Left/right alone inherits the last vertical direction:
            #   previously going up   -> up_left / up_right
            #   previously going down -> down_left / down_right
            _cur_dir = getattr(self, '_mjf_fly_direction', 'up')
            _going_down = _cur_dir in ('down', 'down_left', 'down_right')
            if up and left:
                new_dir = 'up_left'
            elif up and right:
                new_dir = 'up_right'
            elif down and left:
                new_dir = 'down_left'
            elif down and right:
                new_dir = 'down_right'
            elif up:
                new_dir = 'up'
            elif down:
                new_dir = 'down'
            elif left:
                new_dir = 'down_left' if _going_down else 'up_left'
            elif right:
                new_dir = 'down_right' if _going_down else 'up_right'
            else:
                new_dir = _cur_dir

            # Screen-space sprite stays fixed — the map plane rotates/moves under it.
            # (No changes to _mjf_fly_x / _mjf_fly_y here.)

            # Left/right: rotate the camera angle so the plane spins under the player.
            # Up/down: move forward/back along the current facing direction.
            # At high altitude the viewport covers far more of the map, so the same
            # angle increment looks much faster.  Scale speed down with altitude so
            # the visual rotation rate feels consistent regardless of height.
            _alt = getattr(self, '_mjf_altitude', 0.5)
            ROTATE_SPD = 1.8 * dt * (1.0 - 0.75 * _alt)
            MAP_SPD    = self._MJF_FLY_SPEED * 1.8 * dt

            if up or down:
                self._mjf_moving_backward = down and not up

            if left or right:
                import math as _math
                d_angle = ROTATE_SPD * (-1 if left else 1)
                old_angle = self._mjf_cam_angle

                # Use the same constants as _draw_world_map_flying_scene so the
                # pivot lands on the exact world-map point under the shadow.
                _sh        = SCREEN_HEIGHT
                _sky_h     = int(_sh * 0.35)                          # fixed sky height
                _base_f    = getattr(self, '_MJF_FOCAL', SCREEN_WIDTH // 8)
                _FOCAL     = int(_base_f * (0.3 + 1.9 * _alt))        # matches draw method
                _PROJ_HOR  = int(_sh * 0.20)                           # PROJECTION_HORIZON
                _vground_h = _sh - _PROJ_HOR
                _HOR_OFF   = 2                                          # HORIZON_OFFSET
                _hor_y     = int(_sh * (0.30 - 0.20 * _alt))           # horizon_y from draw

                # Pivot = shadow screen position — must match shadow_ground_y in draw code
                _pivot_screen_y = int(_sky_h + (_sh - _sky_h) * 0.32)

                # Curvature-corrected depth: the ground pixel visible at
                # shadow_ground_y comes from a deeper render row because the
                # curvature loop shifts rows upward.  Invert that shift
                # iteratively (4 steps converge well within 1 px).
                _RENDER_DIV = 2
                _curve_px   = getattr(self, '_MJF_CURVATURE', 14)
                _eff_g      = max(_sh - _hor_y - _HOR_OFF, 1)
                _T          = float(_pivot_screen_y - _hor_y - _HOR_OFF)
                _sc         = _T
                for _ in range(4):
                    _tc  = 1.0 - _sc / max(1.0, float(_eff_g - 1))
                    _sc  = _T + float(int(_tc * _tc * _curve_px))
                _sc     = max(0.0, min(float(_eff_g - 1), _sc))
                _rows_f = (int(_sc) // _RENDER_DIV + 1) * _RENDER_DIV + _HOR_OFF
                _depth  = _FOCAL * _vground_h / max(_rows_f, 1)

                _pivot_x = self._mjf_cam_x + _math.sin(old_angle) * _depth
                _pivot_y = self._mjf_cam_y - _math.cos(old_angle) * _depth

                self._mjf_cam_angle += d_angle
                _na = self._mjf_cam_angle
                self._mjf_cam_x = _pivot_x - _math.sin(_na) * _depth
                self._mjf_cam_y = _pivot_y + _math.cos(_na) * _depth

                _tex = getattr(self, '_world_map_texture', None)
                if _tex:
                    self._mjf_cam_x %= _tex.get_width()
                    self._mjf_cam_y %= _tex.get_height()

            # E = rise, Q = descend.
            rise    = keys[pygame.K_e]
            descend = keys[pygame.K_q]
            ALT_SPD = 0.8 * dt
            if rise or descend:
                _old_alt = self._mjf_altitude
                _new_alt = (min(1.0, _old_alt + ALT_SPD) if rise
                            else max(0.0, _old_alt - ALT_SPD))
                if _new_alt != _old_alt:
                    # ── Zoom-into-shadow anchor (Buu's Fury style) ────────────
                    # Keep the world-map point currently under the shadow fixed
                    # while FOCAL / horizon_y change with altitude, so the camera
                    # zooms in/out on that exact spot instead of drifting.
                    import math as _math
                    _sw, _sh   = SCREEN_WIDTH, SCREEN_HEIGHT
                    _base_f    = getattr(self, '_MJF_FOCAL', _sw // 8)
                    _PROJ_HOR  = int(_sh * 0.20)   # must match _draw_world_map_flying_scene
                    _vground_h = _sh - _PROJ_HOR
                    _HOR_OFF   = 2                  # HORIZON_OFFSET constant
                    _sky_h     = int(_sh * 0.35)    # fixed sky height
                    # Shadow is always drawn at the same row — must match shadow_ground_y in draw code.
                    _shad_gy   = int(_sky_h + (_sh - _sky_h) * 0.32)

                    # --- old projection: world point under the shadow ---
                    _old_FOCAL = int(_base_f * (0.3 + 1.9 * _old_alt))
                    _old_hor_y = int(_sh * (0.30 - 0.20 * _old_alt))
                    _old_row   = max(_shad_gy - _old_hor_y, 1)
                    _old_depth = _old_FOCAL * _vground_h / (_old_row + _HOR_OFF)

                    _angle  = self._mjf_cam_angle
                    # Shadow is at screen-centre (cols = 0), so the world point is:
                    _world_x = self._mjf_cam_x + _math.sin(_angle) * _old_depth
                    _world_y = self._mjf_cam_y - _math.cos(_angle) * _old_depth

                    # --- apply altitude ---
                    self._mjf_altitude = _new_alt

                    # --- new projection: re-derive cam so same world point stays ---
                    _new_FOCAL = int(_base_f * (0.3 + 1.9 * _new_alt))
                    _new_hor_y = int(_sh * (0.30 - 0.20 * _new_alt))
                    _new_row   = max(_shad_gy - _new_hor_y, 1)
                    _new_depth = _new_FOCAL * _vground_h / (_new_row + _HOR_OFF)

                    _tex = getattr(self, '_world_map_texture', None)
                    if _tex:
                        _tw, _th = _tex.get_width(), _tex.get_height()
                        self._mjf_cam_x = (_world_x - _math.sin(_angle) * _new_depth) % _tw
                        self._mjf_cam_y = (_world_y + _math.cos(_angle) * _new_depth) % _th
                    else:
                        self._mjf_cam_x = _world_x - _math.sin(_angle) * _new_depth
                        self._mjf_cam_y = _world_y + _math.cos(_angle) * _new_depth
                    # ── End zoom anchor ───────────────────────────────────────

            tex = getattr(self, '_world_map_texture', None)
            if tex:
                import math as _math
                tw, th = tex.get_width(), tex.get_height()
                dx = _math.sin(self._mjf_cam_angle) * MAP_SPD
                dy = -_math.cos(self._mjf_cam_angle) * MAP_SPD
                if up:
                    self._mjf_cam_x = (self._mjf_cam_x + dx) % tw
                    self._mjf_cam_y = (self._mjf_cam_y + dy) % th
                if down:
                    self._mjf_cam_x = (self._mjf_cam_x - dx) % tw
                    self._mjf_cam_y = (self._mjf_cam_y - dy) % th

            # Switch sprite row when direction changes.
            if new_dir != getattr(self, '_mjf_fly_direction', None):
                self._mjf_fly_direction = new_dir
                frames_by_dir = getattr(self, '_mjf_flying_frames_by_dir', {})
                if new_dir in frames_by_dir:
                    self._mjf_flying_frames      = frames_by_dir[new_dir]
                    self._mjf_flying_frame_idx   = 0
                    self._mjf_flying_frame_timer = 0.0

            # (Sprite is fixed; no screen-bounds clamping needed.)

            # ── Landing detection — collision between shadow and icon ─────────
            # Collision is checked in screen space: the player's shadow rect
            # overlaps the icon's projected ground-contact point. Only fires at
            # or near minimum altitude so the player has to descend first.
            # Landing detection: axis-aligned rectangle in world (texture-pixel) space.
            # The icon is the centre. _LAND_W is the half-width (left/right reach),
            # _LAND_H is the half-height (forward/backward reach).
            # Both are in texture pixels; 1 map tile = texture_width / 362 px.
            # _LAND_PAD expands the icon's screen rect on all sides (pixels).
            # Increase to make the trigger zone larger, decrease to tighten it.
            # 3-D proximity check: XY distance on the map plane + altitude match.
            # _LAND_RADIUS   : trigger distance in texture pixels (XY plane).
            # _LAND_ALT_RANGE: how close in altitude (0.0-1.0) the player must
            #                  be to the icon's altitude. Icons default to 0.0
            #                  (ground); set 'altitude' on an icon dict to place
            #                  it at a different height in future.
            _LAND_RADIUS    = 30
            _LAND_ALT_RANGE = 0.15

            _pending_icons = getattr(self, '_wm_last_icon_screen_positions', [])
            self._mjf_nearby_loc_name = ''   # reset each tick; set below if close
            if _pending_icons:
                import math as _math
                _tex = getattr(self, '_world_map_texture', None)
                _tw  = _tex.get_width()  if _tex else 1
                _th  = _tex.get_height() if _tex else 1
                _alt    = self._mjf_altitude
                _base_f = getattr(self, '_MJF_FOCAL', SCREEN_WIDTH // 8)
                _focal  = int(_base_f * (0.3 + 1.9 * _alt))
                _vgh    = SCREEN_HEIGHT - int(SCREEN_HEIGHT * 0.20)
                _hor_y  = int(SCREEN_HEIGHT * (0.30 - 0.20 * _alt))
                _sky_h2 = int(SCREEN_HEIGHT * 0.35)
                # Must match shadow_ground_y formula in draw: sky_h + (sh-sky_h)*0.32
                _sky_h2_draw = int(SCREEN_HEIGHT * 0.35)
                _shad_y = int(_sky_h2_draw + (SCREEN_HEIGHT - _sky_h2_draw) * 0.32)
                _row    = max(_shad_y - _hor_y, 1)
                _pdepth = _focal * _vgh / (_row + 2)
                _angle  = self._mjf_cam_angle
                _px = (self._mjf_cam_x + _math.sin(_angle) * _pdepth) % _tw
                _py = (self._mjf_cam_y - _math.cos(_angle) * _pdepth) % _th
                for _li_wx, _li_wy, _li_loc in _pending_icons:
                    # Altitude check: player must be within _LAND_ALT_RANGE of
                    # the icon's altitude. Derive from the editor 'height' field
                    # (0-500 = ground to high) mapped to the 0.0-1.0 altitude range,
                    # falling back to an explicit 'altitude' key if set directly.
                    _icon_alt = _li_loc.get('altitude',
                                            max(0.0, _li_loc.get('height', 0) / 2000.0))
                    if abs(_alt - _icon_alt) > _LAND_ALT_RANGE:
                        continue
                    _ddx = (_li_wx - _px) % _tw
                    if _ddx > _tw * 0.5: _ddx -= _tw
                    _ddy = (_li_wy - _py) % _th
                    if _ddy > _th * 0.5: _ddy -= _th
                    _dist = _math.sqrt(_ddx * _ddx + _ddy * _ddy)
                    if _dist <= _LAND_RADIUS * 3:
                        self._mjf_nearby_loc_name = _li_loc.get('name', _li_loc.get('room', ''))
                    if _dist <= _LAND_RADIUS:
                        # Player loses control; fade to black then transition.
                        self._mjf_landing_room = _li_loc['room']
                        self._mjf_landing_loc  = _li_loc
                        # Remember which entity (if any) brought the player here.
                        # This persists in the room so the return WMO jump can
                        # position the camera at the entity's new position.
                        # Clear it explicitly for non-entity pins so stale names
                        # from a previous visit don't bleed through.
                        self._mjf_last_entry_entity = _li_loc.get('_entity_name', '')
                        self._mjf_alpha        = 0.0
                        self._mjf_state        = 'landing_fade_out'
                        # Hide the HUD immediately so it is not visible during
                        # the landing sequence.  It will slide back in once the
                        # player has fully descended and the fade clears.
                        _scaled_frame_h = int(self.sprite_hud.config['frame']['h'] * self.sprite_hud.scale)
                        self.sprite_hud.hud_offset_y   = -(self.sprite_hud.hud_y + _scaled_frame_h + 10)
                        self.sprite_hud._hud_slide_out = False
                        self.sprite_hud._hud_slide_in  = False
                        break

        elif self._mjf_state == 'landing_fade_out':
            # Fade to black over 1.5 s — mirrors the rising fade-out speed exactly.
            _LAND_FO_SPD = 255.0 / 1.5
            self._mjf_alpha = min(255.0, self._mjf_alpha + _LAND_FO_SPD * dt)
            if self._mjf_alpha >= 255.0:
                # Screen is fully black — perform the room transition now.
                _target_room_name = getattr(self, '_mjf_landing_room', '')
                _target_room      = self.room_manager.get_room_by_name(_target_room_name)
                if _target_room:
                    self.room_manager.current_room = _target_room
                    self.current_room              = _target_room
                    if self.is_test_mode:
                        self._load_room_objects_as_copies(_target_room)
                    else:
                        self._load_room_objects(_target_room)
                    # Place player at the WorldMapObject in the target room.
                    # If the landing was triggered by a named entity (not a fixed
                    # location pin), prefer the WMO whose entity_name matches.
                    # Fall back to the first generic 'world_map' WMO, then room centre.
                    _spawn_x = _target_room.width  / 2
                    _spawn_y = _target_room.height / 2
                    _landing_loc  = getattr(self, '_mjf_landing_loc', {})
                    _landing_ent  = _landing_loc.get('_entity_name', '') if _landing_loc else ''
                    _best_wmo     = None
                    _fallback_wmo = None
                    for _wmo in self.world_map_objects:
                        if getattr(_wmo, 'variant', '') != 'world_map':
                            continue
                        _ename = getattr(_wmo, 'entity_name', '')
                        if _landing_ent and _ename == _landing_ent:
                            _best_wmo = _wmo
                            break                   # exact match — stop searching
                        if _fallback_wmo is None:
                            _fallback_wmo = _wmo    # remember first generic WMO
                    _chosen_wmo = _best_wmo or _fallback_wmo
                    if _chosen_wmo:
                        _spawn_x = _chosen_wmo.x
                        # player.y is the CENTRE of the sprite frame, but the
                        # visible character sits in the upper half of the frame.
                        # Offsetting by half the player height aligns the visual
                        # character with the centre of the WMO.
                        _spawn_y = _chosen_wmo.y - self.player.height // 2
                    self.player.x = _spawn_x
                    self.player.y = _spawn_y
                    # Set camera centred on spawn, clamped to room bounds.
                    self.camera.x = max(0, int(_spawn_x * RENDER_SCALE) - self.camera.screen_width  // 2)
                    self.camera.y = max(0, int(_spawn_y * RENDER_SCALE) - self.camera.screen_height // 2)
                    self.camera.x = min(self.camera.x, _target_room.width  * RENDER_SCALE - SCREEN_WIDTH)
                    self.camera.y = min(self.camera.y, _target_room.height * RENDER_SCALE - SCREEN_HEIGHT)
                    self.camera.locked = False
                    self.player.is_map_jumping  = False
                    self.player.map_jump_moving = False
                    self.player.on_map_jump_exit = None
                    # Start the player above the top of the screen so they
                    # descend into the spawn point — the opposite of the ascent.
                    _above_screen_y = (self.camera.y - 32 * 2) / RENDER_SCALE
                    self.player.y              = _above_screen_y
                    self._mjf_land_start_y     = _above_screen_y
                    self._mjf_land_target_y    = _spawn_y
                    self._mjf_land_speed       = 120.0   # world-units / sec — mirrors ascent feel
                    self._mjf_landing_done     = False
                    self._load_map_land_sprite()
                    self.mission_manager.on_room_entered(_target_room_name)
                    self._mjf_alpha = 255.0   # start fully black; fade_in counts down to 0
                    self._mjf_state = 'landing_fade_in'

        elif self._mjf_state == 'landing_fade_in':
            _LAND_FI_SPD = 255.0 / 1.5
            self._mjf_alpha = max(0.0, self._mjf_alpha - _LAND_FI_SPD * dt)
            self.player.is_map_jumping  = False
            self.player.map_jump_moving = False
            # Descend the player toward the spawn point.
            if not getattr(self, '_mjf_landing_done', False):
                self.player.y += getattr(self, '_mjf_land_speed', 120.0) * dt
                if self.player.y >= getattr(self, '_mjf_land_target_y', self.player.y):
                    self.player.y          = self._mjf_land_target_y
                    self._mjf_landing_done = True
            # Advance the land-sprite animation for the full landing duration.
            # Uses while/-= (mirrors the ascending sprite system) so variable-dt
            # frames never discard overflow and the loop plays smoothly to the end.
            # Frame schedule: frame 0 briefly at the start, frame 1 for the
            # bulk of the descent, last frame briefly just before touchdown.
            # _FRAME0_DUR    — how long (seconds) the first frame is held.
            # _FRAME_LAST_DIST — switch to the last frame when this many
            #                    world-units remain (= land_speed × 0.20 s).
            _land_frames = getattr(self, '_mjf_land_frames', [])
            if _land_frames:
                _n = len(_land_frames)
                self._mjf_land_elapsed = getattr(self, '_mjf_land_elapsed', 0.0) + dt
                _FRAME0_DUR      = 0.15
                _FRAME_LAST_DIST = getattr(self, '_mjf_land_speed', 120.0) * 0.20
                _dist_left       = max(0.0, getattr(self, '_mjf_land_target_y', self.player.y) - self.player.y)
                _is_done         = getattr(self, '_mjf_landing_done', False)
                if _n == 1:
                    self._mjf_land_frame_idx = 0
                elif _n == 2:
                    # Two-frame sheet: first briefly, second for the rest.
                    self._mjf_land_frame_idx = 0 if self._mjf_land_elapsed < _FRAME0_DUR else 1
                else:
                    # Three-or-more-frame sheet:
                    #   brief frame 0 → long frame 1 → brief last frame
                    if self._mjf_land_elapsed < _FRAME0_DUR:
                        self._mjf_land_frame_idx = 0
                    elif _is_done or _dist_left <= _FRAME_LAST_DIST:
                        self._mjf_land_frame_idx = _n - 1
                    else:
                        self._mjf_land_frame_idx = 1
            if self._mjf_alpha <= 0.0:
                self.player.y = getattr(self, '_mjf_land_target_y', self.player.y)
                self.player.direction = 'down'
                try:
                    self.player.sprite.set_animation('idle', 'down')
                    self.player.current_animation_state = 'idle'
                except Exception:
                    pass
                self._mjf_state = None
                # Landing sequence fully finished — slide the HUD back down
                # from its hidden (off-screen) position into view.
                _scaled_frame_h = int(self.sprite_hud.config['frame']['h'] * self.sprite_hud.scale)
                self.sprite_hud.hud_offset_y   = -(self.sprite_hud.hud_y + _scaled_frame_h + 10)
                self.sprite_hud._hud_slide_out = False
                self.sprite_hud._hud_slide_in  = True

        # Advance the flying sprite animation (looping) while on the world map.
        if self._mjf_flying_frames:
            self._mjf_flying_frame_timer += dt
            if self._mjf_flying_frame_timer >= self._MJF_FLY_FRAME_DUR:
                self._mjf_flying_frame_timer = 0.0
                self._mjf_flying_frame_idx = (
                    (self._mjf_flying_frame_idx + 1) % len(self._mjf_flying_frames)
                )

    def _build_world_map_surface(self, map_name: str, frame_idx: int = 0):
        """Render the tile-map JSON produced by the world map editor into a single
        pygame Surface that the Mode7 renderer can use as its ground texture.

        Returns None if the file is missing or contains no tiles, so the caller
        can fall back to the static PNG.
        """
        import json as _json
        import os as _os

        path = _os.path.join('assets', 'world_maps', f'{map_name}.json')
        try:
            with open(path) as f:
                data = _json.load(f)
        except Exception as e:
            print(f'[world_map] could not open {path}: {e}')
            return None

        # Support both multi-frame and legacy 'tiles' formats (mirrors world_map.py).
        if 'frames' in data and data['frames']:
            frame_idx = max(0, min(frame_idx, len(data['frames']) - 1))
            tiles_list = data['frames'][frame_idx]
        else:
            tiles_list = data.get('tiles', [])

        if not tiles_list:
            print(f'[world_map] {map_name}.json has no tiles — falling back to PNG')
            return None

        # Constants must match world_map.py / world_map_editor.py.
        NATIVE = 8          # source tile size in pixels
        MAP_W  = 362       # map width  in tiles
        MAP_H  = 263       # map height in tiles

        # We render at native resolution (1 pixel per source pixel) then let the
        # existing 2× upscale path in the caller handle magnification.
        surf_w = MAP_W * NATIVE
        surf_h = MAP_H * NATIVE
        surface = pygame.Surface((surf_w, surf_h))
        surface.fill((0, 0, 0))

        # Load each referenced tileset once.
        tileset_dir = _os.path.join('assets', 'tilesets', 'world_map')
        tilesets: dict = {}

        for td in tiles_list:
            ts_name = td['ts']
            if ts_name not in tilesets:
                ts_path = _os.path.join(tileset_dir, ts_name)
                if not ts_path.lower().endswith('.png'):
                    ts_path += '.png'
                try:
                    tilesets[ts_name] = pygame.image.load(ts_path).convert()
                except Exception as e:
                    print(f'[world_map] could not load tileset {ts_name}: {e}')
                    tilesets[ts_name] = None

        # Blit each tile onto the surface.
        for td in tiles_list:
            ts_img = tilesets.get(td['ts'])
            if ts_img is None:
                continue
            src_rect = pygame.Rect(td['tx'] * NATIVE, td['ty'] * NATIVE, NATIVE, NATIVE)
            dst_x    = td['x'] * NATIVE
            dst_y    = td['y'] * NATIVE
            surface.blit(ts_img, (dst_x, dst_y), src_rect)

        print(f'[world_map] built surface from {map_name}.json: {surf_w}x{surf_h}, {len(tiles_list)} tiles')
        # Return at 2× scale to match the legacy PNG pipeline.
        try:
            _png_ref = pygame.image.load('assets/map/world_map.png')
            target_w = _png_ref.get_width() * 2  # same 2× upscale the fallback path uses
            target_h = _png_ref.get_height() * 2
            del _png_ref
            print(f'[world_map] scaling tile surface to match PNG: {target_w}x{target_h}')
        except Exception:
            # PNG missing — use a sensible default (~2px per tile)
            target_w = MAP_W * 2
            target_h = MAP_H * 2
            print(f'[world_map] PNG not found, defaulting to {target_w}x{target_h}')
        return pygame.transform.scale(surface, (surf_w * 2, surf_h * 2))

    def _draw_world_map_flying_scene(self):
        """Draw the world-map flying view.

        Renders the world map (assets/map/world_map.png) as a perspective
        plane below the player — like a 3-D ground surface seen from the air.
        The horizon divides the screen: sky above, map plane below.

        Technique: scanline-by-scanline perspective projection.  Each screen
        row samples a horizontal strip from the map texture.  Rows near the
        horizon are far from the camera → wide strip (many texture pixels
        compressed into one screen row).  Rows at the bottom are close → narrow
        strip (few texture pixels, zoomed in).  Player position scrolls the
        texture so flying around actually moves across the map.

        Called from draw() whenever _mjf_state is 'fade_in' or 'flying'.
        """
        sw, sh = SCREEN_WIDTH, SCREEN_HEIGHT
        altitude = getattr(self, '_mjf_altitude', 0.5)

        # Fixed sky size (never changes)
        sky_h = int(sh * 0.35)

        # Draw the ground starting exactly below the sky
        horizon_y = int(sh * (0.30 - 0.20 * altitude))
        ground_h = sh - horizon_y

        # Hidden projection horizon:
        # smaller values = stronger angle and less visible repetition
        PROJECTION_HORIZON = int(sh * 0.20)

        # ── Sky (skybox image above the horizon) ──────────────────────────
        import math as _math
        import numpy as _np

        # ── Lazy-load map texture ──────────────────────────────────────────
        if not hasattr(self, '_world_map_texture'):
            active_map = getattr(self, '_active_world_map_name', '')
            self._wm_tex_frames: list = []   # one Surface per editor frame
            self._wm_tex_frame_idx   = 0
            self._wm_tex_frame_timer = 0.0
            self._WM_TEX_FRAME_DUR   = 1  # seconds per map frame

            if active_map:
                # Count frames in the JSON first.
                import json as _jf, os as _osf
                _jpath = _osf.path.join('assets', 'world_maps', f'{active_map}.json')
                try:
                    with open(_jpath) as _jf_:
                        _jdata = _jf.load(_jf_)
                    _n_frames = len(_jdata.get('frames', [])) or 1
                except Exception:
                    _n_frames = 1

                for _fi in range(_n_frames):
                    _raw = self._build_world_map_surface(active_map, _fi)
                    if _raw is not None:
                        self._wm_tex_frames.append(_raw)

            if not self._wm_tex_frames:
                # Fall back to the legacy static PNG.
                try:
                    _raw = pygame.image.load('assets/map/world_map.png').convert()
                    _raw = pygame.transform.scale(
                        _raw, (_raw.get_width() * 2, _raw.get_height() * 2)
                    )
                    print(f'[world_map] loaded fallback PNG, 2x upscaled to {_raw.get_width()}x{_raw.get_height()}')
                    self._wm_tex_frames.append(_raw)
                except Exception as e:
                    print(f'[world_map] could not load map texture: {e}')

            # Pre-bake a numpy array for EVERY frame now, at load time.
            # Frame advance then becomes a free pointer swap instead of an
            # expensive surfarray.array3d() call mid-frame.
            import numpy as _np_load
            self._wm_tex_arr_frames: list = []
            for _surf in self._wm_tex_frames:
                try:
                    self._wm_tex_arr_frames.append(
                        _np_load.array(pygame.surfarray.array3d(_surf), dtype=_np_load.uint8)
                    )
                except Exception:
                    self._wm_tex_arr_frames.append(None)

            raw = self._wm_tex_frames[0] if self._wm_tex_frames else None
            self._world_map_texture = raw
            self._world_map_tex_arr = self._wm_tex_arr_frames[0] if self._wm_tex_arr_frames else None
            if raw:
                print(f'[world_map] texture ready: {raw.get_width()}x{raw.get_height()}, '
                      f'{len(self._wm_tex_arr_frames)} frame(s) pre-baked')

        # ── Advance map frame animation ────────────────────────────────────
        _tex_frames = getattr(self, '_wm_tex_frames', [])
        if len(_tex_frames) > 1:
            self._wm_tex_frame_timer += self.dt
            if self._wm_tex_frame_timer >= self._WM_TEX_FRAME_DUR:
                self._wm_tex_frame_timer -= self._WM_TEX_FRAME_DUR
                self._wm_tex_frame_idx = (self._wm_tex_frame_idx + 1) % len(_tex_frames)
                # O(1) pointer swap — numpy arrays were pre-baked at load time.
                self._world_map_texture = _tex_frames[self._wm_tex_frame_idx]
                _arr_frames = getattr(self, '_wm_tex_arr_frames', [])
                self._world_map_tex_arr = (
                    _arr_frames[self._wm_tex_frame_idx]
                    if self._wm_tex_frame_idx < len(_arr_frames) else None
                )

        texture = self._world_map_texture
        tex_arr = getattr(self, '_world_map_tex_arr', None)

        # ── Pin-correction: place the camera so the player shadow lands on the
        # location pin for the room we came from.  Runs on every entry (not just
        # first) via the _mjf_needs_pin_correction flag set in _on_exit.
        # This block sits AFTER the texture is guaranteed to be loaded (so
        # texture dimensions are available) and BEFORE the perspective math below
        # (so the corrected cam_x/cam_y are used on this very frame).
        if getattr(self, '_mjf_needs_pin_correction', False) and texture:
            import math as _pin_math
            _origin_room   = getattr(self, '_mjf_origin_room',   '')
            _origin_entity = getattr(self, '_mjf_origin_entity', '')
            print(f'[pin_correction] origin_room={_origin_room!r}  origin_entity={_origin_entity!r}  anim_t={getattr(self,"_wm_entity_anim_t",None):.2f}')
            _active_map    = getattr(self, '_active_world_map_name', '')
            _locs = getattr(self, '_wm_locations', None)
            if _locs is None and _active_map:
                import json as _jl, os as _osl
                try:
                    with open(_osl.path.join('assets', 'world_maps',
                                             f'{_active_map}.json')) as _fl:
                        _locs = _jl.load(_fl).get('locations', [])
                    self._wm_locations = _locs
                except Exception:
                    _locs = []
            _ppt_x = texture.get_width()  / 362
            _ppt_y = texture.get_height() / 263

            # Compute depth/angle constants used for the spawn-margin offset
            _alt     = getattr(self, '_mjf_altitude', 0.5)
            _base_f  = getattr(self, '_MJF_FOCAL', sw // 8)
            _FOCAL_p = int(_base_f * (0.3 + 1.9 * _alt))
            _PROJ_p  = int(sh * 0.20)
            _vgh_p   = sh - _PROJ_p
            _sky_hp  = int(sh * 0.35)
            _hor_yp  = int(sh * (0.30 - 0.20 * _alt))
            _shad_yp = int(_sky_hp + (sh - _sky_hp) * 0.32)
            _row_p   = max(_shad_yp - _hor_yp + 2, 1)
            _dep_p   = _FOCAL_p * _vgh_p / _row_p
            _ang     = self._mjf_cam_angle
            _SPAWN_MARGIN = 40

            _pin_found = False

            # Priority 1: if we came from an entity room, use the entity's
            # current animated tile position — it keeps moving while in the room.
            if _origin_entity:
                _etile = self._wm_entity_tile_pos(_origin_entity)
                if _etile is not None:
                    _pin_x = _etile[0] * _ppt_x
                    _pin_y = _etile[1] * _ppt_y
                    self._mjf_cam_x = (_pin_x - _pin_math.sin(_ang) * (_dep_p + _SPAWN_MARGIN)) % texture.get_width()
                    self._mjf_cam_y = (_pin_y + _pin_math.cos(_ang) * (_dep_p + _SPAWN_MARGIN)) % texture.get_height()
                    _pin_found = True

            # Priority 2: fall back to matching a location pin by room name
            if not _pin_found:
                for _loc in (_locs or []):
                    if _loc.get('room', '') == _origin_room:
                        _pin_x = _loc['x'] * _ppt_x
                        _pin_y = _loc['y'] * _ppt_y
                        self._mjf_cam_x = (_pin_x - _pin_math.sin(_ang) * (_dep_p + _SPAWN_MARGIN)) % texture.get_width()
                        self._mjf_cam_y = (_pin_y + _pin_math.cos(_ang) * (_dep_p + _SPAWN_MARGIN)) % texture.get_height()
                        break

            self._mjf_needs_pin_correction = False
            self._mjf_origin_room          = ''
            self._mjf_origin_entity        = ''

        base_focal = getattr(self, '_MJF_FOCAL', sw // 8)

        # In your renderer:
        #   larger FOCAL  = world appears farther away
        #   smaller FOCAL = world appears closer to the player
        #
        # Therefore:
        #   altitude = 0.0 (low / descended)  -> small FOCAL  -> close
        #   altitude = 1.0 (high / ascended)  -> large FOCAL  -> far
        FOCAL = int(base_focal * (0.3 + 1.9 * altitude))
        RENDER_DIV = 2

        HORIZON_OFFSET = 2
        if texture:
            tw, th = texture.get_width(), texture.get_height()
        else:
            tw = th = 1

        # ── Sky — completely unaffected by altitude ────────────────────────
        if not hasattr(self, '_world_map_sky'):
            try:
                self._world_map_sky = pygame.image.load(
                    'assets/map/world_map_sky.png'
                ).convert()
            except Exception as e:
                print(f'[world_map] could not load sky texture: {e}')
                self._world_map_sky = None

        # Sky is scaled once to sky_horizon_y (fixed) and cached — rescale only
        # if the screen size changes (e.g. window resize).
        if self._world_map_sky:
            _sky_cache_key = (sw, sky_h)
            if (not hasattr(self, '_mjf_scaled_sky')
                    or self._mjf_scaled_sky_key != _sky_cache_key):
                self._mjf_scaled_sky     = pygame.transform.scale(self._world_map_sky, _sky_cache_key)
                self._mjf_scaled_sky_key = _sky_cache_key
            scaled_sky = self._mjf_scaled_sky
            sky_off_x = -int((self._mjf_cam_angle * 5.0 % (_math.pi * 2)) / (_math.pi * 2) * sw) % sw
            self.logical_surface.blit(scaled_sky, (sky_off_x - sw, 0))
            self.logical_surface.blit(scaled_sky, (sky_off_x,      0))
        else:
            self.logical_surface.fill((8, 10, 35))
            for i in range(80):
                sx = (i * 137 + 41) % sw
                sy = (i * 97  + 13) % horizon_y
                br = 120 + (i * 53) % 136
                pygame.draw.line(
                    self.logical_surface,
                    (140, 200, 220),
                    (0, sky_h - 1),
                    (sw - 1, sky_h - 1),
                    2
                )

        # ── Perspective plane (Mode 7, fully vectorised) ──────────────────────
        if texture and tex_arr is not None:
            cam_x = self._mjf_cam_x
            cam_y = self._mjf_cam_y
            angle = self._mjf_cam_angle

            cos_a = _math.cos(angle)
            sin_a = _math.sin(angle)

            render_w         = max(sw // RENDER_DIV, 1)
            # Project the ground using the full screen height so the perspective
            # converges above the visible sky. This makes the map continue "behind"
            # the sky instead of stopping at the sky/ground boundary.
            projection_ground_h = sh

            # Visible ground area still begins at horizon_y.
            effective_ground = max(ground_h - HORIZON_OFFSET, 1)

            # Render enough scanlines for the visible ground only.
            render_h = max(effective_ground // RENDER_DIV, 1)

            # Reuse offscreen surface across frames (alloc only on first call / resize).
            if (not hasattr(self, '_mjf_ground_surf')
                    or self._mjf_ground_surf.get_size() != (render_w, render_h)):
                self._mjf_ground_surf = pygame.Surface((render_w, render_h))

            # Row indices — HORIZON_OFFSET guarantees depth ≤ tw for every row,
            # so the texture never repeats within the visible ground plane.
            rows_r = _np.arange(1, render_h + 1, dtype=_np.float32)   # (render_h,)
            rows_f = rows_r * RENDER_DIV + HORIZON_OFFSET              # (render_h,)

            virtual_ground_h = sh - PROJECTION_HORIZON
            depth = (FOCAL * virtual_ground_h / rows_f).astype(_np.float32)

            fwd_x = ( sin_a * depth).astype(_np.float32)
            fwd_y = (-cos_a * depth).astype(_np.float32)
            rgt_x = ( cos_a * depth).astype(_np.float32)
            rgt_y = ( sin_a * depth).astype(_np.float32)

            # Cache the column coordinate array — render_w is constant per screen size.
            if (not hasattr(self, '_mjf_cols_cache')
                    or len(self._mjf_cols_cache) != render_w):
                self._mjf_cols_cache = _np.linspace(-0.5, 0.5, render_w, dtype=_np.float32)
            cols = self._mjf_cols_cache

            tx = (cam_x + fwd_x[:, None] + cols[None, :] * rgt_x[:, None]).astype(_np.int32) % tw
            ty = (cam_y + fwd_y[:, None] + cols[None, :] * rgt_y[:, None]).astype(_np.int32) % th

            pixels = tex_arr[tx, ty]   # (render_h, render_w, 3)

            pygame.surfarray.blit_array(self._mjf_ground_surf, pixels.transpose(1, 0, 2))

            scaled_ground = pygame.transform.scale(self._mjf_ground_surf, (sw, effective_ground))

            # ── Ground curvature ──────────────────────────────────────────────
            # Rows near the horizon are shifted upward by a quadratic amount that
            # peaks at the horizon and falls to zero at the bottom of the screen.
            # The clip rect prevents upward-shifted rows from bleeding into the sky.
            #
            # Vectorised band approach: instead of one blit per pixel row (~300+
            # calls), we compute all row shifts with numpy and group consecutive
            # rows that share the same integer shift into a single subsurface blit.
            # With _MJF_CURVATURE=14 there are at most 15 distinct shift values,
            # collapsing hundreds of blits into ≤15.
            _curve_px = getattr(self, '_MJF_CURVATURE', 14)
            self.logical_surface.set_clip(pygame.Rect(0, sky_h, sw, sh - sky_h))

            _n    = effective_ground
            _denom = max(1, _n - 1)
            _t_arr  = 1.0 - _np.arange(_n, dtype=_np.float32) / _denom
            _dy_arr = (_t_arr * _t_arr * _curve_px).astype(_np.int32)

            # Band boundaries: indices where the shift value changes.
            _change_idx = (_np.where(_np.diff(_dy_arr) != 0)[0] + 1).tolist()
            _starts = [0] + _change_idx
            _ends   = _change_idx + [_n]

            for _start, _end in zip(_starts, _ends):
                _dy      = int(_dy_arr[_start])
                _next_dy = int(_dy_arr[_end]) if _end < _n else 0
                # Extend the band downward to fill any gap left by the shift decrease.
                _bh = min(_end - _start + max(0, _dy - _next_dy), _n - _start)
                _band = scaled_ground.subsurface((0, _start, sw, _bh))
                self.logical_surface.blit(_band, (0, horizon_y + HORIZON_OFFSET + _start - _dy))

            self.logical_surface.set_clip(None)

        elif not texture:
            pygame.draw.rect(self.logical_surface, (28, 65, 28),
                             (0, horizon_y, sw, ground_h))

        # ── Horizon blend overlay ─────────────────────────────────────────────
        if not hasattr(self, '_world_map_blend'):
            try:
                self._world_map_blend = pygame.image.load(
                    'assets/map/world_map_blend.png'
                ).convert_alpha()
            except Exception as e:
                print(f'[world_map] could not load blend texture: {e}')
                self._world_map_blend = None

        if self._world_map_blend:
            blend_w = sw
            blend_h = 86 * RENDER_SCALE
            # Cache the scaled+tinted blend — it never changes unless the window is resized.
            _blend_cache_key = (blend_w, blend_h)
            if (not hasattr(self, '_mjf_blend_cached')
                    or self._mjf_blend_cached_key != _blend_cache_key):
                _b = pygame.transform.scale(self._world_map_blend, _blend_cache_key)
                _b.fill((80, 80, 80), special_flags=pygame.BLEND_RGB_MULT)
                self._mjf_blend_cached     = _b
                self._mjf_blend_cached_key = _blend_cache_key
            self.logical_surface.blit(self._mjf_blend_cached, (0, sky_h - 8), special_flags=pygame.BLEND_RGB_ADD)

        # ── Flying character sprite ────────────────────────────────────────
        if self._mjf_flying_frames:
            frame = self._mjf_flying_frames[self._mjf_flying_frame_idx]

            sprite_scale = RENDER_SCALE * (3.0 - 2.3 * altitude)
            w = int(frame.get_width() * sprite_scale)
            h = int(frame.get_height() * sprite_scale)

            base_scale = RENDER_SCALE * 3.0
            base_h = int(frame.get_height() * base_scale)

            # Pixel-measured from Buu's Fury reference: the sprite barely moves
            # vertically on screen. Altitude is communicated through scale and
            # horizon height — NOT by the sprite sweeping up and down.
            # sprite_cy = sky_h * (0.30 at max alt  ->  0.93 at ground level)
            # Lowered the base from 0.48 -> 0.30 so the icon rises higher
            # on-screen as altitude increases (was capped near the midpoint).
            sprite_y = sky_h * (0.30 + 0.63 * (1.0 - altitude))

            # Shadow — now after sprite_y is known
            shadow_x = int(self._mjf_fly_x)
            # Shadow Y: placed lower on screen (0.65 factor vs old 0.35) and shifts
            # slightly toward the horizon at higher altitude, matching Buu's Fury reference.
            # Pixel-measured from GBA footage: shadow sits at ~77% of screen height at
            # low alt, drifting up to ~75% at high alt.
            shadow_ground_y = int(sky_h + (sh - sky_h) * 0.32)

            # ── GBA-style shadow – pixel-exact 11-step lookup ────────────────
            # Each entry: (total_w, total_h, [(left_inset, right_inset), ...])
            # Row definitions extracted pixel-by-pixel from the Buu's Fury
            # reference sprites (white = transparent, black = opaque).
            # Shapes are indexed 0 (lowest altitude) → 10 (highest altitude).
            # All sizes are in GBA native pixels; multiply by RENDER_SCALE for
            # logical-surface coordinates.  Drawn as plain black rects – no
            # surfaces, no blending, no ellipses.
            _SHADOW_SHAPES = (
                # 0  alt≈0.00  20×8
                (25, 6, ((2, 0), (2, 0), (2, 0), (0, 0), (0, 0), (0, 0))),
                # 1  alt≈0.10  18×8
                (25, 6, ((3, 2), (3, 2), (3, 2), (0, 0), (0, 0), (0, 0))),
                # 2  alt≈0.20  18×8
                (25, 6, ((3, 2), (3, 2), (3, 2), (1.5, 0), (1.5, 0), (1.5, 0))),
                # 3  alt≈0.30  20×8
                (25, 6, ((4,4),(4,4),(4,4),(1.5,1.5),(1.5,1.5),(1.5,1.5))),
                # 4  alt≈0.40  20×8  (asymmetric – left inset 1, right inset 2)
                (25, 6, ((4,4),(4,4),(4,4),(2.5,2.5),(2.5,2.5),(2.5,2.5))),
                # 5  alt≈0.50  16×8  (asymmetric – left inset 2, right inset 0)
                (25, 6, ((6,6),(6,6),(6,6),(4.5,4.5),(4.5,4.5),(4.5,4.5))),
                # 6  alt≈0.60  14×8  fully solid
                (25, 7, ((8,8),(8,8),(6.5,6.5),(6.5,6.5),(6.5,6.5),(8,8),(8,8))),
                # 7  alt≈0.70  14×6
                (25, 7, ((7,7),(7,7),(7,7),(7,7),(7,7),(7,7),(7,7))),
                # 8  alt≈0.80  14×6  (inset top AND bottom)
                (25, 5, ((8.5,8.5),(8.5,8.5),(7,7),(7,7),(7,7))),
                # 9  alt≈0.90  12×6  fully solid
                (25, 6, ((8.5,8.5),(8.5,8.5),(7,7),(7,7),(8.5,8.5),(8.5,8.5))),
                # 10 alt≈1.00  12×4
                (25, 6, ((8.5,8.5),(8.5,8.5),(8.5,8.5),(8.5,8.5),(8.5,8.5),(8.5,8.5))),
                # 11
                (25, 6, ((10, 10), (10, 10), (8.5, 8.5), (8.5, 8.5))),
            )
            _idx = min(11, int(altitude * 12))
            _tw, _th, _rows = _SHADOW_SHAPES[_idx]
            RS = RENDER_SCALE
            # Top-left corner of shadow bounding box
            _sx = shadow_x - (_tw * RS) // 2
            _sy = shadow_ground_y - (_th * RS) // 2
            for _ry, (_li, _ri) in enumerate(_rows):
                _rw = (_tw - _li - _ri) * RS
                if _rw > 0:
                    pygame.draw.rect(
                        self.logical_surface, (0, 0, 0),
                        (_sx + _li * RS, _sy + _ry * RS, _rw, RS)
                    )
            # ── End shadow ────────────────────────────────────────────────

            # GBA-style artifacting: scale down to a quantised native size first,
            # then scale back up with nearest-neighbour.  This makes whole pixel
            # rows/columns snap in and out as altitude changes, matching the
            # discrete affine-matrix scaling the GBA hardware produced.
            # _STEPS controls how coarsely the size quantises — lower = chunkier.
            _STEPS = 1.5
            _native_w = max(1, round(w / RENDER_SCALE / _STEPS) * _STEPS // _STEPS)
            _native_h = max(1, round(h / RENDER_SCALE / _STEPS) * _STEPS // _STEPS)
            _small = pygame.transform.scale(frame, (_native_w, _native_h))
            _mjf_scaled = pygame.transform.scale(_small, (w, h))
            _mjf_rect   = _mjf_scaled.get_rect(center=(int(self._mjf_fly_x), int(sprite_y)))
            self._mjf_sprite_rect = _mjf_rect  # expose to update loop for collision
            # Depth of the player on the ground plane — used for painter's-algo
            # sorting against location icons drawn later.
            _shad_row_p      = max(shadow_ground_y - horizon_y + HORIZON_OFFSET, 1)
            _mjf_player_depth = FOCAL * (sh - PROJECTION_HORIZON) / _shad_row_p
        else:
            _mjf_scaled       = None
            _mjf_rect         = None
            _mjf_player_depth = float('inf')  # no player → never occludes icons

        # ── Mini-map HUD (top-right corner) ──────────────────────────────────
        # ── Size control: change _HUD_SCALE to make both sprites bigger/smaller.
        _HUD_SCALE = RENDER_SCALE * 1.5   # e.g. RENDER_SCALE*2 for double size
        if not hasattr(self, '_world_map_hud'):
            try:
                _raw_hud = pygame.image.load('assets/map/world_map_hud.png').convert_alpha()
                self._world_map_hud = pygame.transform.scale(
                    _raw_hud,
                    (_raw_hud.get_width() * _HUD_SCALE, _raw_hud.get_height() * _HUD_SCALE)
                )
            except Exception as e:
                print(f'[world_map] could not load world_map_hud.png: {e}')
                self._world_map_hud = None

        if not hasattr(self, '_world_map_arrow_base'):
            try:
                _raw_arrow = pygame.image.load('assets/map/world_map_arrow.png').convert_alpha()
                # Store the raw sprite (native size) so rotation artifacts naturally
                _br  = _raw_arrow.get_bounding_rect()
                _nw  = _raw_arrow.get_width()  + 2 * abs(_raw_arrow.get_width()  // 2 - _br.centerx)
                _nh  = _raw_arrow.get_height() + 2 * abs(_raw_arrow.get_height() // 2 - _br.centery)
                _recentered = pygame.Surface((_nw, _nh), pygame.SRCALPHA)
                _recentered.blit(_raw_arrow, (_nw // 2 - _br.centerx, _nh // 2 - _br.centery))
                self._world_map_arrow_base     = _recentered        # raw, for rotating
                self._world_map_arrow_hud_scale = int(_HUD_SCALE)   # scale applied after rotate
            except Exception as e:
                print(f'[world_map] could not load world_map_arrow.png: {e}')
                self._world_map_arrow_base      = None
                self._world_map_arrow_hud_scale = int(_HUD_SCALE)

        if self._world_map_hud:
            _hud   = self._world_map_hud
            _hud_w = _hud.get_width()
            _hud_h = _hud.get_height()
            _hud_x = sw - _hud_w - 8 * RENDER_SCALE
            _hud_y = 8 * RENDER_SCALE
            self.logical_surface.blit(_hud, (_hud_x, _hud_y),
                                      special_flags=pygame.BLEND_ADD)

            if texture:
                _shad_screen_y  = int(sky_h + (sh - sky_h) * 0.32)
                _shad_row       = max(_shad_screen_y - horizon_y + HORIZON_OFFSET, 1)
                _shad_depth     = FOCAL * (sh - PROJECTION_HORIZON) / _shad_row
                _angle          = self._mjf_cam_angle
                _shadow_world_x = self._mjf_cam_x + _math.sin(_angle) * _shad_depth
                _shadow_world_y = self._mjf_cam_y - _math.cos(_angle) * _shad_depth

                _norm_x = (_shadow_world_x % tw) / tw
                _norm_y = (_shadow_world_y % th) / th
                _dot_x  = int(_hud_x + _norm_x * _hud_w)
                _dot_y  = int(_hud_y + _norm_y * _hud_h)

                if self._world_map_arrow_base:
                    _arrow_deg = _math.degrees(_angle)
                    if getattr(self, '_mjf_moving_backward', False):
                        _arrow_deg += 180.0
                    # Rotate at native 5×7 size so pixels artifact naturally,
                    # then scale up to display size.
                    _arrow_rot = pygame.transform.rotate(
                        self._world_map_arrow_base, -_arrow_deg
                    )
                    _hs = self._world_map_arrow_hud_scale
                    _arrow_rot = pygame.transform.scale(
                        _arrow_rot,
                        (_arrow_rot.get_width() * _hs, _arrow_rot.get_height() * _hs)
                    )
                    self.logical_surface.blit(
                        _arrow_rot, _arrow_rot.get_rect(center=(_dot_x, _dot_y)))
                else:
                    pygame.draw.circle(
                        self.logical_surface, (255, 255, 80), (_dot_x, _dot_y), RENDER_SCALE + 1)
                    pygame.draw.circle(
                        self.logical_surface, (255, 50, 50),  (_dot_x, _dot_y), RENDER_SCALE)

                # ── Location markers on minimap ────────────────────────────────
                if not hasattr(self, '_world_map_loc_sprite'):
                    try:
                        self._world_map_loc_sprite = pygame.image.load(
                            'assets/map/world_map_loc.png'
                        ).convert_alpha()
                    except Exception as e:
                        print(f'[world_map] could not load world_map_loc.png: {e}')
                        self._world_map_loc_sprite = None

                if self._world_map_loc_sprite and self._wm_locations:
                    _loc_size = 4 * RENDER_SCALE
                    _loc_spr  = pygame.transform.scale(
                        self._world_map_loc_sprite, (_loc_size, _loc_size)
                    )
                    _ppt_x_hm = tw / 362
                    _ppt_y_hm = th / 263
                    for _mloc in self._wm_locations:
                        _mloc_wx  = (_mloc['x'] * _ppt_x_hm) % tw
                        _mloc_wy  = (_mloc['y'] * _ppt_y_hm) % th
                        _mloc_nx  = _mloc_wx / tw
                        _mloc_ny  = _mloc_wy / th
                        _mloc_sx  = int(_hud_x + _mloc_nx * _hud_w)
                        _mloc_sy  = int(_hud_y + _mloc_ny * _hud_h)
                        self.logical_surface.blit(
                            _loc_spr, _loc_spr.get_rect(center=(_mloc_sx, _mloc_sy))
                        )

        # ── Location billboards ───────────────────────────────────────────────
        # Explicitly clear any clip that may have leaked from the ground-tile
        # rendering pass. Icons must draw freely over the full surface.
        self.logical_surface.set_clip(None)
        # Load icon surfaces once, cached by stem
        if not hasattr(self, '_wm_icon_cache'):
            self._wm_icon_cache = {}

        # Load location list once (alongside the texture)
        if not hasattr(self, '_wm_locations'):
            self._wm_locations = []
            _active_map = getattr(self, '_active_world_map_name', '')
            if _active_map:
                import json as _json, os as _os
                try:
                    with open(_os.path.join('assets', 'world_maps',
                                            f'{_active_map}.json')) as _f:
                        _ld = _json.load(_f)
                    self._wm_locations = _ld.get('locations', [])
                    # Load entities from the same file (avoid a second open)
                    if not hasattr(self, '_wm_entities'):
                        self._wm_entities = _ld.get('entities', [])
                except Exception:
                    pass

        # Load entity list once (in case locations were already loaded without them)
        if not hasattr(self, '_wm_entities'):
            self._wm_entities = []
            _active_map2 = getattr(self, '_active_world_map_name', '')
            if _active_map2:
                import json as _json2, os as _os2
                try:
                    with open(_os2.path.join('assets', 'world_maps',
                                             f'{_active_map2}.json')) as _f2:
                        self._wm_entities = _json2.load(_f2).get('entities', [])
                except Exception:
                    pass

        # Vehicle sprite cache and animation timer
        if not hasattr(self, '_wm_vehicle_cache'):
            self._wm_vehicle_cache: dict = {}
        # _wm_entity_anim_t is advanced in update() every frame.

        # Project and collect each location marker (deferred draw for depth sort)
        _icon_draw_list = []         # list of (depth, surf, rect)
        # Collision list: world coords for every location that has a room.
        # Built independently of the draw loop so culling never hides icons.
        _icon_screen_positions = []
        if texture and self._wm_locations:
            _coll_ppt_x = tw / 362
            _coll_ppt_y = th / 263
            for _cloc in self._wm_locations:
                if _cloc.get('room', ''):
                    _icon_screen_positions.append(
                        (_cloc['x'] * _coll_ppt_x, _cloc['y'] * _coll_ppt_y, _cloc)
                    )
        if texture and self._wm_locations:
            _MAP_TILE_W = 362
            _MAP_TILE_H = 263
            _ppt_x = tw / _MAP_TILE_W  # texture pixels per tile, X
            _ppt_y = th / _MAP_TILE_H  # texture pixels per tile, Y

            virtual_ground_h = sh - PROJECTION_HORIZON

            for _loc in self._wm_locations:
                # Convert tile coord → texture pixel space
                _wx = _loc['x'] * _ppt_x
                _wy = _loc['y'] * _ppt_y

                # Delta from camera with shortest-path wrapping
                _dx = (_wx - cam_x) % tw
                if _dx > tw * 0.5:
                    _dx -= tw
                _dy = (_wy - cam_y) % th
                if _dy > th * 0.5:
                    _dy -= th

                # Project onto camera axes
                _depth = _dx * sin_a - _dy * cos_a
                if _depth <= 1:
                    continue  # behind camera or too close

                _col = (_dx * cos_a + _dy * sin_a) / _depth

                # Screen position (ground plane hit point)
                _rows_f = FOCAL * virtual_ground_h / _depth
                _screen_x = int(sw * 0.5 + _col * sw)
                _row_offset = max(0, int(_rows_f) - HORIZON_OFFSET)
                _t_curve = 1.0 - (_row_offset / max(1, effective_ground - 1))
                _dy_curve = int(_t_curve * _t_curve * _curve_px)
                _screen_y = int(horizon_y + _rows_f - _dy_curve)

                # Perspective scale: larger when closer, smaller when farther.
                _near_depth = base_focal * virtual_ground_h / max(1, sh - horizon_y)
                _persp = max(0.1, min(2.5, _near_depth / _depth))

                # Apply the location's height as a perspective-scaled vertical offset.
                # height > 0 raises the icon above the ground plane; < 0 lowers it.
                # Cap the persp used here at 1.0 so the offset stays stable as the
                # player flies close — without the cap, growing _persp at short range
                # would shoot the icon above the skyline and trigger the cull early.
                # Save the ground-plane y before the offset so culling is always
                # based on where the tile sits on the map, not where the icon floats.
                _loc_height = _loc.get('height', 0)
                _ground_screen_y = _screen_y   # unmodified ground-plane hit
                if _loc_height:
                    _height_persp = min(1.0, max(0.5, _persp))
                    _screen_y -= int(_loc_height * _height_persp * 0.5)
                    _screen_y = max(int(horizon_y) + 4, _screen_y)

                _icon_stem = _loc.get('icon', '')
                if _icon_stem:
                    if _icon_stem not in self._wm_icon_cache:
                        try:
                            self._wm_icon_cache[_icon_stem] = pygame.image.load(
                                f'assets/map/icons/{_icon_stem}.png'
                            ).convert_alpha()
                        except Exception:
                            self._wm_icon_cache[_icon_stem] = None

                    _icon_raw = self._wm_icon_cache[_icon_stem]
                    if _icon_raw:
                        _isz = max(2, int(100 * RENDER_SCALE * _persp))  # ← change 50 to resize icons
                        _icon_surf = pygame.transform.scale(_icon_raw, (_isz, _isz))
                        _icon_rect = _icon_surf.get_rect(midbottom=(_screen_x, _screen_y))
                        # Cull on the ground-plane position (_ground_screen_y), not
                        # the visually-shifted _screen_y.  This means an icon whose
                        # tile is behind the horizon correctly hides (ground y <= sky_h),
                        # while an icon whose tile is in front stays visible even if
                        # the height offset pushed the sprite above sky_h.
                        if (0 <= _screen_x < sw) and (_ground_screen_y > sky_h) and (_icon_rect.top < sh):
                            _icon_draw_list.append((_depth, _icon_surf, _icon_rect))

        # ── World-map entity vehicles ─────────────────────────────────────────
        # Each entity follows a path defined in the editor.  We project its
        # current position with the same perspective math as location icons and
        # insert it into the depth-sorted draw list so painter's algorithm handles
        # occlusion correctly.
        import math as _mve

        def _wm_ent_pos(ent, t):
            """Return (tile_x, tile_y) float for entity *ent* at time *t* seconds."""
            path = ent.get('path', [])
            if not path:
                return None
            if len(path) == 1:
                return float(path[0][0]), float(path[0][1])
            closed = ent.get('closed', False)
            pts = list(path)
            if closed:
                pts.append(path[0])
            segs, total = [], 0.0
            for _i in range(len(pts) - 1):
                _d = _mve.hypot(pts[_i+1][0] - pts[_i][0], pts[_i+1][1] - pts[_i][1])
                segs.append(_d); total += _d
            if total == 0.0:
                return float(path[0][0]), float(path[0][1])
            _SPEED = 2.5
            if closed:
                dist = (t * _SPEED) % total
            else:
                cycle = total * 2.0
                phase = (t * _SPEED) % cycle
                dist  = phase if phase <= total else cycle - phase
            walked = 0.0
            for _i, _sl in enumerate(segs):
                if walked + _sl >= dist or _i == len(segs) - 1:
                    frac = ((dist - walked) / _sl) if _sl > 0 else 0.0
                    frac = max(0.0, min(1.0, frac))
                    return (pts[_i][0] + frac * (pts[_i+1][0] - pts[_i][0]),
                            pts[_i][1] + frac * (pts[_i+1][1] - pts[_i][1]))
                walked += _sl
            return float(path[-1][0]), float(path[-1][1])

        # ── Entity collision entries ───────────────────────────────────────────
        # Entities with a linked room use the same collision loop as location icons.
        # We append them here, after _wm_ent_pos is defined, using the entity's
        # current animated world position so the trigger zone moves with the entity.
        if texture and self._wm_entities:
            _cent_ppt_x = tw / 362
            _cent_ppt_y = th / 263
            for _cent in self._wm_entities:
                if not _cent.get('room', ''):
                    continue
                _cepos = _wm_ent_pos(_cent, self._wm_entity_anim_t)
                if _cepos is None:
                    continue
                _ce_wx = _cepos[0] * _cent_ppt_x
                _ce_wy = _cepos[1] * _cent_ppt_y
                _cent_loc = {
                    'room':         _cent['room'],
                    'name':         _cent.get('name', ''),
                    'height':       _cent.get('height', 0),
                    'x':            _cepos[0], 'y': _cepos[1],
                    '_entity_name': _cent.get('name', ''),  # used by landing to pick correct WMO
                }
                _icon_screen_positions.append((_ce_wx, _ce_wy, _cent_loc))

        def _wm_dir_row(dx, dy, num_dirs):
            """Map movement vector to spritesheet row (frames right, dirs top-to-bottom)."""
            if num_dirs <= 1:
                return 0
            _a = (_mve.atan2(dy, dx) + _mve.pi * 2) % (_mve.pi * 2)
            if num_dirs >= 8:
                _sec = int((_a + _mve.pi / 8) / (_mve.pi / 4)) % 8
                return {0: 2, 1: 7, 2: 0, 3: 4, 4: 1, 5: 5, 6: 3, 7: 6}.get(_sec, 0)
            else:
                _sec = int((_a + _mve.pi / 4) / (_mve.pi / 2)) % 4
                return {0: 2, 1: 0, 2: 1, 3: 3}.get(_sec, 0)  # E→right S→down W→left N→up

        def _wm_load_vehicle(stem):
            """Load and cache a vehicle spritesheet, returning a frames dict."""
            if stem in self._wm_vehicle_cache:
                return self._wm_vehicle_cache[stem]
            import os as _osv
            _path = _osv.path.join('assets', 'map', 'vehicle', stem + '.png')
            try:
                _sh = pygame.image.load(_path).convert_alpha()
                _sw2, _sh2 = _sh.get_size()
                _fh    = 32
                _ndirs = max(1, _sh2 // _fh)
                _fw    = 32 if (_sw2 % 32 == 0) else 64
                _nf    = max(1, _sw2 // _fw)
                _fbr   = {}
                for _r in range(_ndirs):
                    _fbr[_r] = [
                        _sh.subsurface(pygame.Rect(_f * _fw, _r * _fh, _fw, _fh)).copy()
                        for _f in range(_nf)
                    ]
                entry = {'frames_by_row': _fbr, 'num_dirs': _ndirs,
                         'num_frames': _nf, 'frame_w': _fw, 'frame_h': _fh}
                self._wm_vehicle_cache[stem] = entry
                return entry
            except Exception as _ev:
                print(f'[world_map] could not load vehicle {stem}: {_ev}')
                self._wm_vehicle_cache[stem] = None
                return None

        _ANIM_FPS  = 4.0
        _ent_fidx  = int(self._wm_entity_anim_t * _ANIM_FPS)

        if texture and self._wm_entities:
            _MAP_TILE_W_E = 362;  _MAP_TILE_H_E = 263
            _ppt_xe = tw / _MAP_TILE_W_E
            _ppt_ye = th / _MAP_TILE_H_E
            _vgh_e  = sh - PROJECTION_HORIZON

            for _ent in self._wm_entities:
                if not _ent.get('path'):
                    continue
                _epos = _wm_ent_pos(_ent, self._wm_entity_anim_t)
                if _epos is None:
                    continue
                _epx, _epy = _epos

                # Movement direction (sample slightly ahead for dir row)
                _epos2 = _wm_ent_pos(_ent, self._wm_entity_anim_t + 0.15)
                if _epos2 and _epos2 != _epos:
                    _emx, _emy = _epos2[0] - _epx, _epos2[1] - _epy
                else:
                    _p = _ent.get('path', [])
                    _emx, _emy = ((_p[1][0]-_p[0][0], _p[1][1]-_p[0][1]) if len(_p)>=2 else (0, 1))

                # World → texture pixel coords
                _ewx = _epx * _ppt_xe
                _ewy = _epy * _ppt_ye

                # Project (identical to location-icon projection above)
                _edx = (_ewx - cam_x) % tw
                if _edx > tw * 0.5: _edx -= tw
                _edy = (_ewy - cam_y) % th
                if _edy > th * 0.5: _edy -= th

                _edepth = _edx * sin_a - _edy * cos_a
                if _edepth <= 1:
                    continue

                _ecol      = (_edx * cos_a + _edy * sin_a) / _edepth
                _erows_f   = FOCAL * _vgh_e / _edepth
                _escreen_x = int(sw * 0.5 + _ecol * sw)
                _erow_off  = max(0, int(_erows_f) - HORIZON_OFFSET)
                _et_curve  = 1.0 - (_erow_off / max(1, effective_ground - 1))
                _edy_curve = int(_et_curve * _et_curve * _curve_px)
                _escreen_y = int(horizon_y + _erows_f - _edy_curve)
                _eground_screen_y = _escreen_y  # ground-plane y before height offset

                _enear  = base_focal * _vgh_e / max(1, sh - horizon_y)
                _epersp = max(0.1, min(2.5, _enear / _edepth))

                # Apply entity height offset (same formula as location icons)
                _ent_height = _ent.get('height', 0)
                if _ent_height:
                    _eheight_persp = min(1.0, max(0.5, _epersp))
                    _escreen_y -= int(_ent_height * _eheight_persp * 0.5)
                    _escreen_y = max(int(horizon_y) + 4, _escreen_y)

                if not (0 <= _escreen_x < sw) or _eground_screen_y <= sky_h:
                    continue

                # Load spritesheet and pick the correct directional frame
                _vstem = _ent.get('sprite', '')
                if not _vstem:
                    continue
                _vdata = _wm_load_vehicle(_vstem)
                if not _vdata:
                    continue

                # Rotate world-space movement into camera-relative screen space.
                # The camera faces direction (sin_a, -cos_a) in texture coords.
                # Rotating (emx, emy) by -cam_angle gives the direction as seen
                # on screen: positive x = right, positive y = toward camera (down).
                _ecam_dx =  _emx * cos_a + _emy * sin_a   # screen-right component
                _ecam_dy = -_emx * sin_a + _emy * cos_a   # screen-down component
                _erow   = _wm_dir_row(_ecam_dx, _ecam_dy, _vdata['num_dirs'])
                _erow   = min(_erow, _vdata['num_dirs'] - 1)
                _efrms  = _vdata['frames_by_row'].get(_erow,
                          _vdata['frames_by_row'].get(0, []))
                if not _efrms:
                    continue
                _eframe = _efrms[_ent_fidx % len(_efrms)]

                # Scale proportionally to perspective
                _eish = max(4, int(_vdata['frame_h'] * RENDER_SCALE * _epersp * 2))
                _eisw = max(4, int(_eish * _vdata['frame_w'] / _vdata['frame_h']))
                _esurf = pygame.transform.scale(_eframe, (_eisw, _eish))
                _erect = _esurf.get_rect(midbottom=(_escreen_x, _escreen_y))

                if _erect.top < sh:
                    _icon_draw_list.append((_edepth, _esurf, _erect))

        # ── Painter's algorithm: draw icons + player back-to-front ────────────
        # Sprites and icons always draw at full opacity. The black overlay drawn
        # at the end of this function covers everything — no per-sprite fading.
        _icon_draw_list.sort(key=lambda e: e[0], reverse=True)
        _player_drawn = False
        for _entry_depth, _entry_surf, _entry_rect in _icon_draw_list:
            # Draw the player once, at the point where its depth is reached.
            if not _player_drawn and _entry_depth <= _mjf_player_depth:
                if _mjf_scaled is not None:
                    _mjf_scaled.set_alpha(255)
                    self.logical_surface.blit(_mjf_scaled, _mjf_rect)
                _player_drawn = True
            _screen_rect = self.logical_surface.get_rect()
            _visible = _entry_rect.clip(_screen_rect)
            if _visible.width > 0 and _visible.height > 0:
                _src_rect = pygame.Rect(
                    _visible.x - _entry_rect.x,
                    _visible.y - _entry_rect.y,
                    _visible.width,
                    _visible.height,
                )
                _entry_surf.set_alpha(255)
                self.logical_surface.blit(_entry_surf, _visible, _src_rect)
        # If no icons were closer than the player (or no icons at all), draw now.
        if not _player_drawn and _mjf_scaled is not None:
            _mjf_scaled.set_alpha(255)
            self.logical_surface.blit(_mjf_scaled, _mjf_rect)

        # Persist icon screen positions so _update_map_flying can do collision.
        self._wm_last_icon_screen_positions = _icon_screen_positions

        # ── Lower bar ─────────────────────────────────────────────────────────
        if not hasattr(self, '_world_map_lower_bar'):
            try:
                _raw_lb = pygame.image.load('assets/map/lower_bar.png').convert_alpha()
                self._world_map_lower_bar = _raw_lb
            except Exception as e:
                print(f'[world_map] could not load lower_bar.png: {e}')
                self._world_map_lower_bar = None

        if self._world_map_lower_bar:
            _lb      = self._world_map_lower_bar
            _lb_h    = int(_lb.get_height() * RENDER_SCALE * 2)
            _lb_surf = pygame.transform.scale(_lb, (sw, _lb_h))
            _lb_y    = sh - _lb_h - int(4 * RENDER_SCALE) - 50
            self.logical_surface.blit(_lb_surf, (0, _lb_y))

            # ── Bitmap font helpers ───────────────────────────────────────────
            import os as _os
            _FDIR = _os.path.join('assets', 'ui', 'fonts', 'world_map')
            _SPACE_W = int(_lb_h * 0.3)   # width of a space character

            # Lazy per-height glyph cache: (char, height) → colourised Surface
            if not hasattr(self, '_wm_glyph_cache'):
                self._wm_glyph_cache: dict = {}

            def _char_to_filename(ch):
                if ch == ':':  return 'colon.png'
                if ch == '/':  return 'slash.png'
                return ch.upper() + '.png'

            def _get_glyph(ch, height, color):
                key = (ch, height, color)
                if key in self._wm_glyph_cache:
                    return self._wm_glyph_cache[key]
                path = _os.path.join(_FDIR, _char_to_filename(ch))
                try:
                    raw = pygame.image.load(path).convert_alpha()
                    # Scale to target height, preserve aspect ratio
                    ow, oh = raw.get_size()
                    nw = max(1, int(ow * height / oh))
                    scaled = pygame.transform.scale(raw, (nw, height))
                    # Colorise: replace white/light pixels with the target colour
                    coloured = scaled.copy()
                    coloured.fill(color, special_flags=pygame.BLEND_RGB_MULT)
                    self._wm_glyph_cache[key] = coloured
                    return coloured
                except Exception:
                    self._wm_glyph_cache[key] = None
                    return None

            def _measure(text, height, color):
                w = 0
                for ch in text:
                    if ch == ' ':
                        w += _SPACE_W
                    else:
                        g = _get_glyph(ch, height, color)
                        if g:
                            w += g.get_width() + _SPACING
                return w

            def _blit_text(surf, text, x, y, height, color):
                for ch in text:
                    if ch == ' ':
                        x += _SPACE_W
                    else:
                        g = _get_glyph(ch, height, color)
                        if g:
                            surf.blit(g, (x, y))
                            x += g.get_width() + _SPACING
                return x

            # ── Draw centred button hints ─────────────────────────────────────
            _WHITE    = (255, 255, 255)
            _CYAN     = (0, 220, 200)
            _SPACING = 4
            _gh       = int(_lb_h * 0.38)   # glyph height
            _nearby   = getattr(self, '_mjf_nearby_loc_name', '')
            if _nearby:
                _segments = [(_nearby, _WHITE)]
            else:
                _segments = [
                    ('A BUTTON: ', _WHITE),
                    ('DESCEND/LAND  ', _CYAN),
                    ('B BUTTON: ', _WHITE),
                    ('ASCEND', _CYAN),
                ]
            _total_w = sum(_measure(t, _gh, c) for t, c in _segments)
            _tx      = (sw - _total_w) // 2
            _ty      = _lb_y + (_lb_h - _gh) // 2
            for _txt, _col in _segments:
                _tx = _blit_text(self.logical_surface, _txt, _tx, _ty, _gh, _col)

        # Fade overlay drawn last so it covers the ground, player sprite, and
        # all location icons — non-zero during 'fade_in' and 'landing_fade_out'.
        self._draw_map_jump_fade(self.logical_surface)

    # ── Character switching ───────────────────────────────────────────────────

    def _get_allowed_ki_modes(self) -> tuple:
        """Return the ordered tuple of ki-attack modes this character can cycle through.

        Derived directly from player.equipped_attacks:
          - 'ki_blast' in equipped  → blast mode available
          - any other attack equipped → beam mode available
          - transform is always appended last

        Falls back to ('blast', 'transform') when no config has been loaded yet.
        """
        equipped = getattr(self.player, 'equipped_attacks', [])

        modes: list[str] = []
        if 'ki_blast' in equipped:
            modes.append('blast')
        if any(a != 'ki_blast' for a in equipped):
            modes.append('beam')

        # Always include transform so the player can still power up.
        modes.append('transform')

        # If nothing was equipped at all, keep blast as the default so the
        # game is still playable before any config file has been saved.
        return tuple(modes) if len(modes) > 1 else ('blast', 'transform')

    def _switch_character(self, character_id):
        """Swap the player's sprite to character_id while keeping all gameplay state intact."""
        from core.sprite_system import create_character_sprite

        # Snapshot current state before the swap.
        state = {
            'x': self.player.x,
            'y': self.player.y,
            'hp': self.player.hp,
            'ki': self.player.ki,
            'level': self.player.level,
            'stats': self.player.stats.copy(),
            'inventory': self.player.inventory.copy(),
            'direction': self.player.direction,
            'current_animation_state': getattr(self.player, 'current_animation_state', 'idle'),  # capture BEFORE swap
        }

        self.player.character = character_id
        self.player.sprite    = create_character_sprite(character_id, 'base', 32, 32)

        # Restore state so the swap is completely seamless to the player.
        for key, value in state.items():
            setattr(self.player, key, value)

        # The freshly created sprite doesn't know the player's pre-swap
        # facing — push it in now, or the new character defaults to facing
        # down until the player's next manual direction change.
        if hasattr(self.player.sprite, 'set_animation'):
            self.player.sprite.set_animation(
                state['current_animation_state'], state['direction'])

        # Apply the character's attack config so only equipped attacks are usable.
        cfg = character_creator.load_config(character_id)
        atk = cfg.get('attacks', {})

        self.player.equipped_attacks = list(atk.get('equipped_attacks', []))
        self.player.ki_mode_config   = atk.get('ki_attack_mode', 'blast')

        # Reset ki_attack_mode to the first mode the new character actually has.
        # This prevents being left in e.g. beam mode after switching to a character
        # that only has blast equipped, and vice-versa.
        allowed = self._get_allowed_ki_modes()
        if self.player.ki_attack_mode not in allowed:
            self.player.ki_attack_mode = allowed[0] if allowed else 'blast'

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
        # Clamp dt so a focus-loss / debugger pause / OS interrupt never causes
        # a multi-second physics step that flings entities through walls or
        # corrupts fade-timers.  Cap at ~66 ms (4 frames @ 60 fps) — anything
        # higher is almost certainly a debugger break, minimize, or OS sleep.
        dt             = min(dt, 4.0 / 60.0)
        self.dt        = dt

        # Advance the world-map entity animation clock every frame so that
        # in-room WorldMapObjects linked to entities always reflect the entity's
        # true path position, even when the world-map flying scene is not active.
        if not hasattr(self, '_wm_entity_anim_t'):
            self._wm_entity_anim_t = 0.0
        self._wm_entity_anim_t += dt

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
            # ── World-map flying sequence ──────────────────────────────────────
            # Once the map-jump fade-out has fired (or is completing), hand off
            # all update logic to _update_map_flying and skip game simulation.
            if self._mjf_state in ('pending_fade_in', 'fade_in', 'flying',
                                   'landing_fade_out', 'landing_fade_in'):
                self._update_map_flying(dt)
                return

            # Accumulate play time only while unpaused and no overlay is blocking.
            if not self.save_point_menu.active and not self.character_switch_menu.active \
                    and not self.pause_menu.active:
                self.play_time += dt

            # Player movement is also suppressed during cutscenes and NPC dialogue.
            if not self.save_point_menu.active and not self.character_switch_menu.active \
                    and not self.pause_menu.active and not self.active_cutscene_runtime \
                    and not self.dialogue_box.active:
                self._update_player_movement(dt)

            self.player.update(dt)

            # Tick the map-jump fade-out.  Start once the player's sprite has
            # fully cleared the top of the camera; ramp alpha to full black.
            if self.player.is_map_jumping and self.player.map_jump_moving:
                player_screen_top = (self.player.y * RENDER_SCALE - self.camera.y
                                     + self.player.height * RENDER_SCALE)
                if player_screen_top < 0:
                    self._mjf_active = True
            if self._mjf_active:
                self._mjf_alpha = min(255.0, self._mjf_alpha + self._MJF_SPEED * dt)
                # Once fully black, don't wait for _on_exit — load the fly sprite
                # and transition immediately.  The player's map_jump animation may
                # still be running off-screen for several seconds before the
                # callback fires, causing a long black freeze.  We pre-load
                # everything _on_exit would do right now.
                if self._mjf_alpha >= 255.0 and self._mjf_state is None:
                    self._load_map_fly_sprite()
                    self._mjf_fly_x = SCREEN_WIDTH  / 2
                    self._mjf_fly_y = SCREEN_HEIGHT * 0.65
                    self._mjf_cam_x = 0.0
                    self._mjf_cam_y = 0.0
                    self._mjf_altitude = 0.5
                    self._mjf_needs_pin_correction = True
                    self._mjf_origin_room   = self.current_room.name if self.current_room else ''
                    self._mjf_origin_entity = getattr(self, '_mjf_last_entry_entity', '')
                    self._apply_world_map_music(getattr(self, '_active_world_map_name', ''))
                    self._mjf_state  = 'fade_in'
                    self._mjf_active = False
                    self.player.sprite.set_animation('idle', self.player.direction)
                    self.player.current_animation_state = 'idle'

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
                    if not getattr(melee, 'hit_something', False):
                        self.sound_manager.play_sfx('melee_miss')
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

            # World-map object proximity detection.
            self._update_world_map_objects(dt)

            # Destructible stones.
            self._update_stones(dt)

            # Level gates.
            self._update_gates(dt)

            # Transformation system (tracks energy and applies power-up state).
            # Frozen during cutscenes so ki doesn't drain while the player is
            # locked into a scripted sequence.
            if self.player.transformation and not self.active_cutscene_runtime:
                self.player.transformation.update(dt, enemies_defeated_this_frame)

            # Transformation aura — loops for as long as the player is
            # transformed and stops the moment they detransform.
            # play_looping_sfx is idempotent, so calling it every frame while
            # transformed doesn't restart the loop from the beginning.
            # NOTE: the flying pad also uses the 'aura' sfx for the duration
            # of a flight, so we must not stop it here while a flight is in
            # progress — FlyingController owns stopping it once landed.
            if self.player.is_transformed():
                self.sound_manager.play_looping_sfx('aura')
            elif not self.flying_controller.is_active():
                self.sound_manager.stop_looping_sfx('aura')

            # Adaptive music — switch between exploration and battle tracks.
            self.sound_manager.update_battle_state(dt, len(self.enemies) > 0)

            # Dev-tool overlays pause simulation when active — bail out early.
            if self.cutscene_editor.active:
                self.cutscene_editor.update(dt)
                return
            if self.dev_menu.active:
                self.dev_menu.update(dt)
                return
            if self.world_map_editor.active:
                self.world_map_editor.update(dt)
                return
            if self.sprite_editor.active:
                self.sprite_editor.update(dt)
                return
            if self.character_creator.active:
                self.character_creator.update(dt)
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

            actually_moved = (self.player.x != old_x) or (self.player.y != old_y)

            # Sprint footsteps — walking has no sound, only running does, and
            # only while the player is actually displacing. Without this,
            # holding into a wall while "running" (is_running stays true as
            # long as a direction key is held) would loop the run sound even
            # though the player is standing still against the wall.
            if actually_moved and self.player.tick_footsteps(dt):
                self.sound_manager.play_sfx('run')
            elif not actually_moved:
                # Fully blocked this frame — stop the timer from counting
                # down while stationary so the next real step, once the
                # player moves away from the wall, fires immediately.
                self.player.footstep_timer = 0.0

            # Collision resolution.
            # player.move() already handles walls/stones/gates/transitions
            # axis-by-axis, so the player never clips through them.
            # Here we only need to:
            #   1. Trigger running knockback when the player is blocked while sprinting.
            #   2. Handle NPC collisions (not in player.obstacles).
            collision = False

            # Running knockback — fire if move() blocked at least one axis while
            # sprinting, and the post-knockback cooldown has expired so holding
            # the key doesn't chain infinite bounces.
            # Diagonal movement just slides along the wall — no knockback.
            if is_running and (self.player._blocked_x or self.player._blocked_y) \
                    and self.player._knockback_cooldown <= 0 \
                    and not (dx != 0 and dy != 0):
                self.player.start_collision_knockback(dx, dy)
                self.camera.start_shake(intensity=15, duration=0.3)
                self.sound_manager.play_sfx('bump')
                collision = True

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

    def _play_melee_hit_sfx(self):
        """Randomly pick one of the two melee-connect swing sounds."""
        self.sound_manager.play_sfx(random.choice(('melee1', 'melee2')))

    def _play_impact_sfx(self):
        """Randomly pick one of the two hit-impact sounds — used whenever
        the player or an enemy actually takes a hit (as opposed to the
        swing sound itself)."""
        self.sound_manager.play_sfx(random.choice(('impact1', 'impact2')))

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
                self._play_impact_sfx()
                self.dmg_numbers.spawn(
                    self.player.x, self.player.y - self.player.height // 2,
                    self.player.last_damage_taken, variant='player',
                )

            for melee in self.melee_attacks:
                if melee.active and enemy.check_collision_with_attack(melee, 'melee'):
                    melee.hit_something = True
                    self._play_melee_hit_sfx()
                    self._play_impact_sfx()
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
        # Rebuild the shooter cache only when the enemy list changes length
        # (room load or enemy death). Saves an O(n) category scan every tick —
        # noticeable in dense rooms with 15+ enemies.
        _en_count = len(self.enemies)
        if getattr(self, '_shooter_cache_len', -1) != _en_count:
            self._shooter_enemies    = [e for e in self.enemies
                                        if getattr(e, 'enemy_category', '') == 'shooter']
            self._shooter_cache_len  = _en_count
        for enemy in self._shooter_enemies:
            if not enemy.active:
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
                self._play_impact_sfx()
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
                    self._play_impact_sfx()
                    self.dmg_numbers.spawn(
                        self.player.x, self.player.y - self.player.height // 2,
                        self.player.last_damage_taken, variant='player',
                    )

            if not blast.active:
                self.enemy_kiblasts.remove(blast)


    # Class-level so re.compile() runs once per interpreter start, not on
    # every NPC interaction. Static methods can’t reference self, so the
    # compiled pattern has to live here.
    _NPC_VARIANT_RE = __import__('re').compile(r'(\d+)$')

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
            m      = Game._NPC_VARIANT_RE.search(variant)
            suffix = str(int(m.group(1)) + 1) if m else '2'

        return f"npc_{npc_id}{suffix}"

    def _update_npcs(self, dt):
        """Tick all NPCs and record whichever one is currently in interaction range."""
        self.nearby_npc = None
        for npc in self.npcs[:]:
            npc.update(dt, self.player, self.current_room.width, self.current_room.height)
            # Only update nearby_npc once — first in-range NPC wins.
            if self.nearby_npc is None and npc.can_interact(self.player):
                self.nearby_npc = npc

    def _update_save_points(self, dt):
        """Tick save points and record whichever one the player is standing near."""
        for sp in self.save_points:
            sp.update(dt, self.player)

        self.nearby_save_point = next(
            (sp for sp in self.save_points if sp.is_player_nearby and sp.active),
            None
        )

    def _update_world_map_objects(self, dt):
        """Tick world-map objects and resolve which one (if any) the player can interact with.

        'world_map' (flat):  player must be standing on the object — checked via
                              rect overlap with the player's collision rect.
        'world_map_sign':    player must be within a small proximity radius, the
                              same approach used for NPCs and save points.
        """
        import pygame as _pg

        _SIGN_INTERACT_RADIUS = 24   # World units — tweak to feel right

        self.nearby_world_map_obj = None
        player_rect = self.player.get_collision_rect()

        for obj in self.world_map_objects:
            if not obj.active:
                continue

            if obj.variant == 'world_map':
                # Flat map: player walks onto it — overlap test.
                obj_rect = _pg.Rect(
                    obj.x - obj.width  // 2,
                    obj.y - obj.height // 2,
                    obj.width,
                    obj.height,
                )
                if player_rect.colliderect(obj_rect):
                    self.nearby_world_map_obj = obj
                    break

            elif obj.variant == 'world_map_sign':
                # Sign: proximity radius, same pattern as NPCs.
                dx = self.player.x - obj.x
                dy = self.player.y - obj.y
                if (dx * dx + dy * dy) <= _SIGN_INTERACT_RADIUS ** 2:
                    self.nearby_world_map_obj = obj
                    break

    def _update_stones(self, dt):
        """Tick destructible stones, check melee hits, and remove anything destroyed."""
        for stone in self.destructible_stones[:]:
            stone.update(dt)
            for melee in self.melee_attacks:
                if melee.active and stone.check_collision_with_attack(melee, 'melee'):
                    melee.hit_something = True
                    self._play_melee_hit_sfx()
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
                    melee.hit_something = True
                    self._play_melee_hit_sfx()

            for projectile in self.projectiles:
                if projectile.active:
                    if gate.check_collision_with_attack(projectile, 'projectile', self.player):
                        projectile.active = False

            if self.player.current_beam:
                beam = self.player.current_beam
                if gate not in getattr(beam, '_hit_gates', set()):
                    if gate.check_collision_with_attack(beam, 'beam', self.player):
                        if not hasattr(beam, '_hit_gates'):
                            beam._hit_gates = set()
                        beam._hit_gates.add(gate)

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

        # World-map flying sequence — replace the entire game scene with the
        # flying view (character sprite on a sky/space background) once the
        # fade-out has completed.  The black overlay is drawn on top by
        # _draw_world_map_flying_scene so the fade-in still works correctly.
        if self._mjf_state in ('pending_fade_in', 'fade_in', 'flying',
                               'landing_fade_out'):
            self._draw_world_map_flying_scene()
            pygame.display.flip()
            return

        # Flush any stale baked tile surfaces once per frame before anything
        # tries to access them — keeps rebuilds to one per frame regardless of
        # how many on_tile_changed calls arrived during event processing.
        self._flush_dirty_tile_rooms()

        # Fill with the default "green dev room" background colour.
        self.logical_surface.fill((34, 139, 34))

        # Scrolling background — was previously only drawn by the room editor's
        # own preview, so it never appeared during actual gameplay/test mode.
        self._draw_scrolling_background(self.dt)

        # Compute the world-space tile range that is currently on screen.
        visible_x_start = self.camera.x // RENDER_SCALE
        visible_y_start = self.camera.y // RENDER_SCALE
        visible_x_end   = (self.camera.x + SCREEN_WIDTH)  // RENDER_SCALE
        visible_y_end   = (self.camera.y + SCREEN_HEIGHT) // RENDER_SCALE

        # Draw a subtle tile grid and room boundary — only when the dev menu or
        # room editor is active. Skipping this in normal gameplay saves hundreds
        # of draw.line calls per frame.
        if self.dev_menu.active or self.room_editor.active:
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
                # Cache the rendered text surface — it never changes.
                if not hasattr(self, '_fly_hint_surf'):
                    _ft = font.render("E to Fly", True, self.colors['YELLOW'])
                    _fbg = pygame.Surface((_ft.get_width() + 10, _ft.get_height() + 5), pygame.SRCALPHA)
                    _fbg.fill((0, 0, 0, 180))
                    self._fly_hint_surf = (_ft, _fbg)
                text, bg = self._fly_hint_surf
                trect = text.get_rect(center=(px, py))
                self.logical_surface.blit(bg,   trect.inflate(10, 5).topleft)
                self.logical_surface.blit(text, trect)

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
            # During landing_fade_in the landing animation replaces the normal
            # player sprite — omit the player here so only one sprite is visible.
            _player_objs = [] if self._mjf_state == 'landing_fade_in' else [self.player]
            for obj in (self.projectiles + _player_objs + self.enemies + self.npcs
                        + self.destructible_stones + self.level_gates
                        + self.bombs + self.explosions + self.flying_pads
                        + self.save_points + self.world_map_objects):
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

            # Landing animation — replaces the normal player sprite while descending
            # into the room.  Drawn after layer_manager.draw_all (player was excluded
            # above) and before the foreground tile layer so trees/walls still occlude
            # the descending character correctly.
            if self._mjf_state == 'landing_fade_in':
                self._draw_landing_sprite()

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

        # Ghost silhouette — drawn immediately after foreground tiles so the
        # player remains readable when standing behind a fence, tree, or wall.
        self._draw_player_silhouette_if_occluded()

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
        self.character_creator.draw(self.logical_surface, self.dt)
        self.room_editor.draw(self.logical_surface)
        self.dev_menu.draw(self.logical_surface)
        self.cutscene_editor.draw(self.logical_surface)
        self.world_map_editor.draw(self.logical_surface)

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
        for tile in sorted(tiles, key=lambda t: t.layer):
            # When the editor's "Hide other layers" mode is on, only bake tiles
            # that belong to the layer currently being edited.
            if te.hide_other_layers and tile.layer != te.current_layer:
                continue

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

    def _draw_scrolling_background(self, dt):
        """Draw the current room's scrolling background image, if it has one.

        Mirrors the room editor's preview (camera-driven parallax) and adds
        the autonomous scroll_x / scroll_y motion configured in the
        Background panel, which the editor preview never animated either —
        this is the single source of truth for both editor and gameplay.
        """
        room = self.current_room
        if not room:
            return

        bg = getattr(room, 'scrolling_bg', None)
        if not bg:
            return

        img_path = bg.get('image', '')
        if not img_path:
            return

        if img_path not in self._bg_image_cache:
            try:
                import os
                raw = pygame.image.load(
                    os.path.join('assets', 'bg', os.path.basename(img_path))
                ).convert()
                sw, sh = self.logical_surface.get_size()
                ratio  = sh / raw.get_height()
                nw     = max(1, int(raw.get_width() * ratio))
                self._bg_image_cache[img_path] = pygame.transform.scale(raw, (nw, sh))
            except Exception:
                self._bg_image_cache[img_path] = None

        surf = self._bg_image_cache.get(img_path)
        if not surf:
            return

        # Advance this room's own scroll phase over time so background motion
        # keeps going independently of the camera.
        accum = self._bg_scroll_accum.setdefault(room.name, [0.0, 0.0])
        accum[0] += bg.get('scroll_x', 0.0) * dt
        accum[1] += bg.get('scroll_y', 0.0) * dt

        parallax = bg.get('parallax', 0.5)
        sw, sh   = self.logical_surface.get_size()
        iw, ih   = surf.get_size()

        off_x = int(self.camera.x * parallax + accum[0]) % iw
        off_y = int(accum[1]) % ih

        y = -off_y
        while y < sh:
            x = -off_x
            while x < sw:
                self.logical_surface.blit(surf, (x, y))
                x += iw
            y += ih

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

    def _get_foreground_tiles(self):
        """Return all foreground tiles (layer >= 0) for the current room.

        Prefers the editor's live tile list so freshly painted tiles are
        considered immediately without needing a cache rebuild.

        Result is cached per (room_name, tile_list_id) and invalidated
        automatically when the tile cache is flushed (same invalidation path
        as the baked tile surfaces).
        """
        if not self.current_room:
            return []
        te = getattr(self.room_editor, 'tileset_editor', None)
        room_name = self.current_room.name
        if te and room_name in getattr(te, 'room_tiles', {}):
            tiles = te.room_tiles[room_name]
        else:
            tiles = getattr(self.current_room, 'tiles', None) or []

        # Cache key: room name + id of the tile list (changes when tiles are
        # repainted or the room switches, matching tile-surface invalidation).
        cache_key = (room_name, id(tiles))
        cached = getattr(self, '_fg_tiles_cache', None)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

        result = [t for t in tiles if t.layer >= 0]
        self._fg_tiles_cache = (cache_key, result)
        return result

    def _draw_player_silhouette_if_occluded(self):
        """After the foreground tile layer is drawn, check whether any opaque
        foreground tile pixel overlaps the player.  If so, blit a pixel-accurate
        dark ghost so the player stays readable through walls/fences/trees.

        Tile surfaces are retrieved and scaled here, then forwarded to
        draw_player_silhouette which builds an occlusion mask from their opaque
        pixels.  Transparent tile borders are excluded, so the ghost only appears
        where a genuinely solid tile sits on top of the player sprite.
        """
        if self.active_cutscene_runtime:
            return

        fg_tiles = self._get_foreground_tiles()
        if not fg_tiles:
            return

        # Build the player's screen-space bounding rect.
        pw = int(self.player.width  * RENDER_SCALE)
        ph = int(self.player.height * RENDER_SCALE)
        px = int(self.player.x * RENDER_SCALE - self.camera.x)
        py = int(self.player.y * RENDER_SCALE - self.camera.y)
        player_rect = pygame.Rect(px - pw // 2, py - ph // 2, pw, ph)

        te = getattr(self.room_editor, 'tileset_editor', None)

        # Collect every tile whose bounding rect overlaps the player, together
        # with its scaled surface.  draw_player_silhouette will then mask the
        # silhouette to only the pixels those surfaces actually cover.
        overlapping: list = []   # [(scaled_surface | None, screen_x, screen_y)]

        for tile in fg_tiles:
            tx = int(tile.x * RENDER_SCALE - self.camera.x)
            ty = int(tile.y * RENDER_SCALE - self.camera.y)

            tw = th = TILE_SIZE * RENDER_SCALE
            if te:
                tileset = te.tileset_manager.get_tileset(tile.tileset_name)
                if tileset:
                    tw = int(tileset.tile_width * RENDER_SCALE)
                    th = int(tileset.tile_height * RENDER_SCALE)

            if not player_rect.colliderect(pygame.Rect(tx, ty, tw, th)):
                continue

            tile_surf = None
            if te and tileset:
                raw = tileset.get_tile_surface(tile.tile_x, tile.tile_y)
                if raw:
                    scale_key = (tile.tileset_name, tile.tile_x, tile.tile_y, tw, th)
                    if scale_key not in self._scaled_tile_cache:
                        self._scaled_tile_cache[scale_key] = pygame.transform.scale(raw, (tw, th))
                    tile_surf = self._scaled_tile_cache[scale_key]

            cache_key = (tile.tileset_name, tile.tile_x, tile.tile_y)
            overlapping.append((tile_surf, tx, ty, cache_key))

        if overlapping:
            self.layer_manager.draw_player_silhouette(
                self.logical_surface, self.player, self.camera,
                fg_tile_surfaces=overlapping,
            )

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
        # Suppressed entirely during the world-map flying/landing sequence so the
        # HUD is never visible while the player is descending.  It is triggered to
        # slide back in by _update_map_flying once the fade has fully cleared.
        _in_map_sequence = self._mjf_state in (
            'pending_fade_in', 'fade_in', 'flying',
            'landing_fade_out', 'landing_fade_in',
        )
        if self.ui.current_screen == 'game' and not self.character_switch_menu.active \
                and not self.pause_menu.active and not _in_map_sequence:
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

        # Map-jump fade drawn dead last so it covers every UI element including
        # the HUD — otherwise the HUD renders on top and the fade looks incomplete.
        self._draw_map_jump_fade(self.logical_surface)

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
        music_manager       = oe.music_manager

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

            if not hasattr(room, 'music_objects'):
                room.music_objects = []
            music_manager.music_objects[room.name] = room.music_objects

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
        # try/finally guarantees cleanup() fires even on an unhandled crash.
        # cleanup() flushes room data and editor state — losing that mid-session
        # would be very painful, so we always run it before the process dies.
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