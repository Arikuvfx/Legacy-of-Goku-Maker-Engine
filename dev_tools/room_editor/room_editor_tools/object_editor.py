import os
import uuid

from objects.ambient_sound_object import AmbientSoundObject

import numpy as np
import pygame
import pygame.gfxdraw

import dev_tools.ui_kit as uk
from config.settings import RENDER_SCALE, TILE_SIZE, WORLD_WIDTH, WORLD_HEIGHT
from objects.spawn_object import SpawnObject, SpawnObjectManager
from objects.collision_object import (CollisionObject, CollisionObjectManager,
                                       draw_collision_object, draw_collision_group)
from objects.animated_region import AnimatedRegion, AnimatedRegionManager, draw_animated_region, REGION_STYLES
from objects.level_gate import LevelGate, LevelGateManager
from objects.room_transition import RoomTransition, RoomTransitionManager, TransitionConfigDialog
from objects.flying_pad import FlyingPad, FlyingPadManager
from objects.nimbus_cloud import NimbusCloud, NimbusCloudManager
from objects.save_point import SavePoint, SavePointManager
from objects.world_map import WorldMapObject, WorldMapObjectManager
from objects.fishing_area import FishingArea, FishingAreaManager
from objects.door_object import Door, DoorManager
from objects.chest_object import Chest, ChestManager
from objects.decoration_objects import (Decoration, DECORATION_STYLES,
                                         reload_decoration_styles)
from core.items import get_item
from objects.trigger_box import (OverlapTriggerBox, KeyTriggerBox, TriggerBoxManager,
                                 draw_trigger_box)
from dev_tools.room_editor.room_editor_tools.flying_pad_path_editor import FlyingPadPathEditor
from dev_tools.room_editor.room_editor_tools.nimbus_cloud_path_editor import NimbusCloudPathEditor
from core.event_editor import EventEditorWindow
from config.settings import ui, ui_text


# =============================================================================
# Small vector chevron for the panel show/hide tab — same primitive
# (draw_line_on), and the same look, as TilesetEditor's own tab chevron,
# rather than the filled-triangle polygon this file used to draw.
# =============================================================================

