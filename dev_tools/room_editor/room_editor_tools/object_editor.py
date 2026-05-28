import os

import pygame
import pygame.gfxdraw

from config.settings import RENDER_SCALE, TILE_SIZE, WORLD_WIDTH, WORLD_HEIGHT
from objects.spawn_object import SpawnObject, SpawnObjectManager
from objects.collision_object import CollisionObject, CollisionObjectManager, draw_collision_object
from objects.level_gate import LevelGate, LevelGateManager
from objects.room_transition import RoomTransition, RoomTransitionManager, TransitionConfigDialog
from objects.flying_pad import FlyingPad, FlyingPadManager
from objects.save_point import SavePoint, SavePointManager
from objects.world_map import WorldMapObject, WorldMapObjectManager
from objects.cutscene_trigger import CutsceneTrigger, CutsceneTriggerManager, draw_cutscene_trigger
from dev_tools.room_editor.room_editor_tools.flying_pad_path_editor import FlyingPadPathEditor


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
        self.items_per_row = 3
        self.scroll_offset = 0
        self.max_scroll = 0

        # ── Object managers ───────────────────────────────────────────────────
        self.spawn_manager = SpawnObjectManager()
        self.collision_manager = CollisionObjectManager()
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

        # ── Flying pad ────────────────────────────────────────────────────────
        self.flying_pad_manager = FlyingPadManager()
        self.flying_pad_path_editor = FlyingPadPathEditor(screen_width, screen_height)
        self.placing_flying_pad = False
        self.pending_flying_pad = None
        self.on_flying_pad_placed = None
        self.on_flying_pad_deleted = None

        # ── Save points ───────────────────────────────────────────────────────
        self.save_point_manager = SavePointManager()
        self.on_save_point_placed = None
        self.on_save_point_deleted = None
        self.world_map_manager = WorldMapObjectManager()
        self.on_world_map_placed = None
        self.on_world_map_deleted = None

        # ── Cutscene triggers ─────────────────────────────────────────────────
        self.cutscene_trigger_manager = CutsceneTriggerManager()
        self.on_cutscene_trigger_placed = None
        self.on_cutscene_trigger_deleted = None
        self.placing_cutscene_trigger = False
        self.cutscene_trigger_start_x = 0
        self.cutscene_trigger_start_y = 0
        self.preview_cutscene_trigger = None
        self.cutscene_id_text = ""
        self.cutscene_id_input_active = False   # kept for compat; not used by dropdown
        self.cutscene_one_shot = True
        self.cutscene_dropdown_open = False     # whether the dropdown list is visible
        self.cutscene_dropdown_names = []       # cached list of cutscene file names

        # ── World map selection ───────────────────────────────────────────────
        self.world_map_name_text = ""           # stem of the selected world map JSON
        self.world_map_dropdown_open = False    # whether the map-name dropdown is open
        self.world_map_dropdown_names = []      # cached list of world map stems

        self.hovered_object = None        # object under the cursor (for deletion highlight)
        self.hovered_object_type = None

        # ── Gate level input ──────────────────────────────────────────────────
        self.gate_required_level = 1
        self.gate_level_input_active = False
        self.gate_level_text = "1"

        # ── Variant selection ─────────────────────────────────────────────────
        self.selected_variant = None
        self.hover_variant_index = -1
        self.showing_variants_for = None

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

        self.categories = {
            'System': [],
            'Decorations': [
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
                        {'type': 'world_map',      'name': 'World Map',      'width': 32, 'height': 37, 'sprite': None},
                        {'type': 'world_map_sign', 'name': 'World Map Sign', 'width': 29, 'height': 32, 'sprite': None},
                    ],
                    'default_variant': 'world_map'
                }
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

        # Add cutscene trigger to System category
        cutscene_sprite = pygame.Surface((16, 16), pygame.SRCALPHA)
        cutscene_sprite.fill((180, 0, 255, 100))
        pygame.draw.rect(cutscene_sprite, (200, 0, 255), (0, 0, 16, 16), 2)
        for i in range(0, 96, 8):
            pygame.draw.line(cutscene_sprite, (160, 0, 200, 120), (i, 0), (i - 16, 16), 1)

        self.categories['System'].append({
            'id': 'cutscene_trigger',
            'name': 'Cutscene Trigger',
            'sprite': cutscene_sprite,
            'width': 16,
            'height': 16,
            'is_cutscene_trigger': True
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

    def set_toolbar(self, toolbar):
        """Set the toolbar reference and pass it to sub-editors that need to hide it"""
        self.toolbar = toolbar
        # Pass toolbar to flying pad path editor so it can hide it during editing
        self.flying_pad_path_editor.set_toolbar(toolbar)

    # -------------------------------------------------------------------------
    # Panel show/hide tab
    # -------------------------------------------------------------------------

    def _panel_toggle_rect(self):
        """Return the rect for the ◀/▶ tab that straddles the panel's left edge."""
        gap = 6
        tx = (self.palette_x - self._panel_tab_w - gap) if self.palette_visible else (self.screen_width - self._panel_tab_w)
        ty = self.palette_y + (self.palette_height - self._panel_tab_h) // 2
        return pygame.Rect(tx, ty, self._panel_tab_w, self._panel_tab_h)

    def _draw_panel_toggle_tab(self, screen):
        """Render the small ◀/▶ tab — always visible so the panel can be recalled."""
        rect   = self._panel_toggle_rect()
        bg     = self.colors['panel_light'] if self._hover_panel_toggle else self.colors['panel']
        border = self.colors['accent']      if self._hover_panel_toggle else self.colors['grid']
        pygame.draw.rect(screen, bg,     rect, border_radius=6)
        pygame.draw.rect(screen, border, rect, 1, border_radius=6)
        arrow = '◀' if self.palette_visible else '▶'
        font  = self.font_small
        label = font.render(
            arrow, True,
            self.colors['accent'] if self._hover_panel_toggle else self.colors['text_dim']
        )
        screen.blit(label, label.get_rect(center=rect.center))

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

                    elif obj['object_type'] == 'level_gate':
                        gate = LevelGate(0, 0, variant['type'], 1)
                        # Store per-variant dimensions so the preview scales correctly
                        # (stone formation is 71×68, all others are 32×32)
                        variant['width']  = gate.width
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
                                    (center_x, 2),          # Top
                                    (width - 2, center_y),  # Right
                                    (center_x, height - 2), # Bottom
                                    (2, center_y)           # Left
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
                            variant['width']  = sprite.get_width()
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
                system_flags = ('is_spawn', 'is_collision', 'is_transition', 'is_cutscene_trigger')
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
            self.placing_cutscene_trigger = False
            self.preview_cutscene_trigger = None
            self.cutscene_id_input_active = False
            self.cutscene_dropdown_open = False
            self.world_map_dropdown_open = False

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

        # Check collision walls
        collision_objs = self.collision_manager.get_collision_objects(self.current_room_name)
        for collision_obj in collision_objs:
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

        # Check flying pads
        pads = self.flying_pad_manager.get_pads(self.current_room_name)
        for pad in pads:
            distance = ((pad.x - world_x) ** 2 + (pad.y - world_y) ** 2) ** 0.5
            if distance < max(pad.width, pad.height) / 2:
                return pad, 'flying_pad'

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

        # Check cutscene triggers
        triggers = self.cutscene_trigger_manager.get_triggers(self.current_room_name)
        for trigger in triggers:
            if (trigger.x <= world_x <= trigger.x + trigger.width and
                    trigger.y <= world_y <= trigger.y + trigger.height):
                return trigger, 'cutscene_trigger'

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

        elif obj_type == 'flying_pad':
            self.flying_pad_manager.remove_pad(self.current_room_name, obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'flying_pads'):
                    if obj in room.flying_pads:
                        room.flying_pads.remove(obj)

            if hasattr(self, 'on_flying_pad_deleted') and self.on_flying_pad_deleted:
                self.on_flying_pad_deleted(obj, self.current_room_name)

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

        elif obj_type == 'cutscene_trigger':
            self.cutscene_trigger_manager.remove_trigger(obj)
            if self.room_manager:
                room = self.room_manager.get_room_by_name(self.current_room_name)
                if room and hasattr(room, 'cutscene_triggers'):
                    if obj in room.cutscene_triggers:
                        room.cutscene_triggers.remove(obj)

            if hasattr(self, 'on_cutscene_trigger_deleted') and self.on_cutscene_trigger_deleted:
                self.on_cutscene_trigger_deleted(obj, self.current_room_name)

    def _is_object_disabled(self, obj) -> bool:
        """Check if we can't place this object (e.g. spawn already exists)"""
        if not obj or not isinstance(obj, dict):
            return True

        if obj.get('is_spawn', False):
            return self.spawn_manager.has_spawn_point(self.current_room_name)
        return False

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
        return (mouse_x >= self.palette_x and
                mouse_y >= self.palette_y and
                mouse_y <= self.palette_y + self.palette_height)

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
                    # Reset variant selection when selecting new object
                    if obj.get('has_variants', False):
                        self.showing_variants_for = obj
                        self.selected_variant = self._get_current_variant(obj)
                    else:
                        self.showing_variants_for = None
                        self.selected_variant = None
                return

    def _place_object(self, camera_x, camera_y, room_name):
        """Actually place the selected object in the world"""
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


        elif self.selected_object.get('object_type') == 'flying_pad':

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

        elif self.selected_object.get('object_type') == 'save_point':
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
                    room.save_points.append(save_point)

            # Notify game
            if self.on_save_point_placed:
                self.on_save_point_placed(save_point)

        elif self.selected_object.get('object_type') == 'world_map_object':
            variant = self.selected_variant or self._get_current_variant(self.selected_object)
            variant_type = variant['type'] if variant and 'type' in variant else 'world_map'
            map_name = self.world_map_name_text if variant_type == 'world_map' else ''

            obj = WorldMapObject(int(self.preview_x), int(self.preview_y), variant_type, map_name)
            self.world_map_manager.add_object(room_name, obj)

            if self.room_manager:
                room = self.room_manager.get_room_by_name(room_name)
                if room:
                    if not hasattr(room, 'world_map_objects'):
                        room.world_map_objects = []
                    room.world_map_objects.append(obj)

            if self.on_world_map_placed:
                self.on_world_map_placed(obj, room_name)

        elif self.selected_object.get('object_type') == 'level_gate':
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
                    room.level_gates.append(gate)

            if hasattr(self, 'on_gate_placed') and self.on_gate_placed:
                self.on_gate_placed(gate, room_name)

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

        elif obj_type in ['stone', 'transition', 'cutscene_trigger']:
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

        collision_obj = CollisionObject(
            int(self.preview_collision.x),
            int(self.preview_collision.y),
            int(self.preview_collision.width),
            int(self.preview_collision.height),
            room_name
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

    def _finalize_cutscene_trigger_placement(self, room_name):
        """Finish placing a cutscene trigger zone after dragging."""
        if not self.preview_cutscene_trigger:
            return

        trigger = CutsceneTrigger(
            int(self.preview_cutscene_trigger.x),
            int(self.preview_cutscene_trigger.y),
            int(self.preview_cutscene_trigger.width),
            int(self.preview_cutscene_trigger.height),
            cutscene_id=self.cutscene_id_text,
            one_shot=self.cutscene_one_shot,
            room_name=room_name,
        )

        self.cutscene_trigger_manager.add_trigger(trigger)

        if self.room_manager:
            room = self.room_manager.get_room_by_name(room_name)
            if room:
                if not hasattr(room, 'cutscene_triggers'):
                    room.cutscene_triggers = []
                room.cutscene_triggers.append(trigger)

        self.preview_cutscene_trigger = None

        if self.on_cutscene_trigger_placed:
            self.on_cutscene_trigger_placed(trigger, room_name)

    def _get_cutscene_names(self):
        """Return a sorted list of cutscene IDs from data/cutscenes/*.json."""
        cutscene_dir = os.path.join('data', 'cutscenes')
        try:
            return sorted(
                f[:-5] for f in os.listdir(cutscene_dir) if f.endswith('.json')
            )
        except FileNotFoundError:
            return []

    def _get_world_map_names(self):
        """Return a sorted list of world map stems from assets/world_maps/*.json."""
        save_dir = os.path.join('assets', 'world_maps')
        try:
            return sorted(f[:-5] for f in os.listdir(save_dir) if f.endswith('.json'))
        except FileNotFoundError:
            return []

    def _is_cutscene_id_input_clicked(self, mouse_pos):
        """Check if the cutscene ID text box was clicked."""
        if not self.selected_object or not isinstance(self.selected_object, dict):
            return False
        if not self.selected_object.get('is_cutscene_trigger', False):
            return False
        input_x = self.palette_x + self.palette_padding + 120
        input_y = self.palette_y + self.palette_height - 105
        input_rect = pygame.Rect(input_x, input_y, 150, 25)
        return input_rect.collidepoint(mouse_pos)

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

        # Scroll through palette
        if event.type == pygame.MOUSEWHEEL and self._is_in_palette(mouse_pos[0], mouse_pos[1]):
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
                # Handle transition spawn placement mode
                if self.placing_transition_spawn:
                    self._finalize_transition_spawn_placement()
                    return

                # Check if clicking on level input box
                if self._is_level_input_clicked(mouse_pos):
                    self.gate_level_input_active = True
                    return

                # Check if clicking on cutscene ID input box
                if self._is_cutscene_id_input_clicked(mouse_pos):
                    self.cutscene_id_input_active = True
                    return

                # Handle cutscene dropdown clicks
                if (self.selected_object and isinstance(self.selected_object, dict)
                        and self.selected_object.get('is_cutscene_trigger', False)):

                    # Dropdown button — toggle open/closed
                    dd_btn = self.ui_rects.get('cutscene_dropdown_btn')
                    if dd_btn and dd_btn.collidepoint(mouse_pos):
                        self.cutscene_dropdown_open = not self.cutscene_dropdown_open
                        if self.cutscene_dropdown_open:
                            self.cutscene_dropdown_names = self._get_cutscene_names()
                        return

                    # Item inside open dropdown list
                    if self.cutscene_dropdown_open:
                        for item_rect, name in self.ui_rects.get('cutscene_dropdown_items', []):
                            if item_rect.collidepoint(mouse_pos):
                                self.cutscene_id_text = name
                                self.cutscene_dropdown_open = False
                                return
                        # Click outside list — close without selecting
                        self.cutscene_dropdown_open = False
                        return

                    # One-shot toggle
                    oneshot_rect = self.ui_rects.get('cutscene_oneshot_rect')
                    if oneshot_rect and oneshot_rect.collidepoint(mouse_pos):
                        self.cutscene_one_shot = not self.cutscene_one_shot
                        return

                # Handle world map dropdown clicks
                if (self.selected_object and isinstance(self.selected_object, dict)
                        and self.selected_object.get('object_type') == 'world_map_object'):
                    current_variant = self._get_current_variant(self.selected_object)
                    if current_variant and current_variant.get('type') == 'world_map':
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

                # Check if clicking on variant selector
                if self._is_variant_selector_clicked(mouse_pos):
                    return

                # Deactivate input if clicking elsewhere
                if self.gate_level_input_active:
                    self.gate_level_input_active = False
                if self.cutscene_id_input_active:
                    self.cutscene_id_input_active = False

                # Finish placing collision wall if we're in the middle of it
                if self.placing_collision:
                    self._finalize_collision_placement(room_name)
                    self.placing_collision = False
                    return

                # Finish placing cutscene trigger if we're in the middle of it
                if self.placing_cutscene_trigger:
                    self._finalize_cutscene_trigger_placement(room_name)
                    self.placing_cutscene_trigger = False
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
                    # Click in world to place object
                    if self.selected_object and not self._is_object_disabled(self.selected_object):
                        if self.selected_object.get('is_collision', False):
                            self.placing_collision = True
                            self.collision_start_x = self.preview_x
                            self.collision_start_y = self.preview_y
                        elif self.selected_object.get('is_cutscene_trigger', False):
                            self.placing_cutscene_trigger = True
                            self.cutscene_trigger_start_x = self.preview_x
                            self.cutscene_trigger_start_y = self.preview_y
                        elif self.selected_object.get('is_transition', False):
                            self.placing_transition = True
                            self.transition_start_x = self.preview_x
                            self.transition_start_y = self.preview_y
                        else:
                            self._place_object(camera_x, camera_y, room_name)

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

            if event.key == pygame.K_g:
                self.grid_snap = not self.grid_snap
            elif event.key == pygame.K_h:
                self.show_grid = not self.show_grid
            elif event.key == pygame.K_ESCAPE or event.key == pygame.K_F3:
                if self.placing_collision:
                    self.placing_collision = False
                    self.preview_collision = None
                elif self.placing_cutscene_trigger:
                    self.placing_cutscene_trigger = False
                    self.preview_cutscene_trigger = None
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
                grid_x = int(self.mouse_world_x / TILE_SIZE) * TILE_SIZE
                grid_y = int(self.mouse_world_y / TILE_SIZE) * TILE_SIZE
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
                grid_x = int(self.mouse_world_x / TILE_SIZE) * TILE_SIZE + TILE_SIZE // 2
                grid_y = int(self.mouse_world_y / TILE_SIZE) * TILE_SIZE + TILE_SIZE // 2
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
                snap_start_x = int(self.collision_start_x / TILE_SIZE) * TILE_SIZE
                snap_start_y = int(self.collision_start_y / TILE_SIZE) * TILE_SIZE
                snap_end_x = int(self.mouse_world_x / TILE_SIZE) * TILE_SIZE
                snap_end_y = int(self.mouse_world_y / TILE_SIZE) * TILE_SIZE

                min_x = min(snap_start_x, snap_end_x)
                min_y = min(snap_start_y, snap_end_y)
                max_x = max(snap_start_x, snap_end_x)
                max_y = max(snap_start_y, snap_end_y)

                width = max(TILE_SIZE, max_x - min_x + TILE_SIZE)
                height = max(TILE_SIZE, max_y - min_y + TILE_SIZE)
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

        if self.placing_cutscene_trigger:
            if self.grid_snap:
                snap_start_x = int(self.cutscene_trigger_start_x / TILE_SIZE) * TILE_SIZE
                snap_start_y = int(self.cutscene_trigger_start_y / TILE_SIZE) * TILE_SIZE
                snap_end_x = int(self.mouse_world_x / TILE_SIZE) * TILE_SIZE
                snap_end_y = int(self.mouse_world_y / TILE_SIZE) * TILE_SIZE

                min_x = min(snap_start_x, snap_end_x)
                min_y = min(snap_start_y, snap_end_y)
                max_x = max(snap_start_x, snap_end_x)
                max_y = max(snap_start_y, snap_end_y)

                width = max(TILE_SIZE, max_x - min_x + TILE_SIZE)
                height = max(TILE_SIZE, max_y - min_y + TILE_SIZE)
            else:
                end_x = self.mouse_world_x
                end_y = self.mouse_world_y

                min_x = min(self.cutscene_trigger_start_x, end_x)
                min_y = min(self.cutscene_trigger_start_y, end_y)
                max_x = max(self.cutscene_trigger_start_x, end_x)
                max_y = max(self.cutscene_trigger_start_y, end_y)

                width = max(16, max_x - min_x)
                height = max(16, max_y - min_y)

            self.preview_cutscene_trigger = CutsceneTrigger(
                int(min_x), int(min_y), int(width), int(height),
                cutscene_id=self.cutscene_id_text,
                one_shot=self.cutscene_one_shot,
                room_name=self.current_room_name,
            )

        if self.placing_transition:
            if self.grid_snap:
                snap_start_x = int(self.transition_start_x / TILE_SIZE) * TILE_SIZE
                snap_start_y = int(self.transition_start_y / TILE_SIZE) * TILE_SIZE
                snap_end_x = int(self.mouse_world_x / TILE_SIZE) * TILE_SIZE
                snap_end_y = int(self.mouse_world_y / TILE_SIZE) * TILE_SIZE

                min_x = min(snap_start_x, snap_end_x)
                min_y = min(snap_start_y, snap_end_y)
                max_x = max(snap_start_x, snap_end_x)
                max_y = max(snap_start_y, snap_end_y)

                width = max(TILE_SIZE, max_x - min_x + TILE_SIZE)
                height = max(TILE_SIZE, max_y - min_y + TILE_SIZE)
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
          - Cutscene-trigger drag preview
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

        if self.placing_transition and self.preview_transition:
            self.preview_transition.draw(screen,
                                         self._make_camera(camera_x, camera_y),
                                         RENDER_SCALE, dev_mode=True, selected=True)
            return

        if self.placing_cutscene_trigger and self.preview_cutscene_trigger:
            draw_cutscene_trigger(screen, self.preview_cutscene_trigger,
                                  camera_x, camera_y, RENDER_SCALE,
                                  dev_mode=True, selected=True)
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
            grid_screen_x = int(self.mouse_world_x / TILE_SIZE) * TILE_SIZE * RENDER_SCALE - camera_x
            grid_screen_y = int(self.mouse_world_y / TILE_SIZE) * TILE_SIZE * RENDER_SCALE - camera_y

            guide_surf = pygame.Surface((TILE_SIZE * RENDER_SCALE, TILE_SIZE * RENDER_SCALE), pygame.SRCALPHA)
            pygame.draw.rect(guide_surf, self.colors['snap_guide'],
                             (0, 0, TILE_SIZE * RENDER_SCALE, TILE_SIZE * RENDER_SCALE), 2)

            center_x = TILE_SIZE * RENDER_SCALE // 2
            center_y = TILE_SIZE * RENDER_SCALE // 2
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

            preview_x = int(screen_x - scaled_width // 2)
            preview_y = int(screen_y - scaled_height // 2)

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

        # Update hover state and always draw the toggle tab
        mx, my = pygame.mouse.get_pos()
        self._hover_panel_toggle = self._panel_toggle_rect().collidepoint(mx, my)
        self._draw_panel_toggle_tab(screen)

        # Always draw the transition config dialog — it must be visible even
        # when the palette panel is hidden (user closed panel to place freely).
        self.transition_config.draw(screen)

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
            return

        if not self.selected_object.get('has_variants', False):
            return

        variants = self.selected_object.get('variants', [])
        if not variants:
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

        # Title
        title_text = self.font_small.render("Select Variant:", True, self.colors['text_dim'])
        screen.blit(title_text, (selector_x + self.palette_padding, selector_y + 5))

        # Draw variant options
        variant_size = 50
        variant_spacing = 10
        start_x = selector_x + self.palette_padding
        start_y = selector_y + 25

        current_variant = self.selected_variant or self._get_current_variant(self.selected_object)

        self.ui_rects['variant_rects'] = []

        for i, variant in enumerate(variants):
            variant_x = start_x + i * (variant_size + variant_spacing)
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

    def draw_cutscene_triggers(self, screen, camera_x, camera_y):
        """Draw all cutscene trigger zones in the current room (dev mode only)."""
        if not self.current_room_name:
            return

        triggers = self.cutscene_trigger_manager.get_triggers(self.current_room_name)
        for trigger in triggers:
            draw_cutscene_trigger(
                screen, trigger, camera_x, camera_y, RENDER_SCALE,
                dev_mode=True, selected=False
            )

    def _draw_settings_panel(self, screen):
        """Draw controls and settings at the bottom of the palette"""
        panel_height = 160
        panel_y = self.palette_y + self.palette_height - panel_height

        panel_rect = pygame.Rect(self.palette_x, panel_y, self.palette_width, panel_height)
        pygame.draw.rect(screen, self.colors['bg'], panel_rect)
        pygame.draw.line(screen, self.colors['accent'],
                         (self.palette_x, panel_y),
                         (self.palette_x + self.palette_width, panel_y), 2)

        y_pos = panel_y + 10

        snap_text = f"Grid Snap: {'ON' if self.grid_snap else 'OFF'}"
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
                'is_cutscene_trigger', False):
            id_label = self.font_medium.render("Cutscene ID:", True, self.colors['text'])
            screen.blit(id_label, (self.palette_x + self.palette_padding, y_pos))

            # ── Dropdown button ───────────────────────────────────────────────
            btn_x = self.palette_x + self.palette_padding + 120
            btn_rect = pygame.Rect(btn_x, y_pos - 3, 200, 25)
            btn_bg = self.colors['input_active'] if self.cutscene_dropdown_open else self.colors['input_bg']
            pygame.draw.rect(screen, btn_bg, btn_rect)
            pygame.draw.rect(screen,
                             self.colors['accent'] if self.cutscene_dropdown_open else self.colors['grid'],
                             btn_rect, 2)

            display_label = self.cutscene_id_text if self.cutscene_id_text else '<select cutscene>'
            label_surf = self.font_small.render(display_label, True, self.colors['text'])
            label_clip = pygame.Rect(btn_rect.x + 4, btn_rect.y, btn_rect.w - 20, btn_rect.h)
            screen.set_clip(label_clip)
            screen.blit(label_surf, (btn_rect.x + 4, btn_rect.y + 6))
            screen.set_clip(None)

            # Small arrow indicator
            arrow_x = btn_rect.right - 14
            arrow_y = btn_rect.centery
            arrow_pts = [(arrow_x, arrow_y - 4), (arrow_x + 8, arrow_y - 4), (arrow_x + 4, arrow_y + 4)]
            pygame.draw.polygon(screen, self.colors['text_dim'], arrow_pts)

            self.ui_rects['cutscene_dropdown_btn'] = btn_rect

            # ── Open dropdown list ────────────────────────────────────────────
            if self.cutscene_dropdown_open:
                names = self.cutscene_dropdown_names
                item_h = 22
                list_h = max(item_h, len(names) * item_h)
                list_rect = pygame.Rect(btn_rect.x, btn_rect.bottom, btn_rect.w, list_h)

                list_bg = pygame.Surface((list_rect.w, list_rect.h), pygame.SRCALPHA)
                list_bg.fill((30, 30, 45, 240))
                screen.blit(list_bg, list_rect.topleft)
                pygame.draw.rect(screen, self.colors['accent'], list_rect, 1)

                self.ui_rects['cutscene_dropdown_items'] = []
                if not names:
                    empty_surf = self.font_small.render('<no cutscenes found>', True, self.colors['text_dark'])
                    screen.blit(empty_surf, (list_rect.x + 4, list_rect.y + 4))
                else:
                    for i, name in enumerate(names):
                        item_rect = pygame.Rect(list_rect.x, list_rect.y + i * item_h, list_rect.w, item_h)
                        is_sel = name == self.cutscene_id_text
                        if is_sel:
                            pygame.draw.rect(screen, self.colors['variant_selected'], item_rect)
                        item_surf = self.font_small.render(name, True,
                                                           self.colors['text'] if is_sel else self.colors['text_dim'])
                        screen.blit(item_surf, (item_rect.x + 6, item_rect.y + 4))
                        self.ui_rects['cutscene_dropdown_items'].append((item_rect, name))

            y_pos += 30

            # One-shot toggle — hidden while the dropdown list is open
            if not self.cutscene_dropdown_open:
                shot_label = self.font_medium.render("One-Shot:", True, self.colors['text'])
                screen.blit(shot_label, (self.palette_x + self.palette_padding, y_pos))

                btn_x = self.palette_x + self.palette_padding + 120
                btn_rect = pygame.Rect(btn_x, y_pos - 3, 60, 22)
                btn_color = self.colors['success'] if self.cutscene_one_shot else self.colors['panel']
                pygame.draw.rect(screen, btn_color, btn_rect, border_radius=4)
                pygame.draw.rect(screen, self.colors['accent'], btn_rect, 2, border_radius=4)
                btn_text = self.font_small.render('ON' if self.cutscene_one_shot else 'OFF', True, self.colors['text'])
                screen.blit(btn_text, btn_text.get_rect(center=btn_rect.center))
                self.ui_rects['cutscene_oneshot_rect'] = btn_rect

                y_pos += 30

        if (self.selected_object and isinstance(self.selected_object, dict)
                and self.selected_object.get('object_type') == 'world_map_object'):
            current_variant = self._get_current_variant(self.selected_object)
            if current_variant and current_variant.get('type') == 'world_map':
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
                                                               self.colors['text'] if is_sel else self.colors['text_dim'])
                            screen.blit(item_surf, (item_rect.x + 6, item_rect.y + 4))
                            self.ui_rects['world_map_dropdown_items'].append((item_rect, name))

                y_pos += 30

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