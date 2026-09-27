import pygame
import pygame.gfxdraw
import os

from dev_tools import entity_creator
import dev_tools.ui_kit as uk


# =============================================================================
# Small vector glyph used by the panel show/hide tab — same primitive
# convention (draw_line_on) as ui_kit / tileset_editor.py's own copy.
# =============================================================================

def _draw_chevron_icon(surface, rect, color, left=True, width=2):
    """Small left/right chevron — the panel show/hide tab. Same primitive
    convention (draw_line_on) as tileset_editor.py's own copy of this glyph."""
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.28
    if left:
        uk.draw_line_on(surface, color, (cx + s * 0.5, cy - s), (cx - s * 0.5, cy), width)
        uk.draw_line_on(surface, color, (cx - s * 0.5, cy), (cx + s * 0.5, cy + s), width)
    else:
        uk.draw_line_on(surface, color, (cx - s * 0.5, cy - s), (cx + s * 0.5, cy), width)
        uk.draw_line_on(surface, color, (cx + s * 0.5, cy), (cx - s * 0.5, cy + s), width)


# Accent colours used for entity-editor preview sprites, keyed by
# entity_type — mirrors EntityEditor.CATEGORY_META['Enemies']/['Enemy Bosses'] accents.
_ENEMY_VARIANT_COLOR = (220, 60, 60)
_BOSS_VARIANT_COLOR  = (180, 50, 180)


def _build_enemy_catalogue():
    """Standalone builder for the 'Enemies' / 'Enemy Bosses' portions of the
    entity catalogue, factored out of EntityEditor._build_entity_catalogue()
    so the roster can be read without instantiating a full EntityEditor
    (which needs a live pygame display / screen size). EntityEditor itself
    calls this too, so there's a single source of truth for what enemies
    exist — see discover_enemy_ids() below for the id-only view other
    modules (e.g. the event editor's spawn_enemies action) actually want.

    Sourced live from assets/enemies/{id}.json via entity_creator — the
    same data the entity creator dev tool writes. There is no hardcoded
    roster anymore: dropping a sprite folder under assets/sprites/enemies/
    and/or saving a config in the entity creator is all it takes for an
    enemy or boss to show up here. entity_type ('enemy' | 'boss') on each
    config decides which of the two categories it lands in.
    """
    catalogue = {'Enemies': [], 'Enemy Bosses': []}

    for entity_id in entity_creator.discover_all_ids(entity_creator.KIND_ENEMY):
        cfg = entity_creator.load_config(entity_creator.KIND_ENEMY, entity_id)
        is_boss = cfg.get('entity_type') == 'boss'
        color = _BOSS_VARIANT_COLOR if is_boss else _ENEMY_VARIANT_COLOR

        variant_ids = entity_creator.scan_variants(entity_creator.KIND_ENEMY, entity_id)
        variants = [
            {
                'type': v,
                'name': 'Default' if v == 'default' else v.replace('_', ' ').title(),
                'color': color,
            }
            for v in variant_ids
        ]

        entry = {
            'id': entity_id,
            'name': cfg.get('display_name') or entity_id.replace('_', ' ').title(),
            'sprite': None,
            'width': cfg.get('width', 32), 'height': cfg.get('height', 32),
            'entity_type': cfg.get('entity_type', 'enemy'),
            'enemy_category': cfg.get('enemy_category', 'melee'),
            'has_variants': True,
            'variants': variants,
            'default_variant': 'default',
        }
        catalogue['Enemy Bosses' if is_boss else 'Enemies'].append(entry)

    for entries in catalogue.values():
        entries.sort(key=lambda e: e['name'])

    return catalogue


def discover_enemy_ids():
    """All placeable enemy/boss ids (regular enemies + bosses), sourced from
    the same catalogue EntityEditor's palette uses (_build_enemy_catalogue()
    above), so this list never drifts out of sync with what actually exists.
    Used by other dev tools (e.g. the event editor's spawn_enemies action
    picker) that need the enemy roster without pulling in a full
    EntityEditor instance. Returns a sorted list, deduped, no 'any' blank
    entry (unlike EntityEditor._get_enemy_ids(), which prefixes one for its
    own filter dropdown) — callers that want a blank/"none" option can add
    their own placeholder."""
    catalogue = _build_enemy_catalogue()
    ids = set()
    for cat_key in ('Enemies', 'Enemy Bosses'):
        for entity in catalogue.get(cat_key, []):
            eid = entity.get('id', '')
            if eid:
                ids.add(eid)
    return sorted(ids)


def discover_boss_ids():
    """Boss-only ids, sourced from the same catalogue as discover_enemy_ids()
    above but filtered to entity_type == 'boss' (the 'Enemy Bosses' category)
    — i.e. it deliberately excludes regular (non-boss) enemies, unlike
    discover_enemy_ids()'s combined list. Used by the event editor's Boss HP
    condition picker: BossEnemy.boss_id (see entities/boss_enemy.py) only
    ever gets set for entries in this narrower list, so a regular enemy id
    wouldn't ever match a live boss_hp_lookup/boss_hp_value_lookup anyway.
    Returns a sorted, deduped list."""
    catalogue = _build_enemy_catalogue()
    ids = {entity.get('id', '') for entity in catalogue.get('Enemy Bosses', [])
           if entity.get('id') and entity.get('entity_type') == 'boss'}
    return sorted(ids)


NPC_SPRITES_ROOT = 'assets/sprites/npc'
_NPC_VARIANT_COLOR = (50, 150, 200)


def _build_npc_catalogue():
    """Standalone builder for the 'NPCs' portion of the entity catalogue,
    factored out of EntityEditor._build_entity_catalogue() for the same
    reason as _build_enemy_catalogue() above: so the roster can be read
    without instantiating a full EntityEditor.

    Sourced live from entity_creator: discover_all_ids() unions every
    sprite-folder id under assets/sprites/npc/ with every id that has a
    saved assets/npcs/{id}.json, so a new folder OR a new config is enough
    to show up here — same "no code changes required" convention the old
    sprite-only scan had, just backed by the shared discovery/config
    helpers instead of a local re-implementation. display_name and the
    default dialogue (used to seed the placement popup) come straight from
    the saved config. Falls back to a single placeholder 'generic' entry
    if nothing is configured yet, so the palette is never empty."""
    npc_ids = entity_creator.discover_all_ids(entity_creator.KIND_NPC)
    if not npc_ids:
        npc_ids = ['generic']

    npcs = []
    for npc_id in npc_ids:
        cfg = entity_creator.load_config(entity_creator.KIND_NPC, npc_id)

        variant_ids = entity_creator.scan_variants(entity_creator.KIND_NPC, npc_id)
        variants = [
            {
                'type': v,
                'name': 'Default' if v == 'default' else v.replace('_', ' ').title(),
                'color': _NPC_VARIANT_COLOR,
            }
            for v in variant_ids
        ]

        display_name = cfg.get('display_name') or (
            'Generic NPC' if npc_id == 'generic' else npc_id.replace('_', ' ').title()
        )

        npcs.append({
            'id': npc_id,
            'name': display_name,
            'sprite': None,
            'width': cfg.get('width', 32), 'height': cfg.get('height', 32),
            'entity_type': 'npc',
            'has_variants': True,
            # Seeds the placement popup's dialogue list — see the
            # 'world click -> place entity' NPC branch in handle_event().
            'default_dialogue_config': dict(cfg.get('dialogue', {})),
            'variants': variants,
            'default_variant': 'default',
        })

    npcs.sort(key=lambda e: e['name'])
    return {'NPCs': npcs}


_CRITTER_VARIANT_COLOR = (120, 190, 90)
# Legacy per-species accents for the three built-in critters, kept purely
# for continuity with how they used to look before this catalogue was
# data-driven. Any critter_type not in here (including new custom
# critters made in entity_creator) just gets the generic accent above.
_CRITTER_TYPE_COLORS = {
    'squirrel':  (150, 100, 60),
    'bird':      (100, 150, 220),
    'butterfly': (230, 150, 200),
}


def _build_critter_catalogue():
    """Standalone builder for the 'Critters' portion of the entity catalogue,
    factored out the same way _build_enemy_catalogue() / _build_npc_catalogue()
    are, so the roster can be read without instantiating a full EntityEditor.

    Sourced live from entity_creator: discover_all_ids() unions every
    sprite-folder id under assets/sprites/critters/ with every id that has a
    saved assets/critters/{id}.json — so a critter created (and saved) in
    entity_creator shows up here automatically instead of only the three
    hardcoded squirrel/bird/butterfly entries. Falls back to those three ids
    if nothing is discovered at all, so the palette is never empty."""
    critter_ids = entity_creator.discover_all_ids(entity_creator.KIND_CRITTER)
    if not critter_ids:
        critter_ids = ['squirrel', 'bird', 'butterfly']

    critters = []
    for critter_id in critter_ids:
        cfg = entity_creator.load_config(entity_creator.KIND_CRITTER, critter_id)

        variant_ids = entity_creator.scan_variants(entity_creator.KIND_CRITTER, critter_id)
        color = _CRITTER_TYPE_COLORS.get(cfg.get('critter_type', ''), _CRITTER_VARIANT_COLOR)
        variants = [
            {
                'type': v,
                'name': 'Default' if v == 'default' else v.replace('_', ' ').title(),
                'color': color,
            }
            for v in variant_ids
        ]

        display_name = cfg.get('display_name') or critter_id.replace('_', ' ').title()

        critters.append({
            'id': critter_id,
            'name': display_name,
            'sprite': None,
            'width': cfg.get('width', 16), 'height': cfg.get('height', 16),
            'entity_type': 'critter',
            'has_variants': True,
            'variants': variants,
            'default_variant': 'default',
        })

    critters.sort(key=lambda e: e['name'])
    return {'Critters': critters}


