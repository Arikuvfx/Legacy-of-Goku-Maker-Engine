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
        'disabled': (100, 100, 100),
        'variant_bg': (25, 25, 40),
        'variant_selected': (50, 150, 255),
        # Entity-category accent colours (used for preview sprites)
        'npc_color': (50, 150, 200),
        'enemy_color': (220, 60, 60),
        'boss_color': (180, 50, 180),
    }

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False

        # ── fonts (same sizes as ObjectEditor) ──────────────────────────────
        self.font_small = pygame.font.Font(None, 16)
        self.font_medium = pygame.font.Font(None, 20)
        self.font_large = pygame.font.Font(None, 24)

        # ── palette geometry (mirrors ObjectEditor exactly) ─────────────────
        self.palette_width = 600
        self.palette_x = screen_width - self.palette_width
        self.palette_y = 100  # below the toolbar
        self.palette_height = 940
        self.palette_padding = 10
        self.item_size = 80
        self.items_per_row = 3
        self.scroll_offset = 0

        # ── category definitions ─────────────────────────────────────────────
        # Keys match the vertical tab labels.  Order matters – it is the
        # render order top → bottom.
        self.category_keys = ['NPCs', 'Enemies', 'Enemy Bosses']

        # ── entity catalogue ─────────────────────────────────────────────────
        # Populated in _build_entity_catalogue().  Structure:
        #   { category_key: [ entity_dict, … ] }
        self.categories: dict[str, list[dict]] = {k: [] for k in self.category_keys}
        self._build_entity_catalogue()

        # ── editor state ─────────────────────────────────────────────────────
        self.current_category = self.category_keys[0]  # active tab
        self.selected_entity = None  # currently picked entity dict
        self.selected_variant = None  # currently picked variant dict (or None)
        self.hover_entity = None  # entity dict under mouse
        self.hover_variant_idx = -1  # variant index under mouse

        # ── placement preview ────────────────────────────────────────────────
        self.preview_x = 0
        self.preview_y = 0

        # ── grid snap (same toggle semantics as ObjectEditor) ───────────────
        self.grid_snap = True
        self.show_grid = True

        # ── AI type selection (for enemies and bosses) ──────────────────────
        self.ai_types = ['easy', 'advanced']
        self.selected_ai_type = 'easy'  # Default AI type
        self.hover_ai_type_idx = -1

        # ── hit-rect cache (rebuilt every frame in draw) ────────────────────
        self.ui_rects: dict[str, list] = {
            'category_rects': [],
            'entity_rects': [],
            'variant_rects': [],
            'ai_type_rects': [],
        }

        # ── hover animation weights (one per category tab) ──────────────────
        self.category_hover_anim = {k: 0.0 for k in self.category_keys}

        # ── placement callback (set by the room_editor) ─────────────────────
        # Signature: on_entity_placed(entity_dict, variant_dict | None, ai_type, world_x, world_y)
        self.on_entity_placed = None

        # ── obstacle list (set by room_editor so placement can validate) ────
        # Populated with the same list that game._assign_obstacles uses:
        # collision_objects + destructible_stones + level_gates + room_transitions
        self.placement_obstacles = []

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
        self.categories['NPCs'] = []

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
        Try to load the first frame of the idle-down row from the enemy's
        spritesheet.  Row 0 = down (4-directional layout).  Returns a
        Surface scaled to (w, h), or None if the asset is missing.
        """
        import os
        base = f"assets/sprites/enemies/{entity_id}"
        # Check variant subfolder, direct enemy folder, then boss subfolder
        candidates = [
            f"{base}/variants/{variant_type}/idle.png",
            f"{base}/idle.png",
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
            # Use the registered frame width (w) directly — avoids assuming
            # square frames (e.g. Pui Pui is 32x46, not square).
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

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mouse_pos = event.pos

            # ── AI type selector click ─────────────────────────────────────
            for entry in self.ui_rects.get('ai_type_rects', []):
                if entry['rect'].collidepoint(mouse_pos):
                    # Rocket launcher variant always uses easy AI - ignore clicks
                    if self._is_rocket_launcher_selected():
                        return True
                    self.selected_ai_type = entry['ai_type']
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
                self._place_entity(mouse_pos, camera_x, camera_y)
                return True

        # ── keyboard shortcuts ─────────────────────────────────────────────
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_g:
                self.grid_snap = not self.grid_snap
                return True
            if event.key == pygame.K_h:
                self.show_grid = not self.show_grid
                return True

        return False

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
            # Rocket launcher variant is always locked to easy AI
            if self._is_rocket_launcher_selected():
                ai_type = 'easy'
            else:
                ai_type = self.selected_ai_type

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

        # clear hit-rect cache each frame
        self.ui_rects = {'category_rects': [], 'entity_rects': [], 'variant_rects': [], 'ai_type_rects': []}

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
        Horizontal row of variant swatches – only visible when the selected
        entity has variants.  Mirrors ObjectEditor._draw_variant_selector.
        """
        if not self.selected_entity or not self.selected_entity.get('has_variants'):
            return

        variants = self.selected_entity.get('variants', [])
        if not variants:
            return

        strip_height = 90
        strip_y = self.palette_y + self.palette_height - 250
        strip_x = self.palette_x

        # background
        strip_rect = pygame.Rect(strip_x, strip_y, self.palette_width, strip_height)
        pygame.draw.rect(screen, self.COLORS['variant_bg'], strip_rect)
        pygame.draw.line(screen, self.COLORS['accent'],
                         (strip_x, strip_y),
                         (strip_x + self.palette_width, strip_y), 2)

        # label
        label = self.font_small.render("Select Variant:", True, self.COLORS['text_dim'])
        screen.blit(label, (strip_x + self.palette_padding, strip_y + 5))

        # swatch row
        swatch_size = 48
        swatch_gap = 8
        current_var = self.selected_variant or self._get_current_variant(self.selected_entity)
        sx = strip_x + self.palette_padding

        for i, variant in enumerate(variants):
            vx = sx + i * (swatch_size + swatch_gap)
            rect = pygame.Rect(vx, strip_y + 24, swatch_size, swatch_size)

            is_sel = (current_var is variant)
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
                # centre sprite inside swatch
                spr = variant['sprite']
                # scale down to fit if bigger than swatch
                max_dim = swatch_size - 6
                sw, sh = spr.get_size()
                if sw > max_dim or sh > max_dim:
                    scale = min(max_dim / sw, max_dim / sh)
                    spr = pygame.transform.scale(spr, (int(sw * scale), int(sh * scale)))
                screen.blit(spr, spr.get_rect(center=rect.center))
            else:
                # plain colour block
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

        # instructions
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

        # don't draw ghost while hovering the palette itself
        if self._mouse_in_palette(mx, my):
            return

        world_x = (mx + camera_x) / RENDER_SCALE
        world_y = (my + camera_y) / RENDER_SCALE

        if self.grid_snap:
            world_x = round(world_x / TILE_SIZE) * TILE_SIZE
            world_y = round(world_y / TILE_SIZE) * TILE_SIZE

        # screen position of the snapped world point
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

            # Red tint when placement is blocked by an obstacle
            blocked = self._placement_blocked(world_x, world_y, self.selected_entity)
            if blocked:
                tint = pygame.Surface((ew, eh), pygame.SRCALPHA)
                tint.fill((200, 0, 0, 100))
                sprite.blit(tint, (0, 0))

            screen.blit(sprite, (int(sx - ew // 2), int(sy - eh // 2)))

            # outline: red when blocked, gold when clear
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
        return (self.palette_x <= mx <= self.palette_x + self.palette_width and
                self.palette_y <= my <= self.palette_y + self.palette_height)

    def _get_current_variant(self, entity):
        """Return the variant dict that should currently be active for *entity*."""
        if not entity or not entity.get('has_variants'):
            return None

        # if this entity is the one selected and we already have a variant chosen
        if entity is self.selected_entity and self.selected_variant:
            return self.selected_variant

        # otherwise fall back to default
        default_type = entity.get('default_variant')
        for v in entity.get('variants', []):
            if v['type'] == default_type:
                return v

        variants = entity.get('variants', [])
        return variants[0] if variants else None