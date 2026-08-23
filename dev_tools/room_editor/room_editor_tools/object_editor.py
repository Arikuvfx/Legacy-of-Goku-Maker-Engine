import os
import colorsys

import numpy as np
import pygame
import pygame.gfxdraw

from config.settings import RENDER_SCALE, TILE_SIZE, WORLD_WIDTH, WORLD_HEIGHT
from objects.spawn_object import SpawnObject, SpawnObjectManager
from objects.collision_object import CollisionObject, CollisionObjectManager, draw_collision_object
from objects.animated_region import AnimatedRegion, AnimatedRegionManager, draw_animated_region, REGION_STYLES
from objects.level_gate import LevelGate, LevelGateManager
from objects.room_transition import RoomTransition, RoomTransitionManager, TransitionConfigDialog
from objects.flying_pad import FlyingPad, FlyingPadManager
from objects.nimbus_cloud import NimbusCloud, NimbusCloudManager
from objects.save_point import SavePoint, SavePointManager
from objects.world_map import WorldMapObject, WorldMapObjectManager
from objects.door_object import Door, DoorManager
from objects.chest_object import Chest, ChestManager
from objects.decoration_objects import Decoration, DECORATION_STYLES
from core.items import get_item
from objects.trigger_box import (OverlapTriggerBox, KeyTriggerBox, TriggerBoxManager,
                                 draw_trigger_box)
from dev_tools.room_editor.room_editor_tools.flying_pad_path_editor import FlyingPadPathEditor
from dev_tools.room_editor.room_editor_tools.nimbus_cloud_path_editor import NimbusCloudPathEditor
from core.event_editor import EventEditorWindow


