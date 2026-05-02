from __future__ import annotations

# ---------------------------------------------------------------------------
# room_editor.py
#
# Top-level dev tool that lets designers browse room groups, create/edit rooms,
# and open an in-engine room viewer where tiles, objects, and entities can all
# be painted live.  Spawns three sub-editors (tileset / object / entity) that
# are toggled via toolbar buttons or F2/F3/F4, and wires them all together so
# undo/redo works across every editor type.
# ---------------------------------------------------------------------------

import copy
import pygame
import pygame.gfxdraw
import math
import time
from collections import deque
from core.camera import Camera
from config.settings import RENDER_SCALE, TILE_SIZE
from dev_tools.room_editor.room_editor_tools.object_editor import ObjectEditor
from dev_tools.room_editor.room_editor_tools.entity_editor import EntityEditor
from entities.boss_enemy import BOSS_REGISTRY


# How many actions we keep in each undo/redo stack before the oldest entry
# gets silently dropped.  50 is plenty without eating much memory.
_MAX_UNDO = 50


class _HistoryEntry:
    """
    A single undoable action recorded in the room editor.
    Stored in the undo/redo deques and replayed by _apply_entry().

    action values
    -------------
    'entity_add'    – an entity was placed            (data: entity dict copy)
    'entity_remove' – an entity was deleted           (data: entity dict copy)
    'entity_move'   – an entity was dragged           (data: {'entity_id', 'old_x', 'old_y', 'new_x', 'new_y'})
    'object_add'    – a game-object was placed        (data: {'obj', 'obj_type', 'room'})
    'object_remove' – a game-object was deleted       (data: {'obj', 'obj_type', 'room'})
    'object_move'   – a game-object was dragged       (data: {'obj', 'obj_type', 'old_x', 'old_y', 'new_x', 'new_y'})
    'tiles_stroke'  – one paint/erase stroke on tiles (data: {'room', 'before': list[dict], 'after': list[dict]})
    """
    # __slots__ keeps each entry lean — we store thousands of these over a session
    __slots__ = ('action', 'data')

    def __init__(self, action: str, data):
        self.action = action
        self.data = data


