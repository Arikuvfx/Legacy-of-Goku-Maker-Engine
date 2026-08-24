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
from core.flag_manager import FlagManager
from core.event_actions import EventRunner
from core.game_config import GameConfig
from core.transformation_system import TransformationSystem
from core.transition_controller import TransitionController
from dev_tools.dev_menu import DevMenu
from dev_tools.npc_config import NPCConfigMenu
from dev_tools.transition_config import TransitionConfigMenu
from entities.enemy import Enemy
from attacks.bomb_projectile import BombProjectile, ExplosionEffect
from attacks.burning_attack import BurningAttack, BurningChargeEffect, BurningHitEffect
from attacks.genkidama import GenkidamaBlast, GenkidamaHitEffect
from attacks.instant_transmission import InstantTransmissionSelector, InstantTransmissionStrike
from attacks.bullet_projectile import bullet_projectile
from attacks.rocket_projectile import rocket_projectile
from attacks.ultra_volleyball_attack import UltraVolleyballAttack
from attacks.big_bang_attack import BigBangAttackBlast, BigBangDestructionBurst
from attacks.beam import BeamAttack
from entities.npc import NPC
from entities.player import Player
from objects.room_transition import RoomTransition
from rooms.room_manager import RoomManager
from ui.dialogue import DialogueBox, DialogueChoiceMenu
from ui.spam_qte import SpamQTEBar
from ui.hud import UI
from ui.notifications import LevelUpNotification
from ui.damage_number import DamageNumberManager
from core.sound_engine import SoundEngine, SoundManager, AudioAssetLoader
from ui.sprite_hud import SpriteHUD
from core.draw_layers import LayerManager, DrawLayer
from dev_tools.sprite_editor import SpriteEditor
from dev_tools.room_editor.room_editor import RoomEditor
from objects.collision_object import CollisionObjectManager
from objects.level_gate import LevelGate
from config.utils.gate_font import get_gate_font
from objects.flying_pad import FlyingPad
from core.flypad_controller import FlyingController
from core.nimbus_controller import NimbusCloudController
from objects.save_point import SavePoint, SavePointMenu, SavePointManager
from objects.decoration_objects import Decoration
from ui.character_switch_menu import CharacterSwitchMenu
from ui.pause_menu import PauseMenu
from ui.credits_screen import CreditsScreen
from ui.scouter_menu import ScouterMenu
from core.items import ITEMS, spawn_item_pickup
from core.item_effects import use_item, tick_item_buffs, equip_item, unequip_item
from dev_tools.room_editor.room_editor_tools.mission_manager import MissionManager
from dev_tools.cutscene_editor import CutsceneEditor
from dev_tools.world_map_editor import WorldMapEditor
from dev_tools import character_creator
from dev_tools import entity_creator
from ui.title_screen import TitleScreen

# Flip to False when building an exported/player-facing release — gates the
# F1 "skip the title screen straight into the test room" dev shortcut (see
# Game._dev_skip_to_default_room / the game_mode == 'title' branch in
# handle_events). Everything else about the title screen itself — New Game,
# the intro cutscene, Quit — behaves identically either way, so this is the
# only flag that needs flipping for now. The rest of the dev tooling
# (DevMenu, room editor, etc.) isn't gated by this yet; that's a separate,
# bigger "export build" pass for later.
DEV_BUILD = True


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


class _LevelUpPlayerSpriteDrawable:
    """Stand-in for the player while the level-up animation plays.

    Registered with the LayerManager using the exact same draw_layer and
    fixed (non-Y-sorted) get_sort_key() that LayerIntegrationHelper.
    setup_player() gives the real player — see core/draw_layers.py. That
    means it slots into precisely the spot the player would occupy in the
    y-sort, so NPCs/enemies/foreground tiles keep drawing in front of or
    behind it correctly instead of the animation always landing on top of
    everything (which was the previous behavior, when it was blitted
    directly after layer_manager.draw_all() instead of going through it).

    The class name deliberately contains "Player" — LayerManager._draw_shadow
    checks type(obj).__name__ against _SHADOW_TYPES, and only draws a ground
    shadow for matching classes. That gives this the player's normal shadow
    during the animation instead of none. shadow_size/shadow_width/
    shadow_y_offset are forwarded from the real player so the shadow matches
    exactly (falling back to _draw_shadow's own defaults if the player
    doesn't set one of them).
    """

    def __init__(self, game):
        self._game = game
        self.draw_layer = DrawLayer.PLAYER
        self.y_sort = False
        self.active = True

    @property
    def x(self):
        return self._game.player.x

    @property
    def y(self):
        return self._game.player.y

    @property
    def width(self):
        return self._game.player.width

    @property
    def height(self):
        return self._game.player.height

    @property
    def shadow_size(self):
        return getattr(self._game.player, 'shadow_size', 'small')

    @property
    def shadow_width(self):
        return getattr(self._game.player, 'shadow_width', self._game.player.width)

    @property
    def shadow_y_offset(self):
        return getattr(self._game.player, 'shadow_y_offset', 0)

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def draw(self, screen, camera, colors):
        self._game._draw_levelup_sprite(screen, camera, colors)



class Game:
    """
    Top-level controller. Owns every subsystem (rendering, audio, rooms, input),
    drives the main loop, and routes communication between them.
    """

    def __init__(self):
        """
        Boot the whole game: pygame/display/audio setup, then construct every
        subsystem in dependency order (camera, room manager, player, dev tools,
        UI, mission/cutscene managers, etc.) and wire their cross-references.
        Everything the main loop touches is created here — see the section
        comments below for where each subsystem is set up.
        """
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
        # max_level is set globally via the character creator's Settings tab
        # (character_creator.save_global_settings) rather than hardcoded —
        # pull in whatever was last saved there, if anything.
        self.game_config.max_level = character_creator.load_global_settings()["max_level"]
        self.sound_engine  = SoundEngine()
        self.sound_manager = SoundManager(self.sound_engine)
        AudioAssetLoader.load_from_directory(self.sound_engine)

        # ── Player ────────────────────────────────────────────────────────────
        # Respect the saved character menu order (character_creator's
        # discover_characters()) instead of silently falling through to
        # Player.__init__'s hardcoded 'goku' default.
        roster = character_creator.discover_characters()
        starting_character = roster[0] if roster else 'goku'
        self.player = Player(
            WORLD_WIDTH // 2, WORLD_HEIGHT // 2,
            character=starting_character,
            game_config=self.game_config,
        )
        # Apply the character creator's saved config (stats, equipped attacks,
        # ki mode, and whether this character has any transformations) to the
        # live player right away. Previously equipped_attacks/has_transformation
        # were only ever set inside _reload_attack_config(), and nothing called
        # that until the character creator had been opened and closed once —
        # so on a fresh launch the player had no equipped attacks and
        # 'transform' wasn't offered as a ki mode until then.
        self.player.transformation = TransformationSystem(self.player, self.game_config)
        self.player.in_transition  = False
        self._reload_attack_config(starting_character)
        # Switchable roster for the save point's "Switch Characters" menu.
        # Being "added" (on disk / discover_characters()) no longer implies
        # playable — only starting_character is switchable to start; the rest
        # are unlocked via the 'character_list' event action.
        self.player.playable_characters = [starting_character]

        # Per-character progress store: {char_id: Player.snapshot_progress()}.
        # Populated/read by _switch_character() so each playable character
        # keeps its own independent level/XP/HP/KI/stats instead of all of
        # them sharing whatever the live Player object currently holds. Not
        # populated for starting_character here — its progress simply *is*
        # the live player object until the first switch snapshots it (see
        # _switch_character / _sync_active_character_progress).
        self.player.character_progress = {}

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
        self.dialogue_choice_menu  = DialogueChoiceMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        # Bottom-middle mash-E-or-Q QTE bar (see ui/spam_qte.py) — armed by
        # the 'spam_qte' event action, same "one shared instance, started/
        # stopped by Game" shape as dialogue_box/dialogue_choice_menu above.
        self.spam_qte_bar          = SpamQTEBar()
        self.level_up_notification = LevelUpNotification(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.flying_controller     = FlyingController(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.nimbus_controller     = NimbusCloudController(SCREEN_WIDTH, SCREEN_HEIGHT)

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
        self.world_map_editor.on_save = self._on_world_map_saved
        self.character_creator = character_creator.CharacterCreator(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.entity_creator = entity_creator.EntityCreator(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.cutscene_editor = CutsceneEditor(
            self.room_manager,
            self.room_editor,
            SCREEN_WIDTH,
            SCREEN_HEIGHT,
            dialogue_box=self.dialogue_box,
            sound_manager=self.sound_manager,
        )

        # ── Active cutscene runtime ───────────────────────────────────────────
        # Set while a cutscene is playing; None the rest of the time.
        self.active_cutscene_runtime = None
        self.dt = 0.0

        # Cutscene transition fade state machine.
        #   _csf_state:   None | 'fade_out' | 'start' | 'fade_in'
        #   _csf_alpha:   current overlay alpha (0 = transparent, 255 = full black)
        #   _csf_pending: cutscene data dict waiting to launch after fade-out
        _FADE_DUR         = 1                    # seconds for each half of the fade
        self._csf_state   = None
        self._csf_alpha   = 0.0
        self._csf_speed   = 255.0 / _FADE_DUR      # alpha units per second
        self._csf_pending = None
        self._csf_sync_player_pos = True           # see _start_cutscene's docstring

        # Callback fired once the currently-playing cutscene finishes.
        # Only set by the 'play_cutscene' EventRunner action (blocking) so
        # its action sequence can resume afterwards.
        self._cutscene_on_finished = None

        # ── Player death sequence ───────────────────────────────────────────
        # Kicks off the frame player.is_dead flips True (see Player.die(),
        # called from take_damage() once hp hits 0) and fully owns input/
        # drawing until the player dismisses the "You have died!" box.
        #   _death_state:      None | 'anim' | 'fade' | 'box'
        #     anim — death.png is playing (player.is_dead is already True,
        #            so Player.update() has frozen everything but the sprite
        #            itself); once it finishes we hold on the last frame for
        #            _DEATH_HOLD_DURATION seconds before fading.
        #     fade — screen ramps to solid black over _DEATH_FADE_DURATION s.
        #     box  — fully black; the info box is up, waiting for E.
        #   _death_hold_timer: counts up once the death animation itself has
        #                      finished, against _DEATH_HOLD_DURATION.
        #   _death_fade_alpha: current black-overlay alpha (0-255) — same
        #                      SRCALPHA-surface approach as _csf_alpha/_mjf_alpha.
        self._death_state         = None
        self._death_hold_timer    = 0.0
        self._death_fade_alpha    = 0.0
        self._DEATH_HOLD_DURATION = 3.0             # seconds to hold on the last death frame
        self._DEATH_FADE_DURATION = 1.0             # seconds for the fade-to-black
        self._death_fade_speed    = 255.0 / self._DEATH_FADE_DURATION

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
        # Eased throttle (0.0-1.0) multiplying MAP_SPD/ROTATE_SPD each frame —
        # ramps toward 1.0 while any flight key is held and back toward 0.0
        # on release, instead of snapping straight to full speed/stop. That
        # instant on/off is what read as too fast/twitchy compared to
        # Buu's Fury's smoother accel/decel feel.
        self._mjf_fly_throttle       = 0.0
        self._MJF_THROTTLE_RATE      = 2.5            # higher = snappier ramp
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
        self.ultra_volleyballs    = []   # Active UltraVolleyballAttack instances
        self.melee_attacks        = []
        self.cutscene_beams       = []   # BeamAttack instances fired by scripted 'firebeam' cutscene actions
        self.bombs                = []   # BombProjectiles from Shooter enemies
        self.masenko_projectiles  = []   # MasenkoProjectiles thrown by the player
        self.enemy_bullets        = []   # bullet_projectiles from Gunner enemies
        self.enemy_rockets        = []   # rocket_projectiles from RocketLauncher enemies
        self.enemy_kiblasts       = []   # Projectiles from kiblast-style enemies (e.g. Android 17/18)
        self.explosions           = []   # Active ExplosionEffect instances
        self.genkidama_hit_effects = []  # Active GenkidamaHitEffect instances
        self.burning_hit_effects  = []  # Active BurningHitEffect instances
        # Kept separate from self.projectiles (unlike a regular Projectile
        # or GenkidamaBlast) specifically so it never gets swept into that
        # list's generic 'projectile' collision handling below, which
        # deactivates on the first hit — BigBangAttackBlast pierces
        # instead (see its own docstring and the dedicated per-enemy
        # collision block in _update_enemies()).
        self.big_bang_attacks = []              # Active BigBangAttackBlast instances
        self.big_bang_destruction_effects = []  # Active BigBangDestructionBurst instances
        self._white_flash_timer   = 0.0  # counts down from _WHITE_FLASH_DURATION
        self._WHITE_FLASH_DURATION = 1  # seconds — genkidama impact flash + hitstop

        # Instant Transmission — non-None only while the player is actively
        # targeting (Q held in that mode). See _update_instant_transmission.
        self.it_selector = None
        self.enemies              = []
        self.npcs                 = []
        self.critters = []  # ambient wildlife: squirrels, birds, butterflies
        self.destructible_stones  = []
        self.decorations          = []  # trees, etc. — see objects/decoration_object.py
        self.collision_objects    = []
        self.room_transitions     = []
        self.level_gates          = []
        self.doors                = []
        self.chests               = []
        self.flying_pads          = []
        self.nimbus_clouds        = []
        self.world_map_objects = []
        self.music_track          = ''   # current room's persisted BGM track; set via trigger box room_music actions
        self.trigger_boxes        = []   # room's placed trigger box zones (see core/event system)
        self.zeni_pickups         = []   # dropped-zeni world pickups; see _update_zeni_pickups
        self.item_pickups         = []   # dropped-item world pickups; see _update_item_pickups
        # item_ids removed from the inventory via 'drop_item:' while the pause
        # menu is open, not yet turned into world pickups — see the
        # pause_menu.active handling below. Flushed (and the actual
        # ItemPickups spawned, at the player's position) the moment the menu
        # closes, per the request that the item only appears in the world
        # once the whole pause menu closes, not the instant Drop is confirmed.
        self._pending_dropped_items = []
        self.active_item_buffs    = []   # timed stat buffs from consumables (e.g. Holy Water); see systems/item_effects.py

        # ── Performance caches ────────────────────────────────────────────────
        # key: (room_name, is_background) → pre-baked tile Surface
        self._room_tile_surfaces: dict = {}
        # (room_name, bg) -> list of Tile instances that are animated and were
        # therefore excluded from the baked surface above; drawn dynamically
        # every frame instead. Populated as a side effect of
        # _build_room_tile_surface() and evicted alongside it.
        self._animated_tile_lists: dict = {}
        self._dirty_tile_rooms:   set  = set()   # rooms pending a full surface rebuild
        # room_name -> set of (grid_x, grid_y) world-space cells pending an
        # in-place patch (single tile placed/erased). Cheaper than a full
        # rebuild since it never reallocates the room-sized surface or
        # re-iterates every tile in the room — see _patch_tile_cell().
        self._dirty_tile_cells:  dict = {}
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

        # In-game "Save Game" flow (see _start_save_flow/_update_save_flow/
        # _draw_saving_popup) — reuses TitleScreen's own SAVE SELECT frame
        # (see TitleScreen.open_save_overlay) plus a small "Saving..."
        # popup drawn in front of it. self.save_flow_active freezes player
        # input/movement for its duration, same shape as save_point_menu.active
        # elsewhere in update().
        self.save_flow_active       = False
        self.save_flow_timer        = 0.0
        self.SAVE_FLOW_POPUP_HOLD   = 1.1   # seconds "Saving..." stays up before backing out
        # Which save slot is "current" for this play session — set once a
        # slot is actually confirmed on the title screen (see
        # _start_new_game/_dev_skip_to_default_room). Placeholder until a
        # real save/load system exists (see TitleScreen._slot_has_save_data's
        # own placeholder note) — only used so the Save Game screen scrolls
        # to the right slot instead of always slot 0.
        self.current_save_slot      = 0
        self.pause_menu            = PauseMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.pause_menu.set_sound_engine(self.sound_engine)

        # Full-screen credits sequence, opened from the pause menu's Options
        # tab (PauseMenu.handle_input returns 'open_credits'). Content lives
        # entirely in data/credits.json — see CreditsScreen's docstring.
        self.credits_screen         = CreditsScreen(SCREEN_WIDTH, SCREEN_HEIGHT)

        # ── Title screen ─────────────────────────────────────────────────────
        # Boots the player into a title screen instead of dropping them
        # straight into gameplay. While self.game_mode == 'title',
        # handle_events()/update()/draw() all early-return to this widget —
        # nothing else in the engine runs until New Game or the F1 dev
        # shortcut flips game_mode to 'playing' (see _start_new_game /
        # _dev_skip_to_default_room below). Content (title text, which
        # cutscene New Game plays) is data-driven from data/game_flow.json.
        self.title_screen = TitleScreen(SCREEN_WIDTH, SCREEN_HEIGHT)
        # Shares the pause menu's Options tab for the title screen's own
        # OPTIONS entry (see TitleScreen._confirm_menu_selection /
        # PauseMenu.open_options_only) — same fonts/box art/volume bars,
        # and it already has sound wired up via set_sound_engine() a few
        # lines above.
        self.title_screen.set_pause_menu(self.pause_menu)
        self.title_screen.set_sound_engine(self.sound_engine)
        self.title_screen.set_sound_manager(self.sound_manager)
        # Lets TitleScreen ask for each slot's save summary (or None if
        # empty) without touching disk itself — see _get_save_slot_summary.
        self.title_screen.set_save_data_provider(self._get_save_slot_summary)
        self.title_screen.open()
        self.game_mode     = 'title'   # 'title' | 'playing'

        # ENTER opens this — separate overlay from the pause menu (see
        # ui/scouter_menu.py). Mirrors the same pre-open fade-to-black /
        # instant-black-then-fade-in-on-close transition as the pause menu
        # (see _pause_fade_active / _open_pause_menu below and their
        # scouter_menu counterparts, _scouter_fade_active / _open_scouter_menu).
        self.scouter_menu          = ScouterMenu(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.scouter_menu.set_sound_engine(self.sound_engine)
        self.play_time             = 0.0   # total seconds spent in gameplay

        # Pre-pause-menu fade-to-black — mirrors the original game: ESC
        # doesn't snap straight into the pause menu, it fades the screen to
        # black first and only opens the menu once fully black (see the
        # K_ESCAPE branch in _handle_game_keydown and _open_pause_menu()
        # below). True for the duration of that fade-out so a repeated ESC
        # press can't restart/stack it.
        self._pause_fade_active    = False

        # Same pre-open fade-out gate as _pause_fade_active, but for the
        # ENTER-triggered scouter menu — see K_RETURN in _handle_game_keydown
        # and _open_scouter_menu().
        self._scouter_fade_active  = False

        # ── Event timers (timer_start/timer_pause/timer_stop actions) ─────────
        # key: timer_id → {'remaining': float seconds, 'running': bool}
        # The most recently started/resumed timer becomes the one shown on the
        # HUD — matches the "one clock on screen at a time" use case these
        # event actions are meant for (challenge/escape timers, etc.).
        self.timers: dict = {}
        self._active_timer_id = None
        # 'spam_qte' event action state — stashed on_complete for the
        # currently-running bar (see _handle_spam_qte_action/_update_spam_qte).
        # None whenever self.spam_qte_bar.active is False.
        self._event_spam_qte_on_complete = None
        self.nearby_save_point     = None
        self.nearby_chest          = None
        self.nearby_world_map_obj  = None   # WorldMapObject the player can currently interact with
        self.nearby_item_pickup    = None   # settled ItemPickup the player can currently interact with — see _update_item_pickups

        # Chest opened but still mid pickup-pose (see _handle_interact's
        # chest branch, _update_chest_pickup, _draw_chest_pickup_icon).
        # None whenever no chest sequence is in progress.
        self._pending_chest       = None
        self._pending_chest_icon  = None
        self._chest_icon_cache: dict = {}  # item_id -> Surface, mirrors PauseMenu._item_icon_cache

        # Dropped-item pickup collected but still mid pickup-pose — same
        # idea as _pending_chest above, see _handle_interact's item-pickup
        # branch, _update_item_pickup_finish, _draw_item_pickup_icon.
        self._pending_item_pickup       = None
        self._pending_item_pickup_icon  = None
        self._item_pickup_icon_cache: dict = {}  # item_id -> Surface, mirrors _chest_icon_cache

        # ── Test mode ─────────────────────────────────────────────────────────
        # Prevents save operations while a room is being previewed live.
        self.is_test_mode           = False
        self.test_room_backup       = None
        self._test_mission_snapshot = None
        self._test_flag_snapshot    = None
        self._test_wm_hidden_snapshot = None  # pre-test copy of self._wm_hidden_locations,
                                               # restored on exit so 'world_map_location'
                                               # add/remove actions fired during a room
                                               # test don't permanently affect the map

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
        oe.on_decoration_deleted       = self._on_decoration_deleted
        oe.on_gate_deleted             = self._on_gate_deleted
        oe.on_transition_placed        = self._on_transition_placed
        oe.on_transition_deleted       = self._on_transition_deleted
        oe.on_flying_pad_deleted       = self._on_flying_pad_deleted
        oe.on_flying_pad_placed        = self._on_flying_pad_placed
        oe.on_nimbus_cloud_deleted     = self._on_nimbus_cloud_deleted
        oe.on_nimbus_cloud_placed      = self._on_nimbus_cloud_placed
        oe.on_save_point_placed        = self._on_save_point_placed
        oe.on_save_point_deleted       = self._on_save_point_deleted
        oe.on_trigger_box_placed       = self._on_trigger_box_placed
        oe.on_trigger_box_deleted      = self._on_trigger_box_deleted
        if hasattr(oe, 'set_sound_manager'):
            oe.set_sound_manager(self.sound_manager)

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
        self._talking_npc             = None   # NPC currently running a mission-phase action list, if any
        self._event_dialogue_active   = False  # True while an event-triggered dialogue_box action owns the box
        self.pause_menu.set_mission_manager(self.mission_manager)

        # ── Level-up sequence ─────────────────────────────────────────────────
        # Freezes the world, spins the player through a full facing rotation,
        # plays the character's levelup.png animation twice, then chains the
        # two "reached level / stat points" dialogue boxes. See
        # _start_levelup_sequence() / _update_levelup_sequence().
        self._levelup_active     = False        # True for the whole sequence — freezes enemies/npcs/player input
        self._levelup_state      = None         # 'turning' | 'playing_anim' | None (dialogue phase)
        self._LEVELUP_TURN_SEQUENCE = ['right', 'down', 'left', 'up',
                                        'right', 'down', 'left', 'up',
                                        'right', 'down']
        self._levelup_turn_idx   = 0
        self._levelup_turn_timer = 0.0
        self._LEVELUP_TURN_DURATION = 0.12       # seconds per facing change

        self._levelup_anim_frames        = []
        self._levelup_anim_idx           = 0
        self._levelup_anim_timer         = 0.0
        self._levelup_anim_loops         = 0
        self._LEVELUP_ANIM_LOOPS_TARGET  = 2      # play the animation twice
        self._LEVELUP_ANIM_FRAME_DURATION = 0.4
        self._levelup_anim_scaled_cache  = {}
        self._levelup_drawable = _LevelUpPlayerSpriteDrawable(self)

        # Snapshot of who/what leveled up, captured at trigger time so the
        # dialogue text and sprite folder stay correct even if something
        # about the player changes while the world is frozen.
        self._levelup_char_at_trigger        = None
        self._levelup_level_at_trigger       = 1
        self._levelup_stat_points_at_trigger = 0

        # ── Flag system ────────────────────────────────────────────────────────
        # map_name -> {location name, ...} currently hidden by the
        # 'world_map_location' event action. The pin itself (x/y/room/icon/
        # height) always comes from assets/world_maps/<map>.json — this only
        # tracks which already-placed pins are toggled off.
        self._wm_hidden_locations = {}

        self.flag_manager = FlagManager()
        self.flag_manager.register_live_lookup('boss_hp_lookup', self._lookup_boss_hp_percent)
        self.flag_manager.register_live_lookup('boss_hp_value_lookup', self._lookup_boss_hp_value)
        self.flag_manager.register_live_lookup('player_has_skill', lambda skill_id: skill_id in getattr(self.player, 'equipped_attacks', []))
        self.flag_manager.register_live_lookup('player_stat', lambda stat_name: getattr(self.player, 'stats', {}).get(stat_name))
        self.flag_manager.register_live_lookup('player_character', lambda: getattr(self.player, 'character', None))
        self.flag_manager.register_live_lookup('player_zeni', lambda: getattr(self.player, 'zeni', 0))
        # player_has_item assumes self.player.inventory is a flat list of item_id
        # strings (matches the .append(item)/.append(item_id) calls elsewhere) —
        # flag if your inventory actually stores dicts/objects instead.
        self.flag_manager.register_live_lookup('player_has_item', lambda item_id: getattr(self.player, 'inventory', []).count(item_id))
        self.flag_manager.register_live_lookup('player_timer_remaining', lambda timer_id: self.timers.get(timer_id, {}).get('remaining'))
        # bar_values already lives on FlagManager itself (set_bar_percent(),
        # called every frame by _update_spam_qte) — this lookup just exposes
        # it to check_bar()/live_check() the same way every other lookup
        # exposes Game-owned state.
        self.flag_manager.register_live_lookup('player_bar_percent', self.flag_manager.get_bar_percent)
        # player_resource not wired — need the real health/energy/transformation_gauge attr names.
        self.flag_manager.set_names_refresh_callback(self._get_flag_condition_names)

        # Give the room editor a FlagManager so its object editor's "Edit
        # Event" button (conditions + actions on trigger boxes / cutscene
        # triggers) actually enables. RoomEditor.set_flag_manager() handles
        # the fact that object_editor is lazy-initialized (None until Room
        # Editor is opened for the first time) — don't reach into
        # room_editor.object_editor directly here, it may not exist yet.
        self.room_editor.set_flag_manager(self.flag_manager)

        # Scope the event editor's 'skill' action add/remove pickers to
        # whichever character is currently being played, so 'add' only
        # offers skills this character doesn't have yet and 'remove' only
        # offers what it actually has equipped. Re-synced in
        # _switch_character() and whenever the Room Editor is (re)opened,
        # since the player's character can change after this initial call.
        self._sync_event_editor_character()
        # Same idea, for the change_map action's room dropdown/Set Spawn
        # preview — see _sync_event_editor_rooms().
        self._sync_event_editor_rooms()

        # ── Event / action system ───────────────────────────────────────────────
        # Register the handlers Game can already back with real subsystems.
        # See core/event_actions.py's module docstring for the rest — each of
        # your other subsystems (dialogue, player stats/inventory, sound,
        # cutscenes, room transitions, enemy spawning...) needs one
        # self.event_runner.register_handler(...) call once its real method
        # names are known.
        self.event_runner = EventRunner()
        self.event_runner.register_handler('set_custom_variable', self._handle_set_custom_variable_action)
        self.event_runner.register_handler('world_map_location', self._handle_world_map_location_action)
        self.event_runner.register_handler('dialogue_box', self._handle_dialogue_box_action, blocking=True)
        self.event_runner.register_handler('dialogue_choice', self._handle_dialogue_choice_action)
        self.event_runner.register_handler('timer_start', self._handle_timer_start_action)
        self.event_runner.register_handler('timer_pause', self._handle_timer_pause_action)
        self.event_runner.register_handler('timer_stop', self._handle_timer_stop_action)
        self.event_runner.register_handler('zeni', self._handle_zeni_action)
        self.event_runner.register_handler('level', self._handle_level_action)
        self.event_runner.register_handler('exp', self._handle_exp_action)
        self.event_runner.register_handler('stat', self._handle_stat_action)
        self.event_runner.register_handler('resource', self._handle_resource_action)
        self.event_runner.register_handler('skill', self._handle_skill_action)
        self.event_runner.register_handler('transformation', self._handle_transformation_action)
        self.event_runner.register_handler('charged_melee', self._handle_charged_melee_action)
        self.event_runner.register_handler('character_list', self._handle_character_list_action)
        self.event_runner.register_handler('set_player_character', self._handle_set_player_character_action)
        self.event_runner.register_handler('set_player_skin', self._handle_set_player_skin_action)
        self.event_runner.register_handler('screen_fade', self._handle_screen_fade_action, blocking=True)
        self.event_runner.register_handler('screen_shake', self._handle_screen_shake_action)
        self.event_runner.register_handler('spam_qte', self._handle_spam_qte_action, blocking=True)
        self.event_runner.register_handler('weather', self._handle_weather_action)
        self.event_runner.register_handler('room_music', self._handle_room_music_action)
        self.event_runner.register_handler('play_sound', self._handle_play_sound_action)
        self.event_runner.register_handler('change_map', self._handle_change_map_action, blocking=True)
        self.event_runner.register_handler('set_player_location', self._handle_set_player_location_action)
        self.event_runner.register_handler('play_cutscene', self._handle_play_cutscene_action, blocking=True)
        self.event_runner.register_handler('item', self._handle_item_action)
        self.event_runner.register_handler('quest', self._handle_quest_action)
        self.event_runner.register_handler('modify_quest_variable', self._handle_modify_quest_variable_action)
        self.event_runner.register_handler('mission', self._handle_mission_action)

        # Create the default starting room (a fresh transient "green dev" room).
        self._create_default_room()

        # Scan all rooms for missions defined on placed NPCs.
        self.mission_manager.scan_rooms_for_missions(self.room_manager)

        # ── Flying controller callbacks ───────────────────────────────────────
        self.flying_controller.on_room_transition = self._handle_flying_room_transition
        self.flying_controller.on_flight_complete = self._handle_flying_complete
        self.flying_controller.set_sound_manager(self.sound_manager)

        # ── Nimbus cloud controller callbacks ───────────────────────────────────
        self.nimbus_controller.on_room_transition = self._handle_nimbus_room_transition
        self.nimbus_controller.on_ride_complete   = self._handle_nimbus_ride_complete
        self.nimbus_controller.set_sound_manager(self.sound_manager)
        self.nimbus_controller.set_camera(self.camera)
        # Required so mid-ride room crossings move the cloud into the destination
        # room's manager bucket (otherwise it vanishes from the drawn list).
        if hasattr(self.room_editor, 'object_editor') and self.room_editor.object_editor:
            self.nimbus_controller.set_nimbus_cloud_manager(
                self.room_editor.object_editor.nimbus_cloud_manager
            )

        # ── Transition / fade system ──────────────────────────────────────────
        self.transition_controller = TransitionController(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.flying_controller.set_transition_controller(self.transition_controller)
        self.nimbus_controller.set_transition_controller(self.transition_controller)

        # ── Ambient room weather ─────────────────────────────────────────────
        # Driven by the 'weather' event action. Separate from a cutscene's own
        # weather system (core/cutscene_runtime.py's weather_start/weather_stop
        # actions) — reuses that module's _WeatherEffect class so the art and
        # fade timings match, but this instance persists outside cutscenes.
        self.room_weather               = None
        self._room_weather_fade_from    = 0.0
        self._room_weather_fade_to      = 0.0
        self._room_weather_fade_dur     = 0.0
        self._room_weather_fade_elapsed = 0.0

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

    # ── Title screen → gameplay ─────────────────────────────────────────────

    def _load_game_flow(self):
        """Load data/game_flow.json — see that file's own '_readme' for the
        format. Missing file / bad JSON both just fall back to an empty
        flow (no intro cutscene configured) rather than crashing, same
        tolerant-of-missing-data approach as _load_cutscene_data."""
        import os, json
        path = os.path.join('data', 'game_flow.json')
        if not os.path.exists(path):
            print(f'[Game] no game_flow.json found at {path} — '
                  f'New Game will start in the default room with no intro cutscene')
            return {}
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f'[Game] failed to load game_flow.json: {e}')
            import traceback; traceback.print_exc()
            return {}

    def _start_new_game(self):
        self.title_screen.close()
        self.game_mode = 'playing'
        # Remember which slot was picked on SAVE SELECT so later save-pad
        # saves scroll to it (see TitleScreen.open_save_overlay).
        self.current_save_slot = self.title_screen.get_selected_save_slot()

        flow = self._load_game_flow()
        cutscene_id = (flow.get('intro_cutscene') or '').strip()
        if not cutscene_id:
            print('[Game] no intro_cutscene configured in game_flow.json — '
                  'starting directly in the default room')
            return

        cutscene_data = self._load_cutscene_data(cutscene_id)
        if cutscene_data is None:
            print(f'[Game] intro cutscene "{cutscene_id}" failed to load — '
                  f'starting directly in the default room')
            return

        # already_faded=True: the save-select screen already faded itself
        # to black before game.py ever got the 'new_game' signal (see
        # title_screen.py's _exit_pending/consume_exit_signal), so this
        # would otherwise fade out a second time on top of an already-black
        # screen.
        # sync_player_position=False: self.player has never actually been
        # placed in a room yet at this point, so its x/y are just the
        # boot-time WORLD_WIDTH//2, WORLD_HEIGHT//2 default from
        # Game.__init__ — syncing to that would yank actor_0 off its
        # authored position and off camera. Let it stay where the
        # cutscene itself placed it.
        self._start_cutscene(cutscene_data, already_faded=True, sync_player_position=False)

    # ── Save / Load ──────────────────────────────────────────────────────────
    # Slot-based save files under saves/slot_<n>.json. TitleScreen never
    # touches disk itself — it asks Game for each slot's summary via the
    # provider handed to set_save_data_provider() (see __init__), and
    # relies on Game to actually restore state once a slot with data is
    # confirmed (see consume_exit_signal()'s 'load_game' branch below).

    SAVE_DIR = 'saves'

    def _save_slot_path(self, slot_index):
        import os
        return os.path.join(self.SAVE_DIR, f'slot_{slot_index}.json')

    def _write_save_slot(self, slot_index):
        """Serializes the current game state to disk for slot_index. Called
        once the "Saving..." popup's hold timer finishes (see
        _update_save_flow) — previously a TODO with nothing behind it.

        Player fields mirror exactly what _switch_character already
        snapshots/restores for a character swap (x/y/hp/ki/level/stats/
        inventory/zeni), plus the roster/current-character/costume info
        _start_save_flow already gathers for the overlay display, plus a
        flags/missions snapshot via FlagManager.snapshot()/MissionManager.
        snapshot() — the same snapshot format already used for the
        test-mode revert flow (see _handle_test_room), so it's known-good
        JSON-serializable data rather than a guess."""
        import os, json

        # Checkpoint whoever's currently active into character_progress
        # before serializing it below — otherwise whatever XP/levels/stats
        # they've earned since the last character switch would never make
        # it into character_progress (it's only written on switch) and
        # would be lost on save/reload.
        self._sync_active_character_progress(getattr(self.player, 'character', None))

        ts = self.player.transformation
        current_costume = (ts.current_transform_costume
                            if (ts and ts.is_transformed and ts.current_transform_costume)
                            else 'base')
        # 'current_costume' above is for display only (see
        # _get_save_slot_summary → TitleScreen's slot preview). The
        # actual restorable transformation state lives in
        # data['transformation'] below, built straight from
        # TransformationSystem's own fields (see transformation_system.py)
        # — is_transforming/is_untransforming are deliberately NOT among
        # them; those are just mid-animation flags with nowhere sensible
        # to resume from, so a save always settles to either fully
        # transformed or fully not on load (see _start_loaded_game).
        transform_data = None
        if ts is not None:
            transform_data = {
                'is_transformed':            ts.is_transformed,
                'current_transform_costume': ts.current_transform_costume,
                'original_character':        ts.original_character,
                'original_costume':          ts.original_costume,
                'transformed_ki':            ts.transformed_ki,
                'max_transformed_ki':        ts.max_transformed_ki,
                'progress':                  ts.progress,
                'is_ready':                  ts.is_ready,
                'is_shining':                ts.is_shining,
                'shine_timer':               ts.shine_timer,
                'ready_notification_shown':  ts.ready_notification_shown,
            }

        data = {
            'version':              1,
            'saved_at':             time.time(),
            'room_name':            self.current_room.name if self.current_room else '',
            'player_x':             self.player.x,
            'player_y':             self.player.y,
            'current_character':    getattr(self.player, 'character', None),
            'current_costume':      current_costume,
            'playable_characters':  list(getattr(self.player, 'playable_characters', [])),
            'play_time':            self.play_time,
            'level':                getattr(self.player, 'level', 1),
            'hp':                   getattr(self.player, 'hp', None),
            'max_hp':               getattr(self.player, 'max_hp', None),
            'ki':                   getattr(self.player, 'ki', None),
            'max_ki':               getattr(self.player, 'max_ki', None),
            'stats':                dict(getattr(self.player, 'stats', {}) or {}),
            'zeni':                 getattr(self.player, 'zeni', 0),
            'inventory':            list(getattr(self.player, 'inventory', []) or []),
            'equipped_attacks':     list(getattr(self.player, 'equipped_attacks', []) or []),
            'transformation':       transform_data,
            # Per-character level/XP/HP/KI/stats for every character that's
            # been played at least once this save (see Player.PROGRESS_
            # FIELDS / snapshot_progress). The top-level 'level'/'hp'/
            # 'stats'/etc. fields above still mirror current_character's
            # numbers for older tooling that reads them directly — they're
            # kept in sync with character_progress[current_character] since
            # _sync_active_character_progress() was just called above.
            'character_progress':  dict(getattr(self.player, 'character_progress', {}) or {}),
        }

        # Equipment slots (body/hands/feet/accessory) — player.equipped is
        # a plain {slot_key: item_id} dict (see pause_menu.py's
        # EQUIP_SLOT_KEYS / _confirm_equip_action), confirmed JSON-safe.
        # Doesn't exist as an attribute at all until the player's first
        # equip (getattr default below), hence the None check rather than
        # hasattr. The try/except is just a defensive net in case that
        # shape ever changes — not expected to trigger.
        equipped = getattr(self.player, 'equipped', None)
        if equipped is not None:
            try:
                json.dumps(equipped)
                data['equipped'] = equipped
            except (TypeError, ValueError):
                print('[Game] player.equipped isn\'t JSON-safe — skipped from this save '
                      '(equipment will need its own serialization once its real shape is confirmed)')

        try:
            data['flags'] = self.flag_manager.snapshot()
        except Exception as e:
            print(f'[Game] failed to snapshot flags for save: {e}')
            data['flags'] = None
        try:
            data['missions'] = self.mission_manager.snapshot()
        except Exception as e:
            print(f'[Game] failed to snapshot missions for save: {e}')
            data['missions'] = None

        try:
            os.makedirs(self.SAVE_DIR, exist_ok=True)
            with open(self._save_slot_path(slot_index), 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception as e:
            print(f'[Game] failed to write save slot {slot_index}: {e}')
            import traceback; traceback.print_exc()

    def _read_save_slot(self, slot_index):
        """Full save payload for slot_index (see _write_save_slot), or None
        if the slot is empty/unreadable. Missing file / bad JSON both just
        return None rather than crash — same tolerant-of-missing-data
        approach as _load_game_flow."""
        import os, json
        path = self._save_slot_path(slot_index)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f'[Game] failed to read save slot {slot_index}: {e}')
            return None

    def _get_save_slot_summary(self, slot_index):
        """Provider callback handed to TitleScreen.set_save_data_provider —
        called from _slot_has_save_data and the normal SAVE SELECT list
        (see title_screen.py's _draw_save_slot_list) to decide whether to
        draw "New Game" or this slot's actual Saga/Room/Time info, and
        from _confirm_save_slot to decide whether A-Select should start a
        new game or load this one. Returns None for an empty slot."""
        data = self._read_save_slot(slot_index)
        if data is None:
            return None
        return {
            'room_name':          data.get('room_name', ''),
            'play_time':          data.get('play_time', 0.0),
            'characters':         list(data.get('playable_characters', [])),
            'current_character':  data.get('current_character'),
            'current_costume':    data.get('current_costume', 'base'),
        }

    def _start_loaded_game(self, slot_index):
        """The "load" counterpart to _start_new_game — taken when
        TitleScreen's SAVE SELECT confirms a slot _get_save_slot_summary
        reported as occupied (see title_screen.py's _confirm_save_slot).
        Restores flags/missions, the player's roster/stats/inventory, and
        drops them back into their saved room at their saved position —
        same room-swap plumbing _handle_flying_room_transition uses for a
        normal mid-game room change, minus the flight-specific bits."""
        data = self._read_save_slot(slot_index)

        if data is None:
            # Save vanished/corrupted between the menu showing it and this
            # actually running — fall back to a fresh game on this slot
            # rather than crash or strand the player on a black screen.
            # _start_new_game() handles its own title_screen.close() /
            # slot bookkeeping, so just defer to it untouched rather than
            # half-close title_screen here first.
            print(f'[Game] save slot {slot_index} could not be loaded — starting a new game instead')
            self._start_new_game()
            return

        self.title_screen.close()
        self.game_mode         = 'playing'
        self.current_save_slot = slot_index

        flags = data.get('flags')
        if flags is not None:
            try:
                self.flag_manager.restore(flags)
            except Exception as e:
                print(f'[Game] failed to restore flags for slot {slot_index}: {e}')
        missions = data.get('missions')
        if missions is not None:
            try:
                self.mission_manager.restore(missions)
            except Exception as e:
                print(f'[Game] failed to restore missions for slot {slot_index}: {e}')

        self.player.playable_characters = list(data.get('playable_characters') or [self.player.character])

        # Per-character level/XP/HP/KI/stats (see Player.snapshot_progress /
        # Game._switch_character). Older saves, written before per-character
        # progression existed, won't have this key at all — a fallback entry
        # for whichever character this save was actually played as gets
        # synthesized from the legacy flat top-level fields a little further
        # down, once saved_character is known.
        self.player.character_progress = dict(data.get('character_progress') or {})

        self.player.inventory           = list(data.get('inventory', []))
        self.player.zeni                = data.get('zeni', getattr(self.player, 'zeni', 0))
        self.player.equipped_attacks    = list(data.get('equipped_attacks', getattr(self.player, 'equipped_attacks', [])))
        # Just replay the {slot_key: item_id} dict as data — do NOT
        # re-run core.item_effects.equip_item() for each entry. player.py
        # documents self.stats as boosted in place ("Base stats — all
        # start at 1; boosted through stat points"), and equip_item()
        # applies an equipped item's stat bonus the same permanent way —
        # so the 'stats' dict restored just below already has any
        # equipment bonuses baked in from when this save was written.
        # Re-calling equip_item() here would apply that bonus a second
        # time on top of the already-boosted restored stats.
        if data.get('equipped') is not None:
            self.player.equipped = data['equipped']
        self.play_time = data.get('play_time', 0.0)

        saved_character = data.get('current_character')

        # If the player picked a different character to load as on the
        # title screen's SAVE SELECT list (see title_screen.py's
        # _draw_slot_character_row show_picker branch / get_selected_
        # character), that pick wins over whichever character this save
        # was actually last played as — same "reads it once right after
        # closing the title screen" pattern _start_new_game already uses
        # for get_selected_save_slot(). Only honored if it's actually one
        # of this save's own unlocked characters, so a stale pick from a
        # previously-viewed slot can never load a character this save
        # never had.
        picked_character = self.title_screen.get_selected_character()
        if picked_character and picked_character in (data.get('playable_characters') or []):
            saved_character = picked_character

        # Legacy-save fallback: pre-per-character-progression saves have no
        # 'character_progress' entry for saved_character at all — synthesize
        # one from the old flat top-level level/hp/stats/etc. fields so
        # those saves still load with the numbers they were saved with,
        # instead of falling through to a fresh level-1 start.
        if saved_character and saved_character not in self.player.character_progress:
            legacy_fields = ('level', 'hp', 'max_hp', 'ki', 'max_ki', 'stats')
            if any(f in data for f in legacy_fields):
                legacy_level = data.get('level', 1)
                self.player.character_progress[saved_character] = {
                    'level':             legacy_level,
                    'exp':               data.get('exp', 0),
                    'total_exp':         data.get('total_exp', data.get('exp', 0)),
                    'exp_to_next_level': data.get('exp_to_next_level',
                                                   self.game_config.get_xp_for_level(legacy_level)),
                    'stat_points':       data.get('stat_points', 0),
                    'pending_level_up':  data.get('pending_level_up', False),
                    'hp':                data.get('hp', data.get('max_hp', 100)),
                    'max_hp':            data.get('max_hp', 100),
                    'ki':                data.get('ki', data.get('max_ki', 100)),
                    'max_ki':            data.get('max_ki', 100),
                    'stats':             dict(data.get('stats') or {}),
                }

        if saved_character and saved_character != getattr(self.player, 'character', None):
            # sync_previous=False — the player object we're switching away
            # from here is just Game.__init__'s fresh boot-time placeholder,
            # not a character actually played this session, so it has
            # nothing worth checkpointing into character_progress (and
            # doing so would clobber a real loaded entry for that same id).
            self._switch_character(saved_character, sync_previous=False)
        elif saved_character:
            # No switch needed (already the right character) — but the live
            # player object still only has Game.__init__'s fresh boot-time
            # numbers, so apply this character's loaded progress directly.
            progress = self.player.character_progress.get(saved_character)
            if progress:
                self.player.restore_progress(progress)

        # Transformation state — restored AFTER any character switch above
        # so the transformed sprite set here doesn't get immediately
        # clobbered by _switch_character's own base-costume sprite swap.
        # Only the settled is_transformed boolean is honored (see
        # _write_save_slot — is_transforming/is_untransforming were never
        # saved), so this always lands the player either fully
        # transformed or fully not, never mid-animation. Mirrors
        # TransformationSystem.complete_transform()'s own end state
        # (sprite swap + 'idle' animation) rather than replaying the
        # transform animation itself.
        transform_data = data.get('transformation')
        ts = self.player.transformation
        if ts is not None and transform_data:
            ts.is_transforming   = False
            ts.is_untransforming = False
            ts.original_character = transform_data.get('original_character')
            ts.original_costume   = transform_data.get('original_costume')
            ts.max_transformed_ki = transform_data.get('max_transformed_ki', ts.max_transformed_ki)
            ts.transformed_ki     = transform_data.get('transformed_ki', ts.max_transformed_ki)

            if transform_data.get('is_transformed'):
                ts.is_transformed            = True
                ts.current_transform_costume = transform_data.get('current_transform_costume')
                # Meter's irrelevant while transformed (see update()'s own
                # early-return) — reset it clean rather than carry over
                # whatever it happened to read at save time.
                ts.progress   = 0.0
                ts.is_ready   = False
                ts.is_shining = False
                ts.shine_timer = 0.0

                transform_costume = ts.current_transform_costume
                if transform_costume:
                    from core.sprite_system import create_character_sprite
                    character = ts.original_character or getattr(self.player, 'character', None) or 'goku'
                    self.player.sprite = create_character_sprite(character, transform_costume, 32, 32)
                    self.player.sprite.set_animation('idle', self.player.direction)
                    self.player.current_animation_state = 'idle'
                else:
                    # No costume path saved for this transformation — can't
                    # know which sprite to load, so don't leave the player
                    # stuck on a placeholder; fall back to standing in base
                    # form instead (same "nowhere to land" bailout
                    # start_transform() itself uses).
                    ts.is_transformed = False
            else:
                ts.is_transformed           = False
                ts.progress                 = transform_data.get('progress', 0.0)
                ts.is_ready                 = transform_data.get('is_ready', False)
                ts.is_shining               = transform_data.get('is_shining', False)
                ts.shine_timer              = transform_data.get('shine_timer', 0.0)
                ts.ready_notification_shown = transform_data.get('ready_notification_shown', False)

        # NOTE: level/XP/HP/KI/stats are now restored above as part of
        # per-character progress (character_progress / restore_progress),
        # not from the flat top-level fields directly — those flat fields
        # still exist in the save data (for older tooling / debugging) but
        # are only *read* here as the legacy-fallback seed a few lines up.
        self.player.update_derived_stats()

        room_name = data.get('room_name') or ''
        room = self.room_manager.get_room_by_name(room_name) if room_name else None
        if room is None:
            # Saved room no longer exists (renamed/deleted since this save
            # was written) — stay wherever the engine already has a room
            # loaded rather than crash.
            print(f'[Game] save slot {slot_index}: room "{room_name}" not found — staying in the current room')
            room = self.current_room

        if room is not None:
            self.room_manager.current_room = room
            self.current_room              = room
            self._load_room_objects(room)
            self.flag_manager.mark_room_visited(room.name)

            self.player.x = data.get('player_x', self.player.x)
            self.player.y = data.get('player_y', self.player.y)
            # update(..., dt=0) rather than hand-computing camera.x/y here:
            # dt<=0 makes update() skip the smooth-follow ease entirely, so
            # this both positions the camera correctly *this* frame and
            # resets its internal _true_x/_true_y to match — a plain
            # camera.x/y assignment would only do the former, leaving the
            # next tracked update() to lerp in from wherever the camera's
            # internal position happened to be before the load.
            self.camera.update(self.player, room.width, room.height, dt=0)

    def _dev_skip_to_default_room(self):
        """Dev-only shortcut (F1 while the title screen is up, gated by
        DEV_BUILD) — jumps straight into gameplay in the existing
        _create_default_room() test room, bypassing New Game and the
        intro cutscene entirely. This is exactly the old pre-title-screen
        boot behavior, kept around so iterating on gameplay doesn't mean
        re-watching the intro cutscene on every single launch.
        """
        self.title_screen.close()
        self.game_mode = 'playing'
        self.current_save_slot = self.title_screen.get_selected_save_slot()

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

            # ── Title screen ────────────────────────────────────────────────
            # Nothing below this — no overlays, no gameplay input — runs
            # while the title screen is up. F1 is the dev-only shortcut to
            # skip straight to the boot-time test room (see
            # _dev_skip_to_default_room's docstring for why this exists).
            if self.game_mode == 'title':
                if DEV_BUILD and event.type == pygame.KEYDOWN and event.key == pygame.K_F1:
                    self._dev_skip_to_default_room()
                else:
                    result = self.title_screen.handle_input(event)
                    # 'new_game' is NOT handled here anymore — confirming
                    # New Game now starts a fade-to-black inside
                    # title_screen.py first, and self.update() picks up
                    # the actual 'new_game' signal via
                    # title_screen.consume_exit_signal() once that fade
                    # completes (see the game_mode == 'title' branch in
                    # update()). 'quit' has no cutscene/fade to wait on,
                    # so it's still handled the instant it's pressed.
                    if result == 'quit':
                        self.running = False
                continue

            # ── Overlay priority pass ─────────────────────────────────────────
            # Each active overlay grabs the event and issues continue so nothing
            # below it processes the same input.

            # Death sequence — absolute top priority. Swallows every event from
            # the moment the death animation starts (player.is_dead True) until
            # the game-over box is dismissed, so nothing (pause menu, attacks,
            # dev tools) can interrupt it. Only reacts to E, and only once the
            # box itself is showing — see _update_death_sequence for the state
            # machine and _advance_death_box/_close_death_box for what E does.
            if self._death_state is not None:
                if (self._death_state == 'box' and event.type == pygame.KEYDOWN
                        and event.key == pygame.K_e):
                    self._advance_death_box()
                continue

            if self.character_switch_menu.active:
                result = self.character_switch_menu.handle_input(event)
                if result and result != 'close':
                    self._switch_character(result)
                continue

            # Credits sequence — opened from the pause menu (still open,
            # paused underneath) via 'open_credits'. Checked above
            # self.pause_menu.active so the same keypress/click doesn't
            # also get processed by the pause menu it's layered on top of.
            if self.credits_screen.active:
                result = self.credits_screen.handle_input(event)
                if result == 'close':
                    self.credits_screen.close()
                continue

            if self.pause_menu.active:
                result = self.pause_menu.handle_input(event)
                if result == 'open_credits':
                    self.credits_screen.open()
                elif result and result.startswith('use_item:'):
                    item_id = result.split(':', 1)[1]
                    # outcome.message is deliberately not shown anywhere —
                    # using an item from the inventory shouldn't pop up any
                    # feedback text (see PauseMenu.flash_item_message,
                    # still defined/available if that's ever wanted back).
                    use_item(self.player, item_id, self.active_item_buffs)
                elif result and result.startswith('equip_item:'):
                    item_id = result.split(':', 1)[1]
                    equip_item(self.player, item_id)
                elif result and result.startswith('unequip_item:'):
                    slot = result.split(':', 1)[1]
                    unequip_item(self.player, slot)
                elif result and result.startswith('drop_item:'):
                    item_id = result.split(':', 1)[1]
                    inventory = getattr(self.player, 'inventory', None)
                    if inventory and item_id in inventory:
                        inventory.remove(item_id)
                    # Dropping the last owned copy of a currently-equipped
                    # item leaves player.equipped pointing at something no
                    # longer in the inventory — the equip slot list would
                    # keep showing it as equipped (see pause_menu.py's
                    # _draw_equip_slot_list, which reads player.equipped
                    # directly). Only unequip once no copies remain; if the
                    # player still owns another copy, it stays equipped.
                    if not inventory or item_id not in inventory:
                        equipped = getattr(self.player, 'equipped', None) or {}
                        for slot, equipped_id in list(equipped.items()):
                            if equipped_id == item_id:
                                unequip_item(self.player, slot)
                    self._pending_dropped_items.append(item_id)
                elif result == 'close':
                    # Pause menu just closed (see PauseMenu.close(), called
                    # internally before this is returned) — this is the
                    # moment anything dropped during this session actually
                    # appears in the world, tossed out from the player's
                    # current position/room. See spawn_item_pickup /
                    # ItemPickup in core/items.py for the hop/bounce
                    # animation and despawn-on-room-leave behaviour.
                    for item_id in self._pending_dropped_items:
                        self.item_pickups.append(
                            spawn_item_pickup(item_id, self.player.x, self.player.y))
                    self._pending_dropped_items = []
                    # Mirror the original game's close transition: the menu
                    # itself closes instantly (screen is already fully black
                    # from the pre-open fade-out — see _open_pause_menu),
                    # holds on that black for a beat, then fades back in to
                    # reveal gameplay again. The hold is intentional — it's
                    # what gives the close its weight vs. the near-instant
                    # open. Tune _PAUSE_CLOSE_HOLD/_PAUSE_CLOSE_FADE below to
                    # adjust the feel.
                    _PAUSE_CLOSE_HOLD = 0.5
                    _PAUSE_CLOSE_FADE = 0.25
                    self.transition_controller.start_plain_fade(
                        'in', _PAUSE_CLOSE_FADE, None, hold=_PAUSE_CLOSE_HOLD)
                continue

            if self.scouter_menu.active:
                result = self.scouter_menu.handle_input(event)
                if result == 'enter_scouter':
                    # MAP -> SCOUTER transition — snapshot every on-screen
                    # entity's frozen screen position right now (see
                    # ScouterMenu.build_scouter_snapshot's docstring for why
                    # this can't just be done continuously in update()).
                    self.scouter_menu.build_scouter_snapshot(
                        self.player, self.npcs, self.enemies, self.camera, RENDER_SCALE,
                        self.colors, self.layer_manager)
                elif isinstance(result, tuple) and result[0] == 'inspect':
                    # Per-entity inspect panel isn't built yet — this is the
                    # hook point for it. See ScouterMenu.handle_input's
                    # docstring.
                    _, _inspected_obj, _inspected_kind = result
                elif result == 'close':
                    # Same instant-black-hold-then-fade-in close transition
                    # as the pause menu — see the pause_menu 'close' handling
                    # just above for why the hold gives the close its weight.
                    _SCOUTER_CLOSE_HOLD = 0.5
                    _SCOUTER_CLOSE_FADE = 0.25
                    self.transition_controller.start_plain_fade(
                        'in', _SCOUTER_CLOSE_FADE, None, hold=_SCOUTER_CLOSE_HOLD)
                continue

            # Spam QTE ('spam_qte' event action) — while a bar is active it
            # owns E/Q entirely (queues a press for the bar instead of
            # firing melee/ki-blast) and swallows every other input too, so
            # nothing else can run mid-QTE. Placed below the pause menu so
            # the player can still pause out, same as the dialogue/menu
            # overlays below.
            if self.spam_qte_bar.active:
                if event.type == pygame.KEYDOWN and event.key in (pygame.K_e, pygame.K_q):
                    self.spam_qte_bar.register_press()
                continue

            if self.dialogue_choice_menu.active:
                self.dialogue_choice_menu.handle_input(event)
                continue

            if self.save_point_menu.active:
                result = self.save_point_menu.handle_input(event)
                if result == 'save':
                    self.save_point_menu.close()
                    self._start_save_flow()
                elif result == 'switch_characters':
                    self.save_point_menu.close()
                    current_character = getattr(self.player, 'character', 'goku')
                    # Since player.update() is frozen for the duration the menu
                    # is open (see the main update loop), snap to plain idle
                    # now — otherwise the player could be left showing
                    # idle_transition/idle_wait (or any other state) frozen
                    # mid-animation for as long as the menu stays open.
                    self.player.enter_idle()
                    self.character_switch_menu.open(current_character, self.player.playable_characters)
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
                result = self.character_creator.handle_input(event)
                if result == 'close' and hasattr(self.player, 'character'):
                    # Pick up any config the player just saved for the
                    # character they're currently playing as — otherwise
                    # equipped attacks stay stale until they visit the
                    # character-switch menu.
                    self._reload_attack_config(self.player.character)
                    # max_level is global (Settings tab), not per-character,
                    # so it isn't covered by _reload_attack_config above —
                    # re-sync it here too, otherwise a change made in the
                    # creator only takes effect on the next full game
                    # restart, since GameConfig is only built once at launch.
                    new_max_level = character_creator.load_global_settings()['max_level']
                    if new_max_level != self.game_config.max_level:
                        self.game_config.max_level = new_max_level
                        # If the cap just got lowered below the player's
                        # current level, pull them back down to it; either
                        # way, exp_to_next_level needs recomputing since it
                        # depends on self.player.level, which may have
                        # just changed.
                        self.player.level = min(self.player.level, new_max_level)
                        self.player.exp_to_next_level = self.game_config.get_xp_for_level(self.player.level)
                continue

            if self.entity_creator.active:
                self.entity_creator.handle_input(event)
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
                    self._sync_event_editor_character()
                    self._sync_event_editor_rooms()
                elif result == 'open_sprite_editor':
                    self.dev_menu.active = False
                    self.sprite_editor.toggle()
                elif result == 'open_cutscene_editor':
                    self.dev_menu.active = False
                    self.cutscene_editor.toggle()
                elif result == 'open_world_map_editor':
                    self.dev_menu.active = False
                    self.world_map_editor.toggle()
                    # Bust the cached room->map index (see
                    # _get_world_map_room_index) so pin edits made in this
                    # editor session are picked up by the Scouter's World
                    # Map section the moment the editor is closed again,
                    # instead of needing a full restart.
                    self._wm_room_index = None
                elif result == 'open_character_creator':
                    self.dev_menu.active = False
                    self.character_creator.toggle()
                elif result == 'open_entity_creator':
                    self.dev_menu.active = False
                    self.entity_creator.toggle()

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
                    if event.key == pygame.K_f:
                        self.player.stop_blocking()
                    # Charged Melee: releasing E either fires the lunge/spin
                    # (if the white overlay has already reached full opacity
                    # once — see charged_melee_ready) or cancels the wind-up
                    # early (same "early release cancels" shape as the
                    # energy sword charge) if it hasn't peaked yet. Doesn't
                    # touch the lunge/spin action itself once that's
                    # started — that runs to completion regardless of key
                    # state, like the sword spin's own free duration.
                    if event.key == pygame.K_e:
                        self.player.is_e_pressed = False
                        if self.player.is_charging_melee:
                            if self.player.charged_melee_ready:
                                self.player.release_charged_melee()
                            else:
                                self.player.cancel_charging_melee()
                    # Release the beam charge when Q is lifted without having fired.
                    if event.key == pygame.K_q:
                        self.player.is_q_pressed = False
                        if self.player.is_charging_beam and not self.player.is_firing_beam:
                            self.player.stop_beam()
                        elif self.player.is_charging_kamekameha and not self.player.is_firing_kamekameha:
                            self.player.stop_kamekameha()
                        elif self.player.is_charging_banshee_blast and not self.player.is_firing_banshee_blast:
                            self.player.stop_banshee_blast()
                        elif self.player.is_charging_final_flash and not self.player.is_firing_final_flash:
                            self.player.stop_final_flash()
                        elif self.player.is_charging_big_bang_kamehameha and not self.player.is_firing_big_bang_kamehameha:
                            self.player.stop_big_bang_kamehameha()
                        # Genkidama fires on release, at whatever state was reached.
                        elif self.player.is_charging_genkidama:
                            blast = self.player.release_genkidama()
                            if blast:
                                self.projectiles.append(blast)
                                self.sound_manager.play_sfx(random.choice(('kiblast1', 'kiblast2')))
                        # Big Bang Attack also fires on release, but always the
                        # same single blast (see release_big_bang_attack()) —
                        # appended to its own big_bang_attacks list rather than
                        # self.projectiles, since it pierces instead of being
                        # consumed on the first hit (see _update_enemies()'s
                        # dedicated collision block for it).
                        elif self.player.is_charging_big_bang_attack:
                            blast = self.player.release_big_bang_attack()
                            if blast:
                                self.big_bang_attacks.append(blast)
                                self.sound_manager.play_sfx(random.choice(('kiblast1', 'kiblast2')))
                        # Burning attack fires on release too, same as genkidama.
                        elif self.player.is_charging_burning:
                            burning = self.player.release_burning()
                            if burning:
                                self.projectiles.append(burning)
                                self.sound_manager.play_sfx(random.choice(('kiblast1', 'kiblast2')))
                        # Flame Kamehameha now charges on hold like beam/burning
                        # (see start_charging_flame_kamehameha/
                        # update_flame_kamehameha_charge/fire_flame_kamehameha_auto
                        # in player.py) — releasing Q either cancels the charge
                        # before it completes, or ends the fired attack outright
                        # (no decay sweep either way, see
                        # FlameKamehamehaAttack.stop()).
                        elif self.player.is_charging_flame_kamehameha or self.player.current_flame_kamehameha:
                            self.player.stop_flame_kamehameha()
                        # Masenko always throws on release, at the indicator's position.
                        elif self.player.is_charging_masenko:
                            masenko = self.player.throw_masenko()
                            if masenko:
                                self.masenko_projectiles.append(masenko)
                                self.sound_manager.play_sfx(random.choice(('kiblast1', 'kiblast2')))
                        # Energy sword: releasing early cancels the draw. Once the
                        # charge finishes it auto-starts the (free, fixed-length)
                        # spin, which isn't tied to the key at all — nothing to
                        # do here in that case.
                        elif self.player.is_charging_sword:
                            self.player.stop_charging_sword()
                        # Instant Transmission teleports to every selected
                        # enemy in pick order, then back home, on release.
                        elif self.player.is_targeting_it:
                            targets = self.it_selector.selected_enemies if self.it_selector else []
                            self.player.begin_teleport_sequence(targets)
                            self.it_selector = None

    def _handle_game_keydown(self, event):
        """Key-down events during normal gameplay.

        Keybindings at a glance:
          F1      — dev menu toggle
          F2      — exit test mode (returns to room editor)
          ESC     — pause menu
          WASD    — move; double-tap a direction to start running
          Shift   — hold to run
          E       — interact / melee / advance dialogue
          F       — hold to block: no other actions, damage halved,
                    knockback reduced to a 1px nudge, no hurt animation/flash
          Q       — ki blast (blast mode), begin beam charge (beam mode),
                    begin Kamekameha charge (kamekameha mode), begin
                    Banshee Blast charge (banshee_blast mode), begin
                    Final Flash charge (final_flash mode), begin Big Bang
                    Kamehameha charge (big_bang_kamehameha mode), begin
                    genkidama charge (genkidama mode), begin Big Bang
                    Attack charge (big_bang_attack mode — single fixed
                    power level, unlike genkidama's escalating one), begin
                    masenko charge (masenko mode), begin flame kamehameha
                    charge (flame_kamehameha mode), throw an instant Energy
                    Punch (energy_punch mode, no charge-up), begin
                    drawing the energy sword (sword mode) — completing the
                    draw auto-starts a short free spin — or launch the
                    Dragon Fist (dragon_fist mode, no charge-up, held for
                    as long as Q stays down), summon the Ghost
                    Kamikaze Attack (ghost_kamikaze mode, no charge-up —
                    plays out entirely on its own afterward, see
                    Player.start_ghost_kamikaze), or trigger a
                    transformation (transform mode, when fully charged) —
                    or detransform if already transformed
          TAB     — cycle ki mode: blast → beam → kamekameha → banshee_blast → final_flash → big_bang_kamehameha → genkidama → big_bang_attack → masenko → burning → flame_kamehameha → ultra_volleyball → sword → energy_punch → dragon_fist → ghost_kamikaze → instant_transmission → transform → blast
        """
        # Dialogue boxes (NPC/event/chest/item-pickup), the level-up
        # sequence, and the save flow all freeze player movement/update
        # elsewhere (see the gating conditions in update()), but they were
        # never in the handle_events() overlay-priority pass, so keydown
        # events kept reaching this handler unfiltered — letting Q/TAB/X/F
        # fire off ki attacks (kamehameha etc.), cycle ki mode, transform,
        # or start blocking while one of those was up. Swallow those
        # attack-capable keys here. E is exempted — _handle_interact()
        # already knows how to advance/close an open dialogue box, and
        # doesn't fall back to a melee swing while one is active.
        _ATTACK_CAPABLE_KEYS = (pygame.K_q, pygame.K_TAB, pygame.K_f)
        if event.key in _ATTACK_CAPABLE_KEYS and (
                self.dialogue_box.active or self._levelup_active or self.save_flow_active):
            return

        if event.key == pygame.K_F1:
            self.dev_menu.toggle()

        elif event.key == pygame.K_F2:
            # F2 exits test mode and drops back into the room editor.
            if self.is_test_mode:
                self._exit_test_mode()
                self.room_editor.active       = True
                self.room_editor.current_view = 'view_room'

        elif event.key == pygame.K_ESCAPE:
            # Don't jump straight into the menu — fade to black first (matches
            # the original game), then open it once the screen is fully
            # black. Guarded so holding/mashing ESC during the fade can't
            # fire a second overlapping fade.
            if not self.pause_menu.active and not self._pause_fade_active \
                    and not self.transition_controller.is_transitioning():
                self._pause_fade_active = True
                self.transition_controller.start_plain_fade(
                    'out', 0.25, self._open_pause_menu)

        elif event.key == pygame.K_RETURN:
            # Same pre-menu fade-to-black shape as ESC/pause menu below,
            # just gated on its own flag/overlay so the two can't stomp
            # each other if both keys get mashed at once.
            if not self.scouter_menu.active and not self._scouter_fade_active \
                    and not self.pause_menu.active and not self._pause_fade_active \
                    and not self.transition_controller.is_transitioning():
                self._scouter_fade_active = True
                self.transition_controller.start_plain_fade(
                    'out', 0.25, self._open_scouter_menu)

        elif event.key == pygame.K_f:
            self.player.start_blocking()

        elif event.key == pygame.K_q:
            # Q fires a ki blast or begins charging a beam, depending on the current mode.
            self.player.is_q_pressed = True
            if self.player.ki_attack_mode == 'blast':
                self.player.shoot_blast()
            elif self.player.ki_attack_mode == 'ultra_volleyball_attack':
                # Instant fire, no charge-up — reuses the same kiblast
                # throw animation as a regular blast (see
                # shoot_ultra_volleyball()), just spawning a
                # UltraVolleyballAttack instead of a Projectile.
                self.player.shoot_ultra_volleyball()
            elif self.player.ki_attack_mode == 'beam':
                self.player.start_charging_beam()
            elif self.player.ki_attack_mode == 'kamekameha':
                self.player.start_charging_kamekameha()
            elif self.player.ki_attack_mode == 'banshee_blast':
                # Same hold-to-charge/auto-fire shape as beam/kamekameha —
                # see _update_banshee_blast/_grow_banshee_blast below.
                self.player.start_charging_banshee_blast()
            elif self.player.ki_attack_mode == 'final_flash':
                self.player.start_charging_final_flash()
            elif self.player.ki_attack_mode == 'big_bang_kamehameha':
                # Same hold-to-charge/auto-fire shape as beam/kamekameha/
                # final_flash — BigBangKamehamehaAttack reuses BeamAttack's
                # pipeline wholesale, so it's driven the same way (see
                # _update_big_bang_kamehameha/_grow_big_bang_kamehameha
                # below), not flame_kamehameha's fixed-chain shape.
                self.player.start_charging_big_bang_kamehameha()
            elif self.player.ki_attack_mode == 'genkidama':
                self.player.start_charging_genkidama()
            elif self.player.ki_attack_mode == 'big_bang_attack':
                # Same hold-to-charge/fire-on-release shape as genkidama
                # (see start_charging_big_bang_attack()), just with a
                # single fixed power level instead of an escalating one —
                # see BigBangAttackChargeEffect's docstring. Releasing Q
                # is handled in the KEYUP handler below.
                self.player.start_charging_big_bang_attack()
            elif self.player.ki_attack_mode == 'masenko':
                self.player.start_charging_masenko()
            elif self.player.ki_attack_mode == 'burning_attack':
                self.player.start_charging_burning()
            elif self.player.ki_attack_mode == 'flame_kamehameha':
                # Holds Q to charge (charging_flame_kamehameha.png, via
                # KamehamehaChargeEffect) then auto-fires once fully charged —
                # same hold-to-charge/auto-fire shape as beam/burning. See the
                # KEYUP handler above for how releasing early cancels it.
                self.player.start_flame_kamehameha()
            elif self.player.ki_attack_mode == 'sword':
                self.player.start_charging_sword()
            elif self.player.ki_attack_mode == 'dragon_fist':
                # Instant on press, no charge-up — held for as long as Q
                # stays down (see Player.start_dragon_fist/update_dragon_fist).
                # Releasing Q is handled generically below (is_q_pressed
                # goes False, which update_dragon_fist() checks itself,
                # same shape as the beam's own Ki-drain tick) — nothing
                # extra needed in the KEYUP handler.
                self.player.start_dragon_fist()
            elif self.player.ki_attack_mode == 'energy_punch':
                # Instant strike, no charge — see player.energy_punch() and
                # Game._update_energy_punch for the hit-check every frame
                # while it plays out.
                self.player.energy_punch()
            elif self.player.ki_attack_mode == 'instant_transmission':
                if self.player.start_targeting_instant_transmission():
                    w, h = self.logical_surface.get_size()
                    self.it_selector = InstantTransmissionSelector(w, h)
            elif self.player.ki_attack_mode == 'ghost_kamikaze_attack':
                # Instant on press, no charge-up, no hold-to-release either
                # — the whole creation → hold → attack sequence runs on
                # its own afterward (see Player.start_ghost_kamikaze and
                # Game._update_ghost_kamikaze). Nothing needed in the
                # KEYUP handler.
                self.player.start_ghost_kamikaze()
            elif self.player.ki_attack_mode == 'transform':
                # Q triggers a transformation when charged and ready, and
                # reverses an active transformation back to base form when
                # already transformed — same trigger key as every other
                # super attack above. Ignored mid-transition
                # (is_transforming / is_untransforming) so a repeat press
                # can't restart the animation partway through.
                ts = self.player.transformation
                if (getattr(self.player, 'has_transformation', False)
                        and ts and not ts.is_transforming and not ts.is_untransforming):
                    if ts.is_transformed:
                        ts.start_untransform()
                    elif ts.is_ready:
                        ts.start_transform()

        elif event.key == pygame.K_e:
            # Tracked so Player.update() can tell, once the normal melee
            # swing finishes, whether E is still held (roll into the
            # charged-melee wind-up — see start_charging_melee()) or not
            # (return to idle as before). See the KEYUP handler for the
            # release side, which also cancels an in-progress charge.
            self.player.is_e_pressed = True
            self._handle_interact()

        elif event.key == pygame.K_TAB:
            # Cycle through only the modes this character is allowed to use.
            modes = self._get_allowed_ki_modes()
            if len(modes) > 1:
                idx = modes.index(self.player.ki_attack_mode) if self.player.ki_attack_mode in modes else 0
                self.player.ki_attack_mode = modes[(idx + 1) % len(modes)]

        elif event.key in (pygame.K_a, pygame.K_d, pygame.K_w, pygame.K_s):
            # Double-tapping a direction key starts a run.
            if self.player.check_double_tap(event.key):
                self.player.is_running = True

    def _handle_interact(self):
        """
        Handle the E (interact) key press.

        Priority:
          1. Nearby save point.
          2. Nearby NPC — start / advance dialogue, with mission branching.
          3. Nearby closed chest — open it.
          4. Nearby dropped item pickup — collect it.
          5. Flying pad.
          6. Default melee attack.
        """
        # Don't allow interact while a flying sequence is in progress.
        if self.flying_controller.is_active():
            return
        # Don't allow interact while a nimbus cloud ride is in progress.
        if self.nimbus_controller.is_active():
            return
        if self._mjf_state in ('pending_fade_in', 'fade_in', 'flying',
                               'landing_fade_out', 'landing_fade_in'):
            return
        # Level-up sequence: E should only ever advance/close the two info
        # boxes at the end (handled by the dialogue_box.active branch
        # below) — not fall through to a melee swing during the turning/
        # character-animation phase, which has no dialogue box open yet.
        # Without this, E during that phase reaches the default melee
        # branch and sets is_attacking=True, but player.update() is frozen
        # for the whole levelup sequence, so it never clears — leaving the
        # player permanently stuck unable to move/act once the sequence ends.
        if self._levelup_active and not self.dialogue_box.active:
            return

        # Save point takes top priority.
        if self.nearby_save_point and not self.dialogue_box.active and not self.save_point_menu.active \
                and not self.save_flow_active:
            # Opening the menu (or going straight into the save flow
            # below) suppresses _update_player_movement() and
            # player.update() itself for as long as it stays open (see
            # the update() gating around save_point_menu.active /
            # save_flow_active). Without a snap-to-idle here, a player
            # who interacts mid-run, or right as idle_transition/
            # idle_wait kicked in, would stay frozen on that frame for
            # the entire time the menu/flow is open.
            # This applies to both save-pad variants — 'big' vs 'small' is
            # purely a sprite/size difference, so both need the same idle
            # snap and the same character-switch check below.
            self.player.is_running = False
            if not self.player.is_transitioning:
                if self.player.current_animation_state in ('walk', 'run', 'idle_transition', 'idle_wait'):
                    self.player.enter_idle()

            current_character = getattr(self.player, 'character', 'goku')
            other_characters  = [c for c in self.player.playable_characters if c != current_character]
            if other_characters:
                self.save_point_menu.open()
            else:
                # Nothing unlocked to switch to — the Save/Switch
                # Characters popup would only ever show one usable
                # option, so skip it entirely and go straight into the
                # save flow, same as picking "Save" from it would do
                # (see the 'save' branch in handle_events()).
                self._start_save_flow()
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

        # Open a nearby closed chest. Flips the sprite to the open frame
        # (Chest.open()) and puts the player into the pickup_item pose —
        # loot is granted and the "You found X!" dialogue box only appears
        # once that pose finishes (see _update_chest_pickup), so the icon
        # has time to float up above the player's head first (see
        # _draw_chest_pickup_icon). nearby_chest already excludes chests
        # that are already open, so this never re-fires on the same one.
        if self.nearby_chest and not self.dialogue_box.active:
            chest = self.nearby_chest
            chest.open()
            self.nearby_chest = None

            self.player.start_pickup_item()
            self.sound_manager.play_sfx('itemget')
            self._pending_chest = chest
            self._pending_chest_icon = (
                self._get_chest_item_icon(chest.item_id) if chest.item_id else None
            )
            return

        # Pick up a nearby dropped item (see core.items.ItemPickup / the
        # 'drop_item:' flow) — same interact-key gate and pickup pose as a
        # chest, just above: walking over one no longer collects it on its
        # own (see ItemPickup.update's is_player_nearby), E does. collect()
        # removes it from the world immediately, same as a chest flipping
        # to its opened sprite, while the actual inventory grant is
        # deferred to _update_item_pickup_finish until the pose plays out.
        if self.nearby_item_pickup and not self.dialogue_box.active:
            pickup = self.nearby_item_pickup
            pickup.collect()
            self.nearby_item_pickup = None

            self.player.start_pickup_item()
            self.sound_manager.play_sfx('itemget')
            self._pending_item_pickup = pickup
            self._pending_item_pickup_icon = self._get_dropped_item_icon(pickup.item_id)
            return

        # Check if the player is standing on a flying pad with waypoints.
        nearby_pad = next(
            (pad for pad in self.flying_pads
             if pad.active and pad.check_collision_with_player(self.player)),
            None
        )
        if nearby_pad and len(nearby_pad.waypoints) > 0:
            self.flying_controller.start_flight(self.player, nearby_pad)
            return

        # Check if the player is standing near a nimbus cloud with waypoints.
        nearby_cloud = next(
            (cloud for cloud in self.nimbus_clouds
             if cloud.active and not cloud.is_occupied and cloud.check_collision_with_player(self.player)),
            None
        )
        if nearby_cloud and len(nearby_cloud.waypoints) > 0:
            self.nimbus_controller.start_ride(self.player, nearby_cloud)
        else:
            # Default: throw a melee punch. A random melee1/melee2 swing
            # sound plays either way (hit or miss) — resolved once the
            # swing ends, see the melee-attack cleanup loop in update().
            melee = self.player.melee_attack()
            if melee:
                melee.hit_something = False
                self.melee_attacks.append(melee)

    # ── Save Game flow (save-pad "Save") ────────────────────────────────────
    # Reuses TitleScreen's own SAVE SELECT frame, re-labelled "Save Game"
    # and locked down (see TitleScreen.open_save_overlay), with a small
    # "Saving..." popup drawn in front of it. Entirely display-only —
    # self.save_flow_active just holds it up for SAVE_FLOW_POPUP_HOLD
    # seconds and then closes itself; the player has no control over any
    # of it (see the update()/handle_events() gating around
    # self.save_flow_active elsewhere in this file).

    def _start_save_flow(self):
        """Kicks off the in-game "Save Game" flow — called either straight
        from _handle_interact (no character to switch to) or from the
        save_point_menu's 'save' result. Opens TitleScreen's save overlay
        scrolled to the current slot and starts the popup timer."""
        slot_index = self.current_save_slot

        # Per-slot unlocked-character sprites for the row TitleScreen draws
        # under each slot's label. Only the active slot has real data right
        # now — there's no per-save-file roster tracking yet (see
        # TitleScreen._slot_has_save_data's own placeholder note), so every
        # other slot just renders no sprites, same as it renders no
        # "New Game" line while the overlay is active.
        slot_characters = {slot_index: list(getattr(self.player, 'playable_characters', []))}

        # Which of those characters is the one actually being played right
        # now, and their current costume/transformation — same lookup
        # _start_map_jump uses to pick the right sprite folder — so the
        # save-slot row can show a live idle sprite for that one entry
        # (see TitleScreen._get_character_idle_icon) instead of a static
        # icon.png.
        ts = self.player.transformation
        current_costume = (ts.current_transform_costume
                            if (ts and ts.is_transformed and ts.current_transform_costume)
                            else 'base')

        # Room + play time shown on the Room/Time lines that now sit
        # where "New Game" used to (see TitleScreen._draw_save_overlay_info).
        # current_room can be None this early/mid-transition, same
        # "never crash on missing state" fallback used everywhere else here.
        room_name = self.current_room.name if self.current_room else ''

        self.title_screen.open_save_overlay(
            slot_index, slot_characters,
            current_character=getattr(self.player, 'character', None),
            current_costume=current_costume,
            room_name=room_name,
            play_time=self.play_time,
        )
        self.save_flow_active = True
        self.save_flow_timer  = 0.0

    def _update_save_flow(self, dt):
        """Ticks the "Saving..." popup's hold timer and closes the whole
        flow once it's done. Called every frame from update() while
        self.save_flow_active is set, alongside title_screen.update(dt)
        (which just keeps its own internal clock/animations consistent —
        the overlay itself is static, see open_save_overlay)."""
        if not self.save_flow_active:
            return
        self.save_flow_timer += dt
        if self.save_flow_timer >= self.SAVE_FLOW_POPUP_HOLD:
            self._write_save_slot(self.current_save_slot)
            self.title_screen.close_save_overlay()
            self.save_flow_active = False
            self.save_flow_timer  = 0.0

    def _saving_popup_rect(self):
        """Geometry-only half of _draw_saving_popup — returns the
        (x, y, w, h) the "Saving..." popup box will occupy this frame,
        without drawing anything. Split out so Game.draw() can hand this
        rect to TitleScreen as an occlusion region *before*
        TitleScreen.draw() renders the save-slot divider bar (see
        TitleScreen._draw_save_slot_divider's occlusion_rect param) —
        otherwise the divider has no way to know where the popup that's
        about to be drawn on top of it will land."""
        pm = self.pause_menu
        if pm is None:
            return None

        text = 'Saving...'
        letter_spacing = pm.menu_uppercase_font.letter_spacing
        surfs = []
        for ch in text:
            font = pm.menu_uppercase_font if ch.isupper() else pm.menu_lowercase_font
            surfs.append(font.render(ch))

        max_h   = max(s.get_height() for s in surfs)
        block_w = sum(s.get_width() for s in surfs) + letter_spacing * (len(surfs) - 1)
        # Descenders don't affect popup_w and only add a few px to block_h
        # (used purely for the size floor below), so a flat +8 stand-in
        # for max_desc is close enough here — the real draw path in
        # _draw_saving_popup computes it precisely per-glyph.
        block_h = max_h + 8

        pad_x   = max(48, int(SCREEN_WIDTH * 0.05))
        pad_y   = max(40, int(SCREEN_HEIGHT * 0.06))
        popup_w = max(block_w + pad_x * 2, int(SCREEN_WIDTH * 0.22)) - 100
        popup_h = max(block_h + pad_y * 2, int(SCREEN_HEIGHT * 0.125)) - 26

        # The border is drawn as a 9-slice (PauseMenu._draw_9slice_sprite,
        # shared by every bordered box in the game) whose flat left/right
        # edges and center fill are *stretched* — not tiled — from a
        # narrow strip of the border texture out to popup_h. That only
        # looks clean when popup_h's stretch factor lands on a whole
        # number of source pixels; every other box in the game happens to,
        # this one (thanks to the -26 above) doesn't, so the strip gets
        # duplicated unevenly and the border reads as inconsistent pixels
        # top-to-bottom. Snapping popup_h up to the next height where the
        # stretch is an exact integer multiple fixes it here only —
        # _draw_9slice_sprite itself, and every other menu that calls it,
        # is untouched. popup_w/popup_x are left completely alone below.
        if pm.box_sprite:
            sh = pm.box_sprite.get_height()
            corner_size = 20
            ch  = min(corner_size, sh // 3)
            sch = min(ch * 4, max(1, popup_h // 2))
            msh = sh - 2 * ch          # source strip height that gets stretched
            if msh > 0:
                mh = popup_h - 2 * sch  # target height that strip stretches to
                if mh > 0:
                    remainder = mh % msh
                    if remainder:
                        popup_h += msh - remainder

        frame_rect = self.title_screen.get_save_select_frame_rect()
        if frame_rect is not None:
            popup_x = frame_rect.x + (frame_rect.width  - popup_w) // 2
            popup_y = frame_rect.y + (frame_rect.height - popup_h) // 2 - 33
        else:
            popup_x = (SCREEN_WIDTH  - popup_w) // 2
            popup_y = (SCREEN_HEIGHT - popup_h) // 2

        return pygame.Rect(popup_x, popup_y, popup_w, popup_h)

    def _draw_saving_popup(self, screen):
        """The "Saving..." popup shown in front of the Save Game screen
        while self.save_flow_active is running — same bordered-box style
        as PauseMenu's own item/equip-confirm popups (see
        PauseMenu._draw_item_confirm_popup). Text uses PauseMenu's own
        per-character uppercase_menu/lowercase_menu fonts, same trick
        TitleScreen._new_game_surfs uses for "New Game", including the
        same per-letter descender nudge PauseMenu's own text renderers use
        (see e.g. PauseMenu._blit_journal_text's _desc dict) so 'g' hangs
        below the baseline instead of sitting on it like every other
        letter here."""
        pm = self.pause_menu
        if pm is None:
            return

        text = 'Saving...'
        # Same offsets PauseMenu's own renderers use — how far below the
        # baseline each of these needs to drop to read as a proper
        # descender instead of a squashed-up glyph.
        descenders = {'p': 8, 'q': 8, 'g': 8, 'y': 8, ',': 8}
        # Same letter spacing PauseMenu's own text renderers use (see
        # PauseMenu._blit_journal_text's char_spacing) — the raw glyphs
        # butt up against each other with none of their own.
        letter_spacing = pm.menu_uppercase_font.letter_spacing

        surfs = []
        for ch in text:
            font = pm.menu_uppercase_font if ch.isupper() else pm.menu_lowercase_font
            s = font.render(ch).copy()
            s.fill((255, 255, 255), special_flags=pygame.BLEND_RGBA_MULT)
            surfs.append((ch, s))

        max_h    = max(s.get_height() for _, s in surfs)
        max_desc = max((descenders.get(ch, 0) for ch, _ in surfs), default=0)
        # Full visual block size, spacing included — this (not the raw
        # cap-height max_h) is what actually needs to land centered in
        # the box, since the descender on "g" and the "..." tail both
        # extend past a plain max_h box.
        block_w = sum(s.get_width() for _, s in surfs) + letter_spacing * (len(surfs) - 1)
        block_h = max_h + max_desc

        # Popup box geometry — same rect Game.draw() already handed
        # TitleScreen as an occlusion region (see _saving_popup_rect)
        # right before calling title_screen.draw(), so the divider bar
        # underneath was already clipped around exactly this box.
        popup_rect = self._saving_popup_rect()
        popup_x, popup_y, popup_w, popup_h = popup_rect

        drawn = pm.box_sprite and pm._draw_9slice_sprite(
            screen, pm.box_sprite, popup_x, popup_y, popup_w, popup_h, corner_size=20
        )
        if not drawn:
            pygame.draw.rect(screen, pm.border_outer, (popup_x-6, popup_y-6, popup_w+12, popup_h+12))
            pygame.draw.rect(screen, pm.border_inner, (popup_x-3, popup_y-3, popup_w+6,  popup_h+6))
            pygame.draw.rect(screen, pm.border_green, (popup_x-1, popup_y-1, popup_w+2,  popup_h+2))
            pm._draw_tiled_background(screen, pygame.Rect(popup_x, popup_y, popup_w, popup_h))

        # Text is centered on the full block_w/block_h (not popup_w/
        # popup_h's own padding) so it sits centered inside the border on
        # both axes regardless of how much floor padding got added above.
        x = popup_x + (popup_w - block_w) // 2
        y = popup_y + (popup_h - block_h) // 2
        for ch, s in surfs:
            shadow = s.copy(); shadow.fill((0, 0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            oy = (max_h - s.get_height()) + descenders.get(ch, 0)
            screen.blit(shadow, (x + 2, y + oy + 2))
            screen.blit(s, (x, y + oy))
            x += s.get_width() + letter_spacing

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
            print(f"[world_map_music] _on_exit callback firing for map '{new_map_name}'")
            self._apply_world_map_music(new_map_name)
            if self._mjf_alpha >= 255.0:
                self._mjf_state  = 'fade_in'
                self._mjf_active = False
            else:
                self._mjf_state = 'pending_fade_in'
            # Reset the player sprite so it doesn't stay frozen on the
            # map_jump frame while the screen is still visible.
            self.player.enter_idle()

        self.player.on_map_jump_exit = _on_exit
        self.camera.locked = True
        # Reset the fade so a repeated jump always starts from transparent.
        self._mjf_alpha  = 0.0
        self._mjf_active = False
        # Departure cue for leaving into the world map via this object —
        # same sound as flying off a flying pad, if you've already got
        # that one wired into FlyingController.start_flight.
        self.sound_manager.play_sfx('flyoff')
        self.player.start_map_jump()

    def _start_npc_dialogue(self, npc):
        """Begin a conversation with an NPC, routing through the mission system.

        Mission state routing (via mission_manager.get_npc_dialogue_state):
          'offer'     — quest not accepted yet; run the 'offer' action list
          'active'    — mission running; run the 'active' action list
          'completed' — all objectives met; run the 'completed' action list
                        (ends in a mission('complete', ...) action — see
                        MissionManager._migrate_legacy_dialogues)
          'rewarded'  — fully done; run the 'rewarded' action list
          None        — plain NPC with no mission; use dialogue_config directly

        Each phase is now just an action list run through the same
        event_runner a trigger box's actions run through (dialogue_box /
        dialogue_choice / mission / ...) instead of the old bespoke
        _active_mission_dialogue line-by-line state machine — see
        _handle_dialogue_box_action / _handle_dialogue_choice_action, which
        already set self._event_dialogue_active and know how to hand
        control back to _advance_npc_dialogue()'s event-dialogue branch.
        """
        # Hide the NPC's own indicator the moment dialogue starts.
        npc.is_talking = True

        # Immediately snap the player to idle so walking-into-E never leaves
        # the walk/run animation frozen on screen during the conversation.
        if self.player.current_animation_state in ('walk', 'run'):
            self.player.enter_idle()
        self.player.is_running = False

        iid = getattr(npc, 'instance_id', '')
        # Generic bookkeeping, not mission-specific — any mission's
        # talk_to_npc objective (or any other flag_is('npc_talked:...')
        # condition) can gate on this NPC being talked to at all,
        # regardless of whether *this* NPC gives a mission itself.
        if iid:
            self.flag_manager.mark_npc_talked(iid)

        state = self.mission_manager.get_npc_dialogue_state(iid) if iid else None

        if state is not None:
            mission = self.mission_manager.get_mission_for_npc(iid)
            actions = (mission or {}).get('dialogue_actions', {}).get(state, [])
            self._talking_npc = npc
            if actions:
                self.event_runner.run_sequence(actions, on_finished=lambda: self._end_npc_talk(npc))
            else:
                self._end_npc_talk(npc)
            return

        # Plain NPC — use the standard dialogue system with no mission routing.
        self._talking_npc = None
        text, is_final, item = npc.start_dialogue()
        if text:
            portrait_key = self._npc_portrait_key(npc)
            self.dialogue_box.show(text, "NPC", is_final, item, portrait_key=portrait_key)
            if item:
                self.player.inventory.append(item)

    def _end_npc_talk(self, npc):
        """Called once a mission-phase action list (or an empty one)
        finishes — mirrors what the old _advance_mission_dialogue's final
        'else' branch used to do to close out the NPC's talking state."""
        npc.is_talking             = False
        npc.current_dialogue_index = 0
        self._talking_npc = None

    def _advance_npc_dialogue(self):
        """Advance or close the active NPC dialogue box on player input."""
        # If text is still typing out, snap it to fully visible on the first press.
        if self.dialogue_box._chars_shown < len(self.dialogue_box.current_text):
            self.dialogue_box._chars_shown = len(self.dialogue_box.current_text)
            return

        # ── Event-triggered dialogue (from the Event Editor's action list,
        # or a mission phase's action list — same mechanism now) ───────────
        if self._event_dialogue_active:
            self.dialogue_box.hide()
            return

        # ── Plain NPC flow (no mission) ───────────────────────────────────────
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

    def _show_level_up_if_pending(self):
        """Consume Player.pending_level_up if it's set — kicks off the
        level-up sequence (freeze, turn, animate, dialogue) and plays
        levelup.wav, so every level-up gets both regardless of which call
        site (mission XP reward vs. enemy-kill XP) triggered it. Safe to
        call unconditionally; no-op if nothing is actually pending."""
        if not self.player.pending_level_up:
            return
        self.player.pending_level_up = False
        self.sound_manager.play_sfx('levelup')
        self._start_levelup_sequence()

    def _start_levelup_sequence(self):
        """Kick off the level-up cutscene: freeze enemies/NPCs/player input,
        spin the player through a full facing rotation, play the character's
        levelup.png animation twice, then chain the two level-up dialogue
        boxes ("reached level X!" -> "currently has N stat points.").

        Snapshots character/level/stat_points at trigger time so the
        dialogue text stays correct even though the world is frozen for the
        whole sequence (nothing else should be able to change them, but this
        matches the same defensive snapshotting used elsewhere, e.g. locked
        boss max HP in sprite_hud.py).
        """
        self._levelup_char_at_trigger        = self.player.character
        self._levelup_level_at_trigger       = self.player.level
        self._levelup_stat_points_at_trigger = self.player.stat_points

        self._levelup_active     = True
        self._levelup_state      = 'turning'
        self._levelup_turn_idx   = 0
        self._levelup_turn_timer = 0.0

        # Snap to idle facing the first direction in the turn sequence so
        # there's no stray walk/run frame carried in from before leveling up
        # — same fix _handle_dialogue_box_action() applies for NPC/event
        # dialogue starting mid-walk.
        if self.player.current_animation_state in ('walk', 'run'):
            self.player.enter_idle()
        self.player.is_running = False
        self.player.direction = self._LEVELUP_TURN_SEQUENCE[0]
        self.player.sprite.set_animation('idle', self.player.direction)

        self._load_levelup_char_frames()

    def _load_levelup_char_frames(self):
        """Load levelup.png from the current character's own sprite folder
        (assets/sprites/characters/<character>/<costume>/levelup.png via
        self.player.sprite.base_path) and slice it into frames matching the
        player's normal sprite size — same convention as start_map_jump()'s
        map_jump.png loader in player.py.

        Assumes one row of frames, each player.width x player.height (32x32
        by default) — let me know if the sheet is laid out differently.
        """
        self._levelup_anim_frames       = []
        self._levelup_anim_idx          = 0
        self._levelup_anim_timer        = 0.0
        self._levelup_anim_loops        = 0
        self._levelup_anim_scaled_cache = {}

        path = f'{self.player.sprite.base_path}/levelup.png'
        try:
            sheet   = pygame.image.load(path).convert_alpha()
            frame_w = self.player.width
            frame_h = self.player.height
            num_frames = max(1, sheet.get_width() // frame_w)
            self._levelup_anim_frames = [
                sheet.subsurface(pygame.Rect(i * frame_w, 0, frame_w, frame_h))
                for i in range(num_frames)
            ]
        except Exception as e:
            # Sheet not found — sequence still runs (just skips straight to
            # dialogue once turning finishes) so nothing hard-crashes.
            print(f'[levelup] could not load {path}: {e}')

    def _update_levelup_sequence(self, dt):
        """Advance the turning / character-animation phases of the level-up
        sequence. No-op once the sequence has handed off to the dialogue
        boxes (self._levelup_state is None at that point) — self._levelup_active
        stays True through both boxes purely to keep the world frozen; see
        _end_levelup_sequence()."""
        if not self._levelup_active or self._levelup_state is None:
            return

        if self._levelup_state == 'turning':
            self._levelup_turn_timer += dt
            if self._levelup_turn_timer >= self._LEVELUP_TURN_DURATION:
                self._levelup_turn_timer -= self._LEVELUP_TURN_DURATION
                self._levelup_turn_idx += 1
                if self._levelup_turn_idx >= len(self._LEVELUP_TURN_SEQUENCE):
                    self._levelup_state = 'playing_anim'
                    if not self._levelup_anim_frames:
                        # Sheet missing — nothing to animate, go straight to dialogue.
                        self._finish_levelup_animation()
                else:
                    new_dir = self._LEVELUP_TURN_SEQUENCE[self._levelup_turn_idx]
                    self.player.direction = new_dir
                    self.player.sprite.set_animation('idle', new_dir)

        elif self._levelup_state == 'playing_anim':
            self._levelup_anim_timer += dt
            if self._levelup_anim_timer >= self._LEVELUP_ANIM_FRAME_DURATION:
                self._levelup_anim_timer -= self._LEVELUP_ANIM_FRAME_DURATION
                self._levelup_anim_idx += 1
                if self._levelup_anim_idx >= len(self._levelup_anim_frames):
                    self._levelup_anim_idx = 0
                    self._levelup_anim_loops += 1
                    if self._levelup_anim_loops >= self._LEVELUP_ANIM_LOOPS_TARGET:
                        self._finish_levelup_animation()

    def _finish_levelup_animation(self):
        """Turning + animation are done — show the first of the two
        level-up dialogue boxes. self._levelup_active stays True (world
        stays frozen) until _end_levelup_sequence() fires after the second
        box closes."""
        self._levelup_state = None
        name = self._levelup_char_at_trigger.replace('_', ' ').title()
        text = f"{name} has reached level {self._levelup_level_at_trigger}!"
        self.dialogue_box.show(
            text, name, True, None,
            on_close=self._show_levelup_stat_points_dialogue,
        )

    def _show_levelup_stat_points_dialogue(self):
        name = self._levelup_char_at_trigger.replace('_', ' ').title()
        text = f"{name} currently has {self._levelup_stat_points_at_trigger} stat points."
        self.dialogue_box.show(
            text, name, True, None,
            on_close=self._end_levelup_sequence,
        )

    def _end_levelup_sequence(self):
        """Second dialogue box closed — unfreeze the world."""
        self._levelup_active = False
        # Safety net: player.update() is frozen for the entire sequence, so
        # if is_attacking somehow got set mid-freeze (e.g. a future change
        # reintroduces a path that starts an attack while _levelup_active is
        # True) it would never get cleared on its own, permanently locking
        # can_act()/can_move() to False. Clearing it here guarantees the
        # player always regains control once the sequence ends.
        self.player.is_attacking = False
        self.player.attack_cooldown = 0
        self.player.enter_idle()

    def _draw_levelup_sprite(self, screen, camera, colors):
        """Blit the current levelup.png animation frame at the player's
        screen position. Signature matches DrawableObject.draw() so this
        can be called by the layer manager like any other sprite — see
        _LevelUpPlayerSpriteDrawable, which registers this with the same
        draw_layer/get_sort_key the player itself uses, so NPCs/enemies/
        foreground tiles correctly draw in front of or behind it instead
        of it always landing on top."""
        frames = self._levelup_anim_frames
        if not frames:
            return
        idx = min(self._levelup_anim_idx, len(frames) - 1)
        sw  = self.player.width  * RENDER_SCALE
        sh  = self.player.height * RENDER_SCALE

        # Pre-scaled-frame cache — same source frame always scales to the
        # same size, so scale once per frame index instead of every draw().
        scaled = self._levelup_anim_scaled_cache.get(idx)
        if scaled is None:
            scaled = pygame.transform.scale(frames[idx], (sw, sh))
            self._levelup_anim_scaled_cache[idx] = scaled

        cx = int(self.player.x * RENDER_SCALE - camera.x)
        cy = int(self.player.y * RENDER_SCALE - camera.y)
        screen.blit(scaled, (cx - sw // 2, cy - sh // 2))

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

        # If the player was mid-way through (or fully inside) the world-map
        # flying sequence when "Test Room" was triggered, force it back to
        # neutral. update() checks self._mjf_state before anything else each
        # frame and, while it's 'flying'/'fade_in'/etc, routes straight into
        # _update_map_flying() and returns — completely bypassing normal
        # room gameplay. Without this reset, everything below (player
        # position, camera, current_room) gets set correctly but the player
        # stays visibly stuck on the world map, since update() never even
        # reaches the code that would show them in the test room.
        self._mjf_state  = None
        self._mjf_active = False
        self._mjf_alpha  = 0.0
        self.camera.locked = False
        self.player.is_map_jumping  = False
        self.player.map_jump_moving = False
        self.player.on_map_jump_exit = None

        self.is_test_mode                = True
        self._create_comprehensive_test_backup()
        self._test_mission_snapshot      = self.mission_manager.snapshot()
        self.mission_manager.block_saves = True   # prevent test progress reaching disk
        self._test_flag_snapshot         = self.flag_manager.snapshot()
        self.flag_manager.block_saves    = True   # prevent test progress reaching disk
        # Snapshot which world-map pins are currently hidden, so a
        # world_map_location 'add'/'remove' action fired while testing this
        # room doesn't permanently show/hide a pin on the real map — see
        # the restore in _exit_test_mode().
        self._test_wm_hidden_snapshot    = {k: set(v) for k, v in self._wm_hidden_locations.items()}

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

        # Centre the camera on the spawn position. update(..., dt=0) skips
        # the smooth-follow ease and also resets the camera's internal
        # true position — see the save-load comment above for why a plain
        # camera.x/y assignment isn't enough on its own. (This also fixes
        # a pre-existing miss here: the old math didn't multiply by
        # RENDER_SCALE like the other spawn/load sites do.)
        self.camera.update(self.player, room.width, room.height, dt=0)

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
                'nimbus_clouds':       [],
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

            if hasattr(room, 'nimbus_clouds'):
                for cloud in room.nimbus_clouds:
                    # Use the full to_dict so origin_room / origin_x/y /
                    # origin_camera / waypoint spawn_x/y all survive the
                    # test-mode backup. The previous hand-rolled dict dropped
                    # them, so a test-mode exit+re-enter lost the return target.
                    room_backup['nimbus_clouds'].append(cloud.to_dict())

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

        # Nimbus clouds — deep-copy so test-mode rides don't touch the editor data.
        self.nimbus_clouds = []
        if hasattr(room, 'nimbus_clouds') and room.nimbus_clouds:
            from objects.nimbus_cloud import NimbusCloud, NimbusCloudWaypoint
            for cloud in room.nimbus_clouds:
                copy_cloud                 = NimbusCloud(cloud.x, cloud.y, cloud.cloud_type)
                copy_cloud.origin_x        = cloud.origin_x
                copy_cloud.origin_y        = cloud.origin_y
                copy_cloud.waypoints       = [NimbusCloudWaypoint.from_dict(wp.to_dict()) for wp in cloud.waypoints]
                copy_cloud.is_return_pad   = cloud.is_return_pad
                copy_cloud.linked_pad_id   = cloud.linked_pad_id
                copy_cloud.source_room     = cloud.source_room
                copy_cloud.current_room    = room.name
                # Preserve the room this cloud was originally placed in —
                # a shuttle cloud's return-trip target depends on it (see
                # NimbusCloud.origin_room / get_reversed_path). Without
                # copying it across here, every test-mode reload silently
                # resets it to "" and the same cross-room return-ride bug
                # comes right back in test mode specifically.
                copy_cloud.origin_room     = getattr(cloud, 'origin_room', '') or cloud.current_room
                copy_cloud.origin_camera_x = getattr(cloud, 'origin_camera_x', 0)
                copy_cloud.origin_camera_y = getattr(cloud, 'origin_camera_y', 0)
                copy_cloud.width           = cloud.width
                copy_cloud.height          = cloud.height
                copy_cloud.rider_offset_x  = cloud.rider_offset_x
                copy_cloud.rider_offset_y  = cloud.rider_offset_y
                copy_cloud.active          = True
                self.nimbus_clouds.append(copy_cloud)

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

        # Decorations (trees, etc.) — copy so test mode doesn't mutate the
        # editor originals. to_dict()/from_dict() round-trips everything a
        # Decoration has (type, position, variant); there's no
        # destructible-style health/state to carry over since decorations
        # are purely ambient.
        self.decorations = []
        if hasattr(room, 'decorations') and room.decorations:
            from objects.decoration_objects import Decoration
            for decoration in room.decorations:
                self.decorations.append(Decoration.from_dict(decoration.to_dict()))

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

        # Room music track — a plain string, so no copy needed to keep test
        # mode from mutating the editor original.
        self.music_track = getattr(room, 'music_track', '')
        self._apply_room_music(room)

        # Trigger boxes — copy so test mode doesn't latch (once=True) the
        # editor originals' `triggered` flag.
        self.trigger_boxes = []
        if hasattr(room, 'trigger_boxes') and room.trigger_boxes:
            from objects.trigger_box import TriggerBox
            for box in room.trigger_boxes:
                self.trigger_boxes.append(TriggerBox.from_dict(box.to_dict()))

        # Doors — copy so test mode doesn't mutate the editor originals.
        # to_dict()/from_dict() round-trips is_open for permanent doors, so a
        # door already opened in the editor's own state (if any) carries over.
        # First, close out any non-permanent door left open in the room we're
        # leaving — those live-mode Door instances are shared with RoomManager
        # (see _load_room_objects below), so without this they'd still show
        # open the next time that room is entered.
        for door in getattr(self, 'doors', []):
            door.close()
        self.doors = []
        if hasattr(room, 'doors') and room.doors:
            from objects.door_object import Door
            for door in room.doors:
                self.doors.append(Door.from_dict(door.to_dict()))

        # Chests — copy so test mode doesn't mutate the editor originals.
        # to_dict()/from_dict() round-trips `opened`, so a chest already
        # opened in the editor's own state (if any) carries over. Unlike
        # doors, a chest has no "close on room exit" behavior to undo first
        # — once opened it just stays opened.
        self.chests = []
        if hasattr(room, 'chests') and room.chests:
            from objects.chest_object import Chest
            for chest in room.chests:
                self.chests.append(Chest.from_dict(chest.to_dict()))

        # Entities (enemies and NPCs)
        self.enemies = []
        self.npcs    = []
        self.critters = []  # ambient wildlife: squirrels, birds, butterflies
        self._spawn_room_entities(room)

        # Clear all in-flight projectiles and attacks.
        self._clear_projectiles()

        # Give the player and all enemies a shared obstacle list for knockback.
        self._assign_obstacles()

    def _exit_test_mode(self):
        """Restore all rooms to their pre-test state and clear test entities."""
        if not self.is_test_mode or not self.test_room_backup:
            return

        # Same reasoning as the reset in _handle_test_room: if the player
        # flew into the world map during the test itself and then pressed F2
        # to exit, leaving _mjf_state set would keep update() routing into
        # _update_map_flying() and bypassing normal gameplay/the room editor
        # even after is_test_mode goes back to False below.
        self._mjf_state  = None
        self._mjf_active = False
        self._mjf_alpha  = 0.0
        self.camera.locked = False
        self.player.is_map_jumping  = False
        self.player.map_jump_moving = False
        self.player.on_map_jump_exit = None

        # Whatever music the test room started (via its persisted music
        # track, a cutscene's play_music action, etc.) otherwise just keeps
        # playing forever — nothing re-applies a context/room track on
        # exiting test mode. Cut it instantly here so dropping back into the room editor
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

            from objects.nimbus_cloud import NimbusCloud
            room.nimbus_clouds = []
            for d in backup.get('nimbus_clouds', []):
                # Full from_dict restores origin_room / origin_x/y / spawn
                # points — the hand-rolled path above dropped them and broke
                # post-test return rides.
                room.nimbus_clouds.append(NimbusCloud.from_dict(d))

            if self.room_editor.object_editor:
                self.room_editor.object_editor.nimbus_cloud_manager.nimbus_clouds[room.name] = room.nimbus_clouds

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

        self.flag_manager.block_saves = False
        if self._test_flag_snapshot is not None:
            self.flag_manager.restore(self._test_flag_snapshot)
            self.flag_manager.save()
            self._test_flag_snapshot = None

        # Restore world-map pin visibility to pre-test state, so any
        # world_map_location 'add'/'remove' action that fired during the
        # test doesn't stick around once you're back in the room editor.
        if self._test_wm_hidden_snapshot is not None:
            self._wm_hidden_locations = self._test_wm_hidden_snapshot
            self._test_wm_hidden_snapshot = None
            # Bust the cached location list (and whatever else keys off it)
            # so a currently-open flying-map view re-derives from the
            # restored hidden set instead of whatever test mode toggled.
            for attr in ('_wm_locations', '_wm_entities', '_wm_vehicle_cache'):
                if hasattr(self, attr):
                    delattr(self, attr)

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

        # Nimbus clouds — shallow copy; current_room is set on each cloud.
        self.nimbus_clouds = []
        if hasattr(room, 'nimbus_clouds') and room.nimbus_clouds:
            self.nimbus_clouds = room.nimbus_clouds[:]
            for cloud in self.nimbus_clouds:
                cloud.current_room = room.name

        # Collision objects — shallow copy.
        self.collision_objects = []
        if hasattr(room, 'collision_objects') and room.collision_objects:
            self.collision_objects = room.collision_objects[:]

        # Invisible boundary walls to contain knockback inside the room.
        self._add_room_boundary_walls(room)

        # One-liner copies for the rest of the room object types.
        self.destructible_stones = room.destructible_stones[:] if hasattr(room, 'destructible_stones') and room.destructible_stones else []
        self.decorations         = room.decorations[:]         if hasattr(room, 'decorations')         and room.decorations         else []
        self.level_gates         = room.level_gates[:]         if hasattr(room, 'level_gates')         and room.level_gates         else []
        self.room_transitions    = room.room_transitions[:]    if hasattr(room, 'room_transitions')    and room.room_transitions    else []
        self.save_points         = room.save_points[:]         if hasattr(room, 'save_points')         and room.save_points         else []
        self.world_map_objects   = room.world_map_objects[:]   if hasattr(room, 'world_map_objects')   and room.world_map_objects   else []
        self.music_track         = getattr(room, 'music_track', '')
        self.trigger_boxes       = room.trigger_boxes[:]       if hasattr(room, 'trigger_boxes')       and room.trigger_boxes       else []
        # Close out any non-permanent door left open in the room we're leaving
        # — a non-permanent door stays open as long as the player is in its
        # room, but should be closed again by the time they come back.
        for door in getattr(self, 'doors', []):
            door.close()
        # Shallow copy — same Door instances as room.doors, not fresh copies —
        # so a permanent door's is_open flag survives leaving and re-entering
        # this room later in the same session.
        self.doors                = room.doors[:]              if hasattr(room, 'doors')               and room.doors               else []
        # Chests — shallow copy, same as doors above (once opened they stay
        # opened, so there's no "close on room exit" step needed here).
        self.chests                = room.chests[:]             if hasattr(room, 'chests')              and room.chests              else []
        self._apply_room_music(room)


        # Spawn entities.
        self.enemies = []
        self.npcs    = []
        self.critters = []  # ambient wildlife: squirrels, birds, butterflies
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
            """Bounding box for a movable entity, centered on (e.x, e.y)."""
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
        from entities.critter import Critter

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

                # display_name / description come from this NPC's saved
                # entity_creator config (assets/npcs/{id}.json), same
                # source BossEnemy already reads from for enemies — read
                # here rather than baked onto NPC.__init__ since (like
                # BossEnemy) it's keyed off the id assigned at placement
                # time, not something NPC can know about itself.
                _npc_cfg          = entity_creator.load_config(entity_creator.KIND_NPC, npc.npc_id)
                npc.display_name  = _npc_cfg.get('display_name') or npc.npc_id
                npc.description   = _npc_cfg.get('description', '')

                # Register the mission if this NPC has one defined inline.
                # Stamp giver_instance_id/id from the live NPC instance
                # (matches scan_rooms_for_missions' behavior) rather than
                # trusting whatever was last saved on data['mission'],
                # since instance_id is only assigned/stable once the NPC is
                # actually placed.
                if data.get('mission') and npc.instance_id:
                    mission_def = dict(data['mission'])
                    mission_def['giver_instance_id'] = npc.instance_id
                    mission_def.setdefault('id', npc.instance_id)
                    self.mission_manager.register_mission(mission_def)

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

            elif entity_type == 'critter':
                critter_id = data.get('id', 'squirrel')
                wander_radius = data.get('wander_radius', 32)
                critter_width = data.get('width', 16)
                critter_height = data.get('height', 16)
                critter = Critter(x, y, critter_type=critter_id, variant=variant_type,
                                  width=critter_width, height=critter_height,
                                  wander_radius=wander_radius)
                critter.active = True

                try:
                    from core.sprite_system import create_critter_sprite
                    critter.sprite     = create_critter_sprite(critter_id, variant_type,
                                                                critter.width, critter.height)
                    critter.has_sprite = critter.sprite is not None
                except Exception:
                    critter.has_sprite = False

                # No _push_out_of_obstacles call — critters have no hitbox and
                # are never checked against obstacles, so there's nothing to
                # push out of.
                self.critters.append(critter)

            elif entity_type == 'boss':
                boss        = BossEnemy(x, y, boss_id=enemy_id, variant=variant_type)
                boss.active = True
                self._push_out_of_obstacles(boss, spawn_obstacles)
                self.enemies.append(boss)

            elif entity_type == 'enemy':
                ai_type        = data.get('ai_type', 'easy')
                enemy_category = data.get('enemy_category', 'melee')
                zeni_pool      = data.get('zeni_pool', 'tier1')
                print(f"DEBUG spawning enemy id={enemy_id} zeni_pool={zeni_pool!r} raw_data_keys={list(data.keys())}")

                # Map variant to the correct projectile type for shooter enemies.
                if variant_type == 'gunner':
                    shooter_style = 'bullet'
                elif variant_type == 'rocketlauncher':
                    shooter_style = 'rocket'
                else:
                    shooter_style = 'bomb'

                enemy        = Enemy(x, y, enemy_type=enemy_id, variant=variant_type,
                                     ai_type=ai_type, enemy_category=enemy_category,
                                     shooter_style=shooter_style, zeni_pool=zeni_pool)
                enemy.active = True

                # display_name / description from this enemy's saved
                # entity_creator config (assets/enemies/{id}.json) — same
                # source BossEnemy already reads in its own __init__.
                # Regular (non-boss) Enemy doesn't load its config itself,
                # so this is applied here instead, right after construction.
                _enemy_cfg         = entity_creator.load_config(entity_creator.KIND_ENEMY, enemy_id)
                enemy.display_name = _enemy_cfg.get('display_name') or enemy_id
                enemy.description  = _enemy_cfg.get('description', '')

                # Apply configured STR/POW/END/SPD stats — same scheme the
                # player uses (see character_creator.py / game.py's
                # _stat_map). BossEnemy.__init__ already reads its own cfg
                # for this; regular Enemy doesn't load its config itself,
                # so it's applied here right after construction instead.
                # Previously these sliders had no effect on non-boss
                # enemies at all — every one used Enemy.__init__'s hardcoded
                # defaults (150 HP / 20 END / preset attack_damage).
                _stats = _enemy_cfg.get('stats', {})
                if _stats:
                    enemy.max_hp   = _stats.get('max_hp', enemy.max_hp)
                    enemy.hp       = enemy.max_hp
                    enemy.defense  = _stats.get('defense', enemy.defense)
                    enemy.speed    = _stats.get('speed', enemy.speed)
                    # Raw STR/POW — stored on the entity even though only
                    # one of them actually drives attack_damage below, so
                    # UI that shows both stats at once (see
                    # ui/scouter_menu.py's _get_data_stats) has real
                    # configured values to read instead of the Enemy.__init__
                    # hardcoded fallback.
                    enemy.strength = _stats.get('strength', enemy.strength)
                    enemy.power    = _stats.get('power', enemy.power)
                    enemy.attack_damage = (enemy.strength if enemy_category == 'melee'
                                            else enemy.power)

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
            + self.decorations
            + self.level_gates
            + self.room_transitions
            + self.chests
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
        self.critters = []  # ambient wildlife: squirrels, birds, butterflies
        self._clear_projectiles()

    def _clear_projectiles(self):
        """Remove all in-flight projectiles and visual effects."""
        self.projectiles   = []
        self.ultra_volleyballs = []
        self.melee_attacks = []
        self.cutscene_beams = []
        self.bombs         = []
        self.masenko_projectiles = []
        self.enemy_bullets = []
        self.enemy_rockets = []
        self.enemy_kiblasts = []
        self.explosions    = []
        self.genkidama_hit_effects = []
        self.burning_hit_effects  = []
        self.big_bang_attacks = []
        self.big_bang_destruction_effects = []
        self.zeni_pickups   = []
        self.item_pickups   = []   # dropped-item pickups despawn on room leave — see core.items.ItemPickup
        self._white_flash_timer = 0.0
        self.it_selector = None
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

    def _on_decoration_deleted(self, decoration, room_name):
        """Sync game list when a decoration (tree, etc.) is removed in the editor."""
        if self.is_test_mode:
            return
        if self.current_room and self.current_room.name == room_name:
            if decoration in self.decorations:
                self.decorations.remove(decoration)

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

    def _on_nimbus_cloud_deleted(self, cloud, room_name):
        """Sync game list when a nimbus cloud is removed in the editor."""
        if self.is_test_mode:
            return
        if self.current_room and self.current_room.name == room_name:
            if cloud in self.nimbus_clouds:
                self.nimbus_clouds.remove(cloud)

    def _on_nimbus_cloud_placed(self, cloud, room_name):
        """Sync game list when a nimbus cloud is placed in the editor."""
        if self.is_test_mode:
            return
        if self.current_room and self.current_room.name == room_name:
            if cloud not in self.nimbus_clouds:
                self.nimbus_clouds.append(cloud)

    def _lookup_boss_hp_percent(self, boss_id):
        """FlagManager.boss_hp_lookup — 0-100 float for the currently active
        boss with this id, or None if it isn't spawned right now."""
        for enemy in self.enemies:
            if getattr(enemy, 'boss_id', None) == boss_id and getattr(enemy, 'active', False):
                max_hp = getattr(enemy, 'max_hp', 0)
                if not max_hp:
                    return None
                return max(0.0, min(100.0, 100.0 * getattr(enemy, 'hp', 0) / max_hp))
        return None

    def _lookup_boss_hp_value(self, boss_id):
        """FlagManager.boss_hp_value_lookup — raw current HP for the
        currently active boss with this id, or None if it isn't spawned
        right now. Counterpart to _lookup_boss_hp_percent above, for
        conditions authored against an absolute HP threshold rather than
        a percentage (e.g. "boss HP < 500")."""
        for enemy in self.enemies:
            if getattr(enemy, 'boss_id', None) == boss_id and getattr(enemy, 'active', False):
                return getattr(enemy, 'hp', None)
        return None

    def _collect_known_timer_ids(self):
        """Every timer_id referenced anywhere — currently-running timers
        plus any timer_start/timer_pause/timer_stop action authored on a
        trigger box in any room (including ones nested inside a
        dialogue_choice option's own action list) — so the Timer condition
        picker can offer "already made" timers instead of making the user
        retype the id by hand."""
        ids = set(self.timers.keys())

        def _scan(actions):
            for action in actions or []:
                if not isinstance(action, dict):
                    continue
                if action.get('type') in ('timer_start', 'timer_pause', 'timer_stop'):
                    timer_id = action.get('timer_id')
                    if timer_id:
                        ids.add(timer_id)
                for value in action.values():
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict) and item.get('actions'):
                                _scan(item.get('actions'))

        for room in self.room_manager.rooms:
            for box in getattr(room, 'trigger_boxes', None) or []:
                _scan(getattr(box, 'actions', None))

        return ids

    def _collect_known_bar_ids(self):
        """Every spam/timing bar id referenced anywhere — FlagManager's own
        bar_values (already-reported bars, live or historical) plus any
        spam_qte action's qte_id authored on a trigger box in any room
        (including nested dialogue_choice option action lists) — same
        "already made" convenience as _collect_known_timer_ids() above,
        for the Spam/Timing Bar condition picker."""
        ids = set(self.flag_manager.bar_values.keys())
        if self.spam_qte_bar.qte_id:
            ids.add(self.spam_qte_bar.qte_id)

        def _scan(actions):
            for action in actions or []:
                if not isinstance(action, dict):
                    continue
                if action.get('type') == 'spam_qte':
                    qte_id = action.get('qte_id')
                    if qte_id:
                        ids.add(qte_id)
                for value in action.values():
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict) and item.get('actions'):
                                _scan(item.get('actions'))

        for room in self.room_manager.rooms:
            for box in getattr(room, 'trigger_boxes', None) or []:
                _scan(getattr(box, 'actions', None))

        return ids

    def _get_flag_condition_names(self):
        """FlagEditor.names_refresh_callback — real, current name lists for
        the condition builder's dropdowns. Extend the empty lists here as
        the engine grows id registries for items/actors/missions/attacks/
        bosses/POIs (see the item_lookup wiring above for the pattern)."""
        return {
            'room_names': sorted(r.name for r in self.room_manager.rooms),
            'timer_names': sorted(self._collect_known_timer_ids()),
            'bar_names': sorted(self._collect_known_bar_ids()),
            # TODO: item_names, actor_names, mission_names, attack_names,
            # enemy_names, boss_names, poi_names once those id lists exist
            # somewhere central — condition rows for those kinds show
            # "(no X yet)" and are skipped until then, so this is safe to
            # leave empty in the meantime.
        }

    def _handle_set_custom_variable_action(self, var_name, mode, value=None):
        """EventRunner handler for the 'set_custom_variable' action — maps
        straight onto FlagManager's own variable store, so no other subsystem
        needs to know about it."""
        if mode == 'set':
            self.flag_manager.set_variable(var_name, value)
        elif mode == 'add':
            self.flag_manager.add_variable(var_name, value)
        elif mode == 'remove':
            self.flag_manager.remove_variable(var_name)

    def _handle_world_map_location_action(self, mode, map_name, name):
        """EventRunner handler for the 'world_map_location' action — show or
        hide a pin that's already placed on this map in the World Map
        Editor (matched by its 'name' field). The pin's own data (x, y,
        room, icon, height) always comes from
        assets/world_maps/<map_name>.json; this just tracks which
        already-placed pins are currently hidden."""
        hidden = self._wm_hidden_locations.setdefault(map_name, set())
        if mode == 'remove':
            hidden.add(name)
        elif mode == 'add':
            hidden.discard(name)

        if getattr(self, '_active_world_map_name', '') == map_name and hasattr(self, '_wm_locations'):
            self._apply_wm_location_overrides(map_name)

    def _apply_wm_location_overrides(self, map_name):
        """Re-derive self._wm_locations from assets/world_maps/<map_name>.json,
        filtering out any pins currently hidden via the 'world_map_location'
        event action, and store the result back on self._wm_locations (the
        cache the flying-scene renderer reads from)."""
        import json as _json, os as _os
        try:
            with open(_os.path.join('assets', 'world_maps', f'{map_name}.json')) as _f:
                _base = _json.load(_f).get('locations', [])
        except Exception:
            _base = []
        hidden = self._wm_hidden_locations.get(map_name, set())
        self._wm_locations = [l for l in _base if l.get('name') not in hidden]

    def _get_world_map_room_index(self):
        """Lazily build and cache a room_name -> (map_name, locations) index
        by scanning every assets/world_maps/*.json file once, so the
        Scouter's World Map section can look up which map (if any) a given
        room is pinned on without re-reading disk every frame/BFS step.
        Feeds scouter_menu.draw()'s world_map_lookup callable — see
        _world_map_lookup() below and
        ui/scouter_menu.py:_resolve_world_map_attachment, which walks this
        per room reachable from current_room via room_transitions."""
        if getattr(self, '_wm_room_index', None) is not None:
            return self._wm_room_index
        import json as _json, os as _os, glob as _glob
        index = {}
        for path in _glob.glob(_os.path.join('assets', 'world_maps', '*.json')):
            map_name = _os.path.splitext(_os.path.basename(path))[0]
            try:
                with open(path) as f:
                    locations = _json.load(f).get('locations', [])
            except Exception:
                continue
            for loc in locations:
                room_name = loc.get('room')
                if room_name and room_name not in index:
                    index[room_name] = (map_name, locations)
        self._wm_room_index = index
        return index

    def _world_map_lookup(self, room_name):
        """callable(room_name) -> (map_name, locations) | None. Passed to
        scouter_menu.draw() as world_map_lookup so the World Map section
        resolves attachment transitively — a room counts as attached if
        it, or any room it transitions with (directly or through a chain
        of further connected rooms), is a pinned location on some map —
        instead of only ever checking current_room in isolation."""
        return self._get_world_map_room_index().get(room_name)

    def _handle_item_action(self, mode, item_id, quantity=1):
        """EventRunner handler for the 'item' action — mode: 'add' | 'remove'.
        Was missing entirely (no register_handler call for it), which meant
        the mission reward migration's item(mode='add', ...) actions (see
        mission_manager._migrate_legacy_rewards) would silently no-op.
        Mirrors the world item-pickup completion path (see
        _update_item_pickup_finish) — bumps inventory plus the same
        item_picked_up/item_count bookkeeping, so a reward-granted item
        satisfies a collect_item mission objective exactly like a
        ground-pickup would."""
        inventory = getattr(self.player, 'inventory', None)
        if inventory is None:
            return
        quantity = int(quantity)
        if mode == 'add':
            for _ in range(max(0, quantity)):
                inventory.append(item_id)
                self.flag_manager.mark_item_picked_up(item_id)
                self.flag_manager.add_variable(f'item_count:{item_id}', 1)
        elif mode == 'remove':
            for _ in range(max(0, quantity)):
                if item_id in inventory:
                    inventory.remove(item_id)

    def _handle_quest_action(self, mode, quest_id):
        """EventRunner handler for the 'quest' action — mode: 'add' | 'remove'.
        A lightweight standalone quest-flag toggle (distinct from the full
        MissionManager) for content that just wants a "is quest X done"
        flag to gate on — e.g. a locked door — without objectives/rewards/
        dialogue. Mirrors FlagManager.mark_quest_finished()'s flag id, so
        `flag_is(f'quest_finished:{quest_id}')` works as a condition
        anywhere, and the 'mission' action's 'complete' mode below also
        triggers this same flag for full missions."""
        if mode == 'add':
            self.flag_manager.mark_quest_finished(quest_id)
        elif mode == 'remove':
            self.flag_manager.clear(f'quest_finished:{quest_id}')

    def _handle_modify_quest_variable_action(self, quest_id, variable_name, mode, value):
        """EventRunner handler for the 'modify_quest_variable' action —
        mode: 'set' | 'add' | 'remove'. Namespaced under the quest id so
        two different quests can each own a variable_name like 'progress'
        without colliding. Routes straight to FlagManager's own variable
        store, same as 'set_custom_variable'."""
        var_name = f'quest_var:{quest_id}:{variable_name}'
        if mode == 'set':
            self.flag_manager.set_variable(var_name, value)
        elif mode == 'add':
            self.flag_manager.add_variable(var_name, value)
        elif mode == 'remove':
            self.flag_manager.remove_variable(var_name)

    def _handle_mission_action(self, mode, mission_id):
        """EventRunner handler for the 'mission' action — mode:
        'start' | 'complete' | 'fail' | 'reset'. This is the entire glue
        between the event/action system and MissionManager: a mission
        offer's dialogue_choice 'Accept' option ends with mission('start',
        ...), and a mission's 'completed' dialogue phase ends with
        mission('complete', ...) — see
        MissionManager._migrate_legacy_dialogues for the auto-generated
        version of both. 'complete' also runs the mission's own
        reward_actions through this same event_runner, so rewards are just
        more zeni()/item()/exp()/skill() actions instead of a bespoke
        _apply_mission_rewards() code path.

        Each transition also flips a flag alongside the MissionManager
        state change — 'quest_active:<id>' / 'quest_finished:<id>' /
        'quest_failed:<id>' — mirroring what the standalone 'quest' action
        already does (see _handle_quest_action). Without this, nothing
        outside MissionManager itself (a trigger box, a locked door, a
        cutscene condition) could ever gate on "is this mission active/
        done/failed" — flag_is('quest_finished:elder_01') now works
        anywhere a condition can be built, not just inside the mission
        system. 'quest_finished' reuses FlagManager.mark_quest_finished()
        so a full mission and the lightweight standalone quest() action
        land on the exact same flag id."""
        if mode == 'start':
            self.mission_manager.accept_mission(mission_id)
            self.flag_manager.trigger(f'quest_active:{mission_id}')
            self.flag_manager.clear(f'quest_finished:{mission_id}')
            self.flag_manager.clear(f'quest_failed:{mission_id}')
        elif mode == 'complete':
            reward_actions = self.mission_manager.claim_reward(mission_id)
            self.flag_manager.mark_quest_finished(mission_id)
            self.flag_manager.clear(f'quest_active:{mission_id}')
            if reward_actions:
                self.event_runner.run_sequence(reward_actions)
        elif mode == 'fail':
            self.mission_manager.fail_mission(mission_id)
            self.flag_manager.trigger(f'quest_failed:{mission_id}')
            self.flag_manager.clear(f'quest_active:{mission_id}')
        elif mode == 'reset':
            self.mission_manager.reset_mission(mission_id)
            self.flag_manager.clear(f'quest_active:{mission_id}')
            self.flag_manager.clear(f'quest_finished:{mission_id}')
            self.flag_manager.clear(f'quest_failed:{mission_id}')

    def _handle_dialogue_box_action(self, on_complete, speaker_type, text, speaker_name=None, portrait=None):
        """EventRunner handler for the 'dialogue_box' action. Single line,
        closed on player input — see _advance_npc_dialogue()'s event branch."""
        # Snap the player to idle so walking into the trigger box doesn't
        # leave the walk/run animation frozen on screen during the dialogue
        # — same fix _start_npc_dialogue() applies for NPC conversations.
        if self.player.current_animation_state in ('walk', 'run'):
            self.player.enter_idle()
        self.player.is_running = False

        name = speaker_name or ('Narrator' if speaker_type == 'narrator' else '')
        self._event_dialogue_active = True
        self.dialogue_box.show(text, name, True, None, portrait_key=portrait,
                                is_narrator=(speaker_type == 'narrator'),
                                on_close=lambda: self._on_event_dialogue_closed(on_complete))

    def _on_event_dialogue_closed(self, on_complete):
        self._event_dialogue_active = False
        on_complete()

    def _handle_dialogue_choice_action(self, prompt, options, on_choice):
        """EventRunner handler for the 'dialogue_choice' action. `options`
        is the raw [{'text':..., 'actions':[...]}] list from event_actions —
        the runner itself splices the picked option's actions back into the
        sequence, so all we do here is show the menu and forward whichever
        index the player picked."""
        if self.player.current_animation_state in ('walk', 'run'):
            self.player.enter_idle()
        self.player.is_running = False

        labels = [opt.get('text', '') for opt in (options or [])]
        self._event_dialogue_active = True
        self.dialogue_choice_menu.open(
            labels, prompt=prompt or "",
            on_choice=lambda index: self._on_event_choice_made(on_choice, index))

    def _on_event_choice_made(self, on_choice, index):
        self._event_dialogue_active = False
        on_choice(index)

    def _handle_timer_start_action(self, timer_id, duration):
        """EventRunner handler for the 'timer_start' action — (re)starts a
        countdown under `timer_id` from `duration` seconds and makes it the
        one shown on the HUD. Non-blocking: fires and the action sequence
        continues immediately."""
        self.timers[timer_id] = {
            'remaining': max(0.0, float(duration)),
            'running':   True,
        }
        self._active_timer_id = timer_id
        # Flag trigger counterpart to the action itself — see
        # FlagManager.mark_timer_started()'s docstring. Without this,
        # any "Flag Is Set: timer_started:<id>" condition can never
        # become true, since nothing else ever sets that flag.
        self.flag_manager.mark_timer_started(timer_id)

    def _handle_timer_pause_action(self, timer_id):
        """EventRunner handler for the 'timer_pause' action — freezes the
        countdown in place without resetting it. A later timer_start on the
        same id resumes/restarts it."""
        timer = self.timers.get(timer_id)
        if timer:
            timer['running'] = False
            self.flag_manager.mark_timer_paused(timer_id)

    def _handle_timer_stop_action(self, timer_id):
        """EventRunner handler for the 'timer_stop' action — ends and hides
        the timer entirely."""
        self.timers.pop(timer_id, None)
        if self._active_timer_id == timer_id:
            self._active_timer_id = None
        self.flag_manager.mark_timer_ended(timer_id)

    def _handle_zeni_action(self, mode, amount):
        """EventRunner handler for the 'zeni' action — mode: 'set' | 'add' | 'remove'.
        Mirrors the direct player.zeni mutation done elsewhere (e.g. the
        zeni-pickup collection code), just routed through the event system.
        Clamped at 0 so 'remove' can't push zeni negative."""
        current = getattr(self.player, 'zeni', 0)
        if mode == 'set':
            new_value = amount
        elif mode == 'add':
            new_value = current + amount
        elif mode == 'remove':
            new_value = current - amount
        else:
            return
        self.player.zeni = max(0, new_value)

    def _handle_level_action(self, mode, amount, character_id=None):
        """EventRunner handler for the 'level' action — mode: 'set' | 'add' | 'remove'.
        Player only tracks a single active character's level right now (no
        per-character roster/save data exists yet — see Player.character),
        so if character_id is given and doesn't match the currently active
        character, this is a no-op rather than silently applying to the
        wrong character. Clamped to [1, game_config.max_level] and recomputes
        exp_to_next_level the same way Player.level_up() does, so the XP bar
        doesn't go stale after a manual level change."""
        if character_id and getattr(self.player, 'character', None) != character_id:
            return
        current = self.player.level
        if mode == 'set':
            new_value = amount
        elif mode == 'add':
            new_value = current + amount
        elif mode == 'remove':
            new_value = current - amount
        else:
            return
        self.player.level = max(1, min(new_value, self.game_config.max_level))
        self.player.exp_to_next_level = self.game_config.get_xp_for_level(self.player.level)

    def _handle_exp_action(self, mode, amount, character_id=None):
        """EventRunner handler for the 'exp' action — mode: 'set' | 'add' | 'remove'.
        Same active-character guard as 'level' (see _handle_level_action) —
        no-op if character_id doesn't match the currently active character,
        since there's no per-character exp storage yet either.

        'add' routes through Player.gain_exp(), which already handles
        cascading level-ups the same way killing an enemy does (see
        game.py's XP-reward call site). 'set'/'remove' just adjust the raw
        exp pool without cascading, consistent with 'set' not cascading
        for the zeni/level actions either."""
        if character_id and getattr(self.player, 'character', None) != character_id:
            return
        if mode == 'add':
            self.player.gain_exp(amount, self.game_config)
            # Mission rewards (and any other designer-triggered exp grant)
            # now route through this same action instead of the old
            # Game._apply_mission_rewards, which used to call
            # _show_level_up_if_pending() itself — keep that behavior here
            # so a reward that crosses a level threshold still plays the
            # level-up sequence instead of silently banking the level.
            self._show_level_up_if_pending()
            return
        current = self.player.exp
        if mode == 'set':
            new_value = amount
        elif mode == 'remove':
            new_value = current - amount
        else:
            return
        self.player.exp = max(0, new_value)

    def _handle_stat_action(self, mode, stat_name, amount, character_id=None):
        """EventRunner handler for the 'stat' action — mode: 'set' | 'add' | 'remove'.
        Same active-character guard as 'level'/'exp'. stat_name must be one
        of Player.stats' keys (strength, ki_power, vitality, energy, speed,
        defense, ki_regen) — anything else is a no-op rather than crashing
        the sequence on a KeyError. Clamped to [1, game_config.max_stat_value]
        like apply_stat_point() clamps manually-spent points, then calls
        update_derived_stats() so max_hp/max_ki/ki_regen_interval stay in
        sync with the new stat block (same as apply_stat_point does)."""
        if character_id and getattr(self.player, 'character', None) != character_id:
            return
        if stat_name not in self.player.stats:
            return
        current = self.player.stats[stat_name]
        if mode == 'set':
            new_value = amount
        elif mode == 'add':
            new_value = current + amount
        elif mode == 'remove':
            new_value = current - amount
        else:
            return
        self.player.stats[stat_name] = max(1, min(new_value, self.game_config.max_stat_value))
        self.player.update_derived_stats()

    def _handle_resource_action(self, mode, resource_name, amount):
        """EventRunner handler for the 'resource' action — mode: 'set' | 'add' | 'remove'.
        resource_name: 'health' | 'energy' | 'transformation_gauge'.

        health -> player.hp (clamped to [0, max_hp]), energy -> player.ki
        (clamped to [0, max_ki]) — same attrs used throughout player.py's
        combat code.

        transformation_gauge is NOT wired — same gap flagged in this file's
        own comment above the (also-unwired) 'player_resource' live lookup:
        "need the real health/energy/transformation_gauge attr names."
        TransformationSystem's source isn't available here to confirm the
        actual attribute, so this is a no-op for that resource rather than
        guessing at a name and silently touching the wrong thing. Tell me
        the real attribute on self.player.transformation and I'll wire it
        the same way as health/energy.
        """
        if resource_name == 'health':
            current, cap = self.player.hp, self.player.max_hp
        elif resource_name == 'energy':
            current, cap = self.player.ki, self.player.max_ki
        elif resource_name == 'transformation_gauge':
            return  # not wired yet — see docstring above
        else:
            return

        if mode == 'set':
            new_value = amount
        elif mode == 'add':
            new_value = current + amount
        elif mode == 'remove':
            new_value = current - amount
        else:
            return
        new_value = max(0, min(new_value, cap))

        if resource_name == 'health':
            self.player.hp = new_value
        elif resource_name == 'energy':
            self.player.ki = new_value

    def _handle_skill_action(self, mode, skill_id):
        """EventRunner handler for the 'skill' action — mode: 'add' | 'remove'.
        Skills/attacks are just entries in player.equipped_attacks (a flat
        list of skill_id strings, same shape the 'player_has_skill' live
        lookup already assumes). 'add' is idempotent — no duplicate entries
        if the skill's already equipped."""
        equipped = getattr(self.player, 'equipped_attacks', None)
        if equipped is None:
            return
        if mode == 'add':
            if skill_id not in equipped:
                equipped.append(skill_id)
        elif mode == 'remove':
            if skill_id in equipped:
                equipped.remove(skill_id)

    def _handle_charged_melee_action(self, mode):
        """EventRunner handler for the 'charged_melee' action — mode:
        'add' | 'remove'. Toggles player.charged_melee_enabled, the gate
        Player.start_charging_melee() should check before letting a held
        E roll into the charge wind-up (see the NOTE in
        _reload_attack_config() below — that guard needs adding in
        player.py, which isn't part of this action-system module).
        Unlike _handle_skill_action/_handle_transformation_action, this
        isn't a list of ids to add/remove from — it's a single flag, so
        'add' just sets it True (charging allowed) and 'remove' sets it
        False (charging blocked); anything else is a no-op."""
        if mode == 'add':
            self.player.charged_melee_enabled = True
        elif mode == 'remove':
            self.player.charged_melee_enabled = False

    def _handle_transformation_action(self, mode, form_id):
        """EventRunner handler for the 'transformation' action — mode:
        'add' | 'remove'. Mirrors _handle_skill_action above: player.
        unlocked_transformations is a flat list of form-id strings (see
        _transformation_form_id() / _reload_attack_config()'s
        initialization of it), gating which of the current character's
        configured transformation forms (costumes with a
        "{owning_costume}/transformations/{form}" entry) are actually
        available to trigger. 'add' is idempotent — no duplicate entries
        if the form's already unlocked.

        Unlike _handle_skill_action, a grant/revoke here can change
        whether the COSTUME the player is currently wearing offers a
        transformation at all, so this re-runs that gate immediately via
        _refresh_transformation_gate() rather than waiting for the next
        costume/character switch. It deliberately does NOT call
        _reload_attack_config() again, which would also reset
        equipped_attacks/ki_mode_config from disk and stomp any runtime
        'skill' grants already applied this session."""
        unlocked = getattr(self.player, 'unlocked_transformations', None)
        if unlocked is None:
            return
        if mode == 'add':
            if form_id not in unlocked:
                unlocked.append(form_id)
        elif mode == 'remove':
            if form_id in unlocked:
                unlocked.remove(form_id)
        self._refresh_transformation_gate()

    def _handle_character_list_action(self, mode, character_id):
        """EventRunner handler for the 'character_list' action — mode: 'add' | 'remove'.
        Governs player.playable_characters, the switchable roster shown in the
        save point's "Switch Characters" menu. Being "added" (on disk, see
        character_creator.discover_characters()) no longer implies playable —
        'add' unlocks character_id for switching, 'remove' revokes it. No-op
        if character_id isn't a real added character, so a stale event can't
        unlock something that doesn't exist."""
        if character_id not in character_creator.discover_characters():
            return
        roster = self.player.playable_characters
        if mode == 'add':
            if character_id not in roster:
                roster.append(character_id)
        elif mode == 'remove':
            if character_id in roster:
                roster.remove(character_id)

    def _handle_screen_shake_action(self, intensity, duration=0.3):
        """EventRunner handler for the 'screen_shake' action. Non-blocking —
        just kicks off Camera.start_shake() and the sequence continues
        immediately; the shake itself decays over its own duration via
        Camera.update(), same as the other start_shake() call sites."""
        self.camera.start_shake(intensity=intensity, duration=duration)

    def _handle_spam_qte_action(self, on_complete, qte_id=None, fill_per_press=0.08,
                                 drain_rate=0.15, start_progress=0.0):
        """EventRunner handler for the 'spam_qte' action — blocking. Arms
        the bottom-middle mash-E-or-Q bar (see ui/spam_qte.py) and stashes
        on_complete; _update_spam_qte() fires it the frame the bar fills.
        No fail state — the sequence simply waits however long it takes."""
        self._event_spam_qte_on_complete = on_complete
        self.spam_qte_bar.start(qte_id=qte_id, fill_per_press=fill_per_press,
                                 drain_rate=drain_rate, start_progress=start_progress)
        # Report the starting percent immediately so a condition checked the
        # same frame (e.g. right after this action in the sequence) already
        # sees it, rather than waiting one frame for _update_spam_qte.
        if qte_id:
            self.flag_manager.set_bar_percent(qte_id, self.spam_qte_bar.progress * 100)

    def _update_spam_qte(self, dt):
        """Advance the spam QTE bar, mirror its live progress into
        FlagManager (set_bar_percent — also latches the 'bar_reached:
        <qte_id>:<0|50|100>' flags and feeds check_bar()), and fire its
        stored on_complete the instant it fills. No-op whenever no
        spam_qte action is running."""
        if not self.spam_qte_bar.active:
            return
        completed = self.spam_qte_bar.update(dt)
        if self.spam_qte_bar.qte_id:
            self.flag_manager.set_bar_percent(self.spam_qte_bar.qte_id, self.spam_qte_bar.progress * 100)
        if completed:
            on_complete = self._event_spam_qte_on_complete
            self._event_spam_qte_on_complete = None
            if on_complete:
                on_complete()

    def _handle_weather_action(self, mode, weather_type=None):
        """EventRunner handler for the 'weather' action — mode: 'set' | 'stop'.
        Ambient room weather, independent of a cutscene's own weather system
        (core/cutscene_runtime.py's weather_start/weather_stop actions) —
        reuses that module's _WeatherEffect class so the art and fade timings
        match. 'set' cross-fades to weather_type (or just resumes the
        fade-in from its current opacity if it's already the active type);
        'stop' fades whatever's currently active out. Actual per-frame
        advancement happens in _update_room_weather()."""
        from core.cutscene_runtime import _WeatherEffect, _WEATHER_START_FADE_IN, _WEATHER_STOP_FADE_OUT
        if mode == 'set':
            if self.room_weather is None or self.room_weather.weather_type != weather_type:
                self.room_weather            = _WeatherEffect(weather_type)
                self.room_weather.opacity    = 0.0
                self._room_weather_fade_from = 0.0
            else:
                self._room_weather_fade_from = self.room_weather.opacity
            self._room_weather_fade_to      = 1.0
            self._room_weather_fade_dur     = _WEATHER_START_FADE_IN
            self._room_weather_fade_elapsed = 0.0
        elif mode == 'stop':
            if self.room_weather is not None:
                self._room_weather_fade_from    = self.room_weather.opacity
                self._room_weather_fade_to      = 0.0
                self._room_weather_fade_dur     = _WEATHER_STOP_FADE_OUT
                self._room_weather_fade_elapsed = 0.0

    def _update_room_weather(self, dt):
        """Advance ambient room weather and its opacity tween — mirrors
        CutsceneRuntime._tick_weather_fade(). Paused while a cutscene is
        playing, since a cutscene drives its own separate weather instead."""
        if self.room_weather is None or self.active_cutscene_runtime:
            return
        self.room_weather.update(dt)
        if self._room_weather_fade_dur > 0.0:
            self._room_weather_fade_elapsed = min(
                self._room_weather_fade_dur, self._room_weather_fade_elapsed + dt)
            t     = self._room_weather_fade_elapsed / self._room_weather_fade_dur
            eased = t * t * (3.0 - 2.0 * t)  # smoothstep
            self.room_weather.opacity = (self._room_weather_fade_from
                + (self._room_weather_fade_to - self._room_weather_fade_from) * eased)
            if self._room_weather_fade_elapsed >= self._room_weather_fade_dur:
                self._room_weather_fade_dur = 0.0
                if self._room_weather_fade_to <= 0.0:
                    self.room_weather = None

    def _handle_room_music_action(self, mode, track=None):
        """EventRunner handler for the 'room_music' action — mode: 'set' | 'stop'.

        Writes the change back onto the *current room's* persisted
        `music_track` string and saves it, so the track keeps playing on
        every future entry into this room too — not just this one time.

        'set'  — replaces the room's music_track with `track` and applies
                 it immediately via _apply_room_music (which already no-ops
                 if that track is already playing).
        'stop' — actually stops whatever music is currently playing (no
                 matter how it was started — a room's persisted track, a
                 direct play_music() call, a cutscene, etc.) AND clears the
                 room's persisted music_track so future entries don't force
                 a track back on. Stop means stop; it doesn't ask for a
                 specific track to stop.

        Guarded to skip the (potentially expensive) room mutation/save when
        the room's persisted music is already in the requested state — the
        stop_music() call itself is cheap and safe to call redundantly, so it
        always runs. RoomPersistence.save_room() is a synchronous full JSON
        dump of the whole room — there's no dirty-flag batching underneath it
        despite game.py's update() comment suggesting otherwise
        (flush_dirty_rooms doesn't actually exist on RoomManagerWithPersistence).
        Without this guard, a repeat-fire trigger box (once=False) with a
        room_music action covering ground the player stands on would call
        this — and therefore save_room() — every single frame it overlaps,
        which is what was causing the walking stutter.
        """
        if not self.current_room:
            return

        existing = getattr(self.current_room, 'music_track', '')

        if mode == 'set':
            if not track:
                return
            if existing == track:
                self._apply_room_music(self.current_room)  # cheap no-op if already playing
                return
            self.music_track = track
            self.current_room.music_track = track
            self._apply_room_music(self.current_room)

        elif mode == 'stop':
            # Always actually stop the music — regardless of whether this
            # room has a persisted track, and regardless of what track (if
            # any) is currently playing.
            self.sound_manager.stop_music()
            if not existing:
                return  # Already no track persisted for this room — nothing to save.
            self.music_track = ''
            self.current_room.music_track = ''

        self.room_manager.save_room(self.current_room)

    def _handle_play_sound_action(self, sound_id):
        """EventRunner handler for the 'play_sound' action. Non-blocking —
        fires the one-shot sfx and the sequence continues immediately,
        same as _handle_screen_shake_action. Routes through
        self.sound_manager.play_sfx(), which is exactly what
        SoundEngine.play_sound() ends up doing — the same call every other
        sfx site (footsteps, melee, impacts, ...) uses, so sound_id here
        should match one of the stems AudioAssetLoader loaded from
        assets/audio/sfx/ (any subfolder)."""
        self.sound_manager.play_sfx(sound_id)

    def _open_pause_menu(self):
        """Fires once the pre-pause fade-out (started from the K_ESCAPE
        handler in _handle_game_keydown) reaches full black. Opens the
        pause menu on top of that black screen — the transition_controller
        overlay is drawn behind the pause menu (see _draw_ui) and is left
        at full alpha rather than fading back in, so the menu sits on a
        solid black backdrop exactly like the original game. The reverse
        fade-in plays only once the menu is actually closed — see the
        result == 'close' handling above."""
        self._pause_fade_active = False
        self.pause_menu.open(self.player)

    def _open_scouter_menu(self):
        """Fires once the pre-open fade-out (started from the K_RETURN
        handler in _handle_game_keydown) reaches full black — mirrors
        _open_pause_menu() above."""
        self._scouter_fade_active = False
        self.scouter_menu.open(self.player)

    def _handle_screen_fade_action(self, on_complete, direction, duration=0.5):
        """EventRunner handler for the 'screen_fade' action — direction:
        'in' | 'out'. Blocking — routes to TransitionController's standalone
        plain fade (see start_plain_fade()), which fires on_complete once the
        fade finishes so the sequence resumes."""
        self.transition_controller.start_plain_fade(direction, duration, on_complete)

    def _handle_change_map_action(self, on_complete, room_name, spawn_x=None, spawn_y=None, wait=True):
        """EventRunner handler for the 'change_map' action — was missing
        entirely (no register_handler call for it), so trigger boxes fired
        the action but nothing ever happened.

        Swaps the active room to room_name and repositions the player at
        (spawn_x, spawn_y), reusing the same fade-to-black bridge as the
        room-transition zones (_check_room_transitions) and the cutscene
        runtime's change_room callback (_cutscene_change_room) so the room
        doesn't just pop into view. Falls back to the target room's centre
        if spawn_x/spawn_y weren't set (mirrors the world-map landing
        sequence's fallback).

        'wait' — True: the action sequence resumes once the room has
        actually swapped (right as the fade-out completes), then the
        fade-in plays out in the background. False: the sequence resumes
        immediately and the whole fade-out/swap/fade-in happens in the
        background without holding anything up — same "wait" semantics as
        the other actions that expose it (e.g. play_character_animation).
        """
        def _do_swap():
            target_room = self.room_manager.get_room_by_name(room_name)
            if not target_room:
                print(f'[Game] change_map: room not found: {room_name}')
                if wait:
                    on_complete()
                return

            # Sync editor tiles into the room object (same as test-mode start
            # and _cutscene_change_room).
            te = getattr(self.room_editor, 'tileset_editor', None)
            if te and room_name in getattr(te, 'room_tiles', {}):
                target_room.tiles = te.room_tiles[room_name][:]
            elif not hasattr(target_room, 'tiles'):
                target_room.tiles = []

            self.room_manager.current_room = target_room
            self.current_room              = target_room
            if self.is_test_mode:
                self._load_room_objects_as_copies(target_room)
            else:
                self._load_room_objects(target_room)

            _spawn_x = spawn_x if spawn_x is not None else target_room.width  / 2
            _spawn_y = spawn_y if spawn_y is not None else target_room.height / 2
            self.player.x = _spawn_x
            self.player.y = _spawn_y

            # Re-centre the camera on the new spawn, clamped to room bounds.
            # update(..., dt=0) skips the smooth-follow ease and also resets
            # the camera's internal true position — see the save-load
            # comment above for why a plain camera.x/y assignment isn't
            # enough on its own.
            self.camera.update(self.player, target_room.width, target_room.height, dt=0)

            self.flag_manager.mark_room_visited(room_name)

            self.transition_controller.start_plain_fade('in', 0.3, None)
            if wait:
                on_complete()

        self.transition_controller.start_plain_fade('out', 0.3, _do_swap)
        if not wait:
            on_complete()

    def _handle_set_player_location_action(self, x, y):
        """EventRunner handler for the 'set_player_location' action — same
        gap as change_map (missing register_handler call), but a much
        simpler case: no room swap, just an instant reposition of the
        player within the *current* room. No fade, no on_complete — the
        camera doesn't need to be re-centred by hand either, since
        Camera.update() already re-follows the player every frame.

        rotation was dropped from the schema (ACTION_SCHEMA in
        event_editor.py) — Player has no facing/rotation concept this
        action could plausibly drive, so it was a dead field."""
        if not self.current_room:
            return
        self.player.x = max(0, min(x, self.current_room.width))
        self.player.y = max(0, min(y, self.current_room.height))

    def _on_save_point_placed(self, save_point):
        """Sync game list when a save point is placed in the editor."""
        if save_point not in self.save_points:
            self.save_points.append(save_point)

    def _on_save_point_deleted(self, save_point):
        """Sync game list when a save point is removed in the editor."""
        if save_point in self.save_points:
            self.save_points.remove(save_point)

    def _on_trigger_box_placed(self, box, room_name):
        """Sync game list when a trigger box is placed OR edited in the
        editor, and persist so it isn't lost.

        This callback fires both when a brand-new box is placed and when
        an existing box's conditions/actions are edited via
        TriggerBox.open_event_editor() — there's no separate "edited"
        signal. If the box already exists in the live self.trigger_boxes
        list (matched by box_id), refresh its conditions/actions in place
        rather than appending a duplicate — this is also what makes edits
        show up immediately even while self.is_test_mode is True, since
        test mode runs its own copied TriggerBox objects (see
        _load_room_objects_as_copies) that wouldn't otherwise pick up
        changes made to a different box object of the same id."""
        if self.current_room and self.current_room.name == room_name:
            existing = next((b for b in self.trigger_boxes if b.box_id == box.box_id), None)
            if existing is not None:
                existing.conditions = box.conditions
                existing.actions = box.actions
            elif not self.is_test_mode:
                self.trigger_boxes.append(box)

        room = self.room_manager.get_room_by_name(room_name)
        if room:
            if not hasattr(room, 'trigger_boxes') or room.trigger_boxes is None:
                room.trigger_boxes = []
            if box not in room.trigger_boxes:
                room.trigger_boxes.append(box)
            self.room_manager.save_room(room)

    def _on_trigger_box_deleted(self, box, room_name):
        """Sync game list when a trigger box is removed in the editor.

        room_music's 'set' mode writes the track onto the *room's* persisted
        music_track field (see _handle_room_music_action) so it survives
        future room entries — that field is deliberately decoupled from any
        one trigger box. But that means deleting the box that originally set
        it did nothing to that field: room.music_track just sat there
        forever, so _apply_room_music() kept reapplying that track on every
        future entry (and across saves) even with no box left to set it.

        Fix: if the deleted box had a room_music 'set' action whose track
        matches the room's current persisted music_track, and no *other*
        remaining trigger box in the room also sets that same track, clear
        music_track (mirroring the 'stop' branch of
        _handle_room_music_action) and stop it if it's what's currently
        playing. If another box still sets the same track, leave it alone —
        that box is still a valid source for it.
        """
        if box in self.trigger_boxes:
            self.trigger_boxes.remove(box)

        room = self.room_manager.get_room_by_name(room_name)
        if room:
            deleted_tracks = {
                a.get('track') for a in getattr(box, 'actions', []) or []
                if a.get('type') == 'room_music' and a.get('mode') == 'set' and a.get('track')
            }
            existing_track = getattr(room, 'music_track', '')
            if deleted_tracks and existing_track in deleted_tracks:
                remaining_boxes = getattr(room, 'trigger_boxes', None) or []
                still_owned = any(
                    a.get('type') == 'room_music' and a.get('mode') == 'set'
                    and a.get('track') == existing_track
                    for other in remaining_boxes
                    for a in (getattr(other, 'actions', []) or [])
                )
                if not still_owned:
                    room.music_track = ''
                    if self.music_track == existing_track:
                        self.music_track = ''
                    if self.current_room is room and self.sound_engine.current_music == existing_track:
                        self.sound_manager.stop_music()

            self.room_manager.save_room(room)

    def _apply_room_music(self, room):
        """Apply the given room's persisted music track, if any, to the
        currently playing track.

        Design rule: if the room has no music track set, do nothing —
        whatever track is already playing keeps playing uninterrupted.
        """
        if not room:
            return

        track = getattr(room, 'music_track', '')
        if not track:
            return  # No track set for this room — leave music as-is

        if track == self.sound_engine.current_music:
            return  # Already playing this track — avoid restarting it on every room entry

        print(f"[room_music] switching to '{track}' for room '{getattr(room, 'name', '?')}'")
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
            print("[world_map_music] no map_name given — skipping")
            return

        import json as _json
        import os as _os

        path = _os.path.join('assets', 'world_maps', f'{map_name}.json')
        try:
            with open(path) as f:
                data = _json.load(f)
        except Exception as e:
            print(f"[world_map_music] could not open {path}: {e}")
            return  # No saved map data yet — leave music as-is

        track = data.get('music', '')
        if not track:
            print(f"[world_map_music] '{map_name}' has no music set — leaving current track alone")
            return  # No track set for this map — keep whatever is already playing

        if track == self.sound_engine.current_music:
            print(f"[world_map_music] '{track}' already current_music — skipping restart")
            return  # Already playing this track — avoid restarting it on re-entry

        if track not in self.sound_engine.music_tracks:
            print(f"[world_map_music] WARNING: '{track}' (set for map '{map_name}') "
                  f"is not in sound_engine.music_tracks — was it loaded from assets/audio/music?")

        print(f"[world_map_music] switching to '{track}' for map '{map_name}'")
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

    def _cutscene_spawn_attack(self, actor, params, attack_action):
        """Wired into CutsceneRuntime.on_spawn_attack — fires when a scripted
        'attack' action reaches its release_delay. Spawns the real visual
        effect into the same live object lists the actual gameplay attacks
        use, so cutscene attacks render identically to in-game ones.

        `actor` is the CutsceneActor that performed the attack (its wrapped
        entity gives us x/y/direction); `params` is the action's raw params
        dict from the cutscene JSON; `attack_action` is the _AttackAction
        instance (carries target_x/target_y if the action aimed at a point).

        kiblast/melee/firebeam all construct their effect objects using the
        same classes/signatures the real spawn sites in this file use:
        Projectile(x + ox, y + oy, direction) (same offset
        get_blast_spawn_position() applies for the pending_blast=='ready'
        block), entity.melee_attack() (same as the KEYUP handler's
        self.player.melee_attack(), since a 'player'-type cutscene actor's
        entity IS a real entities.player.Player), and a directly-
        instantiated, similarly offset BeamAttack(x + ox, y + oy, direction)
        (attacks/beam.py) for firebeam, since there's no player charge state
        to route through mid-cutscene. 'charge' stays a no-op — it's just
        the wind-up pose.

        Unlike normal gameplay attacks, none of self.projectiles/
        melee_attacks/cutscene_beams get routed through self.layer_manager
        during a cutscene (that pass is skipped entirely while
        active_cutscene_runtime is set) — see the cutscene draw branch
        near draw_actors(), which draws these three lists directly instead.
        """
        attack_type = params.get('attack_type', 'melee')
        entity      = actor.entity
        x, y        = entity.x, entity.y
        direction   = getattr(entity, 'direction', 'down')

        # Real gameplay never spawns a kiblast/beam at the caster's raw x/y —
        # it nudges the spawn point per-direction first (see
        # Player._get_spawn_offset(), used by get_blast_spawn_position() and
        # fire_beam_auto() etc.) so the effect originates roughly where the
        # character's hands are instead of dead-center on their body.
        # core.cutscene_actor duplicates that same offset table (see its
        # _spawn_offset()) so this reads identically whether `entity` is the
        # real self.player or a throwaway enemy/NPC cutscene actor without a
        # Player instance's own _get_spawn_offset() to call.
        from core.cutscene_actor import _spawn_offset
        ox, oy = _spawn_offset(direction)

        if attack_type == 'kiblast':
            self.projectiles.append(Projectile(x + ox, y + oy, direction))
            self.sound_manager.play_sfx(random.choice(('kiblast1', 'kiblast2')))

        elif attack_type == 'melee':
            # A cutscene 'player'-type actor's entity is a real
            # entities.player.Player instance (see CutsceneEditor.
            # _entity_factory) — the exact same class self.player is — so
            # it already has melee_attack(), and calling it here builds a
            # real MeleeAttack the same way the KEYUP handler does at
            # self.player.melee_attack() (see _handle_action_key above).
            # Non-player actors (enemies/NPCs/bosses) don't have this
            # method, so this quietly no-ops for them rather than crashing
            # a whole cutscene over a mis-set attack_type.
            if hasattr(entity, 'melee_attack'):
                melee = entity.melee_attack()
                if melee:
                    melee.hit_something = False
                    self.melee_attacks.append(melee)

        elif attack_type == 'firebeam':
            # Fires a plain BeamAttack (attacks/beam.py's default
            # attack_name='kamehameha') directly from the actor's current
            # position/direction, bypassing the player's own hold-to-
            # charge state machine entirely — cutscenes don't have a
            # button being held, they just have a scripted release moment.
            # Tracked in self.cutscene_beams (ticked/wall-obstructed/drawn
            # alongside every other cutscene-attack list — see Game.update()
            # and the cutscene draw branch) rather than self.player.
            # current_beam, since `entity` here is the throwaway cutscene
            # actor, not the real self.player.
            beam = BeamAttack(x + ox, y + oy, direction)
            # Held fully open for (duration - release_delay) — i.e. for as
            # long as attack_action's pose still has left to play after the
            # release moment — then closes itself back down. Falls back to
            # a short default hold if the action didn't specify a duration,
            # so a beam still reads as a beam instead of flickering open
            # for a single frame.
            hold_time = params.get('duration', 0.0) - params.get('release_delay', 0.0)
            beam._cutscene_release_timer = hold_time if hold_time > 0 else 0.4
            self.cutscene_beams.append(beam)
            self.sound_manager.play_sfx('beam')

        elif attack_type == 'charge':
            # 'charge' is typically just the wind-up pose with no effect of
            # its own — left as a no-op unless you want a charge particle/
            # sfx here.
            pass

        # Optional cosmetic-only damage resolution: only fires if the action
        # explicitly opted in and gave an aim point. Deliberately bypasses
        # the normal collision loop — it resolves directly against a named
        # target actor rather than reproducing hitbox/i-frame/death logic
        # for a system (cutscene actors) that doesn't otherwise run it.
        if params.get('deal_damage') and attack_action.target_x is not None:
            target_actor = self._find_cutscene_actor_near(
                attack_action.target_x, attack_action.target_y)
            if target_actor is not None and hasattr(target_actor.entity, 'take_damage'):
                # TODO: pick a real damage amount — e.g. from params.get('damage', ...)
                # or from the attacker's stats, however this game computes it elsewhere.
                target_actor.entity.take_damage(params.get('damage', 0))

    def _find_cutscene_actor_near(self, x, y, max_dist=48.0):
        """Return the live CutsceneActor whose entity is closest to (x, y),
        within max_dist world units, or None. Used to resolve an 'attack'
        action's target_x/target_y into an actual actor for deal_damage.
        """
        runtime = self.active_cutscene_runtime
        if runtime is None:
            return None
        best_actor = None
        best_dist  = max_dist
        for a in runtime.actors.values():
            dx = a.entity.x - x
            dy = a.entity.y - y
            dist = math.hypot(dx, dy)
            if dist <= best_dist:
                best_dist  = dist
                best_actor = a
        return best_actor

    def _update_trigger_boxes(self, dt):
        """Check the current room's trigger boxes against the player each
        frame and fire the corresponding flag when one triggers.

        Trigger boxes are lightweight — no cooldown/state-machine tick
        needed like cutscene triggers, just a per-frame overlap (and, for
        KeyTriggerBox, interact-key) check via TriggerBox.check().
        """
        if not self.trigger_boxes or not self.player:
            return

        keys = pygame.key.get_pressed()
        for box in self.trigger_boxes:
            if box.should_fire(self.player, keys_pressed=keys,
                                evaluate_conditions=self.flag_manager.evaluate_conditions):
                self.flag_manager.mark_box_triggered(box.box_id)
                if box.actions:
                    self.event_runner.run_sequence(box.actions)

    def _switch_active_room(self, room_name):
        """Swap the active room to `room_name`, re-syncing its tiles/objects
        and notifying the mission/flag managers, same as a normal room
        transition. Returns True on success, False if the room doesn't exist.

        Shared by the cutscene runtime's change_room action and by the
        cutscene launch path, which uses it to jump to a cutscene's
        authored home room (the top-level "room" key in the cutscene JSON)
        before spawning actors.
        """
        target_room = self.room_manager.get_room_by_name(room_name)
        if not target_room:
            return False

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
        self.flag_manager.mark_room_visited(room_name)
        return True

    def _update_cutscene_triggers(self, dt):
        """Tick the active cutscene runtime, or check for new trigger fires.

        Fade state machine
        ──────────────────
        'fade_out' → alpha rises to 255 (screen goes black)
                   → on reaching 255, switch to 'start'
        'start'    → runtime is constructed (and, if needed, the room switch
                     happens) here. If we arrived via 'fade_out' the screen
                     is already fully black, so this heavy lifting happens
                     hidden and we continue into 'fade_in'. If no room
                     switch was needed we got here directly with alpha still
                     at 0 — nothing to hide, so we skip 'fade_in' and clear
                     the state immediately (unchanged instant-launch path).
        'fade_in'  → alpha falls to 0 (screen becomes clear) while the
                     cutscene plays underneath
                   → on reaching 0, clear _csf_state; cutscene runs normally
        None       → normal cutscene playback, or no cutscene active

        When the cutscene finishes we kick off the fade-in leg.
        """
        # ── Fade-out: hide the screen before doing a (possibly slow) room
        # switch, so any loading hitch happens behind black instead of in
        # full view. Only entered when the cutscene's home room differs
        # from the current one — see _start_cutscene. ───────────────────────
        if self._csf_state == 'fade_out':
            self._csf_alpha = min(255.0, self._csf_alpha + self._csf_speed * dt)
            if self._csf_alpha >= 255.0:
                self._csf_alpha = 255.0
                self._csf_state = 'start'
            return

        # ── Start frame: launch runtime, hidden behind black if we just
        # faded out, or immediately if no room switch was needed ──────────
        if self._csf_state == 'start':
            data               = self._csf_pending
            self._csf_pending  = None
            _faded_out_first   = self._csf_alpha >= 254.0
            if not _faded_out_first:
                self._csf_alpha = 0.0
            if data is not None:
                try:
                    # Switch to the cutscene's authored home room (top-level
                    # "room" key) before spawning anything, so actor/camera
                    # coordinates line up with what's on screen. This mirrors
                    # CutsceneEditor._get_current_room(), which uses the same
                    # key as the base room for preview — but nothing on the
                    # runtime side previously read it, so a cutscene fired
                    # from a room other than its authored one played out
                    # against the wrong tiles with no visible actors. A
                    # change_room action later in the timeline still takes
                    # over normally once it fires.
                    _base_room_name = (data.get('room') or '').strip()
                    if _base_room_name and (not self.current_room
                                             or self.current_room.name != _base_room_name):
                        if not self._switch_active_room(_base_room_name):
                            print(f'[Game] cutscene base room not found: {_base_room_name}')

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
                        """Callback wired into the cutscene runtime's change_room
                        action: swaps the active room, re-syncs its tiles/objects,
                        and notifies the mission manager the new room was entered."""
                        if not self._switch_active_room(room_name):
                            print(f'[Game] change_room: room not found: {room_name}')

                    self.active_cutscene_runtime.on_change_room = _cutscene_change_room
                    self.active_cutscene_runtime.on_spawn_attack = self._cutscene_spawn_attack
                    self.active_cutscene_runtime.seek(0.0)

                    # After seek() resets actor positions to their scripted spawn,
                    # snap the player actor to the real player's world position so
                    # any move_to tweens start from where the player actually is.
                    # Skipped when sync_player_position=False (see _start_cutscene's
                    # docstring) — the fresh-boot intro cutscene has no real player
                    # position to sync from yet, so the actor stays at its authored
                    # x/y instead of jumping to the WORLD_WIDTH//2 boot default.
                    if self._csf_sync_player_pos:
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
                    _ct = self.active_cutscene_runtime.camera_target
                    if self._csf_sync_player_pos:
                        _px, _py = self.player.x, self.player.y
                    else:
                        # Fresh-boot launch (sync_player_position=False, see
                        # _start_cutscene's docstring) — self.player.x/y is
                        # still just the boot-time WORLD_WIDTH//2 default, so
                        # rebasing off it would aim the camera at nothing.
                        # Anchor on the cutscene's own player-type actor
                        # instead (its authored spawn, already correct — see
                        # the sync loop above, which we skipped on purpose).
                        _anchor = None
                        for _adef2 in data.get('actors', []):
                            if _adef2.get('type') == 'player':
                                _anchor = self.active_cutscene_runtime.actors.get(_adef2['id'])
                                if _anchor:
                                    break
                        _px, _py = (_anchor.entity.x, _anchor.entity.y) if _anchor else (_ct.x, _ct.y)

                        # camera_target itself also needs correcting, not just
                        # the physical camera below — camera_target._tweens is
                        # empty here (no authored camera actions in this
                        # cutscene), so without this Camera.update() keeps
                        # re-deriving the physical camera from camera_target's
                        # stale pre-boot position every subsequent frame,
                        # silently undoing the one-time snap right after it
                        # happens (the actors-appear-top-left symptom).
                        _ct.snap_to(_px, _py)

                        # The physical camera's current position is likewise
                        # just leftover pre-boot state (see camera_target's
                        # own init in CutsceneRuntime.__init__, which seeds
                        # itself from self.camera.x/y) — nothing to lerp
                        # from, so snap it straight to frame the anchor
                        # actor the same way a normal room entry would.
                        self.camera.x = int(_px * RENDER_SCALE) - self.camera.screen_width  // 2
                        self.camera.y = int(_py * RENDER_SCALE) - self.camera.screen_height // 2
                        if self.current_room:
                            self.camera.x = min(self.camera.x,
                                                 self.current_room.width  * RENDER_SCALE - SCREEN_WIDTH)
                            self.camera.y = min(self.camera.y,
                                                 self.current_room.height * RENDER_SCALE - SCREEN_HEIGHT)
                        self.camera.x = max(0, self.camera.x)
                        self.camera.y = max(0, self.camera.y)

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

                    # If we hid the room switch/construction behind a
                    # fade-out, fade back in now that it's ready. Otherwise
                    # this was an instant launch (no room switch needed) —
                    # keep that path exactly as fast as before.
                    self._csf_state = 'fade_in' if _faded_out_first else None
                except Exception as e:
                    print(f'[Game] failed to start cutscene: {e}')
                    import traceback; traceback.print_exc()
                    self.active_cutscene_runtime = None
                    self._csf_state = None
                    self._csf_alpha = 0.0
            else:
                self._csf_state = None
            return

        # ── Fade-in: screen clears back to normal while the cutscene, now
        # constructed, plays underneath. Falls through (no early return) so
        # playback still ticks on the same frame. ───────────────────────────
        if self._csf_state == 'fade_in':
            self._csf_alpha = max(0.0, self._csf_alpha - self._csf_speed * dt)
            if self._csf_alpha <= 0.0:
                self._csf_alpha = 0.0
                self._csf_state = None

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

                # Resume a blocking play_cutscene action's sequence, if one
                # is waiting on this cutscene (see _start_cutscene/
                # _handle_play_cutscene_action).
                _cb = self._cutscene_on_finished
                self._cutscene_on_finished = None
                if _cb:
                    _cb()
            return

    def _load_cutscene_data(self, cutscene_id):
        """Load cutscene JSON by id, or None (with a console message) on failure.

        Prefers the cutscene editor's live in-memory copy when it has this
        cutscene open, so unsaved edits (e.g. a freshly-added change_room
        action) are visible immediately during testing without requiring a
        manual save. Used by the 'play_cutscene' EventRunner action.
        """
        ce = getattr(self, 'cutscene_editor', None)
        if (ce
                and getattr(ce, 'cutscene_name', '') == cutscene_id
                and ce.cutscene_data):
            return ce.cutscene_data

        import os, json
        cutscene_path = os.path.join('data', 'cutscenes', f'{cutscene_id}.json')
        if not os.path.exists(cutscene_path):
            print(f'[Game] cutscene file not found: {cutscene_path}')
            return None
        try:
            with open(cutscene_path) as f:
                return json.load(f)
        except Exception as e:
            print(f'[Game] failed to load cutscene "{cutscene_id}": {e}')
            import traceback; traceback.print_exc()
            return None

    def _start_cutscene(self, cutscene_data, on_finished=None, already_faded=False,
                         sync_player_position=True):
        """Queue cutscene_data to launch.

        Launches instantly (no fade) when the cutscene plays out in the
        room the player is already standing in. If the cutscene's
        authored home room (top-level "room" key) differs from the
        current room, a room switch has to happen before the runtime can
        be built — that involves loading tiles/objects and can hitch for
        a noticeable moment on a room visited for the first time — so in
        that case we fade to black first (see _update_cutscene_triggers'
        'fade_out' handling) and do the heavy lifting behind the fade,
        then fade back in once the runtime is ready.

        `already_faded` — set by _start_new_game, whose caller
        (title_screen.py's save-select confirm) has already faded the
        screen to black itself before this ever gets called. Without
        this we'd fade-out a second time here on top of an already-black
        screen — this skips straight to the 'start' state at alpha=255
        (screen already black, room switch/load still happens behind
        it) so only the fade-in half plays once the intro cutscene's
        runtime is ready.

        `sync_player_position` — snaps the scripted player-type actor to
        self.player's real x/y/direction (see the 'start' branch below),
        so a cutscene triggered mid-gameplay carries over wherever you
        were actually standing. Set to False by _start_new_game: at that
        point the player has never been placed in any room yet, so
        self.player.x/y are still just the boot-time WORLD_WIDTH//2,
        WORLD_HEIGHT//2 defaults from Game.__init__ — syncing to that
        would teleport the actor off its authored position and off
        camera instead of leaving it where the cutscene actually placed
        it.

        `on_finished`, if given, is called with no arguments once the
        cutscene runtime reports finished (see the 'normal cutscene
        playback' branch above). Used by the 'play_cutscene' action so a
        blocking action sequence can resume once the cutscene ends.

        Refuses to stomp on a cutscene that's already running or queued —
        callers should check for that themselves if they need different
        behavior (e.g. queuing instead of dropping).
        """
        if self.active_cutscene_runtime is not None or self._csf_state is not None:
            print('[Game] _start_cutscene: a cutscene is already active/pending, ignoring')
            if on_finished:
                on_finished()
            return False

        base_room_name    = (cutscene_data.get('room') or '').strip()
        needs_room_switch = bool(base_room_name and (not self.current_room
                                                       or self.current_room.name != base_room_name))

        self._csf_pending          = cutscene_data
        self._csf_sync_player_pos  = sync_player_position
        if already_faded:
            self._csf_alpha        = 255.0
            self._csf_state        = 'start'
        else:
            self._csf_alpha        = 0.0
            self._csf_state        = 'fade_out' if needs_room_switch else 'start'
        self._cutscene_on_finished = on_finished
        return True

    def _handle_play_cutscene_action(self, on_complete, cutscene_id):
        """EventRunner handler for the 'play_cutscene' action — was missing
        entirely (no register_handler call for it, same gap as change_map
        and set_player_location had), so a trigger box configured with a
        play_cutscene action fired the action but nothing ever happened.

        Blocking: on_complete isn't called until the cutscene actually
        finishes, so any actions after this one in the same sequence wait
        for the cutscene to play out first.
        """
        cutscene_data = self._load_cutscene_data(cutscene_id)
        if cutscene_data is None:
            on_complete()
            return

        started = self._start_cutscene(cutscene_data, on_finished=on_complete)
        if not started:
            # _start_cutscene already called on_finished (== on_complete) in
            # this case, so nothing left to do.
            pass

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

    def _draw_white_flash(self, surface):
        """Draw the brief screen-wide white flash triggered when a genkidama
        connects with something (see _trigger_genkidama_hit). Ramps from
        no white -> fully white over the first half of
        _WHITE_FLASH_DURATION, then back down to no white over the second
        half. No-op once decayed.
        """
        if self._white_flash_timer <= 0:
            return
        w, h = surface.get_size()

        elapsed = self._WHITE_FLASH_DURATION - self._white_flash_timer
        half = self._WHITE_FLASH_DURATION / 2
        if elapsed <= half:
            progress = elapsed / half            # 0 -> 1, ramping up to full white
        else:
            progress = 1.0 - (elapsed - half) / half   # 1 -> 0, ramping back down
        progress = max(0.0, min(1.0, progress))

        alpha = int(255 * progress)
        if alpha <= 0:
            return
        # Same SRCALPHA approach as _draw_map_jump_fade — set_alpha() doesn't
        # work correctly when blitting onto a pygame.SCALED display surface.
        if not hasattr(self, '_white_flash_surf') or self._white_flash_surf.get_size() != (w, h):
            self._white_flash_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        self._white_flash_surf.fill((255, 255, 255, alpha))
        surface.blit(self._white_flash_surf, (0, 0))

    def _draw_death_fade(self, surface):
        """Draw the black fade overlay for the post-death sequence.

        Same SRCALPHA-surface approach as _draw_map_jump_fade/_draw_cutscene_fade
        above — set_alpha() doesn't work correctly on a pygame.SCALED display
        surface. No-op once alpha is back at 0 (i.e. whenever death isn't in
        progress at all).
        """
        alpha = int(self._death_fade_alpha)
        if alpha <= 0:
            return
        w, h = surface.get_size()
        if not hasattr(self, '_death_fade_surf') or self._death_fade_surf.get_size() != (w, h):
            self._death_fade_surf = pygame.Surface((w, h), pygame.SRCALPHA)
        self._death_fade_surf.fill((0, 0, 0, min(255, alpha)))
        surface.blit(self._death_fade_surf, (0, 0))

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
        # Pre-scaled-frame cache for _draw_landing_sprite — reset here since a
        # fresh load means new source Surface objects (old cache entries would
        # be stale / point at frames that no longer match this direction).
        self._mjf_land_scaled_cache  = {}

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
        _idx    = min(self._mjf_land_frame_idx, len(_land_frames) - 1)
        _sw     = 32 * RENDER_SCALE
        _sh     = 32 * RENDER_SCALE

        # Scaling is deterministic per frame index (same source frame, same
        # target size every time), so cache it instead of re-scaling on every
        # single frame of the landing sequence. Cache is reset in
        # _load_map_land_sprite() whenever a new landing sequence starts.
        _cache = getattr(self, '_mjf_land_scaled_cache', None)
        if _cache is None:
            _cache = self._mjf_land_scaled_cache = {}
        _scaled = _cache.get(_idx)
        if _scaled is None:
            _scaled = pygame.transform.scale(_land_frames[_idx], (_sw, _sh))
            _cache[_idx] = _scaled

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

            _throttle_target = 1.0 if (up or down or left or right) else 0.0
            self._mjf_fly_throttle += ((_throttle_target - self._mjf_fly_throttle)
                                        * min(1.0, dt * self._MJF_THROTTLE_RATE))

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
            ROTATE_SPD = 1.8 * dt * (1.0 - 0.75 * _alt) * self._mjf_fly_throttle
            MAP_SPD    = self._MJF_FLY_SPEED * 1.8 * dt * self._mjf_fly_throttle

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
                    self.flag_manager.mark_room_visited(_target_room_name)
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
                    self.player.enter_idle()
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

    def _on_world_map_saved(self, map_name: str):
        """Called by WorldMapEditor right after it writes a map's JSON to
        disk (see WorldMapEditor.on_save). Drops every render cached from
        the old JSON for that map so the next frame rebuilds from what was
        just saved, instead of showing a stale image until the game
        restarts — this is what makes edits (tiles, and especially the
        Scouter Paint silhouette) show up immediately back in the room.
        """
        if hasattr(self, '_wm_scouter_cache'):
            self._wm_scouter_cache.pop(map_name, None)

        # The Mode7 flying-scene texture is only ever built for whichever
        # map is currently active, and lazily (see _draw_world_map_flying_scene's
        # `if not hasattr(self, '_world_map_texture')` guard) — so if the
        # saved map is the active one, clear those attrs too, the same way
        # _start_map_jump does when the active map itself changes.
        if getattr(self, '_active_world_map_name', None) == map_name:
            for attr in ('_world_map_texture', '_world_map_tex_arr', '_wm_locations',
                         '_wm_entities', '_wm_vehicle_cache'):
                if hasattr(self, attr):
                    delattr(self, attr)

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

    def _build_world_map_scouter_surface(self, map_name: str, frame_idx: int = 0):
        """Render the world map's designer-painted Scouter silhouette (see the
        World Map editor's Scouter Paint mode) for the Scouter's WORLD_MAP
        section.

        Replaces the old automatic approach, which inferred land vs. water by
        color-keying the tile art's palette (every water/shallow-water shade
        followed a strict R < G < B gradient) and tracing a coastline from
        that. That worked but was indirect and fragile -- it broke the moment
        a tileset used a color outside the assumed gradient. This instead
        reads painted cells straight from the map JSON's 'scouter_paint'
        field: [gx, gy] map-tile cells the designer painted directly in the
        editor, the same "paint the shape once, directly" approach
        objects/map_paint.py already uses for the room-level Scouter minimap.
        Nothing here is inferred from the tile art.

        frame_idx is accepted for signature compatibility with the old
        outline builder (and with _build_world_map_surface) but unused: the
        painted silhouette doesn't change when the map's animated frame does.

        Returns None if the map JSON is missing or has no painted cells yet,
        so the caller can show a "not painted" message instead of a blank
        surface. The result is cached per map name until invalidated, since
        re-rendering it every frame the WORLD_MAP section is left open would
        be unnecessary work.
        """
        import json as _json
        import os as _os

        if not hasattr(self, '_wm_scouter_cache'):
            self._wm_scouter_cache = {}

        cached = self._wm_scouter_cache.get(map_name)
        if cached is not None:
            return cached

        path = _os.path.join('assets', 'world_maps', f'{map_name}.json')
        try:
            with open(path) as f:
                data = _json.load(f)
        except Exception as e:
            print(f'[world_map] could not open {path}: {e}')
            return None

        raw_cells = data.get('scouter_paint', [])
        if not raw_cells:
            print(f'[world_map] {map_name}.json has no scouter_paint -- '
                  f'paint it in the World Map editor\'s Scouter mode')
            return None

        cells = set()
        for c in raw_cells:
            try:
                cells.add((int(c[0]), int(c[1])))
            except (TypeError, ValueError, IndexError):
                continue
        if not cells:
            return None

        MAP_W = 362   # must match world_map_editor.MAP_TILE_W
        MAP_H = 263   # must match world_map_editor.MAP_TILE_H
        COLOR_FILL    = (0, 0, 0, 255)        # interior land -- black, only between outlines
        COLOR_OUTLINE = (57, 255, 57, 255)    # 39FF39 -- painted outline cells

        # Painted scouter_paint cells are OUTLINES only (same contract as
        # room map_paint). Interiors are flood-filled by nesting depth so a
        # closed coastline becomes land and nested loops stay holes.
        #
        # Critical: the surface is SRCALPHA with a transparent outside.
        # Filling the whole MAP_W×MAP_H canvas with opaque black is what
        # produced the big black square on the Scouter WORLD_MAP screen —
        # ocean/outside must stay transparent so only the silhouette shows.
        from collections import deque as _deque

        mask = [[False] * MAP_W for _ in range(MAP_H)]
        for gx, gy in cells:
            if 0 <= gx < MAP_W and 0 <= gy < MAP_H:
                mask[gy][gx] = True

        region_id = [[-1] * MAP_W for _ in range(MAP_H)]
        region_count = 0
        border_regions = set()
        for row in range(MAP_H):
            for col in range(MAP_W):
                if mask[row][col] or region_id[row][col] != -1:
                    continue
                rid = region_count
                region_count += 1
                touches_border = False
                dq = _deque([(row, col)])
                region_id[row][col] = rid
                while dq:
                    r, c = dq.popleft()
                    if r == 0 or r == MAP_H - 1 or c == 0 or c == MAP_W - 1:
                        touches_border = True
                    for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                        if (0 <= nr < MAP_H and 0 <= nc < MAP_W
                                and not mask[nr][nc] and region_id[nr][nc] == -1):
                            region_id[nr][nc] = rid
                            dq.append((nr, nc))
                if touches_border:
                    border_regions.add(rid)

        adjacency = [set() for _ in range(region_count)]
        for row in range(MAP_H):
            for col in range(MAP_W):
                if not mask[row][col]:
                    continue
                neighbor_regions = {
                    region_id[nr][nc]
                    for nr, nc in ((row - 1, col), (row + 1, col),
                                   (row, col - 1), (row, col + 1))
                    if 0 <= nr < MAP_H and 0 <= nc < MAP_W and not mask[nr][nc]
                }
                for a in neighbor_regions:
                    for b in neighbor_regions:
                        if a != b:
                            adjacency[a].add(b)

        depth = [None] * region_count
        dq = _deque()
        for rid in border_regions:
            depth[rid] = 0
            dq.append(rid)
        while dq:
            rid = dq.popleft()
            for nb in adjacency[rid]:
                if depth[nb] is None:
                    depth[nb] = depth[rid] + 1
                    dq.append(nb)
        for i in range(region_count):
            if depth[i] is None:
                depth[i] = 1  # sealed pocket with no path to border → filled

        surface = pygame.Surface((MAP_W, MAP_H), pygame.SRCALPHA)
        surface.fill((0, 0, 0, 0))  # fully transparent outside the silhouette
        for row in range(MAP_H):
            for col in range(MAP_W):
                if mask[row][col]:
                    surface.set_at((col, row), COLOR_OUTLINE)
                else:
                    rid = region_id[row][col]
                    if rid >= 0 and depth[rid] is not None and depth[rid] % 2 == 1:
                        surface.set_at((col, row), COLOR_FILL)

        # Scale up to match the legacy PNG pipeline's size, same as
        # _build_world_map_surface, so the WORLD_MAP section's viewport math
        # doesn't need to special-case this surface's resolution.
        try:
            _png_ref = pygame.image.load('assets/map/world_map.png')
            target_w = _png_ref.get_width() * 2
            target_h = _png_ref.get_height() * 2
            del _png_ref
        except Exception:
            target_w = MAP_W * 2
            target_h = MAP_H * 2

        # Nearest-neighbour scale so the 1px outline stays crisp; keep alpha
        # so the transparent ocean doesn't turn into a black rectangle.
        result = pygame.transform.scale(surface, (target_w, target_h))
        self._wm_scouter_cache[map_name] = result
        return result

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
                # Route through the overrides helper (same as the location-
                # billboard lazy-load below) instead of reading the JSON
                # directly — otherwise pins hidden via the 'world_map_location'
                # event action reappear because this sets self._wm_locations
                # first, unfiltered, and the real filtered load later in this
                # function sees hasattr(self, '_wm_locations') already True
                # and skips itself.
                self._apply_wm_location_overrides(_active_map)
                _locs = self._wm_locations
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

                if self._world_map_loc_sprite and getattr(self, '_wm_locations', None):
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
                    # Load entities from the same file (avoid a second open)
                    if not hasattr(self, '_wm_entities'):
                        self._wm_entities = _ld.get('entities', [])
                except Exception:
                    pass
                # Apply through the overrides helper (re-reads the file, but
                # keeps this the single source of truth for add/remove pins
                # queued by the 'world_map_location' event action).
                self._apply_wm_location_overrides(_active_map)

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
                """Map a single character to its glyph filename (handles the
                punctuation that isn't a plain A-Z/0-9 image name)."""
                if ch == ':':  return 'colon.png'
                if ch == '/':  return 'slash.png'
                return ch.upper() + '.png'

            def _get_glyph(ch, height, color):
                """Load, scale to `height`, and tint one glyph, caching the
                result per (char, height, color) so repeat calls are free."""
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
                """Return the pixel width `text` would take up if drawn at
                `height`/`color` — used to center button-hint labels."""
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
                """Draw `text` glyph-by-glyph onto `surf` starting at (x, y);
                returns the x position just past the last glyph drawn."""
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

        Derived directly from player.equipped_attacks — each attack folder id
        maps explicitly to the mode it unlocks:
          - 'ki_blast'   in equipped → blast mode available
          - 'kamehameha' in equipped → beam mode available
          - 'kamekameha' in equipped → kamekameha mode available
          - 'banshee_blast' in equipped → banshee_blast mode available
          - 'final_flash' in equipped → final_flash mode available
          - 'big_bang_kamehameha' in equipped → big_bang_kamehameha mode available
          - 'genkidama'  in equipped → genkidama mode available
          - 'big_bang_attack' in equipped → big_bang_attack mode available
          - 'masenko'    in equipped → masenko mode available
          - 'flame_kamehameha' in equipped → flame_kamehameha mode available
          - 'ultra_volleyball_attack' in equipped → ultra_volleyball mode available
          - 'energy_sword' in equipped → sword mode available
          - 'energy_punch' in equipped → energy_punch mode available
          - 'dragon_fist' in equipped → dragon_fist mode available
          - 'ghost_kamikaze' in equipped → ghost_kamikaze mode available
          - 'instant_transmission' in equipped → instant_transmission mode available
          - transform is appended last, but only if this character actually
            has at least one transformation configured in the character
            creator (player.has_transformation — set in _reload_attack_config
            from cfg["transformations"])

        Falls back to blast-only (plus transform, if the character has one)
        when no config has been loaded yet.
        """
        equipped = getattr(self.player, 'equipped_attacks', [])
        has_transform = getattr(self.player, 'has_transformation', False)

        modes: list[str] = []
        if 'ki_blast' in equipped:
            modes.append('blast')
        if 'kamehameha' in equipped:
            modes.append('beam')
        if 'kamekameha' in equipped:
            modes.append('kamekameha')
        if 'banshee_blast' in equipped:
            modes.append('banshee_blast')
        if 'final_flash' in equipped:
            modes.append('final_flash')
        if 'big_bang_kamehameha' in equipped:
            modes.append('big_bang_kamehameha')
        if 'genkidama' in equipped:
            modes.append('genkidama')
        if 'big_bang_attack' in equipped:
            modes.append('big_bang_attack')
        if 'masenko' in equipped:
            modes.append('masenko')
        if 'burning_attack' in equipped:
            modes.append('burning_attack')
        if 'flame_kamehameha' in equipped:
            modes.append('flame_kamehameha')
        if 'ultra_volleyball_attack' in equipped:
            modes.append('ultra_volleyball_attack')
        if 'energy_sword' in equipped:
            modes.append('sword')
        if 'energy_punch' in equipped:
            modes.append('energy_punch')
        if 'dragon_fist' in equipped:
            modes.append('dragon_fist')
        if 'ghost_kamikaze_attack' in equipped:
            modes.append('ghost_kamikaze_attack')
        if 'instant_transmission' in equipped:
            modes.append('instant_transmission')

        if has_transform:
            modes.append('transform')

        if modes:
            return tuple(modes)

        # If nothing was equipped at all, keep blast as the default so the
        # game is still playable before any config file has been saved.
        return ('blast', 'transform') if has_transform else ('blast',)

    def _sync_event_editor_character(self):
        """Tell the Room Editor's object editor (if it exists yet) which
        character is currently being played, so its event editor's 'skill'
        action pickers stay accurate — see ObjectEditor.set_current_character().
        object_editor is lazy-initialized, hence the existence check (same
        pattern used elsewhere for it, e.g. _on_save_point_placed callers).

        Passes a live getter for player.equipped_attacks (not just the
        character id) since runtime 'skill' actions mutate that list
        in-place — a one-time snapshot would go stale the moment a skill
        was added/removed without another explicit re-sync."""
        oe = getattr(self.room_editor, 'object_editor', None)
        if oe is not None:
            oe.set_current_character(
                getattr(self.player, 'character', None),
                get_equipped_skills=lambda: getattr(self.player, 'equipped_attacks', []),
            )

    def _sync_event_editor_rooms(self):
        """Tell the Room Editor's object editor (if it exists yet) which
        rooms actually exist right now, so its event editor's change_map
        'room_name' dropdown and 'Set Spawn' preview reflect the real,
        current room list/sizes instead of ActionSequenceBuilder's
        on-disk-only fallback discovery — see
        ActionSequenceBuilder.set_known_rooms(). Same lazy-init existence
        check as _sync_event_editor_character() above; also guarded on the
        passthrough actually existing on ObjectEditor, since that's a
        small forwarding method (mirroring its existing
        set_current_character() one) that needs to be added there.
        object_editor is lazy-initialized, hence both checks."""
        oe = getattr(self.room_editor, 'object_editor', None)
        if oe is not None and hasattr(oe, 'set_known_rooms'):
            room_dims = {room.name: (room.width, room.height) for room in self.room_manager.rooms}
            oe.set_known_rooms(sorted(room_dims.keys()), room_dims)
        if oe is not None and hasattr(oe, 'set_room_preview_provider'):
            oe.set_room_preview_provider(self._render_room_tile_preview)

    def _render_room_tile_preview(self, room_name):
        """ActionSequenceBuilder's Set Spawn overlay's room_preview_provider
        callable — renders every placed tile for room_name onto an
        offscreen surface sized to the room's full world extent (at
        RENDER_SCALE, background pass then foreground pass, same ordering/
        animation-frame lookup as TilesetEditor.draw_tiles()).

        Deliberately NOT just calling tileset_editor.draw_tiles() itself:
        that method culls tiles against TilesetEditor.screen_width/height
        (the live game window), which would wrongly clip this preview for
        any room bigger than the window. This walks tile data directly
        instead, using the same public per-tile lookups draw_tiles() uses,
        just without that screen-bounds cull — safe since our surface is
        already sized to exactly the room's extent.

        Also deliberately NOT relying on tileset_editor.room_tiles[room_name]
        alone: RoomEditor only lazily populates that cache for whichever
        room is currently open in the room viewer (see
        RoomEditor._enter_view_room()) — for any other room (which is the
        common case here, since you're picking a spawn in a room you're
        not currently standing in/editing), it'd be empty even though the
        room clearly has tiles. Falls back to the Room object's own raw
        tiles (room.tiles, the same source _enter_view_room() itself loads
        from) when the room isn't the one actively being edited.

        Returns None if the room doesn't exist or has no tiles at all, so
        the overlay falls back to its plain grid rectangle."""
        te = self.room_editor.tileset_editor
        room = self.room_manager.get_room_by_name(room_name)
        if room is None:
            return None

        if room_name in te.room_tiles and te.room_tiles[room_name]:
            tiles = te.room_tiles[room_name]
        else:
            try:
                from dev_tools.room_editor.room_editor_tools.tileset_editor import Tile
            except Exception:
                return None
            raw = getattr(room, 'tiles', None) or []
            tiles = [Tile.from_dict(t) if isinstance(t, dict) else t for t in raw]

        if not tiles:
            return None

        surf_w = max(1, int(room.width * RENDER_SCALE))
        surf_h = max(1, int(room.height * RENDER_SCALE))
        surf = pygame.Surface((surf_w, surf_h))
        surf.fill((40, 40, 55))

        tick_ms = pygame.time.get_ticks()
        for tile in sorted(tiles, key=lambda t: t.layer):
            tileset = te.tileset_manager.get_tileset(tile.tileset_name)
            if not tileset:
                continue
            disp_x, disp_y = tileset.get_animated_coords(tile.tile_x, tile.tile_y, tick_ms)
            scaled_tile = tileset.get_scaled_tile_surface(disp_x, disp_y, RENDER_SCALE)
            if not scaled_tile:
                continue
            surf.blit(scaled_tile, (int(tile.x * RENDER_SCALE), int(tile.y * RENDER_SCALE)))
        return surf

    @staticmethod
    def _transformation_form_id(costume_path):
        """Given a transformation entry's 'costume' field
        ("{owning_costume}/transformations/{form}", see
        _reload_attack_config()), return just the "{form}" tail — the id
        player.unlocked_transformations and the 'transformation' event
        action's form_id key off of. Returns '' if costume_path doesn't
        look like a transformation entry at all. Mirrors
        core/event_editor.py's module-level _transformation_form_id(),
        which the event editor's transformation_id picker uses to build
        its dropdown from the same on-disk config — keep the two in sync
        if this parsing ever changes."""
        marker = '/transformations/'
        if marker not in (costume_path or ''):
            return ''
        return costume_path.split(marker, 1)[1]

    def _refresh_transformation_gate(self, cfg=None):
        """Recompute player.has_transformation for whichever costume the
        player is CURRENTLY wearing, and detransform if it's no longer
        available. Split out of _reload_attack_config() so
        _handle_transformation_action()'s grant/revoke can re-run just
        this gate immediately without also resetting equipped_attacks/
        ki_mode_config from disk (which would stomp any runtime 'skill'
        grants already applied this session).

        cfg is the character-creator config dict for the currently played
        character (character_creator.load_config(self.player.character))
        — pass it when the caller already has it (as _reload_attack_config
        does) to avoid loading it twice; loaded fresh from disk otherwise.

        Transform is only offered as a ki mode if the costume the player
        is CURRENTLY wearing actually has a transformation set up for it —
        not just if *some* costume on this character has one — AND that
        form is currently in player.unlocked_transformations.
        Transformation entries are stored with costume =
        "{owning_costume}/transformations/{form}", so scope the check to
        entries owned by cfg['costume'].
        """
        if cfg is None:
            cfg = character_creator.load_config(self.player.character)

        # Same live-vs-config-default distinction as
        # TransformationSystem._resolve_transform_costume(): cfg['costume']
        # is only the character creator's design-time default and does NOT
        # reflect a runtime costume switch (_handle_set_player_skin_action
        # only updates self.player.costume, never the on-disk config). Prefer
        # the actual equipped costume so this gate agrees with what
        # _resolve_transform_costume() will actually resolve to.
        _current_costume = getattr(self.player, 'costume', None) or cfg.get('costume', '')
        _transform_prefix = f"{_current_costume}/transformations/"
        _unlocked = getattr(self.player, 'unlocked_transformations', None) or []
        self.player.has_transformation = any(
            t.get('costume', '').startswith(_transform_prefix)
            and self._transformation_form_id(t.get('costume', '')) in _unlocked
            for t in cfg.get('transformations', [])
        )

        # If the costume now active doesn't have an unlocked transformation
        # configured, fully deactivate the skill rather than just hiding it
        # from the TAB cycle. Without this, a transformed/transforming state
        # left over from a costume/form that DID have one keeps pointing the
        # sprite loader at "{costume}/transformations/{form}" frames that
        # don't exist/aren't unlocked for the new costume — which is what
        # shows up as a stuck purple placeholder cube and freezes the
        # character (see start_transform()/the sprite fallback in
        # core/sprite_system.py).
        if not self.player.has_transformation:
            ts = getattr(self.player, 'transformation', None)
            if ts is not None and (ts.is_transformed or ts.is_transforming or ts.is_untransforming):
                ts.is_transformed            = False
                ts.is_transforming           = False
                ts.is_untransforming         = False
                ts.current_transform_costume = None

                if getattr(self.player, 'costume', 'base') != 'base':
                    from core.sprite_system import create_character_sprite
                    self.player.sprite  = create_character_sprite(
                        self.player.character, 'base', 32, 32)
                    self.player.costume = 'base'

    def _reload_attack_config(self, character_id, sync_base_stats=True):
        """(Re-)apply character_id's saved attack config to the live player:
        equipped attacks and ki mode. Used both when swapping to a different
        character and when picking up edits made in the character creator
        for the character currently being played.

        sync_base_stats=False skips the STR/POW/END/SPD/ki_regen/max_hp/
        max_ki sync block below — used when this is called from
        _switch_character(), where Player.restore_progress() has *already*
        put character_id's own current (possibly leveled-up, stat-point-
        allocated) numbers onto the player; re-syncing base config values
        on top of that would silently wipe out every stat point ever spent
        on that character on every single switch back to them. The
        character-creator "pick up my edit" call site (below, at the
        'close' handler) still wants sync_base_stats=True — that one really
        does mean to push a freshly-edited base config onto the currently
        active character."""
        cfg = character_creator.load_config(character_id)
        atk = cfg.get('attacks', {})

        # Scouter/UI-facing display name — falls back to the raw character
        # id (e.g. "goku") if the creator's Identity tab was left blank, so
        # a character always shows *something* other than the generic
        # "Player" class-name fallback in ui/scouter_menu.py's
        # _get_entity_display_name().
        self.player.display_name = cfg.get('display_name') or character_id

        # Scouter Data description panel (ui/scouter_menu.py's
        # _get_entity_description) — same "always re-apply on reload" flow
        # as display_name above, so editing the Identity tab's Description
        # box and picking it back up here works for whichever character is
        # currently being played, not just on next character switch.
        self.player.description = cfg.get('description', '')

        self.player.equipped_attacks = list(atk.get('equipped_attacks', []))
        self.player.ki_mode_config   = atk.get('ki_attack_mode', 'blast')

        # Ground-shadow pixel width from the character creator's "Shadow
        # Size" slider (character_creator.py's cfg["shadow_size"], an int
        # in px — not to be confused with Player.shadow_size, the legacy
        # 'small'/'big' string). LayerManager._draw_shadow() reads
        # player.shadow_width directly (falling back to player.width if
        # unset), so without this line the slider had nowhere to go and
        # every character always drew the same default-width shadow.
        self.player.shadow_width = cfg.get('shadow_size', self.player.width)

        # Charged Melee style — whether holding the melee button lunges
        # forward or spins in place once fully charged (see
        # Player.release_charged_melee()). Always available (unlike the
        # equipped/TAB-cycled attacks above); this only picks which variant
        # plays.
        self.player.charged_melee_style = atk.get('charged_melee_style', 'lunge')

        # Charged Melee gate — whether holding E is allowed to roll into
        # the charge wind-up at all (see the 'charged_melee' event action
        # / _handle_charged_melee_action() above; Player.start_charging_
        # melee() in player.py needs an
        # `if not self.charged_melee_enabled: return`-style guard added
        # at its top to actually enforce this — see the NOTE there).
        # Defaults to enabled on every character load/switch, same
        # reset-per-load semantics as equipped_attacks/
        # unlocked_transformations below: a runtime add/remove from a
        # trigger box doesn't persist across switching away and back.
        self.player.charged_melee_enabled = True

        # Every transformation form configured anywhere on this character
        # (across all its costumes), keyed by form id (see
        # _transformation_form_id() below) — the pool the 'transformation'
        # event action's add/remove (_handle_transformation_action())
        # mutates. Defaults to fully unlocked so a character with no
        # explicit lock/unlock events behaves exactly as it did before
        # this action existed. Reset on every reload (character switch or
        # picking up character-creator edits), same as equipped_attacks
        # above — a runtime grant/revoke doesn't persist across switching
        # away from this character and back, same semantics as runtime
        # skill grants.
        self.player.unlocked_transformations = [
            self._transformation_form_id(t.get('costume', ''))
            for t in cfg.get('transformations', [])
            if self._transformation_form_id(t.get('costume', ''))
        ]

        # Recompute has_transformation (and detransform if it's no longer
        # available) for the costume this character is currently wearing —
        # split out into its own method so a 'transformation' action can
        # re-run just this part later without resetting equipped_attacks/
        # ki_mode_config above. See _refresh_transformation_gate().
        self._refresh_transformation_gate(cfg=cfg)

        # Sync character-creator stats → player.stats so ki_regen (and any
        # other stat) takes effect the moment the creator saves. Skipped on
        # switch-driven reloads (see sync_base_stats docstring above) so a
        # returning character's own leveled/allocated stats aren't stomped.
        if sync_base_stats:
            _cc_stats = cfg.get('stats', {})
            _stat_map = {
                'power':    'strength',   # STR
                'ki_power': 'ki_power',   # POW
                'vitality': 'vitality',   # END
                'defense':  'defense',    # legacy/unused — combat reads 'vitality', not this
                'speed':    'speed',      # SPD
                'ki_regen': 'ki_regen',
            }
            changed = False
            for cc_key, player_key in _stat_map.items():
                if cc_key in _cc_stats and player_key in self.player.stats:
                    self.player.stats[player_key] = _cc_stats[cc_key]
                    changed = True
            if changed:
                self.player.update_derived_stats()
            # max_hp / max_ki are direct values in the creator
            if 'max_hp' in _cc_stats:
                self.player.max_hp = _cc_stats['max_hp']
                self.player.hp     = min(self.player.hp, self.player.max_hp)
            if 'max_ki' in _cc_stats:
                self.player.max_ki = _cc_stats['max_ki']
                self.player.ki     = min(self.player.ki, self.player.max_ki)

        # Keep ki_attack_mode valid for whatever's now equipped — prevents
        # being left in e.g. beam mode after a character loses beam attacks.
        allowed = self._get_allowed_ki_modes()
        if self.player.ki_attack_mode not in allowed:
            self.player.ki_attack_mode = allowed[0] if allowed else 'blast'

    def _switch_character(self, character_id, sync_previous=True):
        """Swap the player's sprite to character_id while keeping all gameplay state intact.

        Level/XP/HP/KI/stats are per-character (see Player.snapshot_progress/
        restore_progress) — the outgoing character's numbers are stashed in
        self.player.character_progress and the incoming character's numbers
        are loaded back out of it (or freshly seeded from their
        character-creator config, if this is the first time they've been
        played). Position, inventory, zeni, and direction stay on the
        player as before since those are shared/global, not per-character.

        sync_previous=False skips checkpointing the OUTGOING character's
        progress — used only by _start_loaded_game(), where the "outgoing"
        player object is just Game.__init__'s fresh boot-time placeholder
        rather than a character actually played this session, so there's
        nothing real to save and doing so would clobber a genuine loaded
        entry for that same character id.
        """
        from core.sprite_system import create_character_sprite

        previous_character_id = getattr(self.player, 'character', None)

        # Snapshot the non-progress state before the swap (unchanged by
        # character switching — shared across every character).
        state = {
            'x': self.player.x,
            'y': self.player.y,
            'inventory': self.player.inventory.copy(),
            'zeni': getattr(self.player, 'zeni', 0),
            'direction': self.player.direction,
            'current_animation_state': getattr(self.player, 'current_animation_state', 'idle'),  # capture BEFORE swap
        }

        # Stash the outgoing character's level/XP/HP/KI/stats so they're
        # exactly as-left when this character is switched back to, instead
        # of being overwritten by whoever's switched in next.
        if sync_previous:
            self._sync_active_character_progress(previous_character_id)

        # Use the costume configured for this character in the character
        # creator (assets/characters/{character_id}.json's "costume" field)
        # instead of hardcoding 'base' — otherwise every switch silently
        # reverted the character to their base look even when they'd been
        # set up with a different default costume. This is the same field
        # character_switch_menu.py's _discover_characters() already reads
        # for the roster preview, so the preview and the actual switch now
        # agree with each other.
        costume = character_creator.load_config(character_id).get('costume', 'base')

        self.player.character = character_id
        self.player.costume   = costume
        self.player.sprite    = create_character_sprite(character_id, costume, 32, 32)

        # Restore shared state so the swap is completely seamless to the player.
        for key, value in state.items():
            setattr(self.player, key, value)

        # Load the incoming character's own progress — either picking up
        # exactly where they were left off, or a fresh level-1 start seeded
        # from their character-creator config if they've never been played.
        progress = self.player.character_progress.get(character_id)
        if progress is None:
            progress = Player.fresh_progress_for_character(character_id, self.game_config)
        self.player.restore_progress(progress)

        # The freshly created sprite doesn't know the player's pre-swap
        # facing — push it in now, or the new character defaults to facing
        # down until the player's next manual direction change.
        if hasattr(self.player.sprite, 'set_animation'):
            self.player.sprite.set_animation(
                state['current_animation_state'], state['direction'])

        # Apply the character's attack config so only equipped attacks are
        # usable. sync_base_stats=False: restore_progress() just above
        # already put this character's own current stats/HP/KI in place —
        # see _reload_attack_config's docstring for why re-syncing base
        # config stats here would wipe those back out.
        self._reload_attack_config(character_id, sync_base_stats=False)

        # Keep the event editor's skill pickers scoped to whoever's now
        # being played — see _sync_event_editor_character().
        self._sync_event_editor_character()

    def _sync_active_character_progress(self, character_id):
        """Write the live player's current level/XP/HP/KI/stats into
        self.player.character_progress[character_id] — i.e. checkpoint
        whoever's currently active before either switching away from them
        (_switch_character) or writing a save file (_write_save_slot), so
        neither operation loses progress made since the last switch."""
        if not character_id:
            return
        self.player.character_progress[character_id] = self.player.snapshot_progress()

    def _handle_set_player_character_action(self, character_id, skin_id=None):
        """EventRunner handler for the 'set_player_character' action —
        same plumbing as picking character_id in the character-switch
        menu (see _switch_character() above: sprite swap, attack config
        reload, transformation gate, event-editor skill-picker sync, all
        gameplay state like hp/ki/level/inventory/zeni preserved).

        If skin_id is given, it overrides the character's configured
        default costume — applied via _handle_set_player_skin_action()
        AFTER the switch, since _switch_character() already resets
        player.costume to character_id's own default."""
        self._switch_character(character_id)
        if skin_id:
            self._handle_set_player_skin_action(skin_id)

    def _handle_set_player_skin_action(self, skin_id):
        """EventRunner handler for the 'set_player_skin' action — swaps
        the CURRENTLY active character's costume/skin in place, without
        touching character, level, stats, inventory, etc. skin_id is a
        bare costume folder name (character_creator.py's "costume" field,
        e.g. "default", "gi_alt") — the same kind of value
        _switch_character() reads out of the character's config.

        Mirrors just the costume-assignment/sprite-recreation half of
        _switch_character(), then re-syncs the transformation gate since
        which forms are available depends on which costume's transform
        folder is currently active (see _refresh_transformation_gate())."""
        from core.sprite_system import create_character_sprite

        self.player.costume = skin_id
        self.player.sprite  = create_character_sprite(self.player.character, skin_id, 32, 32)

        # Preserve current facing/animation across the sprite swap — same
        # reasoning as the equivalent block in _switch_character().
        if hasattr(self.player.sprite, 'set_animation'):
            self.player.sprite.set_animation(
                getattr(self.player, 'current_animation_state', 'idle'),
                self.player.direction)

        self._refresh_transformation_gate()

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
        self.flag_manager.mark_room_visited(target_room_name)

    def _handle_flying_complete(self):
        """Called when the flying sequence ends.
        Player control is already restored by FlyingController.
        """
        pass

    def _handle_nimbus_room_transition(self, target_room_name, spawn_x, spawn_y):
        """Swap the active room mid-ride.

        Called by NimbusCloudController at the midpoint of a boundary-waypoint
        transition. Same room-swap plumbing as _handle_flying_room_transition,
        but the camera is re-anchored to a STATIC, top-locked frame instead of
        being centered on the spawn point — it stays there (camera.locked
        remains True) for the whole of the new room's leg, matching how the
        leg was authored in NimbusCloudPathEditor.
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

        # _load_room_objects[_as_copies] just replaced self.nimbus_clouds
        # wholesale with target_room's own authored clouds — it has no idea
        # about the cloud currently being ridden, which is still whatever
        # object nimbus_controller.cloud points to (authored in room a, or
        # wherever a previous leg's transition last placed it). Without
        # re-adding it here, the ridden cloud falls out of the active list:
        # it stops being drawn and stops being reachable for the "ride it
        # back" boarding check in _handle_interact, even though the
        # controller keeps moving it via cloud.x/y for the rest of the leg.
        ridden_cloud = self.nimbus_controller.cloud
        if ridden_cloud is not None:
            ridden_cloud.current_room = target_room_name
            # _load_room_objects[_as_copies] rebuilds self.nimbus_clouds from
            # target_room's own authored list every time. In normal (non-
            # test) mode the ridden cloud IS one of those authored objects
            # (same instance, never actually relocated between rooms' own
            # lists), so it's already present and appending would duplicate
            # it. In test mode, _load_room_objects_as_copies deep-copies a
            # FRESH object from the room's authored data on every reload —
            # a different instance from the one that's been riding — so an
            # identity check alone (`not in`) never catches it, and BOTH the
            # fresh copy and the ridden copy end up in the list side by
            # side (two visible clouds). origin_room/origin_x/origin_y are
            # stable across copies (set once at authoring, never mutated),
            # so use that as the real identity check instead of object
            # identity: drop any list entry that's a stand-in for the same
            # authored cloud, then add the one actually being ridden back.
            ridden_key = (
                getattr(ridden_cloud, 'origin_room', ''),
                ridden_cloud.origin_x,
                ridden_cloud.origin_y,
            )
            self.nimbus_clouds = [
                c for c in self.nimbus_clouds
                if c is not ridden_cloud and (
                    getattr(c, 'origin_room', ''), c.origin_x, c.origin_y
                ) != ridden_key
            ]
            self.nimbus_clouds.append(ridden_cloud)

        if ridden_cloud is not None and getattr(ridden_cloud, 'origin_room', '') == target_room_name:
            # Arriving back at the room this cloud was originally placed in.
            # NimbusCloudPathEditor locks that first leg to whatever view
            # was on screen at placement time (origin_camera_x/y) rather
            # than a computed top-anchored frame — every OTHER leg uses
            # that computed frame instead (see the else branch below, and
            # _snap_camera_to_top_anchor in the editor). Recreate that same
            # frame here so a return ride matches how the leg was authored.
            cam_x = max(0, min(ridden_cloud.origin_camera_x,
                                target_room.width * RENDER_SCALE - SCREEN_WIDTH))
            cam_y = max(0, min(ridden_cloud.origin_camera_y,
                                target_room.height * RENDER_SCALE - SCREEN_HEIGHT))
        else:
            # Static, top-anchored frame — centered horizontally on the spawn
            # point, pinned to the top of the room. Camera stays locked
            # (NimbusCloudController keeps camera.locked = True for the whole
            # ride) so this is a snap, never a scroll.
            cam_x = (spawn_x * RENDER_SCALE) - self.camera.screen_width // 2
            cam_x = max(0, min(cam_x, target_room.width * RENDER_SCALE - SCREEN_WIDTH))
            cam_y = 0  # Top of the room.

        self.camera.x = cam_x
        self.camera.y = cam_y

        self.flag_manager.mark_room_visited(target_room_name)

    def _handle_nimbus_ride_complete(self):
        """Called when the nimbus cloud ride ends.
        Player control is already restored by NimbusCloudController; the
        camera unlocks and resumes normal following from wherever it was
        left (the last static frame of the final leg).
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

        # Title screen — nothing else in the engine ticks while this is up.
        if self.game_mode == 'title':
            self.title_screen.update(dt)
            # Confirming New Game on the save-select page doesn't switch
            # scenes right away — title_screen.py fades the menu to black
            # first (see _confirm_save_slot/_exit_pending) and only reports
            # 'new_game' back here via consume_exit_signal() once that
            # fade has fully completed, so the cut into the cutscene never
            # happens before the screen is solid black.
            result = self.title_screen.consume_exit_signal()
            if result == 'new_game':
                self._start_new_game()
            elif result == 'load_game':
                self._start_loaded_game(self.title_screen.get_selected_save_slot())
            return

        # Advance the world-map entity animation clock every frame so that
        # in-room WorldMapObjects linked to entities always reflect the entity's
        # true path position, even when the world-map flying scene is not active.
        if not hasattr(self, '_wm_entity_anim_t'):
            self._wm_entity_anim_t = 0.0
        self._wm_entity_anim_t += dt

        # Flush any rooms marked dirty by save_room() (object placement/deletion
        # callbacks) — batched here instead of writing to disk on every single
        # call, which used to hitch the editor on every collision/music click.
        if hasattr(self.room_manager, 'flush_dirty_rooms'):
            self.room_manager.flush_dirty_rooms(dt=dt)

        # When the room editor is open, skip all game simulation — only tick the editor.
        if self.room_editor.active:
            self._sync_event_editor_rooms()
            self.room_editor.update(dt, self._get_logical_mouse_pos())
            return

        # Player death overlay — advances the hold/fade/game-over-box state
        # machine, but deliberately does NOT return/hijack update() the way
        # e.g. the white-flash hitstop below does: enemies, projectiles,
        # damage numbers, hurt-tint decay, everything keeps running normally
        # through the whole sequence. Only the player itself is frozen, via
        # is_dead (see Player.update()'s early return). handle_events()
        # still swallows all input while this is active — see there.
        self._update_death_sequence(dt)

        # Genkidama impact hitstop — the world freezes completely for the
        # duration of the white flash. Only the flash timer itself and the
        # hit-flash visuals keep advancing; player, enemies, projectiles,
        # everything else holds perfectly still until it ends.
        if self._white_flash_timer > 0:
            self._white_flash_timer = max(0.0, self._white_flash_timer - dt)
            for hit_fx in self.genkidama_hit_effects[:]:
                hit_fx.update(dt)
                if not hit_fx.active:
                    self.genkidama_hit_effects.remove(hit_fx)
            for hit_fx in self.burning_hit_effects[:]:
                hit_fx.update(dt)
                if not hit_fx.active:
                    self.burning_hit_effects.remove(hit_fx)
            return

        # Instant Transmission — the world freezes entirely (enemies, NPCs,
        # projectiles, everything) only while the player is actively aiming
        # (holding the button, picking targets). Once they let go and the
        # teleport hops actually begin, the world keeps running normally —
        # enemies can move and act while the player blinks around them.
        if self.player.is_targeting_it:
            self._update_instant_transmission(dt)
            return

        # Scouter menu — the whole world freezes while it's open (matches
        # the Scouter section's "frozen frame" requirement, and there's no
        # reason the Map/World Map sections need gameplay ticking either).
        # Only the menu's own animations (crosshair blink, etc.) advance.
        if self.scouter_menu.active:
            self.scouter_menu.update(dt)
            return

        enemies_defeated_this_frame = 0

        # Always tick UI overlays even when gameplay is paused.
        self.character_switch_menu.update(dt)
        self.save_point_menu.update(dt)
        self.dialogue_choice_menu.update(dt)
        self.pause_menu.update(dt)
        self.credits_screen.update(dt)

        # The in-game "Save Game" flow (see _start_save_flow) — ticks
        # TitleScreen's own clock (its update()/draw() otherwise only run
        # while game_mode == 'title') plus the "Saving..." popup's hold
        # timer.
        if self.save_flow_active:
            self.title_screen.update(dt)
            self._update_save_flow(dt)

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
                    and not self.pause_menu.active and not self.dialogue_choice_menu.active \
                    and not self.save_flow_active:
                self.play_time += dt

                # Tick down any running event timers in lockstep with play
                # time — frozen by the same menus/overlays so a paused game
                # never bleeds seconds off a challenge timer.
                if self.timers:
                    for timer in self.timers.values():
                        if timer['running'] and timer['remaining'] > 0:
                            timer['remaining'] = max(0.0, timer['remaining'] - dt)

                # Advance the spam QTE bar in lockstep with play time too —
                # frozen by the same menus/overlays so a paused game can't
                # be used to stall out (or accidentally drain) the bar.
                self._update_spam_qte(dt)

                # Count down consumable stat buffs (e.g. Holy Water's +25
                # STR/POW/END/SPD for 30s) — frozen by the same overlays as
                # play_time/timers above so opening the pause menu can't be
                # used to stretch a buff's duration.
                tick_item_buffs(self.player, self.active_item_buffs, dt)

            # Player movement is also suppressed during cutscenes, NPC
            # dialogue, and while an Instant Transmission hop sequence is
            # driving position directly (arrow-key input would otherwise
            # fight with each hop's teleport).
            if not self.save_point_menu.active and not self.character_switch_menu.active \
                    and not self.pause_menu.active and not self.active_cutscene_runtime \
                    and not self.dialogue_box.active and not self.dialogue_choice_menu.active \
                    and not self.player.is_teleporting_it and not self.spam_qte_bar.active \
                    and not self._levelup_active and not self.save_flow_active:
                self._update_player_movement(dt)

            # The character switch menu and save point menu should freeze the
            # player entirely — not just movement input. Letting player.update(dt)
            # keep running here would still tick idle_timer forward and let
            # idle_transition/idle_wait kick in while either menu is open
            # (only _update_player_movement above was gated before). The
            # dialogue choice menu — and the Save Game flow — get the same
            # treatment.
            if not self.character_switch_menu.active and not self.save_point_menu.active \
                    and not self.dialogue_choice_menu.active and not self._levelup_active \
                    and not self.save_flow_active:
                self.player.update(dt)

            # Level-up sequence — drives the player's facing turns and the
            # levelup.png animation directly, so it needs to keep ticking
            # even while player.update(dt) itself is frozen just above.
            self._update_levelup_sequence(dt)

            # Advance the Instant Transmission hop sequence itself. This now
            # runs alongside the normal (unfrozen) world update — the world
            # only freezes during target aiming above, not during the hops.
            if self.player.is_teleporting_it:
                arrived_enemy = self.player.update_it_teleport(dt)
                if arrived_enemy is not None:
                    self._apply_instant_transmission_damage(arrived_enemy)
                # One teleport.wav per hop — including the final trip back
                # home, which arrived_enemy above never reports since
                # there's no enemy to land on. See Player.
                # pop_pending_it_teleport_hop()/update_it_teleport().
                if self.player.pop_pending_it_teleport_hop():
                    self.sound_manager.play_sfx('teleport')

            # Ghost Kamikaze — its creation/hold/attack lifecycle needs the
            # room's enemy list to pick a target at the moment the hold
            # resolves, which Player doesn't have access to on its own
            # (same reasoning as Instant Transmission's targeting above),
            # so it's ticked centrally here rather than from inside
            # Player.update(). See GhostKamikazeAttack's own docstring.
            self._update_ghost_kamikaze(dt)

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
                    print(f"[world_map_music] fast-path firing for map "
                          f"'{getattr(self, '_active_world_map_name', '')}'")
                    self._apply_world_map_music(getattr(self, '_active_world_map_name', ''))
                    self._mjf_state  = 'fade_in'
                    self._mjf_active = False
                    self.player.enter_idle()

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
                blast = Projectile(spawn_x, spawn_y, self.player.direction)
                blast.owner = self.player  # so damage can be rolled off the player's POW — see Enemy.check_collision_with_attack's 'projectile' branch
                self.projectiles.append(blast)
                self.sound_manager.play_sfx(random.choice(('kiblast1', 'kiblast2')))
                self.player.pending_blast = None

            # Spawn the Ultra Volleyball attack when the (shared kiblast)
            # throw animation completes — same release-frame timing as the
            # regular blast poll above, tracked independently via
            # pending_ultra_volleyball so firing one never consumes the other.
            if self.player.pending_ultra_volleyball == 'ready':
                spawn_x, spawn_y = self.player.get_blast_spawn_position()
                self.ultra_volleyballs.append(UltraVolleyballAttack(
                    spawn_x, spawn_y, self.player.direction,
                    end_frame_width=8, end_frame_height=4,
                    middle_frame_width=8, middle_frame_height=4,
                    decay_frame_width=8, decay_frame_height=4,
                ))
                self.sound_manager.play_sfx(random.choice(('kiblast1', 'kiblast2')))
                self.player.pending_ultra_volleyball = None

            # Charged Melee: spawn a hit every charged_melee_hit_interval
            # seconds while the lunge/spin is playing out. Reuses the exact
            # same MeleeAttack pipeline (collision, sfx, cleanup) as a
            # regular tap-melee swing — see
            # Player.pop_pending_charged_melee_hit()/update_charged_melee_action().
            charged_melee_hit = self.player.pop_pending_charged_melee_hit()
            if charged_melee_hit:
                self.melee_attacks.append(charged_melee_hit)

            # Screen transition update.
            if self.transition_controller.is_transitioning():
                self.transition_controller.update(dt, self.player)

            # Ambient room weather (event-driven, see 'weather' action).
            self._update_room_weather(dt)

            # Flying controller update.
            if self.flying_controller.is_active():
                self.flying_controller.update(dt)
                # Tick the camera here too unless we're mid-room-swap or in a cutscene.
                if not self.flying_controller.is_transitioning_rooms and not self.active_cutscene_runtime:
                    self.camera.update(self.player, self.current_room.width,
                                       self.current_room.height, dt)

            # Nimbus cloud controller update. Unlike the flying controller,
            # the camera is intentionally never ticked here — it stays
            # static/locked for the whole ride (see NimbusCloudController and
            # _handle_nimbus_room_transition) rather than following the player.
            if self.nimbus_controller.is_active():
                self.nimbus_controller.update(dt)

            # Cutscene trigger detection and runtime tick.
            self._update_cutscene_triggers(dt)

            # Trigger box overlap/key-press detection.
            #
            # Gated on the *current* _mjf_state/_mjf_active rather than relying
            # solely on the top-of-frame early-return above: that check only
            # takes effect starting next frame, but _mjf_state can flip to
            # 'fade_in' (and _apply_world_map_music() can fire) partway through
            # THIS frame's update — see the fade-out tick block earlier in this
            # method. Without this guard, a still-live (e.g. always_run,
            # once=False) room-music trigger box would fire again later in that
            # same frame and immediately re-apply the room's track, stomping
            # the world map music that was just switched to a few lines above.
            if self._mjf_state is None and not self._mjf_active:
                self._update_trigger_boxes(dt)

            # Re-check every active mission's objective conditions against
            # current flag/variable/live state — replaces the old per-event
            # mission_manager.on_room_entered/on_enemy_killed/on_npc_talked
            # hooks that used to be sprinkled through this file. See
            # MissionManager.evaluate_active_missions()'s docstring.
            self.mission_manager.evaluate_active_missions(self.flag_manager)

            # Walk-based room transition detection (skip during fade).
            if not self.transition_controller.is_transitioning():
                self._check_room_transitions()

            self.level_up_notification.update(dt)

            # Beam charge and auto-fire mechanics.
            self._update_beam(dt)
            self._update_kamekameha(dt)
            self._update_banshee_blast(dt)
            self._update_final_flash(dt)
            self._update_big_bang_kamehameha(dt)
            self._update_flame_kamehameha(dt)

            # Player projectiles.
            for projectile in self.projectiles[:]:
                projectile.update(self.current_room.width, self.current_room.height, dt)
                if not projectile.active:
                    self.projectiles.remove(projectile)

            # Scripted 'firebeam' cutscene attacks — spawned via
            # _cutscene_spawn_attack into self.cutscene_beams. Ticked here
            # unconditionally (not gated on active_cutscene_runtime) so a
            # beam fired right as a cutscene ends still finishes growing/
            # decaying out normally instead of freezing mid-flight.
            for beam in self.cutscene_beams[:]:
                for wall in self.collision_objects:
                    distance = wall.get_beam_block_distance(beam)
                    if distance is not None:
                        beam.report_obstruction(distance)

                # _cutscene_release_timer (set in _cutscene_spawn_attack) is
                # how much longer the beam stays fully out before closing
                # back up — mirrors the scripted attack action's pose
                # duration. Not present on any other BeamAttack, so this is
                # only ever read/decremented for cutscene-fired ones.
                release_timer = getattr(beam, '_cutscene_release_timer', None)
                if release_timer is not None and not beam.decaying:
                    release_timer -= dt
                    if release_timer <= 0:
                        beam.start_decay()
                    else:
                        beam._cutscene_release_timer = release_timer

                beam.update(dt)
                if not beam.active:
                    self.cutscene_beams.remove(beam)

            # Big Bang Attack — kept in its own list rather than
            # self.projectiles (see where it's appended in the KEYUP
            # handler) since it pierces instead of being consumed on the
            # first hit; the collision block for it is its own dedicated
            # continuous-contact check further down, same shape as
            # dragon_fist. The only way one of these ever ends up
            # inactive here is from reaching its own MAX_DISTANCE or
            # leaving the room (see BigBangAttackBlast.update()) — never
            # from a hit — so spawning the destruction burst exactly
            # here, right as it's pruned, is the correct single place to
            # do it (see spawn_destruction_burst()'s own docstring).
            for blast in self.big_bang_attacks[:]:
                blast.update(self.current_room.width, self.current_room.height, dt)
                if not blast.active:
                    self.big_bang_attacks.remove(blast)
                    self.big_bang_destruction_effects.append(blast.spawn_destruction_burst())

            # Ultra Volleyball attacks — fixed 3-segment chains that travel
            # on their own (unlike a beam, no charge/hold state to drive
            # here); each despawns itself once it reaches its fixed
            # travel_distance, or gets deactivated below on enemy contact.
            for ultra_volleyball in self.ultra_volleyballs[:]:
                ultra_volleyball.update(dt)
                if not ultra_volleyball.active:
                    self.ultra_volleyballs.remove(ultra_volleyball)

            # Melee attacks.
            for melee in self.melee_attacks[:]:
                melee.update(dt)
                if not melee.active:
                    if not getattr(melee, 'hit_something', False):
                        self.sound_manager.play_sfx(random.choice(('melee1', 'melee2')))
                    self.melee_attacks.remove(melee)

            # Enemy AI, combat resolution, and defeat handling — frozen
            # while any dialogue box is up (plain NPC/event dialogue via
            # dialogue_box, or the dialogue_choice_menu), same reasoning as
            # player movement being suppressed below: nothing should be
            # able to sneak up on or hit the player while they're reading.
            # Deliberately NOT frozen for the death-notice box itself (see
            # _update_death_sequence) — the player is already fully locked
            # out via is_dead, but the rest of the world (and whatever
            # killed them) should keep moving right through it.
            if (not self.dialogue_box.active or self._death_state == 'box') \
                    and not self.dialogue_choice_menu.active and not self._levelup_active:
                enemies_defeated_this_frame = self._update_enemies(dt)
            else:
                for enemy in self.enemies:
                    self._freeze_actor_to_idle(enemy)

            # Grow/decay the beam now that enemies have moved (and reported
            # any beam-blocking contact) this frame — see _grow_beam()
            # docstring for why this has to come after _update_enemies().
            self._grow_beam(dt)
            self._grow_kamekameha(dt)
            self._grow_banshee_blast(dt)
            self._grow_final_flash(dt)
            self._grow_big_bang_kamehameha(dt)

            # Enemy projectile systems.
            self._update_bombs(dt)
            self._update_enemy_bullets(dt)
            self._update_enemy_rockets(dt)
            self._update_enemy_kiblasts(dt)

            # Player's masenko projectiles.
            self._update_masenko_projectiles(dt)

            # Dropped zeni pickups — hop/settle, magnet toward player, collect.
            self._update_zeni_pickups(dt)

            # Dropped item pickups — toss/bounce/settle, collect. No cull
            # fast-path like zeni's: there's realistically only ever a
            # handful of these on screen at once, not a pile in the
            # thousands, so the plain per-frame update() is cheap enough.
            self._update_item_pickups(dt)

            # Tick damage number popups.
            self.dmg_numbers.update(dt)

            # Explosion visuals.
            for explosion in self.explosions[:]:
                explosion.update(dt)
                if not explosion.active:
                    self.explosions.remove(explosion)

            # Genkidama hit-flash visuals keep playing out after the hitstop
            # ends too, in case the animation outlasts the freeze itself.
            # (The white flash timer's own countdown happens exclusively in
            # the hitstop gate at the top of update() — not here.)
            for hit_fx in self.genkidama_hit_effects[:]:
                hit_fx.update(dt)
                if not hit_fx.active:
                    self.genkidama_hit_effects.remove(hit_fx)

            # Burning attack hit-impact visuals — a short one-shot playing
            # out at the point of impact, no hitstop/white-flash involved.
            for hit_fx in self.burning_hit_effects[:]:
                hit_fx.update(dt)
                if not hit_fx.active:
                    self.burning_hit_effects.remove(hit_fx)

            # Big Bang Attack's destruction burst — the scattered,
            # staggered brown_destruction puffs left behind once a blast
            # reaches MAX_DISTANCE (see BigBangDestructionBurst). No
            # hitstop/white-flash involved here either.
            for burst in self.big_bang_destruction_effects[:]:
                burst.update(dt)
                if not burst.active:
                    self.big_bang_destruction_effects.remove(burst)

            # NPC interaction detection — frozen while any dialogue box is
            # up, same as enemy AI above, so an NPC doesn't wander off or
            # change facing mid-conversation. Same death-notice exception as
            # enemy AI above — NPCs keep going while it's up.
            if (not self.dialogue_box.active or self._death_state == 'box') \
                    and not self.dialogue_choice_menu.active and not self._levelup_active:
                self._update_npcs(dt)
            else:
                for npc in self.npcs:
                    self._freeze_actor_to_idle(npc)

            # Ambient wildlife — no interaction detection needed, just tick them.
            # Frozen during the level-up sequence too, so it reads as "everything
            # stops" rather than just enemies/NPCs.
            if not self._levelup_active:
                self._update_critters(dt)

            # Dialogue box animation.
            self.dialogue_box.update(dt)

            # Save point proximity detection.
            self._update_save_points(dt)

            # Chest proximity detection.
            self._update_chests(dt)

            # Advance any chest-open sequence in progress (pickup pose +
            # floating icon) — see _handle_interact's chest branch.
            self._update_chest_pickup(dt)

            # Advance any dropped-item pickup sequence in progress — same
            # pose + floating icon flow as chests, see _handle_interact's
            # item-pickup branch.
            self._update_item_pickup_finish(dt)

            # World-map object proximity detection.
            self._update_world_map_objects(dt)

            # Destructible stones.
            self._update_stones(dt)

            # Decorations (trees, etc.) — animation playback only.
            self._update_decorations(dt)

            # Level gates.
            self._update_gates(dt)

            # Doors — proximity open/close.
            self._update_doors(dt)

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
            # progress — FlyingController owns stopping it once landed. The
            # nimbus cloud controller reuses the same cue for its ride, so
            # it's guarded here too.
            if self.player.is_transformed():
                self.sound_manager.play_looping_sfx('aura')
            elif not self.flying_controller.is_active() and not self.nimbus_controller.is_active():
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
            if self.entity_creator.active:
                self.entity_creator.update(dt)
                return
            if self.room_editor.active:
                self._sync_event_editor_rooms()
                self.room_editor.update(dt, self._get_logical_mouse_pos())
                return

        if self.cutscene_editor.active:
            self.cutscene_editor.update(dt)
            return

    # ── Update sub-routines ───────────────────────────────────────────────────

    def _update_player_movement(self, dt):
        """Read directional input, move the player, and resolve collisions.

        Movement is suppressed while a flying sequence is active. While the
        flame kamehameha is firing, the same movement-key input steers its
        tip (see FlameKamehamehaAttack.set_control_input) instead of
        walking the player, since player.move() no-ops during the attack.
        Collision order: stones → gates → walls → NPCs.
        """
        keys       = pygame.key.get_pressed()
        dx = dy    = 0
        is_running = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT] or self.player.is_running
        # While spinning the energy sword, movement is allowed (can_move()
        # special-cases it) but capped to normal walking speed regardless
        # of run being held.
        if self.player.is_spinning_sword:
            is_running = False

        # Build a movement direction vector from the WASD keys.
        if keys[pygame.K_a]  and not keys[pygame.K_d]: dx = -1
        elif keys[pygame.K_d] and not keys[pygame.K_a]: dx = 1
        if keys[pygame.K_w]    and not keys[pygame.K_s]:  dy = -1
        elif keys[pygame.K_s] and not keys[pygame.K_w]:   dy = 1

        # While the flame kamehameha is firing, player.move() is a no-op
        # (can_act() is False), so redirect this frame's raw movement
        # input to steering the chain's tip instead of walking the player.
        if self.player.is_firing_flame_kamehameha and self.player.current_flame_kamehameha:
            self.player.current_flame_kamehameha.set_control_input(dx, dy)

        if dx == 0 and dy == 0:
            # No directional input — snap back to idle. Skipped while the
            # nimbus controller is actively boarding/anchoring the player:
            # it drives player.x/y directly (no keys pressed, so dx/dy are
            # always 0 here) and sets its own walk/idle animation for that
            # phase — letting this block fire every frame would immediately
            # stomp the walk animation it just set back to idle before it
            # can ever advance a frame.
            if not self.nimbus_controller.is_active():
                self.player.is_running = False
                if not self.player.is_transitioning:
                    if self.player.current_animation_state in ('walk', 'run'):
                        self.player.enter_idle()

        if (dx != 0 or dy != 0) and not self.flying_controller.is_active() and not self.nimbus_controller.is_active():
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

            # Running straight into a wall drops the sprint toggle back to a
            # normal walk. is_running is sticky (set by double-tap, see the
            # KEYDOWN handler) so without this the player stays in the run
            # animation/speed indefinitely — even when knockback doesn't
            # fire this frame (e.g. its cooldown is still active) — for as
            # long as the wall-ward key stays held. Diagonal hits are
            # excluded since those slide along the wall on the free axis
            # rather than actually stopping the player (same condition used
            # for the knockback trigger above).
            if is_running and (self.player._blocked_x or self.player._blocked_y) \
                    and not (dx != 0 and dy != 0):
                self.player.is_running = False

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
                        self.flag_manager.mark_room_visited(target_room_name)

                self.player.is_transitioning = True
                self.transition_controller.start_transition(
                    self.player, transition, complete_transition
                )
                break

    def _update_beam(self, dt):
        """Tick beam charging and auto-fire once fully charged.

        Growth/obstruction handling now lives in _grow_beam(dt), called
        later in update() after _update_enemies() — see that method for why.
        """
        if self.player.is_charging_beam:
            self.player.update_beam_charge(dt)

        if (not self.player.is_firing_beam
                and self.player.beam_charge_time >= self.player.beam_charge_required):
            beam = self.player.fire_beam_auto()
            if beam:
                self.player.current_beam = beam
                self.sound_manager.play_sfx('beam')

    def _update_kamekameha(self, dt):
        """Tick Kamekameha charging and auto-fire once fully charged.
        Mirrors _update_beam(dt) exactly, against the Kamekameha's separate
        state.
        """
        if self.player.is_charging_kamekameha:
            self.player.update_kamekameha_charge(dt)

        if (not self.player.is_firing_kamekameha
                and self.player.kamekameha_charge_time >= self.player.kamekameha_charge_required):
            kamekameha = self.player.fire_kamekameha_auto()
            if kamekameha:
                self.player.current_kamekameha = kamekameha
                self.sound_manager.play_sfx('beam')

    def _grow_kamekameha(self, dt):
        """Report obstructions and grow/decay the current Kamekameha.
        Mirrors _grow_beam(dt) exactly — see that method's docstring for why
        this runs after _update_enemies(dt).
        """
        if self.player.current_kamekameha:
            for wall in self.collision_objects:
                distance = wall.get_beam_block_distance(self.player.current_kamekameha)
                if distance is not None:
                    self.player.current_kamekameha.report_obstruction(distance)

            self.player.current_kamekameha.update(dt)
            if not self.player.current_kamekameha.active:
                self.player.current_kamekameha = None

    def _update_banshee_blast(self, dt):
        """Tick Banshee Blast charging and auto-fire once fully charged.
        Mirrors _update_beam(dt)/_update_kamekameha(dt) exactly, against
        the Banshee Blast's separate state.
        """
        if self.player.is_charging_banshee_blast:
            self.player.update_banshee_blast_charge(dt)

        if (not self.player.is_firing_banshee_blast
                and self.player.banshee_blast_charge_time >= self.player.banshee_blast_charge_required):
            banshee_blast = self.player.fire_banshee_blast_auto()
            if banshee_blast:
                self.player.current_banshee_blast = banshee_blast
                self.sound_manager.play_sfx('beam')

    def _grow_banshee_blast(self, dt):
        """Report obstructions and grow/decay the current Banshee Blast.
        Mirrors _grow_beam(dt)/_grow_kamekameha(dt) exactly — see
        _grow_beam(dt)'s docstring for why this runs after _update_enemies().
        """
        if self.player.current_banshee_blast:
            for wall in self.collision_objects:
                distance = wall.get_beam_block_distance(self.player.current_banshee_blast)
                if distance is not None:
                    self.player.current_banshee_blast.report_obstruction(distance)

            self.player.current_banshee_blast.update(dt)
            if not self.player.current_banshee_blast.active:
                self.player.current_banshee_blast = None

    def _update_flame_kamehameha(self, dt):
        """Tick the flame kamehameha and clear it once it stops.

        Charging itself (the charging_flame_kamehameha.png hold effect and
        the auto-fire-when-ready check) is entirely handled inside
        player.update() via update_flame_kamehameha_charge()/
        fire_flame_kamehameha_auto() — see player.py — since it's driven by
        current_animation_state there, same as beam's charge tick. All this
        method does is advance the already-fired FlameKamehamehaAttack
        (which is a fixed-length chain, fully "grown" the instant it's
        created — no _grow_ companion method needed) and clear it once
        stop() has ended it.
        """
        if self.player.current_flame_kamehameha:
            self.player.current_flame_kamehameha.update(dt)
            if not self.player.current_flame_kamehameha.active:
                self.player.current_flame_kamehameha = None

    def _grow_beam(self, dt):
        """Report obstructions and grow/decay the current beam.

        Deliberately called AFTER _update_enemies(dt) rather than back-to-back
        with _update_beam(dt): enemies move (including any beam-driven
        knockback push) inside _update_enemies(), and report their blocking
        distance to the beam as part of that same call. If we grew the beam
        first and let enemies move/report afterward, this frame's growth
        would always be capped using LAST frame's (pre-move) enemy position —
        a one-frame-stale cap that let the beam_rect drift out of overlap
        with the enemy for a frame, showing the normal tip and a gap before
        growth caught back up. Growing the beam after enemies (and after
        their post-push position is already reported) means the cap always
        reflects this frame's true position — the tip stays glued to the
        enemy with no lag. Wall obstructions don't move, so their ordering
        relative to this call never mattered, but reporting them here too
        keeps everything in one place.
        """
        if self.player.current_beam:
            for wall in self.collision_objects:
                distance = wall.get_beam_block_distance(self.player.current_beam)
                if distance is not None:
                    self.player.current_beam.report_obstruction(distance)

            self.player.current_beam.update(dt)
            if not self.player.current_beam.active:
                self.player.current_beam = None

    def _update_final_flash(self, dt):
        """Tick Final Flash charging and auto-fire once fully charged.
        Mirrors _update_beam(dt) exactly, against the beam's separate state.
        """
        if self.player.is_charging_final_flash:
            self.player.update_final_flash_charge(dt)

        if (not self.player.is_firing_final_flash
                and self.player.final_flash_charge_time >= self.player.final_flash_charge_required):
            final_flash = self.player.fire_final_flash_auto()
            if final_flash:
                self.player.current_final_flash = final_flash
                self.sound_manager.play_sfx('final_flash')

    def _grow_final_flash(self, dt):
        """Report obstructions and grow/decay the current Final Flash.
        Mirrors _grow_beam(dt) exactly — see that method's docstring for why
        this runs after _update_enemies(dt).
        """
        if self.player.current_final_flash:
            for wall in self.collision_objects:
                distance = wall.get_beam_block_distance(self.player.current_final_flash)
                if distance is not None:
                    self.player.current_final_flash.report_obstruction(distance)

            self.player.current_final_flash.update(dt)
            if not self.player.current_final_flash.active:
                self.player.current_final_flash = None

    def _update_big_bang_kamehameha(self, dt):
        """Tick Big Bang Kamehameha charging and auto-fire once fully charged.
        Mirrors _update_final_flash(dt)/_update_beam(dt) exactly, against the
        beam's separate state — BigBangKamehamehaAttack reuses BeamAttack's
        growing/tiling/decay pipeline wholesale (see
        attacks/big_bang_kamehameha.py), so it's driven the same
        hold-to-charge/auto-fire way, not flame_kamehameha's fixed-chain shape.
        """
        if self.player.is_charging_big_bang_kamehameha:
            self.player.update_big_bang_kamehameha_charge(dt)

        if (not self.player.is_firing_big_bang_kamehameha
                and self.player.big_bang_kamehameha_charge_time >= self.player.big_bang_kamehameha_charge_required):
            big_bang_kamehameha = self.player.fire_big_bang_kamehameha_auto()
            if big_bang_kamehameha:
                self.player.current_big_bang_kamehameha = big_bang_kamehameha
                self.sound_manager.play_sfx('big_bang_kamehameha')

    def _grow_big_bang_kamehameha(self, dt):
        """Report obstructions and grow/decay the current Big Bang Kamehameha.
        Mirrors _grow_final_flash(dt)/_grow_beam(dt) exactly — see
        _grow_beam's docstring for why this runs after _update_enemies(dt).
        """
        if self.player.current_big_bang_kamehameha:
            for wall in self.collision_objects:
                distance = wall.get_beam_block_distance(self.player.current_big_bang_kamehameha)
                if distance is not None:
                    self.player.current_big_bang_kamehameha.report_obstruction(distance)

            self.player.current_big_bang_kamehameha.update(dt)
            if not self.player.current_big_bang_kamehameha.active:
                self.player.current_big_bang_kamehameha = None

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
            enemy.update(dt, self.player, self.current_room.width, self.current_room.height, game_config=self.game_config)

            # If the enemy's AI called player.take_damage() during update, spawn a popup
            if self.player.last_damage_taken > 0:
                self._play_impact_sfx()
                self.dmg_numbers.spawn(
                    self.player.x, self.player.y - self.player.height // 2,
                    self.player.last_damage_taken, variant='player',
                )

            for melee in self.melee_attacks:
                if not melee.active:
                    continue
                # An encased enemy (see Enemy.encase(), set by
                # UltraVolleyballAttack contact below) is immune to normal
                # melee damage — check_collision_with_attack no-ops for it
                # entirely (see that method's guard). A melee landing on
                # the casing instead triggers the roll via
                # try_trigger_roll(), which deals no damage itself; only
                # the eventual collision impact does (see Enemy._end_roll).
                if enemy.is_encased:
                    if enemy.try_trigger_roll(melee):
                        melee.hit_something = True
                        self._play_melee_hit_sfx()
                        self.camera.start_shake(intensity=8, duration=0.2)
                    continue
                if enemy.check_collision_with_attack(melee, 'melee', self.game_config):
                    melee.hit_something = True
                    self._play_melee_hit_sfx()
                    self._play_impact_sfx()
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            if self.player.is_punching:
                # No separate attack object — the player itself is passed in
                # as `attack` since this is just a plain "how close is the
                # nearest enemy right now" radius test around the player's
                # own position (see enemy.py's 'energy_punch' branch), unlike
                # beam/kamekameha which check against a spawned attack
                # object's own position.
                if enemy.check_collision_with_attack(self.player, 'energy_punch'):
                    self._play_melee_hit_sfx()
                    self._play_impact_sfx()
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            for projectile in self.projectiles:
                if projectile.active and enemy.check_collision_with_attack(projectile, 'projectile', self.game_config):
                    projectile.active = False
                    if isinstance(projectile, GenkidamaBlast):
                        self._trigger_genkidama_hit(projectile.x, projectile.y)
                    elif isinstance(projectile, BurningAttack):
                        enemy.stun(projectile.stun_duration, projectile.direction)
                        self._trigger_burning_hit(enemy.x, enemy.y)
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            # Ultra Volleyball — on contact, encase() the enemy instead of
            # damaging/pushing it (see enemy.py's 'ultra_volleyball' branch
            # of check_collision_with_attack), then consume the attack the
            # same way a regular projectile is consumed on hit.
            for ultra_volleyball in self.ultra_volleyballs:
                if ultra_volleyball.active and enemy.check_collision_with_attack(ultra_volleyball, 'ultra_volleyball_attack'):
                    ultra_volleyball.active = False


            if self.player.current_beam:
                if enemy.check_collision_with_attack(self.player.current_beam, 'beam'):
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            if self.player.current_kamekameha:
                if enemy.check_collision_with_attack(self.player.current_kamekameha, 'beam'):
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            if self.player.current_banshee_blast:
                if enemy.check_collision_with_attack(self.player.current_banshee_blast, 'beam'):
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            if self.player.current_final_flash:
                if enemy.check_collision_with_attack(self.player.current_final_flash, 'beam'):
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            # BigBangKamehamehaAttack reuses BeamAttack's rect/length
            # attributes wholesale (see attacks/big_bang_kamehameha.py), so
            # — unlike flame_kamehameha below — it collides as plain 'beam'
            # rather than needing its own collision type.
            if self.player.current_big_bang_kamehameha:
                if enemy.check_collision_with_attack(self.player.current_big_bang_kamehameha, 'beam'):
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            # FlameKamehamehaAttack is a fixed 3-segment chain, not a
            # stretching beam_rect, so it's given its own collision type
            # ('flame_kamehameha') rather than being passed off as 'beam' —
            # reusing 'beam' would assume rect/length attributes this attack
            # doesn't have. See enemy.py's 'flame_kamehameha' branch of
            # check_collision_with_attack: it hits-tests against the
            # chain's current world bounding box (which follows the
            # player-steered tip) and pushes/damages the same way 'beam'
            # does on contact.
            if self.player.current_flame_kamehameha:
                if enemy.check_collision_with_attack(self.player.current_flame_kamehameha, 'flame_kamehameha'):
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            # DragonFistAttack is a bending, spring-damped chain (not a
            # fixed straight-line one like flame_kamehameha above), so it
            # gets its own collision type ('dragon_fist') too — reusing
            # 'flame_kamehameha' would test one bounding box around the
            # whole chain, which could span empty space between segments
            # on a curve. See enemy.py's 'dragon_fist' branch of
            # check_collision_with_attack: it tests every individual
            # piece (attack.get_segment_rects()) against the enemy, and
            # pushes/damages the same continuous per-frame way 'beam'/
            # 'flame_kamehameha' do on contact.
            if self.player.current_dragon_fist:
                if enemy.check_collision_with_attack(self.player.current_dragon_fist, 'dragon_fist'):
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            # Ghost Kamikaze — one hit per homing ghost, single instance
            # apiece rather than the continuous per-frame push beam/
            # dragon_fist use: each ghost is its own small hitbox
            # (get_homing_ghosts() filters out any still-spawning/idling/
            # already-impacted ghost, see GhostKamikazeAttack), and on a
            # hit it switches straight to its brown_destruction animation
            # (trigger_impact()) instead of lingering to hit again. Backed
            # by enemy.py's 'ghost_kamikaze_attack' branch of
            # check_collision_with_attack — a plain single-hit rect check
            # against attack.get_collision_rect(), same shape as the
            # 'ultra_volleyball_attack' branch there, not a continuous
            # push like 'beam'/'dragon_fist'.
            if self.player.current_ghost_kamikaze:
                for ghost in self.player.current_ghost_kamikaze.get_homing_ghosts():
                    if enemy.check_collision_with_attack(ghost, 'ghost_kamikaze_attack'):
                        ghost.trigger_impact()
                        self.dmg_numbers.spawn(
                            enemy.x, enemy.y - enemy.height // 2,
                            enemy.last_damage_dealt, variant='enemy',
                        )

            # Big Bang Attack — pierces rather than being consumed on
            # hit (see BigBangAttackBlast's own docstring), so this is a
            # continuous per-frame check like 'beam'/'dragon_fist' above,
            # not a single-hit one like ghost_kamikaze/ultra_volleyball —
            # nothing here deactivates the blast; it keeps traveling and
            # can go on to hit something else. An enemy's own
            # take_damage() i-frames are what keep this from restacking
            # damage every frame of overlap. Iterates self.big_bang_attacks
            # directly rather than self.projectiles, since it's kept in
            # its own list for exactly this reason (see where it's
            # appended in the KEYUP handler).
            for blast in self.big_bang_attacks:
                if blast.active and enemy.check_collision_with_attack(blast, 'big_bang_attack'):
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            # Energy sword spin — omnidirectional hitbox around the player,
            # persists across many frames like the beam does above, so
            # per-enemy hit ticking is gated by the effect itself
            # (can_hit/register_hit) rather than re-damaging every frame.
            # NOTE: requires a 'sword' branch in enemy.check_collision_with_attack
            # — see the energy_sword.py delivery notes.
            if self.player.energy_sword_spin:
                sword = self.player.energy_sword_spin
                if sword.can_hit(enemy) and enemy.check_collision_with_attack(sword, 'sword'):
                    sword.register_hit(enemy)
                    self._play_melee_hit_sfx()
                    self._play_impact_sfx()
                    self.dmg_numbers.spawn(
                        enemy.x, enemy.y - enemy.height // 2,
                        enemy.last_damage_dealt, variant='enemy',
                    )

            # Zeni drop — enemy.zeni_drop is rolled once in Enemy.take_damage()
            # at the moment the killing blow lands (see its init comment in
            # enemy.py), so pickups pop out immediately rather than waiting
            # for the death animation to finish. Consumed and cleared here
            # the same frame it's set; spawns world pickups (see ZeniPickup)
            # that hop away from the player, instead of crediting the player
            # straight away — collection happens in _update_zeni_pickups.
            if enemy.zeni_drop:
                from core.zeni_system import spawn_zeni_pickups
                _dx = enemy.x - self.player.x
                _dy = enemy.y - self.player.y
                _dist = math.hypot(_dx, _dy)
                _direction = (_dx / _dist, _dy / _dist) if _dist > 0 else (1.0, 0.0)
                self.zeni_pickups.extend(
                    spawn_zeni_pickups(enemy.zeni_drop, enemy.x, enemy.y, _direction))
                enemy.zeni_drop = None

            if not enemy.active:
                defeated   += 1
                xp_reward   = enemy.get_xp_reward(self.game_config)
                self.player.gain_exp(xp_reward, self.game_config)

                self._show_level_up_if_pending()

                # Generic kill bookkeeping — flag_manager.mark_enemy_defeated
                # covers "kill this specific enemy_id at least once" flag
                # conditions; the kill_count: variable bumps below are what
                # MissionManager's 'kill' objectives (variable_is(...) built
                # in mission_manager.build_objective_condition) actually
                # read — see MissionManager.evaluate_active_missions(),
                # called once a frame from update() rather than from here.
                enemy_id  = getattr(enemy, 'enemy_type', getattr(enemy, 'boss_id', ''))
                room_name = self.current_room.name if self.current_room else ''
                self.flag_manager.mark_enemy_defeated(enemy_id)
                self.flag_manager.add_variable(f'kill_count:{enemy_id}', 1)
                self.flag_manager.add_variable(f'kill_count:{enemy_id}:{room_name}', 1)

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

    def _zeni_cull_rect(self):
        """(left, top, right, bottom) world-space rect the camera can
        currently see, padded by _zeni_cull_margin. Shared by the draw-time
        visibility filter and _update_zeni_pickups' cheap off-screen path
        below, so the two can never disagree about what's "on screen"."""
        _zeni_cull_margin = 100
        cam_left   = self.camera.x / RENDER_SCALE - _zeni_cull_margin
        cam_top    = self.camera.y / RENDER_SCALE - _zeni_cull_margin
        cam_right  = (self.camera.x + self.camera.screen_width)  / RENDER_SCALE + _zeni_cull_margin
        cam_bottom = (self.camera.y + self.camera.screen_height) / RENDER_SCALE + _zeni_cull_margin
        return cam_left, cam_top, cam_right, cam_bottom

    def _update_zeni_pickups(self, dt):
        # A big pile is almost entirely coins sitting off-screen — either
        # already settled there, or still "flying" but scattered far off
        # camera from the moment they spawn (see spawn_zeni_pickups' spread,
        # which grows with drop size). Neither case needs the real update():
        # they can't be collected (the player has to be near the camera)
        # and aren't drawn (see the cull filter in the draw pass), so all
        # that matters is aging them correctly. Routing both off-screen
        # cases through the cheap paths below — instead of the full
        # update(), which does the same trig/collision work whether or not
        # anyone can see it — is what fixes the FPS hit both once a pile
        # has settled AND while it's still landing. See ZeniPickup.age_only
        # and .fast_forward_offscreen for what each skips.
        cam_left, cam_top, cam_right, cam_bottom = self._zeni_cull_rect()

        still_active = []
        for pickup in self.zeni_pickups:
            offscreen = not (cam_left <= pickup.x <= cam_right
                              and cam_top <= pickup.y <= cam_bottom)
            if offscreen and pickup.is_settled:
                pickup.age_only(dt)
            elif offscreen:
                pickup.fast_forward_offscreen(dt)
            else:
                pickup.update(dt, self.player)
                if pickup.collected:
                    self.player.zeni = getattr(self.player, 'zeni', 0) + pickup.value
                    self.sound_manager.play_sfx('zeni')
            if pickup.active:
                still_active.append(pickup)
        self.zeni_pickups = still_active

    def _update_item_pickups(self, dt):
        """Tick every dropped-item pickup (see spawn_item_pickup /
        ItemPickup in core/items.py): toss/bounce/settle while airborne.
        Once settled, it just sits there — walking into it does NOT
        collect it (unlike zeni); picking one up requires E while standing
        nearby, same as opening a chest (see nearby_item_pickup /
        _handle_interact's item-pickup branch and _update_item_pickup_finish
        for the rest of that flow). Unlike zeni, these never age out on
        their own either — they're wiped instead in _clear_projectiles
        whenever the room changes (see the request: sit there until picked
        up, or until the player leaves the room)."""
        still_active = []
        for pickup in self.item_pickups:
            pickup.update(dt, self.player)
            if pickup.active:
                still_active.append(pickup)
        self.item_pickups = still_active

        self.nearby_item_pickup = next(
            (p for p in self.item_pickups if p.is_settled and p.is_player_nearby),
            None
        )

    def _get_dropped_item_icon(self, item_id):
        """Same convention/cache shape as _get_chest_item_icon (and
        PauseMenu._get_item_icon) — assets/sprites/items/{item_id}.png for
        consumables/story items, or
        assets/sprites/items/equipment/{slot}/{item_id}.png for equip
        items, or None if it's not on disk."""
        if item_id not in self._item_pickup_icon_cache:
            item_data = ITEMS.get(item_id) or {}
            slot = item_data.get('slot')
            if slot:
                path = f'assets/sprites/items/equipment/{slot}/{item_id}.png'
            else:
                path = f'assets/sprites/items/{item_id}.png'
            try:
                self._item_pickup_icon_cache[item_id] = pygame.image.load(path).convert_alpha()
            except Exception:
                self._item_pickup_icon_cache[item_id] = None
        return self._item_pickup_icon_cache[item_id]

    def _update_item_pickup_finish(self, dt):
        """Finish the delayed item-pickup sequence once
        Player.start_pickup_item's pose (~PICKUP_ITEM_DURATION seconds) has
        played out — mirrors _update_chest_pickup exactly. Until then,
        _pending_item_pickup just sits here — actually granting the item
        back to the inventory is held back so it lands right as the player
        drops out of the pickup pose, instead of popping in immediately
        alongside it (see _handle_interact's item-pickup branch)."""
        if not self._pending_item_pickup:
            return
        if self.player.is_picking_up_item:
            return

        pickup = self._pending_item_pickup
        self._pending_item_pickup = None
        self._pending_item_pickup_icon = None

        inventory = getattr(self.player, 'inventory', None)
        if inventory is not None:
            inventory.append(pickup.item_id)
            # Generic pickup bookkeeping — mirrors the kill-count bumps in
            # _update_enemies. mark_item_picked_up() covers "picked this
            # item up at least once" flag conditions; item_count: is a
            # lifetime tally MissionManager's 'collect_item' objectives
            # read (see mission_manager.build_objective_condition) — a
            # bring_item objective instead reads live inventory count via
            # check_item(), so it doesn't need this variable at all.
            self.flag_manager.mark_item_picked_up(pickup.item_id)
            self.flag_manager.add_variable(f'item_count:{pickup.item_id}', 1)
        item_data = ITEMS.get(pickup.item_id, {})
        item_name = item_data.get('name', pickup.item_id)
        self.dialogue_box.show(f"Picked up {item_name}!", "Item", True, pickup.item_id)

    def _draw_item_pickup_icon(self, screen, camera):
        """Item icon drifting slowly upward above the player's head while
        the pickup_item pose plays out — mirrors _draw_chest_pickup_icon
        exactly, riding self.player.pickup_item_timer directly."""
        if not self._pending_item_pickup or not self._pending_item_pickup_icon:
            return

        duration = max(0.001, self.player.PICKUP_ITEM_DURATION)
        progress = min(1.0, self.player.pickup_item_timer / duration)

        rise_world = 14
        base_world_y = self.player.y - self.player.height / 2
        icon_world_y = base_world_y - progress * rise_world

        icon = self._pending_item_pickup_icon
        scaled = pygame.transform.scale(icon, (
            max(1, int(icon.get_width() * RENDER_SCALE)),
            max(1, int(icon.get_height() * RENDER_SCALE)),
        ))
        screen_x = int(self.player.x * RENDER_SCALE - camera.x)
        screen_y = int(icon_world_y * RENDER_SCALE - camera.y)
        screen.blit(scaled, scaled.get_rect(midbottom=(screen_x, screen_y)))

    def _update_masenko_projectiles(self, dt):
        """Tick in-flight masenko balls thrown by the player. On detonation,
        apply AoE damage to enemies via enemy.check_collision_with_attack's
        'masenko' branch (a radius check against EXPLOSION_RADIUS, separate
        from the rect-based melee/projectile/beam branches), then hand the
        visual explosion off to the shared self.explosions list.
        """
        for proj in self.masenko_projectiles[:]:
            proj.update(dt)

            if proj.pending_explosion is not None:
                for enemy in self.enemies:
                    if enemy.active and enemy.check_collision_with_attack(proj, 'masenko'):
                        self.dmg_numbers.spawn(
                            enemy.x, enemy.y - enemy.height // 2,
                            enemy.last_damage_dealt, variant='enemy',
                        )
                if proj.pending_explosion not in self.explosions:
                    self.explosions.append(proj.pending_explosion)
                proj.pending_explosion = None

            if not proj.active:
                self.masenko_projectiles.remove(proj)

    def _update_enemy_bullets(self, dt):
        """Tick all Gunner bullets, check player collision, and prune spent ones."""
        for bullet in self.enemy_bullets[:]:
            bullet.update(self.current_room.width, self.current_room.height, dt)

            if not self.player.is_dead and bullet.check_collision_with_player(self.player):
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
                if not self.player.is_blocking:
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

            if not self.player.is_dead and rocket.check_collision_with_player(self.player):
                if not self.player.is_blocking:
                    self.player.hurt_tint = 1.0

            if not rocket.active:
                self.enemy_rockets.remove(rocket)

    def _update_enemy_kiblasts(self, dt):
        """Tick all enemy ki-blasts, check player collision, and prune spent ones."""
        for blast in self.enemy_kiblasts[:]:
            blast.update(self.current_room.width, self.current_room.height, dt)

            if blast.active and not self.player.is_dead:
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
                    if not self.player.is_blocking:
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

    def _freeze_actor_to_idle(self, actor):
        """Snap a moving enemy/NPC to its idle animation while its AI
        update is being skipped (dialogue box or choice menu on screen) —
        mirrors how the player itself is snapped to idle when dialogue
        takes over (see _handle_dialogue_box_action). Uses getattr/hasattr
        throughout since not every actor necessarily exposes these, and
        this runs every frame the freeze is in effect so it must be a
        harmless no-op once already idle."""
        if getattr(actor, 'current_animation_state', None) in ('walk', 'run') \
                and hasattr(actor, 'enter_idle'):
            actor.enter_idle()
        if hasattr(actor, 'is_running'):
            actor.is_running = False

    def _update_npcs(self, dt):
        """Tick all NPCs and record whichever one is currently in interaction range."""
        self.nearby_npc = None
        for npc in self.npcs[:]:
            npc.update(dt, self.player, self.current_room.width, self.current_room.height)
            # Only update nearby_npc once — first in-range NPC wins.
            if self.nearby_npc is None and npc.can_interact(self.player):
                self.nearby_npc = npc

    def _update_critters(self, dt):
        """Tick all ambient wildlife (squirrels, birds, butterflies...).

        No player argument, no nearby-interaction bookkeeping — critters
        never react to the player, so there's nothing to track here beyond
        advancing each one's own wander/animation state.
        """
        for critter in self.critters:
            if not critter.active:
                continue
            critter.update(dt, self.current_room.width, self.current_room.height)

    def _update_save_points(self, dt):
        """Tick save points and record whichever one the player is standing near."""
        for sp in self.save_points:
            sp.update(dt, self.player)

        self.nearby_save_point = next(
            (sp for sp in self.save_points if sp.is_player_nearby and sp.active),
            None
        )

    def _update_chests(self, dt):
        """Tick chests and record whichever one the player can currently
        open with E. Already-opened chests are still ticked (so
        is_player_nearby stays accurate if you stand back near one) but
        excluded from nearby_chest since there's nothing left to interact
        with."""
        for chest in self.chests:
            chest.update(dt, self.player)

        self.nearby_chest = next(
            (c for c in self.chests if c.is_player_nearby and not c.opened),
            None
        )

    def _get_chest_item_icon(self, item_id):
        """Same convention/cache shape as PauseMenu._get_item_icon —
        assets/sprites/items/{item_id}.png for consumables/story items, or
        assets/sprites/items/equipment/{slot}/{item_id}.png for equip items
        (body/hands/feet/accessory), or None if it's not on disk."""
        if item_id not in self._chest_icon_cache:
            item_data = ITEMS.get(item_id) or {}
            slot = item_data.get('slot')
            if slot:
                path = f'assets/sprites/items/equipment/{slot}/{item_id}.png'
            else:
                path = f'assets/sprites/items/{item_id}.png'
            try:
                self._chest_icon_cache[item_id] = pygame.image.load(path).convert_alpha()
            except Exception:
                self._chest_icon_cache[item_id] = None
        return self._chest_icon_cache[item_id]

    def _update_chest_pickup(self, dt):
        """Finish the delayed chest-open sequence once Player.start_pickup_item's
        pose (~PICKUP_ITEM_DURATION seconds) has played out. Until then,
        _pending_chest just sits here — grant_loot() and the reward dialogue
        are deliberately held back so they land right as the player drops
        out of the pickup pose, instead of popping up immediately alongside
        it (see _handle_interact's chest branch)."""
        if not self._pending_chest:
            return
        if self.player.is_picking_up_item:
            return

        chest = self._pending_chest
        self._pending_chest = None
        self._pending_chest_icon = None

        if chest.item_id:
            chest.grant_loot(self.player)
            item_data = ITEMS.get(chest.item_id, {})
            item_name = item_data.get('name', chest.item_id)
            text = (f"You found a {item_name}!" if chest.item_qty == 1
                    else f"You found {chest.item_qty}x {item_name}!")
            self.dialogue_box.show(text, "Chest", True, chest.item_id)

    def _draw_chest_pickup_icon(self, screen, camera):
        """Item icon drifting slowly upward above the player's head while
        the pickup_item pose plays out. Rides self.player.pickup_item_timer
        directly (rather than keeping a second timer here) so the float
        always finishes exactly when the pose itself does."""
        if not self._pending_chest or not self._pending_chest_icon:
            return

        duration = max(0.001, self.player.PICKUP_ITEM_DURATION)
        progress = min(1.0, self.player.pickup_item_timer / duration)

        # Total upward drift over the full duration, in world units —
        # "very slowly" means most of a tile's height, not a full one.
        rise_world = 14
        # Where the icon starts: a bit above the player's head.
        base_world_y = self.player.y - self.player.height / 2
        icon_world_y = base_world_y - progress * rise_world

        icon = self._pending_chest_icon
        scaled = pygame.transform.scale(icon, (
            max(1, int(icon.get_width() * RENDER_SCALE)),
            max(1, int(icon.get_height() * RENDER_SCALE)),
        ))
        screen_x = int(self.player.x * RENDER_SCALE - camera.x)
        screen_y = int(icon_world_y * RENDER_SCALE - camera.y)
        screen.blit(scaled, scaled.get_rect(midbottom=(screen_x, screen_y)))

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

            in_range = False
            if obj.variant == 'world_map':
                # Flat map: player walks onto it — overlap test.
                obj_rect = _pg.Rect(
                    obj.x - obj.width  // 2,
                    obj.y - obj.height // 2,
                    obj.width,
                    obj.height,
                )
                in_range = player_rect.colliderect(obj_rect)

            elif obj.variant == 'world_map_sign':
                # Sign: proximity radius, same pattern as NPCs.
                dx = self.player.x - obj.x
                dy = self.player.y - obj.y
                in_range = (dx * dx + dy * dy) <= _SIGN_INTERACT_RADIUS ** 2

            if not in_range:
                continue

            if not getattr(obj, 'map_name', ''):
                # Object editor now blocks placing these without a map
                # selected (see ObjectEditor._is_object_disabled), but a
                # room saved before that check can still have one lying
                # around. Interacting with it would silently no-op — the
                # Mode7 loader falls back to a placeholder PNG and
                # _apply_world_map_music() bails on the empty name with no
                # visible symptom beyond "nothing happens". Surface it
                # instead of pretending this is a working travel point.
                if not getattr(self, '_warned_empty_wmo_ids', None):
                    self._warned_empty_wmo_ids = set()
                obj_key = id(obj)
                if obj_key not in self._warned_empty_wmo_ids:
                    self._warned_empty_wmo_ids.add(obj_key)
                    print(f"[world_map] WorldMapObject at ({obj.x}, {obj.y}) in "
                          f"'{self.current_room.name if self.current_room else '?'}' has no "
                          f"map_name set — re-open it in the Object Editor and pick a map.")
                continue

            self.nearby_world_map_obj = obj
            break

    def _update_stones(self, dt):
        """Tick destructible stones, check melee/projectile hits, and remove
        anything destroyed."""
        for stone in self.destructible_stones[:]:
            stone.update(dt)
            for melee in self.melee_attacks:
                if melee.active and stone.check_collision_with_attack(melee, 'melee'):
                    melee.hit_something = True
                    self._play_melee_hit_sfx()
            for projectile in self.projectiles:
                if not projectile.active:
                    continue
                collision_type = 'genkidama' if isinstance(projectile, GenkidamaBlast) else 'projectile'
                if stone.check_collision_with_attack(projectile, collision_type):
                    projectile.active = False
                    if isinstance(projectile, GenkidamaBlast):
                        self._trigger_genkidama_hit(projectile.x, projectile.y)
            if not stone.active:
                self.destructible_stones.remove(stone)

    def _update_decorations(self, dt):
        """Advance each placed decoration's (tree, etc.) frame animation.
        No attack/collision checks here — unlike destructible stones,
        decorations are purely ambient and never destroyed."""
        for decoration in self.decorations:
            decoration.update(dt)

    def _trigger_genkidama_hit(self, x, y):
        """Spawn the hit-flash effect and kick off the screen white-flash
        when a GenkidamaBlast connects with an enemy or destructible object."""
        self.genkidama_hit_effects.append(GenkidamaHitEffect(x, y))
        self._white_flash_timer = self._WHITE_FLASH_DURATION

    def _trigger_burning_hit(self, x, y):
        """Spawn the impact effect when a BurningAttack connects with an
        enemy. No screen white-flash/hitstop here — that's a genkidama-only
        beat; the burning attack's payoff is the stun, not a freeze."""
        self.burning_hit_effects.append(BurningHitEffect(x, y))

    def _update_instant_transmission(self, dt):
        """Drive Instant Transmission while the world is frozen for target
        aiming (holding the button, moving the cursor, picking targets).

        player.update() runs here so the player keeps animating/holding
        their pose while everything else (enemies, NPCs, projectiles, etc.)
        holds completely still. Once the button is released and the actual
        teleport hops begin, this function is no longer used — the world
        resumes normally and the hop sequence is advanced from the regular
        per-frame update path instead (see update()).
        """
        self.player.update(dt)

        # Keep the camera following the player while aiming — same call
        # used during normal gameplay.
        if not self.active_cutscene_runtime:
            self.camera.update(self.player, self.current_room.width, self.current_room.height, dt)

        if self.player.is_targeting_it and self.it_selector is not None:
            self.it_selector.update(dt)

            keys = pygame.key.get_pressed()
            dx = dy = 0
            if keys[pygame.K_LEFT] and not keys[pygame.K_RIGHT]:
                dx = -1
            elif keys[pygame.K_RIGHT] and not keys[pygame.K_LEFT]:
                dx = 1
            if keys[pygame.K_UP] and not keys[pygame.K_DOWN]:
                dy = -1
            elif keys[pygame.K_DOWN] and not keys[pygame.K_UP]:
                dy = 1
            if dx or dy:
                self.it_selector.move(dx, dy, dt)

            # Hover-select — mark any enemy the cursor is currently over.
            # Each enemy can only be picked once per charge (try_select
            # handles that internally).
            for enemy in self.enemies:
                if not getattr(enemy, 'active', True):
                    continue
                enemy_w = getattr(enemy, 'width', 32)
                enemy_h = getattr(enemy, 'height', 32)
                screen_x = (enemy.x * RENDER_SCALE) - self.camera.x
                screen_y = (enemy.y * RENDER_SCALE) - self.camera.y
                enemy_rect = pygame.Rect(
                    screen_x - (enemy_w * RENDER_SCALE) / 2,
                    screen_y - (enemy_h * RENDER_SCALE) / 2,
                    enemy_w * RENDER_SCALE,
                    enemy_h * RENDER_SCALE,
                )
                self.it_selector.try_select(enemy, enemy_rect)

    def _apply_instant_transmission_damage(self, enemy):
        """Deal the Instant Transmission teleport-strike's damage to `enemy`.

        NOTE: this relies on Enemy.check_collision_with_attack supporting
        an 'instant_transmission' attack_type branch — add one there
        (mirroring how destructible stones got a 'genkidama' branch) if
        it doesn't already exist; without it, this safely does nothing
        rather than crashing.
        """
        if not getattr(enemy, 'active', True):
            return
        strike = InstantTransmissionStrike(enemy.x, enemy.y)
        if enemy.check_collision_with_attack(strike, 'instant_transmission'):
            self.dmg_numbers.spawn(
                enemy.x, enemy.y - enemy.height // 2,
                enemy.last_damage_dealt, variant='enemy',
            )

    def _update_ghost_kamikaze(self, dt):
        """Tick the player's active GhostKamikazeAttack, if any, with the
        room's current enemy list — needed only at the moment its hold
        phase resolves (see GhostKamikazeAttack._resolve()/_pick_target()).
        Drops the reference once the attack finishes (all 3 ghosts done
        with their destruction animation), mirroring how Player itself
        clears current_beam/current_dragon_fist/etc. once .active goes
        False, just centralized here since this object needs an
        enemies argument Player.update() doesn't pass through.
        """
        ghost_kamikaze = self.player.current_ghost_kamikaze
        if ghost_kamikaze is None:
            return
        ghost_kamikaze.update(dt, self.enemies)
        if not ghost_kamikaze.active:
            self.player.current_ghost_kamikaze = None

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

            if self.player.current_kamekameha:
                kamekameha = self.player.current_kamekameha
                if gate not in getattr(kamekameha, '_hit_gates', set()):
                    if gate.check_collision_with_attack(kamekameha, 'beam', self.player):
                        if not hasattr(kamekameha, '_hit_gates'):
                            kamekameha._hit_gates = set()
                        kamekameha._hit_gates.add(gate)

            if self.player.current_banshee_blast:
                banshee_blast = self.player.current_banshee_blast
                if gate not in getattr(banshee_blast, '_hit_gates', set()):
                    if gate.check_collision_with_attack(banshee_blast, 'beam', self.player):
                        if not hasattr(banshee_blast, '_hit_gates'):
                            banshee_blast._hit_gates = set()
                        banshee_blast._hit_gates.add(gate)

            if self.player.current_final_flash:
                final_flash = self.player.current_final_flash
                if gate not in getattr(final_flash, '_hit_gates', set()):
                    if gate.check_collision_with_attack(final_flash, 'beam', self.player):
                        if not hasattr(final_flash, '_hit_gates'):
                            final_flash._hit_gates = set()
                        final_flash._hit_gates.add(gate)

            if self.player.current_big_bang_kamehameha:
                big_bang_kamehameha = self.player.current_big_bang_kamehameha
                if gate not in getattr(big_bang_kamehameha, '_hit_gates', set()):
                    if gate.check_collision_with_attack(big_bang_kamehameha, 'beam', self.player):
                        if not hasattr(big_bang_kamehameha, '_hit_gates'):
                            big_bang_kamehameha._hit_gates = set()
                        big_bang_kamehameha._hit_gates.add(gate)

            # See the matching NOTE in _update_enemies — 'flame_kamehameha'
            # is its own collision type, needs a branch added in
            # LevelGate.check_collision_with_attack to actually connect.
            if self.player.current_flame_kamehameha:
                flame = self.player.current_flame_kamehameha
                if gate not in getattr(flame, '_hit_gates', set()):
                    if gate.check_collision_with_attack(flame, 'flame_kamehameha', self.player):
                        if not hasattr(flame, '_hit_gates'):
                            flame._hit_gates = set()
                        flame._hit_gates.add(gate)

            if not gate.active:
                self.level_gates.remove(gate)

    def _update_doors(self, dt):
        """Tick doors — open on player proximity and play that door's sound;
        non-permanent doors then stay open until the player leaves the room
        (see _load_room_objects/_load_room_objects_as_copies, which close
        them out on the way to the next room)."""
        for door in self.doors:
            door.update(self.player, self.sound_manager)

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
        # Title screen — replaces the entire frame while it's up.
        if self.game_mode == 'title':
            self.title_screen.draw(self.logical_surface)
            pygame.display.flip()
            return

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
            # During landing_fade_in (world-map descent) or the level-up
            # animation phase, a special animation replaces the normal
            # player sprite. For landing, no sprite goes into the layered
            # list at all (it's drawn separately below, same as before).
            # For level-up, _levelup_drawable takes the player's spot in
            # the layered list — same draw_layer/sort_key as the player —
            # so it still y-sorts correctly against NPCs/enemies/tiles
            # instead of always drawing on top of them.
            if self._mjf_state == 'landing_fade_in':
                _player_objs = []
            elif self._levelup_state == 'playing_anim':
                _player_objs = [self._levelup_drawable]
            else:
                _player_objs = [self.player]
            # Cull zeni pickups outside the camera viewport — a pile can be in
            # the thousands, and each on-screen one costs blit/set_alpha
            # calls, so skipping the off-screen ones is a real win.
            # Same rect _update_zeni_pickups uses for its own off-screen
            # fast path, so draw and update always agree on what's visible.
            _cam_left, _cam_top, _cam_right, _cam_bottom = self._zeni_cull_rect()
            _visible_zeni_pickups = [
                p for p in self.zeni_pickups
                if _cam_left <= p.x <= _cam_right and _cam_top <= p.y <= _cam_bottom
            ]

            for obj in (self.projectiles + self.ultra_volleyballs + _player_objs + self.enemies + self.npcs
                        + self.critters
                        + self.destructible_stones + self.decorations + self.level_gates + self.doors
                        + self.chests
                        + self.bombs + self.explosions + self.genkidama_hit_effects
                        + self.burning_hit_effects + self.flying_pads + self.nimbus_clouds
                        + self.save_points + self.world_map_objects
                        + self.masenko_projectiles + _visible_zeni_pickups
                        + self.item_pickups
                        + self.big_bang_attacks + self.big_bang_destruction_effects):
                self.layer_manager.add_object(obj)
            for melee in self.melee_attacks:
                self.layer_manager.add_object(melee)
            if self.player.current_beam:
                self.layer_manager.add_object(self.player.current_beam)
            if self.player.current_charge_effect:
                self.layer_manager.add_object(self.player.current_charge_effect)
            if self.player.current_kamekameha:
                self.layer_manager.add_object(self.player.current_kamekameha)
            if self.player.current_kamekameha_charge_effect:
                self.layer_manager.add_object(self.player.current_kamekameha_charge_effect)
            if self.player.current_banshee_blast:
                self.layer_manager.add_object(self.player.current_banshee_blast)
            if self.player.current_banshee_blast_charge_effect:
                self.layer_manager.add_object(self.player.current_banshee_blast_charge_effect)
            if self.player.current_final_flash:
                self.layer_manager.add_object(self.player.current_final_flash)
            if self.player.current_big_bang_kamehameha:
                self.layer_manager.add_object(self.player.current_big_bang_kamehameha)
            if self.player.current_big_bang_kamehameha_charge_effect:
                self.layer_manager.add_object(self.player.current_big_bang_kamehameha_charge_effect)
            if self.player.current_flame_kamehameha:
                self.layer_manager.add_object(self.player.current_flame_kamehameha)
            if self.player.current_flame_kamehameha_charge_effect:
                self.layer_manager.add_object(self.player.current_flame_kamehameha_charge_effect)
            if self.player.current_final_flash_charge_effect:
                self.layer_manager.add_object(self.player.current_final_flash_charge_effect)
            if self.player.genkidama_charge_effect:
                self.layer_manager.add_object(self.player.genkidama_charge_effect)
            if self.player.current_big_bang_charge:
                self.layer_manager.add_object(self.player.current_big_bang_charge)
            if self.player.masenko_indicator:
                self.layer_manager.add_object(self.player.masenko_indicator)
            if self.player.masenko_hold_effect:
                self.layer_manager.add_object(self.player.masenko_hold_effect)
            if self.player.burning_charge_effect:
                self.layer_manager.add_object(self.player.burning_charge_effect)
            if self.player.current_sword_charge_effect:
                self.layer_manager.add_object(self.player.current_sword_charge_effect)
            if self.player.energy_sword_spin:
                self.layer_manager.add_object(self.player.energy_sword_spin)
            if self.player.current_dragon_fist:
                self.layer_manager.add_object(self.player.current_dragon_fist)
            if self.player.current_ghost_kamikaze:
                self.layer_manager.add_object(self.player.current_ghost_kamikaze)

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

            # Chest-pickup item icon floating up above the player's head —
            # see _handle_interact's chest branch / _update_chest_pickup.
            self._draw_chest_pickup_icon(self.logical_surface, self.camera)

            # Same, for a dropped-item pickup being collected — see
            # _handle_interact's item-pickup branch / _update_item_pickup_finish.
            self._draw_item_pickup_icon(self.logical_surface, self.camera)

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
            _cs_colors = {
                'WHITE': (255, 255, 255), 'RED': (220, 60, 60),
                'CYAN': (80, 220, 220), 'YELLOW': (255, 220, 80),
            }
            self.active_cutscene_runtime.draw_actors(self.logical_surface, self.camera, _cs_colors)

            # Scripted attacks spawned via _cutscene_spawn_attack (kiblast/
            # melee/firebeam) live in the same lists the normal gameplay
            # loop uses (self.projectiles, self.melee_attacks,
            # self.cutscene_beams), but the layer_manager pass above is
            # skipped entirely during cutscenes — without drawing them here
            # too they'd update/collide invisibly. Drawn directly (no
            # y-sort) same as enemy_bullets/rockets/kiblasts above.
            for projectile in self.projectiles:
                projectile.draw(self.logical_surface, self.camera, self.colors)
            for melee in self.melee_attacks:
                melee.draw(self.logical_surface, self.camera, self.colors)
            for beam in self.cutscene_beams:
                beam.draw(self.logical_surface, self.camera, self.colors)

        # Flying pad path previews — editor-only overlay drawn after the layer pass.
        if self.dev_menu.active or self.room_editor.active:
            for pad in self.flying_pads:
                if pad.active:
                    pad.draw_path_preview(self.logical_surface, self.camera, RENDER_SCALE)
            for cloud in self.nimbus_clouds:
                if cloud.active:
                    cloud.draw_path_preview(self.logical_surface, self.camera, RENDER_SCALE)

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
        elif self.room_weather is not None and not self.pause_menu.active:
            w, h = self.logical_surface.get_size()
            self.room_weather.draw(self.logical_surface, w, h)

        # Cutscene colour/invert overlay (screen fades, flash, invert) is drawn
        # BEFORE the UI layer (dialogue box, HUD, menus) so a fade_in/fade_out/
        # flash/invert never hides an open dialogue box — the dialogue reads on
        # top of the fade instead of disappearing underneath it. It still sits
        # above the world, actors, and weather, all of which were already drawn
        # above (draw_actors() was already called before the foreground tile
        # layer above).
        if self.active_cutscene_runtime and not self.pause_menu.active:
            w, h = self.logical_surface.get_size()
            self.active_cutscene_runtime.draw_overlay(self.logical_surface, w, h)

        if not self.dev_menu.active:
            self._draw_ui(self.dt)

        # Dev tools — always drawn last so they sit on top of everything.
        self.sprite_editor.draw(self.logical_surface)
        self.character_creator.draw(self.logical_surface, self.dt)
        self.entity_creator.draw(self.logical_surface, self.dt)
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

    def _get_timer_glyphs(self):
        """Lazy-load the event timer's bitmap font from assets/ui/fonts/timer,
        same convention as pause_menu.py's FlatBitmapFont: skip any glyph
        file that isn't present rather than raising."""
        if not hasattr(self, '_timer_glyphs'):
            import os
            self._timer_glyphs = {}
            folder = os.path.join('assets', 'ui', 'fonts', 'timer')
            names = {'0': '0.png', '1': '1.png', '2': '2.png', '3': '3.png',
                      '4': '4.png', '5': '5.png', '6': '6.png', '7': '7.png',
                      '8': '8.png', '9': '9.png', ':': 'colon.png'}
            for ch, fname in names.items():
                path = os.path.join(folder, fname)
                if not os.path.exists(path):
                    continue
                try:
                    self._timer_glyphs[ch] = pygame.image.load(path).convert_alpha()
                except Exception as e:
                    print(f"_get_timer_glyphs: could not load {path}: {e}")
        return self._timer_glyphs

    def _render_timer_text(self, text, color):
        """Compose `text` edge-to-edge from the timer bitmap font, tinted to
        `color` via BLEND_RGBA_MULT (matches pause_menu.py's _tint/_shadow
        convention) — outline stays black since black * anything = black."""
        glyphs = self._get_timer_glyphs()
        surfs = [glyphs[ch].copy() for ch in text if ch in glyphs]
        for s in surfs:
            s.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
        w = sum(s.get_width() for s in surfs)
        h = max((s.get_height() for s in surfs), default=0)
        out = pygame.Surface((max(w, 1), max(h, 1)), pygame.SRCALPHA)
        x = 0
        for s in surfs:
            out.blit(s, (x, 0))
            x += s.get_width()
        return out

    def _draw_event_timer(self, screen):
        """Top-right HUD readout for the active event timer (timer_start/
        timer_pause/timer_stop actions). Uses the individual-file pixel
        font in assets/ui/fonts/timer, rendered in yellow with a black
        outline for readability, formatted as seconds:milliseconds."""
        timer = self.timers.get(self._active_timer_id) if self._active_timer_id else None
        if not timer or timer['remaining'] <= 0:
            return

        total = timer['remaining']
        secs  = int(total)
        ms    = int(round((total - secs) * 1000))
        if ms >= 1000:
            ms = 999
        hundredths = min(ms // 10, 99)
        text = f"{secs}:{hundredths:02d}"

        scale = 1.5 * RENDER_SCALE
        pad   = int(10 * RENDER_SCALE)
        tx    = SCREEN_WIDTH - pad
        ty    = pad

        outline = self._render_timer_text(text, (0, 0, 0))
        outline = pygame.transform.scale(
            outline, (int(outline.get_width() * scale), int(outline.get_height() * scale)))
        screen.blit(outline, outline.get_rect(topright=(tx + 2, ty + 2)))

        label = self._render_timer_text(text, self.colors['YELLOW'])
        label = pygame.transform.scale(
            label, (int(label.get_width() * scale), int(label.get_height() * scale)))
        screen.blit(label, label.get_rect(topright=(tx, ty)))

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
        te.on_tile_changed             = lambda room_name=None, cells=None: self.invalidate_tile_cache(room_name, cells)
        self._tile_change_hook_installed = True

    def invalidate_tile_cache(self, room_name: str = None, cells=None):
        """Mark a room's baked surface as stale.

        Actual eviction/patching happens once per frame in
        _flush_dirty_tile_rooms(), not immediately, so multiple
        on_tile_changed calls within the same frame collapse into minimal
        work instead of one rebuild per mouse-motion event.

        `cells`, when given, is an iterable of (grid_x, grid_y, width,
        height) world-space cells that actually changed (e.g. a single tile
        placed or erased), where width/height is that tile's real pixel
        footprint from its own tileset. That lets the flush step patch just
        those spots — sized correctly — in the existing baked surface
        instead of throwing the whole thing away — the common case while
        painting/erasing, and the one that used to get laggier the bigger
        the room and the more tiles it had, since a full rebuild both
        reallocates a room-sized surface and re-blits every tile in the
        room on every single edit. Passing no `cells` (or room_name of
        None) falls back to the old full-invalidate behavior, for bulk
        operations like loading a room or toggling layer visibility, where
        a full rebuild is unavoidable anyway.
        """
        if room_name is None:
            self._dirty_tile_rooms.add(None)  # None = flush everything
            return
        if cells:
            self._dirty_tile_cells.setdefault(room_name, set()).update(cells)
        else:
            self._dirty_tile_rooms.add(room_name)

    def _flush_dirty_tile_rooms(self):
        """Apply pending tile-cache invalidations exactly once per frame.

        Call this at the top of the draw loop before any tile surface is
        accessed so every draw pass works with fresh data without doing
        more work than necessary regardless of how many events arrived
        this frame. Full-room invalidations are handled first (and cancel
        any pending per-cell patch for that room, since the rebuild already
        covers it); remaining per-cell patches are then applied in place.
        """
        if self._dirty_tile_rooms:
            if None in self._dirty_tile_rooms:
                self._room_tile_surfaces.clear()
                self._animated_tile_lists.clear()
                self._dirty_tile_cells.clear()
            else:
                for room in self._dirty_tile_rooms:
                    self._room_tile_surfaces.pop((room, True),  None)
                    self._room_tile_surfaces.pop((room, False), None)
                    self._animated_tile_lists.pop((room, True),  None)
                    self._animated_tile_lists.pop((room, False), None)
                    self._dirty_tile_cells.pop(room, None)
            self._dirty_tile_rooms.clear()

        if self._dirty_tile_cells:
            for room_name, cells in self._dirty_tile_cells.items():
                for cell_x, cell_y, cell_w, cell_h in cells:
                    self._patch_tile_cell(room_name, cell_x, cell_y, cell_w, cell_h)
            self._dirty_tile_cells.clear()

    def _patch_tile_cell(self, room_name: str, cell_x: int, cell_y: int, cell_w: int, cell_h: int):
        """Redraw a single grid cell in-place on the already-baked tile
        surfaces for `room_name`, instead of rebuilding the whole room.

        Mirrors the per-tile logic in _build_room_tile_surface() but scoped
        to one cell, so cost is close to O(tiles overlapping this one
        region) — small in practice — rather than a fresh room-sized
        Surface allocation. Selecting which tiles overlap does still scan
        every tile in the room (an exact-position match was cheaper, but
        silently missed tiles that weren't at exactly (cell_x, cell_y) —
        see below); painting/erasing avoids the far more expensive part,
        which is reallocating and re-blitting the whole baked surface.

        `cell_w`/`cell_h` is the real pixel footprint of the tile that was
        placed or erased here (from the tileset it belongs to), NOT a fixed
        grid constant. Clearing by a generic grid size instead of the tile's
        actual size bleeds into whatever sits at the next cell over on a
        finer-grained tileset (e.g. clearing 16px when the tile is really
        8px reaches into the neighboring 8px tile) and silently erases its
        already-baked pixels without redrawing them.

        If a baked surface for this room/layer hasn't been built yet, there's
        nothing to patch — it'll be built fresh (correctly) on first access.
        """
        te = getattr(self.room_editor, 'tileset_editor', None)
        if not te:
            return

        tileset_mgr = te.tileset_manager

        # Select every tile whose real footprint overlaps the region being
        # patched, not just tiles whose top-left corner exactly equals
        # (cell_x, cell_y).
        #
        # An exact-corner match misses two cases: a tile stamped over
        # several smaller tiles clears a region larger than any single
        # tile's own corner (e.g. one 16x16 tile fully covering — and thus
        # correctly deleting — a 2x2 block of 8x8 tiles), and a tile that
        # only partially overlaps the new one (and is intentionally left in
        # place, still sitting in room_tiles) doesn't share that corner
        # either. Filtering on exact position silently drops that leftover
        # tile out of the redraw even though it's still there — it gets
        # blanked from the cleared rectangle and never repainted. That's
        # why it looked gone in the live editor but reappeared after Test
        # Room, since a full rebuild there draws every tile in room_tiles
        # unconditionally and has no such gap.
        region_x0, region_y0 = cell_x, cell_y
        region_x1, region_y1 = cell_x + cell_w, cell_y + cell_h

        cell_tiles = []
        for t in te.room_tiles.get(room_name, []):
            t_tileset = tileset_mgr.get_tileset(t.tileset_name)
            t_w, t_h = (t_tileset.tile_width, t_tileset.tile_height) if t_tileset else (cell_w, cell_h)
            if (t.x < region_x1 and t.x + t_w > region_x0 and
                    t.y < region_y1 and t.y + t_h > region_y0):
                cell_tiles.append(t)
        cell_tiles.sort(key=lambda t: t.layer)

        for bg in (True, False):
            key = (room_name, bg)
            surf = self._room_tile_surfaces.get(key)
            if surf is None:
                continue  # nothing baked yet for this room/layer — skip

            # Clear just this cell's real footprint back to transparent.
            rect = pygame.Rect(
                int(cell_x * RENDER_SCALE), int(cell_y * RENDER_SCALE),
                int(cell_w * RENDER_SCALE), int(cell_h * RENDER_SCALE),
            )
            surf.fill((0, 0, 0, 0), rect)

            # Drop any stale animated-tile entries touching this region for
            # this layer group; they'll be re-added below if still present.
            # Same overlap reasoning as cell_tiles above, rather than an
            # exact corner match.
            anim_list = self._animated_tile_lists.get(key)
            if anim_list:
                kept_anim = []
                for t in anim_list:
                    t_tileset = tileset_mgr.get_tileset(t.tileset_name)
                    t_w, t_h = (t_tileset.tile_width, t_tileset.tile_height) if t_tileset else (cell_w, cell_h)
                    overlaps = (
                        t.x < region_x1 and t.x + t_w > region_x0 and
                        t.y < region_y1 and t.y + t_h > region_y0
                    )
                    if not overlaps:
                        kept_anim.append(t)
                self._animated_tile_lists[key] = kept_anim

            for tile in cell_tiles:
                is_bg_tile = tile.layer < 0
                if bg != is_bg_tile:
                    continue
                tileset = tileset_mgr.get_tileset(tile.tileset_name)
                if not tileset or not tileset.image:
                    continue
                if tileset.is_tile_animated(tile.tile_x, tile.tile_y):
                    self._animated_tile_lists.setdefault(key, []).append(tile)
                    continue
                scaled = tileset.get_scaled_tile_surface(tile.tile_x, tile.tile_y, RENDER_SCALE)
                if scaled:
                    surf.blit(scaled, (int(tile.x * RENDER_SCALE), int(tile.y * RENDER_SCALE)))

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
        animated_tiles = []
        for tile in sorted(tiles, key=lambda t: t.layer):
            is_bg_tile = tile.layer < 0
            if bg != is_bg_tile:
                continue
            tileset = tileset_mgr.get_tileset(tile.tileset_name)
            if not tileset or not tileset.image:
                continue

            # Animated tiles are excluded from the static bake — they're drawn
            # fresh every frame by _draw_animated_tile_overlay() instead, since
            # a static surface can't show a cycling frame.
            if tileset.is_tile_animated(tile.tile_x, tile.tile_y):
                animated_tiles.append(tile)
                continue

            scaled = tileset.get_scaled_tile_surface(tile.tile_x, tile.tile_y, RENDER_SCALE)
            if scaled:
                surf.blit(scaled, (int(tile.x * RENDER_SCALE), int(tile.y * RENDER_SCALE)))

        self._animated_tile_lists[(room_name, bg)] = animated_tiles
        return surf

    def _draw_animated_tile_overlay(self, target_surface: 'pygame.Surface', room_name: str,
                                     bg: bool, camera_x: int, camera_y: int):
        """Blit the small set of animated tiles (water, flags, rotors, etc.) on top
        of the baked static surface for this room/layer.

        Kept separate from the baked surface because those tiles cycle frames
        every tick and can't be pre-rendered once. Cost stays O(animated tiles)
        regardless of total room tile count, since everything static is still
        a single cached blit.
        """
        animated_tiles = self._animated_tile_lists.get((room_name, bg))
        if not animated_tiles:
            return

        te = getattr(self.room_editor, 'tileset_editor', None)
        if not te:
            return

        tick_ms = pygame.time.get_ticks()
        tileset_mgr = te.tileset_manager

        for tile in animated_tiles:
            tileset = tileset_mgr.get_tileset(tile.tileset_name)
            if not tileset or not tileset.image:
                continue

            screen_x = (tile.x * RENDER_SCALE) - camera_x
            screen_y = (tile.y * RENDER_SCALE) - camera_y

            scaled_width = tileset.tile_width * RENDER_SCALE
            scaled_height = tileset.tile_height * RENDER_SCALE
            sw, sh = target_surface.get_size()
            if not (-scaled_width <= screen_x <= sw and -scaled_height <= screen_y <= sh):
                continue

            disp_x, disp_y = tileset.get_animated_coords(tile.tile_x, tile.tile_y, tick_ms)
            scaled = tileset.get_scaled_tile_surface(disp_x, disp_y, RENDER_SCALE)
            if scaled:
                target_surface.blit(scaled, (int(screen_x), int(screen_y)))

    def _load_region_sheet(self, sprite_name: str, frame_size: int, grid_rows: int = 1, frame_h: int = None):
        """Load (and cache) the frame strip/grid a water/grass/dirt region
        draws from.

        Authored in the standalone Sprite Editor as a single PNG at
        assets/tilesets/animated_tiles/<sprite_name>.png: frame_size wide x
        frame_h tall cells, `grid_rows` rows tall (default 1 — a plain
        left-to-right strip, frame count = sheet width // frame_size),
        columns = sheet width // frame_size either way. Returned as a flat
        list in row-major order (row 0's cols left-to-right, then row 1's,
        ...), same as every other animation in the codebase reads a frame
        strip when grid_rows=1.

        `frame_h` defaults to `frame_size` (a square frame) when omitted,
        which is every 'patch'-mode sheet: water/lava/grass all use
        frame_size=64, grid_rows=1 — each frame is itself a grid of
        sub-patches (8x8 patches of 8px for water/lava, 4x4 patches of 16px
        for grass), and the runtime crops the patch at
        (world_x % 64, world_y % 64) out of whichever frame is playing.

        Dirt (mode='tile') is the one sheet that passes `frame_h`
        explicitly (even though its 24x24 frames are square in this case,
        the two are conceptually independent for 'tile' sheets), and
        it's a vertical strip (grid_rows=4, one variant per row) instead
        of the horizontal strips every 'patch' sheet uses. `grid_rows` > 1
        is otherwise only relevant to the retired 'checkerboard' mode (see
        REGION_STYLES and _draw_animated_regions_overlay).

        Frames are pre-scaled to RENDER_SCALE once here so the per-chunk draw
        loop is a plain blit with no repeated scaling cost. Falls back to a
        single solid-color frame_size x frame_h frame if the file is
        missing, so a missing/renamed asset never crashes the game.
        """
        frame_h = frame_size if frame_h is None else frame_h

        if not hasattr(self, '_region_sheet_cache'):
            self._region_sheet_cache = {}
        cache_key = (sprite_name, frame_size, grid_rows, frame_h)
        if cache_key in self._region_sheet_cache:
            return self._region_sheet_cache[cache_key]

        import os

        path = os.path.join('assets', 'tilesets', 'animated_tiles', f'{sprite_name}.png')
        raw = None
        if os.path.isfile(path):
            try:
                raw = pygame.image.load(path).convert_alpha()
            except (pygame.error, OSError) as e:
                print(f'[animated_region] could not load {path}: {e}')

        if raw is None:
            print(f"[animated_region] sheet not found at {path} — using placeholder")
            raw = pygame.Surface((frame_size, frame_h * grid_rows), pygame.SRCALPHA)
            if 'water' in sprite_name:
                fallback_color = (0, 120, 255, 160)
            elif 'lava' in sprite_name:
                fallback_color = (255, 90, 20, 160)
            elif 'dirt' in sprite_name:
                fallback_color = (120, 80, 40, 160)
            else:
                fallback_color = (60, 170, 40, 160)
            raw.fill(fallback_color)

        num_cols = max(1, raw.get_width() // frame_size)
        raw_frames = [
            raw.subsurface((col * frame_size, row * frame_h, frame_size, frame_h))
            for row in range(grid_rows)
            for col in range(num_cols)
        ]

        if RENDER_SCALE != 1:
            size = (frame_size * RENDER_SCALE, frame_h * RENDER_SCALE)
            frames = [pygame.transform.scale(f, size) for f in raw_frames]
        else:
            frames = [f.copy() for f in raw_frames]

        self._region_sheet_cache[cache_key] = frames
        return frames

    def _get_scroll_tiled_frames(self, sprite_name: str, frame_size: int, color: tuple, frames: list,
                                  key_suffix: str = ''):
        """For patch-mode sprites with a continuous 'scroll', returns
        (cached) copies of `frames` tiled 2x2. A scrolling crop window's
        start position can land anywhere in [0, frame_size) rather than
        snapping to a chunk boundary, so start + chunk_size can spill past
        the plain frame's right/bottom edge. Tiling the frame into a
        (2*frame_size)x(2*frame_size) canvas first means that spill always
        lands on the tile's own repeated copy instead of empty space —
        no wraparound math needed at crop time. Cached per
        (sprite_name, frame_size, color) alongside the tint cache, since
        it's already scaled-and-tinted `frames` being tiled here."""
        if not hasattr(self, '_region_scroll_tile_cache'):
            self._region_scroll_tile_cache = {}
        key = (sprite_name, frame_size, color, key_suffix)
        cached = self._region_scroll_tile_cache.get(key)
        if cached is not None:
            return cached

        scaled_size = frame_size * RENDER_SCALE
        tiled = []
        for f in frames:
            canvas = pygame.Surface((scaled_size * 2, scaled_size * 2), pygame.SRCALPHA)
            canvas.blit(f, (0, 0))
            canvas.blit(f, (scaled_size, 0))
            canvas.blit(f, (0, scaled_size))
            canvas.blit(f, (scaled_size, scaled_size))
            tiled.append(canvas)

        self._region_scroll_tile_cache[key] = tiled
        return tiled

    def _load_region_plain_frame(self, sprite_name: str, frame_size: int):
        """Load (and cache) the 'no waves' variant of a patch-mode sheet —
        drawn for chunks the wave-amount slider has turned off.

        Preferred source: a hand-drawn
        assets/tilesets/animated_tiles/<sprite_name>_plain.png, same layout
        as the animated sheet's frame 0 but with no lines/dots. Draw one of
        these for a clean result.

        Fallback (used automatically if that file doesn't exist): takes
        frame 0 of the animated sheet and flattens any near-white pixel (the
        wave lines and foam dots) to a flat sampled base color. This is a
        threshold heuristic, not real art — anti-aliased edges around the
        original lines can leave faint fringing. Fine to preview with; swap
        in real art for a clean final look.
        """
        if not hasattr(self, '_region_plain_cache'):
            self._region_plain_cache = {}
        cache_key = (sprite_name, frame_size)
        if cache_key in self._region_plain_cache:
            return self._region_plain_cache[cache_key]

        import os

        folder = os.path.join('assets', 'tilesets', 'animated_tiles')
        override_path = os.path.join(folder, f'{sprite_name}_plain.png')
        # Water's sheet has a second highlight shade (90C8F8) baked in
        # alongside its wave-line whites — this needs flattening on the
        # plain frame no matter which source it came from, or wave_amount=0
        # still shows a ring of lighter blue where the highlight used to be.
        extra_colors = [(0x90, 0xC8, 0xF8)] if 'water' in sprite_name else None
        raw = None
        if os.path.isfile(override_path):
            try:
                raw = pygame.image.load(override_path).convert_alpha()
                if extra_colors:
                    # threshold=256 disables the near-white pass (nothing can
                    # exceed 255) — hand-drawn override art is assumed to
                    # already be clean of wave lines, so only the highlight
                    # shade gets flattened here.
                    raw = self._strip_near_white(raw, threshold=256, extra_colors=extra_colors)
            except (pygame.error, OSError) as e:
                print(f'[animated_region] could not load {override_path}: {e}')

        if raw is None:
            sheet_path = os.path.join(folder, f'{sprite_name}.png')
            source = None
            if os.path.isfile(sheet_path):
                try:
                    source = pygame.image.load(sheet_path).convert_alpha()
                except (pygame.error, OSError) as e:
                    print(f'[animated_region] could not load {sheet_path}: {e}')

            if source is None:
                raw = pygame.Surface((frame_size, frame_size), pygame.SRCALPHA)
                if 'lava' in sprite_name:
                    raw.fill((255, 90, 20, 160))
                else:
                    raw.fill((0, 120, 255, 160))
            else:
                frame0 = source.subsurface((0, 0, frame_size, frame_size)).copy()
                raw = self._strip_near_white(frame0, extra_colors=extra_colors)

        scaled = raw
        if RENDER_SCALE != 1:
            size = (frame_size * RENDER_SCALE, frame_size * RENDER_SCALE)
            scaled = pygame.transform.scale(raw, size)

        self._region_plain_cache[cache_key] = scaled
        return scaled

    @staticmethod
    def _strip_near_white(surface: 'pygame.Surface', threshold: int = 200,
                           extra_colors: list = None, extra_tolerance: int = 20) -> 'pygame.Surface':
        """Flattens near-white pixels (wave lines/foam dots) in `surface` to
        a flat color sampled from the rest of the image. See
        _load_region_plain_frame for the fringing caveat — this is a
        stand-in for real art, not a substitute.

        `extra_colors` are additional exact-ish RGB shades to flatten
        alongside near-white — e.g. water's 90C8F8 highlight, which isn't
        white enough to trip the near-white threshold on its own but should
        disappear the same way in the 'no waves' plain frame. A pixel
        within `extra_tolerance` (per-channel max difference) of any listed
        color is treated the same as near-white."""
        import numpy as np

        rgb = pygame.surfarray.array3d(surface)
        alpha = pygame.surfarray.array_alpha(surface)

        is_white = (rgb[:, :, 0] > threshold) & (rgb[:, :, 1] > threshold) & (rgb[:, :, 2] > threshold)

        is_extra = np.zeros(is_white.shape, dtype=bool)
        if extra_colors:
            rgb_int = rgb.astype(np.int16)
            for color in extra_colors:
                diff = np.abs(rgb_int - np.array(color, dtype=np.int16))
                is_extra |= diff.max(axis=2) <= extra_tolerance

        to_flatten = is_white | is_extra
        opaque = alpha > 10
        keep_opaque = opaque & ~to_flatten

        if keep_opaque.any():
            base_color = rgb[keep_opaque].mean(axis=0).astype(np.uint8)
        else:
            base_color = np.array([0, 120, 255], dtype=np.uint8)

        result = surface.copy()
        result_rgb = pygame.surfarray.pixels3d(result)
        mask = to_flatten & opaque
        result_rgb[mask] = base_color
        del result_rgb  # release the surface lock
        return result

    def _get_tinted_frames(self, sprite_name: str, frame_size: int, color: tuple, frames: list, key_suffix: str = '',
                            white_threshold: int = 200):
        """Return (cached) tinted copies of `frames` for a given color.

        Each non-white, opaque pixel keeps the *shape* of its own HSL
        lightness relative to the sprite's other pixels (so shading/
        highlight patterns like the water base vs. its 90C8F8 highlight
        stay visually distinct) but that lightness is scaled by how light
        or dark the picked `color` itself is, so picking a dark color
        actually darkens the result instead of just changing hue/
        saturation while silently keeping the art's original brightness.
        Hue and saturation are replaced with `color`'s outright — the same
        technique GIMP's "Colorize" tool uses, extended with this
        lightness scaling. Lightness (max+min)/2 is used rather than HSV
        "value" (just max(R,G,B)): this water art uses both 58A8F8 (base)
        and 90C8F8 (a lighter highlight shade), and those two colors share
        the same peak blue channel (248), so preserving `max` alone made
        them collapse onto the identical output color once hue/saturation
        got replaced — the highlight silently disappeared into the base
        after tinting. Lightness differs between them (0.66 vs 0.77), so it
        keeps that highlight detail visible under any tint color.

        This is also deliberately NOT a channel multiply — a multiply only
        behaves like a clean "colorize" when the base art is grayscale, and
        this water art's base color is itself a saturated blue, so
        multiplying it by another hue would just multiply two colors
        together (e.g. picking red would zero out the G/B channels of an
        already-blue pixel, giving a dark muddy result instead of clean red
        water).

        Near-white pixels (foam / wave-line highlights) are left completely
        untouched, so picking e.g. red only shifts the blueish body of the
        water and doesn't wash the white foam into the tint color too.
        Cached per (sprite, frame_size, color) since the same combo repeats
        across every chunk of every region using it.
        """
        if not hasattr(self, '_region_tint_cache'):
            self._region_tint_cache = {}
        key = (sprite_name, frame_size, color, key_suffix)
        cached = self._region_tint_cache.get(key)
        if cached is not None:
            return cached

        import colorsys
        import numpy as np

        hue, target_lightness, sat = colorsys.rgb_to_hls(color[0] / 255.0, color[1] / 255.0, color[2] / 255.0)
        # 0.5 lightness is "neutral" (a fully-saturated pure hue with no
        # tint-driven brightening/darkening) — scale each pixel's own
        # lightness relative to that midpoint so picking a near-black or
        # near-white swatch pulls the whole sprite darker/lighter, while a
        # mid-lightness pick like a pure hue reproduces the old
        # preserve-original-lightness behavior (scale == 1).
        lightness_scale = target_lightness / 0.5

        def _hls_component(m1, m2, h):
            """Vectorized version of colorsys._v — m1/m2 are per-pixel
            arrays, h is a fixed scalar hue offset."""
            h = h % 1.0
            if h < 1 / 6:
                return m1 + (m2 - m1) * h * 6
            elif h < 0.5:
                return m2
            elif h < 2 / 3:
                return m1 + (m2 - m1) * (2 / 3 - h) * 6
            return m1

        tinted = []
        for f in frames:
            t = f.copy()
            rgb = pygame.surfarray.pixels3d(t)
            alpha = pygame.surfarray.array_alpha(t)

            is_white = (rgb[:, :, 0] > white_threshold) & (rgb[:, :, 1] > white_threshold) & (rgb[:, :, 2] > white_threshold)
            opaque = alpha > 10
            recolor_mask = opaque & ~is_white

            if recolor_mask.any():
                px = rgb[recolor_mask].astype(np.float32) / 255.0
                lightness = (px.max(axis=-1) + px.min(axis=-1)) / 2  # each pixel's own lightness...
                lightness = np.clip(lightness * lightness_scale, 0.0, 1.0)  # ...scaled toward the picked color's

                if sat == 0.0:
                    recolored = np.stack([lightness, lightness, lightness], axis=-1)
                else:
                    m2 = np.where(lightness <= 0.5, lightness * (1 + sat), lightness + sat - lightness * sat)
                    m1 = 2 * lightness - m2
                    r = _hls_component(m1, m2, hue + 1 / 3)
                    g = _hls_component(m1, m2, hue)
                    b = _hls_component(m1, m2, hue - 1 / 3)
                    recolored = np.stack([r, g, b], axis=-1)

                recolored = np.clip(recolored * 255, 0, 255).astype(np.uint8)
                rgb[recolor_mask] = recolored

            del rgb  # release the surface lock
            tinted.append(t)

        self._region_tint_cache[key] = tinted
        return tinted

    @staticmethod
    def _region_chunk_roll(chunk_col: int, chunk_row: int, seed: int) -> float:
        """Deterministic pseudo-random value in [0, 100) for a tile grid
        position + seed. Same seed always reproduces the same wave layout;
        changing the seed reshuffles which chunks show waves."""
        n = (chunk_col * 374761393 + chunk_row * 668265263 + seed * 2246822519) & 0xFFFFFFFF
        n = (n ^ (n >> 13)) * 1274126177 & 0xFFFFFFFF
        n = n ^ (n >> 16)
        return (n & 0xFFFFFFFF) / 0xFFFFFFFF * 100

    def _draw_animated_regions_overlay(self, target_surface: 'pygame.Surface', room_name: str,
                                        camera_x: int, camera_y: int):
        """Fill every placed water/grass/lava AnimatedRegion algorithmically —
        the box-and-controller pattern, rather than placing individual tiles
        by hand. One live playback mode, picked per region_type via
        REGION_STYLES:

        'patch' (water, lava, grass): each frame is a frame_size x frame_size
        image containing a grid of chunk_size patches (water/lava: 64x64
        frame, 8x8 patches of 8px each; grass: 64x64 frame, 4x4 patches of
        16px each). The runtime crawls the region in chunk_size world-space
        steps and, for each step, crops the patch at
        (world_x % frame_size, world_y % frame_size) out of whichever frame
        is currently playing (frame advances on a shared clock, so the whole
        region plays in sync). Which frame plays next is picked by the
        style's 'anim' setting — water/grass loop straight through their
        strip, lava ping-pongs back and forth for a 1-2-3-2-1-2-3 flicker.
        Separately, the style's 'scroll' setting can continuously slide the
        sample window every chunk crops from, in fractional pixels per
        second — lava uses this to flow down-right, independent of the
        frame flicker. (A chunk-quantized jump instead of a continuous
        offset would just swap in an unrelated patch each step and read as
        extra flicker rather than motion, so this has to stay
        sub-chunk-smooth.)

        'tile' (dirt): no sub-patches, no time-based playback. Each of the
        sheet's grid_rows frames is one complete, static frame_w x frame_h
        tile (dirt: 24x24, 4 variants stacked vertically). A placed
        region's 'variant' index picks which single frame it shows —
        editor-selectable per region, defaulting to 0 — and the runtime
        just tiles that one frame edge-to-edge, snapped to a global
        frame_w x frame_h grid so neighboring dirt regions still line up
        at their shared border.

        'checkerboard' — RETIRED. Grass used to use this (each frame IS the
        whole tile, no sub-patch cropping); the branch is kept commented
        out below in case it's needed again, alongside the retired
        REGION_STYLES entry in animated_region.py.

        Only chunks/tiles intersecting the camera view (plus padding, so
        nothing pops in at the screen border) are drawn either way, so cost
        is proportional to what's on screen, not to how large the region is.
        """
        room = self.room_manager.get_room_by_name(room_name)
        if not room:
            return

        regions = getattr(room, 'animated_regions', None)
        if not regions:
            return

        from objects.animated_region import REGION_STYLES

        PAD = 64  # extra world-px past the camera edge so chunks don't pop in
        default_fps = 6   # matches the default fps used by the tileset's own animated tiles
        tick_ms = pygame.time.get_ticks()
        sw, sh = target_surface.get_size()

        # Visible bounds (+ padding) in the same unscaled world/tile-unit
        # space region.x/.y live in.
        view_left   = (camera_x / RENDER_SCALE) - PAD
        view_top    = (camera_y / RENDER_SCALE) - PAD
        view_right  = ((camera_x + sw) / RENDER_SCALE) + PAD
        view_bottom = ((camera_y + sh) / RENDER_SCALE) + PAD

        for region in regions:
            if not getattr(region, 'active', True):
                continue

            style = REGION_STYLES.get(region.region_type, {})
            sprite_name = style.get('sheet', region.region_type)
            mode = style.get('mode', 'patch')
            grid_rows = max(1, style.get('grid_rows', 1))

            if mode == 'tile':
                # Non-square, non-animated sheet (dirt): frame_w/frame_h
                # instead of a single square frame_size, and no chunk_size
                # sub-patch cropping at all.
                frame_w = style.get('frame_w', style.get('frame_size', 64))
                frame_h = style.get('frame_h', frame_w)
                frames = self._load_region_sheet(sprite_name, frame_w, grid_rows, frame_h=frame_h)
                frame_size_key = (frame_w, frame_h)  # cache-key stand-in for tint/opacity helpers below
            else:
                frame_size = style.get('frame_size', 64)
                chunk_size = style.get('chunk_size', 8)
                frames = self._load_region_sheet(sprite_name, frame_size, grid_rows)
                frame_size_key = frame_size

            if not frames:
                continue

            # Wave Amount is a water-only control now — lava and grass
            # always play the full animation regardless of any stored
            # value (e.g. from before this was restricted to water).
            wave_amount = getattr(region, 'wave_amount', 100) if region.region_type == 'water' else 100
            seed = getattr(region, 'seed', 0)
            plain_frame = None
            if mode == 'patch' and wave_amount < 100:
                plain_frame = self._load_region_plain_frame(sprite_name, frame_size)

            # (255, 255, 255) = original art colors, untouched. Anything else
            # replaces the hue/saturation of the sprite's base (non-white)
            # pixels with that color while keeping each pixel's own HSL
            # lightness (so shading/highlights — including subtly lighter
            # shades like 90C8F8 alongside the 58A8F8 base — stay visually
            # distinct after tinting), leaving white foam/highlight pixels
            # alone, via cached tinted copies so the same color/sprite combo
            # is only computed once.
            color = tuple(getattr(region, 'color', (255, 255, 255)))
            if color != (255, 255, 255):
                frames = self._get_tinted_frames(sprite_name, frame_size_key, color, frames)
                if plain_frame is not None:
                    plain_frame = self._get_tinted_frames(
                        sprite_name, frame_size_key, color, [plain_frame], key_suffix='_plain'
                    )[0]

            # 0-100 editor slider -> 0-255 surface alpha. Set on every frame
            # this region might use (cheap — at most a handful), since the
            # same cached frame objects are shared across regions/instances
            # and each region can have its own opacity.
            alpha_value = max(0, min(255, round(getattr(region, 'opacity', 100) / 100 * 255)))
            for f in frames:
                f.set_alpha(alpha_value)
            if plain_frame is not None:
                plain_frame.set_alpha(alpha_value)

            # Clip the region to the camera view before chunking it.
            rx0 = max(region.x, view_left)
            ry0 = max(region.y, view_top)
            rx1 = min(region.x + region.width,  view_right)
            ry1 = min(region.y + region.height, view_bottom)
            if rx0 >= rx1 or ry0 >= ry1:
                continue

            if mode == 'patch':
                # Snap the starting corner down to the nearest chunk boundary.
                chunk_x0 = int(rx0 // chunk_size) * chunk_size
                chunk_y0 = int(ry0 // chunk_size) * chunk_size

                scaled_chunk = chunk_size * RENDER_SCALE

                anim = style.get('anim', 'loop')
                num_frames = len(frames)
                fps = style.get('fps', default_fps)
                step = int(tick_ms * fps / 1000)
                if anim == 'pingpong' and num_frames > 1:
                    # Bounces forward then back across the strip instead of
                    # cycling straight through: 0,1,...,N-1,N-2,...,1,0,1,...
                    period = 2 * (num_frames - 1)
                    pos = step % period
                    frame_idx = pos if pos < num_frames else period - pos
                else:
                    frame_idx = step % num_frames

                scroll_px_x, scroll_px_y = style.get('scroll', (0, 0))
                scrolling = scroll_px_x != 0 or scroll_px_y != 0

                if scrolling:
                    # Continuous sub-chunk offset (unscaled world pixels),
                    # decoupled from the frame_idx flicker above so the two
                    # motions don't compound into "just faster flicker".
                    t = tick_ms / 1000.0
                    # Sampling further into the texture makes the displayed
                    # content appear to move the opposite way (advancing
                    # the sample point right makes the pattern drift left
                    # on screen), so subtract to get apparent motion in the
                    # (positive x, positive y) = down-right direction that
                    # positive scroll_px values are meant to represent.
                    offset_x = (-(t * scroll_px_x)) % frame_size
                    offset_y = (-(t * scroll_px_y)) % frame_size
                    frame = self._get_scroll_tiled_frames(sprite_name, frame_size, color, frames)[frame_idx]
                    if plain_frame is not None:
                        plain_frame = self._get_scroll_tiled_frames(
                            sprite_name, frame_size, color, [plain_frame], key_suffix='_plain'
                        )[0]
                else:
                    offset_x = offset_y = 0
                    frame = frames[frame_idx]

                cy = chunk_y0
                while cy < ry1:
                    src_y = ((cy % frame_size) + offset_y) * RENDER_SCALE
                    row = cy // chunk_size
                    cx = chunk_x0
                    while cx < rx1:
                        col = cx // chunk_size

                        if wave_amount >= 100:
                            source = frame
                        elif wave_amount <= 0:
                            source = plain_frame
                        else:
                            roll = self._region_chunk_roll(col, row, seed)
                            source = frame if roll < wave_amount else plain_frame

                        src_x = int(((cx % frame_size) + offset_x) * RENDER_SCALE)
                        screen_x = (cx * RENDER_SCALE) - camera_x
                        screen_y = (cy * RENDER_SCALE) - camera_y
                        target_surface.blit(
                            source, (int(screen_x), int(screen_y)),
                            (src_x, int(src_y), scaled_chunk, scaled_chunk)
                        )
                        cx += chunk_size
                    cy += chunk_size

            elif mode == 'tile':
                # No sub-patches, no time-based playback — this region shows
                # exactly one static frame, fixed by its own 'variant' index
                # (editor-selectable, default 0 = the sheet's first row).
                variant_idx = max(0, min(len(frames) - 1, getattr(region, 'variant', 0)))
                frame = frames[variant_idx]

                # Snap to a *global* frame_w x frame_h grid (not relative to
                # this region's x/y) so two adjacent dirt regions still tile
                # seamlessly across their shared border, same idea as the
                # 'patch' branch snapping chunk_x0/chunk_y0 to world space.
                tile_x0 = int(rx0 // frame_w) * frame_w
                tile_y0 = int(ry0 // frame_h) * frame_h

                # Unlike 'patch' chunks (a handful of px, so any overhang
                # past the region edge is invisible), a 24x24 dirt tile is
                # big enough that letting it hang past the box would be
                # obviously wrong — so edge tiles get cropped to rx0/ry0/
                # rx1/ry1 (already the region-bounds-intersect-camera-view
                # rect computed above) rather than blit in full.
                cy = tile_y0
                while cy < ry1:
                    tile_top = max(cy, ry0)
                    tile_bottom = min(cy + frame_h, ry1)
                    if tile_bottom > tile_top:
                        src_y = int((tile_top - cy) * RENDER_SCALE)
                        src_h = int((tile_bottom - tile_top) * RENDER_SCALE)
                        screen_y = (tile_top * RENDER_SCALE) - camera_y

                        cx = tile_x0
                        while cx < rx1:
                            tile_left = max(cx, rx0)
                            tile_right = min(cx + frame_w, rx1)
                            if tile_right > tile_left:
                                src_x = int((tile_left - cx) * RENDER_SCALE)
                                src_w = int((tile_right - tile_left) * RENDER_SCALE)
                                screen_x = (tile_left * RENDER_SCALE) - camera_x
                                target_surface.blit(
                                    frame, (int(screen_x), int(screen_y)),
                                    (src_x, src_y, src_w, src_h)
                                )
                            cx += frame_w
                    cy += frame_h

            # else:  # 'checkerboard' — RETIRED. Grass now uses 'patch' mode
            # like water/lava (see REGION_STYLES above); kept here commented
            # out in case the checkerboard approach is needed again.
            #
            #     num_frames = len(frames)
            #     frames_per_batch = max(1, style.get('frames_per_batch', 4))
            #     batch_swap_ms = max(1, style.get('batch_swap_ms', 1500))
            #     cols_per_row = max(1, num_frames // grid_rows)  # 2 * frames_per_batch, normally
            #
            #     # Which batch row of the sheet is active is a single global
            #     # choice for the whole region — every batch_swap_ms it flips
            #     # for every tile at once, so the whole field sways together
            #     # instead of each tile/row flickering independently.
            #     active_batch = int(tick_ms // batch_swap_ms) % grid_rows
            #     batch_row_offset = active_batch * cols_per_row
            #
            #     cy = chunk_y0
            #     while cy < ry1:
            #         row = cy // chunk_size
            #         # Within the active batch's row, tile-row position picks
            #         # one of two frames_per_batch-wide column groups baked
            #         # side by side into that row (group 0: cols
            #         # 0..frames_per_batch-1, group 1: cols
            #         # frames_per_batch..2*frames_per_batch-1). Mirrored
            #         # rather than straight alternation — 1,2,2,1,1,2,2,1...
            #         # — so a 4-row cycle where rows 0 & 3 use group 0 and
            #         # rows 1 & 2 use group 1.
            #         row_in_cycle = row % 4
            #         group_idx = 1 if row_in_cycle in (1, 2) else 0
            #         row_group_offset = frames_per_batch * group_idx
            #
            #         cx = chunk_x0
            #         while cx < rx1:
            #             col = cx // chunk_size
            #             col_frame = col % frames_per_batch  # cycles across columns, left to right
            #             frame_idx = (batch_row_offset + row_group_offset + col_frame) % num_frames
            #             frame = frames[frame_idx]
            #
            #             screen_x = (cx * RENDER_SCALE) - camera_x
            #             screen_y = (cy * RENDER_SCALE) - camera_y
            #             target_surface.blit(frame, (int(screen_x), int(screen_y)))
            #
            #             cx += chunk_size
            #         cy += chunk_size

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

        # Animated regions (layer -100) are meant to be the floor everything
        # else sits on. The baked "bg" surface bundles EVERY negative tile
        # layer (-100 through -1) into a single blit — there's no per-layer
        # separation inside it — so the region has to be drawn BEFORE that
        # surface. Drawing it after (the old order) put it on top of the
        # whole bg bucket regardless of a tile's individual layer value,
        # which is why -100/-99 tiles were still ending up underneath it.
        if bg:
            self._draw_animated_regions_overlay(
                self.logical_surface, room_name, int(self.camera.x), int(self.camera.y)
            )

        self.logical_surface.blit(surf, (-int(self.camera.x), -int(self.camera.y)))
        self._draw_animated_tile_overlay(
            self.logical_surface, room_name, bg, int(self.camera.x), int(self.camera.y)
        )

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
            # See _draw_room_tiles for why this has to go before the bg blit:
            # the baked surface bundles every negative tile layer together,
            # so the region overlay must sit underneath it, not on top.
            if bg:
                self._draw_animated_regions_overlay(screen, room_name, camera_x, camera_y)
            screen.blit(surf, (-camera_x, -camera_y))
            self._draw_animated_tile_overlay(screen, room_name, bg, camera_x, camera_y)

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
        foreground tile pixel — or any decoration (tree, etc.) currently
        drawn in front of the player by Y-sort — overlaps the player. If so,
        blit a pixel-accurate dark ghost so the player stays readable
        through walls/fences/trees/canopies.

        Tile and decoration surfaces are retrieved and scaled here, then
        forwarded to draw_player_silhouette which builds an occlusion mask
        from their opaque pixels. Transparent borders are excluded, so the
        ghost only appears where something genuinely solid sits on top of
        the player sprite.
        """
        if self.active_cutscene_runtime:
            return

        fg_tiles = self._get_foreground_tiles()

        # Build the player's screen-space bounding rect.
        pw = int(self.player.width  * RENDER_SCALE)
        ph = int(self.player.height * RENDER_SCALE)
        px = int(self.player.x * RENDER_SCALE - self.camera.x)
        py = int(self.player.y * RENDER_SCALE - self.camera.y)
        player_rect = pygame.Rect(px - pw // 2, py - ph // 2, pw, ph)

        te = getattr(self.room_editor, 'tileset_editor', None)

        # Collect every tile/decoration whose bounding rect overlaps the
        # player, together with its scaled surface. draw_player_silhouette
        # will then mask the silhouette to only the pixels those surfaces
        # actually cover.
        overlapping: list = []   # [(scaled_surface | None, screen_x, screen_y, cache_key)]

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

        # Decorations (trees, etc.) — unlike painted foreground tiles, these
        # are Y-sorted against the player (see Decoration's class docstring),
        # so they don't ALWAYS draw in front. Only fold one in here when
        # this frame's Y-sort actually put it in front of the player —
        # matching LayerManager's own (layer, y) compare — otherwise a tree
        # the player is standing in front of (visually correct, no occlusion
        # needed) would incorrectly ghost the player out.
        for decoration in self.decorations:
            if not decoration.active:
                continue
            if decoration.get_sort_key() < self.player.get_sort_key():
                continue  # drawn behind the player this frame — nothing to occlude

            scaled, dx, dy = decoration.get_render_info(self.camera, RENDER_SCALE)
            if not player_rect.colliderect(pygame.Rect(dx, dy, scaled.get_width(), scaled.get_height())):
                continue

            cache_key = ('decoration', decoration.decoration_type, decoration.variant,
                         decoration.current_frame_index(), RENDER_SCALE)
            overlapping.append((scaled, dx, dy, cache_key))

        if overlapping:
            self.layer_manager.draw_player_silhouette(
                self.logical_surface, self.player, self.camera,
                fg_tile_surfaces=overlapping,
            )

    def _draw_ui(self, dt):
        """Draw all UI elements that appear on top of the game world."""
        # Screen-covering fades are drawn first so they sit *behind* the
        # dialogue box, HUD, and menus below. Drawing them last (as before)
        # meant a room-transition wipe or the cutscene launch/end black fade
        # would paint over an open dialogue box and hide it entirely.
        self.transition_controller.draw(self.logical_surface)
        self._draw_cutscene_fade(self.logical_surface)

        self.npc_config_menu.draw(self.logical_surface, self.colors)
        self.dialogue_box.draw(self.logical_surface, self.colors)
        self.save_point_menu.draw(self.logical_surface)
        self.dialogue_choice_menu.draw(self.logical_surface)
        self.character_switch_menu.draw(self.logical_surface)
        self.pause_menu.draw(self.logical_surface, self.player, self.play_time)
        self.credits_screen.draw(self.logical_surface)
        self.scouter_menu.draw(
            self.logical_surface, self.current_room,
            getattr(self, '_active_world_map_name', ''),
            getattr(self, '_wm_locations', []),
            self._build_world_map_scouter_surface,
            self.room_manager,
            player=self.player,
            world_map_lookup=self._world_map_lookup,
        )
        if not self.scouter_menu.active:
            self.level_up_notification.draw(self.logical_surface, self.colors, sprite_hud=self.sprite_hud, player=self.player)

        self.transition_config_menu.draw(self.logical_surface)

        # HUD slide animation — slides in/out when entering/leaving game mode.
        # Suppressed entirely during the world-map flying/landing sequence so the
        # HUD is never visible while the player is descending.  It is triggered to
        # slide back in by _update_map_flying once the fade has fully cleared.
        _in_map_sequence = self._mjf_state in (
            'pending_fade_in', 'fade_in', 'flying',
            'landing_fade_out', 'landing_fade_in',
        )
        if self.ui.current_screen == 'game' and not self.character_switch_menu.active \
                and not self.pause_menu.active and not self.scouter_menu.active and not _in_map_sequence:
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

        # Event timer readout (timer_start/pause/stop actions) — top-right,
        # drawn whenever a timer is active regardless of HUD slide state.
        if self.ui.current_screen == 'game' and not _in_map_sequence:
            self._draw_event_timer(self.logical_surface)

        # Spam QTE bar (bottom-middle mash-E-or-Q, 'spam_qte' action) — same
        # slide-independent treatment as the event timer above.
        if self.ui.current_screen == 'game' and not _in_map_sequence:
            self.spam_qte_bar.draw(self.logical_surface, render_scale=RENDER_SCALE)

        # Full-screen overlays for main menu and sub-screens.
        if self.ui.current_screen == 'main_menu':
            self.ui.draw_main_menu(self.logical_surface, self.colors)
        elif self.ui.current_screen == 'status':
            self.ui.draw_status_screen(self.logical_surface, self.player, self.game_config, self.colors)
        elif self.ui.current_screen == 'inventory':
            self.ui.draw_inventory_screen(self.logical_surface, self.player, self.colors)
        elif self.ui.current_screen == 'options':
            self.ui.draw_options_screen(self.logical_surface, self.colors)

        # Instant Transmission targeting overlay — enemy markers + the
        # cursor itself, drawn on top of the HUD like the flashes below.
        if self.player.is_targeting_it and self.it_selector is not None:
            self.it_selector.draw_markers(self.logical_surface, self.camera)
            self.it_selector.draw_cursor(self.logical_surface)

        # Genkidama impact flash — drawn on top of the HUD like the map-jump
        # fade below, just not as the very last thing (map-jump fade should
        # still win out if both ever happen at once).
        self._draw_white_flash(self.logical_surface)

        # Map-jump fade drawn dead last so it covers every UI element including
        # the HUD — otherwise the HUD renders on top and the fade looks incomplete.
        self._draw_map_jump_fade(self.logical_surface)

        # Death fade drawn absolute last — this needs to cover everything
        # above, including the map-jump fade and the HUD, since dying can in
        # principle happen while other overlays are still visually settling.
        # The "You have died!" box (the same DialogueBox draw used for
        # level-up notices, drawn earlier above at its normal spot in the
        # overlay stack) gets redrawn here too, on top of the fade, so it
        # reads on top of solid black instead of being buried under it.
        self._draw_death_fade(self.logical_surface)
        if self._death_state == 'box':
            self.dialogue_box.draw(self.logical_surface, self.colors)

        # In-game "Save Game" flow — TitleScreen's own SAVE SELECT frame
        # (re-labelled and locked down, see TitleScreen.open_save_overlay)
        # plus the "Saving..." popup in front of it. Drawn dead last, on
        # top of literally everything else in this method (including the
        # death fade), same as the real title screen covers the whole
        # window when it's up.
        if self.save_flow_active:
            # Hand TitleScreen the popup's rect *before* it draws, so its
            # save-slot divider bar (_draw_save_slot_divider) can clip
            # itself around exactly where the popup is about to land —
            # see _saving_popup_rect/set_save_popup_occlusion_rect.
            self.title_screen.set_save_popup_occlusion_rect(self._saving_popup_rect())
            self.title_screen.draw(self.logical_surface)
            self._draw_saving_popup(self.logical_surface)

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
        door_manager       = oe.door_manager
        flying_pad_manager = oe.flying_pad_manager
        nimbus_cloud_manager = oe.nimbus_cloud_manager

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

            if not hasattr(room, 'nimbus_clouds'):
                room.nimbus_clouds = []
            nimbus_cloud_manager.nimbus_clouds[room.name] = room.nimbus_clouds

            if not hasattr(room, 'room_transitions'):
                room.room_transitions = []
            transition_manager.transitions[room.name] = room.room_transitions

            if not hasattr(room, 'level_gates'):
                room.level_gates = []
            gate_manager.gates[room.name] = room.level_gates

            if not hasattr(room, 'doors'):
                room.doors = []
            door_manager.doors[room.name] = room.doors

            if not hasattr(room, 'save_points'):
                room.save_points = []
            self.save_point_manager.save_points[room.name] = room.save_points

            if not hasattr(room, 'music_track'):
                room.music_track = ''

    # ── Player death sequence ────────────────────────────────────────────────

    def _update_death_sequence(self, dt):
        """Advance the post-death overlay: hold on the last frame of
        death.png, fade the screen to black, then show the "You have died!"
        box. Deliberately lightweight — everything else in the world (enemy
        AI, projectiles, damage numbers, hurt-tint decay, camera) keeps
        running through the normal update() flow around this call; only the
        player is frozen, via is_dead (see Player.update()). No-ops on any
        frame there's nothing to do. State machine:
            'anim' — waiting for death.png to finish, then holding on its
                     last frame for _DEATH_HOLD_DURATION seconds.
            'fade' — ramping the black overlay from 0 to full alpha over
                     _DEATH_FADE_DURATION seconds.
            'box'  — fully black; the "You have died!" box (the same
                     DialogueBox used for level-up notices, centered via
                     is_narrator=True) is up, waiting for E — see
                     _advance_death_box/_close_death_box.
        """
        if not self.player.is_dead and self._death_state is None:
            return  # Nothing to do — the common case, every normal frame.

        if self._death_state is None:
            self._death_state      = 'anim'
            self._death_hold_timer = 0.0

        if self._death_state == 'anim':
            if self.player.sprite.is_animation_finished():
                self._death_hold_timer += dt
                if self._death_hold_timer >= self._DEATH_HOLD_DURATION:
                    self._death_state = 'fade'

        elif self._death_state == 'fade':
            self._death_fade_alpha = min(
                255.0, self._death_fade_alpha + self._death_fade_speed * dt)
            if self._death_fade_alpha >= 255.0:
                self._death_fade_alpha = 255.0
                self._death_state      = 'box'
                # Same box the level-up sequence uses (DialogueBox with no
                # portrait, on_close callback) — is_narrator=True is what
                # centers it in the screen instead of anchoring to the
                # bottom like normal NPC/info lines.
                self.dialogue_box.show(
                    "You have died!", "", True, None,
                    on_close=self._close_death_box, is_narrator=True,
                )

        # 'box' has nothing to tick here — dialogue_box.update(dt), already
        # called every frame in the normal gameplay update below, drives its
        # typewriter/close animation; _advance_death_box handles E.

    def _advance_death_box(self):
        """E pressed while the "You have died!" box is up.

        Same two-stage convention as _advance_npc_dialogue: the first press
        snaps the typewriter to fully visible; the next starts the box's
        close animation, which fires _close_death_box via the on_close
        callback once it's fully closed (see dialogue_box.show() above).
        """
        if self.dialogue_box._chars_shown < len(self.dialogue_box.current_text):
            self.dialogue_box._chars_shown = len(self.dialogue_box.current_text)
            return
        self.dialogue_box.hide()

    def _close_death_box(self):
        """Fired once the "You have died!" box has finished closing.

        Resets the death sequence and heals the player back up so nothing
        re-triggers it immediately, then — since there's no main menu yet —
        drops back into editing whatever room was being tested, exactly like
        pressing F2 to exit test mode manually (see the F2 handler in
        _handle_game_keydown). Falls back to a plain in-room respawn if the
        player somehow died outside of test mode.
        """
        self._death_state      = None
        self._death_hold_timer = 0.0
        self._death_fade_alpha = 0.0

        self.player.is_dead = False
        self.player.hp      = self.player.max_hp
        self.player.enter_idle()

        if self.is_test_mode:
            self._exit_test_mode()
            self.room_editor.active       = True
            self.room_editor.current_view = 'view_room'
        else:
            # No test session running — shouldn't normally happen without a
            # main menu, but respawn at the room's spawn point rather than
            # leaving the player stuck wherever they died.
            room = self.current_room
            if room:
                if hasattr(room, 'spawn_points') and room.spawn_points:
                    spawn_pos = (room.spawn_points[0].x, room.spawn_points[0].y)
                elif getattr(room, 'spawn_point', None):
                    spawn_pos = room.spawn_point
                else:
                    spawn_pos = (self.player.x, self.player.y)
                self.player.x, self.player.y = spawn_pos

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