class ObjectEditor:
    """Editor for placing game objects like spawn points, collision walls, and decorations"""

    def __init__(self, screen_width, screen_height, room_manager=None):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False
        self.room_manager = room_manager

        self.font_small = pygame.font.Font(None, 16)
        self.font_medium = pygame.font.Font(None, 20)
        self.font_large = pygame.font.Font(None, 24)

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
            'delete_hover': (255, 100, 100),
            'input_bg': (60, 60, 75),
            'input_active': (80, 80, 100),
            'variant_bg': (25, 25, 40),
            'variant_selected': (50, 150, 255)
        }

        # ── Palette geometry ─────────────────────────────────────────────────
        self.palette_width = 600
        self.palette_x = screen_width - self.palette_width
        self.palette_y = 100
        self.palette_height = 940
        self.palette_padding = 10
        self.item_size = 80
        # How many item_size boxes (plus the 10px gap between them, same gap
        # used at draw time below) actually fit across the palette's usable
        # width. This used to be hardcoded to 3, which only filled ~270px of
        # the 600px-wide panel — every category's grid sat flush left with
        # roughly half the panel sitting empty. Deriving it from the real
        # geometry instead means the grid always uses the full width, and
        # it stays correct if palette_width/item_size ever change.
        item_gap = 10
        usable_width = self.palette_width - self.palette_padding * 2
        self.items_per_row = max(1, (usable_width + item_gap) // (self.item_size + item_gap))
        self.scroll_offset = 0
        self.max_scroll = 0

        # ── Object managers ───────────────────────────────────────────────────
        self.spawn_manager = SpawnObjectManager()
        self.collision_manager = CollisionObjectManager()
        self.animated_region_manager = AnimatedRegionManager()
        self.gate_manager = LevelGateManager()
        self.transition_manager = RoomTransitionManager()

        # Transition configuration dialog
        self.transition_config = TransitionConfigDialog(screen_width, screen_height)
        self.pending_transition = None

        # ── Placement tracking ────────────────────────────────────────────────
        self.placing_collision = False
        self.collision_start_x = 0
        self.collision_start_y = 0
        self.preview_collision = None

        self.placing_animated_region = False
        self.animated_region_start_x = 0
        self.animated_region_start_y = 0
        self.preview_animated_region = None
        self.current_region_type = 'water'  # set from the palette item when placement starts
        self.region_opacity = 100  # 0-100, applies to newly placed water/lava/grass regions
        self._region_opacity_dragging = False
        self.region_wave_amount = 100  # 0-100, fraction of chunks showing animated waves
        self._region_wave_dragging = False
        self.region_seed = 0
        self.region_seed_text = "0"
        self.region_seed_input_active = False
        self.region_color = (255, 255, 255)  # RGB tint; (255,255,255) = original art colors
        self._region_hue_dragging = False  # dragging the hue strip
        self._region_sv_dragging = False  # dragging the saturation/value square
        self.region_variant = 0  # 0-based index into the current tile-mode region's static variants
        self._hue_strip_cache = None  # (size, surface) — hue gradient never changes, computed once
        self._sv_square_cache = None  # (hue, size, surface) — recomputed only when hue changes
        self._region_variant_sprites_cache = {}  # region_type -> list of cropped variant frames, lazy-loaded once each

        self.on_animated_region_placed = None
        self.on_animated_region_deleted = None

        self.placing_transition = False
        self.transition_start_x = 0
        self.transition_start_y = 0
        self.preview_transition = None

        self.placing_transition_spawn = False
        self.transition_spawn_source_room = None
        self.pending_transition_for_spawn = None
        self.transition_spawn_preview_x = 0
        self.transition_spawn_preview_y = 0

        self.on_gate_deleted = None
        self.on_transition_deleted = None
        self.on_transition_placed = None
        self.on_stone_placed = None

        # ── Decorations (trees, etc.) ────────────────────────────────────────
        self.on_decoration_placed = None
        self.on_decoration_deleted = None

        # ── Flying pad ────────────────────────────────────────────────────────
        self.flying_pad_manager = FlyingPadManager()
        self.flying_pad_path_editor = FlyingPadPathEditor(screen_width, screen_height)
        self.placing_flying_pad = False
        self.pending_flying_pad = None
        self.on_flying_pad_placed = None
        self.on_flying_pad_deleted = None

        # ── Nimbus cloud ──────────────────────────────────────────────────────
        self.nimbus_cloud_manager = NimbusCloudManager()
        self.nimbus_cloud_path_editor = NimbusCloudPathEditor(screen_width, screen_height)
        self.placing_nimbus_cloud = False
        self.pending_nimbus_cloud = None
        self.on_nimbus_cloud_placed = None
        self.on_nimbus_cloud_deleted = None

        # ── Save points ───────────────────────────────────────────────────────
        self.save_point_manager = SavePointManager()
        self.on_save_point_placed = None
        self.on_save_point_deleted = None
        self.world_map_manager = WorldMapObjectManager()
        self.on_world_map_placed = None
        self.on_world_map_deleted = None

        # ── Doors ─────────────────────────────────────────────────────────────
        self.door_manager = DoorManager()
        self.on_door_placed = None
        self.on_door_deleted = None
        self.door_permanent = False  # editor toggle — applies to the next door placed
        # Which door SFX the next door placed will use, plus a preview button
        # so the player can hear it before committing. sound_manager is None
        # until set_sound_manager() is called (wired up by game.py) — the
        # preview button just no-ops silently until then.
        self.sound_manager = None
        _door_sounds = Door.list_door_sounds() or Door.DEFAULT_SOUND_NAMES
        self.door_sound_options = _door_sounds
        self.door_sound_text = _door_sounds[0]  # editor toggle — applies to the next door placed

        # ── Chests ────────────────────────────────────────────────────────────
        self.chest_manager = ChestManager()
        self.on_chest_placed = None
        self.on_chest_deleted = None
        # Fired when loot is assigned/stacked on an already-placed chest.
        # Separate from on_chest_placed so undo wiring does not treat loot
        # edits as brand-new object_add entries.
        self.on_chest_loot_changed = None

        # Loot is no longer picked at placement time. Every new chest starts
        # empty (item_id=''); loot is assigned afterward by selecting an
        # item in the toolbar's Items panel and clicking a placed chest —
        # see _try_assign_chest_loot, called from handle_input.

        # Requires a FlagManager (see set_flag_manager) to actually build the
        # conditions/actions popup; without one the "Edit Event" button stays
        # disabled.
        self.flag_manager = None
        self.event_editor = None
        # Character to scope the 'skill' action's add/remove pickers to —
        # may be set via set_current_character() before the event editor
        # exists (set_flag_manager creates it), hence "pending".
        self._pending_character_id = None
        self._pending_get_equipped_skills = None
        # Known rooms (for the change_map action's room picker/Set Spawn
        # preview) — may likewise be set via set_known_rooms() before the
        # event editor exists, hence "pending", same rationale as the
        # character fields above.
        self._pending_known_rooms = None
        self._pending_known_room_dims = None
        # Room tile-preview provider (for the Set Spawn overlay) — same
        # pending-until-the-event-editor-exists shape as the two above.
        self._pending_room_preview_provider = None

        # ── Trigger boxes ────────────────────────────────────────────────────
        # OverlapTriggerBox fires on overlap alone; KeyTriggerBox additionally
        # requires the interact key. `trigger_box_requires_key` picks which
        # class gets instantiated on placement — sticky like the trigger box
        # settings above, until changed.
        self.trigger_box_manager = TriggerBoxManager()
        self.on_trigger_box_placed = None
        self.on_trigger_box_deleted = None
        self.placing_trigger_box = False
        self.trigger_box_start_x = 0
        self.trigger_box_start_y = 0
        self.preview_trigger_box = None
        self.trigger_box_id_text = ""
        self.trigger_box_id_input_active = False
        self.trigger_box_once = True
        self.trigger_box_requires_key = False
        self.trigger_box_always_run = False

        # Conditions + actions attached to the next trigger box placed —
        # sticky like trigger_box_id_text/trigger_box_once above, until
        # changed. Requires a FlagManager (see set_flag_manager) to actually
        # build the popup; without one the "Edit Event" button stays disabled.
        self.trigger_box_conditions = []
        self.trigger_box_actions = []

        # ── World map selection ───────────────────────────────────────────────
        self.world_map_name_text = ""  # stem of the selected world map JSON
        self.world_map_dropdown_open = False  # whether the map-name dropdown is open
        self.world_map_dropdown_names = []  # cached list of world map stems

        self.hovered_object = None  # object under the cursor (for deletion highlight)
        self.hovered_object_type = None

        # ── Gate level input ──────────────────────────────────────────────────
        self.gate_required_level = 1
        self.gate_level_input_active = False
        self.gate_level_text = "1"

        # ── Variant selection ─────────────────────────────────────────────────
        self.selected_variant = None
        self.hover_variant_index = -1
        self.showing_variants_for = None
        self.variant_scroll = 0  # index of first visible variant, for long variant lists

        # ── Variant definitions ───────────────────────────────────────────────
        self.stone_variants = [
            {'type': 'small', 'name': 'Small', 'width': 16, 'height': 16, 'sprite': None},
            {'type': 'medium', 'name': 'Medium', 'width': 24, 'height': 24, 'sprite': None},
            {'type': 'big', 'name': 'Big', 'width': 32, 'height': 32, 'sprite': None}
        ]

        self.gate_variants = [
            {'type': 'stone', 'name': 'Stone', 'sprite': None},
            {'type': 'wood', 'name': 'Wood', 'sprite': None},
            {'type': 'makeshift wood', 'name': 'Makeshift', 'sprite': None},
            {'type': 'stone formation', 'name': 'Formation', 'sprite': None},
            {'type': 'metal', 'name': 'Metal', 'sprite': None}
        ]

        self.flying_pad_variants = [
            {'type': 'stone1', 'name': 'Stone1', 'sprite': None},
            {'type': 'stone2', 'name': 'Stone2', 'sprite': None},
            {'type': 'gras', 'name': 'Gras', 'sprite': None},
            {'type': 'cracked', 'name': 'Cracked', 'sprite': None},
            {'type': 'trunk', 'name': 'Trunk', 'sprite': None},
            {'type': 'kami', 'name': 'Kami', 'sprite': None},
            {'type': 'ice', 'name': 'Ice', 'sprite': None},
            {'type': 'buu', 'name': 'Buu', 'sprite': None}
        ]

        # Nimbus cloud has only a single sprite on disk (no per-type
        # variants like flying pads/chests/doors), so its palette icon is
        # loaded directly from a throwaway NimbusCloud instance below
        # instead of going through the variant system.
        self.nimbus_cloud_sprite = NimbusCloud(0, 0).sprite

        # Tree decoration's palette icon is its first animation frame. Like
        # nimbus_cloud above, it has no per-type variants to pick between
        # (yet — see DECORATION_STYLES['variants']), so it's loaded directly
        # here instead of going through the has_variants/variant-picker
        # system that gates/doors/chests use.
        self._tree_icon_sprite = Decoration(0, 0, 'tree').frames[0]

        # Door variants are discovered from assets/sprites/structures/door/ at
        # startup (one sheet per type — see Door.list_door_types()) rather
        # than hardcoded here, so dropping in a new sheet is enough to make
        # it show up in the picker. Falls back to a single 'wood' entry if
        # the folder is empty/missing so the editor still has something to
        # place (Door itself will fall back to a placeholder sprite for it).
        _door_types = Door.list_door_types() or ['wood']
        self.door_variants = [
            {'type': t, 'name': t.replace('_', ' ').title(), 'sprite': None}
            for t in _door_types
        ]

        # Chest variants are likewise discovered from assets/objects/chest/
        # (one two-frame closed/open sheet per skin) rather than hardcoded —
        # dropping a new PNG in that folder is enough to make it show up
        # here. Falls back to a single 'wood' entry if the folder is
        # empty/missing so the editor still has something to place (Chest
        # itself falls back to a placeholder sprite for it).
        _chest_types = Chest.list_chest_types() or ['wood']
        self.chest_variants = [
            {'type': t, 'name': t.replace('_', ' ').title(), 'sprite': None}
            for t in _chest_types
        ]

        self.categories = {
            'System': [],
            'Terrain': [],
            'Structures': [],
            'Interactive': [
                {
                    'id': 'destructible_stone',
                    'name': 'Destructible Stone',
                    'sprite': None,
                    'width': 24,
                    'height': 24,
                    'object_type': 'destructible_stone',
                    'has_variants': True,
                    'variants': self.stone_variants,
                    'default_variant': 'medium'
                },
                {
                    'id': 'level_gate',
                    'name': 'Level Gate',
                    'sprite': None,
                    'width': 32,
                    'height': 32,
                    'object_type': 'level_gate',
                    'has_variants': True,
                    'variants': self.gate_variants,
                    'default_variant': 'stone',
                    'required_level': 1
                },
                {
                    'id': 'flying_pad',
                    'name': 'Flying Pad',
                    'sprite': None,
                    'width': 32,
                    'height': 32,
                    'object_type': 'flying_pad',
                    'has_variants': True,
                    'variants': self.flying_pad_variants,
                    'default_variant': 'stone1'
                },
                {
                    'id': 'nimbus_cloud',
                    'name': 'Nimbus Cloud',
                    'sprite': self.nimbus_cloud_sprite,
                    'width': 30,
                    'height': 23,
                    'object_type': 'nimbus_cloud',
                },
                {
                    'id': 'save_point',
                    'name': 'Save Point',
                    'sprite': None,
                    'width': 32,
                    'height': 32,
                    'object_type': 'save_point',
                    'has_variants': True,
                    'variants': [
                        {'type': 'big', 'name': 'Big Save Point', 'width': 64, 'height': 52, 'sprite': None},
                        {'type': 'small', 'name': 'Small Save Point', 'width': 32, 'height': 27, 'sprite': None}
                    ],
                    'default_variant': 'big'
                },
                {
                    'id': 'world_map_object',
                    'name': 'World Map',
                    'sprite': None,
                    'width': 32,
                    'height': 37,
                    'object_type': 'world_map_object',
                    'has_variants': True,
                    'variants': [
                        {'type': 'world_map', 'name': 'World Map', 'width': 32, 'height': 37, 'sprite': None},
                        {'type': 'world_map_sign', 'name': 'World Map Sign', 'width': 29, 'height': 32, 'sprite': None},
                    ],
                    'default_variant': 'world_map'
                },
                {
                    'id': 'chest',
                    'name': 'Treasure Chest',
                    'sprite': None,
                    'width': 24,
                    'height': 20,
                    'object_type': 'chest',
                    'has_variants': True,
                    'variants': self.chest_variants,
                    'default_variant': self.chest_variants[0]['type']
                },
                {
                    'id': 'door',
                    'name': 'Door',
                    'sprite': None,
                    'width': 32,
                    'height': 64,
                    'object_type': 'door',
                    'has_variants': True,
                    'variants': self.door_variants,
                    'default_variant': self.door_variants[0]['type']
                },
            ],
            'Decorations': [
                {
                    'id': 'tree',
                    'name': 'Tree',
                    'sprite': self._tree_icon_sprite,
                    'width': DECORATION_STYLES['tree']['frame_w'],
                    'height': DECORATION_STYLES['tree']['frame_h'],
                    'object_type': 'decoration',
                    'decoration_type': 'tree',
                    # No has_variants here — 'tree' has exactly one variant
                    # today (DECORATION_STYLES['tree']['variants']), so there's
                    # nothing to pick between yet. If a second row is ever
                    # added to tree.png, this can switch to the same
                    # has_variants/variants/default_variant scheme doors and
                    # chests use.
                },
            ],
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
        collision_sprite = pygame.Surface((16, 16), pygame.SRCALPHA)
        collision_sprite.fill((255, 0, 0, 100))
        pygame.draw.rect(collision_sprite, (255, 0, 0), (0, 0, 16, 16), 2)
        for i in range(0, 48, 8):
            pygame.draw.line(collision_sprite, (200, 0, 0, 120), (i, 0), (i - 16, 16), 1)

        self.categories['System'].append({
            'id': 'collision_wall',
            'name': 'Collision Wall',
            'sprite': collision_sprite,
            'width': 16,
            'height': 16,
            'is_collision': True
        })

        # Add every region type declared in REGION_STYLES to its own Terrain
        # category — water/lava/grass/dirt today, but this loop is what
        # makes adding a new one purely a REGION_STYLES + sprite-file
        # change: drop a new entry in animated_region.py (any 'patch'-mode
        # 64x64 sheet works the same way water/lava/grass already do) and
        # its file at assets/tilesets/animated_tiles/<sheet>.png, and it
        # shows up here automatically — no editor code changes needed.
        # All region types share the same is_animated_region drag-to-resize
        # placement flow; region_type just tells the runtime controller
        # which sprite sheet to draw from. Palette icon is that sheet's own
        # first frame (falls back to a hand-drawn placeholder using the
        # style's own color if the asset isn't there yet, so a missing file
        # never breaks the palette).
        for region_type, style in REGION_STYLES.items():
            icon_sprite = self._load_region_palette_icon(region_type)
            if icon_sprite is None:
                color = style.get('color', (200, 200, 200))
                icon_sprite = pygame.Surface((16, 16), pygame.SRCALPHA)
                icon_sprite.fill(color + (100,))
                pygame.draw.rect(icon_sprite, color, (0, 0, 16, 16), 2)
                dark_color = tuple(max(0, c - 40) for c in color)
                for i in range(0, 48, 8):
                    pygame.draw.line(icon_sprite, dark_color + (120,), (i, 0), (i - 16, 16), 1)

            self.categories['Terrain'].append({
                'id': f'{region_type}_region',
                'name': style.get('label', f'{region_type.title()} Region'),
                'sprite': icon_sprite,
                'width': TILE_SIZE,
                'height': TILE_SIZE,
                'is_animated_region': True,
                'region_type': region_type
            })

        # Add room transition to System category
        transition_sprite = pygame.Surface((16, 16), pygame.SRCALPHA)
        transition_sprite.fill((0, 100, 255, 100))
        pygame.draw.rect(transition_sprite, (0, 150, 255), (0, 0, 16, 16), 2)
        for i in range(0, 96, 8):
            pygame.draw.line(transition_sprite, (0, 120, 200, 120), (i, 0), (i - 16, 16), 1)

        self.categories['System'].append({
            'id': 'room_transition',
            'name': 'Room Transition',
            'sprite': transition_sprite,
            'width': 16,
            'height': 16,
            'is_transition': True
        })

        # Add trigger box to System category.
        trigger_box_sprite = pygame.Surface((16, 16), pygame.SRCALPHA)
        trigger_box_sprite.fill((0, 220, 120, 100))
        pygame.draw.rect(trigger_box_sprite, (0, 220, 120), (0, 0, 16, 16), 2)
        pygame.draw.line(trigger_box_sprite, (0, 220, 120), (0, 0), (16, 16), 1)
        pygame.draw.line(trigger_box_sprite, (0, 220, 120), (16, 0), (0, 16), 1)

        self.categories['System'].append({
            'id': 'trigger_box',
            'name': 'Trigger Box',
            'sprite': trigger_box_sprite,
            'width': 16,
            'height': 16,
            'is_trigger_box': True
        })

        # Generate sprites and variant sprites
        self._generate_placeholder_sprites()
        self._generate_variant_sprites()

        # Editor state
        self.current_category = list(self.categories.keys())[0]
        self.selected_object = None
        self.hover_object = None
        self.current_room_name = ""

        # Placement options
        # grid_snap_size is the quick placement grid shared with the room
        # editor's toolbar Grid control (0 = off, otherwise the snap size in
        # world pixels — typically 8 or 16). grid_snap is kept as a
        # backward-compatible bool alias (True whenever a size is set) for
        # any code/UI that just wants an on/off reading.
        self.grid_snap_size = TILE_SIZE
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

        # Panel show/hide toggle (same pattern as EditorToolbar)
        self.palette_visible = True
        self._panel_tab_w = 18
        self._panel_tab_h = 72
        self._hover_panel_toggle = False

        # Set via set_toolbar() below; read (via getattr-guarded access) to
        # find out whether an item is armed in the Items panel for the
        # chest-loot-assignment flow — see _try_assign_chest_loot.
        self.toolbar = None

    def set_toolbar(self, toolbar):
        """Set the toolbar reference and pass it to sub-editors that need to hide it"""
        self.toolbar = toolbar
        # Pass toolbar to flying pad path editor so it can hide it during editing
        self.flying_pad_path_editor.set_toolbar(toolbar)
        self.nimbus_cloud_path_editor.set_toolbar(toolbar)

    def set_sound_manager(self, sound_manager):
        """Give the editor a SoundManager (anything with .play_sfx(name)) so
        the door 'Preview' button can actually play the selected sound."""
        self.sound_manager = sound_manager

    def set_flag_manager(self, flag_manager):
        """Give the editor a FlagManager so trigger boxes can gate on
        switches/variables/timers. Enables the "Edit Event" button in the
        trigger placement panel; without this call it stays disabled."""
        self.flag_manager = flag_manager
        self.event_editor = EventEditorWindow(flag_manager, colors=self.colors)
        # Re-apply whatever character was set before the event editor
        # existed (set_flag_manager can run after set_current_character,
        # e.g. on first Room Editor open — see RoomEditor.set_flag_manager()).
        if self._pending_character_id is not None:
            self.event_editor.set_current_character(
                self._pending_character_id, self._pending_get_equipped_skills)
        if self._pending_known_rooms is not None:
            self.event_editor.set_known_rooms(
                self._pending_known_rooms, self._pending_known_room_dims)
        if self._pending_room_preview_provider is not None:
            self.event_editor.set_room_preview_provider(self._pending_room_preview_provider)

    def set_room_preview_provider(self, provider):
        """Tell the event editor how to render an actual tile preview for
        the Set Spawn overlay — see EventEditorWindow.set_room_preview_provider().
        Same pending-until-the-event-editor-exists shape as
        set_known_rooms() above."""
        self._pending_room_preview_provider = provider
        if self.event_editor is not None:
            self.event_editor.set_room_preview_provider(provider)

    def set_known_rooms(self, room_names, room_dims=None):
        """Tell the event editor which rooms actually exist right now —
        see EventEditorWindow.set_known_rooms(). Call this (e.g. with the
        live RoomManager's room list/sizes) whenever the host knows what
        rooms exist, and again any time that could have changed (room
        created/renamed/resized, Room Editor re-opened) so the change_map
        action's room dropdown and Set Spawn preview never go stale.

        Same pending-until-the-event-editor-exists shape as
        set_current_character() above, since this can be called (e.g. by
        RoomEditor at startup) before set_flag_manager() has created
        self.event_editor yet.
        """
        self._pending_known_rooms = room_names
        self._pending_known_room_dims = room_dims
        if self.event_editor is not None:
            self.event_editor.set_known_rooms(room_names, room_dims)

    def set_current_character(self, character_id, get_equipped_skills=None):
        """Tell the event editor which character's equipped skills should
        back the 'skill' action's add/remove pickers — see
        EventEditorWindow.set_current_character(). Call this (e.g. with
        self.player.character and lambda: self.player.equipped_attacks)
        whenever the host knows who's being played, and again any time
        that could have changed (character switch, Room Editor
        re-opened) so the picker never goes stale.

        get_equipped_skills should return the character's LIVE equipped
        list, not a saved/on-disk one — runtime 'skill' actions mutate the
        live player directly and are never written back to disk, so a
        disk-based list would miss anything granted/removed this session.

        Without this being kept in sync, the skill picker has no idea what
        the current character already has equipped: 'remove' shows nothing
        to remove, and 'add' can't tell which skills are already equipped —
        which is why "add skill" actions looked like they silently did
        nothing.
        """
        self._pending_character_id = character_id
        self._pending_get_equipped_skills = get_equipped_skills
        if self.event_editor is not None:
            self.event_editor.set_current_character(character_id, get_equipped_skills)

    # -------------------------------------------------------------------------
    # Panel show/hide tab
    # -------------------------------------------------------------------------

    def _panel_toggle_rect(self):
        """Return the rect for the ◀/▶ tab that straddles the panel's left edge."""
        gap = 6
        tx = (self.palette_x - self._panel_tab_w - gap) if self.palette_visible else (
                    self.screen_width - self._panel_tab_w)
        ty = self.palette_y + (self.palette_height - self._panel_tab_h) // 2
        return pygame.Rect(tx, ty, self._panel_tab_w, self._panel_tab_h)

    def _draw_panel_toggle_tab(self, screen):
        """Render the small ◀/▶ tab — always visible so the panel can be recalled."""
        rect = self._panel_toggle_rect()
        bg = self.colors['panel_light'] if self._hover_panel_toggle else self.colors['panel']
        border = self.colors['accent'] if self._hover_panel_toggle else self.colors['grid']
        pygame.draw.rect(screen, bg, rect, border_radius=6)
        pygame.draw.rect(screen, border, rect, 1, border_radius=6)
        arrow = '◀' if self.palette_visible else '▶'
        font = self.font_small
        label = font.render(
            arrow, True,
            self.colors['accent'] if self._hover_panel_toggle else self.colors['text_dim']
        )
        screen.blit(label, label.get_rect(center=rect.center))

    # Fixed corner-sample size used by _region_icon_crop_size for every
    # tile-mode frame, regardless of that frame's own pixel size — see
    # that method for why a fixed sample beats scaling the full frame.
    _REGION_ICON_SAMPLE = 16

    def _region_icon_crop_size(self, frame_w: int, frame_h: int):
        """Crop size to use for palette/variant thumbnails of a region's
        frame. Every frame — 24x24 dirt, 64x64 mud/sand/etc., 96x96
        clouds, 40x32 whatever, whatever oddball size gets added next —
        is sampled down to the same fixed _REGION_ICON_SAMPLE (16x16)
        corner crop instead of the whole frame, so every region type's
        palette icon reads as the same scale of "close-up on the
        texture" rather than each tile size producing a differently
        zoomed thumbnail. Full-frame art scaled down into the small
        palette slot reads mushy/indistinct for large frames, while a
        fixed small sample of the actual texture reads as a clean tile
        icon at any frame size. Frames smaller than the sample size in
        either dimension are left at their native size (nothing to crop
        down to) rather than upscaled.
        """
        crop_w = min(frame_w, self._REGION_ICON_SAMPLE)
        crop_h = min(frame_h, self._REGION_ICON_SAMPLE)
        return crop_w, crop_h

    def _load_region_palette_icon(self, region_type: str):
        """First frame of a region type's own sprite sheet (per
        REGION_STYLES), at its native pixel size — used as its palette
        thumbnail so the icon actually matches the art (water/lava/grass/
        dirt) instead of a hand-drawn placeholder.

        For 64x64-framed sheets this is a 16x16 corner sample of frame 0
        rather than the whole frame — see _region_icon_crop_size.

        Returned at native size, NOT pre-scaled: _draw_object_item already
        scales whatever sprite it's given to fit the palette slot while
        preserving aspect ratio (see its `scale = min(max_dim/sw,
        max_dim/sh)` — same as every other palette object, e.g. the 16x24
        sign post). Pre-scaling here too would just chain two scales
        together (down to a fixed box, then back up/down again to the
        slot size), softening the result for no reason.

        Returns None if the sheet file isn't there yet (e.g. asset not
        added), so callers can fall back to their own placeholder — same
        "never crash on a missing asset" spirit as the runtime loader in
        game.py.
        """
        style = REGION_STYLES.get(region_type, {})
        sprite_name = style.get('sheet', region_type)
        frame_w = style.get('frame_w', style.get('frame_size', 64))
        frame_h = style.get('frame_h', frame_w)
        crop_w, crop_h = self._region_icon_crop_size(frame_w, frame_h)

        path = os.path.join('assets', 'tilesets', 'animated_tiles', f'{sprite_name}.png')
        if not os.path.isfile(path):
            return None

        try:
            raw = pygame.image.load(path).convert_alpha()
        except (pygame.error, OSError):
            return None

        crop_w = min(crop_w, raw.get_width())
        crop_h = min(crop_h, raw.get_height())
        if crop_w <= 0 or crop_h <= 0:
            return None
        return raw.subsurface((0, 0, crop_w, crop_h)).copy()

    def _load_region_variant_sprites(self, region_type: str):
        """Every static variant of a 'tile'-mode region type (one frame per
        row of its sheet — dirt today, but works for any region_type whose
        REGION_STYLES entry has mode='tile'), for the variant picker in the
        settings panel — same idea as _load_region_palette_icon's single
        first-frame crop, but returns all grid_rows frames instead of just
        frame 0 so the picker can show real art per variant, like the
        sprite thumbnails _draw_variant_selector uses for gates/stones/etc.

        Each variant is cropped down the same way the palette icon is —
        64x64-framed variants (woodplanks, sand, ...) get a 16x16 corner
        sample instead of the whole 64x64 frame; non-64x64 variants
        (dirt's 24x24) are unaffected. See _region_icon_crop_size.

        Cached per region_type on first call since the sheet never changes
        at runtime. Returns a list (possibly empty, on missing/bad asset —
        callers fall back to a placeholder per missing entry) rather than
        None, so the picker can still draw all num_variants slots.
        """
        if region_type in self._region_variant_sprites_cache:
            return self._region_variant_sprites_cache[region_type]

        style = REGION_STYLES.get(region_type, {})
        sprite_name = style.get('sheet', region_type)
        frame_w = style.get('frame_w', style.get('frame_size', 24))
        frame_h = style.get('frame_h', frame_w)
        grid_rows = style.get('grid_rows', 1)
        crop_w, crop_h = self._region_icon_crop_size(frame_w, frame_h)

        path = os.path.join('assets', 'tilesets', 'animated_tiles', f'{sprite_name}.png')
        sprites = []
        if os.path.isfile(path):
            try:
                raw = pygame.image.load(path).convert_alpha()
                for row in range(grid_rows):
                    y = row * frame_h
                    # Each variant frame is still frame_h tall in the sheet
                    # (rows are laid out at the full frame size), but only
                    # the crop_w x crop_h corner of it is sampled out.
                    if y + frame_h <= raw.get_height() and crop_w <= raw.get_width():
                        sprites.append(raw.subsurface((0, y, crop_w, crop_h)).copy())
            except (pygame.error, OSError):
                sprites = []

        self._region_variant_sprites_cache[region_type] = sprites
        return sprites

    def _generate_variant_sprites(self):
        """Load or generate sprites for every object variant.

        For each object that declares ``has_variants``, this method attempts to
        load the real asset from disk and falls back to a programmatic placeholder
        when the file is not yet available.  The object's ``sprite`` field is also
        set to a copy of its default-variant sprite so the palette thumbnail is
        correct before the player selects a variant.
        """
        for category, objects in self.categories.items():
            for obj in objects:
                if not obj.get('has_variants', False):
                    continue

                variants = obj.get('variants', [])
                for variant in variants:
                    # Generate sprite for this variant
                    if obj['object_type'] == 'destructible_stone':
                        try:
                            stone_type = variant['type']
                            sprite_path = f'assets/objects/stones/{stone_type}_stone.png'
                            sprite = pygame.image.load(sprite_path).convert_alpha()
                            sprite = pygame.transform.scale(sprite, (variant['width'], variant['height']))
                            variant['sprite'] = sprite
                        except Exception:
                            # Asset not on disk yet — use a brown placeholder rectangle
                            sprite = pygame.Surface((variant['width'], variant['height']), pygame.SRCALPHA)
                            sprite.fill((139, 69, 19))
                            pygame.draw.rect(sprite, (0, 0, 0), (0, 0, variant['width'], variant['height']), 2)
                            variant['sprite'] = sprite

                    elif obj['object_type'] == 'door':
                        # Width/height come straight off the closed frame
                        # (half the sheet — see Door._load_sprites), so any
                        # size — small wood door or huge gate — just works
                        # without per-variant config here.
                        door = Door(0, 0, variant['type'], permanent=False)
                        variant['width'] = door.width
                        variant['height'] = door.height
                        variant['sprite'] = door.closed_sprite.copy()

                    elif obj['object_type'] == 'chest':
                        # Width/height come straight off the closed frame
                        # (half the sheet — see Chest._load_sprites), same
                        # deal as doors above.
                        chest = Chest(0, 0, variant['type'], opened=False)
                        variant['width'] = chest.width
                        variant['height'] = chest.height
                        variant['sprite'] = chest.closed_sprite.copy()

                    elif obj['object_type'] == 'level_gate':
                        gate = LevelGate(0, 0, variant['type'], 1)
                        # Store per-variant dimensions so the preview scales correctly
                        # (stone formation is 71×68, all others are 32×32)
                        variant['width'] = gate.width
                        variant['height'] = gate.height
                        if gate.sprite:
                            variant['sprite'] = gate.sprite.copy()
                        else:
                            # Fallback placeholder
                            w, h = gate.width, gate.height
                            sprite = pygame.Surface((w, h), pygame.SRCALPHA)
                            sprite.fill((100, 100, 100))
                            pygame.draw.rect(sprite, (0, 0, 0), (0, 0, w, h), 2)
                            variant['sprite'] = sprite

                    elif obj['object_type'] == 'flying_pad':
                        try:
                            pad_type = variant['type']
                            sprite_path = f'assets/objects/flying_pads/{pad_type}_flyingpad.png'
                            sprite = pygame.image.load(sprite_path).convert_alpha()
                            sprite = pygame.transform.scale(sprite, (32, 32))
                            variant['sprite'] = sprite
                        except Exception:
                            # Asset not on disk yet — use a sky-blue placeholder with an arrow
                            sprite = pygame.Surface((32, 32), pygame.SRCALPHA)
                            sprite.fill((100, 200, 255))
                            pygame.draw.rect(sprite, (0, 0, 0), (0, 0, 32, 32), 2)
                            center_x = 16
                            center_y = 16
                            points = [
                                (center_x, center_y - 10),
                                (center_x - 8, center_y + 5),
                                (center_x + 8, center_y + 5)
                            ]
                            pygame.draw.polygon(sprite, (255, 255, 255), points)
                            variant['sprite'] = sprite

                    elif obj['object_type'] == 'save_point':
                        # Try to load custom sprite first
                        variant_type = variant['type']
                        sprite_loaded = False

                        try:
                            sprite_path = f'assets/objects/save_points/{variant_type}_save_point.png'
                            sprite = pygame.image.load(sprite_path).convert_alpha()
                            variant['sprite'] = sprite
                            sprite_loaded = True
                        except Exception:
                            pass

                        if not sprite_loaded:
                            # Generate placeholder with correct dimensions
                            width = variant.get('width', 64 if variant_type == 'big' else 32)
                            height = variant.get('height', 52 if variant_type == 'big' else 27)
                            color = (255, 215, 0)  # Gold
                            sprite = pygame.Surface((width, height), pygame.SRCALPHA)

                            if variant_type == 'big':
                                # Diamond shape for big save point
                                center_x = width // 2
                                center_y = height // 2
                                points = [
                                    (center_x, 2),  # Top
                                    (width - 2, center_y),  # Right
                                    (center_x, height - 2),  # Bottom
                                    (2, center_y)  # Left
                                ]
                                pygame.draw.polygon(sprite, color, points)
                                pygame.draw.polygon(sprite, (255, 255, 200), points, 2)
                            else:
                                # Circle shape for small save point
                                center_x = width // 2
                                center_y = height // 2
                                radius = min(width, height) // 2 - 2
                                pygame.draw.circle(sprite, color, (center_x, center_y), radius)
                                pygame.draw.circle(sprite, (255, 255, 200), (center_x, center_y), radius, 2)

                            variant['sprite'] = sprite

                    elif obj['object_type'] == 'world_map_object':
                        variant_type = variant['type']
                        try:
                            sprite_path = f'assets/objects/world_map/{variant_type}.png'
                            sprite = pygame.image.load(sprite_path).convert_alpha()
                            # Derive world-unit size directly from pixel dimensions —
                            # draw() will multiply by RENDER_SCALE, so no division here.
                            variant['width'] = sprite.get_width()
                            variant['height'] = sprite.get_height()
                            variant['sprite'] = sprite
                        except Exception:
                            # Asset not on disk yet — use a brown/tan placeholder
                            w = variant.get('width', 32)
                            h = variant.get('height', 32)
                            sprite = pygame.Surface((w, h), pygame.SRCALPHA)
                            color = (101, 67, 33) if variant_type == 'world_map_sign' else (139, 90, 43)
                            sprite.fill(color)
                            pygame.draw.rect(sprite, (0, 0, 0), (0, 0, w, h), 2)
                            variant['sprite'] = sprite

                # Set the main object sprite to the default variant
                default_variant_type = obj.get('default_variant')
                if default_variant_type:
                    for variant in variants:
                        if variant['type'] == default_variant_type:
                            obj['sprite'] = variant['sprite'].copy()
                            break

    def _generate_placeholder_sprites(self):
        """Create visual sprites for objects that don't have real art yet"""
        for category, objects in self.categories.items():
            for obj in objects:
                if obj.get('sprite') is not None:
                    continue

                # Skip objects that already have sprites (system objects are built manually above)
                system_flags = ('is_spawn', 'is_collision', 'is_animated_region', 'is_transition')
                if any(obj.get(flag, False) for flag in system_flags):
                    continue

                # Skip variant objects - they get sprites from _generate_variant_sprites
                if obj.get('has_variants', False):
                    continue

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

                obj['sprite'] = sprite

    def set_grid_snap_size(self, size: int):
        """Set the placement/snap grid size (0 = off, otherwise the snap
        size in world pixels). Called by the room editor each frame to
        mirror the toolbar's quick Grid control, and safe to call directly
        too. Keeps the legacy `grid_snap` bool in sync for any code/UI that
        just wants an on/off reading."""
        self.grid_snap_size = max(0, int(size))
        self.grid_snap = self.grid_snap_size > 0

    def _toggle_grid_snap(self):
        """G key: toggle snapping on/off without losing the last chosen
        grid size (8px / 16px) — flips back and forth between 0 and
        whatever size was last active."""
        if self.grid_snap_size > 0:
            self._last_grid_snap_size = self.grid_snap_size
            self.set_grid_snap_size(0)
        else:
            self.set_grid_snap_size(getattr(self, '_last_grid_snap_size', TILE_SIZE) or TILE_SIZE)

    def toggle(self):
        """Open or close the object editor"""
        self.active = not self.active
        if self.active:
            self.selected_object = None
            self.selected_variant = None
            self.showing_variants_for = None
            self.scroll_offset = 0
            self.placing_collision = False
            self.preview_collision = None
            self.gate_level_input_active = False
            self.transition_config.close()
            self.pending_transition = None
            self.placing_transition = False
            self.preview_transition = None
            self.placing_transition_spawn = False
            self.flying_pad_path_editor.close()
            self.pending_flying_pad = None
            self.placing_flying_pad = False
            self.nimbus_cloud_path_editor.close()
            self.pending_nimbus_cloud = None
            self.placing_nimbus_cloud = False
            self.placing_trigger_box = False
            self.preview_trigger_box = None
            self.trigger_box_id_input_active = False
            self.world_map_dropdown_open = False
            if self.event_editor is not None:
                self.event_editor.active = False

    def _get_current_variant(self, obj):
        """Get the currently selected variant for an object"""
        if not obj or not isinstance(obj, dict):
            return None

        if not obj.get('has_variants', False):
            return None

        # If this object is selected and has a variant chosen
        if self.selected_object == obj and self.selected_variant:
            return self.selected_variant

        # Otherwise return default
        default_type = obj.get('default_variant')
        variants = obj.get('variants', [])

        if not variants:
            return None

        for variant in variants:
            if variant['type'] == default_type:
                return variant

        return variants[0] if variants else None

    def _check_object_at_position(self, world_x, world_y):
        """See if there's an object at this position (for deletion)"""
        # Check spawn point
        spawn = self.spawn_manager.get_spawn_point(self.current_room_name)
        if spawn:
            distance = ((spawn.x - world_x) ** 2 + (spawn.y - world_y) ** 2) ** 0.5
            if distance < max(spawn.width, spawn.height) / 2:
                return spawn, 'spawn'

        # Check collision walls. Iterate back-to-front (most recently placed
        # first) since draw_collision_objects draws the list front-to-back —
        # the last item in the list is the one rendered on top. Walls are
        # frequently placed overlapping/adjacent to each other (e.g. to build
        # a corridor), and picking the first (oldest/bottom) match instead of
        # the last (newest/topmost, visually-clicked) one meant a right-click
        # could silently delete a hidden wall underneath while the one you
        # actually clicked stayed put — indistinguishable since both render
        # as the same red overlay — requiring a second click to finish the
        # job.
        collision_objs = self.collision_manager.get_collision_objects(self.current_room_name)
        for collision_obj in reversed(collision_objs):
            if (collision_obj.x <= world_x <= collision_obj.x + collision_obj.width and
                    collision_obj.y <= world_y <= collision_obj.y + collision_obj.height):
                return collision_obj, 'collision'

        # Check room transitions
        transitions = self.transition_manager.get_transitions(self.current_room_name)
        for transition in transitions:
            if transition.check_collision_with_point(int(world_x), int(world_y)):
                return transition, 'transition'

        # Check destructible stones
        if self.room_manager:
            room = self.room_manager.get_room_by_name(self.current_room_name)
            if room and hasattr(room, 'destructible_stones'):
                for stone in room.destructible_stones:
                    distance = ((stone.x - world_x) ** 2 + (stone.y - world_y) ** 2) ** 0.5
                    if distance < max(stone.width, stone.height) / 2:
                        return stone, 'stone'

        # Check decorations (trees, etc.)
        if self.room_manager:
            room = self.room_manager.get_room_by_name(self.current_room_name)
            if room and hasattr(room, 'decorations'):
                for decoration in room.decorations:
                    distance = ((decoration.x - world_x) ** 2 + (decoration.y - world_y) ** 2) ** 0.5
                    if distance < max(decoration.width, decoration.height) / 2:
                        return decoration, 'decoration'

        # Check flying pads
        pads = self.flying_pad_manager.get_pads(self.current_room_name)
        for pad in pads:
            distance = ((pad.x - world_x) ** 2 + (pad.y - world_y) ** 2) ** 0.5
            if distance < max(pad.width, pad.height) / 2:
                return pad, 'flying_pad'

        # Check nimbus clouds
        clouds = self.nimbus_cloud_manager.get_clouds(self.current_room_name)
        for cloud in clouds:
            distance = ((cloud.x - world_x) ** 2 + (cloud.y - world_y) ** 2) ** 0.5
            if distance < max(cloud.width, cloud.height) / 2:
                return cloud, 'nimbus_cloud'

        # Check doors
        for door in self.door_manager.get_doors(self.current_room_name):
            distance = ((door.x - world_x) ** 2 + (door.y - world_y) ** 2) ** 0.5
            if distance < max(door.width, door.height) / 2:
                return door, 'door'

        # Check chests
        for chest in self.chest_manager.get_chests(self.current_room_name):
            distance = ((chest.x - world_x) ** 2 + (chest.y - world_y) ** 2) ** 0.5
            if distance < max(chest.width, chest.height) / 2:
                return chest, 'chest'

        # Check level gates
        gates = self.gate_manager.get_gates(self.current_room_name)
        for gate in gates:
            distance = ((gate.x - world_x) ** 2 + (gate.y - world_y) ** 2) ** 0.5
            if distance < max(gate.width, gate.height) / 2:
                return gate, 'gate'

        # Check save points
        save_points = self.save_point_manager.get_save_points(self.current_room_name)
        for save_point in save_points:
            distance = ((save_point.x - world_x) ** 2 + (save_point.y - world_y) ** 2) ** 0.5
            if distance < max(save_point.width, save_point.height) / 2:
                return save_point, 'save_point'

        # Check world map objects
        for obj in self.world_map_manager.get_objects(self.current_room_name):
            distance = ((obj.x - world_x) ** 2 + (obj.y - world_y) ** 2) ** 0.5
            if distance < max(obj.width, obj.height) / 2:
                return obj, 'world_map_object'

        # Check trigger boxes
        for box in self.trigger_box_manager.get_boxes(self.current_room_name):
            if (box.x <= world_x <= box.x + box.width and
                    box.y <= world_y <= box.y + box.height):
                return box, 'trigger_box'

        # Check water/grass/etc regions LAST — regions tend to be large,
        # ground-level fills that other system boxes (trigger boxes,
        # collision, doors, etc.) get placed on top of. If regions were
        # checked earlier, clicking on a spot where a region overlaps one
        # of those boxes would always hit the region first, forcing it to
        # be deleted/moved before the box underneath could be selected or
        # edited. Checking regions last means any other box "wins" the
        # click when they overlap, and a region is only picked when
        # nothing else is there.
        regions = self.animated_region_manager.get_regions(self.current_room_name)
        for region in regions:
            if (region.x <= world_x <= region.x + region.width and
                    region.y <= world_y <= region.y + region.height):
                return region, 'animated_region'

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

            if hasattr(self, 'on_spawn_deleted') and self.on_spawn_deleted:
                self.on_spawn_deleted(obj, self.current_room_name)

        elif obj_type == 'collision':
            self.collision_manager.remove_collision_object(obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'collision_objects'):
                    if obj in room.collision_objects:
                        room.collision_objects.remove(obj)
                    self.room_manager.save_room(room)

            if hasattr(self, 'on_collision_deleted') and self.on_collision_deleted:
                self.on_collision_deleted(obj, self.current_room_name)

        elif obj_type == 'animated_region':
            self.animated_region_manager.remove_region(obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'animated_regions'):
                    if obj in room.animated_regions:
                        room.animated_regions.remove(obj)
                    self.room_manager.save_room(room)

            if hasattr(self, 'on_animated_region_deleted') and self.on_animated_region_deleted:
                self.on_animated_region_deleted(obj, self.current_room_name)

        elif obj_type == 'flying_pad':
            self.flying_pad_manager.remove_pad(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'flying_pads'):
                    if obj in room.flying_pads:
                        room.flying_pads.remove(obj)

            if hasattr(self, 'on_flying_pad_deleted') and self.on_flying_pad_deleted:
                self.on_flying_pad_deleted(obj, self.current_room_name)

        elif obj_type == 'nimbus_cloud':
            self.nimbus_cloud_manager.remove_cloud(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'nimbus_clouds'):
                    if obj in room.nimbus_clouds:
                        room.nimbus_clouds.remove(obj)

            if hasattr(self, 'on_nimbus_cloud_deleted') and self.on_nimbus_cloud_deleted:
                self.on_nimbus_cloud_deleted(obj, self.current_room_name)

        elif obj_type == 'transition':
            self.transition_manager.remove_transition(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'room_transitions'):
                    if obj in room.room_transitions:
                        room.room_transitions.remove(obj)

            if hasattr(self, 'on_transition_deleted') and self.on_transition_deleted:
                self.on_transition_deleted(obj, self.current_room_name)

        elif obj_type == 'stone':
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'destructible_stones'):
                    if obj in room.destructible_stones:
                        room.destructible_stones.remove(obj)

            if hasattr(self, 'on_stone_deleted') and self.on_stone_deleted:
                self.on_stone_deleted(obj, self.current_room_name)

        elif obj_type == 'decoration':
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'decorations'):
                    if obj in room.decorations:
                        room.decorations.remove(obj)

            if hasattr(self, 'on_decoration_deleted') and self.on_decoration_deleted:
                self.on_decoration_deleted(obj, self.current_room_name)

        elif obj_type == 'door':
            self.door_manager.remove_door(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'doors') and obj in room.doors:
                    room.doors.remove(obj)

            if hasattr(self, 'on_door_deleted') and self.on_door_deleted:
                self.on_door_deleted(obj, self.current_room_name)

        elif obj_type == 'chest':
            self.chest_manager.remove_chest(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'chests') and obj in room.chests:
                    room.chests.remove(obj)

            if hasattr(self, 'on_chest_deleted') and self.on_chest_deleted:
                self.on_chest_deleted(obj, self.current_room_name)

        elif obj_type == 'gate':
            self.gate_manager.remove_gate(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'level_gates'):
                    if obj in room.level_gates:
                        room.level_gates.remove(obj)

            if hasattr(self, 'on_gate_deleted') and self.on_gate_deleted:
                self.on_gate_deleted(obj, self.current_room_name)

        elif obj_type == 'save_point':
            self.save_point_manager.remove_save_point(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'save_points'):
                    if obj in room.save_points:
                        room.save_points.remove(obj)

            if hasattr(self, 'on_save_point_deleted') and self.on_save_point_deleted:
                self.on_save_point_deleted(obj)

        elif obj_type == 'world_map_object':
            self.world_map_manager.remove_object(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'world_map_objects') and obj in room.world_map_objects:
                    room.world_map_objects.remove(obj)
            if self.on_world_map_deleted:
                self.on_world_map_deleted(obj, self.current_room_name)

        elif obj_type == 'trigger_box':
            self.trigger_box_manager.remove_box(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'trigger_boxes'):
                    if obj in room.trigger_boxes:
                        room.trigger_boxes.remove(obj)

            if hasattr(self, 'on_trigger_box_deleted') and self.on_trigger_box_deleted:
                self.on_trigger_box_deleted(obj, self.current_room_name)

    def _is_object_disabled(self, obj) -> bool:
        """Check if we can't place this object (e.g. spawn already exists)"""
        if not obj or not isinstance(obj, dict):
            return True

        if obj.get('is_spawn', False):
            return self.spawn_manager.has_spawn_point(self.current_room_name)
        return False

    def _is_door_permanent_checkbox_clicked(self, mouse_pos):
        """Check if the door 'Permanent' checkbox was clicked, and toggle it."""
        if not self.selected_object or not isinstance(self.selected_object, dict):
            return False
        if self.selected_object.get('object_type') != 'door':
            return False

        box = self.ui_rects.get('door_permanent_checkbox')
        if box and box.collidepoint(mouse_pos):
            self.door_permanent = not self.door_permanent
            return True
        return False

    def _is_door_sound_ui_clicked(self, mouse_pos):
        """Handle clicks on the door sound picker: one small button per
        available door SFX (selects it for the next door placed) plus a
        ▶ Preview button (plays whichever one is currently selected).
        Returns True if the click was consumed here."""
        if not self.selected_object or not isinstance(self.selected_object, dict):
            return False
        if self.selected_object.get('object_type') != 'door':
            return False

        for rect, name in self.ui_rects.get('door_sound_buttons', []):
            if rect.collidepoint(mouse_pos):
                self.door_sound_text = name
                return True

        preview_rect = self.ui_rects.get('door_sound_preview_btn')
        if preview_rect and preview_rect.collidepoint(mouse_pos):
            if self.sound_manager is not None:
                self.sound_manager.play_sfx(self.door_sound_text)
            return True

        return False

    def _try_assign_chest_loot(self, mouse_pos):
        """If the toolbar has an item armed (Items panel selection) and the
        click landed on a placed chest, assign that loot to it and report
        the click as consumed. Clicking the same chest again with the same
        item stacks the quantity (up to 99); picking a different item
        replaces whatever loot the chest had and resets quantity to 1.
        Returns True if the click was consumed here.

        Does NOT call on_chest_placed — that callback is wired to push an
        object_add undo entry, so reusing it for loot edits made Ctrl+Z
        delete the whole chest. Loot mutations go through on_chest_loot_changed
        instead (optional; room data is already live since the chest object
        is mutated in place).
        """
        if not self.toolbar:
            print("[LOOT DEBUG] no toolbar on object_editor!")
            return False
        selected_item_id = getattr(self.toolbar, 'selected_item_id', '')
        if not selected_item_id:
            print("[LOOT DEBUG] no item armed (object_editor's view)")
            return False
        if self._is_in_palette(mouse_pos[0], mouse_pos[1]):
            print("[LOOT DEBUG] click swallowed by _is_in_palette")
            return False

        # Prefer a generous AABB hit test over the centre-distance check used
        # by _check_object_at_position — loot assignment is easy to miss on a
        # small chest, and a miss used to fall through into normal placement.
        chest = self._chest_at_world(self.mouse_world_x, self.mouse_world_y)
        print(f"[LOOT DEBUG] chest lookup in room={self.current_room_name!r}: "
              f"found={chest is not None} candidates={len(self.chest_manager.get_chests(self.current_room_name))}")
        if chest is None:
            return False

        if chest.item_id == selected_item_id:
            print(
                f"[LOOT DEBUG] set chest.item_id={chest.item_id!r} qty={chest.item_qty} on chest at ({chest.x},{chest.y})")
            chest.item_qty = min(99, getattr(chest, 'item_qty', 1) + 1)
        else:
            chest.item_id = selected_item_id
            chest.item_qty = 1

        if getattr(self, 'on_chest_loot_changed', None):
            self.on_chest_loot_changed(chest, self.current_room_name)

        return True

    def _chest_at_world(self, world_x, world_y):
        """Return the chest under (world_x, world_y), or None.

        Uses the chest's full axis-aligned bounds (with a small pad) rather
        than a centre-distance radius so corner/edge clicks still count —
        important for the loot-assignment flow where a miss must not place
        a new object.
        """
        if not self.current_room_name:
            return None
        pad = 4  # world units of forgiveness around the sprite
        for chest in self.chest_manager.get_chests(self.current_room_name):
            hw = max(chest.width, 1) / 2 + pad
            hh = max(chest.height, 1) / 2 + pad
            if (chest.x - hw <= world_x <= chest.x + hw and
                    chest.y - hh <= world_y <= chest.y + hh):
                return chest
        return None

    def _item_armed(self):
        """True when the toolbar Items panel has an item selected for loot assignment."""
        return bool(self.toolbar and getattr(self.toolbar, 'selected_item_id', ''))

    def _is_level_input_clicked(self, mouse_pos):
        """Check if the level input box was clicked"""
        if not self.selected_object or not isinstance(self.selected_object, dict):
            return False

        if self.selected_object.get('object_type') != 'level_gate':
            return False

        input_x = self.palette_x + self.palette_padding + 135
        input_y = self.palette_y + self.palette_height - 105
        input_width = 60
        input_height = 25

        input_rect = pygame.Rect(input_x, input_y, input_width, input_height)
        return input_rect.collidepoint(mouse_pos)

    def _is_variant_selector_clicked(self, mouse_pos):
        """Check if clicking on variant selector and handle selection"""
        if not self.selected_object or not isinstance(self.selected_object, dict):
            return False

        if not self.selected_object.get('has_variants', False):
            return False

        # Check variant selector rects
        for i, rect_data in enumerate(self.ui_rects.get('variant_rects', [])):
            if rect_data['rect'].collidepoint(mouse_pos):
                # Select this variant
                self.selected_variant = rect_data['variant']
                self.showing_variants_for = self.selected_object
                return True

        return False

    def _is_in_palette(self, mouse_x, mouse_y):
        """Check if the mouse is hovering over the palette"""
        if not self.palette_visible:
            return False
        return (self.palette_x <= mouse_x <= self.palette_x + self.palette_width and
                self.palette_y <= mouse_y <= self.palette_y + self.palette_height)

    def _handle_palette_click(self, mouse_pos):
        """Handle clicks inside the palette"""
        category_start_y = self.palette_y + 45

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
                    self.selected_variant = None  # clear old variant before switching object
                    self.selected_object = obj
                    self.variant_scroll = 0
                    # Reset variant selection when selecting new object
                    if obj.get('has_variants', False):
                        self.showing_variants_for = obj
                        self.selected_variant = self._get_current_variant(obj)
                    else:
                        self.showing_variants_for = None
                        self.selected_variant = None
                return

    # Minimum center-to-center distance (world units) allowed between two
    # point-placed objects of the same kind. Placing with nothing to stop
    # you from clicking on top of an existing object silently stacks a new,
    # fully-simulated copy underneath it — invisible to the eye but not to
    # the CPU: every stacked stone/gate/chest/etc. is a real entry in
    # room.collision_objects / obstacles and gets checked every frame for
    # the rest of the room's life. A room with a few dozen "visible" objects
    # secretly holding hundreds of duplicates is where the creeping in-game
    # slowdown reported by players actually comes from.
    _MIN_PLACEMENT_SPACING = 10

    def _too_close_to_existing(self, x, y, existing_objects, min_distance=None):
        """True if (x, y) lands within min_distance of any object already
        in existing_objects. Call this right before appending a new
        point-placed object so repeat clicks on (near) the same spot are a
        no-op instead of piling up an invisible duplicate.
        """
        if not existing_objects:
            return False
        if min_distance is None:
            min_distance = self._MIN_PLACEMENT_SPACING
        min_distance_sq = min_distance * min_distance
        for obj in existing_objects:
            ox = getattr(obj, 'x', None)
            oy = getattr(obj, 'y', None)
            if ox is None or oy is None:
                continue
            if (x - ox) ** 2 + (y - oy) ** 2 <= min_distance_sq:
                return True
        return False

    def _place_object(self, camera_x, camera_y, room_name):
        """Actually place the selected object in the world"""
        # Hard stop: an armed item means "assign loot", never "place object".
        # selected_object often still points at Chest from the palette, so
        # without this a missed loot click silently stamps a second chest.
        if self._item_armed():
            return
        if not self.selected_object or not isinstance(self.selected_object, dict):
            return

        if self.selected_object.get('is_spawn', False):
            spawn_obj = self.spawn_manager.place_spawn_point(
                int(self.preview_x),
                int(self.preview_y),
                room_name
            )

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    room.spawn_point = (int(self.preview_x), int(self.preview_y))

            if hasattr(self, 'on_spawn_placed') and self.on_spawn_placed and spawn_obj:
                self.on_spawn_placed(spawn_obj, room_name)

        elif self.selected_object.get('object_type') == 'destructible_stone':
            from objects.destructible_stone import DestructibleStone

            # Get selected variant or default
            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            stone_type = variant['type'] if variant else 'medium'

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room and self._too_close_to_existing(
                        self.preview_x, self.preview_y,
                        getattr(room, 'destructible_stones', None)):
                    return

            stone = DestructibleStone(
                int(self.preview_x),
                int(self.preview_y),
                stone_type
            )

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    if not hasattr(room, 'destructible_stones'):
                        room.destructible_stones = []
                    room.destructible_stones.append(stone)

                    if hasattr(self, 'on_stone_placed') and self.on_stone_placed:
                        self.on_stone_placed(stone, room_name)


        elif self.selected_object.get('object_type') == 'decoration':
            from objects.decoration_objects import Decoration

            decoration_type = self.selected_object.get('decoration_type', 'tree')

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room and self._too_close_to_existing(
                        self.preview_x, self.preview_y,
                        getattr(room, 'decorations', None)):
                    return

            decoration = Decoration(
                int(self.preview_x),
                int(self.preview_y),
                decoration_type
            )

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    if not hasattr(room, 'decorations'):
                        room.decorations = []
                    room.decorations.append(decoration)

                    if hasattr(self, 'on_decoration_placed') and self.on_decoration_placed:
                        self.on_decoration_placed(decoration, room_name)


        elif self.selected_object.get('object_type') == 'flying_pad':
            existing_pads = None
            if self.room_manager:
                _room = self.room_manager.get_room_by_name(room_name)
                existing_pads = getattr(_room, 'flying_pads', None) if _room else None
            if self._too_close_to_existing(self.preview_x, self.preview_y, existing_pads):
                return

            # Get selected variant
            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            pad_type = variant['type'] if variant and 'type' in variant else 'stone'

            # Create flying pad
            pad = FlyingPad(int(self.preview_x), int(self.preview_y), pad_type)

            # Add the first waypoint at the pad's position automatically
            from objects.flying_pad import FlyingPadWaypoint
            initial_waypoint = FlyingPadWaypoint(int(self.preview_x), int(self.preview_y), is_boundary=False)
            pad.waypoints = [initial_waypoint]

            # Store pad temporarily
            self.pending_flying_pad = pad
            self.placing_flying_pad = True

            # Get available rooms list
            available_rooms = []
            if self.room_manager:
                available_rooms = self.room_manager.get_room_names()

            # Get current room dimensions
            # Fallback room size used when the manager hasn't loaded a room yet
            room_width = 2400
            room_height = 1800

            if self.room_manager:
                current_room = self.room_manager.get_room_by_name(room_name)
                if current_room:
                    room_width = current_room.width
                    room_height = current_room.height

            # Open path editor WITH ROOM DIMENSIONS
            self.flying_pad_path_editor.open(
                pad,
                room_name,
                available_rooms,
                room_width,
                room_height
            )

        elif self.selected_object.get('object_type') == 'nimbus_cloud':
            existing_clouds = None
            if self.room_manager:
                _room = self.room_manager.get_room_by_name(room_name)
                existing_clouds = getattr(_room, 'nimbus_clouds', None) if _room else None
            if self._too_close_to_existing(self.preview_x, self.preview_y, existing_clouds):
                return

            # Get selected variant
            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            cloud_type = variant['type'] if variant and 'type' in variant else 'white'

            # Create nimbus cloud
            cloud = NimbusCloud(int(self.preview_x), int(self.preview_y), cloud_type)

            # Add the first waypoint at the cloud's position automatically
            from objects.nimbus_cloud import NimbusCloudWaypoint
            initial_waypoint = NimbusCloudWaypoint(int(self.preview_x), int(self.preview_y), is_boundary=False)
            cloud.waypoints = [initial_waypoint]

            # Store cloud temporarily
            self.pending_nimbus_cloud = cloud
            self.placing_nimbus_cloud = True

            # origin_room must be the placement room, not whatever room the
            # path editor is in when the user hits Save. Return rides key
            # off this; if it is the destination, boarding there reverses
            # toward the destination again (stuck in room B after reload).
            cloud.origin_room = room_name
            cloud.current_room = room_name

            # Capture the room-editor's current live view — this is the
            # frame NimbusCloudPathEditor is about to lock the first leg
            # to (see the "open path editor" call below). Persisting it on
            # the cloud itself lets runtime playback recreate that exact
            # frame on a return ride back into this room, matching how the
            # leg was authored instead of falling back to the generic
            # top-anchored formula every other leg uses.
            cloud.origin_camera_x = camera_x
            cloud.origin_camera_y = camera_y

            # Get available rooms list
            available_rooms = []
            if self.room_manager:
                available_rooms = self.room_manager.get_room_names()

            # Get current room dimensions
            room_width = 2400
            room_height = 1800

            if self.room_manager:
                current_room = self.room_manager.get_room_by_name(room_name)
                if current_room:
                    room_width = current_room.width
                    room_height = current_room.height

            # Open path editor WITH ROOM DIMENSIONS and the current view —
            # the cloud's path editor locks its camera to whatever's on
            # screen right now for this first leg, rather than following a
            # free-panning camera like the flying pad editor does.
            self.nimbus_cloud_path_editor.open(
                cloud,
                room_name,
                available_rooms,
                room_width,
                room_height,
                camera_x=camera_x,
                camera_y=camera_y
            )

        elif self.selected_object.get('object_type') == 'save_point':
            existing_save_points = None
            if self.room_manager:
                _room = self.room_manager.get_room_by_name(room_name)
                existing_save_points = getattr(_room, 'save_points', None) if _room else None
            if self._too_close_to_existing(self.preview_x, self.preview_y, existing_save_points):
                return

            # Get selected variant
            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            sp_variant = variant['type'] if variant and 'type' in variant else 'big'

            # Create save point
            save_point = SavePoint(int(self.preview_x), int(self.preview_y), sp_variant)

            # Add to manager
            self.save_point_manager.add_save_point(room_name, save_point)

            # Add to the live room so the save point is drawn and serialized immediately
            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    if not hasattr(room, 'save_points'):
                        room.save_points = []
                    # See chest placement above: save_point_manager's list is
                    # aliased to room.save_points, so avoid a duplicate append.
                    if save_point not in room.save_points:
                        room.save_points.append(save_point)

            # Notify game
            if self.on_save_point_placed:
                self.on_save_point_placed(save_point)

        elif self.selected_object.get('object_type') == 'world_map_object':
            existing_wm_objects = None
            if self.room_manager:
                _room = self.room_manager.get_room_by_name(room_name)
                existing_wm_objects = getattr(_room, 'world_map_objects', None) if _room else None
            if self._too_close_to_existing(self.preview_x, self.preview_y, existing_wm_objects):
                return

            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            variant_type = variant['type'] if variant and 'type' in variant else 'world_map'
            map_name = self.world_map_name_text

            obj = WorldMapObject(int(self.preview_x), int(self.preview_y), variant_type, map_name)
            self.world_map_manager.add_object(room_name, obj)

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    if not hasattr(room, 'world_map_objects'):
                        room.world_map_objects = []
                    # See chest placement above: world_map_manager's list is
                    # aliased to room.world_map_objects, so avoid a duplicate append.
                    if obj not in room.world_map_objects:
                        room.world_map_objects.append(obj)

            if self.on_world_map_placed:
                self.on_world_map_placed(obj, room_name)

        elif self.selected_object.get('object_type') == 'door':
            existing_doors = None
            if self.room_manager:
                _room = self.room_manager.get_room_by_name(room_name)
                existing_doors = getattr(_room, 'doors', None) if _room else None
            if self._too_close_to_existing(self.preview_x, self.preview_y, existing_doors):
                return

            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            door_type = variant['type'] if variant and 'type' in variant else self.door_variants[0]['type']

            door = Door(
                int(self.preview_x),
                int(self.preview_y),
                door_type,
                permanent=self.door_permanent,
                door_sound=self.door_sound_text
            )

            self.door_manager.add_door(room_name, door)

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    if not hasattr(room, 'doors'):
                        room.doors = []
                    # See chest placement above: door_manager's list is
                    # aliased to room.doors, so avoid a duplicate append.
                    if door not in room.doors:
                        room.doors.append(door)

            if hasattr(self, 'on_door_placed') and self.on_door_placed:
                self.on_door_placed(door, room_name)

        elif self.selected_object.get('object_type') == 'chest':
            existing_chests = None
            if self.room_manager:
                _room = self.room_manager.get_room_by_name(room_name)
                existing_chests = getattr(_room, 'chests', None) if _room else None
            if self._too_close_to_existing(self.preview_x, self.preview_y, existing_chests):
                return

            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            chest_type = variant['type'] if variant and 'type' in variant else self.chest_variants[0]['type']

            # Chests are always placed empty now — loot is assigned
            # afterward by selecting an item in the toolbar's Items panel
            # and clicking the placed chest (see _try_assign_chest_loot).
            chest = Chest(int(self.preview_x), int(self.preview_y), chest_type)

            self.chest_manager.add_chest(room_name, chest)

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    if not hasattr(room, 'chests'):
                        room.chests = []
                    # chest_manager.chests[room_name] and room.chests are the
                    # same underlying list (aliased in _sync_room_to_editor),
                    # so add_chest() above already appended this chest here.
                    # Guard against a second append, same as the redo path.
                    if chest not in room.chests:
                        room.chests.append(chest)

            if hasattr(self, 'on_chest_placed') and self.on_chest_placed:
                self.on_chest_placed(chest, room_name)

        elif self.selected_object.get('object_type') == 'level_gate':
            existing_gates = None
            if self.room_manager:
                _room = self.room_manager.get_room_by_name(room_name)
                existing_gates = getattr(_room, 'level_gates', None) if _room else None
            if self._too_close_to_existing(self.preview_x, self.preview_y, existing_gates):
                return

            # Get selected variant or default
            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            gate_type = variant['type'] if variant and 'type' in variant else 'stone'

            gate = LevelGate(
                int(self.preview_x),
                int(self.preview_y),
                gate_type,
                self.gate_required_level
            )

            self.gate_manager.add_gate(room_name, gate)

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    if not hasattr(room, 'level_gates'):
                        room.level_gates = []
                    # See chest placement above: gate_manager's list is
                    # aliased to room.level_gates, so avoid a duplicate append.
                    if gate not in room.level_gates:
                        room.level_gates.append(gate)

            if hasattr(self, 'on_gate_placed') and self.on_gate_placed:
                self.on_gate_placed(gate, room_name)

    def _draw_assign_highlight(self, screen, camera_x, camera_y):
        """Green pulsing outline around the hovered chest when an item is
        armed in the toolbar — signals "click to add loot", the assign
        counterpart to _draw_delete_highlight's red delete pulse."""
        chest = self.hovered_object
        screen_x = (chest.x * RENDER_SCALE) - camera_x
        screen_y = (chest.y * RENDER_SCALE) - camera_y
        scaled_width = int(chest.width * RENDER_SCALE)

        pulse = int(20 + 10 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
        pygame.draw.circle(screen, self.colors['success'],
                           (int(screen_x), int(screen_y)),
                           scaled_width // 2 + pulse, 3)

    def _draw_delete_highlight(self, screen, camera_x, camera_y):
        """Draw red outline around object that's about to be deleted"""
        obj = self.hovered_object
        obj_type = self.hovered_object_type

        if obj_type == 'spawn':
            screen_x = (obj.x * RENDER_SCALE) - camera_x
            screen_y = (obj.y * RENDER_SCALE) - camera_y
            scaled_width = int(obj.width * RENDER_SCALE)

            pulse = int(20 + 10 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
            pygame.draw.circle(screen, self.colors['delete'],
                               (int(screen_x), int(screen_y)),
                               scaled_width // 2 + pulse, 3)

        elif obj_type in ['collision', 'gate']:
            screen_x = (obj.x * RENDER_SCALE) - camera_x
            screen_y = (obj.y * RENDER_SCALE) - camera_y
            scaled_width = int(obj.width * RENDER_SCALE)
            scaled_height = int(obj.height * RENDER_SCALE)

            pulse = int(3 + 2 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
            pygame.draw.rect(screen, self.colors['delete'],
                             (int(screen_x), int(screen_y), int(scaled_width), int(scaled_height)),
                             pulse)

        elif obj_type in ['stone', 'transition']:
            screen_x = (obj.x * RENDER_SCALE) - camera_x
            screen_y = (obj.y * RENDER_SCALE) - camera_y
            scaled_width = int(obj.width * RENDER_SCALE)

            pulse = int(20 + 10 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
            pygame.draw.circle(screen, self.colors['delete'],
                               (int(screen_x), int(screen_y)),
                               scaled_width // 2 + pulse, 3)

        elif obj_type == 'decoration':
            # Bottom-anchored (see Decoration's class docstring), so the
            # pulse circle is centered on the trunk/base point rather than
            # the sprite's vertical middle like the stone/transition case
            # above.
            screen_x = (obj.x * RENDER_SCALE) - camera_x
            screen_y = (obj.y * RENDER_SCALE) - camera_y
            scaled_width = int(obj.width * RENDER_SCALE)

            pulse = int(20 + 10 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
            pygame.draw.circle(screen, self.colors['delete'],
                               (int(screen_x), int(screen_y)),
                               scaled_width // 2 + pulse, 3)

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

        new_rect = pygame.Rect(
            int(self.preview_collision.x),
            int(self.preview_collision.y),
            int(self.preview_collision.width),
            int(self.preview_collision.height),
        )

        # Collision walls are checked against every frame for the rest of
        # the room's life (see CollisionObjectManager / obstacles), so a
        # near-zero drag that lands almost exactly on top of an existing
        # wall would otherwise stack an invisible, redundant obstacle on
        # every click. Skip the append when the new rect is (almost)
        # entirely already covered by an existing wall — legitimate walls
        # placed edge-to-edge only partially overlap and are unaffected.
        if self.room_manager and new_rect.width > 0 and new_rect.height > 0:
            room = self.room_manager.get_room_by_name(room_name)
            existing_walls = getattr(room, 'collision_objects', None) if room else None
            if existing_walls:
                new_area = new_rect.width * new_rect.height
                for wall in existing_walls:
                    wall_rect = pygame.Rect(
                        int(getattr(wall, 'x', 0)), int(getattr(wall, 'y', 0)),
                        int(getattr(wall, 'width', 0)), int(getattr(wall, 'height', 0)),
                    )
                    overlap = new_rect.clip(wall_rect)
                    overlap_area = overlap.width * overlap.height
                    if new_area > 0 and overlap_area / new_area >= 0.9:
                        self.preview_collision = None
                        return

        collision_obj = CollisionObject(
            new_rect.x, new_rect.y, new_rect.width, new_rect.height, room_name
        )

        if self.room_manager:
            room = self.room_manager.get_room_by_name(room_name)
            if room:
                if not hasattr(room, 'collision_objects'):
                    room.collision_objects = []

                room.collision_objects.append(collision_obj)
                self.collision_manager.collision_objects[room_name] = room.collision_objects

        self.preview_collision = None

        if hasattr(self, 'on_collision_placed') and self.on_collision_placed:
            self.on_collision_placed(collision_obj, room_name)

    def _set_region_opacity_from_mouse_x(self, mouse_x, slider_rect):
        """Map a mouse x position over the opacity slider track to 0-100,
        clamped to the track bounds. Used for both the initial click (jump
        to that position) and subsequent drag motion."""
        frac = (mouse_x - slider_rect.left) / slider_rect.width
        self.region_opacity = max(0, min(100, round(frac * 100)))

    def _set_region_wave_amount_from_mouse_x(self, mouse_x, slider_rect):
        """Same mapping as _set_region_opacity_from_mouse_x, for the wave
        amount slider."""
        frac = (mouse_x - slider_rect.left) / slider_rect.width
        self.region_wave_amount = max(0, min(100, round(frac * 100)))

    def _region_color_hsv(self):
        """Current self.region_color as (h, s, v), each in 0-1."""
        r, g, b = self.region_color
        return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)

    def _set_region_hue_from_mouse_y(self, mouse_y, hue_rect):
        """Vertical hue strip: y position maps to hue 0-1. Keeps the
        current saturation/value, only the hue changes."""
        _, s, v = self._region_color_hsv()
        frac = (mouse_y - hue_rect.top) / hue_rect.height
        hue = max(0.0, min(1.0, frac))
        r, g, b = colorsys.hsv_to_rgb(hue, s, v)
        self.region_color = (round(r * 255), round(g * 255), round(b * 255))

    def _set_region_sv_from_mouse(self, mouse_pos, sv_rect):
        """Saturation/value square: x -> saturation 0-1, y -> value 1-0
        (top of the square is brightest). Keeps the current hue."""
        h, _, _ = self._region_color_hsv()
        s = max(0.0, min(1.0, (mouse_pos[0] - sv_rect.left) / sv_rect.width))
        v = max(0.0, min(1.0, 1 - (mouse_pos[1] - sv_rect.top) / sv_rect.height))
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        self.region_color = (round(r * 255), round(g * 255), round(b * 255))

    @staticmethod
    def _hsv_to_rgb_grid(hue, s_grid, v_grid):
        """Vectorized HSV->RGB for arrays of s/v sharing one fixed hue.
        Returns an (..., 3) float array in 0-1."""
        h6 = hue * 6.0
        sector = int(h6) % 6
        factor = 1 - abs(h6 % 2 - 1)

        c_grid = v_grid * s_grid
        x_grid = c_grid * factor
        m_grid = v_grid - c_grid
        zero = np.zeros_like(c_grid)

        order = {
            0: (c_grid, x_grid, zero),
            1: (x_grid, c_grid, zero),
            2: (zero, c_grid, x_grid),
            3: (zero, x_grid, c_grid),
            4: (x_grid, zero, c_grid),
            5: (c_grid, zero, x_grid),
        }[sector]
        return np.stack([order[0] + m_grid, order[1] + m_grid, order[2] + m_grid], axis=-1)

    def _get_hue_strip_surface(self, w, h):
        """Vertical rainbow gradient (full saturation/value, hue 0-1 top to
        bottom). Doesn't depend on the current color, so it's computed once
        and reused for the lifetime of the editor."""
        cache = self._hue_strip_cache
        if cache and cache[0] == (w, h):
            return cache[1]

        hues = np.linspace(0.0, 1.0, h, dtype=np.float32)
        column = np.zeros((h, 3), dtype=np.float32)
        for i, hue in enumerate(hues):
            column[i] = self._hsv_to_rgb_grid(float(hue), np.float32(1.0), np.float32(1.0))
        column = np.clip(column * 255, 0, 255).astype(np.uint8)

        arr = np.tile(column[np.newaxis, :, :], (w, 1, 1))  # (w, h, 3) for surfarray
        surf = pygame.surfarray.make_surface(arr)
        self._hue_strip_cache = ((w, h), surf)
        return surf

    def _get_sv_square_surface(self, hue, w, h):
        """Saturation (x, 0-1) / value (y, 1-0 top-to-bottom) gradient for a
        fixed hue. Recomputed only when the hue actually changes."""
        cache = self._sv_square_cache
        if cache and abs(cache[0][0] - hue) < 1e-6 and cache[0][1] == w and cache[0][2] == h:
            return cache[1]

        s = np.linspace(0.0, 1.0, w, dtype=np.float32)
        v = np.linspace(1.0, 0.0, h, dtype=np.float32)
        s_grid, v_grid = np.meshgrid(s, v, indexing='xy')  # shape (h, w)

        rgb = self._hsv_to_rgb_grid(hue, s_grid, v_grid)
        rgb = np.clip(rgb * 255, 0, 255).astype(np.uint8)
        rgb = np.transpose(rgb, (1, 0, 2))  # (w, h, 3) for surfarray

        surf = pygame.surfarray.make_surface(rgb)
        self._sv_square_cache = ((hue, w, h), surf)
        return surf

    def _finalize_animated_region_placement(self, room_name):
        """Finish placing a water/grass region after dragging"""
        if not self.preview_animated_region:
            return

        region = AnimatedRegion(
            int(self.preview_animated_region.x),
            int(self.preview_animated_region.y),
            int(self.preview_animated_region.width),
            int(self.preview_animated_region.height),
            room_name,
            self.preview_animated_region.region_type,
            self.region_opacity,
            self.region_wave_amount,
            self.region_seed,
            self.region_color,
            self.region_variant
        )

        if self.room_manager:
            room = self.room_manager.get_room_by_name(room_name)
            if room:
                if not hasattr(room, 'animated_regions'):
                    room.animated_regions = []

                room.animated_regions.append(region)
                self.animated_region_manager.regions[room_name] = room.animated_regions

        self.preview_animated_region = None

        if hasattr(self, 'on_animated_region_placed') and self.on_animated_region_placed:
            self.on_animated_region_placed(region, room_name)

    def _finalize_transition_placement(self, room_name):
        """Finish placing a room transition after dragging"""
        if not self.preview_transition:
            return

        self.pending_transition = self.preview_transition
        self.preview_transition = None

        available_rooms = self.room_manager.get_room_names() if self.room_manager else []
        self.transition_config.open(self.pending_transition, available_rooms, room_name)

    def _finalize_transition_spawn_placement(self):
        """Finalize the spawn point placement and return to source room"""
        if not self.pending_transition_for_spawn:
            return

        # Get the spawn dimensions
        spawn_width = getattr(self.pending_transition_for_spawn, 'spawn_width',
                              self.pending_transition_for_spawn.width)
        spawn_height = getattr(self.pending_transition_for_spawn, 'spawn_height',
                               self.pending_transition_for_spawn.height)

        # preview coords are world-center; convert to top-left for storage
        # so the transition controller can compute the center as spawn_x + spawn_width // 2.
        spawn_x = int(self.transition_spawn_preview_x - spawn_width // 2)
        spawn_y = int(self.transition_spawn_preview_y - spawn_height // 2)

        self.pending_transition_for_spawn.spawn_x = spawn_x
        self.pending_transition_for_spawn.spawn_y = spawn_y

        # Carry the source transition's dimensions so the destination spawn zone
        # matches the transition's footprint exactly.
        if hasattr(self.pending_transition_for_spawn, 'width'):
            self.pending_transition_for_spawn.spawn_width = self.pending_transition_for_spawn.width
            self.pending_transition_for_spawn.spawn_height = self.pending_transition_for_spawn.height

        source_room_name = self.transition_spawn_source_room
        if source_room_name:
            self.transition_manager.add_transition(source_room_name, self.pending_transition_for_spawn)

            if self.room_manager:
                room = self.room_manager.get_room_by_name(source_room_name)
                if room:
                    if not hasattr(room, 'room_transitions'):
                        room.room_transitions = []
                    room.room_transitions.append(self.pending_transition_for_spawn)

            if hasattr(self, 'on_transition_placed') and self.on_transition_placed:
                self.on_transition_placed(self.pending_transition_for_spawn, source_room_name)

        self.placing_transition_spawn = False
        self.transition_spawn_source_room = None
        self.pending_transition_for_spawn = None
        self.return_to_source_room = source_room_name

    def _always_run_marker_rect(self):
        """Single source of truth for where an Always Run trigger box marker
        will land, in world coordinates. Used by BOTH the hover preview and
        the actual placement call, so the two can never drift apart — this
        deliberately avoids self.preview_x/self.preview_y, which carry a
        +TILE_SIZE//2 'centered on tile' bias baked in for point objects
        (SavePoint, WorldMapObject, etc.) and aren't meant for rect-based
        objects like trigger boxes."""
        marker_size = 16
        if self.grid_snap:
            snap = self.grid_snap_size
            tile_left = int(self.mouse_world_x / snap) * snap
            tile_top = int(self.mouse_world_y / snap) * snap
            x = tile_left + snap // 2 - marker_size // 2
            y = tile_top + snap // 2 - marker_size // 2
        else:
            x = int(self.mouse_world_x - marker_size // 2)
            y = int(self.mouse_world_y - marker_size // 2)
        return x, y, marker_size, marker_size

    def _finalize_trigger_box_placement(self, room_name):
        """Finish placing a trigger box zone after dragging."""
        if not self.preview_trigger_box:
            return

        box_class = KeyTriggerBox if self.trigger_box_requires_key else OverlapTriggerBox
        box = box_class(
            box_id=self.trigger_box_id_text,
            x=int(self.preview_trigger_box.x),
            y=int(self.preview_trigger_box.y),
            width=int(self.preview_trigger_box.width),
            height=int(self.preview_trigger_box.height),
            once=self.trigger_box_once,
            conditions=list(self.trigger_box_conditions),
            actions=list(self.trigger_box_actions),
            always_run=self.trigger_box_always_run,
        )

        self.trigger_box_manager.add_box(room_name, box)

        self.preview_trigger_box = None

        if self.on_trigger_box_placed:
            self.on_trigger_box_placed(box, room_name)

    def _place_always_run_trigger_box(self, room_name):
        """Fast-path placement for Always Run boxes. Since position/size are
        irrelevant to firing, skip the drag-a-rectangle gesture entirely and
        drop a small fixed-size marker at the click location instead.

        Uses _always_run_marker_rect() — the exact same calculation the
        hover preview uses — so the marker always lands exactly where the
        preview showed it, with no possibility of the two drifting apart."""
        box_class = KeyTriggerBox if self.trigger_box_requires_key else OverlapTriggerBox
        x, y, w, h = self._always_run_marker_rect()
        box = box_class(
            box_id=self.trigger_box_id_text,
            x=x, y=y, width=w, height=h,
            once=self.trigger_box_once,
            conditions=list(self.trigger_box_conditions),
            actions=list(self.trigger_box_actions),
            always_run=True,
        )

        self.trigger_box_manager.add_box(room_name, box)

        if self.on_trigger_box_placed:
            self.on_trigger_box_placed(box, room_name)

    def _open_trigger_box_conditions_popup(self):
        """Open the generalized Event Editor for the trigger box about to be
        placed, pre-loaded with whatever conditions/actions are currently
        sticky (self.trigger_box_conditions/self.trigger_box_actions)."""
        if self.event_editor is None:
            return  # no flag_manager wired — button should be disabled anyway

        def _on_save(conditions, actions):
            self.trigger_box_conditions = conditions
            self.trigger_box_actions = actions

        self.event_editor.set_current_room(self.current_room_name)
        self.event_editor.open(
            title="Trigger Box Event",
            existing_conditions=self.trigger_box_conditions,
            existing_actions=self.trigger_box_actions,
            on_save=_on_save,
        )

    def _get_world_map_names(self):
        """Return a sorted list of world map stems from assets/world_maps/*.json."""
        save_dir = os.path.join('assets', 'world_maps')
        try:
            return sorted(f[:-5] for f in os.listdir(save_dir) if f.endswith('.json'))
        except FileNotFoundError:
            return []

    def handle_input(self, event, camera_x, camera_y, room_name):
        """Route pygame events to the appropriate sub-system.

        Priority order:
          1. Transition config dialog (blocks everything else while open)
          2. Flying-pad path editor (blocks everything else while open)
          3. Mouse-wheel palette scroll
          4. Right-click world deletion
          5. Left-click palette / world interaction
          6. Keyboard shortcuts (G = snap, H = grid, ESC / F3 = close)
        """
        if not self.active:
            return

        self.current_room_name = room_name
        # Deselect palette object while an item is armed so a world
        # click can only assign loot, never place a duplicate chest.
        if self._item_armed() and self.selected_object is not None:
            self.selected_object = None
            self.selected_variant = None
            self.showing_variants_for = None
        mouse_pos = pygame.mouse.get_pos()

        # Handle transition config dialog
        if self.transition_config.active:
            result = self.transition_config.handle_input(event)
            if result == 'save' and self.pending_transition:
                # Enter spawn placement mode for target room
                target_room_name = self.pending_transition.target_room

                if target_room_name and self.room_manager:
                    target_room = self.room_manager.get_room_by_name(target_room_name)
                    if target_room:
                        # Store the transition and source room info
                        self.placing_transition_spawn = True
                        self.transition_spawn_source_room = room_name
                        self.pending_transition_for_spawn = self.pending_transition
                        return

                self.pending_transition = None
            elif result == 'cancel':
                self.pending_transition = None
            return

        # Handle flying pad path editor
        if self.flying_pad_path_editor.active:
            result = self.flying_pad_path_editor.handle_input(
                event,
                int(self.mouse_world_x),
                int(self.mouse_world_y),
                self.room_manager.current_room.width if self.room_manager.current_room else WORLD_WIDTH,
                self.room_manager.current_room.height if self.room_manager.current_room else WORLD_HEIGHT
            )

            # Path editor finished — commit the flying pad and return to the original room
            if result and result.startswith('save:'):
                parts = result.split(':')
                return_room_name = parts[1] if len(parts) > 1 else ""
                should_create_return_pad = parts[2] == "return_pad" if len(parts) > 2 else False

                # Add flying pad to room
                if self.pending_flying_pad:
                    # Determine which room the pad actually belongs to
                    # (it should be the initial room where it was placed)
                    pad_room_name = return_room_name

                    self.flying_pad_manager.add_pad(pad_room_name, self.pending_flying_pad)

                    if self.room_manager:
                        room = self.room_manager.get_room_by_name(pad_room_name)
                        if room:
                            if not hasattr(room, 'flying_pads'):
                                room.flying_pads = []
                            room.flying_pads.append(self.pending_flying_pad)

                    if hasattr(self, 'on_flying_pad_placed') and self.on_flying_pad_placed:
                        self.on_flying_pad_placed(self.pending_flying_pad, pad_room_name)

                    # If the user ticked "create return pad", mirror the pad at the path's end point
                    if should_create_return_pad and len(self.pending_flying_pad.waypoints) > 0:
                        # Get the last waypoint position (path end)
                        last_wp = self.pending_flying_pad.waypoints[-1]

                        # Always use the last waypoint's actual position
                        return_pad_x = last_wp.x
                        return_pad_y = last_wp.y

                        # Determine which room the last waypoint is in
                        # by finding the last boundary waypoint before it
                        return_pad_room = pad_room_name  # Default to original room

                        for i in range(len(self.pending_flying_pad.waypoints) - 1, -1, -1):
                            wp = self.pending_flying_pad.waypoints[i]
                            if wp.is_boundary and wp.target_room:
                                # Found a boundary waypoint, so we're in its target room
                                return_pad_room = wp.target_room
                                break

                        # Create the return pad
                        from objects.flying_pad import FlyingPad
                        return_pad = FlyingPad(return_pad_x, return_pad_y, self.pending_flying_pad.pad_type)
                        return_pad.waypoints = self.pending_flying_pad.waypoints.copy()
                        return_pad.is_return_pad = True
                        return_pad.source_room = pad_room_name  # Set the source room for return flight

                        # Link the two pads by id so the game can reverse the flight path.
                        # We use Python's built-in id() as a simple session-scoped token;
                        # proper persistence should replace this with a stable UUID.
                        original_id = id(self.pending_flying_pad)
                        return_id = id(return_pad)
                        self.pending_flying_pad.linked_pad_id = return_id
                        return_pad.linked_pad_id = original_id

                        # Add return pad to its room
                        self.flying_pad_manager.add_pad(return_pad_room, return_pad)

                        if self.room_manager:
                            ret_room = self.room_manager.get_room_by_name(return_pad_room)
                            if ret_room:
                                if not hasattr(ret_room, 'flying_pads'):
                                    ret_room.flying_pads = []
                                ret_room.flying_pads.append(return_pad)

                self.pending_flying_pad = None
                self.placing_flying_pad = False

                # Return command to switch back to initial room
                return f'return_to_room:{return_room_name}'

            # Path editor cancelled — discard the pending pad and return to the original room
            elif result and result.startswith('cancel:'):
                return_room_name = result.split(':', 1)[1]
                self.pending_flying_pad = None
                self.placing_flying_pad = False
                # Return command to switch back to initial room
                return f'return_to_room:{return_room_name}'

            elif result and result.startswith('transition:'):
                # Room transition during path editing
                return result

            return

        # Handle nimbus cloud path editor
        if self.nimbus_cloud_path_editor.active:
            result = self.nimbus_cloud_path_editor.handle_input(
                event,
                int(self.mouse_world_x),
                int(self.mouse_world_y),
                self.room_manager.current_room.width if self.room_manager.current_room else WORLD_WIDTH,
                self.room_manager.current_room.height if self.room_manager.current_room else WORLD_HEIGHT
            )

            # Path editor finished — commit the nimbus cloud and return to the original room
            if result and result.startswith('save:'):
                parts = result.split(':')
                return_room_name = parts[1] if len(parts) > 1 else ""
                should_create_return_cloud = parts[2] == "return_pad" if len(parts) > 2 else False

                if self.pending_nimbus_cloud:
                    # Register under the placement room (origin_room), not
                    # whatever room the path editor reports on save (often
                    # the destination after a boundary spawn is placed).
                    cloud_room_name = (
                        getattr(self.pending_nimbus_cloud, 'origin_room', '') or return_room_name
                    )

                    self.nimbus_cloud_manager.add_cloud(cloud_room_name, self.pending_nimbus_cloud)

                    # NOTE: add_cloud() above already appended into
                    # nimbus_cloud_manager.nimbus_clouds[cloud_room_name].
                    # game.py's _sync_spawn_manager_with_rooms() aliases that
                    # manager list to be the SAME list object as
                    # room.nimbus_clouds, so appending to room.nimbus_clouds
                    # here too would add this cloud twice into one shared
                    # list (visually: two clouds stacked on top of each
                    # other). Point room.nimbus_clouds AT the manager's list
                    # instead — a no-op reassignment once already aliased,
                    # and what keeps a brand-new room's list wired up to the
                    # manager the first time a cloud is placed in it.
                    if self.room_manager:
                        room = self.room_manager.get_room_by_name(cloud_room_name)
                        if room:
                            room.nimbus_clouds = self.nimbus_cloud_manager.nimbus_clouds[cloud_room_name]

                    if hasattr(self, 'on_nimbus_cloud_placed') and self.on_nimbus_cloud_placed:
                        self.on_nimbus_cloud_placed(self.pending_nimbus_cloud, cloud_room_name)

                    # If the user ticked "create return cloud", mirror the cloud at the path's end point
                    if should_create_return_cloud and len(self.pending_nimbus_cloud.waypoints) > 0:
                        last_wp = self.pending_nimbus_cloud.waypoints[-1]
                        return_cloud_x = last_wp.x
                        return_cloud_y = last_wp.y
                        return_cloud_room = cloud_room_name
                        for i in range(len(self.pending_nimbus_cloud.waypoints) - 1, -1, -1):
                            wp = self.pending_nimbus_cloud.waypoints[i]
                            if wp.is_boundary and wp.target_room:
                                return_cloud_room = wp.target_room
                                if getattr(wp, 'spawn_x', None) is not None and getattr(wp, 'spawn_y', None) is not None:
                                    return_cloud_x = wp.spawn_x
                                    return_cloud_y = wp.spawn_y
                                break

                        return_cloud = NimbusCloud(return_cloud_x, return_cloud_y, self.pending_nimbus_cloud.cloud_type)
                        return_cloud.waypoints = self.pending_nimbus_cloud.waypoints.copy()
                        return_cloud.is_return_pad = True
                        return_cloud.source_room = cloud_room_name
                        return_cloud.origin_room = return_cloud_room
                        return_cloud.current_room = return_cloud_room

                        original_id = id(self.pending_nimbus_cloud)
                        return_id = id(return_cloud)
                        self.pending_nimbus_cloud.linked_pad_id = return_id
                        return_cloud.linked_pad_id = original_id

                        self.nimbus_cloud_manager.add_cloud(return_cloud_room, return_cloud)

                        # Same reasoning as above — reference, don't re-append.
                        if self.room_manager:
                            ret_room = self.room_manager.get_room_by_name(return_cloud_room)
                            if ret_room:
                                ret_room.nimbus_clouds = self.nimbus_cloud_manager.nimbus_clouds[return_cloud_room]

                self.pending_nimbus_cloud = None
                self.placing_nimbus_cloud = False

                return f'return_to_room:{return_room_name}'

            elif result and result.startswith('cancel:'):
                return_room_name = result.split(':', 1)[1]
                self.pending_nimbus_cloud = None
                self.placing_nimbus_cloud = False
                return f'return_to_room:{return_room_name}'

            elif result and result.startswith('transition:'):
                return result

            return

        # Handle the Event Editor popup (blocks everything else while open)
        if self.event_editor is not None and self.event_editor.active:
            self.event_editor.handle_input(event)
            return

        # Scroll through palette
        if event.type == pygame.MOUSEWHEEL:
            # If the cursor is over the variant selector row, scroll through
            # variants horizontally instead of scrolling the palette beneath it.
            variant_selector_rect = self.ui_rects.get('variant_selector_rect')
            if (variant_selector_rect and variant_selector_rect.collidepoint(mouse_pos)
                    and isinstance(self.selected_object, dict)
                    and self.selected_object.get('has_variants', False)):
                variants = self.selected_object.get('variants', [])
                if variants:
                    self.variant_scroll -= event.y
                    self.variant_scroll = max(0, min(self.variant_scroll, max(0, len(variants) - 1)))
                return

            if self._is_in_palette(mouse_pos[0], mouse_pos[1]):
                self.scroll_offset -= event.y * 30
                self.scroll_offset = max(0, min(self.scroll_offset, self.max_scroll))

        # Right-click to delete objects
        if event.type == pygame.MOUSEBUTTONDOWN:
            # Panel show/hide toggle — checked first so it always fires
            if event.button == 1 and self._panel_toggle_rect().collidepoint(mouse_pos):
                self.palette_visible = not self.palette_visible
                return

            if event.button == 3:
                if not self._is_in_palette(mouse_pos[0], mouse_pos[1]):
                    if self.hovered_object and self.hovered_object_type:
                        self._delete_object(self.hovered_object, self.hovered_object_type)
                        self.hovered_object = None
                        self.hovered_object_type = None
                return

            # Left-click: place an object or interact with palette/UI
            if event.button == 1:
                # Edit an EXISTING trigger box's own conditions/actions in
                # place. Without this there was no way to configure a box
                # after it's been placed — the palette panel's Event button
                # (below) only bakes the sticky conditions/actions into a
                # brand new box at creation time via _finalize_trigger_box_placement.
                if (not self.placing_trigger_box
                        and not self._is_in_palette(mouse_pos[0], mouse_pos[1])
                        and self.hovered_object_type == 'trigger_box'
                        and self.hovered_object is not None
                        and self.event_editor is not None):
                    self.event_editor.set_current_room(self.current_room_name)
                    self.hovered_object.open_event_editor(self.event_editor)
                    return

                # Handle transition spawn placement mode
                if self.placing_transition_spawn:
                    self._finalize_transition_spawn_placement()
                    return

                # Check if clicking on level input box
                if self._is_level_input_clicked(mouse_pos):
                    self.gate_level_input_active = True
                    return

                # Check if clicking on the door "Permanent" checkbox
                if self._is_door_permanent_checkbox_clicked(mouse_pos):
                    return

                # Check if clicking on the door sound picker or its Preview button
                if self._is_door_sound_ui_clicked(mouse_pos):
                    return

                # Assigning loot to an existing chest via the toolbar's
                # Items panel selection takes priority over normal
                # placement/selection — a click in the world while an item
                # is armed always means "drop this item in that chest".
                # If the click misses every chest, still consume the world
                # click: falling through to normal placement would stamp a
                # brand-new empty object (e.g. another chest) because
                # selected_object stays set from the Objects palette.
                # Palette / settings-panel clicks must still pass through
                # so the designer can switch tools or clear selection.
                if self._item_armed() and not self._is_in_palette(mouse_pos[0], mouse_pos[1]):
                    self._try_assign_chest_loot(mouse_pos)
                    return

                # Handle trigger box property clicks
                if (self.selected_object and isinstance(self.selected_object, dict)
                        and self.selected_object.get('is_trigger_box', False)):

                    id_rect = self.ui_rects.get('trigger_box_id_rect')
                    if id_rect and id_rect.collidepoint(mouse_pos):
                        self.trigger_box_id_input_active = True
                        return

                    once_rect = self.ui_rects.get('trigger_box_once_rect')
                    if once_rect and once_rect.collidepoint(mouse_pos):
                        self.trigger_box_once = not self.trigger_box_once
                        return

                    key_rect = self.ui_rects.get('trigger_box_requires_key_rect')
                    if key_rect and key_rect.collidepoint(mouse_pos):
                        self.trigger_box_requires_key = not self.trigger_box_requires_key
                        return

                    always_run_rect = self.ui_rects.get('trigger_box_always_run_rect')
                    if always_run_rect and always_run_rect.collidepoint(mouse_pos):
                        self.trigger_box_always_run = not self.trigger_box_always_run
                        return

                    # Event button — opens the picker (needs a FlagManager
                    # wired via set_flag_manager; disabled otherwise)
                    event_rect = self.ui_rects.get('trigger_box_event_btn')
                    if event_rect and event_rect.collidepoint(mouse_pos) and self.flag_manager is not None:
                        self._open_trigger_box_conditions_popup()
                        return

                    self.trigger_box_id_input_active = False

                # Handle world map dropdown clicks
                if (self.selected_object and isinstance(self.selected_object, dict)
                        and self.selected_object.get('object_type') == 'world_map_object'):
                    current_variant = self._get_current_variant(self.selected_object)
                    if current_variant and current_variant.get('type') in ('world_map', 'world_map_sign'):
                        # Dropdown button — toggle open/closed
                        wm_btn = self.ui_rects.get('world_map_dropdown_btn')
                        if wm_btn and wm_btn.collidepoint(mouse_pos):
                            self.world_map_dropdown_open = not self.world_map_dropdown_open
                            if self.world_map_dropdown_open:
                                self.world_map_dropdown_names = self._get_world_map_names()
                            return

                        # Item inside open dropdown list
                        if self.world_map_dropdown_open:
                            for item_rect, name in self.ui_rects.get('world_map_dropdown_items', []):
                                if item_rect.collidepoint(mouse_pos):
                                    self.world_map_name_text = name
                                    self.world_map_dropdown_open = False
                                    return
                            # Click outside list — close without selecting
                            self.world_map_dropdown_open = False
                            return

                # Handle water/grass opacity slider
                if (self.selected_object and isinstance(self.selected_object, dict)
                        and self.selected_object.get('is_animated_region', False)):
                    slider_rect = self.ui_rects.get('region_opacity_slider')
                    if slider_rect and slider_rect.collidepoint(mouse_pos):
                        self._region_opacity_dragging = True
                        self._set_region_opacity_from_mouse_x(mouse_pos[0], slider_rect)
                        return

                    if REGION_STYLES.get(self.selected_object.get('region_type'), {}).get('mode', 'patch') == 'tile':
                        for i, variant_rect in enumerate(self.ui_rects.get('region_variant_rects', [])):
                            if variant_rect.collidepoint(mouse_pos):
                                self.region_variant = i
                                return

                    if self.selected_object.get('region_type') in ('water',):
                        wave_rect = self.ui_rects.get('region_wave_slider')
                        if wave_rect and wave_rect.collidepoint(mouse_pos):
                            self._region_wave_dragging = True
                            self._set_region_wave_amount_from_mouse_x(mouse_pos[0], wave_rect)
                            return

                        seed_rect = self.ui_rects.get('region_seed_input')
                        if seed_rect and seed_rect.collidepoint(mouse_pos):
                            self.region_seed_input_active = True
                            self.region_seed_text = str(self.region_seed)
                            return

                        reroll_rect = self.ui_rects.get('region_seed_reroll')
                        if reroll_rect and reroll_rect.collidepoint(mouse_pos):
                            import random
                            self.region_seed = random.randint(0, 999999)
                            self.region_seed_text = str(self.region_seed)
                            return

                    region_style = REGION_STYLES.get(self.selected_object.get('region_type'), {})
                    if region_style.get('mode', 'patch') == 'patch':
                        reset_rect = self.ui_rects.get('region_color_reset')
                        if reset_rect and reset_rect.collidepoint(mouse_pos):
                            self.region_color = (255, 255, 255)
                            return

                        sv_rect = self.ui_rects.get('region_sv_square')
                        if sv_rect and sv_rect.collidepoint(mouse_pos):
                            self._region_sv_dragging = True
                            self._set_region_sv_from_mouse(mouse_pos, sv_rect)
                            return

                        hue_rect = self.ui_rects.get('region_hue_strip')
                        if hue_rect and hue_rect.collidepoint(mouse_pos):
                            self._region_hue_dragging = True
                            self._set_region_hue_from_mouse_y(mouse_pos[1], hue_rect)
                            return

                # Variant scroll-arrow clicks (only present when the row overflows)
                left_arrow = self.ui_rects.get('variant_scroll_left')
                if left_arrow and left_arrow.collidepoint(mouse_pos):
                    self.variant_scroll -= 1
                    return
                right_arrow = self.ui_rects.get('variant_scroll_right')
                if right_arrow and right_arrow.collidepoint(mouse_pos):
                    self.variant_scroll += 1
                    return

                # Check if clicking on variant selector
                if self._is_variant_selector_clicked(mouse_pos):
                    return

                # Deactivate input if clicking elsewhere
                if self.gate_level_input_active:
                    self.gate_level_input_active = False
                if self.region_seed_input_active:
                    self.region_seed_input_active = False
                    try:
                        self.region_seed = int(self.region_seed_text)
                    except ValueError:
                        self.region_seed_text = str(self.region_seed)

                # Finish placing collision wall if we're in the middle of it
                if self.placing_collision:
                    self._finalize_collision_placement(room_name)
                    self.placing_collision = False
                    return

                # Finish placing water/grass region if we're in the middle of it
                if self.placing_animated_region:
                    self._finalize_animated_region_placement(room_name)
                    self.placing_animated_region = False
                    return

                # Finish placing trigger box if we're in the middle of it
                if self.placing_trigger_box:
                    self._finalize_trigger_box_placement(room_name)
                    self.placing_trigger_box = False
                    return

                # Finish placing transition if we're in the middle of it
                if self.placing_transition:
                    self._finalize_transition_placement(room_name)
                    self.placing_transition = False
                    return

                # Click in palette to select object
                if self._is_in_palette(mouse_pos[0], mouse_pos[1]):
                    self._handle_palette_click(mouse_pos)
                else:
                    # Click in world to place object — blocked while an item
                    # is armed for chest-loot assignment (see _item_armed).
                    if self._item_armed():
                        return
                    if self.selected_object and not self._is_object_disabled(self.selected_object):
                        if self.selected_object.get('is_collision', False):
                            self.placing_collision = True
                            self.collision_start_x = self.preview_x
                            self.collision_start_y = self.preview_y
                        elif self.selected_object.get('is_animated_region', False):
                            self.placing_animated_region = True
                            self.current_region_type = self.selected_object.get('region_type', 'water')
                            self.animated_region_start_x = self.preview_x
                            self.animated_region_start_y = self.preview_y
                        elif self.selected_object.get('is_trigger_box', False):
                            if self.trigger_box_always_run:
                                # Position/size don't matter for firing, so
                                # skip the drag-a-rectangle gesture and drop
                                # a small fixed-size marker on a single click.
                                self._place_always_run_trigger_box(room_name)
                            else:
                                self.placing_trigger_box = True
                                self.trigger_box_start_x = self.preview_x
                                self.trigger_box_start_y = self.preview_y
                        elif self.selected_object.get('is_transition', False):
                            self.placing_transition = True
                            self.transition_start_x = self.preview_x
                            self.transition_start_y = self.preview_y
                        else:
                            self._place_object(camera_x, camera_y, room_name)

        # Dragging the water/grass opacity slider
        if event.type == pygame.MOUSEMOTION and self._region_opacity_dragging:
            slider_rect = self.ui_rects.get('region_opacity_slider')
            if slider_rect:
                self._set_region_opacity_from_mouse_x(mouse_pos[0], slider_rect)
            return

        # Dragging the water wave-amount slider
        if event.type == pygame.MOUSEMOTION and self._region_wave_dragging:
            wave_rect = self.ui_rects.get('region_wave_slider')
            if wave_rect:
                self._set_region_wave_amount_from_mouse_x(mouse_pos[0], wave_rect)
            return

        # Dragging the water color saturation/value square
        if event.type == pygame.MOUSEMOTION and self._region_sv_dragging:
            sv_rect = self.ui_rects.get('region_sv_square')
            if sv_rect:
                self._set_region_sv_from_mouse(mouse_pos, sv_rect)
            return

        # Dragging the water color hue strip
        if event.type == pygame.MOUSEMOTION and self._region_hue_dragging:
            hue_rect = self.ui_rects.get('region_hue_strip')
            if hue_rect:
                self._set_region_hue_from_mouse_y(mouse_pos[1], hue_rect)
            return

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._region_opacity_dragging = False
            self._region_wave_dragging = False
            self._region_sv_dragging = False
            self._region_hue_dragging = False

        # Keyboard shortcuts
        if event.type == pygame.KEYDOWN:
            if self.gate_level_input_active:
                if event.key == pygame.K_BACKSPACE:
                    self.gate_level_text = self.gate_level_text[:-1]
                    if not self.gate_level_text:
                        self.gate_level_text = "0"
                elif event.key == pygame.K_RETURN or event.key == pygame.K_ESCAPE:
                    self.gate_level_input_active = False
                    try:
                        level = int(self.gate_level_text)
                        self.gate_required_level = max(1, min(999, level))
                        self.gate_level_text = str(self.gate_required_level)
                    except ValueError:
                        self.gate_level_text = str(self.gate_required_level)
                elif event.unicode.isdigit():
                    if self.gate_level_text == "0":
                        self.gate_level_text = event.unicode
                    elif len(self.gate_level_text) < 3:
                        self.gate_level_text += event.unicode
                    else:
                        self.gate_level_text = event.unicode
                return

            if self.trigger_box_id_input_active:
                if event.key == pygame.K_BACKSPACE:
                    self.trigger_box_id_text = self.trigger_box_id_text[:-1]
                elif event.key == pygame.K_RETURN or event.key == pygame.K_ESCAPE:
                    self.trigger_box_id_input_active = False
                elif event.unicode and (event.unicode.isalnum() or event.unicode in ('_', '-')):
                    if len(self.trigger_box_id_text) < 40:
                        self.trigger_box_id_text += event.unicode
                return

            if self.region_seed_input_active:
                if event.key == pygame.K_BACKSPACE:
                    self.region_seed_text = self.region_seed_text[:-1]
                    if not self.region_seed_text:
                        self.region_seed_text = "0"
                elif event.key == pygame.K_RETURN or event.key == pygame.K_ESCAPE:
                    self.region_seed_input_active = False
                    try:
                        self.region_seed = int(self.region_seed_text)
                        self.region_seed_text = str(self.region_seed)
                    except ValueError:
                        self.region_seed_text = str(self.region_seed)
                elif event.unicode.isdigit():
                    if self.region_seed_text == "0":
                        self.region_seed_text = event.unicode
                    elif len(self.region_seed_text) < 6:
                        self.region_seed_text += event.unicode
                return

            if event.key == pygame.K_g:
                self._toggle_grid_snap()
            elif event.key == pygame.K_h:
                self.show_grid = not self.show_grid
            elif event.key == pygame.K_ESCAPE or event.key == pygame.K_F3:
                if self.placing_collision:
                    self.placing_collision = False
                    self.preview_collision = None
                elif self.placing_animated_region:
                    self.placing_animated_region = False
                    self.preview_animated_region = None
                elif self.placing_trigger_box:
                    self.placing_trigger_box = False
                    self.preview_trigger_box = None
                elif self.placing_transition:
                    self.placing_transition = False
                    self.preview_transition = None
                elif self.placing_transition_spawn:
                    self.placing_transition_spawn = False
                    self.transition_spawn_source_room = None
                    self.pending_transition_for_spawn = None
                elif self.selected_object is not None:
                    # Deselect first — a second ESC will then close the editor.
                    # This also prevents the room editor's re-activation guard from
                    # immediately re-opening the panel on the next frame.
                    self.selected_object = None
                    self.selected_variant = None
                    self.hovered_object = None
                    self.hovered_object_type = None
                else:
                    self.active = False

    def update(self, dt, mouse_pos, camera_x, camera_y):
        """Tick editor state: animate category tabs, update world-mouse coords,
        rebuild drag-resize previews, and compute palette scroll limits."""
        if not self.active:
            return

        self.anim_timer += dt

        for category in self.categories.keys():
            target = 1.0 if category == self.current_category else 0.0
            self.category_hover[category] += (target - self.category_hover[category]) * dt * 10

        self.mouse_world_x = (mouse_pos[0] + camera_x) / RENDER_SCALE
        self.mouse_world_y = (mouse_pos[1] + camera_y) / RENDER_SCALE

        if self.placing_transition_spawn:
            if self.grid_snap and self.pending_transition_for_spawn:
                spawn_width = getattr(self.pending_transition_for_spawn, 'spawn_width',
                                      getattr(self.pending_transition_for_spawn, 'width', 32))
                spawn_height = getattr(self.pending_transition_for_spawn, 'spawn_height',
                                       getattr(self.pending_transition_for_spawn, 'height', 32))
                # Snap top-left to tile grid, then convert to center for the preview system
                snap = self.grid_snap_size
                grid_x = int(self.mouse_world_x / snap) * snap
                grid_y = int(self.mouse_world_y / snap) * snap
                self.transition_spawn_preview_x = grid_x + spawn_width // 2
                self.transition_spawn_preview_y = grid_y + spawn_height // 2
            else:
                self.transition_spawn_preview_x = self.mouse_world_x
                self.transition_spawn_preview_y = self.mouse_world_y
            return

        if not self._is_in_palette(mouse_pos[0], mouse_pos[1]):
            self.hovered_object, self.hovered_object_type = self._check_object_at_position(
                self.mouse_world_x, self.mouse_world_y
            )

        # Guard: selected_object is set to a raw dict — ensure it hasn't been
        # replaced with a live game object before reading dict keys.
        if self.selected_object and isinstance(self.selected_object, dict):
            if self.grid_snap:
                snap = self.grid_snap_size
                grid_x = int(self.mouse_world_x / snap) * snap + snap // 2
                grid_y = int(self.mouse_world_y / snap) * snap + snap // 2
                self.preview_x = grid_x
                self.preview_y = grid_y
            else:
                self.preview_x = self.mouse_world_x
                self.preview_y = self.mouse_world_y
        else:
            # Reset preview position if no valid object is selected
            self.preview_x = 0
            self.preview_y = 0

        # Each draggable zone recalculates its preview rect every frame by
        # snapping or free-dragging from the stored anchor to the current mouse pos.
        if self.placing_collision:
            if self.grid_snap:
                snap = self.grid_snap_size
                snap_start_x = int(self.collision_start_x / snap) * snap
                snap_start_y = int(self.collision_start_y / snap) * snap
                snap_end_x = int(self.mouse_world_x / snap) * snap
                snap_end_y = int(self.mouse_world_y / snap) * snap

                min_x = min(snap_start_x, snap_end_x)
                min_y = min(snap_start_y, snap_end_y)
                max_x = max(snap_start_x, snap_end_x)
                max_y = max(snap_start_y, snap_end_y)

                width = max(snap, max_x - min_x + snap)
                height = max(snap, max_y - min_y + snap)
            else:
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

        if self.placing_animated_region:
            if self.grid_snap:
                snap = self.grid_snap_size
                snap_start_x = int(self.animated_region_start_x / snap) * snap
                snap_start_y = int(self.animated_region_start_y / snap) * snap
                snap_end_x = int(self.mouse_world_x / snap) * snap
                snap_end_y = int(self.mouse_world_y / snap) * snap

                min_x = min(snap_start_x, snap_end_x)
                min_y = min(snap_start_y, snap_end_y)
                max_x = max(snap_start_x, snap_end_x)
                max_y = max(snap_start_y, snap_end_y)

                width = max(snap, max_x - min_x + snap)
                height = max(snap, max_y - min_y + snap)
            else:
                end_x = self.mouse_world_x
                end_y = self.mouse_world_y

                min_x = min(self.animated_region_start_x, end_x)
                min_y = min(self.animated_region_start_y, end_y)
                max_x = max(self.animated_region_start_x, end_x)
                max_y = max(self.animated_region_start_y, end_y)

                width = max(16, max_x - min_x)
                height = max(16, max_y - min_y)

            self.preview_animated_region = AnimatedRegion(
                int(min_x),
                int(min_y),
                int(width),
                int(height),
                self.current_room_name,
                self.current_region_type,
                self.region_opacity,
                self.region_wave_amount,
                self.region_seed,
                self.region_color,
                self.region_variant
            )

        if self.placing_trigger_box:
            if self.grid_snap:
                snap = self.grid_snap_size
                snap_start_x = int(self.trigger_box_start_x / snap) * snap
                snap_start_y = int(self.trigger_box_start_y / snap) * snap
                snap_end_x = int(self.mouse_world_x / snap) * snap
                snap_end_y = int(self.mouse_world_y / snap) * snap

                min_x = min(snap_start_x, snap_end_x)
                min_y = min(snap_start_y, snap_end_y)
                max_x = max(snap_start_x, snap_end_x)
                max_y = max(snap_start_y, snap_end_y)

                width = max(snap, max_x - min_x + snap)
                height = max(snap, max_y - min_y + snap)
            else:
                end_x = self.mouse_world_x
                end_y = self.mouse_world_y

                min_x = min(self.trigger_box_start_x, end_x)
                min_y = min(self.trigger_box_start_y, end_y)
                max_x = max(self.trigger_box_start_x, end_x)
                max_y = max(self.trigger_box_start_y, end_y)

                width = max(16, max_x - min_x)
                height = max(16, max_y - min_y)

            box_class = KeyTriggerBox if self.trigger_box_requires_key else OverlapTriggerBox
            self.preview_trigger_box = box_class(
                box_id=self.trigger_box_id_text,
                x=int(min_x), y=int(min_y),
                width=int(width), height=int(height),
                once=self.trigger_box_once,
                always_run=self.trigger_box_always_run,
            )

        if self.placing_transition:
            if self.grid_snap:
                snap = self.grid_snap_size
                snap_start_x = int(self.transition_start_x / snap) * snap
                snap_start_y = int(self.transition_start_y / snap) * snap
                snap_end_x = int(self.mouse_world_x / snap) * snap
                snap_end_y = int(self.mouse_world_y / snap) * snap

                min_x = min(snap_start_x, snap_end_x)
                min_y = min(snap_start_y, snap_end_y)
                max_x = max(snap_start_x, snap_end_x)
                max_y = max(snap_start_y, snap_end_y)

                width = max(snap, max_x - min_x + snap)
                height = max(snap, max_y - min_y + snap)
            else:
                end_x = self.mouse_world_x
                end_y = self.mouse_world_y

                min_x = min(self.transition_start_x, end_x)
                min_y = min(self.transition_start_y, end_y)
                max_x = max(self.transition_start_x, end_x)
                max_y = max(self.transition_start_y, end_y)

                width = max(32, max_x - min_x)
                height = max(32, max_y - min_y)

            self.preview_transition = RoomTransition(
                int(min_x),
                int(min_y),
                int(width),
                int(height)
            )

        self.hover_object = None
        self.hover_variant_index = -1

        # Update flying pad path editor while it's open
        if self.flying_pad_path_editor.active:
            self.flying_pad_path_editor.update(
                dt,
                int(self.mouse_world_x),
                int(self.mouse_world_y)
            )

        # Update nimbus cloud path editor while it's open
        if self.nimbus_cloud_path_editor.active:
            self.nimbus_cloud_path_editor.update(
                dt,
                int(self.mouse_world_x),
                int(self.mouse_world_y)
            )

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

            # Check variant selector hover
            for i, rect_data in enumerate(self.ui_rects.get('variant_rects', [])):
                if rect_data['rect'].collidepoint(mouse_pos):
                    self.hover_variant_index = i
                    break

        objects = self.categories[self.current_category]
        rows = (len(objects) + self.items_per_row - 1) // self.items_per_row
        total_height = rows * (self.item_size + 10)
        category_section_height = len(self.categories) * 40 + 20
        available_height = self.palette_height - (45 + category_section_height + 200)  # Increased for variant selector
        self.max_scroll = max(0, total_height - available_height)

    def draw_preview(self, screen, camera_x, camera_y):
        """Draw the ghost preview of whatever is about to be placed.

        Handles five distinct placement modes in order:
          - Flying-pad path editor overlay
          - Transition spawn-area placement
          - Collision-wall drag preview
          - Room-transition drag preview
          - Trigger-box drag preview
          - Standard single-object ghost sprite
        """
        if not self.active:
            return

        # Don't show object preview when path editor is active
        if self.flying_pad_path_editor.active:
            self.flying_pad_path_editor.draw(
                screen,
                self._make_camera(camera_x, camera_y),
                RENDER_SCALE
            )
            return

        # Don't show object preview when the nimbus cloud path editor is
        # active either — note it ignores the live camera_x/camera_y passed
        # in here and draws against its own locked frame instead.
        if self.nimbus_cloud_path_editor.active:
            self.nimbus_cloud_path_editor.draw(
                screen,
                self._make_camera(camera_x, camera_y),
                RENDER_SCALE
            )
            return

        if self.placing_transition_spawn:
            # Get transition dimensions from the pending transition
            if self.pending_transition_for_spawn:
                spawn_width = getattr(self.pending_transition_for_spawn, 'spawn_width',
                                      getattr(self.pending_transition_for_spawn, 'width', 32))
                spawn_height = getattr(self.pending_transition_for_spawn, 'spawn_height',
                                       getattr(self.pending_transition_for_spawn, 'height', 32))
            else:
                spawn_width = 32
                spawn_height = 32

            # Calculate the top-left position (preview_x/y is the center)
            preview_left = self.transition_spawn_preview_x - spawn_width // 2
            preview_top = self.transition_spawn_preview_y - spawn_height // 2

            screen_x = (preview_left * RENDER_SCALE) - camera_x
            screen_y = (preview_top * RENDER_SCALE) - camera_y
            screen_width = spawn_width * RENDER_SCALE
            screen_height = spawn_height * RENDER_SCALE

            font = pygame.font.Font(None, 32)
            text = font.render("Click to place transition spawn area", True, (255, 255, 0))
            text_bg = pygame.Surface((text.get_width() + 20, text.get_height() + 10), pygame.SRCALPHA)
            text_bg.fill((0, 0, 0, 180))
            bg_x = (screen.get_width() - text.get_width()) // 2 - 10
            screen.blit(text_bg, (bg_x, 10))
            screen.blit(text, ((screen.get_width() - text.get_width()) // 2, 15))

            # Draw the rectangular transition spawn area
            rect = pygame.Rect(int(screen_x), int(screen_y),
                               int(screen_width), int(screen_height))

            # Semi-transparent blue fill
            fill_surface = pygame.Surface((int(screen_width), int(screen_height)), pygame.SRCALPHA)
            fill_surface.fill((100, 200, 255, 100))
            screen.blit(fill_surface, (int(screen_x), int(screen_y)))

            # Border
            pygame.draw.rect(screen, (0, 150, 255), rect, 3)

            # Diagonal lines pattern (like the transition object)
            line_surface = pygame.Surface((int(screen_width), int(screen_height)), pygame.SRCALPHA)
            spacing = 16 * RENDER_SCALE
            for i in range(int(-screen_height), int(screen_width + screen_height), int(spacing)):
                start_x = i
                start_y = 0
                end_x = i + screen_height
                end_y = screen_height
                pygame.draw.line(line_surface, (0, 120, 200, 120),
                                 (start_x, start_y), (end_x, end_y), 1)
            screen.blit(line_surface, (int(screen_x), int(screen_y)))

            # Center crosshair
            center_x = screen_x + screen_width // 2
            center_y = screen_y + screen_height // 2

            pygame.draw.line(screen, (255, 255, 0),
                             (center_x - 15, center_y),
                             (center_x + 15, center_y), 2)
            pygame.draw.line(screen, (255, 255, 0),
                             (center_x, center_y - 15),
                             (center_x, center_y + 15), 2)

            # Draw dimensions text
            if screen_width > 50 and screen_height > 30:
                dims_font = pygame.font.Font(None, 18)
                dims_text = f"{spawn_width} x {spawn_height}"
                dims_surface = dims_font.render(dims_text, True, (255, 255, 255))
                dims_rect = dims_surface.get_rect(center=(center_x, center_y))

                # Text background
                bg_rect = dims_rect.inflate(8, 4)
                bg_surface = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
                bg_surface.fill((0, 0, 0, 180))
                screen.blit(bg_surface, bg_rect.topleft)
                screen.blit(dims_surface, dims_rect)

            return

        if self.placing_collision and self.preview_collision:
            draw_collision_object(screen, self.preview_collision, camera_x, camera_y,
                                  RENDER_SCALE, dev_mode=True, selected=True)
            return

        if self.placing_animated_region and self.preview_animated_region:
            draw_animated_region(screen, self.preview_animated_region, camera_x, camera_y,
                                 RENDER_SCALE, dev_mode=True, selected=True)
            return

        if self.placing_transition and self.preview_transition:
            self.preview_transition.draw(screen,
                                         self._make_camera(camera_x, camera_y),
                                         RENDER_SCALE, dev_mode=True, selected=True)
            return

        if self.placing_trigger_box and self.preview_trigger_box:
            draw_trigger_box(screen, self.preview_trigger_box,
                             camera_x, camera_y, RENDER_SCALE,
                             dev_mode=True, selected=True)
            return

        if (self.selected_object and isinstance(self.selected_object, dict)
                and self.selected_object.get('is_trigger_box', False)
                and self.trigger_box_always_run
                and not self._is_in_palette(*pygame.mouse.get_pos())):
            # Always Run boxes place instantly on click, so this preview
            # stays on screen continuously (to support stamping down
            # several markers in a row). Drawing it via draw_trigger_box
            # with the same solid styling as a placed box made it read as
            # "duplicated" — a real box plus a full-opacity look-alike
            # chasing the cursor. Render a translucent ghost icon instead,
            # matching the convention every other placeable object uses,
            # so it's unambiguous which one is actually placed.
            mx, my, mw, mh = self._always_run_marker_rect()
            icon_sprite = self.selected_object.get('sprite')
            if icon_sprite:
                scaled_w = max(1, int(mw * RENDER_SCALE))
                scaled_h = max(1, int(mh * RENDER_SCALE))
                ghost = pygame.transform.scale(icon_sprite, (scaled_w, scaled_h)).copy()
                ghost.set_alpha(120)
                ghost_screen_x = int(mx * RENDER_SCALE - camera_x)
                ghost_screen_y = int(my * RENDER_SCALE - camera_y)
                screen.blit(ghost, (ghost_screen_x, ghost_screen_y))
            return

        if (self.hovered_object and self.hovered_object_type == 'chest'
                and self._item_armed()):
            # An item is armed in the toolbar's Items panel — clicking this
            # chest assigns loot rather than deleting it / placing a new
            # object, so show a distinct (green, not red) highlight. Do not
            # require selected_object to be None: the Objects palette often
            # still has Chest (or anything) selected after arming an item.
            self._draw_assign_highlight(screen, camera_x, camera_y)
            return

        # While an item is armed, world clicks never place — suppress the
        # selected-object placement ghost so it doesn't look like another
        # chest is about to drop.
        if self._item_armed():
            return

        if self.hovered_object and self.hovered_object_type and not self.selected_object:
            self._draw_delete_highlight(screen, camera_x, camera_y)
            return

        # Guard: nothing to preview if no valid palette item is selected
        if not self.selected_object or not isinstance(self.selected_object, dict):
            return

        if self._is_object_disabled(self.selected_object):
            return

        mouse_pos = pygame.mouse.get_pos()
        if self._is_in_palette(mouse_pos[0], mouse_pos[1]):
            return

        screen_x = (self.preview_x * RENDER_SCALE) - camera_x
        screen_y = (self.preview_y * RENDER_SCALE) - camera_y

        if self.grid_snap:
            snap = self.grid_snap_size
            grid_screen_x = int(self.mouse_world_x / snap) * snap * RENDER_SCALE - camera_x
            grid_screen_y = int(self.mouse_world_y / snap) * snap * RENDER_SCALE - camera_y

            guide_surf = pygame.Surface((snap * RENDER_SCALE, snap * RENDER_SCALE), pygame.SRCALPHA)
            pygame.draw.rect(guide_surf, self.colors['snap_guide'],
                             (0, 0, snap * RENDER_SCALE, snap * RENDER_SCALE), 2)

            center_x = snap * RENDER_SCALE // 2
            center_y = snap * RENDER_SCALE // 2
            pygame.draw.line(guide_surf, self.colors['snap_guide'],
                             (center_x - 5, center_y), (center_x + 5, center_y), 2)
            pygame.draw.line(guide_surf, self.colors['snap_guide'],
                             (center_x, center_y - 5), (center_x, center_y + 5), 2)
            screen.blit(guide_surf, (int(grid_screen_x), int(grid_screen_y)))

        # Get the sprite to preview (from selected variant or default)
        if self.selected_object.get('has_variants', False):
            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            if variant:
                obj_sprite = variant.get('sprite')
                scaled_width = int(variant.get('width', 32) * RENDER_SCALE)
                scaled_height = int(variant.get('height', 32) * RENDER_SCALE)
            else:
                # Fallback if variant is None
                obj_sprite = self.selected_object.get('sprite')
                scaled_width = int(self.selected_object.get('width', 32) * RENDER_SCALE)
                scaled_height = int(self.selected_object.get('height', 32) * RENDER_SCALE)
        else:
            obj_sprite = self.selected_object.get('sprite')
            scaled_width = int(self.selected_object.get('width', 32) * RENDER_SCALE)
            scaled_height = int(self.selected_object.get('height', 32) * RENDER_SCALE)

        if obj_sprite:
            scaled_sprite = pygame.transform.scale(obj_sprite, (scaled_width, scaled_height))

            preview_surf = scaled_sprite.copy()
            preview_surf.set_alpha(100)

            # Match the anchor convention used by WorldMapObject.draw() and
            # Decoration.get_render_info(): 'world_map_sign' and every
            # decoration (tree, etc.) use midbottom — (preview_x, preview_y)
            # is the base/trunk point — everything else uses center.
            is_bottom_anchor = (
                    (self.selected_object.get('object_type') == 'world_map_object'
                     and (self.selected_variant or {}).get('type') == 'world_map_sign')
                    or self.selected_object.get('object_type') == 'decoration'
            )
            preview_x = int(screen_x - scaled_width // 2)
            preview_y = int(screen_y - scaled_height) if is_bottom_anchor else int(screen_y - scaled_height // 2)

            screen.blit(preview_surf, (preview_x, preview_y))

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

    def draw_animated_regions(self, screen, camera_x, camera_y):
        """Draw all water/grass regions in the current room (editor overlay only)"""
        if not self.current_room_name:
            return

        regions = self.animated_region_manager.get_regions(self.current_room_name)

        for region in regions:
            draw_animated_region(screen, region, camera_x, camera_y,
                                 RENDER_SCALE, dev_mode=True, selected=False)

    def _make_camera(self, camera_x, camera_y):
        """Lightweight camera-like object used by draw methods that expect a .x/.y camera."""

        class _Camera:
            def __init__(self, x, y):
                self.x = x
                self.y = y

        return _Camera(camera_x, camera_y)

    def draw_room_transitions(self, screen, camera_x, camera_y):
        """Draw all room transitions in the current room"""
        if not self.current_room_name:
            return

        transitions = self.transition_manager.get_transitions(self.current_room_name)

        temp_camera = self._make_camera(camera_x, camera_y)

        for transition in transitions:
            if transition.active:
                transition.draw(screen, temp_camera, RENDER_SCALE, dev_mode=True, selected=False)

    def draw_level_gates(self, screen, camera_x, camera_y, colors):
        """Draw level gates in the current room"""
        if not self.current_room_name:
            return

        gates = self.gate_manager.get_gates(self.current_room_name)

        temp_camera = self._make_camera(camera_x, camera_y)

        for gate in gates:
            if gate.active:
                gate.draw(screen, temp_camera, colors)

    def draw_doors(self, screen, camera_x, camera_y, colors=None):
        """Draw doors in the current room (editor preview — always shows the
        closed frame regardless of is_open, since there's no player to be
        near in the editor)."""
        if not self.current_room_name:
            return

        temp_camera = self._make_camera(camera_x, camera_y)

        for door in self.door_manager.get_doors(self.current_room_name):
            door.draw(screen, temp_camera, colors)

    def _get_chest_loot_icon(self, item_id):
        """Same convention/cache as game.py's _get_chest_item_icon —
        assets/sprites/items/{item_id}.png, or None if it's not on disk.
        Used to badge chests that already have loot assigned (see
        draw_chests) so designers can see it at a glance in the editor."""
        if not hasattr(self, '_chest_loot_icon_cache'):
            self._chest_loot_icon_cache = {}
        if item_id not in self._chest_loot_icon_cache:
            path = os.path.join('assets', 'sprites', 'items', f'{item_id}.png')
            try:
                self._chest_loot_icon_cache[item_id] = pygame.image.load(path).convert_alpha()
            except (pygame.error, OSError, FileNotFoundError):
                self._chest_loot_icon_cache[item_id] = None
        return self._chest_loot_icon_cache[item_id]

    def draw_chests(self, screen, camera_x, camera_y, colors=None):
        """Draw chests in the current room (editor preview — always shows
        the closed frame, since there's no player to open one in the
        editor). Chests that already have loot assigned get a small icon
        badge above them (with an x-qty label if more than one) so
        designers can see what's inside without re-selecting anything —
        loot itself is assigned by clicking a chest with an item armed in
        the toolbar's Items panel, see ObjectEditor._try_assign_chest_loot."""
        if not self.current_room_name:
            return

        temp_camera = self._make_camera(camera_x, camera_y)

        for chest in self.chest_manager.get_chests(self.current_room_name):
            chest.draw(screen, temp_camera, colors)
            if chest.item_id:
                self._draw_chest_loot_badge(screen, chest, camera_x, camera_y)

    def _draw_chest_loot_badge(self, screen, chest, camera_x, camera_y):
        """Small floating icon (+ 'xN' if qty > 1) above a chest that
        already has loot, editor-only decoration drawn on top of the
        chest's own sprite."""
        screen_x = int(chest.x * RENDER_SCALE - camera_x)
        top_y = int((chest.y - chest.height / 2) * RENDER_SCALE - camera_y)
        badge_size = 20
        badge_rect = pygame.Rect(0, 0, badge_size, badge_size)
        badge_rect.midbottom = (screen_x, top_y - 4)

        pygame.draw.rect(screen, (25, 25, 40), badge_rect, border_radius=4)
        pygame.draw.rect(screen, self.colors['accent'], badge_rect, 1, border_radius=4)

        icon = self._get_chest_loot_icon(chest.item_id)
        if icon:
            scale = min((badge_size - 4) / icon.get_width(), (badge_size - 4) / icon.get_height())
            icon = pygame.transform.scale(
                icon, (max(1, int(icon.get_width() * scale)), max(1, int(icon.get_height() * scale)))
            )
            screen.blit(icon, icon.get_rect(center=badge_rect.center))

        if chest.item_qty > 1:
            qty_surf = self.font_small.render(f'x{chest.item_qty}', True, self.colors['accent'])
            qty_rect = qty_surf.get_rect(midtop=(badge_rect.centerx, badge_rect.bottom + 1))
            pygame.draw.rect(screen, (25, 25, 40), qty_rect.inflate(4, 2))
            screen.blit(qty_surf, qty_rect)

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

        # Don't show palette when path editor is active
        if self.flying_pad_path_editor.active:
            return
        if self.nimbus_cloud_path_editor.active:
            return

        # Update hover state and always draw the toggle tab
        mx, my = pygame.mouse.get_pos()
        self._hover_panel_toggle = self._panel_toggle_rect().collidepoint(mx, my)
        self._draw_panel_toggle_tab(screen)

        # Always draw the transition config dialog — it must be visible even
        # when the palette panel is hidden (user closed panel to place freely).
        self.transition_config.draw(screen)

        # Note: the Event Editor popup is drawn at the very end of this
        # method (after the settings panel etc.), not here — otherwise the
        # rest of the palette paints right over it every frame.

        if not self.palette_visible:
            return

        palette_rect = pygame.Rect(self.palette_x, self.palette_y, self.palette_width, self.palette_height)
        palette_bg = pygame.Surface((self.palette_width, self.palette_height), pygame.SRCALPHA)
        palette_bg.fill(self.colors['bg_transparent'])
        screen.blit(palette_bg, (self.palette_x, self.palette_y))
        pygame.draw.rect(screen, self.colors['accent'], palette_rect, 2)

        y_pos = self.palette_y + 10

        title = self.font_medium.render("Object Palette", True, self.colors['text'])
        screen.blit(title, (self.palette_x + 20, y_pos))
        y_pos += 35

        for i, category in enumerate(self.categories.keys()):
            is_selected = category == self.current_category
            hover = self.category_hover[category]

            category_rect = pygame.Rect(
                self.palette_x + self.palette_padding,
                y_pos,
                self.palette_width - self.palette_padding * 2,
                30
            )

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

        pygame.draw.line(screen, self.colors['accent'],
                         (self.palette_x + self.palette_padding, y_pos),
                         (self.palette_x + self.palette_width - self.palette_padding, y_pos), 1)
        y_pos += 10

        objects_start_y = y_pos
        objects_content_height = self.palette_height - (y_pos - self.palette_y) - 200

        clip_rect = pygame.Rect(self.palette_x, objects_start_y, self.palette_width, objects_content_height)
        screen.set_clip(clip_rect)

        objects = self.categories[self.current_category]
        current_y = objects_start_y - self.scroll_offset

        for i, obj in enumerate(objects):
            row = i // self.items_per_row
            col = i % self.items_per_row

            item_x = self.palette_x + self.palette_padding + col * (self.item_size + 10)
            item_y = current_y + row * (self.item_size + 10)

            if item_y + self.item_size < objects_start_y or item_y > objects_start_y + objects_content_height:
                continue

            self._draw_object_item(screen, obj, item_x, item_y)

        screen.set_clip(None)

        self._draw_variant_selector(screen)
        self._draw_settings_panel(screen)

        # Drawn last so its dark overlay + window actually cover the whole
        # palette/settings panel instead of being painted over by them.
        if self.event_editor is not None:
            self.event_editor.draw(screen)

    def _draw_object_item(self, screen, obj, x, y):
        """Draw a single object in the palette"""
        item_rect = pygame.Rect(x, y, self.item_size, self.item_size)

        is_selected = self.selected_object == obj
        is_hover = self.hover_object == obj
        is_disabled = self._is_object_disabled(obj)

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

        if obj['sprite']:
            src = obj['sprite']
            sw, sh = src.get_size()
            max_dim = self.item_size - 8  # 8px padding on each axis
            scale = min(max_dim / sw, max_dim / sh)
            scaled = pygame.transform.scale(src, (max(1, int(sw * scale)), max(1, int(sh * scale))))
            if is_disabled:
                scaled.fill((100, 100, 100, 150), special_flags=pygame.BLEND_RGBA_MULT)
            screen.blit(scaled, scaled.get_rect(center=item_rect.center))

        name_color = self.colors['disabled'] if is_disabled else self.colors['text_dim']
        name_text = self.font_small.render(obj['name'], True, name_color)
        name_rect = name_text.get_rect(centerx=item_rect.centerx, top=item_rect.bottom + 2)
        screen.blit(name_text, name_rect)

        if is_disabled and obj.get('is_spawn', False):
            placed_text = self.font_small.render("PLACED", True, self.colors['disabled'])
            placed_rect = placed_text.get_rect(centerx=item_rect.centerx, centery=item_rect.centery)

            bg_surf = pygame.Surface((placed_rect.width + 8, placed_rect.height + 4), pygame.SRCALPHA)
            bg_surf.fill((0, 0, 0, 180))
            screen.blit(bg_surf, (placed_rect.x - 4, placed_rect.y - 2))

            screen.blit(placed_text, placed_rect)

        # Show variant indicator
        if obj.get('has_variants', False) and is_hover:
            variant_text = self.font_small.render("Click to select variant", True, self.colors['accent'])
            variant_rect = variant_text.get_rect(centerx=item_rect.centerx, top=item_rect.bottom + 18)

            bg_surf = pygame.Surface((variant_rect.width + 6, variant_rect.height + 2), pygame.SRCALPHA)
            bg_surf.fill((0, 0, 0, 180))
            screen.blit(bg_surf, (variant_rect.x - 3, variant_rect.y - 1))

            screen.blit(variant_text, variant_rect)

    def _draw_variant_selector(self, screen):
        """Draw variant selection row when an object with variants is selected"""
        if not self.selected_object or not isinstance(self.selected_object, dict):
            self.ui_rects['variant_selector_rect'] = None
            return

        if not self.selected_object.get('has_variants', False):
            self.ui_rects['variant_selector_rect'] = None
            return

        variants = self.selected_object.get('variants', [])
        if not variants:
            self.ui_rects['variant_selector_rect'] = None
            return

        # Position above settings panel
        selector_height = 100
        selector_y = self.palette_y + self.palette_height - 250
        selector_x = self.palette_x

        # Background
        selector_rect = pygame.Rect(selector_x, selector_y, self.palette_width, selector_height)
        pygame.draw.rect(screen, self.colors['variant_bg'], selector_rect)
        pygame.draw.line(screen, self.colors['accent'],
                         (selector_x, selector_y),
                         (selector_x + self.palette_width, selector_y), 2)
        self.ui_rects['variant_selector_rect'] = selector_rect

        # Title
        title_text = self.font_small.render("Select Variant:", True, self.colors['text_dim'])
        screen.blit(title_text, (selector_x + self.palette_padding, selector_y + 5))

        # Draw variant options
        variant_size = 50
        variant_spacing = 10
        arrow_width = 20  # reserved on each side when the list needs scrolling
        start_y = selector_y + 25

        current_variant = self.selected_variant or self._get_current_variant(self.selected_object)

        # How many variants fit at once — recomputed every frame since the
        # palette can be resized. Reserve room for scroll arrows only once
        # we actually know the full row won't fit.
        usable_width = self.palette_width - self.palette_padding * 2
        slot_width = variant_size + variant_spacing
        visible_count = max(1, usable_width // slot_width)
        needs_scroll = len(variants) > visible_count
        if needs_scroll:
            # Re-derive visible_count with arrow space carved out on both sides.
            usable_width -= arrow_width * 2
            visible_count = max(1, usable_width // slot_width)

        max_variant_scroll = max(0, len(variants) - visible_count)
        self.variant_scroll = max(0, min(self.variant_scroll, max_variant_scroll))

        start_x = selector_x + self.palette_padding + (arrow_width if needs_scroll else 0)
        visible_variants = variants[self.variant_scroll:self.variant_scroll + visible_count]

        self.ui_rects['variant_rects'] = []
        self.ui_rects['variant_scroll_left'] = None
        self.ui_rects['variant_scroll_right'] = None

        for i, variant in enumerate(visible_variants):
            variant_x = start_x + i * slot_width
            variant_rect = pygame.Rect(variant_x, start_y, variant_size, variant_size)

            is_selected = (current_variant == variant)
            is_hover = (self.hover_variant_index == i)

            # Background
            if is_selected:
                bg_color = self.colors['variant_selected']
            elif is_hover:
                bg_color = self.colors['panel_light']
            else:
                bg_color = self.colors['panel']

            pygame.draw.rect(screen, bg_color, variant_rect, border_radius=3)

            # Border
            border_color = self.colors['accent'] if is_selected else self.colors['grid']
            border_width = 2 if is_selected else 1
            pygame.draw.rect(screen, border_color, variant_rect, border_width, border_radius=3)

            # Sprite
            if variant.get('sprite'):
                sprite = variant['sprite'].copy()
                sprite_rect = sprite.get_rect(center=variant_rect.center)
                screen.blit(sprite, sprite_rect)

            # Label
            label_text = self.font_small.render(variant['name'], True, self.colors['text_dim'])
            label_rect = label_text.get_rect(centerx=variant_rect.centerx, top=variant_rect.bottom + 2)
            screen.blit(label_text, label_rect)

            # Store rect for click detection
            self.ui_rects['variant_rects'].append({
                'rect': variant_rect,
                'variant': variant
            })

        if needs_scroll:
            arrow_y = start_y + variant_size // 2
            left_rect = pygame.Rect(selector_x + self.palette_padding, start_y, arrow_width, variant_size)
            right_rect = pygame.Rect(
                selector_x + self.palette_width - self.palette_padding - arrow_width,
                start_y, arrow_width, variant_size,
            )

            left_active = self.variant_scroll > 0
            right_active = self.variant_scroll < max_variant_scroll
            left_color = self.colors['text'] if left_active else self.colors['text_dim']
            right_color = self.colors['text'] if right_active else self.colors['text_dim']

            pygame.draw.polygon(screen, left_color, [
                (left_rect.right - 4, arrow_y - 8),
                (left_rect.left + 4, arrow_y),
                (left_rect.right - 4, arrow_y + 8),
            ])
            pygame.draw.polygon(screen, right_color, [
                (right_rect.left + 4, arrow_y - 8),
                (right_rect.right - 4, arrow_y),
                (right_rect.left + 4, arrow_y + 8),
            ])

            if left_active:
                self.ui_rects['variant_scroll_left'] = left_rect
            if right_active:
                self.ui_rects['variant_scroll_right'] = right_rect

            # Position indicator, e.g. "3-8 / 14"
            count_text = self.font_small.render(
                f"{self.variant_scroll + 1}-{min(self.variant_scroll + visible_count, len(variants))} / {len(variants)}",
                True, self.colors['text_dim'],
            )
            count_rect = count_text.get_rect(
                right=selector_x + self.palette_width - self.palette_padding,
                top=selector_y + 5,
            )
            screen.blit(count_text, count_rect)

    def draw_flying_pads(self, screen, camera_x, camera_y, colors):
        """Draw flying pads in the current room"""
        if not self.current_room_name:
            return

        pads = self.flying_pad_manager.get_pads(self.current_room_name)

        temp_camera = self._make_camera(camera_x, camera_y)

        for pad in pads:
            if pad.active:
                pad.draw(screen, temp_camera, colors, RENDER_SCALE)
                # Draw path preview in editor
                pad.draw_path_preview(screen, temp_camera, RENDER_SCALE)

    def draw_nimbus_clouds(self, screen, camera_x, camera_y, colors):
        """Draw nimbus clouds in the current room"""
        if not self.current_room_name:
            return

        clouds = self.nimbus_cloud_manager.get_clouds(self.current_room_name)

        temp_camera = self._make_camera(camera_x, camera_y)

        for cloud in clouds:
            if cloud.active:
                cloud.draw(screen, temp_camera, colors, RENDER_SCALE)
                # Draw path preview in editor
                cloud.draw_path_preview(screen, temp_camera, RENDER_SCALE)

    def draw_save_points(self, screen, camera_x, camera_y, colors):
        """Draw save points in the current room"""
        if not self.current_room_name:
            return

        save_points = self.save_point_manager.get_save_points(self.current_room_name)

        temp_camera = self._make_camera(camera_x, camera_y)

        for save_point in save_points:
            if save_point.active:
                save_point.draw(screen, temp_camera, colors)

    def draw_world_map_objects(self, screen, camera_x, camera_y, colors):
        """Draw world map objects in the current room."""
        if not self.current_room_name:
            return
        temp_camera = self._make_camera(camera_x, camera_y)
        for obj in self.world_map_manager.get_objects(self.current_room_name):
            if obj.active:
                obj.draw(screen, temp_camera, colors)

    def draw_trigger_boxes(self, screen, camera_x, camera_y):
        """Draw all trigger box zones in the current room (dev mode only)."""
        if not self.current_room_name:
            return

        for box in self.trigger_box_manager.get_boxes(self.current_room_name):
            draw_trigger_box(
                screen, box, camera_x, camera_y, RENDER_SCALE,
                dev_mode=True, selected=False
            )

    def _settings_panel_content_height(self):
        """Compute how tall _draw_settings_panel's content actually is, so the
        panel (and therefore the clickable area for its controls) can grow to
        fit instead of clipping/pushing rows below the palette bounds."""
        height = 10  # top padding (y_pos = panel_y + 10)
        height += 25  # Grid Snap row
        height += 25  # Show Grid row

        obj = self.selected_object
        if obj and isinstance(obj, dict):
            if obj.get('object_type') == 'level_gate':
                height += 30

            if obj.get('object_type') == 'door':
                height += 30

            if obj.get('is_trigger_box', False):
                height += 30  # Box ID row
                height += 30  # Once toggle row
                height += 30  # Requires Key toggle row

            if obj.get('object_type') == 'world_map_object':
                current_variant = self._get_current_variant(obj)
                if current_variant and current_variant.get('type') in ('world_map', 'world_map_sign'):
                    height += 30

            if obj.get('is_animated_region', False):
                height += 20 + 14 + 16  # Opacity label + slider + gap
                region_type = obj.get('region_type')
                if region_type in ('water',):
                    height += 20 + 14 + 16  # Wave Amount label + slider + gap
                    height += 30  # Seed input + reroll row
                if REGION_STYLES.get(region_type, {}).get('mode', 'patch') == 'patch':
                    height += 26  # Color label + swatch + reset row
                    height += 90 + 10  # SV square / hue strip + trailing gap
                region_style = REGION_STYLES.get(region_type, {})
                if region_style.get('mode', 'patch') == 'tile':
                    # A single-frame tile sheet (grid_rows omitted or 1 —
                    # e.g. a one-off, non-square sprite with nothing to
                    # pick between) has no variant to choose, so the picker
                    # itself is skipped entirely rather than showing one
                    # useless button.
                    num_variants = max(1, region_style.get('grid_rows', 1))
                    if num_variants > 1:
                        # Variant slots are square, sized to fill the row
                        # (same math as the draw code), so their height
                        # scales with palette width rather than a fixed
                        # constant.
                        btn_gap = 6
                        slot_w = (self.palette_width - self.palette_padding * 2
                                  - btn_gap * (num_variants - 1)) // num_variants
                        height += 20 + slot_w + 16  # Variant label + thumbnail row + gap

        height += 10  # bottom padding
        return height

    def _draw_settings_panel(self, screen):
        """Draw controls and settings at the bottom of the palette"""
        panel_height = max(160, self._settings_panel_content_height())
        panel_y = self.palette_y + self.palette_height - panel_height

        panel_rect = pygame.Rect(self.palette_x, panel_y, self.palette_width, panel_height)
        pygame.draw.rect(screen, self.colors['bg'], panel_rect)
        pygame.draw.line(screen, self.colors['accent'],
                         (self.palette_x, panel_y),
                         (self.palette_x + self.palette_width, panel_y), 2)

        y_pos = panel_y + 10

        snap_text = f"Grid Snap: {self.grid_snap_size}px" if self.grid_snap else "Grid Snap: OFF"
        snap_color = self.colors['success'] if self.grid_snap else self.colors['text_dim']
        snap_surf = self.font_medium.render(snap_text, True, snap_color)
        screen.blit(snap_surf, (self.palette_x + self.palette_padding, y_pos))

        hint = self.font_small.render("(Press G)", True, self.colors['text_dim'])
        screen.blit(hint, (self.palette_x + self.palette_padding + 120, y_pos + 3))
        y_pos += 25

        grid_text = f"Show Grid: {'ON' if self.show_grid else 'OFF'}"
        grid_color = self.colors['success'] if self.show_grid else self.colors['text_dim']
        grid_surf = self.font_medium.render(grid_text, True, grid_color)
        screen.blit(grid_surf, (self.palette_x + self.palette_padding, y_pos))

        hint = self.font_small.render("(Press H)", True, self.colors['text_dim'])
        screen.blit(hint, (self.palette_x + self.palette_padding + 120, y_pos + 3))
        y_pos += 25

        if self.selected_object and isinstance(self.selected_object, dict) and self.selected_object.get(
                'object_type') == 'level_gate':
            level_label = self.font_medium.render("Gate Level Req:", True, self.colors['text'])
            screen.blit(level_label, (self.palette_x + self.palette_padding, y_pos))

            input_x = self.palette_x + self.palette_padding + 135
            input_y = y_pos - 3
            input_width = 60
            input_height = 25

            input_rect = pygame.Rect(input_x, input_y, input_width, input_height)
            input_bg_color = self.colors['input_active'] if self.gate_level_input_active else self.colors['input_bg']
            pygame.draw.rect(screen, input_bg_color, input_rect)
            pygame.draw.rect(screen, self.colors['accent'] if self.gate_level_input_active else self.colors['grid'],
                             input_rect, 2)

            display_text = self.gate_level_text if self.gate_level_input_active else str(self.gate_required_level)
            text_surf = self.font_medium.render(display_text, True, self.colors['text'])
            text_rect = text_surf.get_rect(center=input_rect.center)
            screen.blit(text_surf, text_rect)

            if self.gate_level_input_active:
                if int(pygame.time.get_ticks() / 500) % 2 == 0:
                    cursor_x = text_rect.right + 3
                    cursor_y = input_rect.centery
                    pygame.draw.line(screen, self.colors['text'],
                                     (cursor_x, cursor_y - 10),
                                     (cursor_x, cursor_y + 10), 2)

            y_pos += 30

        if self.selected_object and isinstance(self.selected_object, dict) and self.selected_object.get(
                'object_type') == 'door':
            label = self.font_medium.render("Permanent:", True, self.colors['text'])
            screen.blit(label, (self.palette_x + self.palette_padding, y_pos))

            box_size = 20
            box_x = self.palette_x + self.palette_padding + 100
            box_y = y_pos - 2
            box_rect = pygame.Rect(box_x, box_y, box_size, box_size)
            self.ui_rects['door_permanent_checkbox'] = box_rect

            box_bg = self.colors['success'] if self.door_permanent else self.colors['input_bg']
            pygame.draw.rect(screen, box_bg, box_rect)
            pygame.draw.rect(screen, self.colors['accent'] if self.door_permanent else self.colors['grid'],
                             box_rect, 2)

            if self.door_permanent:
                pygame.draw.line(screen, (255, 255, 255),
                                 (box_x + 4, box_y + 10), (box_x + 8, box_y + 15), 2)
                pygame.draw.line(screen, (255, 255, 255),
                                 (box_x + 8, box_y + 15), (box_x + 16, box_y + 5), 2)

            hint = self.font_small.render("(stays open once opened)", True, self.colors['text_dim'])
            screen.blit(hint, (box_x + box_size + 8, y_pos + 3))

            y_pos += 30

            # ── Door sound picker ───────────────────────────────────────────
            sound_label = self.font_medium.render("Sound:", True, self.colors['text'])
            screen.blit(sound_label, (self.palette_x + self.palette_padding, y_pos))

            btn_h = 22
            btn_gap = 4
            btn_x = self.palette_x + self.palette_padding + 100
            btn_y = y_pos - 2

            door_sound_buttons = []
            for name in self.door_sound_options:
                # Short label — e.g. 'door1' -> '1' — so a row of these reads
                # like numbered tabs rather than repeating "door" each time.
                short_label = name[4:] if name.lower().startswith('door') else name
                label_surf = self.font_small.render(short_label, True, self.colors['text'])
                btn_w = max(24, label_surf.get_width() + 12)
                btn_rect = pygame.Rect(btn_x, btn_y, btn_w, btn_h)

                is_selected = (name == self.door_sound_text)
                bg = self.colors['variant_selected'] if is_selected else self.colors['input_bg']
                pygame.draw.rect(screen, bg, btn_rect)
                pygame.draw.rect(screen, self.colors['accent'] if is_selected else self.colors['grid'],
                                 btn_rect, 2)
                screen.blit(label_surf, label_surf.get_rect(center=btn_rect.center))

                door_sound_buttons.append((btn_rect, name))
                btn_x += btn_w + btn_gap

            self.ui_rects['door_sound_buttons'] = door_sound_buttons

            # Preview button — plays whatever's currently selected. Drawn as a
            # small play triangle + label rather than a unicode ▶ glyph, since
            # the default pygame font doesn't reliably have that character.
            preview_rect = pygame.Rect(btn_x + 6, btn_y, 78, btn_h)
            preview_hover = preview_rect.collidepoint(pygame.mouse.get_pos())
            preview_bg = self.colors['input_active'] if preview_hover else self.colors['input_bg']
            pygame.draw.rect(screen, preview_bg, preview_rect)
            pygame.draw.rect(screen, self.colors['accent'], preview_rect, 2)

            tri_x = preview_rect.x + 8
            tri_y = preview_rect.centery
            pygame.draw.polygon(screen, self.colors['accent'], [
                (tri_x, tri_y - 5), (tri_x, tri_y + 5), (tri_x + 8, tri_y)
            ])
            preview_label = self.font_small.render("Preview", True, self.colors['text'])
            screen.blit(preview_label, (tri_x + 14, preview_rect.centery - preview_label.get_height() // 2))
            self.ui_rects['door_sound_preview_btn'] = preview_rect

            y_pos += 30

        if self.selected_object and isinstance(self.selected_object, dict) and self.selected_object.get(
                'is_trigger_box', False):
            id_label = self.font_medium.render("Box ID:", True, self.colors['text'])
            screen.blit(id_label, (self.palette_x + self.palette_padding, y_pos))

            btn_x = self.palette_x + self.palette_padding + 120
            id_rect = pygame.Rect(btn_x, y_pos - 3, 200, 25)
            id_bg = self.colors['input_active'] if self.trigger_box_id_input_active else self.colors['input_bg']
            pygame.draw.rect(screen, id_bg, id_rect)
            pygame.draw.rect(screen,
                             self.colors['accent'] if self.trigger_box_id_input_active else self.colors['grid'],
                             id_rect, 2)

            display_label = self.trigger_box_id_text if self.trigger_box_id_text else '<box id>'
            id_text_surf = self.font_small.render(display_label, True, self.colors['text'])
            id_clip = pygame.Rect(id_rect.x + 4, id_rect.y, id_rect.w - 8, id_rect.h)
            screen.set_clip(id_clip)
            screen.blit(id_text_surf, (id_rect.x + 4, id_rect.y + 6))
            screen.set_clip(None)

            self.ui_rects['trigger_box_id_rect'] = id_rect

            y_pos += 30

            once_label = self.font_medium.render("Once:", True, self.colors['text'])
            screen.blit(once_label, (self.palette_x + self.palette_padding, y_pos))

            once_rect = pygame.Rect(btn_x, y_pos - 3, 60, 22)
            once_color = self.colors['success'] if self.trigger_box_once else self.colors['panel']
            pygame.draw.rect(screen, once_color, once_rect, border_radius=4)
            pygame.draw.rect(screen, self.colors['accent'], once_rect, 2, border_radius=4)
            once_text = self.font_small.render('ON' if self.trigger_box_once else 'OFF', True, self.colors['text'])
            screen.blit(once_text, once_text.get_rect(center=once_rect.center))
            self.ui_rects['trigger_box_once_rect'] = once_rect

            y_pos += 30

            key_label = self.font_medium.render("Requires Key:", True, self.colors['text'])
            screen.blit(key_label, (self.palette_x + self.palette_padding, y_pos))

            key_rect = pygame.Rect(btn_x, y_pos - 3, 60, 22)
            key_color = self.colors['success'] if self.trigger_box_requires_key else self.colors['panel']
            pygame.draw.rect(screen, key_color, key_rect, border_radius=4)
            pygame.draw.rect(screen, self.colors['accent'], key_rect, 2, border_radius=4)
            key_text = self.font_small.render('ON' if self.trigger_box_requires_key else 'OFF', True,
                                              self.colors['text'])
            screen.blit(key_text, key_text.get_rect(center=key_rect.center))
            self.ui_rects['trigger_box_requires_key_rect'] = key_rect

            y_pos += 30

            always_run_label = self.font_medium.render("Always Run:", True, self.colors['text'])
            screen.blit(always_run_label, (self.palette_x + self.palette_padding, y_pos))

            always_run_rect = pygame.Rect(btn_x, y_pos - 3, 60, 22)
            always_run_color = self.colors['success'] if self.trigger_box_always_run else self.colors['panel']
            pygame.draw.rect(screen, always_run_color, always_run_rect, border_radius=4)
            pygame.draw.rect(screen, self.colors['accent'], always_run_rect, 2, border_radius=4)
            always_run_text = self.font_small.render('ON' if self.trigger_box_always_run else 'OFF', True,
                                                      self.colors['text'])
            screen.blit(always_run_text, always_run_text.get_rect(center=always_run_rect.center))
            self.ui_rects['trigger_box_always_run_rect'] = always_run_rect

            if self.trigger_box_always_run:
                hint_surf = self.font_small.render(
                    "Fires passively — position/size ignored, single-click to place",
                    True, self.colors['text_dark'])
                screen.blit(hint_surf, (self.palette_x + self.palette_padding, y_pos + 24))
                y_pos += 18

            y_pos += 30

            # Event button — grey/disabled until a FlagManager is wired
            event_label = self.font_medium.render("Event:", True, self.colors['text'])
            screen.blit(event_label, (self.palette_x + self.palette_padding, y_pos))

            event_btn_rect = pygame.Rect(btn_x, y_pos - 3, 160, 25)
            event_cond_count = len(self.trigger_box_conditions)
            event_action_count = len(self.trigger_box_actions)

            if self.flag_manager is None:
                pygame.draw.rect(screen, self.colors['panel'], event_btn_rect)
                pygame.draw.rect(screen, self.colors['grid'], event_btn_rect, 2)
                event_text = "(flags not connected)"
                event_color = self.colors['text_dark']
            else:
                pygame.draw.rect(screen, self.colors['input_bg'], event_btn_rect)
                pygame.draw.rect(screen, self.colors['accent'], event_btn_rect, 2)
                if event_cond_count or event_action_count:
                    event_text = f"Edit ({event_cond_count} cond, {event_action_count} act)"
                else:
                    event_text = "None — click to add"
                event_color = self.colors['text']

            event_label_surf = self.font_small.render(event_text, True, event_color)
            screen.blit(event_label_surf, event_label_surf.get_rect(center=event_btn_rect.center))
            self.ui_rects['trigger_box_event_btn'] = event_btn_rect

            y_pos += 30

        if (self.selected_object and isinstance(self.selected_object, dict)
                and self.selected_object.get('object_type') == 'world_map_object'):
            current_variant = self._get_current_variant(self.selected_object)
            if current_variant and current_variant.get('type') in ('world_map', 'world_map_sign'):
                map_label = self.font_medium.render("Map:", True, self.colors['text'])
                screen.blit(map_label, (self.palette_x + self.palette_padding, y_pos))

                # ── Dropdown button ───────────────────────────────────────────
                btn_x = self.palette_x + self.palette_padding + 120
                btn_rect = pygame.Rect(btn_x, y_pos - 3, 200, 25)
                btn_bg = self.colors['input_active'] if self.world_map_dropdown_open else self.colors['input_bg']
                pygame.draw.rect(screen, btn_bg, btn_rect)
                pygame.draw.rect(screen,
                                 self.colors['accent'] if self.world_map_dropdown_open else self.colors['grid'],
                                 btn_rect, 2)

                display_label = self.world_map_name_text if self.world_map_name_text else '<select map>'
                label_surf = self.font_small.render(display_label, True, self.colors['text'])
                label_clip = pygame.Rect(btn_rect.x + 4, btn_rect.y, btn_rect.w - 20, btn_rect.h)
                screen.set_clip(label_clip)
                screen.blit(label_surf, (btn_rect.x + 4, btn_rect.y + 6))
                screen.set_clip(None)

                arrow_x = btn_rect.right - 14
                arrow_y = btn_rect.centery
                arrow_pts = [(arrow_x, arrow_y - 4), (arrow_x + 8, arrow_y - 4), (arrow_x + 4, arrow_y + 4)]
                pygame.draw.polygon(screen, self.colors['text_dim'], arrow_pts)

                self.ui_rects['world_map_dropdown_btn'] = btn_rect

                # ── Open dropdown list ────────────────────────────────────────
                if self.world_map_dropdown_open:
                    names = self.world_map_dropdown_names
                    item_h = 22
                    list_h = max(item_h, len(names) * item_h)
                    list_rect = pygame.Rect(btn_rect.x, btn_rect.bottom, btn_rect.w, list_h)

                    list_bg = pygame.Surface((list_rect.w, list_rect.h), pygame.SRCALPHA)
                    list_bg.fill((30, 30, 45, 240))
                    screen.blit(list_bg, list_rect.topleft)
                    pygame.draw.rect(screen, self.colors['accent'], list_rect, 1)

                    self.ui_rects['world_map_dropdown_items'] = []
                    if not names:
                        empty_surf = self.font_small.render('<no maps found>', True, self.colors['text_dark'])
                        screen.blit(empty_surf, (list_rect.x + 4, list_rect.y + 4))
                    else:
                        for i, name in enumerate(names):
                            item_rect = pygame.Rect(list_rect.x, list_rect.y + i * item_h, list_rect.w, item_h)
                            is_sel = name == self.world_map_name_text
                            if is_sel:
                                pygame.draw.rect(screen, self.colors['variant_selected'], item_rect)
                            item_surf = self.font_small.render(name, True,
                                                               self.colors['text'] if is_sel else self.colors[
                                                                   'text_dim'])
                            screen.blit(item_surf, (item_rect.x + 6, item_rect.y + 4))
                            self.ui_rects['world_map_dropdown_items'].append((item_rect, name))

                y_pos += 30

        if self.selected_object and isinstance(self.selected_object, dict) and self.selected_object.get(
                'is_animated_region', False):
            label = self.font_medium.render(f"Opacity: {self.region_opacity}%", True, self.colors['text'])
            screen.blit(label, (self.palette_x + self.palette_padding, y_pos))
            y_pos += 20

            track_x = self.palette_x + self.palette_padding
            track_y = y_pos
            track_w = self.palette_width - self.palette_padding * 2
            track_h = 14
            track_rect = pygame.Rect(track_x, track_y, track_w, track_h)

            pygame.draw.rect(screen, self.colors['input_bg'], track_rect, border_radius=4)

            fill_w = int(track_w * (self.region_opacity / 100))
            if fill_w > 0:
                fill_rect = pygame.Rect(track_x, track_y, fill_w, track_h)
                pygame.draw.rect(screen, self.colors['accent'], fill_rect, border_radius=4)

            pygame.draw.rect(screen, self.colors['grid'], track_rect, 2, border_radius=4)

            handle_x = track_x + fill_w
            handle_rect = pygame.Rect(0, 0, 10, track_h + 8)
            handle_rect.center = (handle_x, track_y + track_h // 2)
            pygame.draw.rect(screen, self.colors['text'], handle_rect, border_radius=3)

            self.ui_rects['region_opacity_slider'] = track_rect

            y_pos += track_h + 16

            current_region_type = self.selected_object.get('region_type')
            if current_region_type in ('water',):
                # Wave/Flicker Amount slider — fraction of 8x8 chunks showing
                # the animated patch vs. the plain (no-line) patch.
                amount_label_text = "Wave Amount"
                wave_label = self.font_medium.render(
                    f"{amount_label_text}: {self.region_wave_amount}%", True, self.colors['text'])
                screen.blit(wave_label, (self.palette_x + self.palette_padding, y_pos))
                y_pos += 20

                wtrack_x = self.palette_x + self.palette_padding
                wtrack_y = y_pos
                wtrack_w = self.palette_width - self.palette_padding * 2
                wtrack_h = 14
                wtrack_rect = pygame.Rect(wtrack_x, wtrack_y, wtrack_w, wtrack_h)

                pygame.draw.rect(screen, self.colors['input_bg'], wtrack_rect, border_radius=4)

                wfill_w = int(wtrack_w * (self.region_wave_amount / 100))
                if wfill_w > 0:
                    wfill_rect = pygame.Rect(wtrack_x, wtrack_y, wfill_w, wtrack_h)
                    pygame.draw.rect(screen, self.colors['accent'], wfill_rect, border_radius=4)

                pygame.draw.rect(screen, self.colors['grid'], wtrack_rect, 2, border_radius=4)

                whandle_x = wtrack_x + wfill_w
                whandle_rect = pygame.Rect(0, 0, 10, wtrack_h + 8)
                whandle_rect.center = (whandle_x, wtrack_y + wtrack_h // 2)
                pygame.draw.rect(screen, self.colors['text'], whandle_rect, border_radius=3)

                self.ui_rects['region_wave_slider'] = wtrack_rect

                y_pos += wtrack_h + 16

                # Seed — determines which chunks lose their waves at a given
                # Wave Amount; reroll to reshuffle the layout.
                seed_label = self.font_medium.render("Seed:", True, self.colors['text'])
                screen.blit(seed_label, (self.palette_x + self.palette_padding, y_pos))

                seed_input_x = self.palette_x + self.palette_padding + 60
                seed_input_y = y_pos - 3
                seed_input_rect = pygame.Rect(seed_input_x, seed_input_y, 80, 25)
                seed_bg_color = self.colors['input_active'] if self.region_seed_input_active else self.colors[
                    'input_bg']
                pygame.draw.rect(screen, seed_bg_color, seed_input_rect)
                pygame.draw.rect(screen,
                                 self.colors['accent'] if self.region_seed_input_active else self.colors['grid'],
                                 seed_input_rect, 2)

                seed_display = self.region_seed_text if self.region_seed_input_active else str(self.region_seed)
                seed_text_surf = self.font_medium.render(seed_display, True, self.colors['text'])
                seed_text_rect = seed_text_surf.get_rect(center=seed_input_rect.center)
                screen.blit(seed_text_surf, seed_text_rect)

                self.ui_rects['region_seed_input'] = seed_input_rect

                reroll_rect = pygame.Rect(seed_input_rect.right + 8, seed_input_y, 60, 25)
                pygame.draw.rect(screen, self.colors['panel_light'], reroll_rect, border_radius=4)
                pygame.draw.rect(screen, self.colors['accent'], reroll_rect, 2, border_radius=4)
                reroll_text = self.font_small.render("Reroll", True, self.colors['text'])
                screen.blit(reroll_text, reroll_text.get_rect(center=reroll_rect.center))
                self.ui_rects['region_seed_reroll'] = reroll_rect

                y_pos += 30

            current_style = REGION_STYLES.get(current_region_type, {})
            num_tile_variants = max(1, current_style.get('grid_rows', 1))
            if current_style.get('mode', 'patch') == 'tile' and num_tile_variants > 1:
                # Static variant picker — tile-mode regions have no wave/
                # color controls, just which of the sheet's non-animated
                # variants a newly placed region uses. Default (slot 0) is
                # the first variant, matching AnimatedRegion's own default.
                # Shows the actual cropped sprite for each variant, same
                # look as _draw_variant_selector's gate/stone/etc
                # thumbnails, rather than plain numbered buttons. A
                # single-frame tile sheet (grid_rows omitted or 1 — e.g. a
                # one-off, non-square sprite with nothing to choose
                # between) skips this picker entirely.
                variant_label = self.font_medium.render("Variant:", True, self.colors['text'])
                screen.blit(variant_label, (self.palette_x + self.palette_padding, y_pos))
                y_pos += 20

                variant_sprites = self._load_region_variant_sprites(current_region_type)
                num_variants = num_tile_variants
                btn_gap = 6
                # Cap button size so a sheet with few variants (e.g. 2)
                # doesn't stretch each button to fill the whole row width —
                # buttons only shrink below this cap when there are enough
                # variants that the full-width division would exceed it.
                max_btn_size = 56
                fit_w = (self.palette_width - self.palette_padding * 2 - btn_gap * (num_variants - 1)) // num_variants
                btn_w = min(max_btn_size, fit_w)
                btn_h = btn_w
                variant_rects = []
                for i in range(num_variants):
                    btn_x = self.palette_x + self.palette_padding + i * (btn_w + btn_gap)
                    btn_rect = pygame.Rect(btn_x, y_pos, btn_w, btn_h)
                    selected = (self.region_variant == i)
                    bg_color = self.colors['variant_selected'] if selected else self.colors['panel_light']
                    pygame.draw.rect(screen, bg_color, btn_rect, border_radius=4)
                    pygame.draw.rect(screen, self.colors['accent'], btn_rect,
                                     3 if selected else 2, border_radius=4)

                    sprite = variant_sprites[i] if i < len(variant_sprites) else None
                    if sprite:
                        sw, sh = sprite.get_size()
                        max_dim = btn_w - 8
                        scale = min(max_dim / sw, max_dim / sh)
                        if scale != 1:
                            sprite = pygame.transform.scale(
                                sprite, (max(1, int(sw * scale)), max(1, int(sh * scale)))
                            )
                        screen.blit(sprite, sprite.get_rect(center=btn_rect.center))
                    else:
                        # Asset not loaded yet — fall back to a number so the
                        # slot is still identifiable and clickable.
                        num_text = self.font_small.render(str(i + 1), True, self.colors['text'])
                        screen.blit(num_text, num_text.get_rect(center=btn_rect.center))

                    variant_rects.append(btn_rect)

                self.ui_rects['region_variant_rects'] = variant_rects
                y_pos += btn_h + 16

            # Color tint — every 'patch'-mode region supports it (water/lava/
            # grass today, and any new 64x64 sheet added to REGION_STYLES).
            # A hue strip + saturation/value square, plus a swatch preview.
            # (255, 255, 255) leaves the sprite's original colors alone. The
            # runtime only recolors the sprite's non-white pixels, so foam/
            # highlight whites (water/lava) or the lightest grass shade stay
            # put no matter what's picked here.
            current_style = REGION_STYLES.get(current_region_type, {})
            if current_style.get('mode', 'patch') == 'patch':
                type_label = current_style.get('label', current_region_type.title())
                # style label is like "Water Region" — swap the trailing
                # "Region" for "Color:" so new sheets get a sensible label
                # for free without needing their own dict entry.
                color_label_text = type_label.replace('Region', '').strip() + " Color:" \
                    if 'Region' in type_label else f"{type_label} Color:"
                color_label = self.font_medium.render(color_label_text, True, self.colors['text'])
                screen.blit(color_label, (self.palette_x + self.palette_padding, y_pos))

                swatch_rect = pygame.Rect(self.palette_x + self.palette_padding + 120, y_pos - 2, 30, 18)
                pygame.draw.rect(screen, self.region_color, swatch_rect)
                pygame.draw.rect(screen, self.colors['grid'], swatch_rect, 2)

                reset_rect = pygame.Rect(swatch_rect.right + 8, y_pos - 2, 60, 18)
                pygame.draw.rect(screen, self.colors['panel_light'], reset_rect, border_radius=4)
                pygame.draw.rect(screen, self.colors['accent'], reset_rect, 2, border_radius=4)
                reset_text = self.font_small.render("Reset", True, self.colors['text'])
                screen.blit(reset_text, reset_text.get_rect(center=reset_rect.center))
                self.ui_rects['region_color_reset'] = reset_rect

                y_pos += 26

                hue, sat, val = self._region_color_hsv()

                hue_w = 18
                gap = 10
                sv_h = 90
                sv_x = self.palette_x + self.palette_padding
                sv_y = y_pos
                sv_w = self.palette_width - self.palette_padding * 2 - hue_w - gap
                sv_rect = pygame.Rect(sv_x, sv_y, sv_w, sv_h)

                sv_surf = self._get_sv_square_surface(hue, sv_w, sv_h)
                screen.blit(sv_surf, sv_rect.topleft)
                pygame.draw.rect(screen, self.colors['grid'], sv_rect, 2)

                # Cursor ring — white on dark colors, black on light ones so
                # it's always visible against whatever's under it.
                sv_cursor_x = sv_rect.left + int(sat * sv_rect.width)
                sv_cursor_y = sv_rect.top + int((1 - val) * sv_rect.height)
                ring_color = (255, 255, 255) if val < 0.6 else (0, 0, 0)
                pygame.draw.circle(screen, ring_color, (sv_cursor_x, sv_cursor_y), 6, 2)

                self.ui_rects['region_sv_square'] = sv_rect

                hue_rect = pygame.Rect(sv_rect.right + gap, sv_y, hue_w, sv_h)
                hue_surf = self._get_hue_strip_surface(hue_w, sv_h)
                screen.blit(hue_surf, hue_rect.topleft)
                pygame.draw.rect(screen, self.colors['grid'], hue_rect, 2)

                hue_marker_y = hue_rect.top + int(hue * hue_rect.height)
                marker_rect = pygame.Rect(hue_rect.left - 2, hue_marker_y - 2, hue_rect.width + 4, 4)
                pygame.draw.rect(screen, (255, 255, 255), marker_rect, 1)

                self.ui_rects['region_hue_strip'] = hue_rect

                y_pos += sv_h + 10

        instructions = [
            "Click: Select Object",
            "Click World: Place",
            "Right-Click: Delete",
            "Item armed + Click Chest: Add Loot",
            "ESC/F3: Close"
        ]

        for inst in instructions:
            inst_surf = self.font_small.render(inst, True, self.colors['text_dim'])
            screen.blit(inst_surf, (self.palette_x + self.palette_padding, y_pos))
            y_pos += 18