class RoomEditor:
    """Top-level room editor — lists, creates, and edits rooms and their contents."""

    def __init__(self, room_manager, screen_width, screen_height):
        self.room_manager = room_manager
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False

        self.font_title = pygame.font.Font(None, 48)
        self.font_large = pygame.font.Font(None, 32)
        self.font_medium = pygame.font.Font(None, 24)
        self.font_small = pygame.font.Font(None, 18)

        self.current_view = 'groups'
        self.selected_index = 0
        self.hover_index = -1
        self.scroll_offset = 0
        self.selected_group = None

        # Double-click detection — only active in the rooms list view
        self.last_click_index = -1
        self.last_click_time = 0
        self.double_click_threshold = 0.3  # 300ms for double-click

        self.viewing_room = None
        self.camera = Camera(screen_width, screen_height)
        # Scale speeds by RENDER_SCALE so the camera covers the same visual
        # distance per second regardless of how large the room is rendered.
        self.camera_speed = 300 * RENDER_SCALE
        self.camera_fast_speed = 600 * RENDER_SCALE

        # ── Sub-editors ───────────────────────────────────────────────────────
        # tileset_editor is initialised eagerly so the baked tile-surface cache
        # in game.py is never populated with a blank surface before the editor
        # has had a chance to load its tilesets.  Lazy init (inside toggle())
        # caused the cache to be poisoned on the very first frame of gameplay.
        from dev_tools.room_editor.room_editor_tools.tileset_editor import TilesetEditor
        self.tileset_editor = TilesetEditor(screen_width, screen_height)
        self.object_editor = None
        self.entity_editor = None

        from dev_tools.room_editor.room_editor_tools.editor_toolbar import EditorToolbar
        self.toolbar = EditorToolbar(screen_width, screen_height)

        # ── Text input ───────────────────────────────────────────────────────
        self.editing_field = None
        self.text_input = ""
        self.cursor_blink = 0

        # ── Create-room form ─────────────────────────────────────────────────
        self.create_form = {
            'name': '',
            'width': '2400',
            'height': '1800',
            'group': 'Default'
        }
        self.create_form_fields = ['name', 'width', 'height', 'group', 'create', 'cancel']

        self.editing_room = None

        # Where to return after the edit-room screen closes
        self.edit_return_view = 'rooms'

        # ── Select / drag ────────────────────────────────────────────────────
        self.drag_target = None        # the entity dict or object being dragged
        self.drag_target_type = None   # 'entity' | object-type string from _check_object_at_position
        self.drag_offset_x = 0.0       # cursor → object-centre offset in world units
        self.drag_offset_y = 0.0
        self.is_dragging = False       # True once mouse has moved after mousedown

        self._entity_last_click_target = None
        self._entity_last_click_time   = 0.0

        # ── Undo / redo ──────────────────────────────────────────────────────
        self._undo_stack = deque(maxlen=_MAX_UNDO)
        self._redo_stack = deque(maxlen=_MAX_UNDO)
        self._drag_start_world_x = 0.0
        self._drag_start_world_y = 0.0
        self._tile_stroke_before = None
        self._applying_history = False

        self.zoom_active = False  # True when zoomed out to fit entire room
        self._zoom_cache: pygame.Surface | None = None  # cached scaled surface
        self._zoom_offset = (0, 0)   # (ox, oy) blit position on screen
        self._zoom_scale  = 1.0      # fit scale factor used for coord conversion
        self._zoom_dirty  = True     # when True the cache must be rebuilt

        self.anim_timer = 0
        self.hover_anim = [0.0] * 20

        self.clickable_rects = []

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

        # ── Layout ───────────────────────────────────────────────────────────
        self.sidebar_width = 280
        self.header_height = 80
        self.item_height = 60
        self.padding = 20

        self._view_icon = self._load_icon('assets/ui/room_editor/view.png', 28, 28)

        # Wired by game.py to game.blit_room_tiles — lets the editor use the
        # same baked-surface path as gameplay instead of scaling every tile
        # every frame.  Falls back to the per-tile loop when None.
        self.blit_tiles_callback = None

    @staticmethod
    def _load_icon(path, w, h):
        """Load and scale an icon from disk. Returns None quietly if the file isn't there."""
        import os
        if not os.path.exists(path):
            return None
        try:
            img = pygame.image.load(path).convert_alpha()
            return pygame.transform.smoothscale(img, (w, h))
        except Exception:
            return None

    def deactivate(self):
        """Close the room editor and always restore key-repeat to default.
        Called both explicitly and as a fallback so we never leave the game
        stuck in the editor's 400 ms / 50 ms repeat mode."""
        self.active = False
        pygame.key.set_repeat(0, 0)

    def toggle(self):
        """Open or close the editor.  Sub-editors are initialised on first open
        and reused on subsequent visits — lazy init for object/entity editor,
        but tileset_editor is always eager (see __init__ comment above)."""
        self.active = not self.active
        if self.active:
            # Enable key-repeat so held keys produce repeated KEYDOWN events
            # (400 ms initial delay, 50 ms repeat interval)
            pygame.key.set_repeat(400, 50)
            self.current_view = 'groups'
            self.selected_index = 0
            self.hover_index = -1
            self.scroll_offset = 0
            self.editing_field = None
            self.selected_group = None
            self.last_click_index = -1
            self.last_click_time = 0

            # Lazy-init the object editor on first open — skip if already created
            if self.object_editor is None:
                self.object_editor = ObjectEditor(
                    self.screen_width,
                    self.screen_height,
                    self.room_manager
                )

                # Give the object editor a reference to our shared toolbar
                # so it can read the active tool without duplicating state
                if self.object_editor:
                    self.object_editor.set_toolbar(self.toolbar)

                # Wire object-editor placement / deletion callbacks for undo
                self._wire_undo_callbacks()

            # Lazy-init the entity editor on first open
            if self.entity_editor is None:
                self.entity_editor = EntityEditor(
                    self.screen_width,
                    self.screen_height
                )
                # This callback fires when the user clicks to place an entity in the world
                self.entity_editor.on_entity_placed = self._on_entity_placed
                # Let the entity editor read room data for mission/dialogue dropdowns
                self.entity_editor.room_manager = self.room_manager
        else:
            # Closing the editor — restore the game's normal key-repeat settings
            pygame.key.set_repeat(0, 0)

    def _refresh_placement_obstacles(self):
        """
        Build the obstacle list for entity-placement collision validation and
        push it to entity_editor.placement_obstacles.
        Called every time the entity editor is opened or the room changes.
        """
        if not self.entity_editor or not self.viewing_room or not self.object_editor:
            return
        room_name = self.viewing_room.name
        obstacles = []
        # Collision walls
        obstacles += self.object_editor.collision_manager.get_collision_objects(room_name)
        # Destructible stones
        if hasattr(self.viewing_room, 'destructible_stones'):
            obstacles += [s for s in self.viewing_room.destructible_stones if s.active and s.solid]
        # Level gates
        obstacles += self.object_editor.gate_manager.get_gates(room_name)
        # Room transitions
        obstacles += self.object_editor.transition_manager.get_transitions(room_name)
        self.entity_editor.placement_obstacles = obstacles

    def handle_input(self, event):
        """Route a pygame event to whichever sub-system owns it right now.
        Priority order: text-field capture → room-viewer → menu navigation."""
        if not self.active:
            return None

        # Text-field modal is open — swallow all input until confirmed/cancelled
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
                    # Tab commits the current field and advances to the next one
                    self._finish_text_input()
                    self._next_form_field()
                else:
                    # Cap at 50 chars; reject non-printable codes (e.g. arrow keys)
                    if len(self.text_input) < 50 and event.unicode.isprintable():
                        self.text_input += event.unicode
            return None

        # Room viewing mode gets special treatment (includes mouse)
        if self.current_view == 'view_room':
            return self._handle_view_room_input(event)

        # Handle mouse clicks for menu navigation
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mouse_pos = event.pos
            for clickable in self.clickable_rects:
                if clickable['rect'].collidepoint(mouse_pos):
                    clicked_index = clickable['index']
                    click_type = clickable.get('type', 'item')

                    # View button on a room row
                    if click_type == 'view_room':
                        self._enter_view_room(clicked_index)
                        return None

                    # Settings button on a room row
                    if click_type == 'edit_room':
                        rooms_in_group = self.room_manager.get_rooms_in_group(
                            self.selected_group) if self.selected_group else []
                        if 0 <= clicked_index < len(rooms_in_group):
                            self.editing_room = rooms_in_group[clicked_index]
                            self.current_view = 'edit'
                            self.edit_return_view = 'rooms'
                            self.selected_index = 0
                            self.hover_index = -1
                        return None

                    if self.current_view == 'rooms':
                        rooms_in_group = self.room_manager.get_rooms_in_group(
                            self.selected_group) if self.selected_group else []

                        if clicked_index < len(rooms_in_group):
                            # Single click on room row — just select it
                            self.selected_index = clicked_index
                        else:
                            # Bottom buttons (Create / Back)
                            self.selected_index = clicked_index
                            return self._handle_item_action()
                    else:
                        # All other views — single click performs action
                        self.selected_index = clicked_index
                        return self._handle_item_action()
                    break

        # Handle mouse motion for hover effects
        if event.type == pygame.MOUSEMOTION:
            mouse_pos = event.pos
            self.hover_index = -1
            for clickable in self.clickable_rects:
                if clickable['rect'].collidepoint(mouse_pos):
                    self.hover_index = clickable['index']
                    break

        # Regular navigation (keyboard only)
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                # ESC walks back up the view hierarchy:
                # groups → close editor | rooms → groups | edit → wherever we came from
                if self.current_view == 'groups':
                    self.deactivate()
                    return 'close'
                elif self.current_view == 'rooms':
                    self.current_view = 'groups'
                    self.selected_index = 0
                    self.selected_group = None
                else:
                    if self.edit_return_view == 'view_room':
                        # We came from the room viewer — go back to it
                        self.current_view = 'view_room'
                        self.edit_return_view = 'rooms'
                        pygame.key.set_repeat(0, 0)
                    elif self.selected_group:
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

    def _handle_item_action(self):
        """Handle when user clicks or presses Enter on a menu item"""
        if self.current_view == 'groups':
            total_items = len(self.room_manager.groups) + 2
            if self.selected_index < len(self.room_manager.groups):
                self.selected_group = self.room_manager.groups[self.selected_index]
                self.current_view = 'rooms'
                self.selected_index = 0
                self.hover_index = -1
            elif self.selected_index == len(self.room_manager.groups):
                self.editing_field = 'new_group'
                self.text_input = ""
            elif self.selected_index == len(self.room_manager.groups) + 1:
                self.deactivate()
                return 'close'

        elif self.current_view == 'rooms':
            if not self.selected_group:
                return None
            rooms_in_group = self.room_manager.get_rooms_in_group(self.selected_group)
            total_items = len(rooms_in_group) + 2

            if self.selected_index < len(rooms_in_group):
                # Keyboard Enter on room - open edit view (double-click handled separately in mouse code)
                self.editing_room = rooms_in_group[self.selected_index]
                self.current_view = 'edit'
                self.edit_return_view = 'rooms'
                self.selected_index = 0
                self.hover_index = -1
            elif self.selected_index == len(rooms_in_group):
                self.current_view = 'create'
                self.selected_index = 0
                self.hover_index = -1
                self.create_form = {
                    'name': '',
                    'width': '2400',
                    'height': '1800',
                    'group': self.selected_group
                }
            elif self.selected_index == len(rooms_in_group) + 1:
                self.current_view = 'groups'
                self.selected_index = 0
                self.hover_index = -1
                self.selected_group = None

        elif self.current_view == 'create':
            field = self.create_form_fields[self.selected_index]
            if field == 'create':
                self._create_room()
            elif field == 'cancel':
                self.current_view = 'rooms'
                self.selected_index = 0
                self.hover_index = -1
            elif field == 'group':
                current_group = self.create_form['group']
                groups = self.room_manager.groups
                current_idx = groups.index(current_group) if current_group in groups else 0
                next_idx = (current_idx + 1) % len(groups)
                self.create_form['group'] = groups[next_idx]
            else:
                self.editing_field = field
                self.text_input = self.create_form[field]

        elif self.current_view == 'edit':
            edit_fields = ['name', 'width', 'height', 'group', 'save', 'delete', 'cancel']
            field = edit_fields[self.selected_index]

            if field == 'save':
                self.current_view = self.edit_return_view
                if self.edit_return_view == 'view_room':
                    pygame.key.set_repeat(0, 0)
                self.edit_return_view = 'rooms'
                self.selected_index = 0
                self.hover_index = -1
            elif field == 'delete':
                self.room_manager.delete_room(self.editing_room)
                self.current_view = 'rooms'  # always go to rooms list after delete
                self.edit_return_view = 'rooms'
                self.selected_index = 0
                self.hover_index = -1
            elif field == 'cancel':
                self.current_view = self.edit_return_view
                if self.edit_return_view == 'view_room':
                    pygame.key.set_repeat(0, 0)
                self.edit_return_view = 'rooms'
                self.selected_index = 0
                self.hover_index = -1
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

    def _handle_groups_input(self, event):
        """Navigate through groups"""
        total_items = len(self.room_manager.groups) + 2

        if event.key in (pygame.K_UP, pygame.K_w):
            self.selected_index = (self.selected_index - 1) % total_items
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.selected_index = (self.selected_index + 1) % total_items
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
            return self._handle_item_action()
        elif event.key == pygame.K_DELETE:
            if 0 <= self.selected_index < len(self.room_manager.groups):
                group_name = self.room_manager.groups[self.selected_index]
                if group_name != "Default":
                    self.room_manager.delete_group(group_name)
                    self.selected_index = min(self.selected_index, len(self.room_manager.groups))

        return None

    def _enter_view_room(self, room_index):
        """Open the room viewer for the room at *room_index* in the current group."""
        rooms_in_group = self.room_manager.get_rooms_in_group(self.selected_group) if self.selected_group else []
        if not (0 <= room_index < len(rooms_in_group)):
            return
        self.viewing_room = rooms_in_group[room_index]
        self.selected_index = room_index
        room_name = self.viewing_room.name

        if self.tileset_editor:
            if room_name not in self.tileset_editor.room_tiles or not self.tileset_editor.room_tiles[room_name]:
                from dev_tools.room_editor.room_editor_tools.tileset_editor import Tile
                raw = self.viewing_room.tiles
                self.tileset_editor.room_tiles[room_name] = [
                    Tile.from_dict(t) if isinstance(t, dict) else t
                    for t in raw
                ]

        if self.object_editor and hasattr(self.object_editor, 'collision_manager'):
            self.object_editor.collision_manager.collision_objects[room_name] = []
            if not hasattr(self.viewing_room, 'collision_objects'):
                self.viewing_room.collision_objects = []
            self.object_editor.collision_manager.collision_objects[room_name] = self.viewing_room.collision_objects

        if not hasattr(self.viewing_room, 'destructible_stones'):
            self.viewing_room.destructible_stones = []

        # Sync cutscene triggers so the manager works with the room's live list.
        # Without this the manager starts empty for the room and any existing
        # triggers are invisible / overwritten on save.
        if self.object_editor and hasattr(self.object_editor, 'cutscene_trigger_manager'):
            if not hasattr(self.viewing_room, 'cutscene_triggers'):
                self.viewing_room.cutscene_triggers = []
            self.object_editor.cutscene_trigger_manager._triggers[room_name] = (
                self.viewing_room.cutscene_triggers
            )

        center_x = (self.viewing_room.width * RENDER_SCALE - self.screen_width) // 2
        center_y = (self.viewing_room.height * RENDER_SCALE - self.screen_height) // 2
        self.camera.x = center_x
        self.camera.y = center_y
        self.current_view = 'view_room'
        pygame.key.set_repeat(0, 0)

        # Fresh undo/redo history per room visit
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._tile_stroke_before = None

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
            return self._handle_item_action()
        elif event.key == pygame.K_v:
            self._enter_view_room(self.selected_index)
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
            return self._handle_item_action()

        return None

    def _handle_edit_input(self, event):
        """Handle form inputs for editing a room"""
        edit_fields = ['name', 'width', 'height', 'group', 'save', 'delete', 'cancel']
        BUTTON_INDICES = (4, 5, 6)   # save, delete, cancel — laid out horizontally
        LAST_FIELD     = 3           # 'group' — the row just above the buttons

        if event.key in (pygame.K_UP, pygame.K_w):
            if self.selected_index in BUTTON_INDICES:
                # All three buttons are in the same row — UP goes to the field above
                self.selected_index = LAST_FIELD
            else:
                self.selected_index = (self.selected_index - 1) % len(edit_fields)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            if self.selected_index == LAST_FIELD:
                # Drop down from the last field onto the first button
                self.selected_index = BUTTON_INDICES[0]
            elif self.selected_index not in BUTTON_INDICES:
                self.selected_index = (self.selected_index + 1) % len(edit_fields)
        elif event.key in (pygame.K_LEFT, pygame.K_a):
            if self.selected_index in BUTTON_INDICES:
                idx = list(BUTTON_INDICES).index(self.selected_index)
                self.selected_index = BUTTON_INDICES[(idx - 1) % len(BUTTON_INDICES)]
        elif event.key in (pygame.K_RIGHT, pygame.K_d):
            if self.selected_index in BUTTON_INDICES:
                idx = list(BUTTON_INDICES).index(self.selected_index)
                self.selected_index = BUTTON_INDICES[(idx + 1) % len(BUTTON_INDICES)]
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
            return self._handle_item_action()

        return None

    def _handle_view_room_input(self, event):
        """Handle inputs while viewing/editing a room"""

        # ── Undo / Redo ───────────────────────────────────────────────────────
        if event.type == pygame.KEYDOWN:
            ctrl = pygame.key.get_mods() & pygame.KMOD_CTRL
            if ctrl and event.key == pygame.K_z:
                self._apply_undo()
                return None
            if ctrl and event.key in (pygame.K_y, pygame.K_r):
                self._apply_redo()
                return None
            # Ctrl+Shift+Z also redoes (common convention)
            if ctrl and event.key == pygame.K_z and (pygame.key.get_mods() & pygame.KMOD_SHIFT):
                self._apply_redo()
                return None

        # Check if we're in transition spawn placement mode
        is_placing_spawn = (self.object_editor and
                            hasattr(self.object_editor, 'placing_transition_spawn') and
                            self.object_editor.placing_transition_spawn)

        # If placing spawn, ONLY allow object editor input
        if is_placing_spawn:
            if self.object_editor:
                self.object_editor.handle_input(
                    event,
                    int(self.camera.x),
                    int(self.camera.y),
                    self.viewing_room.name if self.viewing_room else ""
                )
            return None

        # Normal editor mode - check for toolbar clicks
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            result = self.toolbar.handle_click(event.pos)
            if result:
                # Each editor panel is mutually exclusive — toggling one always
                # closes the others so we never end up with two panels open.
                if result == 'tiles':
                    if self.tileset_editor:
                        self.tileset_editor.toggle()
                        if self.object_editor and self.object_editor.active:
                            self.object_editor.toggle()
                        if self.entity_editor and self.entity_editor.active:
                            self.entity_editor.toggle()
                        # Clear any in-progress drag so it doesn't ghost across panels
                        self.drag_target = None
                        self.drag_target_type = None
                        self.is_dragging = False
                elif result == 'objects':
                    if self.object_editor:
                        self.object_editor.toggle()
                        if self.tileset_editor and self.tileset_editor.active:
                            self.tileset_editor.toggle()
                        if self.entity_editor and self.entity_editor.active:
                            self.entity_editor.toggle()
                        self.drag_target = None
                        self.drag_target_type = None
                        self.is_dragging = False
                elif result == 'entities':
                    if self.entity_editor:
                        self.entity_editor.toggle()
                        if self.tileset_editor and self.tileset_editor.active:
                            self.tileset_editor.toggle()
                        if self.object_editor and self.object_editor.active:
                            self.object_editor.toggle()
                        self.drag_target = None
                        self.drag_target_type = None
                        self.is_dragging = False
                        # Rebuild obstacles so entity placement collision checks are fresh
                        if self.entity_editor.active:
                            self._refresh_placement_obstacles()
                elif result == 'settings':
                    self.editing_room = self.viewing_room
                    self.current_view = 'edit'
                    self.edit_return_view = 'view_room'
                    self.selected_index = 0
                    self.hover_index = -1
                elif result == 'action_zoom':
                    self.zoom_active = not self.zoom_active
                    if self.zoom_active:
                        # Mark cache dirty so the next draw rebuilds the overview surface
                        self._zoom_dirty = True
                elif result == 'action_test':
                    self.deactivate()
                    return f'test_room:{self.viewing_room.name}'
                elif result == 'action_save':
                    self._save_current_room()
                return None

        # F2/F3/F4 are keyboard shortcuts for the same toolbar buttons above.
        # Same mutual-exclusion logic applies — toggling one closes the others.
        if event.type == pygame.KEYDOWN and event.key == pygame.K_F2:  # Tile editor
            if self.tileset_editor:
                self.tileset_editor.toggle()
                if self.object_editor and self.object_editor.active:
                    self.object_editor.toggle()
                if self.entity_editor and self.entity_editor.active:
                    self.entity_editor.toggle()
            return None

        if event.type == pygame.KEYDOWN and event.key == pygame.K_F3:  # Object editor
            if self.object_editor:
                self.object_editor.toggle()
                if self.tileset_editor and self.tileset_editor.active:
                    self.tileset_editor.toggle()
                if self.entity_editor and self.entity_editor.active:
                    self.entity_editor.toggle()
            return None

        if event.type == pygame.KEYDOWN and event.key == pygame.K_F4:  # Entity editor
            if self.entity_editor:
                self.entity_editor.toggle()
                if self.tileset_editor and self.tileset_editor.active:
                    self.tileset_editor.toggle()
                if self.object_editor and self.object_editor.active:
                    self.object_editor.toggle()
                if self.entity_editor.active:
                    self._refresh_placement_obstacles()
            return None

        # Pass input to active editor
        if self.tileset_editor and self.tileset_editor.active:
            room_name = self.viewing_room.name if self.viewing_room else ""

            # ── Tile-stroke undo snapshot logic ─────────────────────────────
            # A stroke begins on MOUSEBUTTONDOWN (left or right) in the world
            # and ends on MOUSEBUTTONUP.  Keyboard deletes are treated as
            # instant single-action strokes.
            is_key_delete = (
                event.type == pygame.KEYDOWN and
                event.key in (pygame.K_DELETE, pygame.K_x)
            )
            is_mouse_down = (
                event.type == pygame.MOUSEBUTTONDOWN and
                event.button in (1, 3) and
                not self.tileset_editor._is_in_palette(*event.pos)
            )
            is_mouse_up = event.type == pygame.MOUSEBUTTONUP and event.button in (1, 3)

            if (is_mouse_down or is_key_delete) and self._tile_stroke_before is None and room_name:
                # snapshot tiles before the stroke
                self._tile_stroke_before = [
                    t.to_dict()
                    for t in self.tileset_editor.room_tiles.get(room_name, [])
                ]

            self.tileset_editor.handle_input(
                event,
                int(self.camera.x),
                int(self.camera.y),
                room_name
            )

            if is_mouse_up and self._tile_stroke_before is not None and room_name:
                after = [t.to_dict() for t in self.tileset_editor.room_tiles.get(room_name, [])]
                if after != self._tile_stroke_before:
                    self._push_undo(_HistoryEntry('tiles_stroke', {
                        'room':   room_name,
                        'before': self._tile_stroke_before,
                        'after':  after,
                    }))
                self._tile_stroke_before = None

            if is_key_delete and self._tile_stroke_before is not None and room_name:
                after = [t.to_dict() for t in self.tileset_editor.room_tiles.get(room_name, [])]
                if after != self._tile_stroke_before:
                    self._push_undo(_HistoryEntry('tiles_stroke', {
                        'room':   room_name,
                        'before': self._tile_stroke_before,
                        'after':  after,
                    }))
                self._tile_stroke_before = None

            return None

        if self.object_editor and self.object_editor.active:
            # ── Cutscene-trigger drag intercept ───────────────────────────────
            # When the object editor is active its placement handler fires on
            # every left-click, which would create a second trigger instead of
            # moving the existing one.  We intercept mouse events here — before
            # they reach handle_input — whenever the cursor is on an existing
            # trigger so that we own the drag and the object editor never sees
            # the click that would cause the duplication.
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my = event.pos
                if not self.object_editor._is_in_palette(mx, my):
                    world_x, world_y = self._screen_to_world(mx, my)
                    hit_trigger = self._find_cutscene_trigger_at(world_x, world_y)
                    if hit_trigger is not None:
                        self.drag_target = hit_trigger
                        self.drag_target_type = 'cutscene_trigger'
                        self.drag_offset_x = hit_trigger.x - world_x
                        self.drag_offset_y = hit_trigger.y - world_y
                        self.is_dragging = False
                        self._drag_start_world_x = hit_trigger.x
                        self._drag_start_world_y = hit_trigger.y
                        return None  # consumed — object editor must not see this click

            if event.type == pygame.MOUSEMOTION and self.drag_target_type == 'cutscene_trigger':
                if self.drag_target is not None and pygame.mouse.get_pressed()[0]:
                    mx, my = event.pos
                    world_x, world_y = self._screen_to_world(mx, my)
                    self.is_dragging = True
                    self.drag_target.x = world_x + self.drag_offset_x
                    self.drag_target.y = world_y + self.drag_offset_y
                    return None

            if event.type == pygame.MOUSEBUTTONUP and event.button == 1 \
                    and self.drag_target_type == 'cutscene_trigger':
                if self.drag_target is not None and self.is_dragging:
                    new_x, new_y = self.drag_target.x, self.drag_target.y
                    if (new_x, new_y) != (self._drag_start_world_x, self._drag_start_world_y):
                        self._push_undo(_HistoryEntry('object_move', {
                            'obj':      self.drag_target,
                            'obj_type': 'cutscene_trigger',
                            'old_x':    self._drag_start_world_x,
                            'old_y':    self._drag_start_world_y,
                            'new_x':    new_x,
                            'new_y':    new_y,
                        }))
                self.drag_target = None
                self.drag_target_type = None
                self.is_dragging = False
                return None
            # ── end cutscene-trigger drag intercept ───────────────────────────

            result = self.object_editor.handle_input(
                event,
                int(self.camera.x),
                int(self.camera.y),
                self.viewing_room.name if self.viewing_room else ""
            )

            # Handle room transition from path editor
            if result and result.startswith('transition:'):
                target_room_name = result.split(':', 1)[1]
                self._switch_to_room_for_path_editing(target_room_name)
                return None

            # NEW: Handle return to initial room after saving path
            if result and result.startswith('return_to_room:'):
                return_room_name = result.split(':', 1)[1]
                self._return_to_initial_room(return_room_name)
                return None

            # Check if we need to switch rooms for spawn placement
            if hasattr(self.object_editor, 'placing_transition_spawn') and self.object_editor.placing_transition_spawn:
                if hasattr(self.object_editor, 'pending_transition_for_spawn'):
                    transition = self.object_editor.pending_transition_for_spawn
                    target_room_name = transition.target_room

                    if target_room_name:
                        target_room = self.room_manager.get_room_by_name(target_room_name)
                        if target_room and target_room != self.viewing_room:
                            # Switch to target room
                            self.viewing_room = target_room
                            self._sync_room_to_editor(target_room)

                            # Center camera on room (always centered regardless of size)
                            center_x = (target_room.width * RENDER_SCALE - self.screen_width) // 2
                            center_y = (target_room.height * RENDER_SCALE - self.screen_height) // 2
                            self.camera.x = center_x
                            self.camera.y = center_y
            return None

        # Pass input to entity editor when it is the active palette
        if self.entity_editor and self.entity_editor.active:
            # ESC closes entity editor — but only when no dialogue popup is open.
            # When the popup IS open, fall through to handle_event so it can
            # dismiss the popup itself (its own ESC branch).
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                if self.entity_editor._dialogue_popup is None:
                    self.entity_editor.toggle()
                    return None
                # popup is open → let handle_event below consume the ESC

            # Delegate scroll / click / hotkeys to entity editor
            consumed = self.entity_editor.handle_event(
                event,
                int(self.camera.x),
                int(self.camera.y)
            )
            if consumed:
                return None

            # In popup-only mode, block all other interaction
            if self.entity_editor._popup_only_mode:
                return None

            # Right-click in the world deletes the nearest placed entity
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
                mx, my = event.pos
                if not self.entity_editor._mouse_in_palette(mx, my):
                    self._delete_entity_at(mx, my)
                    return None

            return None

        # Exit the room viewer
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._save_current_room()
                self.current_view = 'rooms'
                self.viewing_room = None
                self.selected_index = 0
                self.hover_index = -1
                self.drag_target = None
                self.drag_target_type = None
                self.is_dragging = False
                pygame.key.set_repeat(400, 50)

            return None

        # Select / drag / right-click-delete when no panel is open
        if self._no_editor_active():
            if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEMOTION, pygame.MOUSEBUTTONUP):
                self._handle_select_drag_event(event)

        return None

    def _return_to_initial_room(self, initial_room_name: str):
        """Return to the initial room after finishing flying pad path"""
        initial_room = self.room_manager.get_room_by_name(initial_room_name)

        if not initial_room:
            return

        # Save current room before switching
        if self.viewing_room and self.viewing_room != initial_room:
            self._save_current_room()

        # Switch back to initial room
        self.room_manager.current_room = initial_room
        self.viewing_room = initial_room

        # Sync room data to editor
        self._sync_room_to_editor(initial_room)

        # Center camera on the middle of the room
        from config.settings import SCREEN_WIDTH, SCREEN_HEIGHT
        self.camera.x = (initial_room.width // 2 * RENDER_SCALE) - (SCREEN_WIDTH // 2)
        self.camera.y = (initial_room.height // 2 * RENDER_SCALE) - (SCREEN_HEIGHT // 2)

        # Note: Camera clamping is handled in update() method

        # Keep object editor active
        if self.object_editor:
            self.object_editor.current_room_name = initial_room.name
            self.object_editor.active = True

    def _switch_to_room_for_path_editing(self, target_room_name: str):
        """Switch editor view to target room when building flying pad paths"""
        target_room = self.room_manager.get_room_by_name(target_room_name)

        if not target_room:
            return

        # Save current room state before switching
        if self.viewing_room:
            self._save_current_room()

        # Switch to the target room
        self.room_manager.current_room = target_room
        self.viewing_room = target_room

        # Sync room data to editor
        self._sync_room_to_editor(target_room)

        # Center camera on the middle of the new room
        from config.settings import SCREEN_WIDTH, SCREEN_HEIGHT
        self.camera.x = (target_room.width // 2 * RENDER_SCALE) - (SCREEN_WIDTH // 2)
        self.camera.y = (target_room.height // 2 * RENDER_SCALE) - (SCREEN_HEIGHT // 2)

        # Note: Camera clamping is handled in update() method

        # Update object editor's current room
        if self.object_editor:
            self.object_editor.current_room_name = target_room.name

            # Update room dimensions in the path editor so boundary detection is accurate
            if hasattr(self.object_editor, 'flying_pad_path_editor'):
                available_rooms = self.room_manager.get_room_names()
                path_editor = self.object_editor.flying_pad_path_editor

                # Update available rooms
                path_editor.available_rooms = [
                    r for r in available_rooms if r != target_room.name
                ]
                path_editor.current_room_name = target_room.name

                # NEW: Update room dimensions for boundary detection
                path_editor.room_width = target_room.width
                path_editor.room_height = target_room.height

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
            self.hover_index = -1
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

        # Move flying pads from editor to room
        if self.object_editor and hasattr(self.object_editor, 'flying_pad_manager'):
            pads = self.object_editor.flying_pad_manager.get_pads(self.viewing_room.name)
            self.viewing_room.flying_pads = pads

        # Make sure destructible stones exist
        if not hasattr(self.viewing_room, 'destructible_stones'):
            self.viewing_room.destructible_stones = []

        # Entities are stored directly on the room; guarantee the list exists
        if not hasattr(self.viewing_room, 'entities'):
            self.viewing_room.entities = []

        # Move cutscene triggers from editor to room before writing to disk.
        # The manager holds the live list (shared by reference after _enter_view_room),
        # but we re-assign here to be safe in case the manager replaced its list.
        if self.object_editor and hasattr(self.object_editor, 'cutscene_trigger_manager'):
            triggers = self.object_editor.cutscene_trigger_manager._triggers.get(
                self.viewing_room.name, []
            )
            self.viewing_room.cutscene_triggers = triggers if triggers is not None else []
        else:
            if not hasattr(self.viewing_room, 'cutscene_triggers'):
                self.viewing_room.cutscene_triggers = []

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

            # Flying pads
            if hasattr(self.object_editor, 'flying_pad_manager'):
                for room in self.room_manager.rooms:
                    pads = self.object_editor.flying_pad_manager.get_pads(room.name)
                    if pads:
                        room.flying_pads = pads
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

            # Room transitions
            if hasattr(self.object_editor, 'transition_manager'):
                for room in self.room_manager.rooms:
                    transitions = self.object_editor.transition_manager.get_transitions(room.name)
                    if transitions:
                        room.room_transitions = transitions
                        transferred_count += 1

            # Level gates
            if hasattr(self.object_editor, 'gate_manager'):
                for room in self.room_manager.rooms:
                    gates = self.object_editor.gate_manager.get_gates(room.name)
                    if gates:
                        room.level_gates = gates
                        transferred_count += 1

            # Cutscene triggers
            if hasattr(self.object_editor, 'cutscene_trigger_manager'):
                for room in self.room_manager.rooms:
                    triggers = self.object_editor.cutscene_trigger_manager._triggers.get(
                        room.name, []
                    )
                    if triggers:
                        room.cutscene_triggers = triggers
                        transferred_count += 1

        return transferred_count

    def _sync_room_to_editor(self, room):
        """Sync room data to editor managers when switching rooms"""
        if not room:
            return

        room_name = room.name

        # Sync tiles
        if self.tileset_editor:
            if room_name not in self.tileset_editor.room_tiles or not self.tileset_editor.room_tiles[room_name]:
                from dev_tools.room_editor.room_editor_tools.tileset_editor import Tile
                self.tileset_editor.room_tiles[room_name] = [
                    Tile.from_dict(t) if isinstance(t, dict) else t
                    for t in room.tiles
                ]

        # Sync collision objects
        if self.object_editor and hasattr(self.object_editor, 'collision_manager'):
            if not hasattr(room, 'collision_objects'):
                room.collision_objects = []
            self.object_editor.collision_manager.collision_objects[room_name] = room.collision_objects

        # Sync flying pads
        if self.object_editor and hasattr(self.object_editor, 'flying_pad_manager'):
            if not hasattr(room, 'flying_pads'):
                room.flying_pads = []
            self.object_editor.flying_pad_manager.flying_pads[room_name] = room.flying_pads

        # Sync destructible stones
        if not hasattr(room, 'destructible_stones'):
            room.destructible_stones = []

        # Sync entities
        if not hasattr(room, 'entities'):
            room.entities = []

        # Sync spawn points
        if self.object_editor and hasattr(self.object_editor, 'spawn_manager'):
            if hasattr(room, 'spawn_points') and room.spawn_points:
                for spawn in room.spawn_points:
                    self.object_editor.spawn_manager.spawn_points[room_name] = spawn

        # Sync transitions
        if self.object_editor and hasattr(self.object_editor, 'transition_manager'):
            if not hasattr(room, 'room_transitions'):
                room.room_transitions = []
            self.object_editor.transition_manager.transitions[room_name] = room.room_transitions

        # Sync level gates
        if self.object_editor and hasattr(self.object_editor, 'gate_manager'):
            if not hasattr(room, 'level_gates'):
                room.level_gates = []
            self.object_editor.gate_manager.gates[room_name] = room.level_gates

        # Sync cutscene triggers
        if self.object_editor and hasattr(self.object_editor, 'cutscene_trigger_manager'):
            if not hasattr(room, 'cutscene_triggers'):
                room.cutscene_triggers = []
            self.object_editor.cutscene_trigger_manager._triggers[room_name] = room.cutscene_triggers

    # =========================================================================
    # Undo / Redo  (Ctrl-Z / Ctrl-Y)
    # =========================================================================

    def _wire_undo_callbacks(self):
        """Attach placement/deletion callbacks to the object editor so every
        object mutation can be recorded in the undo history.

        We use closures rather than methods so each callback captures the exact
        obj_type string it needs without us having to pass it at call-site.
        The object editor just calls e.g. on_collision_placed(obj, room) and we
        handle the undo book-keeping here.
        """
        oe = self.object_editor
        if not oe:
            return

        # ── placements ────────────────────────────────────────────────────────
        def _on_collision_placed(obj, room):
            self._push_undo(_HistoryEntry('object_add', {'obj': obj, 'obj_type': 'collision', 'room': room}))
        oe.on_collision_placed = _on_collision_placed

        def _on_gate_placed(obj, room):
            self._push_undo(_HistoryEntry('object_add', {'obj': obj, 'obj_type': 'gate', 'room': room}))
        oe.on_gate_placed = _on_gate_placed

        def _on_spawn_placed(obj, room):
            self._push_undo(_HistoryEntry('object_add', {'obj': obj, 'obj_type': 'spawn', 'room': room}))
        oe.on_spawn_placed = _on_spawn_placed

        def _on_stone_placed(obj, room):
            self._push_undo(_HistoryEntry('object_add', {'obj': obj, 'obj_type': 'stone', 'room': room}))
        oe.on_stone_placed = _on_stone_placed

        def _on_save_point_placed(obj):
            room = oe.current_room_name
            self._push_undo(_HistoryEntry('object_add', {'obj': obj, 'obj_type': 'save_point', 'room': room}))
        oe.on_save_point_placed = _on_save_point_placed

        def _on_flying_pad_placed(obj, room):
            self._push_undo(_HistoryEntry('object_add', {'obj': obj, 'obj_type': 'flying_pad', 'room': room}))
        oe.on_flying_pad_placed = _on_flying_pad_placed

        def _on_transition_placed(obj, room):
            self._push_undo(_HistoryEntry('object_add', {'obj': obj, 'obj_type': 'transition', 'room': room}))
        oe.on_transition_placed = _on_transition_placed

        # ── deletions ─────────────────────────────────────────────────────────
        def _on_collision_deleted(obj, room):
            self._push_undo(_HistoryEntry('object_remove', {'obj': obj, 'obj_type': 'collision', 'room': room}))
        oe.on_collision_deleted = _on_collision_deleted

        def _on_gate_deleted(obj, room):
            self._push_undo(_HistoryEntry('object_remove', {'obj': obj, 'obj_type': 'gate', 'room': room}))
        oe.on_gate_deleted = _on_gate_deleted

        def _on_spawn_deleted(obj, room):
            self._push_undo(_HistoryEntry('object_remove', {'obj': obj, 'obj_type': 'spawn', 'room': room}))
        oe.on_spawn_deleted = _on_spawn_deleted

        def _on_stone_deleted(obj, room):
            self._push_undo(_HistoryEntry('object_remove', {'obj': obj, 'obj_type': 'stone', 'room': room}))
        oe.on_stone_deleted = _on_stone_deleted

        def _on_save_point_deleted(obj):
            room = oe.current_room_name
            self._push_undo(_HistoryEntry('object_remove', {'obj': obj, 'obj_type': 'save_point', 'room': room}))
        oe.on_save_point_deleted = _on_save_point_deleted

        def _on_flying_pad_deleted(obj, room):
            self._push_undo(_HistoryEntry('object_remove', {'obj': obj, 'obj_type': 'flying_pad', 'room': room}))
        oe.on_flying_pad_deleted = _on_flying_pad_deleted

        def _on_transition_deleted(obj, room):
            self._push_undo(_HistoryEntry('object_remove', {'obj': obj, 'obj_type': 'transition', 'room': room}))
        oe.on_transition_deleted = _on_transition_deleted

    def _push_undo(self, entry: _HistoryEntry):
        """Record a new action; doing so discards the redo stack.
        No-op while an undo/redo operation is being applied — prevents the
        placement/deletion callbacks from injecting phantom entries."""
        if self._applying_history:
            return
        self._undo_stack.append(entry)
        self._redo_stack.clear()
        self._zoom_dirty = True

    def _apply_undo(self):
        """Pop the top undo entry, reverse it, and push it onto the redo stack."""
        if not self._undo_stack:
            return
        entry = self._undo_stack.pop()
        self._applying_history = True
        try:
            self._apply_entry(entry, forward=False)
        finally:
            self._applying_history = False
        self._redo_stack.append(entry)
        self._zoom_dirty = True

    def _apply_redo(self):
        """Pop the top redo entry, replay it, and push it back onto the undo stack."""
        if not self._redo_stack:
            return
        entry = self._redo_stack.pop()
        self._applying_history = True
        try:
            self._apply_entry(entry, forward=True)
        finally:
            self._applying_history = False
        self._undo_stack.append(entry)
        self._zoom_dirty = True

    def _apply_entry(self, entry: _HistoryEntry, forward: bool):
        """Apply or reverse a history entry.
        *forward=True*  → redo  (replay the original action)
        *forward=False* → undo  (reverse the original action)
        """
        action = entry.action
        data   = entry.data

        # Resolve the "effective" operation from action + direction.
        # e.g. undoing an entity_add is the same as removing the entity,
        # and redoing an entity_remove is also removing it.
        adding    = (action == 'entity_add'    and forward) or (action == 'entity_remove' and not forward)
        removing  = (action == 'entity_remove' and forward) or (action == 'entity_add'    and not forward)
        obj_add   = (action == 'object_add'    and forward) or (action == 'object_remove' and not forward)
        obj_del   = (action == 'object_remove' and forward) or (action == 'object_add'    and not forward)

        # ── entities ─────────────────────────────────────────────────────────
        if adding:
            if self.viewing_room is not None:
                if not hasattr(self.viewing_room, 'entities'):
                    self.viewing_room.entities = []
                self.viewing_room.entities.append(data)

        elif removing:
            if self.viewing_room is not None and hasattr(self.viewing_room, 'entities'):
                target_id = data.get('instance_id')
                self.viewing_room.entities = [
                    e for e in self.viewing_room.entities
                    if e.get('instance_id') != target_id
                ]

        elif action == 'entity_move':
            if self.viewing_room is not None and hasattr(self.viewing_room, 'entities'):
                target_id = data['instance_id']
                for ent in self.viewing_room.entities:
                    if ent.get('instance_id') == target_id:
                        ent['x'] = data['new_x'] if forward else data['old_x']
                        ent['y'] = data['new_y'] if forward else data['old_y']
                        break

        # ── objects ──────────────────────────────────────────────────────────
        elif obj_add:
            self._readd_object(data['obj'], data['obj_type'], data['room'])

        elif obj_del:
            if self.object_editor:
                self.object_editor.current_room_name = data['room']
                self.object_editor._delete_object(data['obj'], data['obj_type'])

        elif action == 'object_move':
            obj  = data['obj']
            new_x = data['new_x'] if forward else data['old_x']
            new_y = data['new_y'] if forward else data['old_y']
            obj.x = new_x
            obj.y = new_y

        # ── tiles ────────────────────────────────────────────────────────────
        elif action == 'tiles_stroke':
            if self.tileset_editor is None or not self.viewing_room:
                return
            room = data['room']
            from dev_tools.room_editor.room_editor_tools.tileset_editor import Tile
            tile_list = data['after'] if forward else data['before']
            self.tileset_editor.room_tiles[room] = [Tile.from_dict(t) for t in tile_list]

    def _readd_object(self, obj, obj_type: str, room_name: str):
        """Re-insert a previously deleted object back into the room (for undo/redo)."""
        if not self.object_editor:
            return
        oe = self.object_editor
        room = self.room_manager.get_room_by_name(room_name) if self.room_manager else None

        if obj_type == 'collision':
            oe.collision_manager.collision_objects.setdefault(room_name, []).append(obj)
            if room is not None:
                if not hasattr(room, 'collision_objects'):
                    room.collision_objects = []
                if obj not in room.collision_objects:
                    room.collision_objects.append(obj)

        elif obj_type == 'gate':
            oe.gate_manager.add_gate(room_name, obj)
            if room is not None:
                if not hasattr(room, 'level_gates'):
                    room.level_gates = []
                if obj not in room.level_gates:
                    room.level_gates.append(obj)

        elif obj_type == 'spawn':
            oe.spawn_manager.spawn_points[room_name] = obj
            if room is not None:
                room.spawn_points = [obj]

        elif obj_type == 'stone':
            if room is not None:
                if not hasattr(room, 'destructible_stones'):
                    room.destructible_stones = []
                if obj not in room.destructible_stones:
                    room.destructible_stones.append(obj)

        elif obj_type == 'save_point':
            oe.save_point_manager.add_save_point(room_name, obj)
            if room is not None:
                if not hasattr(room, 'save_points'):
                    room.save_points = []
                if obj not in room.save_points:
                    room.save_points.append(obj)

        elif obj_type == 'flying_pad':
            oe.flying_pad_manager.add_pad(room_name, obj)
            if room is not None:
                if not hasattr(room, 'flying_pads'):
                    room.flying_pads = []
                if obj not in room.flying_pads:
                    room.flying_pads.append(obj)

        elif obj_type == 'transition':
            oe.transition_manager.add_transition(room_name, obj)
            if room is not None:
                if not hasattr(room, 'room_transitions'):
                    room.room_transitions = []
                if obj not in room.room_transitions:
                    room.room_transitions.append(obj)

    # ─────────────────────────────────────────────────────────────────────────
    # Entity editor helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _on_entity_placed(self, entity, variant, ai_type, world_x, world_y):
        """Callback fired by EntityEditor when the user clicks in the world.
        Persists a lightweight record onto viewing_room.entities so it survives
        save / reload without needing the editor to be open.
        """
        if not self.viewing_room:
            return

        if not hasattr(self.viewing_room, 'entities'):
            self.viewing_room.entities = []

        # ai_type passed directly; fall back to easy if None
        if entity.get('entity_type') in ['enemy', 'boss'] and not ai_type:
            ai_type = 'easy'

        # Extract enemy category (melee vs shooter) — only relevant for enemies/bosses
        entity_type = entity.get('entity_type')
        enemy_category = (
            entity.get('enemy_category', 'melee')
            if entity_type in ('enemy', 'boss')
            else None
        )

        # Generate a stable short instance ID for this entity
        import uuid
        instance_id = str(uuid.uuid4())[:8]

        entity_data = {
            'instance_id': instance_id,
            'id': entity['id'],
            'name': entity['name'],
            'entity_type': entity['entity_type'],
            'variant_type': variant['type'] if variant else None,
            'variant_name': variant['name'] if variant else None,
            'variant_color': variant['color'] if variant else None,
            'x': world_x,
            'y': world_y,
            'width': entity['width'],
            'height': entity['height'],
            'hitbox_height': entity.get('hitbox_height', entity['height']),
            'shadow_width':   entity.get('shadow_width', 32),
            'shadow_y_offset': entity.get('shadow_y_offset', 0),
        }

        # For bosses, pull shadow config directly from BOSS_REGISTRY so there's
        # a single source of truth — no need to duplicate values in entity_editor.
        if entity_data['entity_type'] == 'boss':
            boss_cfg = BOSS_REGISTRY.get(entity_data['id'], {})
            entity_data['hitbox_height']   = boss_cfg.get('hitbox_height', boss_cfg.get('height', entity['height']))
            entity_data['shadow_width']    = boss_cfg.get('shadow_width', 32)
            entity_data['shadow_y_offset'] = boss_cfg.get('shadow_y_offset', 0)

        # Add ai_type for enemies and bosses
        if ai_type:
            entity_data['ai_type'] = ai_type

        # Add enemy_category for enemies and bosses
        if enemy_category:
            entity_data['enemy_category'] = enemy_category

        # Add NPC-specific settings
        if entity.get('entity_type') == 'npc':
            entity_data['npc_mode']        = entity.get('_npc_mode',   'static')
            entity_data['npc_facing']      = entity.get('_npc_facing', 'down')
            entity_data['dialogue_config'] = entity.get('_npc_dialogue_config', None)
            # Attach mission if the editor set one; stamp it with the instance_id
            if entity.get('_npc_mission'):
                mission = dict(entity['_npc_mission'])
                mission['id']                = instance_id
                mission['giver_instance_id'] = instance_id
                entity_data['mission'] = mission

        self.viewing_room.entities.append(entity_data)
        # Record in undo history (deep-copy so later edits don't mutate the snapshot)
        self._push_undo(_HistoryEntry('entity_add', copy.deepcopy(entity_data)))

    def _delete_entity_at(self, screen_x, screen_y):
        """Remove the entity closest to a right-click position.
        Works in world coordinates; threshold is 40 units so it feels
        generous on small sprites without being sloppy on dense rooms."""
        if not self.viewing_room or not hasattr(self.viewing_room, 'entities'):
            return

        world_x = (screen_x + self.camera.x) / RENDER_SCALE
        world_y = (screen_y + self.camera.y) / RENDER_SCALE

        best_idx = -1
        best_dist = 40  # world-unit click radius

        for i, ent in enumerate(self.viewing_room.entities):
            dx = ent['x'] - world_x
            dy = ent['y'] - world_y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_idx = i

        if best_idx >= 0:
            removed = self.viewing_room.entities[best_idx]
            self._push_undo(_HistoryEntry('entity_remove', copy.deepcopy(removed)))
            self.viewing_room.entities.pop(best_idx)

    # =========================================================================
    # Select / drag / delete  (no-editor-panel mode)
    # =========================================================================

    def _no_editor_active(self):
        """True when all editor panels are closed – the mode that allows drag/select."""
        return (
            not (self.tileset_editor and self.tileset_editor.active) and
            not (self.object_editor and self.object_editor.active) and
            not (self.entity_editor and self.entity_editor.active)
        )

    def _find_entity_at(self, world_x, world_y):
        """Return the entity dict under world_x/y, or None."""
        if not self.viewing_room or not hasattr(self.viewing_room, 'entities'):
            return None
        for ent in reversed(self.viewing_room.entities):  # top-most first
            hw = ent['width'] / 2
            hh = ent['height'] / 2
            if (abs(ent['x'] - world_x) <= hw and abs(ent['y'] - world_y) <= hh):
                return ent
        return None

    def _find_object_at(self, world_x, world_y):
        """Return (obj, obj_type) for the placed object under the cursor, or (None, None)."""
        if not self.object_editor:
            return None, None
        self.object_editor.current_room_name = self.viewing_room.name
        return self.object_editor._check_object_at_position(world_x, world_y)

    def _find_cutscene_trigger_at(self, world_x, world_y):
        """Return the CutsceneTrigger object whose rect contains (world_x, world_y), or None.

        Used to intercept clicks on existing triggers so the object editor's
        placement handler never fires and duplicates the trigger.
        """
        if not self.viewing_room or not self.object_editor:
            return None
        if not hasattr(self.object_editor, 'cutscene_trigger_manager'):
            return None
        room_name = self.viewing_room.name
        triggers = self.object_editor.cutscene_trigger_manager._triggers.get(room_name, [])
        for trigger in reversed(triggers):  # top-most first
            tx = getattr(trigger, 'x', None)
            ty = getattr(trigger, 'y', None)
            tw = getattr(trigger, 'width',  64)
            th = getattr(trigger, 'height', 64)
            if tx is None or ty is None:
                continue
            # Triggers are axis-aligned rects; treat (x, y) as centre.
            if (abs(tx - world_x) <= tw / 2 and abs(ty - world_y) <= th / 2):
                return trigger
        return None

    def _screen_to_world(self, sx, sy):
        return (sx + self.camera.x) / RENDER_SCALE, (sy + self.camera.y) / RENDER_SCALE

    def _handle_select_drag_event(self, event):
        """Handle click-select, drag, and right-click-delete when no editor panel is open."""
        if not self._no_editor_active():
            return False

        # Ignore toolbar area (top 80px)
        mx, my = event.pos
        if my < self.toolbar.height:
            return False

        world_x, world_y = self._screen_to_world(mx, my)

        # ── right-click: delete ───────────────────────────────────────────
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            # Try entity first
            ent = self._find_entity_at(world_x, world_y)
            if ent:
                self._push_undo(_HistoryEntry('entity_remove', copy.deepcopy(ent)))
                self.viewing_room.entities.remove(ent)
                if self.drag_target is ent:
                    self.drag_target = None
                    self.drag_target_type = None
                    self.is_dragging = False
                return True
            # Try object
            obj, obj_type = self._find_object_at(world_x, world_y)
            if obj and obj_type:
                # callback in object_editor will push the undo entry
                self.object_editor._delete_object(obj, obj_type)
                if self.drag_target is obj:
                    self.drag_target = None
                    self.drag_target_type = None
                    self.is_dragging = False
                return True
            return False

        # ── left mouse down: begin select/drag ────────────────────────────
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            # Try entity
            ent = self._find_entity_at(world_x, world_y)
            if ent:
                now = time.time()
                is_double = (
                    ent is self._entity_last_click_target and
                    (now - self._entity_last_click_time) < self.double_click_threshold
                )
                self._entity_last_click_target = ent
                self._entity_last_click_time   = now

                if is_double and ent.get('entity_type') == 'npc':
                    # Double-click on NPC → open dialogue editor
                    if self.entity_editor:
                        self.entity_editor.open_npc_edit_popup(ent)
                    return True

                self.drag_target = ent
                self.drag_target_type = 'entity'
                self.drag_offset_x = ent['x'] - world_x
                self.drag_offset_y = ent['y'] - world_y
                self.is_dragging = False
                # snapshot position at drag start for undo
                self._drag_start_world_x = ent['x']
                self._drag_start_world_y = ent['y']
                return True
            # Try object
            obj, obj_type = self._find_object_at(world_x, world_y)
            if obj and obj_type:
                self.drag_target = obj
                self.drag_target_type = obj_type
                self.drag_offset_x = obj.x - world_x
                self.drag_offset_y = obj.y - world_y
                self.is_dragging = False
                # snapshot position at drag start for undo
                self._drag_start_world_x = obj.x
                self._drag_start_world_y = obj.y
                return True
            # Clicked empty space – deselect
            self.drag_target = None
            self.drag_target_type = None
            self.is_dragging = False
            return False

        # ── mouse motion while held: drag ─────────────────────────────────
        if event.type == pygame.MOUSEMOTION and self.drag_target is not None:
            if pygame.mouse.get_pressed()[0]:
                self.is_dragging = True
                new_x = world_x + self.drag_offset_x
                new_y = world_y + self.drag_offset_y
                if self.drag_target_type == 'entity':
                    self.drag_target['x'] = new_x
                    self.drag_target['y'] = new_y
                else:
                    self.drag_target.x = new_x
                    self.drag_target.y = new_y
                return True

        # ── left mouse up: end drag ───────────────────────────────────────
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.drag_target is not None and self.is_dragging:
                # Record the completed move for undo
                if self.drag_target_type == 'entity':
                    ent = self.drag_target
                    new_x, new_y = ent['x'], ent['y']
                    if (new_x, new_y) != (self._drag_start_world_x, self._drag_start_world_y):
                        self._push_undo(_HistoryEntry('entity_move', {
                            'instance_id': ent.get('instance_id', ''),
                            'old_x': self._drag_start_world_x,
                            'old_y': self._drag_start_world_y,
                            'new_x': new_x,
                            'new_y': new_y,
                        }))
                else:
                    obj = self.drag_target
                    new_x, new_y = obj.x, obj.y
                    if (new_x, new_y) != (self._drag_start_world_x, self._drag_start_world_y):
                        self._push_undo(_HistoryEntry('object_move', {
                            'obj':   obj,
                            'obj_type': self.drag_target_type,
                            'old_x': self._drag_start_world_x,
                            'old_y': self._drag_start_world_y,
                            'new_x': new_x,
                            'new_y': new_y,
                        }))
                self.is_dragging = False
                return True

        return False

    def _draw_drag_highlight(self, screen, camera_x, camera_y):
        """Draw a highlight border around the currently selected/dragged item."""
        if self.drag_target is None or not self._no_editor_active():
            return
        if self.drag_target_type == 'entity':
            ent = self.drag_target
            sw = ent['width'] * RENDER_SCALE
            sh = ent['height'] * RENDER_SCALE
            sx = int(ent['x'] * RENDER_SCALE - camera_x) - sw // 2
            sy = int(ent['y'] * RENDER_SCALE - camera_y) - sh // 2
            color = (255, 255, 0) if self.is_dragging else (255, 200, 0)
            pygame.draw.rect(screen, color, (sx - 2, sy - 2, sw + 4, sh + 4), 2)
        else:
            obj = self.drag_target
            if hasattr(obj, 'x') and hasattr(obj, 'width'):
                sx = int(obj.x * RENDER_SCALE - camera_x)
                sy = int(obj.y * RENDER_SCALE - camera_y)
                sw = int(getattr(obj, 'width', 32) * RENDER_SCALE)
                sh = int(getattr(obj, 'height', 32) * RENDER_SCALE)
                # Some objects are centred, others are top-left — use a generous outline
                pygame.draw.rect(screen, (255, 200, 0),
                                 (sx - sw // 2 - 2, sy - sh // 2 - 2, sw + 4, sh + 4), 2)

    # =========================================================================
    # Cache for loaded idle-down sprites keyed by (entity_id, variant_type)
    # =========================================================================

    _placed_sprite_cache = {}
    _shadow_surf_cache = {}
    _shadow_source = None  # loaded once on first use

    @staticmethod
    def _get_editor_shadow(width_world):
        """Return a cached shadow surface for the given world-unit width."""
        if RoomEditor._shadow_source is None:
            try:
                RoomEditor._shadow_source = pygame.image.load(
                    'assets/sprites/universal/shadow.png'
                ).convert_alpha()
            except Exception:
                s = pygame.Surface((32, 12), pygame.SRCALPHA)
                pygame.draw.ellipse(s, (0, 0, 0, 80), s.get_rect())
                RoomEditor._shadow_source = s

        key = width_world
        if key not in RoomEditor._shadow_surf_cache:
            src = RoomEditor._shadow_source
            target_w = max(8, int(width_world * RENDER_SCALE * 0.32))
            target_h = max(4, int(src.get_height() * target_w / src.get_width()))
            RoomEditor._shadow_surf_cache[key] = pygame.transform.scale(src, (target_w, target_h))
        return RoomEditor._shadow_surf_cache[key]

    @staticmethod
    def _load_placed_sprite(entity_id, variant_type, w, h):
        """Load and cache the idle-down first frame for a placed entity.

        Sprite sheets are assumed to be laid out with 4 rows (down/left/right/up)
        so row 0, column 0 gives the standing-down frame.  Falls back to None
        if the file doesn't exist — callers should handle that gracefully.
        """
        import os
        key = (entity_id, variant_type, w, h)
        if key in RoomEditor._placed_sprite_cache:
            return RoomEditor._placed_sprite_cache[key]
        base = f"assets/sprites/enemies/{entity_id}"
        # Try paths in priority order: variant-specific first, then generic fallbacks
        candidates = [
            # NPC paths
            f"assets/sprites/npc/{entity_id}/variants/{variant_type}/idle.png",
            f"assets/sprites/npc/{entity_id}/idle.png",
            # Enemy / boss paths
            f"{base}/variants/{variant_type}/idle.png",
            f"{base}/idle.png",
            f"assets/sprites/enemies/boss/{entity_id}/idle.png",
        ]
        path = next((p for p in candidates if os.path.exists(p)), None)
        sprite = None
        if path:
            try:
                sheet = pygame.image.load(path).convert_alpha()
                frame_h = sheet.get_height() // 4  # row 0 = down
                # Use the registered frame width (w) directly — avoids assuming
                # square frames (e.g. Pui Pui is 32×46).
                frame_w = w if 0 < w <= sheet.get_width() else frame_h
                frame = sheet.subsurface(pygame.Rect(0, 0, frame_w, frame_h))
                sprite = pygame.transform.scale(frame, (w * RENDER_SCALE, h * RENDER_SCALE))
            except Exception:
                sprite = None
        RoomEditor._placed_sprite_cache[key] = sprite
        return sprite

    def _draw_placed_entities(self, screen, camera_x, camera_y):
        """Render every NPC / enemy / boss that has been placed in the room
        using their idle-down sprite, falling back to coloured shapes.
        """
        if not self.viewing_room or not hasattr(self.viewing_room, 'entities'):
            return

        for ent in self.viewing_room.entities:
            # ── world → screen ──────────────────────────────────────────
            sx = int(ent['x'] * RENDER_SCALE - camera_x)
            sy = int(ent['y'] * RENDER_SCALE - camera_y)
            w = ent['width']
            h = ent['height']

            # cull anything fully off-screen
            if sx + w < 0 or sx - w > self.screen_width or \
                    sy + h < 0 or sy - h > self.screen_height:
                continue

            # ── try real sprite first ────────────────────────────────────
            entity_id = ent.get('id', '')
            entity_type = ent.get('entity_type', 'enemy')
            variant_type = ent.get('variant_type', 'default')
            sprite = self._load_placed_sprite(entity_id, variant_type, w, h)
            sw = w * RENDER_SCALE
            sh = h * RENDER_SCALE
            rect = pygame.Rect(sx - sw // 2, sy - sh // 2, sw, sh)

            if sprite:
                # Shadow beneath the sprite, matching draw_layers positioning
                shadow_w = ent.get('shadow_width', 32)
                shadow = self._get_editor_shadow(shadow_w)
                feet_x = sx
                hitbox_h = ent.get('hitbox_height', 32)
                feet_y = sy + int((hitbox_h * RENDER_SCALE) // 2.25) + ent.get('shadow_y_offset', 0)
                screen.blit(shadow, (feet_x - shadow.get_width() // 2,
                                     feet_y - shadow.get_height() // 2))
                screen.blit(sprite, rect)
            else:
                # ── fallback shape ──────────────────────────────────────
                color = ent.get('variant_color') or (128, 128, 128)
                # variant_color is stored as a list when it comes from JSON, so normalise it
                if isinstance(color, list):
                    color = tuple(color)
                dark = tuple(max(0, c - 60) for c in color)
                light = tuple(min(255, c + 50) for c in color)
                if entity_type == 'npc':
                    pygame.draw.rect(screen, color, rect, border_radius=6)
                    pygame.draw.rect(screen, dark, rect, 2, border_radius=6)
                    pygame.draw.circle(screen, light, rect.center, min(sw, sh) // 5)
                elif entity_type == 'enemy':
                    pygame.draw.rect(screen, color, rect)
                    pygame.draw.rect(screen, dark, rect, 2)
                    pad = 6
                    pygame.draw.line(screen, dark,
                                     (rect.x + pad, rect.y + pad),
                                     (rect.right - pad, rect.bottom - pad), 3)
                    pygame.draw.line(screen, dark,
                                     (rect.right - pad, rect.y + pad),
                                     (rect.x + pad, rect.bottom - pad), 3)
                elif entity_type == 'boss':
                    pygame.draw.rect(screen, color, rect)
                    pygame.draw.rect(screen, dark, rect, 3)
                    cx, cy = rect.center
                    r = min(sw, sh) // 4
                    pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
                    pygame.draw.polygon(screen, light, pts)
                    pygame.draw.polygon(screen, dark, pts, 2)

            # ── name + variant label above the sprite ───────────────────
            variant_name = ent.get('variant_name', '')
            label_text = ent.get('name', ent.get('id', '?'))
            if variant_name and variant_name != 'Default':
                label_text += f" [{variant_name}]"

            # Add AI type indicator for enemies and bosses
            if entity_type in ('enemy', 'boss'):
                ai_type = ent.get('ai_type', 'easy')
                if ai_type != 'easy':  # Only show if not default
                    label_text += f" (AI:{ai_type.capitalize()})"

            label_surf = self.font_small.render(label_text, True, self.colors['text'])
            screen.blit(label_surf,
                        label_surf.get_rect(centerx=rect.centerx, bottom=rect.top - 2))

    def update(self, dt, mouse_pos=None):
        """Update animations and camera"""
        if not self.active:
            return

        # Check if object editor wants to return to source room after spawn placement
        # Handle this in update, not input, so we don't consume input events
        if self.object_editor and hasattr(self.object_editor,
                                          'return_to_source_room') and self.object_editor.return_to_source_room:
            source_room_name = self.object_editor.return_to_source_room
            self.object_editor.return_to_source_room = None

            # Switch back to source room
            source_room = self.room_manager.get_room_by_name(source_room_name)
            if source_room:
                self.viewing_room = source_room
                self._sync_room_to_editor(source_room)

                # Ensure object editor stays active
                if self.object_editor:
                    self.object_editor.active = True
                    self.object_editor.placing_transition_spawn = False

        self.anim_timer += dt
        self.cursor_blink += dt

        # Smooth hover animations
        for i in range(len(self.hover_anim)):
            # Highlight both selected AND hovered items
            if i == self.selected_index or i == self.hover_index:
                self.hover_anim[i] = min(1.0, self.hover_anim[i] + dt * 5)
            else:
                self.hover_anim[i] = max(0.0, self.hover_anim[i] - dt * 5)

        # Update toolbar in room view
        if self.current_view == 'view_room':
            mouse_pos = mouse_pos if mouse_pos is not None else pygame.mouse.get_pos()
            self._logical_mouse_pos = mouse_pos  # cache for draw() hover checks
            self.toolbar.update(dt, mouse_pos)

            if self.object_editor and self.object_editor.active:
                self.object_editor.update(
                    dt,
                    mouse_pos,
                    int(self.camera.x),
                    int(self.camera.y)
                )

            if self.entity_editor and self.entity_editor.active:
                self.entity_editor.update(dt, mouse_pos)

        # Handle camera movement
        if self.current_view == 'view_room' and self.viewing_room:
            mouse_pos = getattr(self, '_logical_mouse_pos', None) or pygame.mouse.get_pos()
            mouse_over_palette = False

            # Check if mouse is hovering over an editor palette
            # Note: the tileset editor never binds WASD so we deliberately
            # exclude it here — camera movement should always work with WASD
            # regardless of where the cursor sits while that panel is open.
            if self.object_editor and self.object_editor.active:
                mouse_over_palette = self.object_editor._is_in_palette(mouse_pos[0], mouse_pos[1])
            elif self.entity_editor and self.entity_editor.active:
                mouse_over_palette = self.entity_editor._mouse_in_palette(mouse_pos[0], mouse_pos[1])

            # Block camera movement while any text input dialogue is open
            typing_active = (
                self.editing_field is not None or
                (self.entity_editor and self.entity_editor.active and
                 self.entity_editor._dialogue_popup is not None)
            )

            # Only move camera if not hovering over palette and not typing
            if not mouse_over_palette and not typing_active:
                keys = pygame.key.get_pressed()

                # Faster movement with shift held
                speed = self.camera_fast_speed if (
                        keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]) else self.camera_speed

                if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                    self.camera.x -= speed * dt
                if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                    self.camera.x += speed * dt
                if keys[pygame.K_UP] or keys[pygame.K_w]:
                    self.camera.y -= speed * dt
                if keys[pygame.K_DOWN] or keys[pygame.K_s]:
                    self.camera.y += speed * dt

            # Keep camera inside the room bounds (or centered if room is smaller than screen)
            if self.viewing_room.width * RENDER_SCALE <= self.screen_width:
                # Room is smaller than screen width - keep centered
                self.camera.x = (self.viewing_room.width * RENDER_SCALE - self.screen_width) // 2
            else:
                # Room is larger than screen - clamp to room bounds
                self.camera.x = max(0, min(self.camera.x, (self.viewing_room.width * RENDER_SCALE) - self.screen_width))

            if self.viewing_room.height * RENDER_SCALE <= self.screen_height:
                # Room is smaller than screen height - keep centered
                self.camera.y = (self.viewing_room.height * RENDER_SCALE - self.screen_height) // 2
            else:
                # Room is larger than screen - clamp to room bounds
                self.camera.y = max(0,
                                    min(self.camera.y, (self.viewing_room.height * RENDER_SCALE) - self.screen_height))

    def draw(self, screen):
        """Draw the current view"""
        if not self.active:
            return

        # Clear clickable rects at start of each frame
        self.clickable_rects = []

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

        # ── Zoom-to-fit overview ──────────────────────────────────────────────
        if self.zoom_active:
            self._render_zoom_overview(screen)
            # Draw toolbar and mouse coords on top, then return
            is_placing_spawn = (self.object_editor and
                                hasattr(self.object_editor, 'placing_transition_spawn') and
                                self.object_editor.placing_transition_spawn)
            is_editing_flying_pad_path = (self.object_editor and
                                          hasattr(self.object_editor, 'flying_pad_path_editor') and
                                          self.object_editor.flying_pad_path_editor.active)
            if not is_placing_spawn and not is_editing_flying_pad_path:
                self.toolbar.draw(screen)
            # Mouse coords (converted for zoom space)
            mouse_sx, mouse_sy = pygame.mouse.get_pos()
            world_x = int((mouse_sx - self._zoom_offset[0]) / (RENDER_SCALE * self._zoom_scale))
            world_y = int((mouse_sy - self._zoom_offset[1]) / (RENDER_SCALE * self._zoom_scale))
            coord_text = f"X: {world_x}  Y: {world_y}"
            coord_surf = self.font_medium.render(coord_text, True, self.colors['text'])
            coord_bg_w = coord_surf.get_width() + 16
            coord_bg_h = coord_surf.get_height() + 10
            coord_bg = pygame.Surface((coord_bg_w, coord_bg_h), pygame.SRCALPHA)
            coord_bg.fill((0, 0, 0, 160))
            margin = 10
            screen.blit(coord_bg, (margin, self.screen_height - coord_bg_h - margin))
            screen.blit(coord_surf, (margin + 8, self.screen_height - coord_bg_h - margin + 5))
            return

        screen.fill((34, 139, 34))

        # Background tiles — use baked surface if available (O(1) blit),
        # fall back to per-tile loop only when the callback isn't wired.
        if self.blit_tiles_callback:
            self.blit_tiles_callback(
                screen, self.viewing_room.name,
                int(self.camera.x), int(self.camera.y), True
            )
        elif self.tileset_editor:
            self.tileset_editor.draw_tiles(
                screen, int(self.camera.x), int(self.camera.y),
                self.viewing_room.name, layer='background'
            )

        # Draw the grid
        if self.tileset_editor and self.tileset_editor.active:
            if self.tileset_editor.show_grid:
                self.tileset_editor.draw_grid(
                    screen,
                    int(self.camera.x),
                    int(self.camera.y),
                    self.viewing_room.width,
                    self.viewing_room.height
                )
        elif self.object_editor and self.object_editor.active:
            if self.object_editor.show_grid:
                self._draw_default_grid(screen)
        elif self.entity_editor and self.entity_editor.active:
            if self.entity_editor.show_grid:
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

        # Draw destructible stones
        if hasattr(self.viewing_room, 'destructible_stones'):
            for stone in self.viewing_room.destructible_stones:
                if stone.active:
                    stone.draw(screen, self.camera, self.colors)

        # Draw placed entities (NPCs / enemies / bosses)
        self._draw_placed_entities(screen, int(self.camera.x), int(self.camera.y))

        # Foreground tiles — same baked path as background.
        if self.blit_tiles_callback:
            self.blit_tiles_callback(
                screen, self.viewing_room.name,
                int(self.camera.x), int(self.camera.y), False
            )
        elif self.tileset_editor:
            self.tileset_editor.draw_tiles(
                screen, int(self.camera.x), int(self.camera.y),
                self.viewing_room.name, layer='foreground'
            )

        # --- Editor overlays always drawn above ALL tile layers ---

        # Draw collision objects
        if self.object_editor:
            self.object_editor.current_room_name = self.viewing_room.name
            self.object_editor.draw_collision_objects(
                screen,
                int(self.camera.x),
                int(self.camera.y)
            )

        # Draw flying pads
        if self.object_editor:
            self.object_editor.draw_flying_pads(
                screen,
                int(self.camera.x),
                int(self.camera.y),
                self.colors
            )

        # Draw save points
        if self.object_editor:
            self.object_editor.draw_save_points(
                screen,
                int(self.camera.x),
                int(self.camera.y),
                self.colors
            )

        # Draw level gates
        if self.object_editor:
            self.object_editor.draw_level_gates(
                screen,
                int(self.camera.x),
                int(self.camera.y),
                self.colors
            )

        # Draw room transitions
        if self.object_editor:
            self.object_editor.draw_room_transitions(
                screen,
                int(self.camera.x),
                int(self.camera.y)
            )

        # Draw cutscene triggers
        if self.object_editor:
            self.object_editor.draw_cutscene_triggers(
                screen,
                int(self.camera.x),
                int(self.camera.y)
            )

        # Highlight selected/dragged item (no-panel mode)
        self._draw_drag_highlight(screen, int(self.camera.x), int(self.camera.y))

        # Editor previews
        if self.object_editor and self.object_editor.active:
            self.object_editor.draw_preview(
                screen,
                int(self.camera.x),
                int(self.camera.y)
            )

        if self.entity_editor and self.entity_editor.active:
            self.entity_editor.draw_preview(screen, int(self.camera.x), int(self.camera.y))

        if self.tileset_editor and self.tileset_editor.active:
            self.tileset_editor.draw_tile_preview(
                screen,
                int(self.camera.x),
                int(self.camera.y)
            )

        # Check if we're in transition spawn placement mode
        is_placing_spawn = (self.object_editor and
                            hasattr(self.object_editor, 'placing_transition_spawn') and
                            self.object_editor.placing_transition_spawn)

        # Check if flying pad path editor is active
        is_editing_flying_pad_path = (self.object_editor and
                                      hasattr(self.object_editor, 'flying_pad_path_editor') and
                                      self.object_editor.flying_pad_path_editor.active)

        # Hide toolbar and palettes during spawn placement or flying pad path editing
        if not is_placing_spawn and not is_editing_flying_pad_path:
            # Toolbar and palettes on top of everything
            self.toolbar.draw(screen)

            if self.tileset_editor and self.tileset_editor.active:
                self.tileset_editor.draw_palette(screen)

            if self.object_editor and self.object_editor.active:
                self.object_editor.draw_palette(screen)

            if self.entity_editor and self.entity_editor.active:
                self.entity_editor.draw(screen)

        # ── Mouse coordinates overlay (bottom-left) ───────────────────────────
        mouse_sx, mouse_sy = pygame.mouse.get_pos()
        world_x = int((mouse_sx + self.camera.x) / RENDER_SCALE)
        world_y = int((mouse_sy + self.camera.y) / RENDER_SCALE)
        coord_text = f"X: {world_x}  Y: {world_y}"
        coord_surf = self.font_medium.render(coord_text, True, self.colors['text'])
        coord_bg_w = coord_surf.get_width() + 16
        coord_bg_h = coord_surf.get_height() + 10
        coord_bg = pygame.Surface((coord_bg_w, coord_bg_h), pygame.SRCALPHA)
        coord_bg.fill((0, 0, 0, 160))
        margin = 10
        screen.blit(coord_bg, (margin, self.screen_height - coord_bg_h - margin))
        screen.blit(coord_surf, (margin + 8, self.screen_height - coord_bg_h - margin + 5))

    def _render_zoom_overview(self, screen):
        """Render the entire room scaled to fit the screen for the zoom-out view.
        The scaled surface is cached and only rebuilt when _zoom_dirty is True."""
        room = self.viewing_room

        if not self._zoom_dirty and self._zoom_cache is not None:
            # Fast path — just blit the pre-built cache
            screen.fill((15, 15, 25))
            screen.blit(self._zoom_cache, self._zoom_offset)
            return

        # ── Build the cache ───────────────────────────────────────────────────
        room_pw = room.width * RENDER_SCALE
        room_ph = room.height * RENDER_SCALE

        # Offscreen surface covering the full room in pixels
        zoom_surf = pygame.Surface((room_pw, room_ph))
        zoom_surf.fill((34, 139, 34))

        # Temporarily widen the viewport on sub-editors so their tile-culling
        # covers the whole room rather than just screen_width × screen_height.
        sub_editors = [e for e in [self.tileset_editor, self.object_editor, self.entity_editor] if e]
        orig_dims = {}
        for ed in sub_editors:
            orig_dims[id(ed)] = (
                getattr(ed, 'screen_width',  None),
                getattr(ed, 'screen_height', None),
            )
            if hasattr(ed, 'screen_width'):  ed.screen_width  = room_pw
            if hasattr(ed, 'screen_height'): ed.screen_height = room_ph

        orig_sw, orig_sh = self.screen_width, self.screen_height
        self.screen_width  = room_pw
        self.screen_height = room_ph

        cam_x, cam_y = 0, 0
        room_name = room.name

        if self.tileset_editor:
            self.tileset_editor.draw_tiles(zoom_surf, cam_x, cam_y, room_name, layer='background')

        pygame.draw.rect(zoom_surf, self.colors['accent'], (0, 0, room_pw, room_ph), 3)

        if self.object_editor:
            self.object_editor.draw_spawn_points(zoom_surf, cam_x, cam_y)

        if hasattr(room, 'destructible_stones'):
            for stone in room.destructible_stones:
                if stone.active:
                    stone.draw(zoom_surf, type('_Cam', (), {'x': 0, 'y': 0})(), self.colors)

        self._draw_placed_entities(zoom_surf, cam_x, cam_y)

        if self.tileset_editor:
            self.tileset_editor.draw_tiles(zoom_surf, cam_x, cam_y, room_name, layer='foreground')

        if self.object_editor:
            self.object_editor.current_room_name = room_name
            self.object_editor.draw_collision_objects(zoom_surf, cam_x, cam_y)
            self.object_editor.draw_flying_pads(zoom_surf, cam_x, cam_y, self.colors)
            self.object_editor.draw_save_points(zoom_surf, cam_x, cam_y, self.colors)
            self.object_editor.draw_level_gates(zoom_surf, cam_x, cam_y, self.colors)
            self.object_editor.draw_room_transitions(zoom_surf, cam_x, cam_y)

        # Restore sub-editor and self dimensions
        for ed in sub_editors:
            sw, sh = orig_dims[id(ed)]
            if sw is not None and hasattr(ed, 'screen_width'):  ed.screen_width  = sw
            if sh is not None and hasattr(ed, 'screen_height'): ed.screen_height = sh
        self.screen_width  = orig_sw
        self.screen_height = orig_sh

        # Scale to fit screen (scale is faster than smoothscale for large surfaces)
        fit_scale = min(orig_sw / room_pw, orig_sh / room_ph)
        scaled_w = max(1, int(room_pw * fit_scale))
        scaled_h = max(1, int(room_ph * fit_scale))
        self._zoom_cache  = pygame.transform.scale(zoom_surf, (scaled_w, scaled_h))
        ox = (orig_sw - scaled_w) // 2
        oy = (orig_sh - scaled_h) // 2
        self._zoom_offset = (ox, oy)
        self._zoom_scale  = fit_scale
        self._zoom_dirty  = False

        # Blit the freshly built cache
        screen.fill((15, 15, 25))
        screen.blit(self._zoom_cache, self._zoom_offset)

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
        """Draw the menu background with gradient and animated grid.

        The gradient is drawn one horizontal line at a time which is fine for
        the menu screen — it only runs when we're NOT in view_room mode.
        """
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
            is_hovered = (i == self.hover_index)
            item_rect = self._draw_group_item(screen, group_name, content_x, y_pos, content_width,
                                              is_selected or is_hovered, i)
            self.clickable_rects.append({'rect': item_rect, 'index': i, 'type': 'item'})
            y_pos += self.item_height + 10

        buttons = [
            ("+ Create New Group", self.colors['success']),
            ("< Back to Menu", self.colors['text_dim'])
        ]

        for j, (label, color) in enumerate(buttons):
            i = len(self.room_manager.groups) + j
            is_selected = (i == self.selected_index)
            is_hovered = (i == self.hover_index)
            btn_rect = self._draw_button(screen, label, content_x, y_pos, content_width, is_selected or is_hovered,
                                         color, i)
            self.clickable_rects.append({'rect': btn_rect, 'index': i, 'type': 'item'})
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
            is_hovered = (i == self.hover_index)
            item_rect = self._draw_room_item(screen, room, content_x, y_pos, content_width, is_selected or is_hovered,
                                             i)
            self.clickable_rects.append({'rect': item_rect, 'index': i, 'type': 'item'})
            y_pos += self.item_height + 10

        buttons = [
            ("+ Create New Room", self.colors['success']),
            ("< Back to Groups", self.colors['text_dim'])
        ]

        for j, (label, color) in enumerate(buttons):
            i = len(rooms_in_group) + j
            is_selected = (i == self.selected_index)
            is_hovered = (i == self.hover_index)
            btn_rect = self._draw_button(screen, label, content_x, y_pos, content_width, is_selected or is_hovered,
                                         color, i)
            self.clickable_rects.append({'rect': btn_rect, 'index': i, 'type': 'item'})
            y_pos += self.item_height + 10

    def _draw_room_item(self, screen, room, x, y, width, selected, index):
        """Draw a single room in the list with View (👁) and Settings (⚙) buttons."""
        # Reserve space on the right for the two buttons
        btn_w = 44
        btn_h = 36
        btn_gap = 8
        buttons_total = (btn_w + btn_gap) * 2
        row_width = width - buttons_total - self.padding

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

        # Group colour circle
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

        # "CURRENT" indicator
        if self.room_manager.current_room == room:
            indicator = self.font_small.render("* CURRENT", True, self.colors['success'])
            screen.blit(indicator, (x + row_width - 90, y + 20))

        # ── View button (👁) ────────────────────────────────────────────────
        btn_right_edge = x + width - self.padding
        settings_btn_rect = pygame.Rect(btn_right_edge - btn_w, y + (self.item_height - btn_h) // 2, btn_w, btn_h)
        view_btn_rect     = pygame.Rect(settings_btn_rect.x - btn_gap - btn_w,
                                        y + (self.item_height - btn_h) // 2, btn_w, btn_h)

        _lm = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())
        view_hovered     = view_btn_rect.collidepoint(_lm)
        settings_hovered = settings_btn_rect.collidepoint(_lm)

        # View button
        view_bg = (60, 120, 200) if view_hovered else (40, 80, 140)
        pygame.draw.rect(screen, view_bg, view_btn_rect, border_radius=6)
        pygame.draw.rect(screen, (100, 160, 255) if view_hovered else (70, 120, 200),
                         view_btn_rect, 2, border_radius=6)
        if self._view_icon:
            screen.blit(self._view_icon, self._view_icon.get_rect(center=view_btn_rect.center))
        else:
            eye_surf = self.font_large.render("V", True, self.colors['text'])
            screen.blit(eye_surf, eye_surf.get_rect(center=view_btn_rect.center))

        # Settings button
        settings_bg = (80, 60, 160) if settings_hovered else (50, 40, 110)
        pygame.draw.rect(screen, settings_bg, settings_btn_rect, border_radius=6)
        pygame.draw.rect(screen, (140, 100, 255) if settings_hovered else (100, 70, 200),
                         settings_btn_rect, 2, border_radius=6)
        gear_surf = self.font_large.render("S", True, self.colors['text'])
        screen.blit(gear_surf, gear_surf.get_rect(center=settings_btn_rect.center))

        # Register the two action buttons as separate clickable entries
        self.clickable_rects.append({'rect': view_btn_rect,     'index': index, 'type': 'view_room'})
        self.clickable_rects.append({'rect': settings_btn_rect, 'index': index, 'type': 'edit_room'})

        return panel_rect

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
            is_hovered = (i == self.hover_index)

            label = field_name.replace('_', ' ').title()
            label_surf = self.font_medium.render(label, True, self.colors['text_dim'])
            screen.blit(label_surf, (content_x, y_pos))
            y_pos += 30

            field_rect = pygame.Rect(content_x, y_pos, field_width, 40)
            bg_color = self.colors['panel_light'] if (is_selected or is_hovered) else self.colors['panel']
            border_color = self.colors['accent'] if (is_selected or is_hovered) else self.colors['grid']

            pygame.draw.rect(screen, bg_color, field_rect, border_radius=5)
            pygame.draw.rect(screen, border_color, field_rect, 2, border_radius=5)

            self.clickable_rects.append({'rect': field_rect, 'index': i, 'type': 'item'})

            if field_name == 'group':
                value = self.create_form[field_name]
                hint = " (CLICK to cycle)"
            else:
                value = self.create_form[field_name] if field_name in self.create_form else ""
                hint = " (CLICK to edit)" if (is_selected or is_hovered) else ""

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
            btn_index = self.create_form_fields.index(btn_id)
            is_selected = (self.selected_index == btn_index)
            is_hovered = (self.hover_index == btn_index)

            btn_rect = pygame.Rect(content_x + j * 260, y_pos, 250, 50)
            bg_color = btn_color if (is_selected or is_hovered) else self.colors['panel']

            if is_selected or is_hovered:
                glow_alpha = int(50 + 30 * math.sin(self.anim_timer * 3))
                glow_surf = pygame.Surface((260, 60), pygame.SRCALPHA)
                pygame.draw.rect(glow_surf, (*btn_color, glow_alpha), (0, 0, 260, 60), border_radius=8)
                screen.blit(glow_surf, (content_x + j * 260 - 5, y_pos - 5))

            pygame.draw.rect(screen, bg_color, btn_rect, border_radius=8)
            pygame.draw.rect(screen, btn_color, btn_rect, 2, border_radius=8)

            self.clickable_rects.append({'rect': btn_rect, 'index': btn_index, 'type': 'item'})

            text_color = self.colors['panel'] if (is_selected or is_hovered) else btn_color
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
            is_hovered = (i == self.hover_index)

            label_surf = self.font_medium.render(label, True, self.colors['text_dim'])
            screen.blit(label_surf, (content_x, y_pos))
            y_pos += 30

            field_rect = pygame.Rect(content_x, y_pos, field_width, 40)
            bg_color = self.colors['panel_light'] if (is_selected or is_hovered) else self.colors['panel']
            border_color = self.colors['accent'] if (is_selected or is_hovered) else self.colors['grid']

            pygame.draw.rect(screen, bg_color, field_rect, border_radius=5)
            pygame.draw.rect(screen, border_color, field_rect, 2, border_radius=5)

            self.clickable_rects.append({'rect': field_rect, 'index': i, 'type': 'item'})

            hint = " (CLICK to cycle)" if field_id == 'group' else " (CLICK to edit)"
            value_text = value + (hint if (is_selected or is_hovered) else "")
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
            is_hovered = (self.hover_index == btn_index)

            btn_rect = pygame.Rect(content_x + j * 180, y_pos, 170, 50)
            bg_color = btn_color if (is_selected or is_hovered) else self.colors['panel']

            if is_selected or is_hovered:
                glow_alpha = int(50 + 30 * math.sin(self.anim_timer * 3))
                glow_surf = pygame.Surface((180, 60), pygame.SRCALPHA)
                pygame.draw.rect(glow_surf, (*btn_color, glow_alpha), (0, 0, 180, 60), border_radius=8)
                screen.blit(glow_surf, (content_x + j * 180 - 5, y_pos - 5))

            pygame.draw.rect(screen, bg_color, btn_rect, border_radius=8)
            pygame.draw.rect(screen, btn_color, btn_rect, 2, border_radius=8)

            self.clickable_rects.append({'rect': btn_rect, 'index': btn_index, 'type': 'item'})

            text_color = self.colors['panel'] if (is_selected or is_hovered) else btn_color
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
        """Convert a hue angle (0–360) to an RGB tuple.

        This is a simplified HSV→RGB conversion with fixed saturation (0.7)
        and value (0.9) so group icons always look vivid without needing a
        full colorsys import.  The 'c', 'x', 'm' variables follow the standard
        HSV chroma/intermediate/match formulas.
        """
        h = hue / 60.0
        c = 0.7 * 0.9  # chroma = saturation * value
        x = c * (1 - abs(h % 2 - 1))
        m = 0.9 - c    # value minus chroma gives the minimum RGB component

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

    def _draw_button(self, screen, label, x, y, width, selected, color, index):
        """Draw a button in the menu - returns the clickable rect"""
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

        return panel_rect

    def _draw_group_item(self, screen, group_name, x, y, width, selected, index):
        """Draw a single group in the list - returns the clickable rect"""
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
            hint = self.font_small.render("DELETE to remove | Double-Click to open", True, self.colors['accent'])
            screen.blit(hint, (x + width - 360, y + 20))
        elif selected:
            hint = self.font_small.render("Double-Click to open", True, self.colors['accent'])
            screen.blit(hint, (x + width - 190, y + 20))

        return panel_rect