def discover_critter_ids():
    """All placeable critter ids, sourced from the same catalogue
    EntityEditor's palette uses (_build_critter_catalogue() above), so this
    list never drifts out of sync with what actually exists. Mirrors
    discover_npc_ids()/discover_enemy_ids() for other dev tools that want
    the critter roster without pulling in a full EntityEditor instance."""
    catalogue = _build_critter_catalogue()
    ids = {entity.get('id', '') for entity in catalogue.get('Critters', []) if entity.get('id')}
    return sorted(ids)


def discover_npc_ids():
    """All placeable NPC ids, sourced from the same catalogue EntityEditor's
    palette uses (_build_npc_catalogue() above), so this list never drifts
    out of sync with what actually exists. Used by other dev tools (e.g.
    the event editor's spawn_npc action) that need the NPC roster without
    pulling in a full EntityEditor instance. Returns a sorted, deduped list
    reflecting whatever folders currently exist under assets/sprites/npc/
    (or just ['generic'] if that folder is empty/missing)."""
    catalogue = _build_npc_catalogue()
    ids = {entity.get('id', '') for entity in catalogue.get('NPCs', []) if entity.get('id')}
    return sorted(ids)


class EntityEditor:
    """
    Editor for placing NPCs, Enemies, Bosses and Critters in a room.

    Re-skinned to match the engine's shared "modern DBZ" dev-tool visual
    language (dev_tools/ui_kit.py + dev_menu.py + editor_toolbar.py +
    tileset_editor.py): flat navy panels, gold/ki-blue accents, hairline
    borders that light up on hover, BitmapFont labels and vector line-icons
    instead of the old default-pygame-font look. Only the *look* changed —
    every public method/attribute a caller (RoomEditor) already depends on
    keeps its old name and behaviour. See CATEGORY_META for the per-category
    icon + accent used by the category tabs and entity-grid accents.

    Layout mirrors ObjectEditor's panel geometry:
        - Right-side palette panel (same width, position, padding).
        - Category tabs stacked vertically: NPCs | Enemies | Enemy Bosses | Critters.
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
    # Per-category accent, used on the category tabs, the entity-grid
    # selection glow and the variant-selector highlight. Same shape as
    # dev_menu.py's CATEGORIES table (minus the icon — this panel doesn't
    # use generated category icons), so a new category is a one-line
    # addition here rather than a scattered set of color literals.
    # ---------------------------------------------------------------------------
    CATEGORY_META = {
        'NPCs':         {'accent': uk.Theme.GOLD},
        'Enemies':      {'accent': uk.Theme.GOLD},
        'Enemy Bosses': {'accent': uk.Theme.GOLD},
        'Critters':     {'accent': uk.Theme.GOLD},
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

        # Continuous editor zoom (Ctrl+scroll), kept in sync by RoomEditor
        # each frame. Click placement gets its position pre-converted by
        # RoomEditor._zoom_adjust_event(), but draw_preview reads the live
        # cursor directly and has to do that conversion itself, using this.
        self.editor_zoom = 1.0

        # Same bitmap font family + Theme colors as DevMenu / EditorToolbar /
        # TilesetEditor's own chrome, so this panel reads as part of the same
        # tool family instead of a mismatched leftover panel.
        self.font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self.title_size = 16
        self.body_size = 11
        self.hint_size = 11  # matches ObjectEditor's font_small (11) used for its palette/label text
        self.label_size = 10

        # A couple of accents that don't have a direct Theme constant, kept
        # local the same way tileset_editor.py keeps its own SUCCESS/SELECTION.
        self.SUCCESS = (140, 220, 140)

        # Keybinds reference popup — toggled by the '?' info button next to
        # the title, same convention as ObjectEditor / TilesetEditor. Set
        # each frame in _draw_title(); checked in handle_event().
        self.show_keybinds_popup = False
        self._info_button_rect = None

        # Optional custom icon for the info ('?') badge — same PNG-override
        # convention as EditorToolbar (assets/ui/toolbar/<id>.png): drop a
        # PNG there and it replaces the procedural circle+'?' mark below.
        self._info_icon = None
        try:
            _info_img = pygame.image.load('assets/ui/toolbar/info.png').convert_alpha()
            _iw, _ih = _info_img.get_size()
            _scale = min(18 / _iw, 18 / _ih)
            self._info_icon = pygame.transform.scale(
                _info_img, (max(1, int(_iw * _scale)), max(1, int(_ih * _scale))))
        except Exception:
            pass

        # Palette-thumbnail / label render caches — same reasoning as
        # tileset_editor's per-frame text caching: this panel redraws a lot
        # of small bitmap-font labels and scaled sprite thumbnails every
        # frame it's open, so cache the results instead of rebuilding them.
        self._palette_thumb_cache = {}
        self._palette_label_cache = {}
        self._label_fit_cache = {}

        self.palette_width = 600
        self.palette_x = screen_width - self.palette_width
        self.palette_y = 100  # below the toolbar
        self.palette_height = 940
        self.palette_padding = 10
        self.item_size = 80
        self.item_gap = 10
        available_w = self.palette_width - self.palette_padding * 2
        self.items_per_row = max(1, (available_w + self.item_gap) // (self.item_size + self.item_gap))
        self.scroll_offset = 0

        # Tab order also controls top-to-bottom render order
        self.category_keys = ['NPCs', 'Enemies', 'Enemy Bosses', 'Critters']

        # Populated by _build_entity_catalogue(); structure: {category: [entity_dict, ...]}
        self.categories: dict[str, list[dict]] = {k: [] for k in self.category_keys}
        self._build_entity_catalogue()

        self.current_category = self.category_keys[0]
        self.selected_entity = None
        self.selected_variant = None
        self.hover_entity = None
        self.hover_variant_idx = -1
        self.variant_scroll = 0            # index of first visible variant row, for long variant lists
        self._variant_selector_rect = None  # set each frame in _draw_variant_selector; used for wheel routing

        self.preview_x = 0
        self.preview_y = 0

        self.grid_snap = True
        self.show_grid = True

        # ── NPC settings ─────────────────────────────────────────────────────
        # AI type and zeni drop pool used to be set per-placement here, but
        # both now live entirely in the entity creator config (ai_type /
        # zeni_pool on assets/enemies/{id}.json) — see entity_creator.py.
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
            'npc_mode_rects': [],
            'npc_facing_rects': [],
            'npc_dialogue_rects': [],
        }

        self.category_hover_anim = {k: 0.0 for k in self.category_keys}

        # Signature: on_entity_placed(entity_dict, variant|None, ai_type, world_x, world_y)
        # ai_type is now always None here -- callbacks should read it from the
        # entity's own config (entity_creator) instead.
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

        # Slide animation for the panel opening/closing — chased toward
        # 1.0 (fully open) or 0.0 (fully closed) each frame, same pattern
        # as TilesetEditor/ObjectEditor's palette panels. Advanced from
        # inside draw() (timed off real elapsed ms) since this widget has
        # no separate per-frame update(dt) hook of its own.
        self._panel_slide_anim    = 1.0 if self.palette_visible else 0.0
        self._panel_slide_last_ms = None
        self._PANEL_SLIDE_RATE    = 9.0

    # =========================================================================
    # Entity catalogue
    # =========================================================================

    @staticmethod
    def _resolve_critter_frame_size(critter_id, variant_type, default_w, default_h):
        """Read {folder}/sprite_size.txt for a critter, checking the variant
        folder first then the flat folder — same lookup order sprite_system's
        CritterSpriteLoader uses at runtime. Falls back to (default_w,
        default_h) if no sprite_size.txt exists, so a critter with no config
        file yet still gets a sane placeholder size.
        """
        import os
        candidates = [
            f"assets/sprites/critters/{critter_id}/variants/{variant_type}/sprite_size.txt",
            f"assets/sprites/critters/{critter_id}/sprite_size.txt",
        ]
        for path in candidates:
            if os.path.isfile(path):
                try:
                    with open(path) as f:
                        text = f.read().strip().lower()
                    w, h = text.split('x')
                    return int(w), int(h)
                except Exception:
                    pass
        return default_w, default_h

    def _build_entity_catalogue(self):
        """
        Populate self.categories with the master list of placeable entities.

        Adding a new entity in the future is a single-dict append.  Variants
        are just colour definitions for now; swap in sprite paths later the
        same way ObjectEditor does for stones / gates.
        """

        # ── NPCs ─────────────────────────────────────────────────────────────
        # Sourced from the module-level _build_npc_catalogue() so this
        # roster and discover_npc_ids() (used by e.g. the event editor's
        # spawn_npc picker) can never drift apart.
        self.categories['NPCs'] = _build_npc_catalogue()['NPCs']

        # ── Enemies / Enemy Bosses ──────────────────────────────────────────
        # Sourced from the module-level _build_enemy_catalogue() so this
        # roster and discover_enemy_ids() (used by e.g. the event editor's
        # spawn_enemies picker) can never drift apart.
        enemy_catalogue = _build_enemy_catalogue()
        self.categories['Enemies'] = enemy_catalogue['Enemies']
        self.categories['Enemy Bosses'] = enemy_catalogue['Enemy Bosses']

        # ── Critters (ambient wildlife — no hitbox, no AI, no dialogue) ────────
        # Sourced from the module-level _build_critter_catalogue() so this
        # roster and discover_critter_ids() can never drift apart, and so
        # critters created in entity_creator (beyond the original built-in
        # squirrel/bird/butterfly) actually show up here.
        self.categories['Critters'] = _build_critter_catalogue()['Critters']

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
                        entity['width'], entity['height'],
                        entity.get('entity_type', '')
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
    def _load_idle_down_sprite(entity_id, variant_type, w, h, entity_type=''):
        """
        Try to load the first frame of the idle-down row from the entity's
        spritesheet.  Checks NPC paths first, then enemy/boss paths.
        Returns a Surface scaled to (w, h), or None if the asset is missing.

        Bosses (entity_type == 'boss') live in assets/sprites/enemies/boss/
        instead of the flat enemies/ root — only searched there when the
        config actually says boss, not as a blind fallback for every enemy.
        """
        import os
        enemy_root = "assets/sprites/enemies/boss" if entity_type == 'boss' else "assets/sprites/enemies"
        candidates = [
            # NPC paths
            f"assets/sprites/npc/{entity_id}/variants/{variant_type}/idle.png",
            f"assets/sprites/npc/{entity_id}/idle.png",
            # Enemy / boss paths
            f"{enemy_root}/{entity_id}/variants/{variant_type}/idle.png",
            f"{enemy_root}/{entity_id}/idle.png",
            # Critter paths — idle.png first, falling back to flying.png since
            # butterflies (and similar always-airborne critters) have no idle.
            f"assets/sprites/critters/{entity_id}/variants/{variant_type}/idle.png",
            f"assets/sprites/critters/{entity_id}/idle.png",
            f"assets/sprites/critters/{entity_id}/variants/{variant_type}/flying.png",
            f"assets/sprites/critters/{entity_id}/flying.png",
        ]
        path = next((p for p in candidates if os.path.exists(p)), None)
        if path is None:
            return None
        try:
            sheet = pygame.image.load(path).convert_alpha()
            sheet_w = sheet.get_width()
            sheet_h = sheet.get_height()
            # flying.png sheets (butterflies, birds) are always 8-directional;
            # everything else on these candidate paths is the 4-dir layout.
            num_rows = 8 if path.endswith('flying.png') else 4
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

        elif entity_type == 'critter':
            # Small, soft, no outline — reads as "harmless ambient wildlife"
            # rather than something placed to fight or talk to.
            cx, cy = w // 2, h // 2
            r = max(2, min(w, h) // 3)
            pygame.draw.circle(surf, color, (cx, cy), r)
            pygame.draw.circle(surf, light, (cx, cy), max(1, r // 2))

        return surf

    # =========================================================================
    # Public API  (called by the room editor / toolbar)
    # =========================================================================

    def toggle(self):
        """Open or close the entity editor (mirrors ObjectEditor.toggle).

        Rebuilds the catalogue on open (not just once in __init__) so any
        enemy/boss/NPC saved via the entity creator since the room editor
        started shows up immediately, instead of requiring an app restart.
        """
        self.active = not self.active
        if self.active:
            self._build_entity_catalogue()
            self.selected_entity = None
            self.selected_variant = None
            self.scroll_offset = 0
            self.variant_scroll = 0

    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # Panel show/hide tab
    # -------------------------------------------------------------------------

    def _panel_toggle_rect(self):
        """Return the rect for the show/hide tab that straddles the panel's
        left edge. Tracks the animated slide (_panel_slide_anim), not just
        the instant palette_visible flag, so the tab visually stays glued
        to the panel's edge as it slides in/out instead of snapping
        straight to its new spot — same as TilesetEditor/ObjectEditor's
        tabs. This also doubles as the click/hover hit-rect, which is what
        we want: the tab should be clickable where it's actually drawn.
        self.palette_x here is always the panel's settled resting position
        (draw() restores it after each shifted draw), so this is stable to
        call from anywhere, animating or not."""
        gap = 6
        tx_shown  = self.palette_x - self._panel_tab_w - gap
        tx_hidden = self.screen_width - self._panel_tab_w
        tx = round(uk.lerp(tx_hidden, tx_shown, self._panel_slide_anim))
        ty = self.palette_y + (self.palette_height - self._panel_tab_h) // 2
        return pygame.Rect(tx, ty, self._panel_tab_w, self._panel_tab_h)

    def _draw_panel_toggle_tab(self, screen):
        """Render the small show/hide tab — always visible so the panel can
        be recalled. Same flat panel + vector chevron treatment as
        tileset_editor.py's own copy of this control."""
        rect = self._panel_toggle_rect()
        lit = self._hover_panel_toggle
        bg = uk.lerp_color((22, 25, 35), (30, 34, 46), 1.0 if lit else 0.0)
        border = uk.Theme.GOLD if lit else uk.Theme.PANEL_BORDER
        uk.draw_panel(screen, rect, bg=(*bg, 235), border=border, border_width=1,
                      radius=6, shadow=False)
        chevron_color = uk.Theme.GOLD_BRIGHT if lit else uk.Theme.TEXT_MUTED
        _draw_chevron_icon(screen, rect, chevron_color, left=self.palette_visible, width=2)

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
            mouse_pos = pygame.mouse.get_pos()
            # If the cursor is over the variant selector strip, scroll its
            # rows instead of scrolling the main entity grid beneath it.
            if (self._variant_selector_rect is not None
                    and self._variant_selector_rect.collidepoint(mouse_pos)
                    and self.selected_entity and self.selected_entity.get('has_variants')):
                variants = self.selected_entity.get('variants', [])
                if variants:
                    cols = self._variant_items_per_row()
                    total_rows = (len(variants) + cols - 1) // cols
                    visible_rows = min(total_rows, 3)
                    max_row_scroll = max(0, total_rows - visible_rows)
                    self.variant_scroll -= event.y
                    self.variant_scroll = max(0, min(self.variant_scroll, max_row_scroll))
                return True
            if self._mouse_in_palette(*mouse_pos):
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

            if self._info_button_rect is not None and self._info_button_rect.collidepoint(mouse_pos):
                self.show_keybinds_popup = not self.show_keybinds_popup
                return True

            # While the keybinds popup is open, any other click just closes
            # it — nothing underneath (palette, dialogue popup, or world)
            # should react to a click that was really the person dismissing
            # the popup.
            if self.show_keybinds_popup:
                self.show_keybinds_popup = False
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
                    return True

            # ── category tab click ─────────────────────────────────────────
            for entry in self.ui_rects.get('category_rects', []):
                if entry['rect'].collidepoint(mouse_pos):
                    self.current_category = entry['key']
                    self.selected_entity = None
                    self.selected_variant = None
                    self.scroll_offset = 0
                    self.variant_scroll = 0
                    return True

            # ── entity item click ──────────────────────────────────────────
            for entry in self.ui_rects.get('entity_rects', []):
                if entry['rect'].collidepoint(mouse_pos):
                    self.selected_variant = None  # clear old variant before switching entity
                    self.selected_entity = entry['entity']
                    self.selected_variant = self._get_current_variant(entry['entity'])
                    self.variant_scroll = 0
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
                        default_cfg = self.selected_entity.get('default_dialogue_config') or {}
                        default_dialogues = list(default_cfg.get('dialogues') or [''])
                        self._dialogue_popup = {
                            'dialogues': default_dialogues,
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
        return [''] + discover_enemy_ids()

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

        # AI type and zeni drop pool are no longer set here -- both are read
        # straight from the entity's entity_creator config (ai_type / zeni_pool
        # in assets/enemies/{id}.json) when the enemy/boss is actually spawned.
        # ai_type stays as a callback arg only for signature compatibility below.
        ai_type = None

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

    def draw(self, screen):
        """Render the entire palette panel.  Call once per frame when active."""
        if not self.active:
            return

        mx, my = getattr(self, '_logical_mouse_pos', pygame.mouse.get_pos())

        # In popup-only mode (editing an existing NPC) skip the palette entirely
        if self._popup_only_mode:
            self._info_button_rect = None  # button isn't drawn — its click-zone shouldn't be live
            self._variant_selector_rect = None  # ditto — not drawn, so its wheel-routing zone shouldn't be live
            if self._dialogue_popup is not None:
                self._draw_dialogue_popup(screen)
            uk.update_hover_cursor((mx, my))
            return

        # Update hover state and always draw the toggle tab
        self._hover_panel_toggle = self._panel_toggle_rect().collidepoint(mx, my)
        uk.register_hoverable(self._panel_toggle_rect())

        # Advance the panel's open/close slide toward its target (chased,
        # not a fixed-duration tween — same style as TilesetEditor/
        # ObjectEditor's panels and EditorToolbar's bar).
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

        # Keep drawing the panel for as long as it's still sliding, even
        # after palette_visible has already flipped to False — otherwise
        # it would vanish instantly the moment the tab is clicked, before
        # the slide even starts. palette_visible itself still flips
        # immediately (unchanged), so anything that reads it for hit-
        # testing is unaffected — only the drawn frame lags behind.
        if self._panel_slide_anim <= 0.001:
            self._info_button_rect = None  # button isn't drawn — its click-zone shouldn't be live
            self._variant_selector_rect = None  # ditto — not drawn, so its wheel-routing zone shouldn't be live
            # Still draw the dialogue popup even when the palette is hidden
            if self._dialogue_popup is not None:
                self._draw_dialogue_popup(screen)
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

        # clear hit-rect cache each frame
        self.ui_rects = {'category_rects': [], 'entity_rects': [], 'variant_rects': [], 'npc_mode_rects': [], 'npc_facing_rects': [], 'npc_dialogue_rects': []}

        self._draw_palette_background(screen)
        self._draw_title(screen)
        self._draw_category_tabs(screen)
        y_after_tabs = self._category_tabs_bottom_y()

        # Lay the entity grid, variant selector and settings panel out
        # bottom-up, each sized to exactly what it needs this frame (0
        # means "don't draw it at all") — same pattern as ObjectEditor's
        # draw_palette, and what fixes the variant strip ending up
        # overlapping the grid instead of sitting flush at the bottom.
        settings_height = self._settings_panel_content_height()
        variant_height = self._variant_selector_content_height()
        reserved_bottom = settings_height + variant_height
        content_height = max(
            100, self.palette_height - (y_after_tabs - self.palette_y) - reserved_bottom)

        self._draw_entity_grid(screen, y_after_tabs, content_height)

        settings_y = self.palette_y + self.palette_height - settings_height
        variant_y = settings_y - variant_height
        self._draw_variant_selector(screen, variant_y, variant_height)
        self._draw_settings_panel(screen, settings_y, settings_height)

        # ── Dialogue popup overlay ──────────────────────────────────────────
        if self._dialogue_popup is not None:
            self._draw_dialogue_popup(screen)

        # Drawn last so it floats above the palette, the dialogue popup and
        # everything else — same convention as ObjectEditor / TilesetEditor.
        self._draw_keybinds_popup(screen)

        self.palette_x -= dx  # restore — the shift above was only for this draw pass

        # Resolve the frame's cursor last, now that every clickable widget
        # drawn above (category tabs, entity cards, variant swatches, chips,
        # text boxes, dropdown buttons/lists, info button, toggle tab) has
        # had a chance to register itself via register_hoverable.
        uk.update_hover_cursor((mx, my))


    # =========================================================================
    # Drawing helpers — shared small widgets
    # =========================================================================

    def _draw_chip(self, screen, rect, label, selected=False, hover=False,
                   disabled=False, danger=False, success=False):
        """Small pill/chip button — the one shared visual for every toggle,
        tab and action button in this editor (category tabs excluded, which
        need an icon slot too and are drawn inline in _draw_category_tabs)."""
        if disabled:
            bg, border, txt, bw = uk.Theme.PANEL_BG, uk.Theme.PANEL_BORDER, uk.Theme.TEXT_DIM, 1
        elif danger:
            bg = uk.Theme.CARD_BG_HOVER if hover else uk.Theme.CARD_BG
            border, txt, bw = uk.Theme.DANGER_BRIGHT, uk.Theme.DANGER_BRIGHT, 2
        elif success:
            bg = uk.Theme.CARD_BG_HOVER if hover else uk.Theme.CARD_BG
            border, txt, bw = self.SUCCESS, self.SUCCESS, 2
        elif selected:
            bg, border, txt, bw = uk.Theme.CARD_BG_SELECTED, uk.Theme.GOLD, uk.Theme.TEXT_PRIMARY, 2
        elif hover:
            bg, border, txt, bw = uk.Theme.CARD_BG_HOVER, uk.Theme.GOLD, uk.Theme.TEXT_SECONDARY, 1
        else:
            bg, border, txt, bw = uk.Theme.CARD_BG, uk.Theme.CARD_BORDER, uk.Theme.TEXT_MUTED, 1
        uk.draw_rect_on(screen, bg, rect, 0, 5)
        uk.draw_rect_on(screen, border, rect, bw, 5)
        if not disabled:
            uk.register_hoverable(rect)
        label_h = min(self.hint_size, rect.h - 6)
        while label_h > 6 and self.font.size(label, height=label_h)[0] > rect.w - 8:
            label_h -= 1
        label_s = self.font.render(label, color=txt, height=max(6, label_h))
        uk.blit_surface(screen, label_s, label_s.get_rect(center=rect.center), transient=True)

    def _draw_text_box(self, screen, rect, text, active):
        """Single-line editable text field — dialogue lines, mission fields,
        objective params. Trailing '_' caret matches the original editor's
        own (non-blinking) active-field convention."""
        bg = uk.Theme.CARD_BG_HOVER if active else uk.Theme.CARD_BG
        border = uk.Theme.GOLD if active else uk.Theme.PANEL_BORDER
        uk.draw_rect_on(screen, bg, rect, 0, 4)
        uk.draw_rect_on(screen, border, rect, 2 if active else 1, 4)

        display = text + ("_" if active else "")
        color = uk.Theme.TEXT_PRIMARY if (text or active) else uk.Theme.TEXT_DIM
        text_h = min(self.body_size, max(7, rect.h - 8))
        max_w = rect.w - 12
        while text_h > 7 and self.font.size(display, height=text_h)[0] > max_w:
            text_h -= 1
        while self.font.size(display, height=text_h)[0] > max_w and len(display) > 1:
            display = display[:-1]
        txt_s = self.font.render(display, color=color, height=max(7, text_h))
        uk.blit_surface(screen, txt_s, (rect.x + 6, rect.y + (rect.h - txt_s.get_height()) // 2), transient=True)

    def _draw_dropdown_button(self, screen, rect, display_text, is_open, has_value=True):
        """Value + chevron button that opens a floating option list
        (_draw_open_dropdown) — used for enemy_id / room / npc_instance_id
        objective params."""
        bg = uk.Theme.CARD_BG_HOVER if is_open else uk.Theme.CARD_BG
        border = uk.Theme.GOLD if is_open else uk.Theme.PANEL_BORDER
        uk.draw_rect_on(screen, bg, rect, 0, 4)
        uk.draw_rect_on(screen, border, rect, 2 if is_open else 1, 4)
        uk.register_hoverable(rect)

        disp = display_text
        text_h = min(self.hint_size, max(6, rect.h - 8))
        max_text_w = rect.w - 22
        while text_h > 6 and self.font.size(disp, height=text_h)[0] > max_text_w:
            text_h -= 1
        while self.font.size(disp, height=text_h)[0] > max_text_w and len(disp) > 1:
            disp = disp[:-1]
        color = uk.Theme.TEXT_PRIMARY if has_value else uk.Theme.TEXT_DIM
        txt_s = self.font.render(disp, color=color, height=max(6, text_h))
        uk.blit_surface(screen, txt_s, (rect.x + 6, rect.y + (rect.h - txt_s.get_height()) // 2), transient=True)

        chev_color = uk.Theme.GOLD if is_open else uk.Theme.TEXT_MUTED
        cx, cy = rect.right - 12, rect.centery
        if is_open:
            uk.draw_line_on(screen, chev_color, (cx - 4, cy + 2), (cx, cy - 3), 2)
            uk.draw_line_on(screen, chev_color, (cx, cy - 3), (cx + 4, cy + 2), 2)
        else:
            uk.draw_line_on(screen, chev_color, (cx - 4, cy - 2), (cx, cy + 3), 2)
            uk.draw_line_on(screen, chev_color, (cx, cy + 3), (cx + 4, cy - 2), 2)

    # =========================================================================
    # Drawing — palette panel
    # =========================================================================

    def _draw_palette_background(self, screen):
        rect = pygame.Rect(self.palette_x, self.palette_y, self.palette_width, self.palette_height)
        uk.draw_panel(screen, rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=2, radius=uk.Theme.RADIUS_PANEL, shadow=True)

    def _draw_title(self, screen):
        title_s = self.font.render("Entities", color=uk.Theme.GOLD, height=self.title_size)
        title_pos = (self.palette_x + 20, self.palette_y + 12)
        uk.blit_surface(screen, title_s, title_pos, transient=True)

        # '?' info badge — opens the keybinds popup (_draw_keybinds_popup).
        info_d = 18
        info_x = title_pos[0] + title_s.get_width() + 14
        info_y = title_pos[1] + (title_s.get_height() - info_d) // 2
        info_rect = pygame.Rect(info_x, info_y, info_d, info_d)
        self._info_button_rect = info_rect
        self._draw_info_button(screen, info_rect)

    # ── category tabs ────────────────────────────────────────────────────────

    def _category_tabs_top_y(self):
        return self.palette_y + 45  # below title

    def _category_tabs_bottom_y(self):
        return self._category_tabs_top_y() + len(self.category_keys) * 40 + 10

    # Display label shown on the category tab — separate from the catalogue
    # key (self.category_keys / self.categories / CATEGORY_META) so nothing
    # in the catalogue-building or click-handling logic has to change.
    CATEGORY_LABELS = {
        'Enemy Bosses': 'Bosses',
    }

    def _draw_category_tabs(self, screen):
        y = self._category_tabs_top_y()

        for key in self.category_keys:
            is_sel = (key == self.current_category)
            hover_t = self.category_hover_anim.get(key, 0.0)
            meta = self.CATEGORY_META.get(key, {})
            accent = meta.get('accent', uk.Theme.GOLD)

            tab_rect = pygame.Rect(
                self.palette_x + self.palette_padding, y,
                self.palette_width - self.palette_padding * 2, 30
            )
            uk.register_hoverable(tab_rect)

            t = max(hover_t, 1.0 if is_sel else 0.0)
            bg = uk.lerp_color(uk.Theme.CARD_BG[:3], uk.Theme.CARD_BG_HOVER[:3], hover_t)
            if is_sel:
                bg = uk.lerp_color(bg, uk.Theme.CARD_BG_SELECTED[:3], 1.0)
            border = uk.lerp_color(uk.Theme.CARD_BORDER, accent, t)
            uk.draw_panel(screen, tab_rect, bg=(*bg, 255), border=border,
                          border_width=2 if is_sel else 1, radius=6, shadow=False)

            if t > 0.02:
                uk.draw_soft_glow(screen, tab_rect.center, 20, accent, max_alpha=int(24 * t))

            txt_col = uk.Theme.TEXT_PRIMARY if is_sel else uk.Theme.TEXT_SECONDARY
            label_text = self.CATEGORY_LABELS.get(key, key)
            label_s = self.font.render(label_text, color=txt_col, height=self.body_size + 2)
            label_rect = label_s.get_rect(center=tab_rect.center)
            uk.blit_surface(screen, label_s, label_rect, transient=True)

            # store for hit-testing
            self.ui_rects['category_rects'].append({'rect': tab_rect, 'key': key})
            y += 40

        # separator line
        uk.draw_line_on(screen, uk.Theme.PANEL_BORDER,
                        (self.palette_x + self.palette_padding, y),
                        (self.palette_x + self.palette_width - self.palette_padding, y), 1)

    # ── entity grid ──────────────────────────────────────────────────────────

    def _draw_entity_grid(self, screen, start_y, content_height):
        """Draw the scrollable grid of entity items for the active category.
        content_height is computed by draw() from how much space the
        variant selector + settings panel actually need this frame, so the
        grid always gets whatever's left over instead of a fixed guess."""
        clip_rect = pygame.Rect(self.palette_x, start_y,
                                self.palette_width, content_height)
        screen.set_clip(clip_rect)

        entities = self.categories[self.current_category]
        current_y = start_y - self.scroll_offset

        for i, entity in enumerate(entities):
            row = i // self.items_per_row
            col = i % self.items_per_row

            item_x = self.palette_x + self.palette_padding + col * (self.item_size + self.item_gap)
            item_y = current_y + row * (self.item_size + self.item_gap)

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

    def _fit_label_text(self, text, height, max_width):
        """Shorten text with a trailing '...' so it renders within
        max_width at the given bitmap-font height — same fix as
        ObjectEditor._fit_label_text, ported to this panel's font API.
        The bitmap font has no ellipsis glyph, so three periods stand in
        for one. Returns the text unchanged if it already fits. Results
        are cached since this runs every frame for every visible card."""
        cache_key = (text, height, max_width)
        cached = self._label_fit_cache.get(cache_key)
        if cached is not None:
            return cached

        if self.font.size(text, height=height)[0] <= max_width:
            self._label_fit_cache[cache_key] = text
            return text

        suffix = "..."
        suffix_w = self.font.size(suffix, height=height)[0]
        if suffix_w >= max_width:
            self._label_fit_cache[cache_key] = suffix
            return suffix

        # Binary search for the longest prefix that still fits alongside
        # the "..." suffix.
        lo, hi, best = 0, len(text), ""
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = text[:mid].rstrip()
            if self.font.size(candidate, height=height)[0] + suffix_w <= max_width:
                best = candidate
                lo = mid + 1
            else:
                hi = mid - 1
        result = (best + suffix) if best else suffix
        self._label_fit_cache[cache_key] = result
        return result

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
        meta = self.CATEGORY_META.get(self.current_category, {})
        accent = meta.get('accent', uk.Theme.GOLD)

        # glow behind selected / hovered tile
        if is_selected or is_hover:
            glow_alpha = 55 if is_selected else 26
            uk.draw_soft_glow(screen, item_rect.center, max(item_w, item_h) // 2 + 6,
                              accent, max_alpha=glow_alpha)

        bg = uk.Theme.CARD_BG_SELECTED if is_selected else (
            uk.Theme.CARD_BG_HOVER if is_hover else uk.Theme.CARD_BG)
        border = accent if is_selected else (
            uk.lerp_color(uk.Theme.CARD_BORDER, accent, 0.6) if is_hover else uk.Theme.CARD_BORDER)
        uk.draw_panel(screen, item_rect, bg=bg, border=border,
                      border_width=2 if is_selected else 1, radius=uk.Theme.RADIUS_CARD, shadow=False)

        # sprite fills tile at correct proportions (no squishing)
        if entity['sprite']:
            src = entity['sprite']
            # This runs every frame for every visible palette tile, so an
            # uncached transform.scale() here was a steady per-frame tax the
            # whole time the entity palette is open. Cache the scaled
            # thumbnail per (sprite identity, item size).
            thumb_key = (id(src), item_w, item_h)
            scaled = self._palette_thumb_cache.get(thumb_key)
            if scaled is None:
                sw, sh = src.get_size()
                scale = min((item_w - 8) / sw, (item_h - 8) / sh)
                scaled = pygame.transform.scale(src, (max(1, int(sw * scale)), max(1, int(sh * scale))))
                self._palette_thumb_cache[thumb_key] = scaled
            uk.blit_surface(screen, scaled, scaled.get_rect(center=item_rect.center), transient=False)

        # name label below tile — capped to (roughly) this card's own
        # column width before rendering, so a long entity name can no
        # longer spill sideways past the card and into the neighboring
        # column's label (same fix as ObjectEditor's palette cards).
        # Cached per (text, height) since this runs every frame for every
        # visible card.
        label_max_w = self.item_size + self.item_gap - 4
        label_text = self._fit_label_text(entity['name'], self.hint_size, label_max_w)
        name_surf = self._palette_label_cache.get(label_text)
        if name_surf is None:
            name_surf = self.font.render(label_text, color=uk.Theme.TEXT_MUTED, height=self.hint_size)
            self._palette_label_cache[label_text] = name_surf
        uk.blit_surface(screen, name_surf, name_surf.get_rect(centerx=item_rect.centerx,
                                                              top=item_rect.bottom + 2), transient=False)

        # register for hit-testing
        self.ui_rects['entity_rects'].append({'rect': item_rect, 'entity': entity})
        uk.register_hoverable(item_rect)

    # ── variant selector strip ───────────────────────────────────────────────

    def _variant_items_per_row(self):
        swatch_size = 48
        swatch_gap = 8
        available_w = self.palette_width - self.palette_padding * 2
        return max(1, (available_w + swatch_gap) // (swatch_size + swatch_gap))

    def _variant_selector_content_height(self):
        """How tall the variant selector needs to be to show up to 3 rows of
        variants at once (further rows scroll with the mouse wheel). Returns
        0 when there's nothing to show, so draw() can skip reserving any
        space for it at all — this, together with a fixed row cap, is what
        keeps the strip pinned flush above the settings footer instead of
        the old fixed-guess reserve letting a long variant list grow upward
        into (and overlap) the entity grid."""
        if not self.selected_entity or not self.selected_entity.get('has_variants'):
            return 0
        variants = self.selected_entity.get('variants', [])
        if not variants:
            return 0

        cols = self._variant_items_per_row()
        total_rows = (len(variants) + cols - 1) // cols
        visible_rows = min(total_rows, 3)
        header_h = 22
        row_pitch = 48 + 22  # swatch_size + row_gap (room for the name label)
        return header_h + visible_rows * row_pitch + 8

    def _draw_variant_selector(self, screen, selector_y, selector_height):
        """
        Grid of variant swatches – only drawn when the selected entity has
        variants. Shows up to 3 rows at once; further rows scroll with the
        mouse wheel (hover the strip and scroll) instead of the strip
        growing without bound — see _variant_selector_content_height().
        """
        if selector_height <= 0:
            self.ui_rects['variant_rects'] = []
            self._variant_selector_rect = None
            return

        variants = self.selected_entity.get('variants', [])
        strip_x = self.palette_x

        strip_rect = pygame.Rect(strip_x, selector_y, self.palette_width, selector_height)
        self._variant_selector_rect = strip_rect
        uk.draw_rect_on(screen, uk.Theme.CARD_BG, strip_rect, 0, 0)
        uk.draw_line_on(screen, uk.Theme.PANEL_BORDER,
                        (strip_x, selector_y), (strip_x + self.palette_width, selector_y), 1)

        swatch_size = 48
        swatch_gap = 8
        row_gap = 22
        cols = self._variant_items_per_row()
        total_rows = (len(variants) + cols - 1) // cols
        visible_rows = min(total_rows, 3)
        max_row_scroll = max(0, total_rows - visible_rows)
        self.variant_scroll = max(0, min(self.variant_scroll, max_row_scroll))

        # label
        label_s = self.font.render("Select Variant", color=uk.Theme.TEXT_DIM, height=self.hint_size)
        uk.blit_surface(screen, label_s, (strip_x + self.palette_padding, selector_y + 6), transient=True)

        if max_row_scroll > 0:
            row_hint_s = self.font.render(
                f"Row {self.variant_scroll + 1}/{total_rows} - scroll for more",
                color=uk.Theme.TEXT_DIM, height=max(7, self.hint_size - 1))
            row_hint_rect = row_hint_s.get_rect(
                right=strip_x + self.palette_width - self.palette_padding, top=selector_y + 6)
            uk.blit_surface(screen, row_hint_s, row_hint_rect, transient=True)

        meta = self.CATEGORY_META.get(self.current_category, {})
        accent = meta.get('accent', uk.Theme.GOLD)

        current_var  = self.selected_variant or self._get_current_variant(self.selected_entity)
        sx           = strip_x + self.palette_padding
        grid_top     = selector_y + 22
        label_max_w  = swatch_size + swatch_gap - 4

        first_index = self.variant_scroll * cols
        last_index = min(len(variants), first_index + visible_rows * cols)
        visible_variants = variants[first_index:last_index]

        self.ui_rects['variant_rects'] = []

        clip_rect = pygame.Rect(strip_x, grid_top, self.palette_width, visible_rows * (swatch_size + row_gap))
        screen.set_clip(clip_rect)

        for list_idx, variant in enumerate(visible_variants):
            col  = list_idx % cols
            row  = list_idx // cols
            vx   = sx + col * (swatch_size + swatch_gap)
            vy   = grid_top + row * (swatch_size + row_gap)
            rect = pygame.Rect(vx, vy, swatch_size, swatch_size)

            is_sel   = (current_var is variant)
            is_hover = (self.hover_variant_idx == list_idx)

            bg = uk.Theme.CARD_BG_SELECTED if is_sel else (
                uk.Theme.CARD_BG_HOVER if is_hover else uk.Theme.CARD_BG)
            border = accent if is_sel else (
                uk.lerp_color(uk.Theme.CARD_BORDER, accent, 0.6) if is_hover else uk.Theme.CARD_BORDER)
            uk.draw_panel(screen, rect, bg=bg, border=border,
                          border_width=2 if is_sel else 1, radius=6, shadow=False)

            # variant sprite (or colour swatch fallback)
            if variant.get('sprite'):
                spr = variant['sprite']
                max_dim = swatch_size - 6
                sw, sh = spr.get_size()
                if sw > max_dim or sh > max_dim:
                    scale = min(max_dim / sw, max_dim / sh)
                    spr = pygame.transform.scale(spr, (int(sw * scale), int(sh * scale)))
                uk.blit_surface(screen, spr, spr.get_rect(center=rect.center), transient=True)
            else:
                inner = rect.inflate(-6, -6)
                uk.draw_rect_on(screen, variant.get('color', (128, 128, 128)), inner, 0, 3)

            # name below swatch — truncated to the slot's own width so a
            # long variant name can't spill into the next slot's label
            # (same fix as the main entity grid's cards).
            name_h = max(7, self.hint_size - 1)
            label_str = self._fit_label_text(variant['name'], name_h, label_max_w)
            name_s = self.font.render(label_str, color=uk.Theme.TEXT_DIM, height=name_h)
            uk.blit_surface(screen, name_s, name_s.get_rect(centerx=rect.centerx, top=rect.bottom + 2), transient=True)

            # store for hit-testing
            self.ui_rects['variant_rects'].append({'rect': rect, 'variant': variant})
            uk.register_hoverable(rect)

        screen.set_clip(None)

    # ── settings footer (NPC mode/facing only) ────────────────────────────────

    def _settings_panel_content_height(self):
        """How tall _draw_settings_panel's content actually is, so the
        panel only takes up space when the selected entity actually has
        settings to show (currently: NPC Mode/Facing) — otherwise this
        returns 0 and draw() skips it entirely, letting the entity grid (or
        the variant selector) use that space instead of leaving an empty
        rectangle sitting at the bottom of the panel."""
        if self.selected_entity and self.selected_entity.get('entity_type') == 'npc':
            top_pad = 12
            mode_block = 16 + 24 + 10       # label + button row + gap
            facing_block = 16 + 24 + 12     # label + button row + gap
            bottom_pad = 8
            return top_pad + mode_block + facing_block + bottom_pad
        return 0

    def _draw_settings_panel(self, screen, panel_y, panel_h):
        """NPC Mode / Facing selectors — only drawn (and only takes up
        space, via _settings_panel_content_height()) when an NPC is
        selected. No background panel/box: this sits directly on the
        palette background rather than in its own boxed-off footer."""
        if panel_h <= 0:
            self.ui_rects['npc_mode_rects'] = []
            self.ui_rects['npc_facing_rects'] = []
            return

        y = panel_y + 12
        bx = self.palette_x + self.palette_padding

        # Note: AI Type and Zeni Pool used to be selectable here for enemies
        # and bosses. Both now come entirely from the entity's entity_creator
        # config (ai_type / zeni_pool in assets/enemies/{id}.json), so there's
        # nothing to configure per-placement anymore.

        # NPC Mode + Facing selectors (only shown when an NPC is selected)
        if self.selected_entity and self.selected_entity.get('entity_type') == 'npc':
            button_width  = 68
            button_height = 24
            button_gap    = 6

            # Mode
            self.ui_rects['npc_mode_rects'] = []
            mode_label_s = self.font.render("NPC Mode", color=uk.Theme.TEXT_DIM, height=self.hint_size)
            uk.blit_surface(screen, mode_label_s, (bx, y), transient=True)
            y += 16
            for i, mode in enumerate(self.npc_modes):
                button_rect = pygame.Rect(bx + i * (button_width + button_gap), y, button_width, button_height)
                is_sel = (mode == self.selected_npc_mode)
                is_hov = (self.hover_npc_mode_idx == i)
                self._draw_chip(screen, button_rect, mode.capitalize(), selected=is_sel, hover=is_hov)
                self.ui_rects['npc_mode_rects'].append({'rect': button_rect, 'mode': mode, 'index': i})
            y += button_height + 10

            # Facing (only meaningful in static mode)
            self.ui_rects['npc_facing_rects'] = []
            facing_enabled = (self.selected_npc_mode == 'static')
            facing_label_s = self.font.render("Facing (static)", color=uk.Theme.TEXT_DIM, height=self.hint_size)
            uk.blit_surface(screen, facing_label_s, (bx, y), transient=True)
            y += 16
            for i, facing in enumerate(self.npc_facings):
                button_rect = pygame.Rect(bx + i * (button_width + button_gap), y, button_width, button_height)
                is_sel = (facing == self.selected_npc_facing)
                is_hov = (self.hover_npc_facing_idx == i)
                self._draw_chip(screen, button_rect, facing.capitalize(),
                                selected=is_sel and facing_enabled, hover=is_hov and facing_enabled,
                                disabled=not facing_enabled)
                self.ui_rects['npc_facing_rects'].append({'rect': button_rect, 'facing': facing, 'index': i})
            y += button_height + 12

        # Instruction tooltips used to live here — replaced by the '?' info
        # button next to the title (_draw_keybinds_popup) so the panel isn't
        # permanently cluttered with reference text.

    # =========================================================================
    # Drawing — dialogue / mission popup
    # =========================================================================

    def _draw_dialogue_popup(self, screen):
        """Overlay popup with [Dialogues] and [Mission] tabs."""
        p  = self._dialogue_popup
        sw, sh = self.screen_width, self.screen_height

        # Dim background
        uk.draw_rect_on(screen, (0, 0, 0, 165), pygame.Rect(0, 0, sw, sh), 0, 0)

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
        uk.draw_panel(screen, box, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=2, radius=uk.Theme.RADIUS_PANEL, shadow=True)

        # ── Tab bar ──────────────────────────────────────────────────────────
        self._dialogue_popup_rects = []
        tab_y  = py + 14
        tabs   = [('dialogues', 'Dialogues'), ('mission', 'Mission')]
        tab_w  = 120
        active_tab = p.get('active_tab', 'dialogues')
        for i, (tab_key, tab_label) in enumerate(tabs):
            tr = pygame.Rect(px + 16 + i * (tab_w + 8), tab_y, tab_w, 30)
            is_sel = (tab_key == active_tab)
            self._draw_chip(screen, tr, tab_label, selected=is_sel)
            self._dialogue_popup_rects.append({'rect': tr, 'action': 'switch_tab', 'tab': tab_key})

        content_top = tab_y + 40

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
        hint_s = self.font.render(
            f"{n} line{'s' if n != 1 else ''}  -  Shift+Enter new line  -  Tab next  -  Backspace on empty deletes",
            color=uk.Theme.TEXT_DIM, height=self.hint_size)
        uk.blit_surface(screen, hint_s, (px + 16, content_top), transient=True)
        rows_top = content_top + 22

        for i, text in enumerate(p['dialogues']):
            if i >= max_vis:
                break
            ry     = rows_top + i * row_h
            is_sel = (i == p['active_index'])

            lbl_s = self.font.render(f"Line {i + 1}",
                                     color=uk.Theme.TEXT_PRIMARY if is_sel else uk.Theme.TEXT_DIM,
                                     height=self.hint_size)
            uk.blit_surface(screen, lbl_s, (px + 16, ry + 11), transient=True)

            field = pygame.Rect(px + 76, ry + 4, pw - 76 - 46, row_h - 8)
            self._draw_text_box(screen, field, text, is_sel)
            self._dialogue_popup_rects.append({'rect': field, 'action': 'select_line', 'index': i})

            rm = pygame.Rect(field.right + 6, ry + 6, 28, row_h - 12)
            can_remove = n > 1
            self._draw_chip(screen, rm, "x", danger=can_remove, disabled=not can_remove)
            if can_remove:
                self._dialogue_popup_rects.append({'rect': rm, 'action': 'remove', 'index': i})

        footer_y = rows_top + vis * row_h + 10
        add_r = pygame.Rect(px + 16, footer_y, 120, 30)
        self._draw_chip(screen, add_r, "+ Add Line", success=True)
        self._dialogue_popup_rects.append({'rect': add_r, 'action': 'add'})

        self._draw_popup_footer(screen, px, footer_y, pw)

    def _draw_mission_tab(self, screen, p, px, py, pw, ph, content_top):
        """Draw the mission editor tab."""
        bx   = px + 16
        rw   = pw - 32   # usable row width
        y    = content_top
        m    = p['mission']
        af   = p.get('mission_active_field')
        enabled = p.get('mission_enabled', False)

        def text_field(field_key, value, x, fy, w, h=26, label=None):
            """Draw a labelled text field and register its rect."""
            if label:
                ls = self.font.render(label, color=uk.Theme.TEXT_DIM, height=self.hint_size)
                uk.blit_surface(screen, ls, (x, fy + 6), transient=True)
                lw = self.font.size(label, height=self.hint_size)[0] + 6
                x += lw
                w -= lw
            r    = pygame.Rect(x, fy, max(20, w), h)
            is_f = (af == field_key)
            self._draw_text_box(screen, r, value, is_f)
            self._dialogue_popup_rects.append({'rect': r, 'action': 'focus_field', 'field': field_key})
            return r

        # ── Enable toggle ─────────────────────────────────────────────────
        en_r = pygame.Rect(bx, y, 160, 30)
        self._draw_chip(screen, en_r, "Mission ON" if enabled else "Mission OFF", success=enabled)
        self._dialogue_popup_rects.append({'rect': en_r, 'action': 'toggle_mission'})
        y += 38

        if not enabled:
            # Footer (confirm/cancel) when mission is off
            self._draw_popup_footer(screen, px, py + ph - 44, pw)
            return

        # ── Quest type cycle ──────────────────────────────────────────────
        qt        = m.get('quest_type', 'side')
        qt_colors = {'main': (255, 205, 90), 'side': uk.Theme.KI_BLUE, 'other': uk.Theme.TEXT_MUTED}
        qt_col    = qt_colors.get(qt, uk.Theme.GOLD)
        qt_r      = pygame.Rect(bx, y, 150, 26)
        uk.draw_rect_on(screen, uk.Theme.CARD_BG, qt_r, 0, 5)
        uk.draw_rect_on(screen, qt_col, qt_r, 2, 5)
        qt_s = self.font.render(f"Type: {qt.capitalize()}", color=qt_col, height=self.hint_size)
        uk.blit_surface(screen, qt_s, (qt_r.x + 8, qt_r.y + (qt_r.h - qt_s.get_height()) // 2), transient=True)
        cx, cy = qt_r.right - 14, qt_r.centery
        uk.draw_line_on(screen, qt_col, (cx - 4, cy - 2), (cx, cy + 3), 2)
        uk.draw_line_on(screen, qt_col, (cx, cy + 3), (cx + 4, cy - 2), 2)
        self._dialogue_popup_rects.append({'rect': qt_r, 'action': 'toggle_quest_type'})
        y += 34

        # ── Sequential toggle ─────────────────────────────────────────────
        seq   = m.get('sequential', False)
        seq_r = pygame.Rect(bx, y, 150, 26)
        self._draw_chip(screen, seq_r, "Sequential: " + ("YES" if seq else "NO"), selected=seq)
        self._dialogue_popup_rects.append({'rect': seq_r, 'action': 'toggle_sequential'})
        y += 34

        # ── Objectives ────────────────────────────────────────────────────
        section_s = self.font.render("OBJECTIVES", color=uk.Theme.TEXT_DIM, height=self.hint_size)
        uk.blit_surface(screen, section_s, (bx, y), transient=True)
        uk.draw_line_on(screen, uk.Theme.PANEL_BORDER, (bx + 86, y + 6), (bx + rw, y + 6), 1)
        y += 20

        for i, obj in enumerate(m.get('objectives', [])[:8]):
            obj_type = obj['type']
            row_x    = bx

            # Type cycle button
            type_r = pygame.Rect(row_x, y, 88, 26)
            self._draw_chip(screen, type_r, obj_type, selected=True)
            self._dialogue_popup_rects.append({'rect': type_r, 'action': 'obj_cycle_type', 'index': i})
            row_x += 94

            # Param fields
            for (param_key, param_label, param_w) in self.OBJECTIVE_PARAM_FIELDS.get(obj_type, []):
                field_key = f'obj:{i}:{param_key}'
                val = str(obj['params'].get(param_key, ''))
                avail_w = min(param_w, bx + rw - row_x - 36)
                if avail_w <= 20:
                    break

                # ── Dropdown button ─────────────────────────────────────────
                if param_key in self.DROPDOWN_PARAMS:
                    disp = self._param_display_value(param_key, val)
                    is_open = (
                        self._open_dropdown is not None
                        and self._open_dropdown['obj_idx'] == i
                        and self._open_dropdown['param'] == param_key
                    )

                    pl = self.font.render(param_label, color=uk.Theme.TEXT_DIM, height=max(7, self.hint_size - 1))
                    uk.blit_surface(screen, pl, (row_x + 2, y - 13), transient=True)

                    btn_r = pygame.Rect(row_x, y, avail_w, 26)
                    self._draw_dropdown_button(screen, btn_r, disp, is_open, has_value=bool(val))

                    self._dialogue_popup_rects.append({
                        'rect': btn_r, 'action': 'obj_dropdown_open',
                        'index': i, 'param': param_key,
                        'anchor_x': btn_r.x, 'anchor_y': btn_r.bottom,
                        'width': avail_w,
                    })

                # ── Free-text field (count, item_id, etc.) ──────────────────
                else:
                    text_field(field_key, val, row_x, y, avail_w, label=None)
                    pl = self.font.render(param_label, color=uk.Theme.TEXT_DIM, height=max(7, self.hint_size - 1))
                    uk.blit_surface(screen, pl, (row_x + 2, y - 13), transient=True)

                row_x += param_w + 6
                if row_x > bx + rw - 36:
                    break

            # Description field (shown in journal next to quest icon)
            desc_key = f'obj:{i}:description'
            desc_val = obj.get('description', '')
            desc_avail_w = bx + rw - 28 - row_x - 6
            if desc_avail_w > 40:
                text_field(desc_key, desc_val, row_x, y, desc_avail_w, label="Journal:")

            # Remove button
            rm_r = pygame.Rect(bx + rw - 26, y, 26, 26)
            self._draw_chip(screen, rm_r, "x", danger=True)
            self._dialogue_popup_rects.append({'rect': rm_r, 'action': 'obj_remove', 'index': i})
            y += 32

        # Add objective
        add_r = pygame.Rect(bx, y, 140, 26)
        self._draw_chip(screen, add_r, "+ Add Objective", success=True)
        self._dialogue_popup_rects.append({'rect': add_r, 'action': 'obj_add'})
        y += 34

        # ── Rewards ───────────────────────────────────────────────────────
        uk.draw_line_on(screen, uk.Theme.PANEL_BORDER, (bx, y), (bx + rw, y), 1)
        y += 10
        xp_val = str(m.get('rewards', {}).get('xp', 0))
        text_field('reward_xp', xp_val, bx, y, 90, label="XP Reward:")
        y += 34

        # ── Per-state dialogue strings ────────────────────────────────────
        section2_s = self.font.render("STATE DIALOGUES", color=uk.Theme.TEXT_DIM, height=self.hint_size)
        uk.blit_surface(screen, section2_s, (bx, y), transient=True)
        uk.draw_line_on(screen, uk.Theme.PANEL_BORDER, (bx + 140, y + 6), (bx + rw, y + 6), 1)
        y += 20
        dlg_states = [
            ('dlg_accepted',  'After Accept:'),
            ('dlg_active',    'While Active:'),
            ('dlg_completed', 'On Complete:'),
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
        options  = dd['options']
        scroll   = dd['scroll']
        ax, ay   = dd['anchor_x'], dd['anchor_y']
        w        = dd['width']
        row_h    = 24
        max_vis  = min(10, len(options))
        list_h   = max_vis * row_h + 6

        # Flip upward if list would go off screen
        if ay + list_h > self.screen_height - 10:
            ay = dd['anchor_y'] - dd['btn_h'] - list_h

        list_r = pygame.Rect(ax, ay, w, list_h)
        uk.draw_panel(screen, list_r, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=1, radius=6, shadow=True)

        # Clip to list area
        clip_r = pygame.Rect(ax + 1, ay + 3, w - 2, list_h - 6)
        old_clip = screen.get_clip()
        screen.set_clip(clip_r)

        mx, my = pygame.mouse.get_pos()

        for j, opt in enumerate(options[scroll: scroll + max_vis]):
            real_idx = scroll + j
            ry   = ay + 3 + j * row_h
            item_r = pygame.Rect(ax + 1, ry, w - 2, row_h)
            hovered = item_r.collidepoint(mx, my)
            bg = uk.Theme.CARD_BG_HOVER if hovered else (
                uk.Theme.CARD_BG if real_idx % 2 == 0 else uk.Theme.PANEL_BG)
            uk.draw_rect_on(screen, bg, item_r, 0, 0)

            disp = opt if opt else ('(any)' if dd['param'] in ('enemy_id', 'room') else '—')
            trim = disp
            while self.font.size(trim, height=self.hint_size)[0] > w - 14 and len(trim) > 1:
                trim = trim[:-1]
            if trim != disp:
                trim = trim[:-1]
            col = uk.Theme.GOLD if hovered else (uk.Theme.TEXT_PRIMARY if opt else uk.Theme.TEXT_DIM)
            opt_s = self.font.render(trim, color=col, height=self.hint_size)
            uk.blit_surface(screen, opt_s, (ax + 6, ry + (row_h - opt_s.get_height()) // 2), transient=True)

        screen.set_clip(old_clip)

        # Scroll indicators
        if len(options) > max_vis:
            if scroll > 0:
                ux, uy = ax + w - 12, ay + 8
                uk.draw_line_on(screen, uk.Theme.GOLD, (ux - 4, uy + 3), (ux, uy - 2), 2)
                uk.draw_line_on(screen, uk.Theme.GOLD, (ux, uy - 2), (ux + 4, uy + 3), 2)
            if scroll + max_vis < len(options):
                dx, dy = ax + w - 12, ay + list_h - 8
                uk.draw_line_on(screen, uk.Theme.GOLD, (dx - 4, dy - 3), (dx, dy + 2), 2)
                uk.draw_line_on(screen, uk.Theme.GOLD, (dx, dy + 2), (dx + 4, dy - 3), 2)

        # Register item rects for click handling (appended after tab rects so
        # they take priority — we process from the end in handle_event)
        for j in range(min(max_vis, len(options) - scroll)):
            real_idx = scroll + j
            ry = ay + 3 + j * row_h
            item_r = pygame.Rect(ax + 1, ry, w - 2, row_h)
            uk.register_hoverable(item_r)
            self._dialogue_popup_rects.append({
                'rect':   item_r,
                'action': 'obj_dropdown_select',
                'opt_idx': real_idx,
            })

    def _draw_popup_footer(self, screen, px, footer_y, pw):
        """Draw shared Confirm / Cancel buttons."""
        ok_r = pygame.Rect(px + pw - 220, footer_y, 96, 30)
        self._draw_chip(screen, ok_r, "Confirm", success=True)
        self._dialogue_popup_rects.append({'rect': ok_r, 'action': 'confirm'})

        cl_r = pygame.Rect(px + pw - 114, footer_y, 96, 30)
        self._draw_chip(screen, cl_r, "Cancel", danger=True)
        self._dialogue_popup_rects.append({'rect': cl_r, 'action': 'cancel'})

    # =========================================================================
    # Info button / keybinds popup
    # =========================================================================

    def _draw_info_button(self, screen, rect):
        """Small circular badge that toggles the keybinds popup. Uses a
        custom icon from assets/ui/toolbar/info.png when present, falling
        back to the procedural '?' mark otherwise — same widget as
        ObjectEditor / TilesetEditor."""
        mouse_pos = pygame.mouse.get_pos()
        hovered = rect.collidepoint(mouse_pos)
        center = rect.center
        radius = rect.width // 2
        uk.register_hoverable(rect)

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
            mark_s = self.font.render("?", color=mark_color, height=self.body_size)
            uk.blit_surface(screen, mark_s,
                            (center[0] - mark_s.get_width() // 2, center[1] - mark_s.get_height() // 2),
                            transient=True)

    def _draw_keybinds_popup(self, screen):
        """Full keybind reference, opened from the '?' info button. Drawn
        last, over a dimmed backdrop, so it reads as a modal overlay above
        the whole panel (and the dialogue popup / world beneath it). Any
        click while it's open closes it (handled in handle_event) — this
        method only ever draws."""
        if not self.show_keybinds_popup:
            return

        overlay = pygame.Rect(0, 0, self.screen_width, self.screen_height)
        uk.draw_rect_on(screen, (8, 9, 13, 170), overlay, 0, 0)

        sections = [
            ("Entities", [
                ("Click Entity", "Select entity / variant"),
                ("Click World", "Place selected entity"),
                ("Right Click World", "Delete entity"),
                ("G", "Toggle grid snap"),
                ("H", "Toggle grid visibility"),
            ]),
            ("Dialogue / mission popup", [
                ("Shift + Enter", "New dialogue line"),
                ("Enter", "Confirm and close"),
                ("Tab", "Next line"),
                ("Backspace", "Delete line when empty"),
                ("Esc", "Cancel / close popup"),
            ]),
        ]

        row_h = 20
        section_gap = 14
        header_h = 50
        margin_x = 20
        key_indent = 10
        col_gap = 18
        right_pad = 20

        key_surfaces = []
        desc_surfaces = []
        for _, rows in sections:
            for key_label, desc in rows:
                key_surfaces.append(self.font.render(key_label, color=uk.Theme.GOLD, height=self.body_size))
                desc_surfaces.append(self.font.render(desc, color=uk.Theme.TEXT_PRIMARY, height=self.body_size))

        key_col_w = max(s.get_width() for s in key_surfaces) + col_gap
        max_desc_w = max(s.get_width() for s in desc_surfaces)

        title_s = self.font.render("Keybinds", color=uk.Theme.GOLD, height=self.title_size)
        close_s = self.font.render("Click anywhere to close", color=uk.Theme.TEXT_DIM, height=self.hint_size)

        content_w = key_indent + key_col_w + max_desc_w + right_pad
        header_w = title_s.get_width() + 24 + close_s.get_width()
        panel_w = max(360, margin_x * 2 + max(content_w, header_w))

        content_rows = sum(1 + len(rows) for _, rows in sections)
        panel_h = header_h + content_rows * row_h + len(sections) * section_gap + 16

        panel_x = (self.screen_width - panel_w) // 2
        panel_y = max(30, (self.screen_height - panel_h) // 2)
        panel_rect = pygame.Rect(panel_x, panel_y, panel_w, panel_h)

        uk.draw_panel(screen, panel_rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=2, radius=uk.Theme.RADIUS_PANEL, shadow=True)

        uk.blit_surface(screen, title_s, (panel_x + margin_x, panel_y + 14), transient=True)
        uk.blit_surface(screen, close_s,
                        (panel_x + panel_w - close_s.get_width() - margin_x, panel_y + 20), transient=True)

        uk.draw_rect_on(screen, uk.Theme.PANEL_BORDER,
                        (panel_x + 16, panel_y + header_h - 10, panel_w - 32, 1), 0, 0)

        y = panel_y + header_h
        i = 0
        for section_name, rows in sections:
            section_s = self.font.render(section_name, color=uk.Theme.TEXT_MUTED, height=self.body_size)
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

        mx, my = mx / self.editor_zoom, my / self.editor_zoom
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

            outline_color = uk.Theme.DANGER_BRIGHT if blocked else uk.Theme.GOLD
            screen.draw_rect(outline_color,
                             (int(sx - ew // 2), int(sy - eh // 2), ew, eh), 2)

    # =========================================================================
    # Internal helpers
    # =========================================================================

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