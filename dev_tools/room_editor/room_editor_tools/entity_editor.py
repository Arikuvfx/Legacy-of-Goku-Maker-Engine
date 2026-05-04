import pygame
import pygame.gfxdraw


class EntityEditor:
    """
    Editor for placing NPCs and Enemies in a room.

    Layout mirrors ObjectEditor exactly:
        - Right-side palette panel (same width, position, padding).
        - Category tabs stacked vertically: NPCs | Enemies | Enemy Bosses.
        - Scrollable item grid (3 columns, 80 px tiles).
        - Variant selector strip above the settings footer.
        - Settings / instructions panel at the very bottom.

    Entity data structure (mirrors object_editor item dicts):
        {
            'id':              str   – unique key used for placement & saving
            'name':            str   – display label
            'sprite':          Surface | None
            'width':           int
            'height':          int
            'entity_type':     str   – 'npc' | 'enemy' | 'boss'
            'has_variants':    bool
            'variants':        list[dict]   # each: {'type', 'name', 'color'}
            'default_variant': str   – variant['type'] that is selected on first open
        }
    """

    # ---------------------------------------------------------------------------
    # Colour palette – identical tokens as ObjectEditor so the two panels look
    # like siblings on screen.
    # ---------------------------------------------------------------------------
    COLORS = {
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
        'danger':  (255, 100, 100),
        'disabled': (100, 100, 100),
        'variant_bg': (25, 25, 40),
        'variant_selected': (50, 150, 255),
        # Entity-category accent colours (used for preview sprites)
        'npc_color': (50, 150, 200),
        'enemy_color': (220, 60, 60),
        'boss_color': (180, 50, 180),
    }

    # ── Mission objective metadata (mirrors mission_manager constants) ────────
    OBJECTIVE_TYPES = ['kill', 'reach_room', 'bring_item', 'collect_item', 'talk_to_npc']
    OBJECTIVE_DEFAULTS = {
        'kill':         {'enemy_id': '', 'count': 1, 'room': ''},
        'reach_room':   {'room_name': ''},
        'bring_item':   {'item_id': '', 'count': 1},
        'collect_item': {'item_id': '', 'count': 1},
        'talk_to_npc':  {'npc_instance_id': ''},
    }
    # (param_key, short_label, pixel_width)
    OBJECTIVE_PARAM_FIELDS = {
        'kill':         [('enemy_id', 'Enemy', 120), ('count', '#', 38), ('room', 'Room', 110)],
        'reach_room':   [('room_name', 'Room Name', 220)],
        'bring_item':   [('item_id', 'Item ID', 160), ('count', '#', 38)],
        'collect_item': [('item_id', 'Item ID', 160), ('count', '#', 38)],
        'talk_to_npc':  [('npc_instance_id', 'NPC Instance ID', 260)],
    }

    # Param keys that become dropdown/cycle widgets instead of free-text fields.
    # Values are populated at runtime from room_manager and the entity catalogue.
    DROPDOWN_PARAMS = {'enemy_id', 'room', 'room_name', 'npc_instance_id'}

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False

        self.font_small = pygame.font.Font(None, 16)
        self.font_medium = pygame.font.Font(None, 20)
        self.font_large = pygame.font.Font(None, 24)

        self.palette_width = 600
        self.palette_x = screen_width - self.palette_width
        self.palette_y = 100  # below the toolbar
        self.palette_height = 940
        self.palette_padding = 10
        self.item_size = 80
        self.items_per_row = 3
        self.scroll_offset = 0

        # Tab order also controls top-to-bottom render order
        self.category_keys = ['NPCs', 'Enemies', 'Enemy Bosses']

        # Populated by _build_entity_catalogue(); structure: {category: [entity_dict, ...]}
        self.categories: dict[str, list[dict]] = {k: [] for k in self.category_keys}
        self._build_entity_catalogue()

        self.current_category = self.category_keys[0]
        self.selected_entity = None
        self.selected_variant = None
        self.hover_entity = None
        self.hover_variant_idx = -1

        self.preview_x = 0
        self.preview_y = 0

        self.grid_snap = True
        self.show_grid = True

        # ── AI / NPC settings ────────────────────────────────────────────────
        self.ai_types = ['easy', 'advanced']
        self.selected_ai_type = 'easy'
        self.hover_ai_type_idx = -1

        self.npc_modes    = ['static', 'moving']
        self.npc_facings  = ['down', 'up', 'left', 'right']
        self.selected_npc_mode   = 'static'
        self.selected_npc_facing = 'down'
        self.hover_npc_mode_idx    = -1
        self.hover_npc_facing_idx  = -1

        # ── NPC dialogue popup ───────────────────────────────────────────────
        self._dialogue_popup = None
        self._dialogue_popup_rects = []   # rebuilt each frame in draw()
        self._popup_only_mode = False     # editing an existing NPC; palette stays hidden

        # ── Hit-rect cache ───────────────────────────────────────────────────
        self.ui_rects: dict[str, list] = {
            'category_rects': [],
            'entity_rects': [],
            'variant_rects': [],
            'ai_type_rects': [],
            'npc_mode_rects': [],
            'npc_facing_rects': [],
            'npc_dialogue_rects': [],
        }

        self.category_hover_anim = {k: 0.0 for k in self.category_keys}

        # Signature: on_entity_placed(entity_dict, variant|None, ai_type, world_x, world_y)
        self.on_entity_placed = None

        # Populated from game._assign_obstacles: collisions, stones, gates, transitions
        self.placement_obstacles = []

        # Set by room_editor after construction; needed for room/NPC dropdowns in the mission panel
        self.room_manager = None

        # None when closed; dict with anchor/scroll state when open
        self._open_dropdown = None

        # Panel show/hide toggle (same pattern as EditorToolbar)
        self.palette_visible = True
        self._panel_tab_w = 18
        self._panel_tab_h = 72
        self._hover_panel_toggle = False

    # =========================================================================
    # Entity catalogue
    # =========================================================================

    def _build_entity_catalogue(self):
        """
        Populate self.categories with the master list of placeable entities.

        Adding a new entity in the future is a single-dict append.  Variants
        are just colour definitions for now; swap in sprite paths later the
        same way ObjectEditor does for stones / gates.
        """

        # ── NPCs ─────────────────────────────────────────────────────────────
        import os
        npc_variants = [{'type': 'default', 'name': 'Default', 'color': (50, 150, 200)}]
        variants_dir = 'assets/sprites/npc/generic/variants'
        if os.path.isdir(variants_dir):
            for v in sorted(os.listdir(variants_dir)):
                if os.path.isdir(os.path.join(variants_dir, v)) and v != 'default':
                    npc_variants.append({'type': v, 'name': v.replace('_', ' ').title(), 'color': (50, 150, 200)})

        self.categories['NPCs'] = [
            {
                'id': 'generic',
                'name': 'Generic NPC',
                'sprite': None,
                'width': 32, 'height': 32,
                'entity_type': 'npc',
                'has_variants': True,
                'variants': npc_variants,
                'default_variant': 'default',
            },
        ]

        # ── Normal Enemies ───────────────────────────────────────────────────
        self.categories['Enemies'] = [
            {
                'id': 'tiger_bandit',
                'name': 'Tiger_bandit',
                'sprite': None,
                'width': 32, 'height': 32,
                'entity_type': 'enemy',
                'enemy_category': 'melee',  # Mark as melee type
                'has_variants': True,
                'variants': [
                    {'type': 'default', 'name': 'Default', 'color': (220, 60, 60)},
                ],
                'default_variant': 'default',
            },
            {
                'id': 'shooter',
                'name': 'Shooter (Ranged)',
                'sprite': None,
                'width': 32, 'height': 32,
                'entity_type': 'enemy',
                'enemy_category': 'shooter',  # Mark as shooter type
                'has_variants': True,
                'variants': [
                    {'type': 'default', 'name': 'Thrower', 'color': (100, 120, 220)},
                    {'type': 'gunner', 'name': 'Gunner', 'color': (180, 80, 80)},
                    {'type': 'rocketlauncher', 'name': 'Rocket Launcher', 'color': (200, 120, 40)},
                ],
                'default_variant': 'default',
            },
        ]

        # ── Boss Enemies ─────────────────────────────────────────────────────
        self.categories['Enemy Bosses'] = [
            {
                'id': 'pui_pui',
                'name': 'Pui Pui',
                'sprite': None,
                'width': 64, 'height': 64,
                'entity_type': 'boss',
                'enemy_category': 'melee',
                'has_variants': True,
                'variants': [
                    {'type': 'default', 'name': 'Default', 'color': (180, 50, 180)},
                ],
                'default_variant': 'default',
            },
            {
                'id': 'android_17',
                'name': 'Android 17',
                'sprite': None,
                'width': 32, 'height': 32,
                'entity_type': 'boss',
                'enemy_category': 'shooter',
                'has_variants': True,
                'variants': [
                    {'type': 'default', 'name': 'Default', 'color': (50, 180, 220)},
                ],
                'default_variant': 'default',
            },
            {
                'id': 'android_18',
                'name': 'Android 18',
                'sprite': None,
                'width': 32, 'height': 32,
                'entity_type': 'boss',
                'enemy_category': 'shooter',
                'has_variants': True,
                'variants': [
                    {'type': 'default', 'name': 'Default', 'color': (220, 180, 50)},
                ],
                'default_variant': 'default',
            },
        ]

        # generate placeholder sprites after catalogue is built
        self._generate_sprites()

    # =========================================================================
    # Sprite generation  (swap real assets in here later)
    # =========================================================================

    def _generate_sprites(self):
        """
        For every entity (and every one of its variants) load the idle-down
        frame from the real spritesheet, falling back to the placeholder shape
        if the asset is not yet available.
        """
        for cat_key, entities in self.categories.items():
            for entity in entities:
                variants = entity.get('variants', [])
                default_type = entity.get('default_variant')
                entity_id = entity.get('id', '')

                for variant in variants:
                    sprite = self._load_idle_down_sprite(
                        entity_id, variant['type'],
                        entity['width'], entity['height']
                    )
                    if sprite is None:
                        sprite = self._make_entity_sprite(
                            entity['width'], entity['height'],
                            variant['color'], entity['entity_type']
                        )
                    variant['sprite'] = sprite

                # point main sprite at default variant
                if variants:
                    for v in variants:
                        if v['type'] == default_type:
                            entity['sprite'] = v['sprite'].copy()
                            break
                    else:
                        entity['sprite'] = variants[0]['sprite'].copy()

    @staticmethod
    def _load_idle_down_sprite(entity_id, variant_type, w, h):
        """
        Try to load the first frame of the idle-down row from the entity's
        spritesheet.  Checks NPC paths first, then enemy/boss paths.
        Returns a Surface scaled to (w, h), or None if the asset is missing.
        """
        import os
        candidates = [
            # NPC paths
            f"assets/sprites/npc/{entity_id}/variants/{variant_type}/idle.png",
            f"assets/sprites/npc/{entity_id}/idle.png",
            # Enemy / boss paths
            f"assets/sprites/enemies/{entity_id}/variants/{variant_type}/idle.png",
            f"assets/sprites/enemies/{entity_id}/idle.png",
            f"assets/sprites/enemies/boss/{entity_id}/idle.png",
        ]
        path = next((p for p in candidates if os.path.exists(p)), None)
        if path is None:
            return None
        try:
            sheet = pygame.image.load(path).convert_alpha()
            sheet_w = sheet.get_width()
            sheet_h = sheet.get_height()
            num_rows = 4
            frame_h = sheet_h // num_rows
            frame_w = w if 0 < w <= sheet_w else frame_h
            frame = sheet.subsurface(pygame.Rect(0, 0, frame_w, frame_h))
            return pygame.transform.scale(frame, (w, h))
        except Exception:
            return None

    @staticmethod
    def _make_entity_sprite(w, h, color, entity_type):
        """
        Draw a distinguishable placeholder for each entity category:
            NPC      – rounded rect + small dot (friendly feel)
            Enemy    – sharp rect with an X mark
            Boss     – larger rect with a star / diamond accent
        """
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        dark = tuple(max(0, c - 60) for c in color)
        light = tuple(min(255, c + 50) for c in color)

        if entity_type == 'npc':
            pygame.draw.rect(surf, color, (0, 0, w, h), border_radius=6)
            pygame.draw.rect(surf, dark, (0, 0, w, h), 2, border_radius=6)
            # little friendly circle in centre
            pygame.draw.circle(surf, light, (w // 2, h // 2), min(w, h) // 5)

        elif entity_type == 'enemy':
            pygame.draw.rect(surf, color, (0, 0, w, h))
            pygame.draw.rect(surf, dark, (0, 0, w, h), 2)
            # X mark
            pad = 6
            pygame.draw.line(surf, dark, (pad, pad), (w - pad, h - pad), 3)
            pygame.draw.line(surf, dark, (w - pad, pad), (pad, h - pad), 3)

        elif entity_type == 'boss':
            pygame.draw.rect(surf, color, (0, 0, w, h))
            pygame.draw.rect(surf, dark, (0, 0, w, h), 3)
            # diamond in centre
            cx, cy = w // 2, h // 2
            r = min(w, h) // 4
            pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
            pygame.draw.polygon(surf, light, pts)
            pygame.draw.polygon(surf, dark, pts, 2)

        return surf

    # =========================================================================
    # Public API  (called by the room editor / toolbar)
    # =========================================================================

    def toggle(self):
        """Open or close the entity editor (mirrors ObjectEditor.toggle)."""
        self.active = not self.active
        if self.active:
            self.selected_entity = None
            self.selected_variant = None
            self.scroll_offset = 0

    # -------------------------------------------------------------------------
    # Panel show/hide tab
    # -------------------------------------------------------------------------

    def _panel_toggle_rect(self):
        """Return the rect for the ◀/▶ tab that straddles the panel's left edge."""
        tx = (self.palette_x - self._panel_tab_w) if self.palette_visible else (self.screen_width - self._panel_tab_w)
        ty = self.palette_y + 150
        return pygame.Rect(tx, ty, self._panel_tab_w, self._panel_tab_h)

    def _draw_panel_toggle_tab(self, screen):
        """Render the small ◀/▶ tab — always visible so the panel can be recalled."""
        rect   = self._panel_toggle_rect()
        bg     = self.COLORS['panel_light'] if self._hover_panel_toggle else self.COLORS['panel']
        border = self.COLORS['accent']      if self._hover_panel_toggle else self.COLORS['grid']
        pygame.draw.rect(screen, bg,     rect, border_radius=6)
        pygame.draw.rect(screen, border, rect, 1, border_radius=6)
        arrow = '◀' if self.palette_visible else '▶'
        label = self.font_small.render(
            arrow, True,
            self.COLORS['accent'] if self._hover_panel_toggle else self.COLORS['text_dim']
        )
        screen.blit(label, label.get_rect(center=rect.center))

    def set_current_category(self, key):
        """Programmatically switch category (e.g. from a hotkey)."""
        if key in self.categories:
            self.current_category = key
            self.scroll_offset = 0

    # =========================================================================
    # Update
    # =========================================================================

    def update(self, dt, mouse_pos):
        """Per-frame hover animation + scroll clamping."""
        if not self.active:
            return

        mx, my = mouse_pos

        # animate category tab hover weights
        for key in self.category_keys:
            rects = [r for r in self.ui_rects['category_rects'] if r['key'] == key]
            hovering = any(r['rect'].collidepoint(mx, my) for r in rects)
            if hovering:
                self.category_hover_anim[key] = min(1.0, self.category_hover_anim[key] + dt * 8)
            else:
                self.category_hover_anim[key] = max(0.0, self.category_hover_anim[key] - dt * 8)

        # entity hover
        self.hover_entity = None
        for entry in self.ui_rects.get('entity_rects', []):
            if entry['rect'].collidepoint(mx, my):
                self.hover_entity = entry['entity']
                break

        # variant hover
        self.hover_variant_idx = -1
        for i, entry in enumerate(self.ui_rects.get('variant_rects', [])):
            if entry['rect'].collidepoint(mx, my):
                self.hover_variant_idx = i
                break

        # AI type hover
        self.hover_ai_type_idx = -1
        for entry in self.ui_rects.get('ai_type_rects', []):
            if entry['rect'].collidepoint(mx, my):
                self.hover_ai_type_idx = entry['index']
                break

        # NPC mode hover
        self.hover_npc_mode_idx = -1
        for entry in self.ui_rects.get('npc_mode_rects', []):
            if entry['rect'].collidepoint(mx, my):
                self.hover_npc_mode_idx = entry['index']
                break

        # NPC facing hover
        self.hover_npc_facing_idx = -1
        for entry in self.ui_rects.get('npc_facing_rects', []):
            if entry['rect'].collidepoint(mx, my):
                self.hover_npc_facing_idx = entry['index']
                break

    # =========================================================================
    # Events
    # =========================================================================

    def handle_event(self, event, camera_x, camera_y):
        """
        Process pygame events.  Returns True if the event was consumed.

        Scroll wheel scrolls the item grid.  Left-click selects categories,
        entities, and variants.  Right-click in the world deletes placed
        entities (handled upstream; this editor does not own placed objects).
        """
        if not self.active:
            return False

        if event.type == pygame.MOUSEWHEEL:
            if self._mouse_in_palette(*pygame.mouse.get_pos()):
                self.scroll_offset -= event.y * 30
                self.scroll_offset = max(0, self.scroll_offset)
                return True
            # Scroll open dropdown list
            if self._open_dropdown is not None:
                dd = self._open_dropdown
                max_scroll = max(0, len(dd['options']) - min(10, len(dd['options'])))
                dd['scroll'] = max(0, min(dd['scroll'] - event.y, max_scroll))
                return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mouse_pos = event.pos

            # Panel show/hide toggle — checked first so it always fires
            if self._panel_toggle_rect().collidepoint(mouse_pos):
                self.palette_visible = not self.palette_visible
                return True

            # ── Dialogue popup mouse clicks (highest priority) ─────────────
            if self._dialogue_popup is not None:
                # If a dropdown is open, check its item rects first (appended last)
                if self._open_dropdown is not None:
                    for entry in reversed(self._dialogue_popup_rects):
                        if entry.get('action') == 'obj_dropdown_select':
                            if entry['rect'].collidepoint(mouse_pos):
                                dd      = self._open_dropdown
                                opt     = dd['options'][entry['opt_idx']]
                                objs    = self._dialogue_popup['mission']['objectives']
                                idx     = dd['obj_idx']
                                pk      = dd['param']
                                if 0 <= idx < len(objs):
                                    if pk == 'npc_instance_id':
                                        objs[idx]['params'][pk] = self._npc_entry_to_id(opt)
                                    else:
                                        objs[idx]['params'][pk] = opt
                                self._open_dropdown = None
                                return True
                    # Click outside dropdown list → close it
                    self._open_dropdown = None
                    return True

                for entry in self._dialogue_popup_rects:
                    if entry['rect'].collidepoint(mouse_pos):
                        action = entry['action']
                        p = self._dialogue_popup
                        if action == 'switch_tab':
                            p['active_tab'] = entry['tab']
                            p['mission_active_field'] = None
                        elif action == 'select_line':
                            p['active_index'] = entry['index']
                        elif action == 'add':
                            p['dialogues'].append('')
                            p['active_index'] = len(p['dialogues']) - 1
                        elif action == 'remove':
                            if len(p['dialogues']) > 1:
                                p['dialogues'].pop(p['active_index'])
                                p['active_index'] = min(p['active_index'], len(p['dialogues']) - 1)
                        elif action == 'confirm':
                            self._confirm_dialogue_popup()
                        elif action == 'cancel':
                            self._dialogue_popup = None
                            if self._popup_only_mode:
                                self.active = False
                                self._popup_only_mode = False
                        # ── Mission tab actions ────────────────────────────
                        elif action == 'toggle_mission':
                            p['mission_enabled'] = not p.get('mission_enabled', False)
                        elif action == 'toggle_sequential':
                            p['mission']['sequential'] = not p['mission'].get('sequential', False)
                        elif action == 'toggle_quest_type':
                            types = ['main', 'side', 'other']
                            cur   = p['mission'].get('quest_type', 'side')
                            p['mission']['quest_type'] = types[(types.index(cur) + 1) % len(types)]
                        elif action == 'obj_cycle_type':
                            idx  = entry['index']
                            objs = p['mission']['objectives']
                            if 0 <= idx < len(objs):
                                cur  = objs[idx]['type']
                                nxt  = self.OBJECTIVE_TYPES[(self.OBJECTIVE_TYPES.index(cur) + 1) % len(self.OBJECTIVE_TYPES)]
                                objs[idx]['type']   = nxt
                                objs[idx]['params'] = dict(self.OBJECTIVE_DEFAULTS[nxt])
                        elif action == 'obj_remove':
                            idx = entry['index']
                            if 0 <= idx < len(p['mission']['objectives']):
                                p['mission']['objectives'].pop(idx)
                                p['mission_active_field'] = None
                        elif action == 'obj_add':
                            from dev_tools.room_editor.room_editor_tools.mission_manager import MissionManager
                            p['mission']['objectives'].append(MissionManager.make_objective('kill'))
                        elif action == 'obj_dropdown_open':
                            idx       = entry['index']
                            param_key = entry['param']
                            # Toggle: clicking the same button closes it
                            if (self._open_dropdown is not None
                                    and self._open_dropdown['obj_idx'] == idx
                                    and self._open_dropdown['param'] == param_key):
                                self._open_dropdown = None
                            else:
                                self._open_dropdown = {
                                    'obj_idx':   idx,
                                    'param':     param_key,
                                    'options':   self._get_dropdown_options(param_key),
                                    'scroll':    0,
                                    'anchor_x':  entry['anchor_x'],
                                    'anchor_y':  entry['anchor_y'],
                                    'btn_h':     24,
                                    'width':     entry['width'],
                                }
                        elif action == 'focus_field':
                            p['mission_active_field'] = entry['field']
                        return True
                return True  # consume any click while popup is open

            # ── AI type selector click ─────────────────────────────────────
            for entry in self.ui_rects.get('ai_type_rects', []):
                if entry['rect'].collidepoint(mouse_pos):
                    if self._is_rocket_launcher_selected():
                        return True
                    self.selected_ai_type = entry['ai_type']
                    return True

            # ── NPC mode selector click ────────────────────────────────────
            for entry in self.ui_rects.get('npc_mode_rects', []):
                if entry['rect'].collidepoint(mouse_pos):
                    self.selected_npc_mode = entry['mode']
                    return True

            # ── NPC facing selector click ──────────────────────────────────
            for entry in self.ui_rects.get('npc_facing_rects', []):
                if entry['rect'].collidepoint(mouse_pos):
                    self.selected_npc_facing = entry['facing']
                    return True

            # ── NPC dialogue clicks ────────────────────────────────────────
            for entry in self.ui_rects.get('npc_dialogue_rects', []):
                if entry['rect'].collidepoint(mouse_pos):
                    action = entry.get('action')
                    if action == 'edit':
                        self.npc_dialogue_index = entry['index']
                        self.npc_text_input     = self.npc_dialogues[entry['index']]
                        self.npc_editing_text   = True
                    elif action == 'prev':
                        self.npc_dialogue_index = max(0, self.npc_dialogue_index - 1)
                    elif action == 'next':
                        self.npc_dialogue_index = min(len(self.npc_dialogues) - 1, self.npc_dialogue_index + 1)
                    elif action == 'add':
                        self.npc_dialogues.append("New line.")
                        self.npc_dialogue_index = len(self.npc_dialogues) - 1
                        self.npc_text_input     = self.npc_dialogues[-1]
                        self.npc_editing_text   = True
                    elif action == 'remove':
                        if len(self.npc_dialogues) > 1:
                            self.npc_dialogues.pop(self.npc_dialogue_index)
                            self.npc_dialogue_index = max(0, self.npc_dialogue_index - 1)
                    return True

            # ── variant selector click ─────────────────────────────────────
            for i, entry in enumerate(self.ui_rects.get('variant_rects', [])):
                if entry['rect'].collidepoint(mouse_pos):
                    self.selected_variant = entry['variant']
                    # update main sprite so the grid thumbnail reflects choice
                    if self.selected_entity:
                        self.selected_entity['sprite'] = entry['variant']['sprite'].copy()
                    # Rocket launcher is always easy AI - reset if user just picked it
                    if entry['variant'].get('type') == 'rocketlauncher':
                        self.selected_ai_type = 'easy'
                    return True

            # ── category tab click ─────────────────────────────────────────
            for entry in self.ui_rects.get('category_rects', []):
                if entry['rect'].collidepoint(mouse_pos):
                    self.current_category = entry['key']
                    self.selected_entity = None
                    self.selected_variant = None
                    self.scroll_offset = 0
                    return True

            # ── entity item click ──────────────────────────────────────────
            for entry in self.ui_rects.get('entity_rects', []):
                if entry['rect'].collidepoint(mouse_pos):
                    self.selected_variant = None  # clear old variant before switching entity
                    self.selected_entity = entry['entity']
                    self.selected_variant = self._get_current_variant(entry['entity'])
                    return True

            # ── world click → place entity ─────────────────────────────────
            if self.selected_entity and not self._mouse_in_palette(*mouse_pos):
                if self.selected_entity.get('entity_type') == 'npc':
                    # Open dialogue popup before placing
                    from config.settings import RENDER_SCALE, TILE_SIZE
                    mx, my = mouse_pos
                    wx = (mx + camera_x) / RENDER_SCALE
                    wy = (my + camera_y) / RENDER_SCALE
                    if self.grid_snap:
                        wx = round(wx / TILE_SIZE) * TILE_SIZE
                        wy = round(wy / TILE_SIZE) * TILE_SIZE
                    if not self._placement_blocked(wx, wy, self.selected_entity):
                        self._dialogue_popup = {
                            'dialogues': [''],
                            'active_index': 0,
                            'active_tab': 'dialogues',
                            'world_x': wx, 'world_y': wy,
                            'camera_x': camera_x, 'camera_y': camera_y,
                            'mode': 'place',
                            'edit_target': None,
                            'mission_enabled': False,
                            'mission_active_field': None,
                            'mission': {
                                'id': '',
                                'quest_type': 'side',
                                'sequential': False, 'objectives': [],
                                'rewards': {'xp': 0},
                                'dialogues': {
                                    'accepted': '', 'active': '',
                                    'completed': '', 'rewarded': '',
                                },
                            },
                        }
                else:
                    self._place_entity(mouse_pos, camera_x, camera_y)
                return True

        # ── keyboard shortcuts ─────────────────────────────────────────────
        if event.type == pygame.KEYDOWN:
            # Dialogue popup consumes all input while open
            if self._dialogue_popup is not None:
                p = self._dialogue_popup

                # ── Mission tab keyboard ───────────────────────────────────
                if p.get('active_tab') == 'mission':
                    field = p.get('mission_active_field')
                    if field:
                        if event.key == pygame.K_ESCAPE:
                            p['mission_active_field'] = None
                        elif event.key == pygame.K_RETURN:
                            p['mission_active_field'] = None
                        elif event.key == pygame.K_BACKSPACE:
                            val = self._mfield_get(p, field)
                            self._mfield_set(p, field, val[:-1])
                        elif event.unicode and event.unicode.isprintable():
                            val = self._mfield_get(p, field)
                            if len(val) < 120:
                                self._mfield_set(p, field, val + event.unicode)
                    elif event.key == pygame.K_ESCAPE:
                        if self._open_dropdown is not None:
                            self._open_dropdown = None
                        else:
                            self._dialogue_popup = None
                            if self._popup_only_mode:
                                self.active = False
                                self._popup_only_mode = False
                    return True

                # ── Dialogues tab keyboard ─────────────────────────────────
                idx = p['active_index']
                if event.key == pygame.K_RETURN and (pygame.key.get_mods() & pygame.KMOD_SHIFT):
                    p['dialogues'].insert(idx + 1, '')
                    p['active_index'] = idx + 1
                elif event.key == pygame.K_RETURN:
                    self._confirm_dialogue_popup()
                elif event.key == pygame.K_ESCAPE:
                    self._dialogue_popup = None
                    if self._popup_only_mode:
                        self.active = False
                        self._popup_only_mode = False
                elif event.key == pygame.K_TAB:
                    p['active_index'] = (idx + 1) % len(p['dialogues'])
                elif event.key == pygame.K_BACKSPACE:
                    if p['dialogues'][idx]:
                        p['dialogues'][idx] = p['dialogues'][idx][:-1]
                    elif len(p['dialogues']) > 1:
                        p['dialogues'].pop(idx)
                        p['active_index'] = max(0, idx - 1)
                elif event.unicode and event.unicode.isprintable():
                    if len(p['dialogues'][idx]) < 120:
                        p['dialogues'][idx] += event.unicode
                return True

            if event.key == pygame.K_g:
                self.grid_snap = not self.grid_snap
                return True
            if event.key == pygame.K_h:
                self.show_grid = not self.show_grid
                return True

        return False

    # =========================================================================
    # Dropdown data helpers
    # =========================================================================

    def _get_room_names(self) -> list:
        """Return sorted list of all room names, prefixed with '' (any/none)."""
        names = []
        if self.room_manager:
            for room in getattr(self.room_manager, 'rooms', []):
                n = getattr(room, 'name', None) or room.get('name', '')
                if n and not getattr(room, 'is_transient', False):
                    names.append(n)
        return [''] + sorted(set(names))

    def _get_enemy_ids(self) -> list:
        """Return sorted list of all enemy/boss IDs from the entity catalogue,
        prefixed with '' meaning 'any enemy'."""
        ids = set()
        for cat_key in ('Enemies', 'Enemy Bosses'):
            for entity in self.categories.get(cat_key, []):
                eid = entity.get('id', '')
                if eid:
                    ids.add(eid)
        return [''] + sorted(ids)

    def _get_npc_instance_ids(self) -> list:
        """Return list of (instance_id, label) tuples for every NPC placed in
        any room, plus '' for none.  Label = 'instance_id (room_name)'."""
        entries = ['']
        if not self.room_manager:
            return entries
        for room in getattr(self.room_manager, 'rooms', []):
            room_name = getattr(room, 'name', '') or room.get('name', '')
            for ent in getattr(room, 'entities', []):
                if ent.get('entity_type') != 'npc':
                    continue
                iid = ent.get('instance_id', '')
                if iid:
                    entries.append(f"{iid} ({room_name})")
        return entries

    @staticmethod
    def _npc_entry_to_id(entry: str) -> str:
        """Extract the bare instance_id from an entry returned by _get_npc_instance_ids."""
        # Entries are either '' or 'abc12345 (Room Name)'
        return entry.split(' ')[0] if entry else ''

    def _get_dropdown_options(self, param_key: str) -> list:
        """Return the option list for a given dropdown param key."""
        if param_key == 'enemy_id':
            return self._get_enemy_ids()
        if param_key in ('room', 'room_name'):
            return self._get_room_names()
        if param_key == 'npc_instance_id':
            return self._get_npc_instance_ids()
        return ['']

    def _param_display_value(self, param_key: str, raw_value: str) -> str:
        """Convert a stored param value to its display label."""
        if param_key == 'npc_instance_id' and raw_value:
            # Find the matching full entry label
            for entry in self._get_npc_instance_ids():
                if entry.startswith(raw_value + ' ') or entry == raw_value:
                    return entry
        return raw_value if raw_value else '(any)' if param_key in ('enemy_id', 'room') else '—'

    # =========================================================================
    # Placement
    # =========================================================================

    def _placement_blocked(self, world_x, world_y, entity):
        """
        Fail-safe 1: Return True if placing *entity* centred at (world_x, world_y)
        would overlap any solid obstacle (collision wall, stone, gate, transition).
        """
        import pygame
        w = entity.get('width', 32)
        h = entity.get('height', 32)
        entity_rect = pygame.Rect(world_x - w // 2, world_y - h // 2, w, h)

        for obs in self.placement_obstacles:
            if not getattr(obs, 'active', True):
                continue
            # Collision walls are top-left anchored; everything else is centred
            if hasattr(obs, 'id') and obs.id == 'collision_wall':
                obs_rect = pygame.Rect(obs.x, obs.y, obs.width, obs.height)
            elif hasattr(obs, 'solid') and not obs.solid:
                continue  # destroyed stone
            elif hasattr(obs, 'get_rect'):
                obs_rect = obs.get_rect()
            elif hasattr(obs, 'x') and hasattr(obs, 'width'):
                obs_rect = pygame.Rect(
                    obs.x - obs.width // 2,
                    obs.y - obs.height // 2,
                    obs.width,
                    obs.height,
                )
            else:
                continue
            if entity_rect.colliderect(obs_rect):
                return True
        return False

    def _confirm_dialogue_popup(self):
        """Confirm the popup — place new NPC or update existing one."""
        p = self._dialogue_popup
        self._dialogue_popup = None
        self._open_dropdown = None

        dialogues = [d.strip() for d in p['dialogues']]
        dialogues = [d for d in dialogues if d] or ["Hello, traveler!"]

        dialogue_config = {
            'dialogues':        dialogues,
            'trigger_limit':    -1,
            'triggers_used':    0,
            'after_limit_text': "...",
            'random_order':     False,
            'give_item':        None,
            'item_given':       False,
        }

        if p.get('mode') == 'edit' and p.get('edit_target') is not None:
            tgt = p['edit_target']
            tgt['dialogue_config'] = dialogue_config
            # Save or clear mission
            if p.get('mission_enabled'):
                mission = dict(p.get('mission', {}))
                mission['dialogues'] = dict(mission.get('dialogues', {}))
                mission['dialogues']['offer'] = dialogues
                iid = tgt.get('instance_id', mission.get('id', ''))
                mission['id']                = iid
                mission['giver_instance_id'] = iid
                tgt['mission'] = mission
            else:
                tgt.pop('mission', None)
            if self._popup_only_mode:
                self.active = False
                self._popup_only_mode = False
            return

        # Placing a new NPC
        if not self.selected_entity:
            return
        self.selected_entity['_npc_dialogue_config'] = dialogue_config
        self.selected_entity['_npc_mode']   = self.selected_npc_mode
        self.selected_entity['_npc_facing'] = self.selected_npc_facing
        if p.get('mission_enabled'):
            mission = dict(p.get('mission', {}))
            mission['dialogues'] = dict(mission.get('dialogues', {}))
            mission['dialogues']['offer'] = dialogues
            self.selected_entity['_npc_mission'] = mission
        else:
            self.selected_entity.pop('_npc_mission', None)
        variant = self.selected_variant or self._get_current_variant(self.selected_entity)
        if self.on_entity_placed:
            self.on_entity_placed(self.selected_entity, variant, None, p['world_x'], p['world_y'])

    def open_npc_edit_popup(self, entity_data):
        """Open the NPC editor popup for an already-placed NPC (double-click)."""
        cfg       = entity_data.get('dialogue_config') or {}
        dialogues = list(cfg.get('dialogues', [''])) or ['']

        existing_m   = entity_data.get('mission') or {}
        mission_data = {
            'id':          existing_m.get('id', ''),
            'quest_type':  existing_m.get('quest_type', 'side'),
            'sequential':  existing_m.get('sequential', False),
            'objectives':  list(existing_m.get('objectives', [])),
            'rewards':     dict(existing_m.get('rewards', {'xp': 0})),
            'dialogues': {
                'accepted':  existing_m.get('dialogues', {}).get('accepted',  ''),
                'active':    existing_m.get('dialogues', {}).get('active',    ''),
                'completed': existing_m.get('dialogues', {}).get('completed', ''),
                'rewarded':  existing_m.get('dialogues', {}).get('rewarded',  ''),
            },
        }

        self._dialogue_popup = {
            'dialogues':            dialogues,
            'active_index':         0,
            'active_tab':           'dialogues',
            'mode':                 'edit',
            'edit_target':          entity_data,
            'world_x':              None,
            'world_y':              None,
            'camera_x':             0,
            'camera_y':             0,
            'mission_enabled':      bool(existing_m),
            'mission_active_field': None,
            'mission':              mission_data,
        }
        self._popup_only_mode = True
        self.active = True

    # ── Mission field helpers ─────────────────────────────────────────────────

    def _mfield_get(self, p, field) -> str:
        m = p['mission']
        if field == 'reward_xp':    return str(m.get('rewards', {}).get('xp', 0))
        if field.startswith('dlg_'):
            return m.get('dialogues', {}).get(field[4:], '')
        if field.startswith('obj:'):
            _, idx, param = field.split(':', 2)
            idx = int(idx)
            objs = m.get('objectives', [])
            if 0 <= idx < len(objs):
                if param == 'description':
                    return objs[idx].get('description', '')
                return str(objs[idx]['params'].get(param, ''))
        return ''

    def _mfield_set(self, p, field, value: str):
        m = p['mission']
        if field == 'reward_xp':
            try:    m.setdefault('rewards', {})['xp'] = int(value)
            except: m.setdefault('rewards', {})['xp'] = 0
            return
        if field.startswith('dlg_'):
            m.setdefault('dialogues', {})[field[4:]] = value; return
        if field.startswith('obj:'):
            _, idx, param = field.split(':', 2)
            idx  = int(idx)
            objs = m.get('objectives', [])
            if 0 <= idx < len(objs):
                if param == 'description':
                    objs[idx]['description'] = value
                else:
                    objs[idx]['params'][param] = value

    def _place_entity(self, mouse_pos, camera_x, camera_y):
        """Convert screen click → world coords, snap if needed, fire callback."""
        from config.settings import RENDER_SCALE, TILE_SIZE

        screen_x, screen_y = mouse_pos
        world_x = (screen_x + camera_x) / RENDER_SCALE
        world_y = (screen_y + camera_y) / RENDER_SCALE

        if self.grid_snap:
            world_x = round(world_x / TILE_SIZE) * TILE_SIZE
            world_y = round(world_y / TILE_SIZE) * TILE_SIZE

        # ── Fail-safe 1: refuse to place inside a solid obstacle ────────────
        if self.selected_entity and self._placement_blocked(world_x, world_y, self.selected_entity):
            return  # silently block; the ghost preview already shows the position

        variant = self.selected_variant or self._get_current_variant(self.selected_entity)

        # Determine AI type (only for enemies and bosses)
        ai_type = None
        if self.selected_entity and self.selected_entity.get('entity_type') in ['enemy', 'boss']:
            if self._is_rocket_launcher_selected():
                ai_type = 'easy'
            else:
                ai_type = self.selected_ai_type

        # Embed NPC settings into entity dict before callback
        if self.selected_entity and self.selected_entity.get('entity_type') == 'npc':
            self.selected_entity['_npc_mode']   = self.selected_npc_mode
            self.selected_entity['_npc_facing'] = self.selected_npc_facing
            self.selected_entity['_npc_dialogue_config'] = {
                'dialogues':      list(self.npc_dialogues),
                'trigger_limit':  -1,
                'triggers_used':  0,
                'after_limit_text': "...",
                'random_order':   False,
                'give_item':      None,
                'item_given':     False,
            }

        if self.on_entity_placed:
            # BACKWARDS COMPATIBILITY: Try new signature (with ai_type), fall back to old
            import inspect
            try:
                # Check if callback accepts ai_type parameter
                sig = inspect.signature(self.on_entity_placed)
                param_count = len(sig.parameters)

                if param_count >= 5:
                    # New signature: (entity_dict, variant_dict, ai_type, world_x, world_y)
                    self.on_entity_placed(self.selected_entity, variant, ai_type, world_x, world_y)
                else:
                    # Old signature: (entity_dict, variant_dict, world_x, world_y)
                    # Store ai_type in entity_dict temporarily for room_editor to access
                    if ai_type and self.selected_entity:
                        self.selected_entity['_ai_type'] = ai_type
                    self.on_entity_placed(self.selected_entity, variant, world_x, world_y)
            except:
                # Fallback: try new signature, if fails try old
                try:
                    self.on_entity_placed(self.selected_entity, variant, ai_type, world_x, world_y)
                except TypeError:
                    # Old callback - store ai_type in entity_dict
                    if ai_type and self.selected_entity:
                        self.selected_entity['_ai_type'] = ai_type
                    self.on_entity_placed(self.selected_entity, variant, world_x, world_y)

    # =========================================================================
    # Drawing  ── main entry point
    # =========================================================================

    def draw(self, screen):
        """Render the entire palette panel.  Call once per frame when active."""
        if not self.active:
            return

        # In popup-only mode (editing an existing NPC) skip the palette entirely
        if self._popup_only_mode:
            if self._dialogue_popup is not None:
                self._draw_dialogue_popup(screen)
            return

        # Update hover state and always draw the toggle tab
        mx, my = pygame.mouse.get_pos()
        self._hover_panel_toggle = self._panel_toggle_rect().collidepoint(mx, my)
        self._draw_panel_toggle_tab(screen)

        if not self.palette_visible:
            # Still draw the dialogue popup even when the palette is hidden
            if self._dialogue_popup is not None:
                self._draw_dialogue_popup(screen)
            return

        # clear hit-rect cache each frame
        self.ui_rects = {'category_rects': [], 'entity_rects': [], 'variant_rects': [], 'ai_type_rects': [], 'npc_mode_rects': [], 'npc_facing_rects': [], 'npc_dialogue_rects': []}

        self._draw_palette_background(screen)
        self._draw_title(screen)
        self._draw_category_tabs(screen)
        y_after_tabs = self._category_tabs_bottom_y()
        self._draw_entity_grid(screen, y_after_tabs)
        self._draw_variant_selector(screen)
        self._draw_settings_panel(screen)

    # =========================================================================
    # Drawing helpers
    # =========================================================================

    def _draw_palette_background(self, screen):
        rect = pygame.Rect(self.palette_x, self.palette_y,
                           self.palette_width, self.palette_height)
        bg = pygame.Surface((self.palette_width, self.palette_height), pygame.SRCALPHA)
        bg.fill(self.COLORS['bg_transparent'])
        screen.blit(bg, (self.palette_x, self.palette_y))
        pygame.draw.rect(screen, self.COLORS['accent'], rect, 2)

    def _draw_title(self, screen):
        title = self.font_medium.render("Entity Palette", True, self.COLORS['text'])
        screen.blit(title, (self.palette_x + 20, self.palette_y + 10))

    # ── category tabs ────────────────────────────────────────────────────────

    def _category_tabs_top_y(self):
        return self.palette_y + 45  # below title

    def _category_tabs_bottom_y(self):
        return self._category_tabs_top_y() + len(self.category_keys) * 40 + 10

    def _draw_category_tabs(self, screen):
        y = self._category_tabs_top_y()

        for key in self.category_keys:
            is_sel = (key == self.current_category)
            hover_w = self.category_hover_anim.get(key, 0.0)

            tab_rect = pygame.Rect(
                self.palette_x + self.palette_padding,
                y,
                self.palette_width - self.palette_padding * 2,
                30
            )

            # hover glow
            if hover_w > 0:
                glow = pygame.Surface((tab_rect.width + 4, tab_rect.height + 4), pygame.SRCALPHA)
                pygame.draw.rect(glow,
                                 (*self.COLORS['accent'], int(hover_w * 100)),
                                 (0, 0, tab_rect.width + 4, tab_rect.height + 4),
                                 border_radius=5)
                screen.blit(glow, (tab_rect.x - 2, tab_rect.y - 2))

            bg_col = self.COLORS['panel_light'] if is_sel else self.COLORS['panel']
            border_col = self.COLORS['accent'] if is_sel else self.COLORS['grid']
            pygame.draw.rect(screen, bg_col, tab_rect, border_radius=5)
            pygame.draw.rect(screen, border_col, tab_rect, 2, border_radius=5)

            txt_col = self.COLORS['text'] if is_sel else self.COLORS['text_dim']
            surf = self.font_medium.render(key, True, txt_col)
            screen.blit(surf, surf.get_rect(center=tab_rect.center))

            # store for hit-testing
            self.ui_rects['category_rects'].append({'rect': tab_rect, 'key': key})
            y += 40

        # separator line
        pygame.draw.line(screen, self.COLORS['accent'],
                         (self.palette_x + self.palette_padding, y),
                         (self.palette_x + self.palette_width - self.palette_padding, y), 1)

    # ── entity grid ──────────────────────────────────────────────────────────

    def _draw_entity_grid(self, screen, start_y):
        """Draw the scrollable grid of entity items for the active category."""
        # clip region – stop before variant selector + settings footer
        content_height = (self.palette_height
                          - (start_y - self.palette_y)
                          - 200)  # 200 px reserved for variant strip + settings
        clip_rect = pygame.Rect(self.palette_x, start_y,
                                self.palette_width, content_height)
        screen.set_clip(clip_rect)

        entities = self.categories[self.current_category]
        current_y = start_y - self.scroll_offset

        for i, entity in enumerate(entities):
            row = i // self.items_per_row
            col = i % self.items_per_row

            item_x = self.palette_x + self.palette_padding + col * (self.item_size + 10)
            item_y = current_y + row * (self.item_size + 10)

            # compute the actual tile height for this entity (same logic as _draw_entity_item)
            ew = entity.get('width', 1)
            eh = entity.get('height', 1)
            aspect = eh / ew if ew > 0 else 1.0
            item_h = max(self.item_size, int(self.item_size * aspect))

            # skip fully off-screen items (but still register rects for
            # click detection inside the clip region)
            if item_y + item_h < start_y or item_y > start_y + content_height:
                continue

            self._draw_entity_item(screen, entity, item_x, item_y)

        screen.set_clip(None)

    def _draw_entity_item(self, screen, entity, x, y):
        """Draw one entity tile in the grid."""
        # Compute tile dimensions from aspect ratio so tall sprites
        # (e.g. Pui Pui 64×64) aren't squished into a square box.
        ew = entity.get('width', 1)
        eh = entity.get('height', 1)
        aspect = eh / ew if ew > 0 else 1.0
        item_w = self.item_size
        item_h = max(self.item_size, int(self.item_size * aspect))
        item_rect = pygame.Rect(x, y, item_w, item_h)

        is_selected = (self.selected_entity is entity)
        is_hover = (self.hover_entity is entity)

        # glow behind selected / hovered tile
        if is_selected or is_hover:
            glow = pygame.Surface((item_w + 4, item_h + 4), pygame.SRCALPHA)
            alpha = 150 if is_selected else 80
            pygame.draw.rect(glow,
                             (*self.COLORS['accent'], alpha),
                             (0, 0, item_w + 4, item_h + 4),
                             border_radius=5)
            screen.blit(glow, (x - 2, y - 2))

        bg_col = self.COLORS['panel_light'] if is_selected else self.COLORS['panel']
        border_col = self.COLORS['accent'] if is_selected else self.COLORS['grid']
        pygame.draw.rect(screen, bg_col, item_rect, border_radius=5)
        pygame.draw.rect(screen, border_col, item_rect, 2 if is_selected else 1, border_radius=5)

        # sprite fills tile at correct proportions (no squishing)
        if entity['sprite']:
            sprite = pygame.transform.scale(entity['sprite'], (item_w - 8, item_h - 8))
            screen.blit(sprite, sprite.get_rect(center=item_rect.center))

        # name label below tile
        name_surf = self.font_small.render(entity['name'], True, self.COLORS['text_dim'])
        screen.blit(name_surf, name_surf.get_rect(centerx=item_rect.centerx,
                                                  top=item_rect.bottom + 2))

        # "has variants" hint on hover
        if entity.get('has_variants') and is_hover:
            hint = self.font_small.render("Has variants", True, self.COLORS['accent'])
            hint_rect = hint.get_rect(centerx=item_rect.centerx, top=item_rect.bottom + 18)
            bg = pygame.Surface((hint_rect.width + 6, hint_rect.height + 2), pygame.SRCALPHA)
            bg.fill((0, 0, 0, 180))
            screen.blit(bg, (hint_rect.x - 3, hint_rect.y - 1))
            screen.blit(hint, hint_rect)

        # register for hit-testing
        self.ui_rects['entity_rects'].append({'rect': item_rect, 'entity': entity})

    # ── variant selector strip ───────────────────────────────────────────────

    def _draw_variant_selector(self, screen):
        """
        Grid of variant swatches – only visible when the selected entity has
        variants.  Swatches wrap onto additional rows so nothing is clipped
        when there are many variants.
        """
        if not self.selected_entity or not self.selected_entity.get('has_variants'):
            return

        variants = self.selected_entity.get('variants', [])
        if not variants:
            return

        swatch_size  = 48
        swatch_gap   = 8
        label_height = 20   # "Select Variant:" label
        name_height  = 14   # variant name below each swatch
        row_height   = swatch_size + name_height + swatch_gap
        top_pad      = 8
        bot_pad      = 6

        # How many swatches fit in one row?
        available_w  = self.palette_width - self.palette_padding * 2
        per_row      = max(1, (available_w + swatch_gap) // (swatch_size + swatch_gap))
        num_rows     = (len(variants) + per_row - 1) // per_row

        strip_height = top_pad + label_height + num_rows * row_height + bot_pad

        # Bottom of variant strip sits flush against the settings panel
        strip_bottom = self.palette_y + self.palette_height - 160
        strip_y      = strip_bottom - strip_height
        strip_x      = self.palette_x

        # background
        strip_rect = pygame.Rect(strip_x, strip_y, self.palette_width, strip_height)
        pygame.draw.rect(screen, self.COLORS['variant_bg'], strip_rect)
        pygame.draw.line(screen, self.COLORS['accent'],
                         (strip_x, strip_y),
                         (strip_x + self.palette_width, strip_y), 2)

        # label
        label = self.font_small.render("Select Variant:", True, self.COLORS['text_dim'])
        screen.blit(label, (strip_x + self.palette_padding, strip_y + top_pad))

        current_var  = self.selected_variant or self._get_current_variant(self.selected_entity)
        sx           = strip_x + self.palette_padding
        swatches_top = strip_y + top_pad + label_height

        for i, variant in enumerate(variants):
            col  = i % per_row
            row  = i // per_row
            vx   = sx + col * (swatch_size + swatch_gap)
            vy   = swatches_top + row * row_height
            rect = pygame.Rect(vx, vy, swatch_size, swatch_size)

            is_sel   = (current_var is variant)
            is_hover = (self.hover_variant_idx == i)

            # background
            if is_sel:
                bg_col = self.COLORS['variant_selected']
            elif is_hover:
                bg_col = self.COLORS['panel_light']
            else:
                bg_col = self.COLORS['panel']
            pygame.draw.rect(screen, bg_col, rect, border_radius=4)

            # border
            border_col = self.COLORS['accent'] if is_sel else self.COLORS['grid']
            pygame.draw.rect(screen, border_col, rect, 2 if is_sel else 1, border_radius=4)

            # variant sprite (or colour swatch fallback)
            if variant.get('sprite'):
                spr = variant['sprite']
                max_dim = swatch_size - 6
                sw, sh = spr.get_size()
                if sw > max_dim or sh > max_dim:
                    scale = min(max_dim / sw, max_dim / sh)
                    spr = pygame.transform.scale(spr, (int(sw * scale), int(sh * scale)))
                screen.blit(spr, spr.get_rect(center=rect.center))
            else:
                inner = rect.inflate(-6, -6)
                pygame.draw.rect(screen, variant.get('color', (128, 128, 128)), inner, border_radius=2)

            # name below swatch
            name = self.font_small.render(variant['name'], True, self.COLORS['text_dim'])
            screen.blit(name, name.get_rect(centerx=rect.centerx, top=rect.bottom + 2))

            # store for hit-testing
            self.ui_rects['variant_rects'].append({'rect': rect, 'variant': variant})

    # ── settings / instructions footer ──────────────────────────────────────

    def _draw_settings_panel(self, screen):
        panel_h = 160
        panel_y = self.palette_y + self.palette_height - panel_h

        panel_rect = pygame.Rect(self.palette_x, panel_y, self.palette_width, panel_h)
        pygame.draw.rect(screen, self.COLORS['bg'], panel_rect)
        pygame.draw.line(screen, self.COLORS['accent'],
                         (self.palette_x, panel_y),
                         (self.palette_x + self.palette_width, panel_y), 2)

        y = panel_y + 10

        # Grid Snap toggle
        snap_label = f"Grid Snap: {'ON' if self.grid_snap else 'OFF'}"
        snap_color = self.COLORS['success'] if self.grid_snap else self.COLORS['text_dim']
        screen.blit(self.font_medium.render(snap_label, True, snap_color),
                    (self.palette_x + self.palette_padding, y))
        screen.blit(self.font_small.render("(Press G)", True, self.COLORS['text_dim']),
                    (self.palette_x + self.palette_padding + 120, y + 3))
        y += 25

        # Show Grid toggle
        grid_label = f"Show Grid: {'ON' if self.show_grid else 'OFF'}"
        grid_color = self.COLORS['success'] if self.show_grid else self.COLORS['text_dim']
        screen.blit(self.font_medium.render(grid_label, True, grid_color),
                    (self.palette_x + self.palette_padding, y))
        screen.blit(self.font_small.render("(Press H)", True, self.COLORS['text_dim']),
                    (self.palette_x + self.palette_padding + 120, y + 3))
        y += 30

        # AI Type selector (only shown when an enemy or boss is selected)
        if (self.selected_entity and
                self.selected_entity.get('entity_type') in ['enemy', 'boss']):

            if self._is_rocket_launcher_selected():
                # Rocket launcher is always easy AI - show a locked indicator
                ai_label = self.font_small.render("AI Type:", True, self.COLORS['text_dim'])
                screen.blit(ai_label, (self.palette_x + self.palette_padding, y))
                locked_surf = self.font_small.render(
                    "Easy  (Rocket Launcher is always Easy)", True, self.COLORS['text_dark'])
                screen.blit(locked_surf, (self.palette_x + self.palette_padding, y + 18))
                y += 48
            else:
                ai_label = self.font_small.render("AI Type:", True, self.COLORS['text_dim'])
                screen.blit(ai_label, (self.palette_x + self.palette_padding, y))
                y += 18

                # AI type buttons
                button_width = 70
                button_height = 24
                button_gap = 8
                bx = self.palette_x + self.palette_padding

                for i, ai_type in enumerate(self.ai_types):
                    button_x = bx + i * (button_width + button_gap)
                    button_rect = pygame.Rect(button_x, y, button_width, button_height)

                    is_selected = (ai_type == self.selected_ai_type)
                    is_hover = (self.hover_ai_type_idx == i)

                    # Background
                    if is_selected:
                        bg_color = self.COLORS['variant_selected']
                    elif is_hover:
                        bg_color = self.COLORS['panel_light']
                    else:
                        bg_color = self.COLORS['panel']
                    pygame.draw.rect(screen, bg_color, button_rect, border_radius=4)

                    # Border
                    border_color = self.COLORS['accent'] if is_selected else self.COLORS['grid']
                    border_width = 2 if is_selected else 1
                    pygame.draw.rect(screen, border_color, button_rect, border_width, border_radius=4)

                    # Text
                    text_color = self.COLORS['text'] if is_selected else self.COLORS['text_dim']
                    text = self.font_small.render(ai_type.capitalize(), True, text_color)
                    screen.blit(text, text.get_rect(center=button_rect.center))

                    # Store for hit-testing
                    self.ui_rects['ai_type_rects'].append({
                        'rect': button_rect,
                        'ai_type': ai_type,
                        'index': i
                    })

                y += button_height + 12

        # NPC Mode + Facing selectors (only shown when an NPC is selected)
        if self.selected_entity and self.selected_entity.get('entity_type') == 'npc':
            button_width  = 70
            button_height = 24
            button_gap    = 8
            bx = self.palette_x + self.palette_padding

            # Mode
            self.ui_rects['npc_mode_rects'] = []
            screen.blit(self.font_small.render("NPC Mode:", True, self.COLORS['text_dim']),
                        (bx, y))
            y += 18
            for i, mode in enumerate(self.npc_modes):
                button_rect = pygame.Rect(bx + i * (button_width + button_gap), y, button_width, button_height)
                is_sel  = (mode == self.selected_npc_mode)
                is_hov  = (self.hover_npc_mode_idx == i)
                bg_col  = self.COLORS['variant_selected'] if is_sel else (self.COLORS['panel_light'] if is_hov else self.COLORS['panel'])
                bd_col  = self.COLORS['accent'] if is_sel else self.COLORS['grid']
                pygame.draw.rect(screen, bg_col,  button_rect, border_radius=4)
                pygame.draw.rect(screen, bd_col,  button_rect, 2 if is_sel else 1, border_radius=4)
                txt_col = self.COLORS['text'] if is_sel else self.COLORS['text_dim']
                screen.blit(self.font_small.render(mode.capitalize(), True, txt_col),
                            self.font_small.render(mode.capitalize(), True, txt_col).get_rect(center=button_rect.center))
                self.ui_rects['npc_mode_rects'].append({'rect': button_rect, 'mode': mode, 'index': i})
            y += button_height + 10

            # Facing (only meaningful in static mode)
            self.ui_rects['npc_facing_rects'] = []
            facing_label_col = self.COLORS['text_dim'] if self.selected_npc_mode == 'static' else self.COLORS['text_dark']
            screen.blit(self.font_small.render("Facing (static):", True, facing_label_col), (bx, y))
            y += 18
            for i, facing in enumerate(self.npc_facings):
                button_rect = pygame.Rect(bx + i * (button_width + button_gap), y, button_width, button_height)
                is_sel  = (facing == self.selected_npc_facing)
                is_hov  = (self.hover_npc_facing_idx == i)
                if self.selected_npc_mode != 'static':
                    bg_col = self.COLORS['panel']
                    bd_col = self.COLORS['grid']
                    txt_col = self.COLORS['text_dark']
                else:
                    bg_col  = self.COLORS['variant_selected'] if is_sel else (self.COLORS['panel_light'] if is_hov else self.COLORS['panel'])
                    bd_col  = self.COLORS['accent'] if is_sel else self.COLORS['grid']
                    txt_col = self.COLORS['text'] if is_sel else self.COLORS['text_dim']
                pygame.draw.rect(screen, bg_col, button_rect, border_radius=4)
                pygame.draw.rect(screen, bd_col, button_rect, 2 if is_sel else 1, border_radius=4)
                screen.blit(self.font_small.render(facing.capitalize(), True, txt_col),
                            self.font_small.render(facing.capitalize(), True, txt_col).get_rect(center=button_rect.center))
                self.ui_rects['npc_facing_rects'].append({'rect': button_rect, 'facing': facing, 'index': i})
            y += button_height + 12

        instructions = [
            "Click Entity: Select",
            "Click World: Place",
            "Right-Click World: Delete",
            "ESC: Close",
        ]
        for line in instructions:
            screen.blit(self.font_small.render(line, True, self.COLORS['text_dim']),
                        (self.palette_x + self.palette_padding, y))
            y += 18

        # ── Dialogue popup overlay ─────────────────────────────────────────────
        if self._dialogue_popup is not None:
            self._draw_dialogue_popup(screen)

    def _draw_dialogue_popup(self, screen):
        """Overlay popup with [Dialogues] and [Mission] tabs."""
        p  = self._dialogue_popup
        sw, sh = self.screen_width, self.screen_height

        # Dim background
        overlay = pygame.Surface((sw, sh), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        screen.blit(overlay, (0, 0))

        pw = 700
        # Height depends on tab
        if p.get('active_tab') == 'mission' and p.get('mission_enabled'):
            n_obj = len(p['mission'].get('objectives', []))
            ph    = 80 + 36 + 32 + 36 + 36 + 30 + 22 + (32 * min(n_obj, 8)) + 36 + 10 + 36 + 22 + 30 * 4 + 52
        elif p.get('active_tab') == 'mission':
            ph = 80 + 36 + 52  # just the enable toggle + footer
        else:
            row_h   = 36
            max_vis = 6
            n_dlg   = len(p['dialogues'])
            vis     = min(n_dlg, max_vis)
            ph      = 80 + vis * row_h + 52
        ph = max(ph, 200)

        px = (sw - pw) // 2
        py = max(10, (sh - ph) // 2)

        box = pygame.Rect(px, py, pw, ph)
        pygame.draw.rect(screen, self.COLORS['panel'], box, border_radius=8)
        pygame.draw.rect(screen, self.COLORS['accent'], box, 2, border_radius=8)

        # ── Tab bar ──────────────────────────────────────────────────────────
        self._dialogue_popup_rects = []
        tab_y  = py + 10
        tabs   = [('dialogues', 'Dialogues'), ('mission', 'Mission')]
        tab_w  = 110
        active_tab = p.get('active_tab', 'dialogues')
        for i, (tab_key, tab_label) in enumerate(tabs):
            tr = pygame.Rect(px + 14 + i * (tab_w + 6), tab_y, tab_w, 28)
            is_sel = (tab_key == active_tab)
            bg  = self.COLORS['panel_light'] if is_sel else self.COLORS['panel']
            bd  = self.COLORS['accent']      if is_sel else self.COLORS['grid']
            pygame.draw.rect(screen, bg, tr, border_radius=4)
            pygame.draw.rect(screen, bd, tr, 2 if is_sel else 1, border_radius=4)
            tc  = self.COLORS['accent'] if is_sel else self.COLORS['text_dim']
            lbl = self.font_medium.render(tab_label, True, tc)
            screen.blit(lbl, lbl.get_rect(center=tr.center))
            self._dialogue_popup_rects.append({'rect': tr, 'action': 'switch_tab', 'tab': tab_key})

        content_top = tab_y + 36

        # ── Route to active tab ──────────────────────────────────────────────
        if active_tab == 'mission':
            self._draw_mission_tab(screen, p, px, py, pw, ph, content_top)
        else:
            self._draw_dialogues_tab(screen, p, px, py, pw, ph, content_top)

        # ── Open dropdown list (drawn last so it floats above everything) ────
        if self._open_dropdown is not None:
            self._draw_open_dropdown(screen)

    def _draw_dialogues_tab(self, screen, p, px, py, pw, ph, content_top):
        """Draw the multi-line dialogue editor (original popup content)."""
        n       = len(p['dialogues'])
        row_h   = 36
        max_vis = 6
        vis     = min(n, max_vis)

        # Sub-title
        hint = self.font_small.render(
            f"{n} line{'s' if n != 1 else ''}  (Shift+Enter=new  Tab=next  Backspace-on-empty=delete)",
            True, self.COLORS['text_dark'])
        screen.blit(hint, (px + 14, content_top))
        rows_top = content_top + 20

        for i, text in enumerate(p['dialogues']):
            if i >= max_vis:
                break
            ry     = rows_top + i * row_h
            is_sel = (i == p['active_index'])

            lbl = self.font_small.render(f"Line {i+1}", True,
                                         self.COLORS['text'] if is_sel else self.COLORS['text_dim'])
            screen.blit(lbl, (px + 14, ry + 10))

            field = pygame.Rect(px + 72, ry + 4, pw - 72 - 46, row_h - 8)
            bg    = self.COLORS['panel_light'] if is_sel else self.COLORS['bg']
            bd    = self.COLORS['accent']      if is_sel else self.COLORS['grid']
            pygame.draw.rect(screen, bg, field, border_radius=4)
            pygame.draw.rect(screen, bd, field, 2 if is_sel else 1, border_radius=4)
            display = text + ("_" if is_sel else "")
            if len(display) > 58: display = "..." + display[-55:]
            screen.blit(self.font_medium.render(display, True, self.COLORS['text']),
                        (field.x + 6, field.y + 8))
            self._dialogue_popup_rects.append({'rect': field, 'action': 'select_line', 'index': i})

            rm = pygame.Rect(field.right + 6, ry + 6, 28, row_h - 12)
            rc = self.COLORS['danger'] if n > 1 else self.COLORS['disabled']
            pygame.draw.rect(screen, self.COLORS['panel'], rm, border_radius=3)
            pygame.draw.rect(screen, rc, rm, 1, border_radius=3)
            xs = self.font_small.render("x", True, rc)
            screen.blit(xs, xs.get_rect(center=rm.center))
            if n > 1:
                self._dialogue_popup_rects.append({'rect': rm, 'action': 'remove', 'index': i})

        footer_y = rows_top + vis * row_h + 8
        add_r = pygame.Rect(px + 14, footer_y, 110, 28)
        pygame.draw.rect(screen, self.COLORS['panel_light'], add_r, border_radius=4)
        pygame.draw.rect(screen, self.COLORS['success'],     add_r, 1, border_radius=4)
        at  = self.font_small.render("+ Add Line", True, self.COLORS['success'])
        screen.blit(at, at.get_rect(center=add_r.center))
        self._dialogue_popup_rects.append({'rect': add_r, 'action': 'add'})

        ok_r = pygame.Rect(px + pw - 220, footer_y, 96, 28)
        pygame.draw.rect(screen, self.COLORS['panel_light'], ok_r, border_radius=4)
        pygame.draw.rect(screen, self.COLORS['success'],     ok_r, 2, border_radius=4)
        screen.blit(self.font_medium.render("Confirm", True, self.COLORS['success']),
                    self.font_medium.render("Confirm", True, self.COLORS['success']).get_rect(center=ok_r.center))
        self._dialogue_popup_rects.append({'rect': ok_r, 'action': 'confirm'})

        cl_r = pygame.Rect(px + pw - 114, footer_y, 96, 28)
        pygame.draw.rect(screen, self.COLORS['panel_light'], cl_r, border_radius=4)
        pygame.draw.rect(screen, self.COLORS['danger'],      cl_r, 2, border_radius=4)
        screen.blit(self.font_medium.render("Cancel", True, self.COLORS['danger']),
                    self.font_medium.render("Cancel", True, self.COLORS['danger']).get_rect(center=cl_r.center))
        self._dialogue_popup_rects.append({'rect': cl_r, 'action': 'cancel'})

    def _draw_mission_tab(self, screen, p, px, py, pw, ph, content_top):
        """Draw the mission editor tab."""
        C    = self.COLORS
        bx   = px + 14
        rw   = pw - 28   # usable row width
        y    = content_top
        m    = p['mission']
        af   = p.get('mission_active_field')
        enabled = p.get('mission_enabled', False)

        def text_field(field_key, value, x, fy, w, h=24, label=None):
            """Draw a labelled text field and register its rect."""
            if label:
                ls = self.font_small.render(label, True, C['text_dim'])
                screen.blit(ls, (x, fy + 4))
                x  += self.font_small.size(label)[0] + 6
                w  -= self.font_small.size(label)[0] + 6
            r    = pygame.Rect(x, fy, w, h)
            is_f = (af == field_key)
            bg   = C['panel_light'] if is_f else C['bg']
            bd   = C['accent']      if is_f else C['grid']
            pygame.draw.rect(screen, bg, r, border_radius=3)
            pygame.draw.rect(screen, bd, r, 2 if is_f else 1, border_radius=3)
            disp = value + ("_" if is_f else "")
            if len(disp) > 52: disp = "..." + disp[-49:]
            screen.blit(self.font_small.render(disp, True, C['text']), (r.x + 5, r.y + 5))
            self._dialogue_popup_rects.append({'rect': r, 'action': 'focus_field', 'field': field_key})

        # ── Enable toggle ─────────────────────────────────────────────────
        en_r = pygame.Rect(bx, y, 160, 28)
        en_bg = C['success'] if enabled else C['panel_light']
        en_bd = C['success'] if enabled else C['grid']
        pygame.draw.rect(screen, en_bg, en_r, border_radius=5)
        pygame.draw.rect(screen, en_bd, en_r, 2, border_radius=5)
        en_lbl = self.font_medium.render(
            "Mission ON" if enabled else "Mission OFF",
            True, C['panel'] if enabled else C['text_dim'])
        screen.blit(en_lbl, en_lbl.get_rect(center=en_r.center))
        self._dialogue_popup_rects.append({'rect': en_r, 'action': 'toggle_mission'})
        y += 36

        if not enabled:
            # Footer (confirm/cancel) when mission is off
            self._draw_popup_footer(screen, px, py + ph - 44, pw)
            return

        # ── Quest type cycle ──────────────────────────────────────────────
        qt       = m.get('quest_type', 'side')
        qt_colors = {'main': (255, 200, 50), 'side': (100, 200, 255), 'other': (180, 180, 180)}
        qt_col   = qt_colors.get(qt, C['accent'])
        qt_r     = pygame.Rect(bx, y, 140, 24)
        pygame.draw.rect(screen, C['panel_light'], qt_r, border_radius=4)
        pygame.draw.rect(screen, qt_col, qt_r, 2, border_radius=4)
        qt_lbl   = self.font_small.render(f"Type: {qt.capitalize()}  >>", True, qt_col)
        screen.blit(qt_lbl, qt_lbl.get_rect(center=qt_r.center))
        self._dialogue_popup_rects.append({'rect': qt_r, 'action': 'toggle_quest_type'})
        y += 32

        # ── Sequential toggle ─────────────────────────────────────────────
        seq    = m.get('sequential', False)
        seq_r  = pygame.Rect(bx, y, 140, 24)
        seq_bg = C['variant_selected'] if seq else C['panel']
        pygame.draw.rect(screen, seq_bg, seq_r, border_radius=4)
        pygame.draw.rect(screen, C['accent'] if seq else C['grid'], seq_r, 1, border_radius=4)
        sl = self.font_small.render("Sequential: " + ("YES" if seq else "NO"), True,
                                    C['text'] if seq else C['text_dim'])
        screen.blit(sl, sl.get_rect(center=seq_r.center))
        self._dialogue_popup_rects.append({'rect': seq_r, 'action': 'toggle_sequential'})
        y += 32

        # ── Objectives ────────────────────────────────────────────────────
        screen.blit(self.font_small.render("-- Objectives ------------------------------------------",
                                           True, C['text_dark']), (bx, y))
        y += 20
        for i, obj in enumerate(m.get('objectives', [])[:8]):
            obj_type = obj['type']
            row_x    = bx

            # Type cycle button
            type_r = pygame.Rect(row_x, y, 84, 24)
            pygame.draw.rect(screen, C['panel_light'], type_r, border_radius=3)
            pygame.draw.rect(screen, C['accent'], type_r, 1, border_radius=3)
            tl = self.font_small.render(obj_type, True, C['accent'])
            screen.blit(tl, tl.get_rect(center=type_r.center))
            self._dialogue_popup_rects.append({'rect': type_r, 'action': 'obj_cycle_type', 'index': i})
            row_x += 90

            # Param fields
            for (param_key, param_label, param_w) in self.OBJECTIVE_PARAM_FIELDS.get(obj_type, []):
                field_key = f'obj:{i}:{param_key}'
                val = str(obj['params'].get(param_key, ''))
                avail_w = min(param_w, bx + rw - row_x - 36)

                # ── Dropdown button ─────────────────────────────────────────
                if param_key in self.DROPDOWN_PARAMS:
                    disp = self._param_display_value(param_key, val)
                    is_open = (
                        self._open_dropdown is not None
                        and self._open_dropdown['obj_idx'] == i
                        and self._open_dropdown['param'] == param_key
                    )

                    # Label above
                    pl = self.font_small.render(param_label, True, C['text_dark'])
                    screen.blit(pl, (row_x + 2, y - 12 if y > content_top + 60 else y))

                    # Button body
                    btn_r = pygame.Rect(row_x, y, avail_w, 24)
                    bg    = C['panel_light'] if is_open else C['bg']
                    bd    = C['accent']      if is_open else C['grid']
                    pygame.draw.rect(screen, bg, btn_r, border_radius=3)
                    pygame.draw.rect(screen, bd, btn_r, 2 if is_open else 1, border_radius=3)

                    # Value text (clipped)
                    disp_trim = disp
                    max_text_w = avail_w - 20
                    while self.font_small.size(disp_trim)[0] > max_text_w and len(disp_trim) > 1:
                        disp_trim = disp_trim[:-1]
                    if disp_trim != disp:
                        disp_trim = disp_trim[:-1] + '…'
                    vt = self.font_small.render(disp_trim, True, C['text'] if val else C['text_dark'])
                    screen.blit(vt, (btn_r.x + 5, btn_r.y + 5))

                    # ▼ chevron
                    chev = self.font_small.render('v', True, C['accent'] if is_open else C['text_dark'])
                    screen.blit(chev, chev.get_rect(midright=(btn_r.right - 4, btn_r.centery)))

                    self._dialogue_popup_rects.append({
                        'rect': btn_r, 'action': 'obj_dropdown_open',
                        'index': i, 'param': param_key,
                        'anchor_x': btn_r.x, 'anchor_y': btn_r.bottom,
                        'width': avail_w,
                    })

                # ── Free-text field (count, item_id, etc.) ──────────────────
                else:
                    text_field(field_key, val, row_x, y, avail_w, label=None)
                    pl = self.font_small.render(param_label, True, C['text_dark'])
                    screen.blit(pl, (row_x + 2, y - 12 if y > content_top + 60 else y))

                row_x += param_w + 6
                if row_x > bx + rw - 36:
                    break

            # Description field (shown in journal next to quest icon)
            desc_key = f'obj:{i}:description'
            desc_val = obj.get('description', '')
            desc_avail_w = bx + rw - 28 - row_x - 6
            if desc_avail_w > 40:
                text_field(desc_key, desc_val, row_x, y, desc_avail_w, label="Journal text:")

            # Remove button
            rm_r = pygame.Rect(bx + rw - 28, y, 24, 24)
            pygame.draw.rect(screen, C['panel'], rm_r, border_radius=3)
            pygame.draw.rect(screen, C['danger'], rm_r, 1, border_radius=3)
            xs = self.font_small.render("x", True, C['danger'])
            screen.blit(xs, xs.get_rect(center=rm_r.center))
            self._dialogue_popup_rects.append({'rect': rm_r, 'action': 'obj_remove', 'index': i})
            y += 32

        # Add objective
        add_r = pygame.Rect(bx, y, 130, 24)
        pygame.draw.rect(screen, C['panel_light'], add_r, border_radius=4)
        pygame.draw.rect(screen, C['success'], add_r, 1, border_radius=4)
        al = self.font_small.render("+ Add Objective", True, C['success'])
        screen.blit(al, al.get_rect(center=add_r.center))
        self._dialogue_popup_rects.append({'rect': add_r, 'action': 'obj_add'})
        y += 32

        # ── Rewards ───────────────────────────────────────────────────────
        pygame.draw.line(screen, C['grid'], (bx, y), (bx + rw, y), 1)
        y += 8
        xp_val = str(m.get('rewards', {}).get('xp', 0))
        text_field('reward_xp', xp_val, bx, y, 80, label="XP Reward:")
        y += 32

        # ── Per-state dialogue strings ────────────────────────────────────
        screen.blit(self.font_small.render("-- State Dialogues --------------------------------------",
                                           True, C['text_dark']), (bx, y))
        y += 20
        dlg_states = [
            ('dlg_accepted',  'After Accept:'),
            ('dlg_active',    'While Active:'),
            ('dlg_completed', 'On Complete: '),
            ('dlg_rewarded',  'After Reward:'),
        ]
        for fkey, flabel in dlg_states:
            state_key = fkey[4:]
            val       = m.get('dialogues', {}).get(state_key, '')
            text_field(fkey, val, bx, y, rw - 2, label=flabel)
            y += 30

        # ── Footer ────────────────────────────────────────────────────────
        self._draw_popup_footer(screen, px, py + ph - 44, pw)

    def _draw_open_dropdown(self, screen):
        """Draw the floating list for the currently open dropdown."""
        dd = self._open_dropdown
        C  = self.COLORS
        options  = dd['options']
        scroll   = dd['scroll']
        ax, ay   = dd['anchor_x'], dd['anchor_y']
        w        = dd['width']
        row_h    = 22
        max_vis  = min(10, len(options))
        list_h   = max_vis * row_h + 4

        # Flip upward if list would go off screen
        if ay + list_h > self.screen_height - 10:
            ay = dd['anchor_y'] - dd['btn_h'] - list_h

        list_r = pygame.Rect(ax, ay, w, list_h)

        # Shadow
        shadow = pygame.Surface((w + 4, list_h + 4), pygame.SRCALPHA)
        shadow.fill((0, 0, 0, 100))
        screen.blit(shadow, (ax - 2, ay + 2))

        # Background + border
        pygame.draw.rect(screen, C['bg'],     list_r, border_radius=4)
        pygame.draw.rect(screen, C['accent'], list_r, 1, border_radius=4)

        # Clip to list area
        clip_r = pygame.Rect(ax + 1, ay + 2, w - 2, list_h - 4)
        old_clip = screen.get_clip()
        screen.set_clip(clip_r)

        mx, my = pygame.mouse.get_pos()

        for j, opt in enumerate(options[scroll: scroll + max_vis]):
            real_idx = scroll + j
            ry   = ay + 2 + j * row_h
            item_r = pygame.Rect(ax + 1, ry, w - 2, row_h)
            hovered = item_r.collidepoint(mx, my)
            bg = C['variant_selected'] if hovered else (C['panel_light'] if real_idx % 2 == 0 else C['bg'])
            pygame.draw.rect(screen, bg, item_r)

            disp = opt if opt else '(any)' if dd['param'] in ('enemy_id', 'room') else '—'
            trim = disp
            while self.font_small.size(trim)[0] > w - 12 and len(trim) > 1:
                trim = trim[:-1]
            if trim != disp:
                trim = trim[:-1] + '…'
            col = C['text'] if opt else C['text_dark']
            screen.blit(self.font_small.render(trim, True, col), (ax + 5, ry + 4))

        screen.set_clip(old_clip)

        # Scroll indicator
        if len(options) > max_vis:
            if scroll > 0:
                up = self.font_small.render('^', True, C['accent'])
                screen.blit(up, up.get_rect(midright=(ax + w - 4, ay + 10)))
            if scroll + max_vis < len(options):
                dn = self.font_small.render('v', True, C['accent'])
                screen.blit(dn, dn.get_rect(midright=(ax + w - 4, ay + list_h - 8)))

        # Register item rects for click handling (appended after tab rects so
        # they take priority — we process from the end in handle_event)
        for j in range(min(max_vis, len(options) - scroll)):
            real_idx = scroll + j
            ry = ay + 2 + j * row_h
            self._dialogue_popup_rects.append({
                'rect':   pygame.Rect(ax + 1, ry, w - 2, row_h),
                'action': 'obj_dropdown_select',
                'opt_idx': real_idx,
            })

    def _draw_popup_footer(self, screen, px, footer_y, pw):
        """Draw shared Confirm / Cancel buttons."""
        C = self.COLORS
        ok_r = pygame.Rect(px + pw - 220, footer_y, 96, 28)
        pygame.draw.rect(screen, C['panel_light'], ok_r, border_radius=4)
        pygame.draw.rect(screen, C['success'],     ok_r, 2, border_radius=4)
        ok_t = self.font_medium.render("Confirm", True, C['success'])
        screen.blit(ok_t, ok_t.get_rect(center=ok_r.center))
        self._dialogue_popup_rects.append({'rect': ok_r, 'action': 'confirm'})

        cl_r = pygame.Rect(px + pw - 114, footer_y, 96, 28)
        pygame.draw.rect(screen, C['panel_light'], cl_r, border_radius=4)
        pygame.draw.rect(screen, C['danger'],      cl_r, 2, border_radius=4)
        cl_t = self.font_medium.render("Cancel", True, C['danger'])
        screen.blit(cl_t, cl_t.get_rect(center=cl_r.center))
        self._dialogue_popup_rects.append({'rect': cl_r, 'action': 'cancel'})

    # =========================================================================
    # Preview ghost (drawn INTO the game world, not into the palette)
    # =========================================================================

    def draw_preview(self, screen, camera_x, camera_y):
        """
        Draw a semi-transparent ghost of the selected entity at the mouse
        position in world space.  Call this from the room-editor's world-draw
        pass (after tiles, before UI) so the ghost sits at the right depth.
        """
        if not self.active or not self.selected_entity:
            return

        from config.settings import RENDER_SCALE, TILE_SIZE

        mx, my = pygame.mouse.get_pos()

        if self._mouse_in_palette(mx, my):
            return

        world_x = (mx + camera_x) / RENDER_SCALE
        world_y = (my + camera_y) / RENDER_SCALE

        if self.grid_snap:
            world_x = round(world_x / TILE_SIZE) * TILE_SIZE
            world_y = round(world_y / TILE_SIZE) * TILE_SIZE

        sx = world_x * RENDER_SCALE - camera_x
        sy = world_y * RENDER_SCALE - camera_y

        variant = self.selected_variant or self._get_current_variant(self.selected_entity)
        sprite = variant['sprite'].copy() if variant and variant.get('sprite') else (
            self.selected_entity['sprite'].copy() if self.selected_entity.get('sprite') else None)

        if sprite:
            sprite.set_alpha(140)
            from config.settings import RENDER_SCALE
            ew = self.selected_entity['width'] * RENDER_SCALE
            eh = self.selected_entity['height'] * RENDER_SCALE
            sprite = pygame.transform.scale(sprite, (ew, eh))

            blocked = self._placement_blocked(world_x, world_y, self.selected_entity)
            if blocked:
                tint = pygame.Surface((ew, eh), pygame.SRCALPHA)
                tint.fill((200, 0, 0, 100))
                sprite.blit(tint, (0, 0))

            screen.blit(sprite, (int(sx - ew // 2), int(sy - eh // 2)))

            outline_color = (220, 50, 50) if blocked else self.COLORS['accent']
            pygame.draw.rect(screen, outline_color,
                             (int(sx - ew // 2), int(sy - eh // 2), ew, eh), 2)

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _is_rocket_launcher_selected(self):
        """Return True when the currently active variant is the rocket launcher."""
        variant = self.selected_variant or self._get_current_variant(self.selected_entity)
        return (
            self.selected_entity is not None
            and self.selected_entity.get('enemy_category') == 'shooter'
            and variant is not None
            and variant.get('type') == 'rocketlauncher'
        )

    def _mouse_in_palette(self, mx, my):
        if not self.palette_visible:
            return False
        return (self.palette_x <= mx <= self.palette_x + self.palette_width and
                self.palette_y <= my <= self.palette_y + self.palette_height)

    def _get_current_variant(self, entity):
        """Return the variant dict that should currently be active for *entity*."""
        if not entity or not entity.get('has_variants'):
            return None

        if entity is self.selected_entity and self.selected_variant:
            return self.selected_variant

        default_type = entity.get('default_variant')
        for v in entity.get('variants', []):
            if v['type'] == default_type:
                return v

        variants = entity.get('variants', [])
        return variants[0] if variants else None