def _draw_chevron_icon(surface, rect, color, left=True, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.28
    if left:
        uk.draw_line_on(surface, color, (cx + s * 0.5, cy - s), (cx - s * 0.5, cy), width)
        uk.draw_line_on(surface, color, (cx - s * 0.5, cy), (cx + s * 0.5, cy + s), width)
    else:
        uk.draw_line_on(surface, color, (cx - s * 0.5, cy - s), (cx + s * 0.5, cy), width)
        uk.draw_line_on(surface, color, (cx + s * 0.5, cy), (cx - s * 0.5, cy + s), width)


class _BitmapFontView:
    """Adapts a shared uk.BitmapFont to the plain pygame.font.Font call shape
    -- render(text, antialias, color) -- at one fixed pixel height, so every
    draw call below that used to say `self.font_small.render(text, True,
    color)` against an old pygame.font.Font keeps working unchanged against
    the new bitmap font. Same trick dev_menu.py's DevMenu uses for its
    ModalTextInput fonts."""

    def __init__(self, bitmap_font, height):
        self._font = bitmap_font
        self._height = height

    def render(self, text, antialias=True, color=(255, 255, 255)):
        return self._font.render(text, color=color, height=self._height)

    def size(self, text):
        return self._font.size(text, height=self._height)


class ObjectEditor:
    """Editor for placing game objects like spawn points, collision walls, and decorations"""

    # Maps every obj_type string returned by _check_object_at_position /
    # _all_objects to the palette category (self.categories key / tab)
    # it's placed from. Used to scope right-click deletion (and hover) to
    # whichever category tab is currently open — see _check_object_at_position.
    OBJ_TYPE_CATEGORY = {
        'spawn': 'System',
        'collision': 'System',
        'transition': 'System',
        'trigger_box': 'System',
        'ambient_sound': 'System',
        'animated_region': 'Terrain',
        'stone': 'Interactive',
        'gate': 'Interactive',
        'flying_pad': 'Interactive',
        'nimbus_cloud': 'Interactive',
        'save_point': 'Interactive',
        'world_map_object': 'Interactive',
        'fishing_area': 'Interactive',
        'chest': 'Interactive',
        'door': 'Interactive',
        'decoration': 'Decorations',
    }

    def __init__(self, screen_width, screen_height, room_manager=None):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False
        self.room_manager = room_manager

        # ── Shared "modern DBZ" dev-tool visual language ────────────────────
        # Same bitmap font family + Theme palette as DevMenu/EditorToolbar/
        # RoomEditor's own chrome: deep navy panels, gold/ki-blue accents,
        # hairline borders that light up on hover, vector line-icons instead
        # of loaded PNGs. self.font_small/medium/large below are thin
        # _BitmapFontView adapters over the one shared bitmap font so every
        # existing `self.font_X.render(text, True, color)` call across this
        # file keeps working unchanged against the new font.
        self.font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self.font_small = _BitmapFontView(self.font, ui_text(11))
        self.font_medium = _BitmapFontView(self.font, ui_text(13))
        self.font_large = _BitmapFontView(self.font, ui_text(18))
        self.font_hint = _BitmapFontView(self.font, ui_text(10))

        # self.colors is kept only as a thin compatibility alias for any
        # in-world (non-chrome) draw call in this file that still reads a
        # named color out of it (e.g. the placement snap-guide) -- every
        # palette/panel/variant-selector visual below reads uk.Theme
        # directly instead of this dict.
        self.colors = {
            'bg': (14, 17, 25),
            'bg_transparent': (14, 17, 25, 235),
            'panel': uk.Theme.CARD_BG[:3],
            'panel_light': uk.Theme.CARD_BG_HOVER[:3],
            'accent': uk.Theme.GOLD,
            'accent_dim': (196, 152, 61),
            'text': uk.Theme.TEXT_PRIMARY,
            'text_dim': uk.Theme.TEXT_MUTED,
            'text_dark': uk.Theme.TEXT_DIM,
            'grid': uk.Theme.CARD_BORDER,
            'success': uk.Theme.KI_BLUE,
            'preview': (255, 255, 255, 100),
            'snap_guide': (*uk.Theme.GOLD, 150),
            'disabled': (90, 94, 106),
            'delete': uk.Theme.DANGER_BRIGHT,
            'delete_hover': uk.Theme.DANGER,
            'input_bg': (32, 36, 50, 255),
            'input_active': (42, 47, 66, 255),
            'variant_bg': uk.Theme.PANEL_BG,
            'variant_selected': uk.Theme.KI_BLUE,
        }

        # ── Palette geometry ─────────────────────────────────────────────────
        self.palette_width = ui(600)
        self.palette_x = screen_width - self.palette_width
        self.palette_y = ui(100)
        # Never taller than the screen allows (940 was tuned for 1080p).
        self.palette_height = max(ui(300), min(ui(940), screen_height - self.palette_y - ui(10)))
        self.palette_padding = ui(10)
        self.item_size = ui(80)
        # How many item_size boxes (plus the 10px gap between them, same gap
        # used at draw time below) actually fit across the palette's usable
        # width. This used to be hardcoded to 3, which only filled ~270px of
        # the 600px-wide panel — every category's grid sat flush left with
        # roughly half the panel sitting empty. Deriving it from the real
        # geometry instead means the grid always uses the full width, and
        # it stays correct if palette_width/item_size ever change.
        item_gap = ui(10)
        usable_width = self.palette_width - self.palette_padding * 2
        self.items_per_row = max(1, (usable_width + item_gap) // (self.item_size + item_gap))
        # Column pitch (left-right) stays at the original gap so the grid's
        # width/columns-per-row math above doesn't change. Row pitch
        # (top-bottom) gets extra headroom so the name label drawn below
        # each card has room to breathe before the next row of cards starts
        # — previously it used the same 10px gap as columns, which wasn't
        # enough for a line of text and made row N's labels overlap row
        # N+1's cards.
        self._item_col_gap = item_gap
        self._item_row_gap = item_gap + ui(16)
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
        # Set when the current collision drag started with Shift held --
        # instead of one rectangular box, the drag lays down a "staircase"
        # chain of small square boxes following the drag line (walked in
        # _build_diagonal_collision_chain), drawn merged into one smooth
        # angled quad (see draw_collision_group) but functioning as
        # ordinary axis-aligned collision underneath.
        self.collision_diagonal_mode = False
        self.preview_collision_group = []

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
        self._region_r_dragging = False  # dragging the R gradient bar
        self._region_g_dragging = False  # dragging the G gradient bar
        self._region_b_dragging = False  # dragging the B gradient bar
        self.region_channel_input_active = None  # 'r' / 'g' / 'b' / None — which spin box is being typed into
        self.region_channel_text = ""
        self.region_hex_text = "FFFFFF"
        self.region_hex_input_active = False
        self.region_variant = 0  # 0-based index into the current tile-mode region's static variants
        # region_type -> last-used {opacity, wave_amount, seed, color, variant}.
        # region_color/opacity/etc above are a single shared "current settings"
        # scratch space edited by the sliders; without this cache, switching
        # which region type is selected in the palette left that scratch space
        # untouched, so e.g. tinting a water region red would leak that same
        # red into the next lava/grass/dirt region placed afterward.
        self.region_settings_by_type = {}
        self._channel_gradient_cache = {}  # channel idx -> ((w, h), surface) — black->full-channel gradient, doesn't depend on the current value
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

        # ── Fishing areas ─────────────────────────────────────────────────────
        self.fishing_area_manager = FishingAreaManager()
        self.on_fishing_area_placed = None
        self.on_fishing_area_deleted = None

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

        # Positional ambient emitters live independently of the room-wide BGS
        # slot. room_editor.py aliases each room's persisted list into this
        # dict, the same way the existing object managers are synchronized.
        self.ambient_sound_objects = {}
        self.ambient_sound_options = self._discover_ambient_sound_names()
        self.ambient_sound_name = self.ambient_sound_options[0] if self.ambient_sound_options else ''
        self.ambient_sound_max_distance = 256
        self.on_ambient_sound_placed = None
        self.on_ambient_sound_deleted = None

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

        # ── Gate character lock ─────────────────────────────────────────────
        # None = "Any" — the old behaviour, no character requirement. When set
        # to a char_id, the placed gate is locked to that character and its
        # number is drawn in that character's assigned color (see
        # objects/level_gate.py's LevelGate._load_gate_color). Sticky like
        # gate_required_level above: it carries over to the next gate placed
        # until changed. Cache is built lazily from
        # dev_tools/character_creator.py's discover_characters() the first
        # time it's needed, and can be refreshed with
        # _refresh_gate_character_choices() if a character is added/removed
        # while this editor is open.
        self.gate_required_character = None
        self._gate_character_choices_cache = None

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

        # Decoration catalogue is generated from DECORATION_STYLES. The
        # 'tree' entry remains fully hardcoded in decoration_objects.py, so
        # its hand-authored animation sequence can never be replaced by the
        # automatic asset scanner. Any other decoration folder found under
        # assets/objects/decorations/ is added automatically.
        self.decoration_variants_by_type = {}
        self.decoration_palette_entries = self._build_decoration_palette_entries()
        self.tree_variants = self.decoration_variants_by_type.get('tree', [])

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
                    'id': 'fishing_area',
                    'name': 'Fishing Area',
                    'sprite': None,
                    'width': 48,
                    'height': 48,
                    'object_type': 'fishing_area',
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
            'Decorations': self.decoration_palette_entries,
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

        # Add positional Ambient Sound emitter to System. The icon is editor
        # only; runtime rendering is just an optional debug marker/radius.
        ambient_sprite = pygame.Surface((16, 16), pygame.SRCALPHA)
        pygame.draw.circle(ambient_sprite, (170, 110, 255, 120), (8, 8), 7)
        pygame.draw.circle(ambient_sprite, (220, 190, 255), (8, 8), 3)
        pygame.draw.arc(ambient_sprite, (235, 220, 255), (1, 1, 14, 14), -0.8, 0.8, 2)
        self.categories['System'].append({
            'id': 'ambient_sound',
            'name': 'Ambient Sound',
            'sprite': ambient_sprite,
            'width': 16,
            'height': 16,
            'object_type': 'ambient_sound',
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

        # I-beam-on-hover for real text-entry fields (gate level, trigger box
        # id, region seed/channel/hex) — rebuilt each draw_palette() call, same
        # convention as RoomEditor.text_field_rects/_owns_text_cursor.
        self.text_field_rects = []
        self._owns_text_cursor = False

        # Panel show/hide toggle (same pattern as EditorToolbar)
        self.palette_visible = True
        self._panel_tab_w = ui(18)
        self._panel_tab_h = ui(72)
        self._hover_panel_toggle = False

        # Slide animation for the panel opening/closing — chased toward
        # 1.0 (fully open) or 0.0 (fully closed) each frame, same pattern
        # as TilesetEditor's palette panel. Advanced from inside
        # draw_palette() (timed off real elapsed ms) since this widget has
        # no separate per-frame update(dt) hook of its own.
        self._panel_slide_anim    = 1.0 if self.palette_visible else 0.0
        self._panel_slide_last_ms = None
        self._PANEL_SLIDE_RATE    = 9.0

        # Keybinds reference popup — toggled by the '?' info button next to
        # the palette title (same pattern as TilesetEditor). Replaces the
        # old always-on instructions footer.
        self.show_keybinds_popup = False

        # Optional custom icon for the info ('?') badge — same PNG-override
        # convention as EditorToolbar/TilesetEditor (assets/ui/toolbar/<id>.png):
        # drop a PNG there and it replaces the procedural circle+'?' mark.
        self._info_icon = None
        try:
            _info_img = pygame.image.load('assets/ui/toolbar/info.png').convert_alpha()
            _iw, _ih = _info_img.get_size()
            _scale = min(18 / _iw, 18 / _ih)
            self._info_icon = pygame.transform.scale(
                _info_img, (max(1, int(_iw * _scale)), max(1, int(_ih * _scale))))
        except Exception:
            pass

        # Set via set_toolbar() below; read (via getattr-guarded access) to
        # find out whether an item is armed in the Items panel for the
        # chest-loot-assignment flow — see _try_assign_chest_loot.
        self.toolbar = None

    def _build_decoration_palette_entries(self):
        """Build one palette card per discovered/hardcoded decoration type.

        Tree is kept first for continuity with the existing editor. Every
        other decoration is driven by DECORATION_STYLES, so no editor code is
        needed when a new decoration folder is added.
        """
        entries = []
        decoration_types = list(DECORATION_STYLES.keys())
        if 'tree' in decoration_types:
            decoration_types.remove('tree')
            decoration_types.insert(0, 'tree')

        for decoration_type in decoration_types:
            style = DECORATION_STYLES.get(decoration_type, {})
            frame_w = int(style.get('frame_w', 32))
            frame_h = int(style.get('frame_h', 32))
            variant_names = style.get('variants', [])
            if not variant_names:
                variant_names = [style.get('label', decoration_type.replace('_', ' ').title())]

            variants = [
                {'type': i, 'name': str(name), 'sprite': None}
                for i, name in enumerate(variant_names)
            ]
            self.decoration_variants_by_type[decoration_type] = variants

            entries.append({
                'id': decoration_type,
                'name': style.get('label', decoration_type.replace('_', ' ').replace('-', ' ').title()),
                'sprite': None,
                'width': frame_w,
                'height': frame_h,
                'object_type': 'decoration',
                'decoration_type': decoration_type,
                'has_variants': len(variants) > 0,
                'variants': variants,
                'default_variant': 0,
            })

        return entries


    def refresh_decoration_catalog(self):
        """Reload decoration definitions and rebuild the Decorations palette.

        The Decoration Creator calls this after saving so newly-added or
        reconfigured decorations appear in the Object Editor without restarting
        the game. The hardcoded Tree remains protected by
        reload_decoration_styles().
        """
        current_id = None
        current_variant_type = None
        if isinstance(getattr(self, 'selected_object', None), dict):
            if self.selected_object.get('object_type') == 'decoration':
                current_id = self.selected_object.get('decoration_type')
        if isinstance(getattr(self, 'selected_variant', None), dict):
            current_variant_type = self.selected_variant.get('type')

        reload_decoration_styles()
        self.decoration_variants_by_type = {}
        self.decoration_palette_entries = self._build_decoration_palette_entries()
        self.tree_variants = self.decoration_variants_by_type.get('tree', [])

        # The palette is already constructed during __init__, but this method
        # may be called later after the category dictionary exists.
        if hasattr(self, 'categories'):
            self.categories['Decorations'] = self.decoration_palette_entries

        self.showing_variants_for = None
        if current_id:
            replacement = next(
                (obj for obj in self.decoration_palette_entries
                 if obj.get('decoration_type') == current_id),
                None,
            )
            self.selected_object = replacement
            if replacement:
                variants = replacement.get('variants', [])
                if current_variant_type is not None:
                    self.selected_variant = next(
                        (v for v in variants if v.get('type') == current_variant_type),
                        variants[0] if variants else None,
                    )
                else:
                    self.selected_variant = variants[0] if variants else None
            else:
                self.selected_object = None
                self.selected_variant = None
        elif getattr(self, 'selected_object', None) and self.selected_object.get('object_type') != 'decoration':
            # Preserve selections for unrelated categories.
            pass
        else:
            self.selected_object = None
            self.selected_variant = None

        self._generate_variant_sprites()

    @staticmethod
    def _discover_ambient_sound_names():
        """Return extensionless BGS keys from assets/audio/sfx/ambient.

        AudioAssetLoader keys these sounds by bare filename stem even when they
        live in nested folders, so the editor mirrors that convention.
        """
        ambient_dir = os.path.join('assets', 'audio', 'sfx', 'ambient')
        names = []
        try:
            for _root, _dirs, filenames in os.walk(ambient_dir):
                for filename in filenames:
                    if filename.lower().endswith(('.wav', '.ogg')):
                        names.append(os.path.splitext(filename)[0])
        except OSError:
            pass
        return sorted(set(names))

    def set_ambient_sound_objects(self, room_name, objects):
        """Alias a room's live AmbientSoundObject list into the editor."""
        self.ambient_sound_objects[room_name] = objects

    def get_ambient_sound_objects(self, room_name):
        return self.ambient_sound_objects.setdefault(room_name, [])

    def update_positional_ambient_sounds(self, player_x, player_y, room_name):
        """Update every placed emitter in room_name for the current frame."""
        for emitter in self.get_ambient_sound_objects(room_name):
            emitter.update_audio(player_x, player_y, self.sound_manager)

    def stop_positional_ambient_sounds(self, room_name=None):
        """Stop emitter-owned channels for one room, or every known room."""
        if room_name is None:
            groups = list(self.ambient_sound_objects.values())
        else:
            groups = [self.get_ambient_sound_objects(room_name)]
        for emitters in groups:
            for emitter in emitters:
                emitter.stop_audio(self.sound_manager)

    def _cycle_ambient_sound(self, direction):
        if not self.ambient_sound_options:
            self.ambient_sound_name = ''
            return
        try:
            index = self.ambient_sound_options.index(self.ambient_sound_name)
        except ValueError:
            index = 0
        self.ambient_sound_name = self.ambient_sound_options[(index + direction) % len(self.ambient_sound_options)]

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
        """Return the rect for the ◀/▶ tab that straddles the panel's left
        edge. Tracks the animated slide (_panel_slide_anim), not just the
        instant palette_visible flag, so the tab visually stays glued to
        the panel's edge as it slides in/out instead of snapping straight
        to its new spot — same as TilesetEditor's tab. This also doubles
        as the click/hover hit-rect, which is what we want: the tab should
        be clickable where it's actually drawn. self.palette_x here is
        always the panel's settled resting position (draw_palette restores
        it after each shifted draw), so this is stable to call from
        anywhere, animating or not."""
        gap = ui(6)
        tx_shown  = self.palette_x - self._panel_tab_w - gap
        tx_hidden = self.screen_width - self._panel_tab_w
        tx = round(uk.lerp(tx_hidden, tx_shown, self._panel_slide_anim))
        ty = self.palette_y + (self.palette_height - self._panel_tab_h) // 2
        return pygame.Rect(tx, ty, self._panel_tab_w, self._panel_tab_h)

    def _draw_panel_toggle_tab(self, screen):
        """Render the small collapse/expand tab — always visible so the
        panel can be recalled. Same pill-with-line-chevron look as
        TilesetEditor's own show/hide tab (_draw_chevron_icon above),
        rather than the filled-triangle glyph this used to draw."""
        rect = self._panel_toggle_rect()
        t = 1.0 if self._hover_panel_toggle else 0.0
        base = uk.lerp_color((22, 25, 35), (30, 34, 46), t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, t)
        uk.draw_panel(screen, rect, bg=(*base, 235), border=border,
                      border_width=1, radius=ui(6), shadow=False)

        chevron_color = uk.lerp_color(uk.Theme.TEXT_MUTED, uk.Theme.GOLD_BRIGHT, t)
        _draw_chevron_icon(screen, rect, chevron_color, left=self.palette_visible, width=2)

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

                    elif obj['object_type'] == 'decoration':
                        # variant['type'] is the row index itself (see
                        # tree_variants above) — Decoration._load_frames
                        # slices row `variant * frame_h` out of the sheet,
                        # so this just asks for that row's first frame.
                        decoration_type = obj.get('decoration_type', 'tree')
                        variant_index = variant['type']
                        deco = Decoration(0, 0, decoration_type, variant_index)
                        variant['width'] = deco.width
                        variant['height'] = deco.height
                        variant['sprite'] = deco.frames[0].copy()

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
                if default_variant_type is not None:
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
            self.refresh_decoration_catalog()
            self.selected_object = None
            self.selected_variant = None
            self.showing_variants_for = None
            self.scroll_offset = 0
            self.placing_collision = False
            self.preview_collision = None
            self.collision_diagonal_mode = False
            self.preview_collision_group = []
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

    def _all_objects(self, room_name):
        """Yield (obj, obj_type) for every placed object in the room, across
        every manager. Used by the room editor's area-select rubber-band to
        collect everything inside a drag rectangle — unlike
        _check_object_at_position this doesn't hit-test or apply the
        current-category-tab scoping, since a rect select isn't tied to
        whichever palette tab happens to be open."""
        spawn = self.spawn_manager.get_spawn_point(room_name)
        if spawn:
            yield spawn, 'spawn'

        for obj in self.collision_manager.get_collision_objects(room_name):
            yield obj, 'collision'

        room = self.room_manager.get_room_by_name(room_name) if self.room_manager else None
        if room:
            for stone in getattr(room, 'destructible_stones', []):
                yield stone, 'stone'
            for decoration in getattr(room, 'decorations', []):
                yield decoration, 'decoration'

        for pad in self.flying_pad_manager.get_pads(room_name):
            yield pad, 'flying_pad'

        for cloud in self.nimbus_cloud_manager.get_clouds(room_name):
            yield cloud, 'nimbus_cloud'

        for door in self.door_manager.get_doors(room_name):
            yield door, 'door'

        for chest in self.chest_manager.get_chests(room_name):
            yield chest, 'chest'

        for gate in self.gate_manager.get_gates(room_name):
            yield gate, 'gate'

        for save_point in self.save_point_manager.get_save_points(room_name):
            yield save_point, 'save_point'

        for obj in self.world_map_manager.get_objects(room_name):
            yield obj, 'world_map_object'

        for area in self.fishing_area_manager.get_fishing_areas(room_name):
            yield area, 'fishing_area'

        for emitter in self.get_ambient_sound_objects(room_name):
            yield emitter, 'ambient_sound'

        for box in self.trigger_box_manager.get_boxes(room_name):
            yield box, 'trigger_box'

        for transition in self.transition_manager.get_transitions(room_name):
            yield transition, 'transition'

        for region in self.animated_region_manager.get_regions(room_name):
            yield region, 'animated_region'

    def _check_object_at_position(self, world_x, world_y):
        """See if there's an object at this position (for deletion).

        Category-tab locked: only object types belonging to whichever
        category is currently open (self.current_category — System,
        Terrain, Interactive, Decorations, Structures) are hit-tested at
        all. An object from a different category is completely invisible
        here, so a right-click while the Decorations tab is open can never
        reach through and delete a System collision wall, an Interactive
        door, etc. sitting on/near the spot clicked — you have to switch
        to that object's own tab first. This also naturally keeps
        collision walls and terrain regions (System vs Terrain) from
        stealing each other's clicks, since they're different tabs.
        """
        category = self.current_category

        def wants(obj_type):
            return self.OBJ_TYPE_CATEGORY.get(obj_type) == category

        # Check spawn point
        if wants('spawn'):
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
        if wants('collision'):
            collision_objs = self.collision_manager.get_collision_objects(self.current_room_name)
            for collision_obj in reversed(collision_objs):
                if (collision_obj.x <= world_x <= collision_obj.x + collision_obj.width and
                        collision_obj.y <= world_y <= collision_obj.y + collision_obj.height):
                    return collision_obj, 'collision'

        if wants('transition'):
            # Check room transitions
            transitions = self.transition_manager.get_transitions(self.current_room_name)
            for transition in transitions:
                if transition.check_collision_with_point(int(world_x), int(world_y)):
                    return transition, 'transition'

        if wants('trigger_box'):
            # Check trigger boxes
            for box in self.trigger_box_manager.get_boxes(self.current_room_name):
                if (box.x <= world_x <= box.x + box.width and
                        box.y <= world_y <= box.y + box.height):
                    return box, 'trigger_box'

        if wants('stone'):
            # Check destructible stones
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'destructible_stones'):
                    for stone in room.destructible_stones:
                        distance = ((stone.x - world_x) ** 2 + (stone.y - world_y) ** 2) ** 0.5
                        if distance < max(stone.width, stone.height) / 2:
                            return stone, 'stone'

        if wants('decoration'):
            # Check decorations (trees, etc.)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'decorations'):
                    for decoration in room.decorations:
                        distance = ((decoration.x - world_x) ** 2 + (decoration.y - world_y) ** 2) ** 0.5
                        if distance < max(decoration.width, decoration.height) / 2:
                            return decoration, 'decoration'

        if wants('flying_pad'):
            # Check flying pads
            pads = self.flying_pad_manager.get_pads(self.current_room_name)
            for pad in pads:
                distance = ((pad.x - world_x) ** 2 + (pad.y - world_y) ** 2) ** 0.5
                if distance < max(pad.width, pad.height) / 2:
                    return pad, 'flying_pad'

        if wants('nimbus_cloud'):
            # Check nimbus clouds
            clouds = self.nimbus_cloud_manager.get_clouds(self.current_room_name)
            for cloud in clouds:
                distance = ((cloud.x - world_x) ** 2 + (cloud.y - world_y) ** 2) ** 0.5
                if distance < max(cloud.width, cloud.height) / 2:
                    return cloud, 'nimbus_cloud'

        if wants('door'):
            # Check doors
            for door in self.door_manager.get_doors(self.current_room_name):
                distance = ((door.x - world_x) ** 2 + (door.y - world_y) ** 2) ** 0.5
                if distance < max(door.width, door.height) / 2:
                    return door, 'door'

        if wants('chest'):
            # Check chests
            for chest in self.chest_manager.get_chests(self.current_room_name):
                distance = ((chest.x - world_x) ** 2 + (chest.y - world_y) ** 2) ** 0.5
                if distance < max(chest.width, chest.height) / 2:
                    return chest, 'chest'

        if wants('gate'):
            # Check level gates
            gates = self.gate_manager.get_gates(self.current_room_name)
            for gate in gates:
                distance = ((gate.x - world_x) ** 2 + (gate.y - world_y) ** 2) ** 0.5
                if distance < max(gate.width, gate.height) / 2:
                    return gate, 'gate'

        if wants('save_point'):
            # Check save points
            save_points = self.save_point_manager.get_save_points(self.current_room_name)
            for save_point in save_points:
                distance = ((save_point.x - world_x) ** 2 + (save_point.y - world_y) ** 2) ** 0.5
                if distance < max(save_point.width, save_point.height) / 2:
                    return save_point, 'save_point'

        if wants('world_map_object'):
            # Check world map objects
            for obj in self.world_map_manager.get_objects(self.current_room_name):
                distance = ((obj.x - world_x) ** 2 + (obj.y - world_y) ** 2) ** 0.5
                if distance < max(obj.width, obj.height) / 2:
                    return obj, 'world_map_object'

        if wants('fishing_area'):
            # Check fishing areas
            for area in self.fishing_area_manager.get_fishing_areas(self.current_room_name):
                distance = ((area.x - world_x) ** 2 + (area.y - world_y) ** 2) ** 0.5
                if distance < max(area.width, area.height) / 2:
                    return area, 'fishing_area'

        if wants('ambient_sound'):
            for emitter in reversed(self.get_ambient_sound_objects(self.current_room_name)):
                distance = ((emitter.x - world_x) ** 2 + (emitter.y - world_y) ** 2) ** 0.5
                if distance <= max(emitter.width, emitter.height) / 2:
                    return emitter, 'ambient_sound'

        # Check water/grass/etc regions LAST — regions tend to be large,
        # ground-level fills that other system boxes (trigger boxes,
        # collision, doors, etc.) get placed on top of. If regions were
        # checked earlier, clicking on a spot where a region overlaps one
        # of those boxes would always hit the region first, forcing it to
        # be deleted/moved before the box underneath could be selected or
        # edited. Checking regions last means any other box "wins" the
        # click when they overlap, and a region is only picked when
        # nothing else is there. (Moot when they're on different tabs, but
        # still matters for two regions/objects sharing the same tab.)
        if wants('animated_region'):
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

        elif obj_type == 'fishing_area':
            self.fishing_area_manager.remove_fishing_area(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'fishing_areas') and obj in room.fishing_areas:
                    room.fishing_areas.remove(obj)
            if self.on_fishing_area_deleted:
                self.on_fishing_area_deleted(obj, self.current_room_name)

        elif obj_type == 'ambient_sound':
            obj.stop_audio(self.sound_manager)
            emitters = self.get_ambient_sound_objects(self.current_room_name)
            if obj in emitters:
                emitters.remove(obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'ambient_sounds') and obj in room.ambient_sounds:
                    room.ambient_sounds.remove(obj)
            if self.on_ambient_sound_deleted:
                self.on_ambient_sound_deleted(obj, self.current_room_name)

        elif obj_type == 'trigger_box':
            self.trigger_box_manager.remove_box(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'trigger_boxes'):
                    if obj in room.trigger_boxes:
                        room.trigger_boxes.remove(obj)

            if hasattr(self, 'on_trigger_box_deleted') and self.on_trigger_box_deleted:
                self.on_trigger_box_deleted(obj, self.current_room_name)

    def _delete_collision_group(self, group_id):
        """Delete every box sharing `group_id` (a Shift-dragged diagonal
        run -- see CollisionObject.diagonal_group_id) as one action.

        Right-click delete on a diagonal wall used to only remove
        whichever single little box was under the cursor, leaving the
        rest of the run behind (looking like only "the tip" got deleted).
        This finds every box tagged with the same group id, removes them
        all together, and reports the whole batch through
        on_collision_group_deleted so the host editor can push one
        grouped undo entry for it (mirroring on_collision_group_placed),
        instead of _delete_object's single-object hook.
        """
        collision_objs = list(self.collision_manager.get_collision_objects(self.current_room_name))
        group_boxes = [c for c in collision_objs if getattr(c, 'diagonal_group_id', None) == group_id]
        if not group_boxes:
            return

        for box in group_boxes:
            self.collision_manager.remove_collision_object(box)

        if self.room_manager:
            room = self.room_manager.get_room_by_name(self.current_room_name)
            if room and hasattr(room, 'collision_objects'):
                for box in group_boxes:
                    if box in room.collision_objects:
                        room.collision_objects.remove(box)
                self.room_manager.save_room(room)

        if hasattr(self, 'on_collision_group_deleted') and self.on_collision_group_deleted:
            self.on_collision_group_deleted(group_boxes, self.current_room_name)
        elif hasattr(self, 'on_collision_deleted') and self.on_collision_deleted:
            # Fallback for a host that hasn't wired the group hook yet --
            # still fully functional, just one undo step per box.
            for box in group_boxes:
                self.on_collision_deleted(box, self.current_room_name)

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
        """Check if the level input box was clicked. Reads the rect the
        settings panel actually drew this frame (self.ui_rects), same
        pattern every other panel control already uses, rather than
        re-deriving the row's position independently."""
        if not self.selected_object or not isinstance(self.selected_object, dict):
            return False

        if self.selected_object.get('object_type') != 'level_gate':
            return False

        input_rect = self.ui_rects.get('gate_level_input_rect')
        return bool(input_rect and input_rect.collidepoint(mouse_pos))

    # ── Gate character lock helpers ─────────────────────────────────────────

    def _gate_character_choices(self):
        """[None, *char_ids] for the Gate Character cycle control — None is
        always first ('Any character'). Built lazily and cached since
        discover_characters() touches disk on every call."""
        if self._gate_character_choices_cache is None:
            self._refresh_gate_character_choices()
        return self._gate_character_choices_cache

    def _refresh_gate_character_choices(self):
        """Rebuild the cached character list. Call this if the roster could
        have changed since the editor opened (e.g. a character was just
        created or deleted in Character Creator)."""
        try:
            from dev_tools import character_creator
            self._gate_character_choices_cache = [None] + character_creator.discover_characters()
        except Exception:
            self._gate_character_choices_cache = [None]

    def _gate_character_display_name(self, char_id):
        """Label shown in the palette for char_id ('Any' for None)."""
        if not char_id:
            return "Any"
        try:
            from dev_tools import character_creator
            cfg = character_creator.load_config(char_id)
            return cfg.get('display_name') or char_id.replace('_', ' ').title()
        except Exception:
            return char_id.replace('_', ' ').title()

    def _gate_character_color(self, char_id):
        """RGB the gate's number will actually render in for char_id — mirrors
        LevelGate._load_gate_color exactly, so the palette swatch preview
        never drifts from what gets placed."""
        if not char_id:
            return (255, 215, 0)
        try:
            from dev_tools import character_creator
            cfg = character_creator.load_config(char_id)
            return character_creator.hex_to_rgb(cfg.get('color', '#FFD700'), fallback=(255, 215, 0))
        except Exception:
            return (255, 215, 0)

    def _cycle_gate_character(self, direction):
        """Step gate_required_character forward/backward (direction = +1/-1)
        through [None, *discover_characters()], wrapping around."""
        choices = self._gate_character_choices()
        if not choices:
            return
        current = self.gate_required_character if self.gate_required_character in choices else None
        idx = (choices.index(current) + direction) % len(choices)
        self.gate_required_character = choices[idx]

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
        category_start_y = self.palette_y + ui(45)

        for i, category in enumerate(self.categories.keys()):
            category_rect = pygame.Rect(
                self.palette_x + self.palette_padding,
                category_start_y + i * ui(40),
                self.palette_width - self.palette_padding * 2,
                ui(30)
            )
            if category_rect.collidepoint(mouse_pos):
                self.current_category = category
                self.scroll_offset = 0
                return

        objects = self.categories[self.current_category]
        objects_start_y = category_start_y + len(self.categories) * ui(40) + ui(20) - self.scroll_offset

        for i, obj in enumerate(objects):
            row = i // self.items_per_row
            col = i % self.items_per_row

            item_x = self.palette_x + self.palette_padding + col * (self.item_size + self._item_col_gap)
            item_y = objects_start_y + row * (self.item_size + self._item_row_gap)

            item_rect = pygame.Rect(item_x, item_y, self.item_size, self.item_size)
            if item_rect.collidepoint(mouse_pos):
                if not self._is_object_disabled(obj):
                    self.selected_variant = None  # clear old variant before switching object
                    self.selected_object = obj
                    self.variant_scroll = 0
                    # Animated regions keep per-type color/opacity/wave/seed/
                    # variant settings — load this type's own last-used
                    # values instead of leaving whatever a different region
                    # type's sliders left behind (see region_settings_by_type).
                    if obj.get('is_animated_region', False):
                        self.current_region_type = obj.get('region_type', 'water')
                        self._load_region_settings_for_type(self.current_region_type)
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

        elif self.selected_object.get('object_type') == 'ambient_sound':
            emitter = AmbientSoundObject(
                int(self.preview_x), int(self.preview_y),
                self.ambient_sound_name, self.ambient_sound_max_distance
            )
            emitters = self.get_ambient_sound_objects(room_name)
            if self._too_close_to_existing(self.preview_x, self.preview_y, emitters):
                return
            emitters.append(emitter)

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    if not hasattr(room, 'ambient_sounds'):
                        room.ambient_sounds = emitters
                    elif room.ambient_sounds is not emitters and emitter not in room.ambient_sounds:
                        room.ambient_sounds.append(emitter)

            if self.on_ambient_sound_placed:
                self.on_ambient_sound_placed(emitter, room_name)

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

            # Get selected variant (row index) or default
            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            variant_index = variant['type'] if variant else 0

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room and self._too_close_to_existing(
                        self.preview_x, self.preview_y,
                        getattr(room, 'decorations', None)):
                    return

            decoration = Decoration(
                int(self.preview_x),
                int(self.preview_y),
                decoration_type,
                variant_index
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

        elif self.selected_object.get('object_type') == 'fishing_area':
            width = self.selected_object.get('width', 48)
            height = self.selected_object.get('height', 48)
            obj = FishingArea(int(self.preview_x), int(self.preview_y), width, height)
            self.fishing_area_manager.add_fishing_area(room_name, obj)

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    if not hasattr(room, 'fishing_areas'):
                        room.fishing_areas = []
                    if obj not in room.fishing_areas:
                        room.fishing_areas.append(obj)

            if self.on_fishing_area_placed:
                self.on_fishing_area_placed(obj, room_name)

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
                self.gate_required_level,
                self.gate_required_character
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
        uk.draw_circle_on(screen, uk.Theme.KI_BLUE,
                           (int(screen_x), int(screen_y)),
                           scaled_width // 2 + pulse, ui(3))

    def _draw_delete_highlight(self, screen, camera_x, camera_y):
        """Draw a danger-accent outline around the object about to be deleted."""
        obj = self.hovered_object
        obj_type = self.hovered_object_type
        delete_color = uk.Theme.DANGER_BRIGHT

        if obj_type == 'spawn':
            screen_x = (obj.x * RENDER_SCALE) - camera_x
            screen_y = (obj.y * RENDER_SCALE) - camera_y
            scaled_width = int(obj.width * RENDER_SCALE)

            pulse = int(20 + 10 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
            uk.draw_circle_on(screen, delete_color,
                               (int(screen_x), int(screen_y)),
                               scaled_width // 2 + pulse, ui(3))

        elif obj_type in ['collision', 'gate']:
            screen_x = (obj.x * RENDER_SCALE) - camera_x
            screen_y = (obj.y * RENDER_SCALE) - camera_y
            scaled_width = int(obj.width * RENDER_SCALE)
            scaled_height = int(obj.height * RENDER_SCALE)

            pulse = int(3 + 2 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
            uk.draw_rect_on(screen, delete_color,
                             (int(screen_x), int(screen_y), int(scaled_width), int(scaled_height)),
                             pulse)

        elif obj_type in ['stone', 'transition']:
            screen_x = (obj.x * RENDER_SCALE) - camera_x
            screen_y = (obj.y * RENDER_SCALE) - camera_y
            scaled_width = int(obj.width * RENDER_SCALE)

            pulse = int(20 + 10 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
            uk.draw_circle_on(screen, delete_color,
                               (int(screen_x), int(screen_y)),
                               scaled_width // 2 + pulse, ui(3))

        elif obj_type == 'decoration':
            # Bottom-anchored (see Decoration's class docstring), so the
            # pulse circle is centered on the trunk/base point rather than
            # the sprite's vertical middle like the stone/transition case
            # above.
            screen_x = (obj.x * RENDER_SCALE) - camera_x
            screen_y = (obj.y * RENDER_SCALE) - camera_y
            scaled_width = int(obj.width * RENDER_SCALE)

            pulse = int(20 + 10 * abs(pygame.time.get_ticks() % 1000 - 500) / 500)
            uk.draw_circle_on(screen, delete_color,
                               (int(screen_x), int(screen_y)),
                               scaled_width // 2 + pulse, ui(3))

        mouse_pos = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        uk.draw_line_on(screen, delete_color,
                         (mouse_pos[0] - ui(10), mouse_pos[1] - ui(10)),
                         (mouse_pos[0] + ui(10), mouse_pos[1] + ui(10)), ui(3))
        uk.draw_line_on(screen, delete_color,
                         (mouse_pos[0] + ui(10), mouse_pos[1] - ui(10)),
                         (mouse_pos[0] - ui(10), mouse_pos[1] + ui(10)), ui(3))

    def _build_diagonal_collision_chain(self, start_x, start_y, end_x, end_y):
        """Walk the grid cells from (start_x, start_y) to (end_x, end_y)
        and return the ordered chain of tile-sized CollisionObjects that
        approximate that line -- the "staircase" used for a Shift+drag
        diagonal wall.

        This walks actual grid cells (a Bresenham line over cell indices,
        matching the same `int(coord / step) * step` snapping the normal
        rectangular tool already uses) rather than sampling points along
        the raw, unsnapped drag line -- sampling-and-centering was the
        earlier bug: it sized boxes to the grid but never actually
        snapped their *position* to it, so the run drifted off-grid.

        Plain Bresenham can take a diagonal step (x and y both move in the
        same step), which only touches the previous cell at a corner --
        enough for a player to slip through on the diagonal. Whenever
        that happens here, an extra edge-adjacent cell is inserted so the
        run stays fully edge-connected the whole way (a "supercover"
        line), the same guarantee a normal rectangular wall gives for
        free. `step` is the current grid-snap size when snapping is on,
        or TILE_SIZE otherwise, so the run stays tile-aligned either way.
        """
        step = self.grid_snap_size if (self.grid_snap and self.grid_snap_size > 0) else TILE_SIZE

        gx0 = int(start_x / step)
        gy0 = int(start_y / step)
        gx1 = int(end_x / step)
        gy1 = int(end_y / step)

        if gx0 == gx1 and gy0 == gy1:
            # Too short a drag yet to have crossed into a second cell --
            # preview a single ordinary box so a fresh/tiny Shift-drag
            # still shows something instead of an empty list.
            return [CollisionObject(gx0 * step, gy0 * step, step, step, self.current_room_name)]

        dx = abs(gx1 - gx0)
        dy = -abs(gy1 - gy0)
        sx = 1 if gx0 < gx1 else -1
        sy = 1 if gy0 < gy1 else -1
        err = dx + dy

        cells = [(gx0, gy0)]
        gx, gy = gx0, gy0
        while (gx, gy) != (gx1, gy1):
            e2 = 2 * err
            moved_x = False
            moved_y = False
            if e2 >= dy:
                err += dy
                gx += sx
                moved_x = True
            if e2 <= dx:
                err += dx
                gy += sy
                moved_y = True
            if moved_x and moved_y:
                # Diagonal step -- insert the in-between cell so this
                # step stays edge-connected instead of corner-only.
                cells.append((gx - sx, gy))
            cells.append((gx, gy))

        return [CollisionObject(cx * step, cy * step, step, step, self.current_room_name)
                for cx, cy in cells]

    def _finalize_diagonal_collision_placement(self, room_name):
        """Finish placing a Shift-dragged diagonal collision run.

        Turns the previewed staircase of boxes into real CollisionObjects,
        tags them all with a shared diagonal_group_id (purely cosmetic --
        see CollisionObject.diagonal_group_id) and pushes a single undo
        entry for the whole run via on_collision_group_placed, so Ctrl+Z
        removes the entire diagonal wall in one step instead of one little
        box at a time.
        """
        boxes = self.preview_collision_group
        self.preview_collision_group = []

        if len(boxes) < 2:
            # A drag too short to have produced a real run -- nothing to
            # place (mirrors a near-zero rectangular drag being dropped).
            return

        group_id = uuid.uuid4().hex
        placed = []

        if self.room_manager:
            room = self.room_manager.get_room_by_name(room_name)
            if room:
                if not hasattr(room, 'collision_objects'):
                    room.collision_objects = []
                for box in boxes:
                    collision_obj = CollisionObject(
                        box.x, box.y, box.width, box.height, room_name, group_id
                    )
                    room.collision_objects.append(collision_obj)
                    placed.append(collision_obj)
                self.collision_manager.collision_objects[room_name] = room.collision_objects

        if not placed:
            return

        if hasattr(self, 'on_collision_group_placed') and self.on_collision_group_placed:
            # Preferred path: host editor records the whole run as one
            # grouped undo entry (e.g. an 'area_add' with one item per box).
            self.on_collision_group_placed(placed, room_name)
        elif hasattr(self, 'on_collision_placed') and self.on_collision_placed:
            # Fallback if the host hasn't wired the group hook yet -- still
            # fully functional, just costs one undo step per box in the run.
            for collision_obj in placed:
                self.on_collision_placed(collision_obj, room_name)

    def _finalize_collision_placement(self, room_name):
        """Finish placing a collision wall after dragging"""
        if self.collision_diagonal_mode:
            self._finalize_diagonal_collision_placement(room_name)
            return

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

    @staticmethod
    def _clamp_channel(value):
        return max(0, min(255, int(value)))

    def _sync_hex_text(self):
        """Refresh the hex field's text from the current self.region_color.
        Call this any time region_color changes from something other than
        the hex field itself (slider drag, spin arrows, typed channel,
        reset)."""
        r, g, b = self.region_color
        self.region_hex_text = f"{r:02X}{g:02X}{b:02X}"

    def _commit_hex_text(self):
        """Parse self.region_hex_text (typed by the user) into region_color.
        Invalid/partial input is simply discarded and the field snaps back
        to match the current color."""
        text = self.region_hex_text.strip()
        if len(text) == 6:
            try:
                r = int(text[0:2], 16)
                g = int(text[2:4], 16)
                b = int(text[4:6], 16)
                self.region_color = (r, g, b)
            except ValueError:
                pass
        self._sync_hex_text()

    def _set_region_channel_from_mouse_x(self, channel_idx, mouse_x, bar_rect):
        """Horizontal R/G/B gradient bar: x position maps that one channel
        to 0-255, the other two channels are untouched."""
        frac = (mouse_x - bar_rect.left) / bar_rect.width
        value = self._clamp_channel(round(frac * 255))
        channel = list(self.region_color)
        channel[channel_idx] = value
        self.region_color = tuple(channel)
        self._sync_hex_text()

    def _get_channel_gradient_surface(self, channel_idx, w, h):
        """Horizontal black -> full-channel-color gradient (e.g. black to
        pure red for channel 0). Doesn't depend on the current color, so
        it's cached once per channel/size and reused."""
        cache = self._channel_gradient_cache.get(channel_idx)
        if cache and cache[0] == (w, h):
            return cache[1]

        ramp = np.linspace(0, 255, w, dtype=np.uint8)
        arr = np.zeros((w, h, 3), dtype=np.uint8)
        arr[:, :, channel_idx] = ramp[:, np.newaxis]
        surf = pygame.surfarray.make_surface(arr)
        self._channel_gradient_cache[channel_idx] = ((w, h), surf)
        return surf

    def _save_region_settings_for_type(self, region_type):
        """Snapshot the current opacity/wave/seed/color/variant scratch
        fields into the per-type cache, so they aren't lost/overwritten
        when the palette selection switches to a different region type."""
        self.region_settings_by_type[region_type] = {
            'opacity': self.region_opacity,
            'wave_amount': self.region_wave_amount,
            'seed': self.region_seed,
            'color': self.region_color,
            'variant': self.region_variant,
        }

    def _load_region_settings_for_type(self, region_type):
        """Restore the scratch fields for region_type from the per-type
        cache (or sensible defaults if it's never been configured), so
        selecting e.g. Lava after tinting Water doesn't carry Water's
        tint over."""
        settings = self.region_settings_by_type.get(region_type)
        if settings is None:
            settings = {
                'opacity': 100,
                'wave_amount': 100,
                'seed': 0,
                'color': (255, 255, 255),
                'variant': 0,
            }
        self.region_opacity = settings['opacity']
        self.region_wave_amount = settings['wave_amount']
        self.region_seed = settings['seed']
        self.region_seed_text = str(settings['seed'])
        self.region_color = settings['color']
        self.region_variant = settings['variant']
        self._sync_hex_text()

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

        self._open_event_editor_for_box(box, room_name)

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

        self._open_event_editor_for_box(box, room_name)

    def _open_event_editor_for_box(self, box, room_name):
        """Immediately pop the Event Editor open for a trigger box that was
        just placed, pre-filled with whatever conditions/actions it was
        created with (self.trigger_box_conditions/self.trigger_box_actions —
        set by whatever wired up placement — usually empty for a brand new
        box). No-op if no FlagManager/event editor is wired up, matching the
        same "disabled until connected" behavior the old palette Event
        button had.

        Saving from here writes straight back onto the box itself via
        TriggerBox.open_event_editor()/_on_event_editor_save(), same as
        editing an already-placed box by clicking on it."""
        if self.event_editor is None:
            return
        self.event_editor.set_current_room(room_name)
        box.open_event_editor(self.event_editor)


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
        mouse_pos = event.dict.get('_room_editor_raw_pos', getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos()))

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
            # If the cursor is over the variant selector, scroll through its
            # rows instead of scrolling the palette grid beneath it.
            variant_selector_rect = self.ui_rects.get('variant_selector_rect')
            if (variant_selector_rect and variant_selector_rect.collidepoint(mouse_pos)
                    and isinstance(self.selected_object, dict)
                    and self.selected_object.get('has_variants', False)):
                variants = self.selected_object.get('variants', [])
                if variants:
                    cols = self._variant_items_per_row()
                    total_rows = (len(variants) + cols - 1) // cols
                    visible_rows = min(total_rows, 3)
                    max_row_scroll = max(0, total_rows - visible_rows)
                    self.variant_scroll -= event.y
                    self.variant_scroll = max(0, min(self.variant_scroll, max_row_scroll))
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

            info_rect = self.ui_rects.get('info_button')
            if event.button == 1 and info_rect and info_rect.collidepoint(mouse_pos):
                self.show_keybinds_popup = not self.show_keybinds_popup
                return

            # While the keybinds popup is open, any other click just closes
            # it — nothing underneath (palette or world) should react to a
            # click that was really the person dismissing the popup.
            if self.show_keybinds_popup:
                self.show_keybinds_popup = False
                return

            if event.button == 3:
                if not self._is_in_palette(mouse_pos[0], mouse_pos[1]):
                    if self.hovered_object and self.hovered_object_type:
                        group_id = (getattr(self.hovered_object, 'diagonal_group_id', None)
                                    if self.hovered_object_type == 'collision' else None)
                        if group_id:
                            # Part of a Shift-dragged diagonal run -- delete
                            # every segment together instead of just the one
                            # box under the cursor.
                            self._delete_collision_group(group_id)
                        else:
                            self._delete_object(self.hovered_object, self.hovered_object_type)
                        self.hovered_object = None
                        self.hovered_object_type = None
                return

            # Left-click: place an object or interact with palette/UI
            if event.button == 1:
                # Edit an EXISTING trigger box's own conditions/actions in
                # place — same event editor a newly placed box now opens
                # automatically (see _open_event_editor_for_box).
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

                # All the panel-control checks below (settings panel inputs,
                # opacity/color sliders, dropdowns, checkboxes) only apply while
                # the panel is visible — skip them when hidden so a click doesn't
                # land on stale rects left over from before the panel was closed.
                if self.palette_visible:
                    # Check if clicking on level input box
                    if self._is_level_input_clicked(mouse_pos):
                        self.gate_level_input_active = True
                        return

                    # Check if clicking the Gate Character cycle arrows
                    if (self.selected_object and isinstance(self.selected_object, dict)
                            and self.selected_object.get('object_type') == 'level_gate'):
                        left_arrow = self.ui_rects.get('gate_char_arrow_left')
                        if left_arrow and left_arrow.collidepoint(mouse_pos):
                            self._cycle_gate_character(-1)
                            return
                        right_arrow = self.ui_rects.get('gate_char_arrow_right')
                        if right_arrow and right_arrow.collidepoint(mouse_pos):
                            self._cycle_gate_character(1)
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

                    # Ambient Sound settings: cycle BGS asset and tune audible radius.
                    if (self.selected_object and isinstance(self.selected_object, dict)
                            and self.selected_object.get('object_type') == 'ambient_sound'):
                        left = self.ui_rects.get('ambient_sound_left')
                        right = self.ui_rects.get('ambient_sound_right')
                        minus = self.ui_rects.get('ambient_distance_minus')
                        plus = self.ui_rects.get('ambient_distance_plus')
                        if left and left.collidepoint(mouse_pos):
                            self._cycle_ambient_sound(-1)
                            return
                        if right and right.collidepoint(mouse_pos):
                            self._cycle_ambient_sound(1)
                            return
                        if minus and minus.collidepoint(mouse_pos):
                            self.ambient_sound_max_distance = max(32, self.ambient_sound_max_distance - 32)
                            return
                        if plus and plus.collidepoint(mouse_pos):
                            self.ambient_sound_max_distance = min(4096, self.ambient_sound_max_distance + 32)
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
                                self._sync_hex_text()
                                return

                            for key, idx in (('r', 0), ('g', 1), ('b', 2)):
                                bar_rect = self.ui_rects.get(f'region_{key}_bar')
                                if bar_rect and bar_rect.collidepoint(mouse_pos):
                                    if key == 'r':
                                        self._region_r_dragging = True
                                    elif key == 'g':
                                        self._region_g_dragging = True
                                    else:
                                        self._region_b_dragging = True
                                    self._set_region_channel_from_mouse_x(idx, mouse_pos[0], bar_rect)
                                    return

                                spin_text_rect = self.ui_rects.get(f'region_{key}_spin_text')
                                if spin_text_rect and spin_text_rect.collidepoint(mouse_pos):
                                    self.region_channel_input_active = key
                                    self.region_channel_text = str(self.region_color[idx])
                                    return

                                spin_up_rect = self.ui_rects.get(f'region_{key}_spin_up')
                                if spin_up_rect and spin_up_rect.collidepoint(mouse_pos):
                                    channel = list(self.region_color)
                                    channel[idx] = self._clamp_channel(channel[idx] + 1)
                                    self.region_color = tuple(channel)
                                    self._sync_hex_text()
                                    return

                                spin_down_rect = self.ui_rects.get(f'region_{key}_spin_down')
                                if spin_down_rect and spin_down_rect.collidepoint(mouse_pos):
                                    channel = list(self.region_color)
                                    channel[idx] = self._clamp_channel(channel[idx] - 1)
                                    self.region_color = tuple(channel)
                                    self._sync_hex_text()
                                    return

                            hex_rect = self.ui_rects.get('region_hex_input')
                            if hex_rect and hex_rect.collidepoint(mouse_pos):
                                self.region_hex_input_active = True
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
                if self.region_channel_input_active:
                    key = self.region_channel_input_active
                    idx = {'r': 0, 'g': 1, 'b': 2}[key]
                    self.region_channel_input_active = None
                    try:
                        value = self._clamp_channel(int(self.region_channel_text or 0))
                    except ValueError:
                        value = self.region_color[idx]
                    channel = list(self.region_color)
                    channel[idx] = value
                    self.region_color = tuple(channel)
                    self._sync_hex_text()
                if self.region_hex_input_active:
                    self.region_hex_input_active = False
                    self._commit_hex_text()

                # Finish placing collision wall if we're in the middle of it
                if self.placing_collision:
                    self._finalize_collision_placement(room_name)
                    self.placing_collision = False
                    self.collision_diagonal_mode = False
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
                            # Shift+drag places a diagonal "staircase" run
                            # instead of one rectangular box -- decided once
                            # up front at drag-start, same as every other
                            # drag gesture here, rather than re-checked every
                            # frame (which would let releasing Shift
                            # mid-drag switch modes under the cursor).
                            self.collision_diagonal_mode = bool(
                                pygame.key.get_mods() & pygame.KMOD_SHIFT)
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

        # Dragging the water color R gradient bar
        if event.type == pygame.MOUSEMOTION and self._region_r_dragging:
            bar_rect = self.ui_rects.get('region_r_bar')
            if bar_rect:
                self._set_region_channel_from_mouse_x(0, mouse_pos[0], bar_rect)
            return

        # Dragging the water color G gradient bar
        if event.type == pygame.MOUSEMOTION and self._region_g_dragging:
            bar_rect = self.ui_rects.get('region_g_bar')
            if bar_rect:
                self._set_region_channel_from_mouse_x(1, mouse_pos[0], bar_rect)
            return

        # Dragging the water color B gradient bar
        if event.type == pygame.MOUSEMOTION and self._region_b_dragging:
            bar_rect = self.ui_rects.get('region_b_bar')
            if bar_rect:
                self._set_region_channel_from_mouse_x(2, mouse_pos[0], bar_rect)
            return

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._region_opacity_dragging = False
            self._region_wave_dragging = False
            self._region_r_dragging = False
            self._region_g_dragging = False
            self._region_b_dragging = False

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

            if self.region_channel_input_active:
                key = self.region_channel_input_active
                idx = {'r': 0, 'g': 1, 'b': 2}[key]
                if event.key == pygame.K_BACKSPACE:
                    self.region_channel_text = self.region_channel_text[:-1]
                elif event.key == pygame.K_RETURN or event.key == pygame.K_ESCAPE:
                    self.region_channel_input_active = None
                    try:
                        value = self._clamp_channel(int(self.region_channel_text or 0))
                    except ValueError:
                        value = self.region_color[idx]
                    channel = list(self.region_color)
                    channel[idx] = value
                    self.region_color = tuple(channel)
                    self._sync_hex_text()
                elif event.unicode.isdigit():
                    if len(self.region_channel_text) < 3:
                        self.region_channel_text += event.unicode
                return

            if self.region_hex_input_active:
                if event.key == pygame.K_BACKSPACE:
                    self.region_hex_text = self.region_hex_text[:-1]
                elif event.key == pygame.K_RETURN or event.key == pygame.K_ESCAPE:
                    self.region_hex_input_active = False
                    self._commit_hex_text()
                elif event.unicode and event.unicode.upper() in "0123456789ABCDEF":
                    if len(self.region_hex_text) < 6:
                        self.region_hex_text += event.unicode.upper()
                return

            if event.key == pygame.K_g:
                self._toggle_grid_snap()
            elif event.key == pygame.K_h:
                self.show_grid = not self.show_grid
            elif (event.key == pygame.K_r and self.hovered_object_type == 'fishing_area'
                  and self.hovered_object is not None):
                # Rotate the hovered fishing area's jump direction. Mutates
                # the live FishingArea in place -- it's already the exact
                # instance saved with the room (same treatment chest loot
                # assignment gets, see on_chest_loot_changed above), so no
                # separate placement/undo step is needed for this to stick.
                self.hovered_object.cycle_direction(
                    -1 if (pygame.key.get_mods() & pygame.KMOD_SHIFT) else 1
                )
            elif event.key == pygame.K_ESCAPE or event.key == pygame.K_F3:
                if self.placing_collision:
                    self.placing_collision = False
                    self.preview_collision = None
                    self.collision_diagonal_mode = False
                    self.preview_collision_group = []
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

    def update(self, dt, mouse_pos, camera_x, camera_y, editor_zoom=1.0):
        """Tick editor state: animate category tabs, update world-mouse coords,
        rebuild drag-resize previews, and compute palette scroll limits.

        editor_zoom is the room editor's Ctrl+scroll continuous zoom factor
        (RoomEditor._effective_editor_zoom()). mouse_pos is always the real
        screen position, but while zoomed the world is rendered into a
        larger virtual viewport before being scaled down onto the real
        screen — so a real screen pixel corresponds to mouse_pos / editor_zoom
        virtual pixels, not mouse_pos directly. Click *events* already get
        this correction via RoomEditor._zoom_adjust_event() before reaching
        the tileset editor, which is why tile placement tracks the cursor
        correctly at any zoom; object placement instead derives its position
        from mouse_world_x/y computed here every frame, so the same
        correction has to be applied on this path too, matching
        RoomEditor._screen_to_world().
        """
        if not self.active:
            if self._owns_text_cursor:
                uk.set_text_cursor(False)
                self._owns_text_cursor = False
            return

        self.anim_timer += dt

        for category in self.categories.keys():
            target = 1.0 if category == self.current_category else 0.0
            self.category_hover[category] += (target - self.category_hover[category]) * dt * 10

        mouse_x, mouse_y = mouse_pos
        if editor_zoom != 1.0:
            mouse_x = mouse_x / editor_zoom
            mouse_y = mouse_y / editor_zoom
        self.mouse_world_x = (mouse_x + camera_x) / RENDER_SCALE
        self.mouse_world_y = (mouse_y + camera_y) / RENDER_SCALE

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
            # Keep the per-type settings cache in sync with whatever the
            # sliders currently hold, so this region type's tint/opacity/etc.
            # survive switching to a different region type and back.
            if self.selected_object.get('is_animated_region', False):
                self._save_region_settings_for_type(
                    self.selected_object.get('region_type', 'water')
                )
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
            if self.collision_diagonal_mode:
                self.preview_collision_group = self._build_diagonal_collision_chain(
                    self.collision_start_x, self.collision_start_y,
                    self.mouse_world_x, self.mouse_world_y)
                self.preview_collision = None
            elif self.grid_snap:
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

                self.preview_collision = CollisionObject(
                    int(min_x), int(min_y), int(width), int(height), self.current_room_name
                )
                self.preview_collision_group = []
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
                    int(min_x), int(min_y), int(width), int(height), self.current_room_name
                )
                self.preview_collision_group = []

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
            category_start_y = self.palette_y + ui(45)
            objects_start_y = category_start_y + len(self.categories) * ui(40) + ui(20) - self.scroll_offset

            for i, obj in enumerate(objects):
                row = i // self.items_per_row
                col = i % self.items_per_row

                item_x = self.palette_x + self.palette_padding + col * (self.item_size + self._item_col_gap)
                item_y = objects_start_y + row * (self.item_size + self._item_row_gap)

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
        total_height = rows * (self.item_size + self._item_row_gap)
        category_section_height = len(self.categories) * ui(40) + ui(20)
        # Mirrors draw_palette's dynamic bottom reservation (settings panel +
        # variant selector, each 0 when there's nothing to show) so how far
        # the grid can scroll always matches how much of it is actually
        # visible on screen.
        reserved_bottom = self._settings_panel_content_height() + self._variant_selector_content_height()
        available_height = max(ui(100), self.palette_height - (ui(45) + category_section_height + reserved_bottom))
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

            text = self.font.render("Click to place transition spawn area",
                                     color=uk.Theme.GOLD_BRIGHT, height=ui_text(18))
            text_bg = pygame.Surface((text.get_width() + ui(20), text.get_height() + ui(10)), pygame.SRCALPHA)
            text_bg.fill((0, 0, 0, 180))
            bg_x = (self.screen_width - text.get_width()) // 2 - ui(10)
            screen.blit(text_bg, (bg_x, ui(10)))
            screen.blit(text, ((self.screen_width - text.get_width()) // 2, ui(15)))

            # Draw the rectangular transition spawn area
            rect = pygame.Rect(int(screen_x), int(screen_y),
                               int(screen_width), int(screen_height))

            # Semi-transparent blue fill
            fill_surface = pygame.Surface((int(screen_width), int(screen_height)), pygame.SRCALPHA)
            fill_surface.fill((100, 200, 255, 100))
            screen.blit(fill_surface, (int(screen_x), int(screen_y)))

            # Border
            screen.draw_rect( (0, 150, 255), rect, 3)

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

            screen.draw_line( (255, 255, 0),
                             (center_x - ui(15), center_y),
                             (center_x + ui(15), center_y), 2)
            screen.draw_line( (255, 255, 0),
                             (center_x, center_y - ui(15)),
                             (center_x, center_y + ui(15)), 2)

            # Draw dimensions text
            if screen_width > 50 and screen_height > 30:
                dims_text = f"{spawn_width} x {spawn_height}"
                dims_surface = self.font.render(dims_text, color=uk.Theme.TEXT_PRIMARY, height=ui_text(12))
                dims_rect = dims_surface.get_rect(center=(center_x, center_y))

                # Text background
                bg_rect = dims_rect.inflate(ui(8), ui(4))
                bg_surface = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
                bg_surface.fill((0, 0, 0, 180))
                screen.blit(bg_surface, bg_rect.topleft)
                screen.blit(dims_surface, dims_rect)

            return

        if self.placing_collision and self.collision_diagonal_mode and self.preview_collision_group:
            draw_collision_group(screen, self.preview_collision_group, camera_x, camera_y,
                                 RENDER_SCALE, dev_mode=True, selected=True)
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
                and not self._is_in_palette(*getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos()))):
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

        mouse_pos = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        if self._is_in_palette(mouse_pos[0], mouse_pos[1]):
            return

        # Decoration ghosts are drawn by the caller (room_editor) instead,
        # interleaved with already-placed decorations by Y position — see
        # draw_decoration_preview(). That way a tree being placed behind
        # another tree previews as behind it, rather than always drawing
        # on top of every decoration in the room like a flat, unsorted
        # overlay would.
        if self.selected_object.get('object_type') == 'decoration':
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
                             (center_x - ui(5), center_y), (center_x + ui(5), center_y), 2)
            pygame.draw.line(guide_surf, self.colors['snap_guide'],
                             (center_x, center_y - ui(5)), (center_x, center_y + ui(5)), 2)
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

            screen.draw_circle( self.colors['accent'], (int(screen_x), int(screen_y)), 3)
            screen.draw_circle( self.colors['text'], (int(screen_x), int(screen_y)), 1)

    def _decoration_preview_eligible(self):
        """Whether a decoration ghost should currently be previewed for
        placement. Mirrors the guard order at the top of draw_preview()
        (exclusive placement modes, disabled objects, palette hover, an
        armed item) but only for the decoration case, since that's the
        one room_editor draws separately for Y-sorting — see
        draw_decoration_preview().
        """
        if not self.active or not self.selected_object or not isinstance(self.selected_object, dict):
            return False
        if self.selected_object.get('object_type') != 'decoration':
            return False
        if (self.flying_pad_path_editor.active
                or self.nimbus_cloud_path_editor.active
                or self.placing_transition_spawn):
            return False
        if self._is_object_disabled(self.selected_object):
            return False
        if self._item_armed():
            return False
        mouse_pos = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        if self._is_in_palette(mouse_pos[0], mouse_pos[1]):
            return False
        return True

    def get_pending_decoration_preview_y(self):
        """World-space Y of the decoration about to be placed (the same
        bottom/trunk anchor used by placed Decoration objects), or None if
        nothing is currently being previewed for placement. Lets the
        caller (room_editor) work out where in its Y-sorted decoration
        pass the ghost belongs, before actually drawing it — see
        draw_decoration_preview().
        """
        if not self._decoration_preview_eligible():
            return None
        return self.preview_y

    def draw_decoration_preview(self, screen, camera_x, camera_y):
        """Draw the ghost preview for a decoration about to be placed.

        Split out from draw_preview() so room_editor can draw this at the
        correct point in its Y-sorted decoration pass — a tree about to be
        placed behind another tree should preview as behind it, instead of
        always rendering on top of every decoration in the room. Callers
        should check get_pending_decoration_preview_y() first to decide
        where this belongs in that sort.
        """
        if not self._decoration_preview_eligible():
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
                             (center_x - ui(5), center_y), (center_x + ui(5), center_y), 2)
            pygame.draw.line(guide_surf, self.colors['snap_guide'],
                             (center_x, center_y - ui(5)), (center_x, center_y + ui(5)), 2)
            screen.blit(guide_surf, (int(grid_screen_x), int(grid_screen_y)))

        if self.selected_object.get('has_variants', False):
            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            if variant:
                obj_sprite = variant.get('sprite')
                scaled_width = int(variant.get('width', 32) * RENDER_SCALE)
                scaled_height = int(variant.get('height', 32) * RENDER_SCALE)
            else:
                obj_sprite = self.selected_object.get('sprite')
                scaled_width = int(self.selected_object.get('width', 32) * RENDER_SCALE)
                scaled_height = int(self.selected_object.get('height', 32) * RENDER_SCALE)
        else:
            obj_sprite = self.selected_object.get('sprite')
            scaled_width = int(self.selected_object.get('width', 32) * RENDER_SCALE)
            scaled_height = int(self.selected_object.get('height', 32) * RENDER_SCALE)

        if not obj_sprite:
            return

        scaled_sprite = pygame.transform.scale(obj_sprite, (scaled_width, scaled_height))
        preview_surf = scaled_sprite.copy()
        preview_surf.set_alpha(100)

        # Bottom-anchored, same convention as Decoration.get_render_info()
        # and the placed decoration's (x, y) trunk/base point.
        preview_x = int(screen_x - scaled_width // 2)
        preview_y = int(screen_y - scaled_height)

        screen.blit(preview_surf, (preview_x, preview_y))

        screen.draw_circle( self.colors['accent'], (int(screen_x), int(screen_y)), 3)
        screen.draw_circle( self.colors['text'], (int(screen_x), int(screen_y)), 1)

    def _in_view(self, world_x, world_y, camera_x, camera_y, margin=ui(160),
                 world_w=0, world_h=0):
        """Cheap screen-space visibility check used to skip drawing objects
        that can't possibly be on screen. `margin` is generous slack (in
        screen pixels) to cover sprite size/anchoring we don't know exactly
        here, so nothing pops in/out at the edge. Pass `world_w`/`world_h`
        for objects with a real footprint (e.g. collision boxes) so a large
        object isn't culled just because its (x, y) origin corner happens
        to be off-screen while the rest of it is still visible. Only ever
        used to *skip* draw calls, never to change what's drawn, so it
        can't hide anything that would actually be visible; worst case a
        few extra objects just outside the edge still get drawn.

        These per-object draw loops previously drew every object in the
        room every frame, whether or not it was anywhere near the camera —
        harmless for a small room, but with a large room and everything on
        screen at once (which is exactly what happens while zoomed out),
        that was a lot of unnecessary draw() calls piling up.
        """
        left = (world_x * RENDER_SCALE) - camera_x
        top = (world_y * RENDER_SCALE) - camera_y
        right = left + world_w * RENDER_SCALE
        bottom = top + world_h * RENDER_SCALE
        return (right >= -margin and left <= self.screen_width + margin and
                bottom >= -margin and top <= self.screen_height + margin)

    def _center_obj_in_view(self, obj, camera_x, camera_y, margin=ui(160)):
        """Same as _in_view, but for the many room objects here (pads,
        clouds, doors, chests, gates, save points, world map objects,
        regions) whose .x/.y is their center, not a top-left corner."""
        w = getattr(obj, 'width', 0) or 0
        h = getattr(obj, 'height', 0) or 0
        return self._in_view(obj.x - w / 2, obj.y - h / 2, camera_x, camera_y,
                              margin=margin, world_w=w, world_h=h)

    def draw_collision_objects(self, screen, camera_x, camera_y):
        """Draw all collision walls in the current room.

        Boxes that share a diagonal_group_id (a Shift-dragged diagonal
        run -- see CollisionObject.diagonal_group_id) are collected and
        drawn together as one merged angled quad via draw_collision_group,
        instead of each rendering its own little red square, so a
        diagonal wall reads as one smooth line rather than a staircase.
        Ungrouped boxes still draw individually exactly as before.
        """
        if not self.current_room_name:
            return

        collision_objs = self.collision_manager.get_collision_objects(self.current_room_name)

        diagonal_groups = {}
        for collision_obj in collision_objs:
            group_id = getattr(collision_obj, 'diagonal_group_id', None)
            if group_id:
                diagonal_groups.setdefault(group_id, []).append(collision_obj)

        drawn_group_ids = set()
        for collision_obj in collision_objs:
            group_id = getattr(collision_obj, 'diagonal_group_id', None)

            if group_id:
                if group_id in drawn_group_ids:
                    continue
                group_boxes = diagonal_groups[group_id]
                if not any(self._in_view(b.x, b.y, camera_x, camera_y,
                                          world_w=b.width, world_h=b.height) for b in group_boxes):
                    drawn_group_ids.add(group_id)
                    continue
                draw_collision_group(screen, group_boxes, camera_x, camera_y,
                                     RENDER_SCALE, dev_mode=True, selected=False)
                drawn_group_ids.add(group_id)
                continue

            if not self._in_view(collision_obj.x, collision_obj.y, camera_x, camera_y,
                                  world_w=collision_obj.width, world_h=collision_obj.height):
                continue
            draw_collision_object(screen, collision_obj, camera_x, camera_y,
                                  RENDER_SCALE, dev_mode=True, selected=False)

    def draw_animated_regions(self, screen, camera_x, camera_y, show_handles=True,
                               show_fill=True, show_border=True):
        """Draw all water/grass regions in the current room (editor overlay only).

        show_handles=False keeps the region fill/border visible but hides the
        yellow corner drag handles — used while the tile editor is active,
        since those handles belong to region editing, not tile painting.

        show_fill=False skips the translucent fill (border/handles still
        draw) — pass this only when the caller guarantees an opaque draw
        covers this same area immediately afterward (see
        draw_animated_region's docstring for why).

        show_border=False skips each region's outline rect too. Pass the
        active editor's show_grid state here — with the grid hidden, these
        outlines (usually tiled edge-to-edge on animated tiles) otherwise
        read as grid lines that never went away.
        """
        if not self.current_room_name:
            return

        regions = self.animated_region_manager.get_regions(self.current_room_name)

        for region in regions:
            # Regions are stored top-left + width/height (see the hit-test
            # at region.x <= world_x <= region.x + region.width above),
            # unlike the center-anchored objects below.
            if not self._in_view(region.x, region.y, camera_x, camera_y,
                                  world_w=region.width, world_h=region.height):
                continue
            draw_animated_region(screen, region, camera_x, camera_y,
                                 RENDER_SCALE, dev_mode=True, selected=False,
                                 show_handles=show_handles, show_fill=show_fill,
                                 show_border=show_border)

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
            if not transition.active:
                continue
            if not self._in_view(transition.x, transition.y, camera_x, camera_y,
                                  world_w=transition.width, world_h=transition.height):
                continue
            transition.draw(screen, temp_camera, RENDER_SCALE, dev_mode=True, selected=False)

    def draw_level_gates(self, screen, camera_x, camera_y, colors):
        """Draw level gates in the current room"""
        if not self.current_room_name:
            return

        gates = self.gate_manager.get_gates(self.current_room_name)

        temp_camera = self._make_camera(camera_x, camera_y)

        for gate in gates:
            if not gate.active:
                continue
            if not self._center_obj_in_view(gate, camera_x, camera_y):
                continue
            gate.draw(screen, temp_camera, colors)

    def draw_doors(self, screen, camera_x, camera_y, colors=None):
        """Draw doors in the current room (editor preview — always shows the
        closed frame regardless of is_open, since there's no player to be
        near in the editor)."""
        if not self.current_room_name:
            return

        temp_camera = self._make_camera(camera_x, camera_y)

        for door in self.door_manager.get_doors(self.current_room_name):
            if not self._center_obj_in_view(door, camera_x, camera_y):
                continue
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
            if not self._center_obj_in_view(chest, camera_x, camera_y):
                continue
            chest.draw(screen, temp_camera, colors)
            if chest.item_id:
                self._draw_chest_loot_badge(screen, chest, camera_x, camera_y)

    def _draw_chest_loot_badge(self, screen, chest, camera_x, camera_y):
        """Small floating icon (+ 'xN' if qty > 1) above a chest that
        already has loot, editor-only decoration drawn on top of the
        chest's own sprite."""
        screen_x = int(chest.x * RENDER_SCALE - camera_x)
        top_y = int((chest.y - chest.height / 2) * RENDER_SCALE - camera_y)
        badge_size = ui(96)
        badge_rect = pygame.Rect(0, 0, badge_size, badge_size)
        badge_rect.midbottom = (screen_x, top_y + ui(60))

        screen.draw_rect( (25, 25, 40), badge_rect, border_radius=4)
        screen.draw_rect( self.colors['accent'], badge_rect, 2, border_radius=4)

        icon = self._get_chest_loot_icon(chest.item_id)
        if icon:
            # Cache the badge-sized icon per item_id — was being rescaled
            # from the full-size source icon every frame for every chest
            # with assigned loot.
            if not hasattr(self, '_chest_badge_icon_cache'):
                self._chest_badge_icon_cache = {}
            badge_icon = self._chest_badge_icon_cache.get(chest.item_id)
            if badge_icon is None:
                scale = min((badge_size - ui(4)) / icon.get_width(), (badge_size - ui(4)) / icon.get_height())
                badge_icon = pygame.transform.scale(
                    icon, (max(1, int(icon.get_width() * scale)), max(1, int(icon.get_height() * scale)))
                )
                self._chest_badge_icon_cache[chest.item_id] = badge_icon
            screen.blit(badge_icon, badge_icon.get_rect(center=badge_rect.center))

        if chest.item_qty > 1:
            if not hasattr(self, '_chest_qty_label_cache'):
                self._chest_qty_label_cache = {}
            qty_surf = self._chest_qty_label_cache.get(chest.item_qty)
            if qty_surf is None:
                qty_surf = self.font_small.render(f'x{chest.item_qty}', True, self.colors['accent'])
                self._chest_qty_label_cache[chest.item_qty] = qty_surf
            qty_rect = qty_surf.get_rect(midtop=(badge_rect.centerx, badge_rect.bottom + 1))
            screen.draw_rect( (25, 25, 40), qty_rect.inflate(ui(4), 2))
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

        # Rebuilt below by every real text-entry field this frame (gate
        # level, trigger box id, region seed/channel/hex) — see
        # _resolve_field_cursor and _draw_field_box's text-entry call sites.
        self.text_field_rects = []

        # Don't show palette when path editor is active
        if self.flying_pad_path_editor.active:
            return
        if self.nimbus_cloud_path_editor.active:
            return

        # Update hover state and always draw the toggle tab
        mx, my = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        self._hover_panel_toggle = self._panel_toggle_rect().collidepoint(mx, my)
        uk.register_hoverable(self._panel_toggle_rect())

        # Advance the panel's open/close slide toward its target (chased,
        # not a fixed-duration tween — same style as TilesetEditor's panel
        # and EditorToolbar's bar).
        now_ms = pygame.time.get_ticks()
        dt = 0.0
        if self._panel_slide_last_ms is not None:
            dt = min(0.05, max(0.0, (now_ms - self._panel_slide_last_ms) / 1000.0))
        self._panel_slide_last_ms = now_ms
        target_slide = 1.0 if self.palette_visible else 0.0
        self._panel_slide_anim += (target_slide - self._panel_slide_anim) * min(1.0, dt * self._PANEL_SLIDE_RATE)
        if abs(target_slide - self._panel_slide_anim) < 0.001:
            self._panel_slide_anim = target_slide

        self._draw_panel_toggle_tab(screen)

        # Always draw the transition config dialog — it must be visible even
        # when the palette panel is hidden (user closed panel to place freely).
        self.transition_config.draw(screen)

        # Note: the Event Editor popup is drawn at the very end of this
        # method (after the settings panel etc.), not here — otherwise the
        # rest of the palette paints right over it every frame.

        # Keep drawing the panel for as long as it's still sliding, even
        # after palette_visible has already flipped to False — otherwise
        # it would vanish instantly the moment the tab is clicked, before
        # the slide even starts. palette_visible itself still flips
        # immediately (unchanged), so anything that reads it for hit-
        # testing is unaffected — only the drawn frame lags behind.
        if self._panel_slide_anim <= 0.001:
            self._resolve_field_cursor((mx, my))
            uk.update_hover_cursor((mx, my))
            return

        # Slide offset: 0 fully open, palette_width fully closed (panel
        # pushed entirely past the right edge). Every position below is
        # already computed from self.palette_x, so temporarily shifting it
        # moves the whole panel as one unit. Drawn directly to `screen` in
        # a single pass — no intermediate offscreen surface (that caused
        # visible seams and a glow halo on the toolbar bar).
        dx = round(self.palette_width * (1.0 - self._panel_slide_anim))
        self.palette_x += dx

        palette_rect = pygame.Rect(self.palette_x, self.palette_y, self.palette_width, self.palette_height)
        uk.draw_panel(screen, palette_rect, bg=(*uk.Theme.PANEL_BG[:3], 235),
                      border=uk.Theme.GOLD, border_width=2,
                      radius=uk.Theme.RADIUS_PANEL, shadow=True)

        y_pos = self.palette_y + ui(10)

        title = self.font.render("Objects", color=uk.Theme.GOLD, height=ui_text(16))
        screen.blit(title, (self.palette_x + ui(20), y_pos))

        # Info button — small '?' badge right after the title that opens the
        # full keybinds reference (see _draw_keybinds_popup).
        info_d = ui(18)
        info_x = self.palette_x + ui(20) + title.get_width() + ui(14)
        info_y = y_pos + (title.get_height() - info_d) // 2
        info_rect = pygame.Rect(info_x, info_y, info_d, info_d)
        self.ui_rects['info_button'] = info_rect
        uk.register_hoverable(info_rect)
        self._draw_info_button(screen, info_rect)

        y_pos += ui(35)

        for i, category in enumerate(self.categories.keys()):
            is_selected = category == self.current_category
            hover = self.category_hover[category]
            t = round(max(hover, 1.0 if is_selected else 0.0) * 20) / 20.0

            category_rect = pygame.Rect(
                self.palette_x + self.palette_padding,
                y_pos,
                self.palette_width - self.palette_padding * 2,
                ui(30)
            )
            uk.register_hoverable(category_rect)

            if t > 0.01:
                uk.draw_soft_glow(screen, category_rect.center,
                                  max(category_rect.w, category_rect.h) // 2,
                                  uk.Theme.GOLD, max_alpha=int(26 * t))

            bg_color = uk.lerp_color(uk.Theme.CARD_BG[:3], uk.Theme.CARD_BG_SELECTED[:3], t)
            border_color = uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD, t)
            uk.draw_rect_on(screen, (*bg_color, 255), category_rect, 0, ui(6))
            uk.draw_rect_on(screen, border_color, category_rect, 2 if is_selected else 1, ui(6))

            # Thin lit accent bar along the left edge of the active tab —
            # same "which one is current" language the settings panel's
            # selected pill controls use, just oriented for a vertical list.
            if t > 0.01:
                accent_bar = pygame.Rect(category_rect.x + ui(3), category_rect.y + ui(5),
                                          ui(3), category_rect.h - ui(10))
                uk.draw_rect_on(screen, uk.lerp_color(uk.Theme.CARD_BORDER, uk.Theme.GOLD_BRIGHT, t),
                                accent_bar, 0, 2)

            text_color = uk.lerp_color(uk.Theme.TEXT_MUTED, uk.Theme.TEXT_PRIMARY, t)
            cat_text = self.font_medium.render(category, True, text_color)
            text_rect = cat_text.get_rect(center=category_rect.center)
            screen.blit(cat_text, text_rect)

            y_pos += ui(40)

        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER,
                        pygame.Rect(self.palette_x + self.palette_padding, y_pos,
                                    self.palette_width - self.palette_padding * 2, 1), 0, 0)
        y_pos += ui(10)

        objects_start_y = y_pos

        # Reserve only as much bottom space as the variant selector and
        # settings panel actually need this frame (both can be 0 when the
        # selected object has neither), so the grid gets to use whatever
        # space they aren't using instead of a fixed chunk always being
        # carved out — this is what used to leave an empty rectangle at the
        # bottom of the palette when nothing needed it.
        settings_height = self._settings_panel_content_height()
        variant_height = self._variant_selector_content_height()
        reserved_bottom = settings_height + variant_height
        objects_content_height = max(
            ui(100), self.palette_height - (y_pos - self.palette_y) - reserved_bottom)

        clip_rect = pygame.Rect(self.palette_x, objects_start_y, self.palette_width, objects_content_height)
        screen.set_clip(clip_rect)

        objects = self.categories[self.current_category]
        current_y = objects_start_y - self.scroll_offset

        for i, obj in enumerate(objects):
            row = i // self.items_per_row
            col = i % self.items_per_row

            item_x = self.palette_x + self.palette_padding + col * (self.item_size + self._item_col_gap)
            item_y = current_y + row * (self.item_size + self._item_row_gap)

            if item_y + self.item_size < objects_start_y or item_y > objects_start_y + objects_content_height:
                continue

            self._draw_object_item(screen, obj, item_x, item_y)

        screen.set_clip(None)

        # Lay the variant selector and settings panel out bottom-up, each
        # sized to exactly what it needs (0 means "don't draw it at all").
        settings_y = self.palette_y + self.palette_height - settings_height
        variant_y = settings_y - variant_height
        self._draw_variant_selector(screen, variant_y, variant_height)
        self._draw_settings_panel(screen, settings_y, settings_height)

        # Drawn last so its dark overlay + window actually cover the whole
        # palette/settings panel instead of being painted over by them.
        if self.event_editor is not None:
            self.event_editor.draw(screen)

        self.palette_x -= dx  # restore — the shift above was only for this draw pass

        # Resolve the frame's cursor last, now that every clickable rect
        # drawn above (category tabs, info button, object cards, toggle
        # tab, variant slots, checkboxes/switches, text-entry fields) has
        # had a chance to register itself via register_hoverable, and every
        # text-entry field via text_field_rects. I-beam takes priority over
        # the generic hand cursor — see _resolve_field_cursor.
        self._resolve_field_cursor((mx, my))
        uk.update_hover_cursor((mx, my))

    def _fit_label_text(self, text, font_view, max_width):
        """Shorten text with a trailing '...' so it renders within max_width.
        The bitmap font has no ellipsis glyph, so three periods stand in for
        one. Returns the text unchanged if it already fits. Results are
        cached since this runs every frame for every visible palette card."""
        if not hasattr(self, '_label_fit_cache'):
            self._label_fit_cache = {}
        cache_key = (text, max_width)
        cached = self._label_fit_cache.get(cache_key)
        if cached is not None:
            return cached

        if font_view.size(text)[0] <= max_width:
            self._label_fit_cache[cache_key] = text
            return text

        suffix = "..."
        suffix_w = font_view.size(suffix)[0]
        if suffix_w >= max_width:
            self._label_fit_cache[cache_key] = suffix
            return suffix

        # Binary search for the longest prefix that still fits alongside
        # the "..." suffix.
        lo, hi, best = 0, len(text), ""
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = text[:mid].rstrip()
            if font_view.size(candidate)[0] + suffix_w <= max_width:
                best = candidate
                lo = mid + 1
            else:
                hi = mid - 1
        result = (best + suffix) if best else suffix
        self._label_fit_cache[cache_key] = result
        return result

    def _draw_object_item(self, screen, obj, x, y):
        """Draw a single object card in the palette — same card language
        (hover lift via glow, gold border/selection, hairline rest state)
        as EditorToolbar's Items panel thumbnails."""
        item_rect = pygame.Rect(x, y, self.item_size, self.item_size)

        is_selected = self.selected_object == obj
        is_hover = self.hover_object == obj
        is_disabled = self._is_object_disabled(obj)

        if not is_disabled:
            uk.register_hoverable(item_rect)

        t = 0.0 if is_disabled else (1.0 if is_selected else (0.6 if is_hover else 0.0))

        if t > 0.01:
            uk.draw_soft_glow(screen, item_rect.center, self.item_size // 2 + ui(6),
                              uk.Theme.GOLD, max_alpha=int(55 * t))

        if is_disabled:
            bg_color, border_color, border_width = uk.Theme.CARD_BG[:3], uk.Theme.CARD_BORDER, 1
        elif is_selected:
            bg_color, border_color, border_width = uk.Theme.CARD_BG_SELECTED[:3], uk.Theme.GOLD_BRIGHT, 2
        elif is_hover:
            bg_color, border_color, border_width = uk.Theme.CARD_BG_HOVER[:3], uk.Theme.GOLD, 1
        else:
            bg_color, border_color, border_width = uk.Theme.CARD_BG[:3], uk.Theme.CARD_BORDER, 1

        uk.draw_rect_on(screen, (*bg_color, 255), item_rect, 0, uk.Theme.RADIUS_CARD)
        uk.draw_rect_on(screen, border_color, item_rect, border_width, uk.Theme.RADIUS_CARD)

        if obj['sprite']:
            src = obj['sprite']
            # This loop runs every frame the palette is visible, over every
            # on-screen tile — re-scaling the thumbnail and re-tinting it
            # for disabled state here used to cost a fresh
            # pygame.transform.scale() (and a fill() for disabled items)
            # per item per frame regardless of whether anything changed.
            # Cache both the plain and disabled-tinted thumbnail keyed by
            # (sprite identity, item_size), same idea as the tile/sprite
            # caches used elsewhere in the editor.
            if not hasattr(self, '_palette_thumb_cache'):
                self._palette_thumb_cache = {}
            thumb_key = (id(src), self.item_size, is_disabled)
            scaled = self._palette_thumb_cache.get(thumb_key)
            if scaled is None:
                sw, sh = src.get_size()
                max_dim = self.item_size - ui(8)  # 8px padding on each axis
                scale = min(max_dim / sw, max_dim / sh)
                scaled = pygame.transform.scale(src, (max(1, int(sw * scale)), max(1, int(sh * scale))))
                if is_disabled:
                    scaled.fill((100, 100, 100, 150), special_flags=pygame.BLEND_RGBA_MULT)
                self._palette_thumb_cache[thumb_key] = scaled
            screen.blit(scaled, scaled.get_rect(center=item_rect.center))

        # Name label — same reasoning: cached per (text, color) instead of
        # re-rasterized every frame for every visible palette item.
        if not hasattr(self, '_palette_label_cache'):
            self._palette_label_cache = {}
        name_color = uk.Theme.TEXT_DIM if is_disabled else uk.Theme.TEXT_MUTED
        # Cap the label to (roughly) its own card's width before rendering,
        # so a long object name can no longer spill sideways past the card
        # and into the neighboring column's label — that horizontal overlap
        # was the main "names run into each other" issue. A couple of extra
        # px into the column gap is allowed since that space is otherwise
        # empty.
        label_max_w = self.item_size + self._item_col_gap - ui(4)
        label_text = self._fit_label_text(obj['name'], self.font_small, label_max_w)
        label_key = (label_text, name_color)
        name_text = self._palette_label_cache.get(label_key)
        if name_text is None:
            name_text = self.font_small.render(label_text, True, name_color)
            self._palette_label_cache[label_key] = name_text
        name_rect = name_text.get_rect(centerx=item_rect.centerx, top=item_rect.bottom + 2)
        screen.blit(name_text, name_rect)

        if is_disabled and obj.get('is_spawn', False):
            placed_text = self.font_small.render("PLACED", True, uk.Theme.TEXT_DIM)
            placed_rect = placed_text.get_rect(centerx=item_rect.centerx, centery=item_rect.centery)
            badge_rect = placed_rect.inflate(ui(10), ui(6))
            uk.draw_rect_on(screen, (16, 18, 26, 210), badge_rect, 0, ui(5))
            uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, badge_rect, 1, ui(5))
            screen.blit(placed_text, placed_rect)

    def _variant_items_per_row(self):
        """How many variant slots fit across one row of the selector, given
        the current palette width — same derivation as items_per_row for the
        main object grid."""
        usable_width = self.palette_width - self.palette_padding * 2
        slot = ui(50) + ui(10)  # variant_size + col_gap
        return max(1, usable_width // slot)

    def _variant_selector_content_height(self):
        """How tall the variant selector needs to be to show up to 3 rows of
        variants at once (further rows scroll with the mouse wheel). Returns
        0 when there's nothing to show, so draw_palette can skip reserving
        any space for it at all."""
        if not self.selected_object or not isinstance(self.selected_object, dict):
            return 0
        if not self.selected_object.get('has_variants', False):
            return 0
        variants = self.selected_object.get('variants', [])
        if not variants:
            return 0

        cols = self._variant_items_per_row()
        total_rows = (len(variants) + cols - 1) // cols
        visible_rows = min(total_rows, 3)
        header_h = ui(25)
        row_pitch = ui(50) + ui(26)  # variant_size + row_gap, matches the object grid's row spacing
        return header_h + visible_rows * row_pitch + ui(10)

    def _draw_variant_selector(self, screen, selector_y, selector_height):
        """Draw the variant picker as a wrapping grid (rows of slots) above
        the settings panel, when the selected object has variants. Rows
        beyond what fits scroll with the mouse wheel instead of everything
        being squeezed into one horizontally-scrolling row."""
        if selector_height <= 0:
            self.ui_rects['variant_selector_rect'] = None
            self.ui_rects['variant_rects'] = []
            return

        variants = self.selected_object.get('variants', [])
        selector_x = self.palette_x

        selector_rect = pygame.Rect(selector_x, selector_y, self.palette_width, selector_height)
        uk.draw_rect_on(screen, uk.Theme.PANEL_BG, selector_rect, 0, 0)
        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER,
                        pygame.Rect(selector_x, selector_y, self.palette_width, 1), 0, 0)
        self.ui_rects['variant_selector_rect'] = selector_rect

        title_text = self.font_small.render("SELECT VARIANT", True, uk.Theme.TEXT_MUTED)
        screen.blit(title_text, (selector_x + self.palette_padding, selector_y + ui(5)))

        variant_size = ui(50)
        col_gap = ui(10)
        row_gap = ui(26)  # matches the object grid's row-gap fix — room for the label line
        cols = self._variant_items_per_row()
        total_rows = (len(variants) + cols - 1) // cols
        visible_rows = min(total_rows, 3)
        max_row_scroll = max(0, total_rows - visible_rows)
        self.variant_scroll = max(0, min(self.variant_scroll, max_row_scroll))

        grid_top = selector_y + ui(25)
        start_x = selector_x + self.palette_padding
        label_max_w = variant_size + col_gap - ui(4)

        current_variant = self.selected_variant or self._get_current_variant(self.selected_object)

        first_index = self.variant_scroll * cols
        last_index = min(len(variants), first_index + visible_rows * cols)
        visible_variants = variants[first_index:last_index]

        self.ui_rects['variant_rects'] = []

        clip_rect = pygame.Rect(selector_x, grid_top, self.palette_width,
                                 visible_rows * (variant_size + row_gap))
        screen.set_clip(clip_rect)

        for list_idx, variant in enumerate(visible_variants):
            row = list_idx // cols
            col = list_idx % cols

            variant_x = start_x + col * (variant_size + col_gap)
            variant_y = grid_top + row * (variant_size + row_gap)
            variant_rect = pygame.Rect(variant_x, variant_y, variant_size, variant_size)

            is_selected = (current_variant == variant)
            is_hover = (self.hover_variant_index == list_idx)

            if is_selected:
                bg_color, border_color, border_width = uk.Theme.CARD_BG_SELECTED[:3], uk.Theme.KI_BLUE, 2
            elif is_hover:
                bg_color, border_color, border_width = uk.Theme.CARD_BG_HOVER[:3], uk.Theme.GOLD, 1
            else:
                bg_color, border_color, border_width = uk.Theme.CARD_BG[:3], uk.Theme.CARD_BORDER, 1

            if is_selected:
                uk.draw_soft_glow(screen, variant_rect.center, variant_size // 2 + ui(4),
                                  uk.Theme.KI_BLUE, max_alpha=60)

            uk.draw_rect_on(screen, (*bg_color, 255), variant_rect, 0, ui(8))
            uk.draw_rect_on(screen, border_color, variant_rect, border_width, ui(8))
            uk.register_hoverable(variant_rect)

            # Sprite — scaled to fit the slot (mirrors _draw_object_item's
            # scale-to-fit, needed now that variant sprites aren't all
            # small icons — a tree frame is 80x92, well past variant_size).
            if variant.get('sprite'):
                sprite = variant['sprite']
                sw, sh = sprite.get_size()
                max_dim = variant_size - ui(6)  # small padding on each axis
                scale = min(max_dim / sw, max_dim / sh, 1.0)
                scaled_w = max(1, int(sw * scale))
                scaled_h = max(1, int(sh * scale))
                scaled_sprite = pygame.transform.scale(sprite, (scaled_w, scaled_h))
                sprite_rect = scaled_sprite.get_rect(center=variant_rect.center)
                screen.blit(scaled_sprite, sprite_rect)

            # Label — truncated to the slot's own width so a long variant
            # name can't spill sideways into the next slot's label, same
            # fix as the main object grid's cards.
            label_color = uk.Theme.GOLD_BRIGHT if is_selected else uk.Theme.TEXT_MUTED
            label_str = self._fit_label_text(variant['name'], self.font_small, label_max_w)
            label_text = self.font_small.render(label_str, True, label_color)
            label_rect = label_text.get_rect(centerx=variant_rect.centerx, top=variant_rect.bottom + 2)
            screen.blit(label_text, label_rect)

            # Store rect for click detection
            self.ui_rects['variant_rects'].append({
                'rect': variant_rect,
                'variant': variant
            })

        screen.set_clip(None)

        if max_row_scroll > 0:
            hint_text = self.font_hint.render(
                f"Row {self.variant_scroll + 1}/{total_rows} \u2014 scroll for more",
                True, uk.Theme.TEXT_DIM,
            )
            hint_rect = hint_text.get_rect(
                right=selector_x + self.palette_width - self.palette_padding,
                top=selector_y + ui(5),
            )
            screen.blit(hint_text, hint_rect)

    def draw_flying_pads(self, screen, camera_x, camera_y, colors):
        """Draw flying pads in the current room"""
        if not self.current_room_name:
            return

        pads = self.flying_pad_manager.get_pads(self.current_room_name)

        temp_camera = self._make_camera(camera_x, camera_y)

        for pad in pads:
            if not pad.active:
                continue
            # Only cull the pad sprite itself, not its path preview — a
            # pad's path can span far beyond the pad, so a segment of it
            # may still be on screen even when the pad marker isn't.
            if self._center_obj_in_view(pad, camera_x, camera_y):
                pad.draw(screen, temp_camera, colors, RENDER_SCALE)
            pad.draw_path_preview(screen, temp_camera, RENDER_SCALE)

    def draw_nimbus_clouds(self, screen, camera_x, camera_y, colors):
        """Draw nimbus clouds in the current room"""
        if not self.current_room_name:
            return

        clouds = self.nimbus_cloud_manager.get_clouds(self.current_room_name)

        temp_camera = self._make_camera(camera_x, camera_y)

        for cloud in clouds:
            if not cloud.active:
                continue
            # Same reasoning as flying pads: only cull the cloud sprite,
            # never its path preview.
            if self._center_obj_in_view(cloud, camera_x, camera_y):
                cloud.draw(screen, temp_camera, colors, RENDER_SCALE)
            cloud.draw_path_preview(screen, temp_camera, RENDER_SCALE)

    def draw_save_points(self, screen, camera_x, camera_y, colors):
        """Draw save points in the current room"""
        if not self.current_room_name:
            return

        save_points = self.save_point_manager.get_save_points(self.current_room_name)

        temp_camera = self._make_camera(camera_x, camera_y)

        for save_point in save_points:
            if not save_point.active:
                continue
            if not self._center_obj_in_view(save_point, camera_x, camera_y):
                continue
            save_point.draw(screen, temp_camera, colors)

    def draw_world_map_objects(self, screen, camera_x, camera_y, colors):
        """Draw world map objects in the current room."""
        if not self.current_room_name:
            return
        temp_camera = self._make_camera(camera_x, camera_y)
        for obj in self.world_map_manager.get_objects(self.current_room_name):
            if not obj.active:
                continue
            if not self._center_obj_in_view(obj, camera_x, camera_y):
                continue
            obj.draw(screen, temp_camera, colors)

    def draw_fishing_areas(self, screen, camera_x, camera_y, colors):
        """Draw fishing areas in the current room."""
        if not self.current_room_name:
            return
        temp_camera = self._make_camera(camera_x, camera_y)
        for area in self.fishing_area_manager.get_fishing_areas(self.current_room_name):
            if not area.active:
                continue
            if not self._center_obj_in_view(area, camera_x, camera_y):
                continue
            area.draw(screen, temp_camera, colors, dev_mode=True)
            self._draw_fishing_area_direction_arrow(screen, area, camera_x, camera_y)

    def _draw_fishing_area_direction_arrow(self, screen, area, camera_x, camera_y):
        """Editor-only arrow showing which way this area's jump direction is
        currently set -- deliberately NOT part of FishingArea.draw() itself,
        since that method also runs during real gameplay and this is purely
        a room-editor authoring aid (see area.direction / cycle_direction,
        set via hover + R)."""
        sx = (area.x * RENDER_SCALE) - camera_x
        sy = (area.y * RENDER_SCALE) - camera_y
        reach = max(10, int(area.width * RENDER_SCALE * 0.35))

        dx, dy = area.get_direction_vector()
        tip = (sx + dx * reach, sy + dy * reach)
        # perpendicular unit vector, for the arrowhead's two back corners
        px, py = -dy, dx
        back = (sx + dx * reach * 0.45, sy + dy * reach * 0.45)
        spread = reach * 0.35
        left = (back[0] + px * spread, back[1] + py * spread)
        right = (back[0] - px * spread, back[1] - py * spread)

        color = (255, 230, 60)
        screen.draw_line(color, (int(sx), int(sy)), (int(back[0]), int(back[1])), 3)
        screen.draw_polygon(color, [
            (int(tip[0]), int(tip[1])),
            (int(left[0]), int(left[1])),
            (int(right[0]), int(right[1])),
        ], 0)

        # "Facing: down (R to rotate)" hint only while this exact area is
        # hovered -- otherwise every fishing area in a busy room would be
        # captioned at once.
        if self.hovered_object is area and self.hovered_object_type == 'fishing_area':
            label = self.font_small.render(
                f"Facing: {area.direction}  (R to rotate, Shift+R back)", True, color)
            screen.blit(label, (int(sx - label.get_width() / 2), int(sy + reach + ui(6))))

    def draw_ambient_sounds(self, screen, camera_x, camera_y):
        """Draw editor-only positional sound markers and audible radii."""
        for emitter in self.get_ambient_sound_objects(self.current_room_name):
            sx = int(emitter.x * RENDER_SCALE - camera_x)
            sy = int(emitter.y * RENDER_SCALE - camera_y)
            radius = max(1, int(emitter.max_distance * RENDER_SCALE))
            # Radius ring communicates exactly where the linear fade reaches 0.
            screen.draw_circle((170, 110, 255), (sx, sy), radius, 1)
            screen.draw_circle((225, 205, 255), (sx, sy), max(4, int(7 * RENDER_SCALE)), 2)
            screen.draw_circle((225, 205, 255), (sx, sy), max(1, int(2 * RENDER_SCALE)), 0)

    def draw_trigger_boxes(self, screen, camera_x, camera_y):
        """Draw all trigger box zones in the current room (dev mode only)."""
        if not self.current_room_name:
            return

        for box in self.trigger_box_manager.get_boxes(self.current_room_name):
            if not self._in_view(box.x, box.y, camera_x, camera_y,
                                  world_w=box.width, world_h=box.height):
                continue
            draw_trigger_box(
                screen, box, camera_x, camera_y, RENDER_SCALE,
                dev_mode=True, selected=False
            )

    def _settings_panel_content_height(self):
        """Compute how tall _draw_settings_panel's content actually is, so
        the panel only appears (and only takes up space) when the selected
        object actually has settings to show — otherwise this returns 0 and
        draw_palette skips the panel entirely, letting the object grid (or
        the variant selector) use that space instead."""
        inner = 0

        obj = self.selected_object
        if obj and isinstance(obj, dict):
            if obj.get('object_type') == 'level_gate':
                inner += 30  # Gate Level Req row
                inner += 30  # Gate Character row

            if obj.get('object_type') == 'door':
                inner += 30  # Permanent checkbox row
                inner += 30  # Sound picker + preview row

            if obj.get('object_type') == 'ambient_sound':
                inner += 60  # sound picker + max-distance row

            if obj.get('is_trigger_box', False):
                inner += 30  # Box ID row
                inner += 30  # Once toggle row
                inner += 30  # Requires Key toggle row
                if self.trigger_box_always_run:
                    inner += 18  # "Fires passively..." hint line
                inner += 30  # Always Run toggle row

            if obj.get('object_type') == 'world_map_object':
                current_variant = self._get_current_variant(obj)
                if current_variant and current_variant.get('type') in ('world_map', 'world_map_sign'):
                    inner += 30

            if obj.get('is_animated_region', False):
                inner += ui(20) + ui(14) + ui(16)  # Opacity label + slider + gap
                region_type = obj.get('region_type')
                if region_type in ('water',):
                    inner += ui(20) + ui(14) + ui(16)  # Wave Amount label + slider + gap
                    inner += 30  # Seed input + reroll row
                if REGION_STYLES.get(region_type, {}).get('mode', 'patch') == 'patch':
                    inner += 26  # Color label + swatch + reset row
                    inner += ui(90) + ui(10)  # SV square / hue strip + trailing gap
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
                        btn_gap = ui(6)
                        slot_w = (self.palette_width - self.palette_padding * 2
                                  - btn_gap * (num_variants - 1)) // num_variants
                        inner += ui(20) + slot_w + ui(16)  # Variant label + thumbnail row + gap

        if inner <= 0:
            return 0
        return inner + ui(24)  # top (14) + bottom (10) padding, only when there's content

    # ── Small reusable inspector-row primitives ─────────────────────────────
    # The settings panel below is a long, sequential list of rows for very
    # different controls (per selected object type), so rather than inline
    # the same "pill toggle" / "dark input box" shape a dozen times, factor
    # the couple of shapes that repeat into small helpers using the same
    # card language (hairline border, gold accent when active/lit) as the
    # rest of the dev-tool chrome.

    def _resolve_field_cursor(self, mouse_pos):
        """Switch the OS cursor to an I-beam while hovering any real
        text-entry field registered this frame via self.text_field_rects
        (gate level, trigger box id, region seed/channel/hex), same
        no-op-guarded ownership pattern as RoomEditor's hovering_text_field
        check. Call this right before update_hover_cursor so the I-beam
        wins out over the generic hand cursor those same rects also get
        from _draw_field_box's register_hoverable call."""
        hovering_text_field = any(r.collidepoint(mouse_pos) for r in self.text_field_rects)
        if hovering_text_field:
            uk.set_text_cursor(True)
            self._owns_text_cursor = True
        elif self._owns_text_cursor:
            uk.set_text_cursor(False)
            self._owns_text_cursor = False

    def _draw_field_box(self, screen, rect, active=False, radius=ui(6)):
        """Dark inset box used for every text-entry field (gate level, seed,
        hex, box id, dropdown buttons) -- lights up gold-bordered while
        actively being typed into, same convention as ModalTextInput."""
        bg = uk.Theme.CARD_BG_HOVER if active else (28, 31, 42, 255)
        border = uk.Theme.GOLD if active else uk.Theme.CARD_BORDER
        uk.draw_rect_on(screen, bg, rect, 0, radius)
        uk.draw_rect_on(screen, border, rect, 2 if active else 1, radius)
        # Hand cursor by default; text-entry call sites additionally append
        # `rect` to self.text_field_rects, which wins out with the I-beam —
        # see draw_palette's set_text_cursor call, resolved before this
        # frame's update_hover_cursor.
        uk.register_hoverable(rect)

    def _draw_onoff_pill(self, screen, rect, is_on, on_color=None):
        """Small ON/OFF toggle pill (Once / Requires Key / Always Run, etc.)."""
        on_color = on_color or uk.Theme.KI_BLUE
        bg = (*on_color, 60) if is_on else uk.Theme.CARD_BG
        border = on_color if is_on else uk.Theme.CARD_BORDER
        uk.draw_rect_on(screen, bg, rect, 0, rect.h // 2)
        uk.draw_rect_on(screen, border, rect, 2, rect.h // 2)
        text_color = uk.Theme.TEXT_PRIMARY if is_on else uk.Theme.TEXT_DIM
        label = self.font_small.render('ON' if is_on else 'OFF', True, text_color)
        screen.blit(label, label.get_rect(center=rect.center))
        uk.register_hoverable(rect)

    def _draw_mini_button(self, screen, rect, hover=False, selected=False, radius=ui(6)):
        """Small pill/rect button (Reroll, Preview, Reset, sound picker
        buttons, +/- steppers, world-map dropdown, etc.)."""
        if selected:
            bg, border, bw = (*uk.Theme.KI_BLUE, 55), uk.Theme.KI_BLUE, 2
        elif hover:
            bg, border, bw = uk.Theme.CARD_BG_HOVER, uk.Theme.GOLD, 1
        else:
            bg, border, bw = uk.Theme.CARD_BG, uk.Theme.CARD_BORDER, 1
        uk.draw_rect_on(screen, bg, rect, 0, radius)
        uk.draw_rect_on(screen, border, rect, bw, radius)
        uk.register_hoverable(rect)

    def _draw_slider_row(self, screen, track_rect, fraction, ui_key):
        """Horizontal fill-track slider (Opacity, Wave Amount) with a lit
        gold handle -- registers `ui_key` in self.ui_rects the same as the
        old track rect did, for handle_input's existing drag hit-test."""
        uk.draw_rect_on(screen, uk.Theme.CARD_BG, track_rect, 0, track_rect.h // 2)
        fill_w = int(track_rect.w * max(0.0, min(1.0, fraction)))
        if fill_w > 0:
            fill_rect = pygame.Rect(track_rect.x, track_rect.y, fill_w, track_rect.h)
            uk.draw_rect_on(screen, uk.Theme.GOLD, fill_rect, 0, track_rect.h // 2)
        uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, track_rect, 1, track_rect.h // 2)

        handle_x = track_rect.x + fill_w
        handle_center = (handle_x, track_rect.centery)
        uk.draw_soft_glow(screen, handle_center, ui(10), uk.Theme.GOLD, max_alpha=90)
        uk.draw_circle_on(screen, uk.Theme.GOLD_BRIGHT, handle_center, track_rect.h // 2 + 2)
        uk.draw_circle_on(screen, (16, 18, 26), handle_center, track_rect.h // 2 + 2, 1)

        self.ui_rects[ui_key] = track_rect

    def _settings_section_label(self, screen, text, y_pos):
        """Small caps-style muted label for a settings row (kept as plain
        text, not a full section header, to match the panel's compact row
        rhythm)."""
        surf = self.font_medium.render(text, True, uk.Theme.TEXT_SECONDARY)
        screen.blit(surf, (self.palette_x + self.palette_padding, y_pos))
        return surf

    def _draw_settings_panel(self, screen, panel_y, panel_height):
        """Draw controls and settings at the bottom of the palette. Caller
        (draw_palette) already knows panel_height is > 0 — it comes from
        _settings_panel_content_height, which returns 0 (and no panel is
        drawn at all) when the selected object has no per-object settings."""
        if panel_height <= 0:
            return

        panel_rect = pygame.Rect(self.palette_x, panel_y, self.palette_width, panel_height)
        uk.draw_rect_on(screen, uk.Theme.PANEL_BG, panel_rect, 0, 0)
        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER,
                        pygame.Rect(self.palette_x, panel_y, self.palette_width, 1), 0, 0)

        y_pos = panel_y + ui(14)

        if (self.selected_object and isinstance(self.selected_object, dict)
                and self.selected_object.get('object_type') == 'ambient_sound'):
            self._settings_section_label(screen, 'Sound:', y_pos)
            left = pygame.Rect(self.palette_x + ui(90), y_pos - ui(3), ui(24), ui(22))
            right = pygame.Rect(self.palette_x + self.palette_width - self.palette_padding - ui(24), y_pos - ui(3), ui(24), ui(22))
            self._draw_mini_button(screen, left, radius=ui(5))
            self._draw_mini_button(screen, right, radius=ui(5))
            screen.draw_polygon(uk.Theme.TEXT_PRIMARY, [
                (left.centerx + ui(3), left.centery - ui(5)), (left.centerx - ui(4), left.centery), (left.centerx + ui(3), left.centery + ui(5))
            ])
            screen.draw_polygon(uk.Theme.TEXT_PRIMARY, [
                (right.centerx - ui(3), right.centery - ui(5)), (right.centerx + ui(4), right.centery), (right.centerx - ui(3), right.centery + ui(5))
            ])
            sound_text = self.ambient_sound_name or '<no ambient files>'
            sound_surf = self.font_small.render(sound_text, True, uk.Theme.TEXT_MUTED)
            screen.blit(sound_surf, (left.right + ui(8), y_pos + 2))
            self.ui_rects['ambient_sound_left'] = left
            self.ui_rects['ambient_sound_right'] = right
            y_pos += ui(30)

            self._settings_section_label(screen, f'Radius: {self.ambient_sound_max_distance}px', y_pos)
            minus = pygame.Rect(self.palette_x + ui(180), y_pos - ui(3), ui(24), ui(22))
            plus = pygame.Rect(self.palette_x + ui(210), y_pos - ui(3), ui(24), ui(22))
            self._draw_mini_button(screen, minus, radius=ui(5))
            self._draw_mini_button(screen, plus, radius=ui(5))
            minus_txt = self.font_medium.render('-', True, uk.Theme.TEXT_PRIMARY)
            plus_txt = self.font_medium.render('+', True, uk.Theme.TEXT_PRIMARY)
            screen.blit(minus_txt, minus_txt.get_rect(center=minus.center))
            screen.blit(plus_txt, plus_txt.get_rect(center=plus.center))
            self.ui_rects['ambient_distance_minus'] = minus
            self.ui_rects['ambient_distance_plus'] = plus
            y_pos += ui(30)

        if self.selected_object and isinstance(self.selected_object, dict) and self.selected_object.get(
                'object_type') == 'level_gate':
            self._settings_section_label(screen, "Gate Level Req:", y_pos)

            input_x = self.palette_x + self.palette_padding + ui(135)
            input_y = y_pos - ui(3)
            input_width = ui(60)
            input_height = ui(25)

            input_rect = pygame.Rect(input_x, input_y, input_width, input_height)
            self._draw_field_box(screen, input_rect, active=self.gate_level_input_active)
            self.text_field_rects.append(input_rect)
            self.ui_rects['gate_level_input_rect'] = input_rect

            display_text = self.gate_level_text if self.gate_level_input_active else str(self.gate_required_level)
            text_surf = self.font_medium.render(display_text, True, uk.Theme.TEXT_PRIMARY)
            text_rect = text_surf.get_rect(center=input_rect.center)
            screen.blit(text_surf, text_rect)

            if self.gate_level_input_active:
                if int(pygame.time.get_ticks() / 500) % 2 == 0:
                    cursor_x = text_rect.right + ui(3)
                    cursor_y = input_rect.centery
                    uk.draw_line_on(screen, uk.Theme.TEXT_PRIMARY,
                                     (cursor_x, cursor_y - ui(10)),
                                     (cursor_x, cursor_y + ui(10)), 2)

            y_pos += ui(30)

            # ── Gate Character lock ─────────────────────────────────────
            # Cycle arrows through [Any, *characters]; the swatch previews
            # exactly the color the placed gate's number will use (see
            # _gate_character_color / LevelGate._load_gate_color).
            self._settings_section_label(screen, "Gate Character:", y_pos)

            arrow_size = ui(22)
            name_x = self.palette_x + self.palette_padding + ui(135)
            left_rect = pygame.Rect(name_x, y_pos - ui(3), arrow_size, arrow_size)
            right_rect = pygame.Rect(
                self.palette_x + self.palette_width - self.palette_padding - arrow_size,
                y_pos - ui(3), arrow_size, arrow_size,
            )
            self._draw_mini_button(screen, left_rect, radius=ui(6))
            self._draw_mini_button(screen, right_rect, radius=ui(6))

            ay = left_rect.centery
            screen.draw_polygon(uk.Theme.TEXT_PRIMARY, [
                (left_rect.right - ui(6), ay - ui(7)),
                (left_rect.left + ui(5), ay),
                (left_rect.right - ui(6), ay + ui(7)),
            ])
            screen.draw_polygon(uk.Theme.TEXT_PRIMARY, [
                (right_rect.left + ui(6), ay - ui(7)),
                (right_rect.right - ui(5), ay),
                (right_rect.left + ui(6), ay + ui(7)),
            ])

            self.ui_rects['gate_char_arrow_left'] = left_rect
            self.ui_rects['gate_char_arrow_right'] = right_rect

            name_txt = self._gate_character_display_name(self.gate_required_character)
            name_surf = self.font_medium.render(name_txt, True, uk.Theme.TEXT_PRIMARY)
            name_area = pygame.Rect(left_rect.right + ui(6), y_pos - ui(3),
                                     right_rect.left - left_rect.right - ui(12) - ui(20), arrow_size)
            name_clip = name_surf.get_rect()
            name_clip.width = min(name_clip.width, name_area.width)
            screen.blit(name_surf, name_area.topleft, name_clip)

            swatch_rect = pygame.Rect(right_rect.left - ui(18), y_pos, ui(14), ui(14))
            uk.draw_rect_on(screen, self._gate_character_color(self.gate_required_character), swatch_rect, 0, ui(4))
            uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, swatch_rect, 1, ui(4))

            y_pos += ui(30)

        if self.selected_object and isinstance(self.selected_object, dict) and self.selected_object.get(
                'object_type') == 'door':
            self._settings_section_label(screen, "Permanent:", y_pos)

            box_size = ui(20)
            box_x = self.palette_x + self.palette_padding + ui(100)
            box_y = y_pos - 2
            box_rect = pygame.Rect(box_x, box_y, box_size, box_size)
            self.ui_rects['door_permanent_checkbox'] = box_rect

            self._draw_field_box(screen, box_rect, active=self.door_permanent, radius=ui(5))
            if self.door_permanent:
                uk.draw_line_on(screen, uk.Theme.GOLD_BRIGHT,
                                 (box_x + ui(4), box_y + ui(10)), (box_x + ui(8), box_y + ui(15)), 2)
                uk.draw_line_on(screen, uk.Theme.GOLD_BRIGHT,
                                 (box_x + ui(8), box_y + ui(15)), (box_x + ui(16), box_y + ui(5)), 2)

            hint = self.font_small.render("(stays open once opened)", True, uk.Theme.TEXT_MUTED)
            screen.blit(hint, (box_x + box_size + ui(8), y_pos + ui(3)))

            y_pos += ui(30)

            # ── Door sound picker ───────────────────────────────────────────
            self._settings_section_label(screen, "Sound:", y_pos)

            btn_h = ui(22)
            btn_gap = ui(4)
            btn_x = self.palette_x + self.palette_padding + ui(100)
            btn_y = y_pos - 2

            door_sound_buttons = []
            for name in self.door_sound_options:
                # Short label — e.g. 'door1' -> '1' — so a row of these reads
                # like numbered tabs rather than repeating "door" each time.
                short_label = name[4:] if name.lower().startswith('door') else name
                label_surf = self.font_small.render(short_label, True, uk.Theme.TEXT_PRIMARY)
                btn_w = max(ui(24), label_surf.get_width() + ui(12))
                btn_rect = pygame.Rect(btn_x, btn_y, btn_w, btn_h)

                is_selected = (name == self.door_sound_text)
                self._draw_mini_button(screen, btn_rect, selected=is_selected, radius=ui(5))
                screen.blit(label_surf, label_surf.get_rect(center=btn_rect.center))

                door_sound_buttons.append((btn_rect, name))
                btn_x += btn_w + btn_gap

            self.ui_rects['door_sound_buttons'] = door_sound_buttons

            # Preview button — plays whatever's currently selected. Drawn as a
            # small play triangle + label rather than a unicode ▶ glyph, since
            # the bitmap font has no such glyph either.
            preview_rect = pygame.Rect(btn_x + ui(6), btn_y, ui(78), btn_h)
            preview_hover = preview_rect.collidepoint(getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos()))
            self._draw_mini_button(screen, preview_rect, hover=preview_hover, radius=ui(5))

            tri_x = preview_rect.x + ui(8)
            tri_y = preview_rect.centery
            screen.draw_polygon(uk.Theme.GOLD, [
                (tri_x, tri_y - ui(5)), (tri_x, tri_y + ui(5)), (tri_x + ui(8), tri_y)
            ])
            preview_label = self.font_small.render("Preview", True, uk.Theme.TEXT_PRIMARY)
            screen.blit(preview_label, (tri_x + ui(14), preview_rect.centery - preview_label.get_height() // 2))
            self.ui_rects['door_sound_preview_btn'] = preview_rect

            y_pos += ui(30)

        if self.selected_object and isinstance(self.selected_object, dict) and self.selected_object.get(
                'is_trigger_box', False):
            self._settings_section_label(screen, "Box ID:", y_pos)

            btn_x = self.palette_x + self.palette_padding + ui(120)
            id_rect = pygame.Rect(btn_x, y_pos - ui(3), ui(200), ui(25))
            self._draw_field_box(screen, id_rect, active=self.trigger_box_id_input_active)
            self.text_field_rects.append(id_rect)

            display_label = self.trigger_box_id_text if self.trigger_box_id_text else '<box id>'
            id_text_surf = self.font_small.render(display_label, True, uk.Theme.TEXT_PRIMARY)
            id_clip = pygame.Rect(id_rect.x + ui(4), id_rect.y, id_rect.w - ui(8), id_rect.h)
            screen.set_clip(id_clip)
            screen.blit(id_text_surf, (id_rect.x + ui(4), id_rect.y + ui(6)))
            screen.set_clip(None)

            self.ui_rects['trigger_box_id_rect'] = id_rect

            y_pos += ui(30)

            self._settings_section_label(screen, "Once:", y_pos)
            once_rect = pygame.Rect(btn_x, y_pos - ui(3), ui(60), ui(22))
            self._draw_onoff_pill(screen, once_rect, self.trigger_box_once)
            self.ui_rects['trigger_box_once_rect'] = once_rect

            y_pos += ui(30)

            self._settings_section_label(screen, "Requires Key:", y_pos)
            key_rect = pygame.Rect(btn_x, y_pos - ui(3), ui(60), ui(22))
            self._draw_onoff_pill(screen, key_rect, self.trigger_box_requires_key)
            self.ui_rects['trigger_box_requires_key_rect'] = key_rect

            y_pos += ui(30)

            self._settings_section_label(screen, "Always Run:", y_pos)
            always_run_rect = pygame.Rect(btn_x, y_pos - ui(3), ui(60), ui(22))
            self._draw_onoff_pill(screen, always_run_rect, self.trigger_box_always_run)
            self.ui_rects['trigger_box_always_run_rect'] = always_run_rect

            if self.trigger_box_always_run:
                hint_surf = self.font_small.render(
                    "Fires passively — position/size ignored, single-click to place",
                    True, uk.Theme.TEXT_DIM)
                screen.blit(hint_surf, (self.palette_x + self.palette_padding, y_pos + ui(24)))
                y_pos += ui(18)

            y_pos += ui(30)

        if (self.selected_object and isinstance(self.selected_object, dict)
                and self.selected_object.get('object_type') == 'world_map_object'):
            current_variant = self._get_current_variant(self.selected_object)
            if current_variant and current_variant.get('type') in ('world_map', 'world_map_sign'):
                self._settings_section_label(screen, "Map:", y_pos)

                # ── Dropdown button ───────────────────────────────────────────
                btn_x = self.palette_x + self.palette_padding + ui(120)
                btn_rect = pygame.Rect(btn_x, y_pos - ui(3), ui(200), ui(25))
                self._draw_field_box(screen, btn_rect, active=self.world_map_dropdown_open)

                display_label = self.world_map_name_text if self.world_map_name_text else '<select map>'
                label_surf = self.font_small.render(display_label, True, uk.Theme.TEXT_PRIMARY)
                label_clip = pygame.Rect(btn_rect.x + ui(4), btn_rect.y, btn_rect.w - ui(20), btn_rect.h)
                screen.set_clip(label_clip)
                screen.blit(label_surf, (btn_rect.x + ui(4), btn_rect.y + ui(6)))
                screen.set_clip(None)

                arrow_x = btn_rect.right - ui(14)
                arrow_y = btn_rect.centery
                arrow_pts = [(arrow_x, arrow_y - ui(4)), (arrow_x + ui(8), arrow_y - ui(4)), (arrow_x + ui(4), arrow_y + ui(4))]
                screen.draw_polygon(uk.Theme.TEXT_MUTED, arrow_pts)

                self.ui_rects['world_map_dropdown_btn'] = btn_rect

                # ── Open dropdown list ────────────────────────────────────────
                if self.world_map_dropdown_open:
                    names = self.world_map_dropdown_names
                    item_h = ui(22)
                    list_h = max(item_h, len(names) * item_h)
                    list_rect = pygame.Rect(btn_rect.x, btn_rect.bottom, btn_rect.w, list_h)

                    uk.draw_panel(screen, list_rect, bg=(*uk.Theme.PANEL_BG[:3], 245),
                                  border=uk.Theme.GOLD, border_width=1, radius=ui(6), shadow=True)

                    self.ui_rects['world_map_dropdown_items'] = []
                    if not names:
                        empty_surf = self.font_small.render('<no maps found>', True, uk.Theme.TEXT_DIM)
                        screen.blit(empty_surf, (list_rect.x + ui(4), list_rect.y + ui(4)))
                    else:
                        for i, name in enumerate(names):
                            item_rect = pygame.Rect(list_rect.x, list_rect.y + i * item_h, list_rect.w, item_h)
                            is_sel = name == self.world_map_name_text
                            if is_sel:
                                uk.draw_rect_on(screen, (*uk.Theme.KI_BLUE, 60), item_rect, 0, 0)
                            item_surf = self.font_small.render(
                                name, True, uk.Theme.TEXT_PRIMARY if is_sel else uk.Theme.TEXT_MUTED)
                            screen.blit(item_surf, (item_rect.x + ui(6), item_rect.y + ui(4)))
                            self.ui_rects['world_map_dropdown_items'].append((item_rect, name))

                y_pos += ui(30)

        if self.selected_object and isinstance(self.selected_object, dict) and self.selected_object.get(
                'is_animated_region', False):
            self._settings_section_label(screen, f"Opacity: {self.region_opacity}%", y_pos)
            y_pos += ui(20)

            track_rect = pygame.Rect(self.palette_x + self.palette_padding, y_pos,
                                      self.palette_width - self.palette_padding * 2, ui(14))
            self._draw_slider_row(screen, track_rect, self.region_opacity / 100, 'region_opacity_slider')

            y_pos += track_rect.h + ui(16)

            current_region_type = self.selected_object.get('region_type')
            if current_region_type in ('water',):
                # Wave/Flicker Amount slider — fraction of 8x8 chunks showing
                # the animated patch vs. the plain (no-line) patch.
                self._settings_section_label(
                    screen, f"Wave Amount: {self.region_wave_amount}%", y_pos)
                y_pos += ui(20)

                wtrack_rect = pygame.Rect(self.palette_x + self.palette_padding, y_pos,
                                           self.palette_width - self.palette_padding * 2, ui(14))
                self._draw_slider_row(screen, wtrack_rect, self.region_wave_amount / 100, 'region_wave_slider')

                y_pos += wtrack_rect.h + ui(16)

                # Seed — determines which chunks lose their waves at a given
                # Wave Amount; reroll to reshuffle the layout.
                self._settings_section_label(screen, "Seed:", y_pos)

                seed_input_x = self.palette_x + self.palette_padding + ui(60)
                seed_input_y = y_pos - ui(3)
                seed_input_rect = pygame.Rect(seed_input_x, seed_input_y, ui(80), ui(25))
                self._draw_field_box(screen, seed_input_rect, active=self.region_seed_input_active)
                self.text_field_rects.append(seed_input_rect)

                seed_display = self.region_seed_text if self.region_seed_input_active else str(self.region_seed)
                seed_text_surf = self.font_medium.render(seed_display, True, uk.Theme.TEXT_PRIMARY)
                seed_text_rect = seed_text_surf.get_rect(center=seed_input_rect.center)
                screen.blit(seed_text_surf, seed_text_rect)

                self.ui_rects['region_seed_input'] = seed_input_rect

                reroll_rect = pygame.Rect(seed_input_rect.right + ui(8), seed_input_y, ui(60), ui(25))
                self._draw_mini_button(screen, reroll_rect, radius=ui(6))
                reroll_text = self.font_small.render("Reroll", True, uk.Theme.TEXT_PRIMARY)
                screen.blit(reroll_text, reroll_text.get_rect(center=reroll_rect.center))
                self.ui_rects['region_seed_reroll'] = reroll_rect

                y_pos += ui(30)

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
                self._settings_section_label(screen, "Variant:", y_pos)
                y_pos += ui(20)

                variant_sprites = self._load_region_variant_sprites(current_region_type)
                num_variants = num_tile_variants
                btn_gap = ui(6)
                # Cap button size so a sheet with few variants (e.g. 2)
                # doesn't stretch each button to fill the whole row width —
                # buttons only shrink below this cap when there are enough
                # variants that the full-width division would exceed it.
                max_btn_size = ui(56)
                fit_w = (self.palette_width - self.palette_padding * 2 - btn_gap * (num_variants - 1)) // num_variants
                btn_w = min(max_btn_size, fit_w)
                btn_h = btn_w
                variant_rects = []
                for i in range(num_variants):
                    btn_x = self.palette_x + self.palette_padding + i * (btn_w + btn_gap)
                    btn_rect = pygame.Rect(btn_x, y_pos, btn_w, btn_h)
                    selected = (self.region_variant == i)
                    self._draw_mini_button(screen, btn_rect, selected=selected, radius=ui(6))

                    sprite = variant_sprites[i] if i < len(variant_sprites) else None
                    if sprite:
                        sw, sh = sprite.get_size()
                        max_dim = btn_w - ui(8)
                        scale = min(max_dim / sw, max_dim / sh)
                        if scale != 1:
                            sprite = pygame.transform.scale(
                                sprite, (max(1, int(sw * scale)), max(1, int(sh * scale)))
                            )
                        screen.blit(sprite, sprite.get_rect(center=btn_rect.center))
                    else:
                        # Asset not loaded yet — fall back to a number so the
                        # slot is still identifiable and clickable.
                        num_text = self.font_small.render(str(i + 1), True, uk.Theme.TEXT_PRIMARY)
                        screen.blit(num_text, num_text.get_rect(center=btn_rect.center))

                    variant_rects.append(btn_rect)

                self.ui_rects['region_variant_rects'] = variant_rects
                y_pos += btn_h + ui(16)

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
                self._settings_section_label(screen, color_label_text, y_pos)

                swatch_rect = pygame.Rect(self.palette_x + self.palette_padding + ui(120), y_pos - 2, ui(30), ui(18))
                uk.draw_rect_on(screen, self.region_color, swatch_rect, 0, ui(4))
                uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, swatch_rect, 1, ui(4))

                reset_rect = pygame.Rect(swatch_rect.right + ui(8), y_pos - 2, ui(60), ui(18))
                self._draw_mini_button(screen, reset_rect, radius=ui(6))
                reset_text = self.font_small.render("Reset", True, uk.Theme.TEXT_PRIMARY)
                screen.blit(reset_text, reset_text.get_rect(center=reset_rect.center))
                self.ui_rects['region_color_reset'] = reset_rect

                y_pos += ui(26)

                # RGB sliders + spin boxes + a hex field — one gradient bar
                # per channel (black -> full channel color) with a draggable
                # marker, a numeric read-out with tiny +/- spin arrows next
                # to it, and a hex box that free-types a color directly.
                row_h = ui(18)
                row_gap = ui(6)
                label_w = ui(16)
                spin_text_w = ui(32)
                spin_arrow_w = ui(12)
                spin_w = spin_text_w + spin_arrow_w
                bar_gap = ui(6)
                bar_x = self.palette_x + self.palette_padding + label_w
                bar_w = self.palette_width - self.palette_padding * 2 - label_w - bar_gap - spin_w

                for key, idx, label_text in (('r', 0, 'R'), ('g', 1, 'G'), ('b', 2, 'B')):
                    bar_rect = pygame.Rect(bar_x, y_pos, bar_w, row_h)
                    grad_surf = self._get_channel_gradient_surface(idx, bar_w, row_h)
                    screen.blit(grad_surf, bar_rect.topleft)
                    uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, bar_rect, 1, ui(3))

                    label_surf = self.font_small.render(label_text + ":", True, uk.Theme.TEXT_PRIMARY)
                    screen.blit(label_surf, (bar_rect.left - label_w,
                                              bar_rect.centery - label_surf.get_height() // 2))

                    val = self.region_color[idx]
                    marker_x = bar_rect.left + int((val / 255) * bar_rect.width)
                    marker_rect = pygame.Rect(marker_x - 2, bar_rect.top - 2, ui(4), bar_rect.height + ui(4))
                    uk.draw_rect_on(screen, (255, 255, 255), marker_rect, 1)
                    uk.draw_rect_on(screen, (0, 0, 0), marker_rect, 1)
                    self.ui_rects[f'region_{key}_bar'] = bar_rect

                    spin_rect = pygame.Rect(bar_rect.right + bar_gap, y_pos, spin_text_w, row_h)
                    self._draw_field_box(screen, spin_rect,
                                         active=(self.region_channel_input_active == key), radius=ui(4))
                    self.text_field_rects.append(spin_rect)
                    val_text = self.region_channel_text if self.region_channel_input_active == key else str(val)
                    val_surf = self.font_small.render(val_text, True, uk.Theme.TEXT_PRIMARY)
                    screen.blit(val_surf, val_surf.get_rect(center=spin_rect.center))
                    self.ui_rects[f'region_{key}_spin_text'] = spin_rect

                    arrow_up_rect = pygame.Rect(spin_rect.right, y_pos, spin_arrow_w, row_h // 2)
                    arrow_down_rect = pygame.Rect(spin_rect.right, y_pos + row_h // 2,
                                                   spin_arrow_w, row_h - row_h // 2)
                    uk.draw_rect_on(screen, uk.Theme.CARD_BG, arrow_up_rect, 0, 0)
                    uk.draw_rect_on(screen, uk.Theme.CARD_BG, arrow_down_rect, 0, 0)
                    uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, arrow_up_rect, 1, 0)
                    uk.draw_rect_on(screen, uk.Theme.CARD_BORDER, arrow_down_rect, 1, 0)
                    up_pts = [(arrow_up_rect.centerx, arrow_up_rect.top + ui(3)),
                              (arrow_up_rect.left + ui(3), arrow_up_rect.bottom - 2),
                              (arrow_up_rect.right - ui(3), arrow_up_rect.bottom - 2)]
                    down_pts = [(arrow_down_rect.centerx, arrow_down_rect.bottom - ui(3)),
                                (arrow_down_rect.left + ui(3), arrow_down_rect.top + 2),
                                (arrow_down_rect.right - ui(3), arrow_down_rect.top + 2)]
                    screen.draw_polygon(uk.Theme.TEXT_MUTED, up_pts)
                    screen.draw_polygon(uk.Theme.TEXT_MUTED, down_pts)
                    self.ui_rects[f'region_{key}_spin_up'] = arrow_up_rect
                    self.ui_rects[f'region_{key}_spin_down'] = arrow_down_rect

                    y_pos += row_h + row_gap

                hex_label_surf = self.font_small.render("Hex:", True, uk.Theme.TEXT_PRIMARY)
                hex_label_x = self.palette_x + self.palette_padding
                screen.blit(hex_label_surf, (hex_label_x, y_pos + row_h // 2 - hex_label_surf.get_height() // 2))

                hex_field_x = hex_label_x + label_w + ui(24)
                hex_rect = pygame.Rect(hex_field_x, y_pos,
                                        self.palette_x + self.palette_width - self.palette_padding - hex_field_x,
                                        row_h)
                self._draw_field_box(screen, hex_rect, active=self.region_hex_input_active, radius=ui(4))
                self.text_field_rects.append(hex_rect)
                hex_surf = self.font_small.render("#" + self.region_hex_text, True, uk.Theme.TEXT_PRIMARY)
                screen.blit(hex_surf, (hex_rect.left + ui(6), hex_rect.centery - hex_surf.get_height() // 2))
                self.ui_rects['region_hex_input'] = hex_rect

                y_pos += row_h + ui(10)

        # Keybinds popup — replaces the old always-on instructions footer
        # that used to sit here. Drawn last so it sits on top of everything
        # else the panel just painted.
        self._draw_keybinds_popup(screen)

    def _draw_info_button(self, screen: pygame.Surface, rect: pygame.Rect):
        """Small circular badge that toggles the keybinds popup. Uses a
        custom icon from assets/ui/toolbar/info.png when present, falling
        back to the procedural '?' mark otherwise."""
        mouse_pos = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        hovered = rect.collidepoint(mouse_pos)
        center = rect.center
        radius = rect.width // 2

        if self.show_keybinds_popup:
            fill = uk.Theme.GOLD
            mark_color = (14, 17, 25)
        else:
            fill = (40, 44, 58) if hovered else (28, 31, 42)
            mark_color = uk.Theme.GOLD_BRIGHT if hovered else uk.Theme.TEXT_MUTED

        uk.draw_circle_on(screen, fill, center, radius)
        uk.draw_circle_on(screen, uk.Theme.GOLD, center, radius, 1)

        if self._info_icon:
            uk.blit_surface(screen, self._info_icon,
                            self._info_icon.get_rect(center=center), transient=True)
        else:
            mark_s = self.font.render("?", color=mark_color, height=ui_text(11))
            uk.blit_surface(screen, mark_s,
                            (center[0] - mark_s.get_width() // 2, center[1] - mark_s.get_height() // 2),
                            transient=True)

    def _draw_keybinds_popup(self, screen: pygame.Surface):
        """Full keybind reference, opened from the '?' info button. Drawn last,
        over a dimmed backdrop, so it reads as a modal overlay above the whole
        panel (and the world beneath it). Any click while it's open closes it
        (handled in the event loop) — this method only ever draws."""
        if not self.show_keybinds_popup:
            return

        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((8, 9, 13, 170))
        screen.blit(overlay, (0, 0))

        sections = [
            ("Object editor", [
                ("Click Palette", "Select object"),
                ("Click World", "Place object"),
                ("Right Click", "Delete object"),
                ("Item armed + Click Chest", "Add loot"),
            ]),
            ("Other", [
                ("G", "Toggle grid snap"),
                ("H", "Toggle grid"),
                ("R (on fishing area)", "Cycle jump direction"),
                ("Shift + R", "Cycle direction (reverse)"),
                ("ESC / F3", "Close editor"),
            ]),
        ]

        row_h = ui(20)
        section_gap = ui(14)
        header_h = ui(50)
        margin_x = ui(20)
        key_indent = ui(10)       # key label offset from the left margin
        col_gap = ui(18)          # gap between the key column and the desc column
        right_pad = ui(20)

        # Cache rendered surfaces so widths are measured once and reused for
        # both sizing the panel and drawing it (avoids re-deriving layout
        # constants from guessed pixel widths, which caused overflow/overlap
        # in the old fixed-width layout).
        key_surfaces = []
        desc_surfaces = []
        for _, rows in sections:
            for key_label, desc in rows:
                key_surfaces.append(self.font.render(key_label, color=uk.Theme.GOLD, height=ui_text(11)))
                desc_surfaces.append(self.font.render(desc, color=uk.Theme.TEXT_PRIMARY, height=ui_text(11)))

        key_col_w = max(s.get_width() for s in key_surfaces) + col_gap
        max_desc_w = max(s.get_width() for s in desc_surfaces)

        title_s = self.font.render("Keybinds", color=uk.Theme.GOLD, height=ui_text(16))
        close_s = self.font.render("Click anywhere to close", color=uk.Theme.TEXT_DIM, height=ui_text(10))

        content_w = key_indent + key_col_w + max_desc_w + right_pad
        header_w = title_s.get_width() + ui(24) + close_s.get_width()
        panel_w = max(360, margin_x * 2 + max(content_w, header_w))

        # Every section header line also consumes a row, so count one extra
        # row per section on top of its keybind rows.
        content_rows = sum(1 + len(rows) for _, rows in sections)
        panel_h = header_h + content_rows * row_h + len(sections) * section_gap + ui(16)

        panel_x = (self.screen_width - panel_w) // 2
        panel_y = max(ui(30), (self.screen_height - panel_h) // 2)
        panel_rect = pygame.Rect(panel_x, panel_y, panel_w, panel_h)

        uk.draw_panel(screen, panel_rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=2, radius=uk.Theme.RADIUS_PANEL, shadow=True)

        uk.blit_surface(screen, title_s, (panel_x + margin_x, panel_y + ui(14)), transient=True)
        uk.blit_surface(screen, close_s,
                        (panel_x + panel_w - close_s.get_width() - margin_x, panel_y + ui(20)), transient=True)

        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER,
                        (panel_x + ui(16), panel_y + header_h - ui(10), panel_w - ui(32), 1), 0, 0)

        y = panel_y + header_h
        i = 0
        for section_name, rows in sections:
            section_s = self.font.render(section_name, color=uk.Theme.TEXT_MUTED, height=ui_text(11))
            uk.blit_surface(screen, section_s, (panel_x + margin_x, y), transient=True)
            y += row_h
            for _ in rows:
                key_s = key_surfaces[i]
                desc_s = desc_surfaces[i]
                i += 1
                uk.blit_surface(screen, key_s, (panel_x + margin_x + key_indent, y), transient=True)
                uk.blit_surface(screen, desc_s,
                                (panel_x + margin_x + key_indent + key_col_w, y), transient=True)
                y += row_h
            y += section_gap