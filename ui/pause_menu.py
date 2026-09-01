"""
Pause Menu
----------
Opened with ESC during gameplay. Same visual style as CharacterSwitchMenu:
tiled background, 9-slice border frame, bitmap fonts, sprite buttons.

Tab order: STATUS | INVENTORY | EQUIP | OPTIONS | JOURNAL

Status page:   portrait on the left, stats on the right.
Inventory:     scrollable item list with sub-tabs (Supplies / Story Items).
Equip:         slot sprites with stat column.
Options:       SFX vol / Music vol / Text Speed bars + Credits / Sleep links.
Journal:       active or completed quest list with quest-type icons.

Pressing S or Down on the Status tab signals 'open_skills' back to game.py.
"""

import pygame
import os
from core.bitmap_font import BitmapFont
from dev_tools.character_creator import discover_characters, load_config, resolve_portrait_path
from core.items import (
    get_item, get_items_by_category, CATEGORY_SUPPLIES, CATEGORY_STORY_ITEMS,
    CATEGORY_EQUIP_BODY, CATEGORY_EQUIP_HANDS, CATEGORY_EQUIP_FEET, CATEGORY_EQUIP_ACCESSORY,
)

# Fixed in place of config.settings.RENDER_SCALE — this menu's own sizing is
# cosmetic UI scale, not a world/camera transform, so it's pinned here at the
# engine's current default (4) instead of tracking that setting. Changing
# RENDER_SCALE elsewhere no longer resizes anything in this file.
RENDER_SCALE = 4
_S = max(1, RENDER_SCALE)

TABS = ['STATUS', 'INVENTORY', 'EQUIP', 'OPTIONS', 'JOURNAL']

# Equip slots, in the fixed order they're shown/cycled through on the Equip
# tab. EQUIP_SLOT_KEYS match the 'slot' value on each equippable item's data
# dict (see core/items.py) and the keys used in player.equipped (managed by
# systems/item_effects.py's equip_item()/unequip_item() — NOT written to
# directly from here). EQUIP_SLOT_LABELS are the display names shown in the
# menu. EQUIP_SLOT_CATEGORIES are each slot's item category, for filtering
# the catalog down to just what fits that slot.
EQUIP_SLOT_KEYS       = ['body', 'hands', 'feet', 'accessory']
EQUIP_SLOT_LABELS     = ['Body', 'Hands', 'Feet', 'Accessories']
EQUIP_SLOT_CATEGORIES = [CATEGORY_EQUIP_BODY, CATEGORY_EQUIP_HANDS, CATEGORY_EQUIP_FEET, CATEGORY_EQUIP_ACCESSORY]

# Stats that stat points can be allocated into on the Status page. SPD is
# deliberately excluded — it has no allocation cursor/arrow and is skipped
# by up/down navigation while allocating.
STAT_ALLOC_LABELS = ('STR', 'POW', 'END')


class FlatBitmapFont:
    """
    Loads glyphs from a single flat folder (no subdirectories).
    A.png–Z.png for letters, 0.png–9.png for digits, plus a handful of
    special-character filenames. Falls back to pygame's built-in font if the
    folder doesn't exist or nothing loads.
    """

    def __init__(self, folder, letter_spacing=2, scale=1.0):
        self.folder         = folder
        self.letter_spacing = letter_spacing
        self.scale          = scale
        self.glyphs         = {}
        self.fallback_font  = None
        self._load()

    def _load(self):
        if not os.path.exists(self.folder):
            return
        for ch in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
            self._try_load(ch, f'{ch}.png')
        special = {
            ':': 'colon.png', '/': 'slash.png', '.': 'period.png',
            ',': 'comma.png', '!': 'exclamation.png', '?': 'question.png',
            '(': 'open_paren.png', ')': 'close_paren.png',
            '-': 'dash.png', "'": 'apostrophe.png',
        }
        for ch, fname in special.items():
            self._try_load(ch, fname)

    def _try_load(self, char, filename):
        path = os.path.join(self.folder, filename)
        if not os.path.exists(path):
            return
        try:
            img = pygame.image.load(path).convert_alpha()
            if self.scale != 1.0:
                img = pygame.transform.scale(img, (
                    int(img.get_width()  * self.scale),
                    int(img.get_height() * self.scale),
                ))
            self.glyphs[char] = img
        except Exception as e:
            print(f"FlatBitmapFont: could not load {path}: {e}")

    def render(self, text):
        text = text.upper()
        if not self.glyphs:
            if not self.fallback_font:
                self.fallback_font = pygame.font.Font(None, int(28 * self.scale))
            return self.fallback_font.render(text, True, (255, 255, 0))

        total_w = 0
        max_h   = 0
        for ch in text:
            if ch in self.glyphs:
                total_w += self.glyphs[ch].get_width() + self.letter_spacing
                max_h    = max(max_h, self.glyphs[ch].get_height())
            elif ch == ' ':
                total_w += int(8 * self.scale)

        total_w = max(0, total_w - self.letter_spacing)
        if total_w == 0 or max_h == 0:
            return pygame.Surface((1, 1), pygame.SRCALPHA)

        surf = pygame.Surface((total_w, max_h), pygame.SRCALPHA)
        x = 0
        for ch in text:
            if ch in self.glyphs:
                g = self.glyphs[ch]
                surf.blit(g, (x, max_h - g.get_height()))
                x += g.get_width() + self.letter_spacing
            elif ch == ' ':
                x += int(8 * self.scale)
        return surf

    def get_line_height(self):
        if not self.glyphs:
            return int(28 * self.scale)
        return max(g.get_height() for g in self.glyphs.values())


class PauseMenu:

    def __init__(self, screen_width, screen_height):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.active        = False
        self.tab_index     = 0

        self.equip_slot_index   = 0

        # Item-browsing sub-mode of the Equip tab. Pressing Select on one of
        # the 4 slots swaps the slot list for a scrollable list of every
        # owned item that fits that slot (mirrors the Inventory tab's list).
        # B backs out to the 4-slot list without closing the whole menu.
        self.equip_browsing_items = False
        self.equip_item_index     = 0
        self.equip_item_scroll    = 0
        self._equip_rows_visible  = 1
        # item_id -> display name, so the slot list can show what's equipped
        # without re-scanning the item catalog every frame.
        self._equip_name_cache    = {}

        # Equip-confirm popup. Pressing Select on a row in the equip
        # item-browse list opens this smaller bordered window instead of
        # immediately equipping/unequipping — mirrors item_confirm_open/
        # _pending_item_id below (Inventory tab), but with two selectable
        # options ("Equip to <Character>" / "Drop") instead of Use/Cancel
        # A/B buttons. equip_confirm_option: 0 = Equip, 1 = Drop.
        self.equip_confirm_open     = False
        self._pending_equip_item_id = None
        self.equip_confirm_option   = 0

        self.options_item_index = 0   # 0–4: SFX, Music, TextSpeed, Credits, Sleep
        self.options_editing    = False
        self.options_values     = [1, 1, 1]  # 0.0–1.0 per bar
        self.options_step       = 0.2         # ← how much left/right changes a bar

        # Standalone/"restricted" mode — used by TitleScreen to show just
        # this menu's Options tab from the main menu, without a Player and
        # without the L/R shoulder-button sprites or the tab-name text that
        # flank them (Status/Inventory/Equip/Journal don't exist pre-game,
        # so there's nothing to page L/R between). See open_options_only().
        self.restricted_mode    = False

        # Word gap for the inventory empty-state message ("No Supplies" /
        # "No items."). Tweak this to widen/narrow the space between the
        # words — kept separate from the default word gap used elsewhere
        # so it can be adjusted without affecting other text in the menu.
        self.inv_empty_word_gap = int(2 * _S)

        self._sound_engine    = None
        self._click_zones: dict = {}  # rebuilt each frame

        # Canvas — same proportions as CharacterSwitchMenu
        self.canvas_width  = int(screen_width  * 0.85)
        self.canvas_height = int(screen_height * 0.75)
        self.canvas_x      = (screen_width  - self.canvas_width)  // 2
        self.canvas_y      = (screen_height - self.canvas_height) // 2

        self._tab_strip_h = max(20, int(screen_height * 0.045))

        self.button_states       = {'b': False, 'l': False, 'r': False, 'a': False}
        self.button_press_timers = {'b': 0.0,   'l': 0.0,   'r': 0.0,  'a': 0.0}
        self.button_press_duration = 0.1

        # Item-confirm popup's own A/B press flash — kept separate from
        # self.button_states/self.button_press_timers above. Those are
        # shared by every A/B/L/R sprite drawn on the main panel, so
        # reusing them here meant opening the popup (which flashes the
        # inventory row's A via _press('a')) made the popup's own A button
        # render as already-pressed on its very first frame, before the
        # user had touched anything inside the popup itself.
        self.popup_button_states       = {'a': False, 'b': False}
        self.popup_button_press_timers = {'a': 0.0,   'b': 0.0}

        # Blink timer for the "LevelUp!" banner shown above the Pts. row
        # whenever there are stat points to spend — see _draw_status_page.
        # Also reused to blink the stat-allocation cursor arrow, since the
        # two things are never shown at the same time (the banner hides
        # while allocating).
        self._levelup_blink_timer    = 0.0
        self._levelup_blink_interval = 0.2  # seconds visible, then seconds hidden

        # Stat-point allocation mode (Status tab). Entered by pressing A on
        # "Use Points" when the active player has unspent stat points.
        # self._session_stat_alloc tracks how many points were added to each
        # stat *during this allocation session only* — that's what limits
        # left/right's "remove" action to points just added, not a
        # character's stats from before this session opened.
        self.allocating_stats     = False
        self.stat_alloc_index     = 0   # index into STAT_ALLOC_LABELS
        self._session_stat_alloc  = {lbl: 0 for lbl in STAT_ALLOC_LABELS}
        self._player               = None  # cached ref so handle_input() can mutate stats without a player arg

        # Colors — match CharacterSwitchMenu exactly
        self.bg_scanline_dark  = (0,   80,  0)
        self.bg_scanline_light = (0,  100,  0)
        self.border_outer      = (255, 215, 0)
        self.border_inner      = (180, 100, 0)
        self.border_green      = (0,   255, 0)
        self.text_color        = (255, 255, 0)
        self.text_shadow_color = (0,   0,   0)
        self.shadow_offset     = (1, 1)

        self.bg_offset_x = 0
        self.bg_offset_y = 0

        # Fonts
        font_scale = 4
        self.font_scale = font_scale  # stashed on self so _load_ui_sprites (background texture) can match it
        self.bitmap_font = BitmapFont(
            'assets/ui/fonts',
            letter_spacing=max(1, int(2 / RENDER_SCALE)),
            scale=font_scale
        )
        self.bold_font             = FlatBitmapFont('assets/ui/fonts/bold',           letter_spacing=max(4, int(10/RENDER_SCALE)), scale=font_scale)
        self.bold_lowercase_font   = FlatBitmapFont('assets/ui/fonts/bold_lowercase',  letter_spacing=max(4, int(10/RENDER_SCALE)), scale=font_scale)
        self.menu_uppercase_font   = FlatBitmapFont('assets/ui/fonts/uppercase_menu',  letter_spacing=max(5, int(10/RENDER_SCALE)), scale=font_scale)
        self.menu_lowercase_font   = FlatBitmapFont('assets/ui/fonts/lowercase_menu',  letter_spacing=max(5, int(10/RENDER_SCALE)), scale=font_scale)
        self.bold_numbers_font     = FlatBitmapFont('assets/ui/fonts/bold_numbers',    letter_spacing=max(0, int(0 /RENDER_SCALE)) - 1, scale=font_scale)
        self.stats_font            = FlatBitmapFont('assets/ui/fonts/stats',           letter_spacing=max(6, int(10/RENDER_SCALE)), scale=font_scale)
        self.stats_numbers_font    = FlatBitmapFont('assets/ui/fonts/numbers',         letter_spacing=max(1, int(2 /RENDER_SCALE)), scale=font_scale)

        # Borrow the slash glyph from the stats folder into the numbers font
        _slash_path = 'assets/ui/fonts/stats/slash.png'
        if os.path.exists(_slash_path):
            try:
                _slash = pygame.image.load(_slash_path).convert_alpha()
                if font_scale != 1.0:
                    _slash = pygame.transform.scale(_slash, (
                        round(_slash.get_width()  * font_scale),
                        round(_slash.get_height() * font_scale),
                    ))
                self.stats_numbers_font.glyphs['/'] = _slash
            except Exception:
                pass

        # Character sprite cache
        self._char_sprite_cache = {}
        self._current_char_id   = None
        self._current_char_key  = None
        self._char_sprite       = None
        # Which character's status is currently being previewed on the Status
        # page. Defaults to the active player's character on open(); clicking
        # another character's name tag switches this without changing who
        # you're actually playing as.
        self._viewed_char_id    = None
        self.char_name_sprites  = {}
        # Status page name-tag row: which index into the roster list the
        # row is scrolled to, plus press-feedback timers for the left/
        # right scroll arrows — mirrors scroll_up_timer/scroll_down_timer.
        self._status_char_scroll       = 0
        self.status_scroll_left_timer  = 0.0
        self.status_scroll_right_timer = 0.0

        self._load_ui_sprites()

    # ── Asset loading ─────────────────────────────────────────────────────────

    def _load_ui_sprites(self):
        def _img(path):
            try:    return pygame.image.load(path).convert_alpha()
            except: return None

        self.button_b         = _img('assets/ui/buttons/button_b.png')
        self.button_b_pressed = _img('assets/ui/buttons/button_b_pressed.png')
        self.button_a         = _img('assets/ui/buttons/button_a.png')
        self.button_a_pressed = _img('assets/ui/buttons/button_a_pressed.png')
        self.button_l         = _img('assets/ui/buttons/button_l.png')
        self.button_l_pressed = _img('assets/ui/buttons/button_l_pressed.png')
        self.button_r         = _img('assets/ui/buttons/button_r.png')
        self.button_r_pressed = _img('assets/ui/buttons/button_r_pressed.png')

        self.box_sprite       = _img('assets/ui/textbox/border.png')
        self.spacing_bar      = _img('assets/ui/textbox/spacing_bar.png')
        self.hpepbar_black    = _img('assets/ui/textbox/inventory/hpepbar_black.png')
        self.hpbar            = _img('assets/ui/textbox/inventory/hpbar.png')
        self.epbar            = _img('assets/ui/textbox/inventory/epbar.png')

        self.equip_body        = _img('assets/ui/textbox/equip/body.png')
        self.equip_hands       = _img('assets/ui/textbox/equip/hands.png')
        self.equip_feet        = _img('assets/ui/textbox/equip/feet.png')
        self.equip_accessories = _img('assets/ui/textbox/equip/accessories.png')
        self.equip_arrow       = _img('assets/ui/textbox/arrow.png')

        self.optionbar_empty  = _img('assets/ui/textbox/options/optionbar_empty.png')
        self.optionbar_filled = _img('assets/ui/textbox/options/optionbar_filled.png')

        # Character display names (from the character creator's saved config),
        # keyed by char_id. Populated lazily in _get_display_name() and reset
        # whenever the roster refreshes so edits made in the character creator
        # show up without a restart.
        self._display_name_cache = {}

        # Character name tag sprites (selected / unselected variant per character).
        # Rebuilt dynamically from the roster each time the menu opens (see
        # _refresh_name_sprites / open()) so new/removed characters show up
        # without restarting the game. _name_sprite_img_cache avoids re-reading
        # a character's images off disk once they've been loaded.
        self._name_sprite_img_cache = {}
        self._name_sprites = {}
        self._refresh_name_sprites()

        # Inventory sub-tab sprites
        self._inv_tabs = [
            {'id': 'supplies',   'selected': _img('assets/ui/textbox/inventory/supplies_selected.png'),   'unselected': _img('assets/ui/textbox/inventory/supplies_unselected.png')},
            {'id': 'storyitems', 'selected': _img('assets/ui/textbox/inventory/storyitems_selected.png'), 'unselected': _img('assets/ui/textbox/inventory/storyitems_unselected.png')},
        ]
        self.inventory_tab_index = 0

        # Item icons (assets/sprites/items/<item_id>.png), loaded lazily the
        # first time each item is drawn so adding new items never requires
        # touching this file. Missing sprites fall back to a text-only row.
        self._item_icon_cache = {}

        # Cursor into the currently visible supplies/story-items list —
        # separate from inv_scroll_offset, which is how far the *window*
        # into that list has scrolled. UP/DOWN move the cursor and only
        # push inv_scroll_offset when the cursor would go off-screen.
        self.inv_selected_index = 0
        self._inv_rows_visible  = 1

        # Item-use confirmation popup. Pressing Select on an inventory row
        # opens this smaller bordered window instead of immediately using
        # the item; self._pending_item_id holds which row it was opened
        # for. Contents (Use/Cancel etc.) come in a later step — for now
        # it just opens and closes.
        self.item_confirm_open = False
        self._pending_item_id  = None


        # Brief on-screen confirmation ("Used Rice Ball! Restores 40 HP")
        # shown after a successful/failed item use — set via
        # flash_item_message(), called by game.py once it has actually
        # applied the effect.
        self._item_feedback_text    = ''
        self._item_feedback_timer   = 0.0
        self._item_feedback_duration = 2.0

        # Journal quest-type icons
        self._quest_type_sprites = {
            'main':  _img('assets/ui/textbox/journal/main.png'),
            'side':  _img('assets/ui/textbox/journal/Side.png'),
            'other': _img('assets/ui/textbox/journal/Other.png'),
        }
        self._quest_sprite_h = max(16, int(self.canvas_height * 0.08))  # ← change 0.08 to resize

        # Journal sub-tab sprites
        self._journal_tabs = [
            {'id': 'goals',          'selected': _img('assets/ui/textbox/journal/goals_selected.png'),          'unselected': _img('assets/ui/textbox/journal/goals_unselected.png')},
            {'id': 'completedgoals', 'selected': _img('assets/ui/textbox/journal/completedgoals_selected.png'), 'unselected': _img('assets/ui/textbox/journal/completedgoals_unselected.png')},
        ]
        self.journal_tab_index = 0
        self._mission_manager  = None
        self._journal_scroll   = 0

        # Scroll arrows (used in inventory and journal)
        self.arrow_up           = _img('assets/ui/buttons/arrow_up.png')
        self.arrow_up_pressed   = _img('assets/ui/buttons/arrow_up_pressed.png')
        self.arrow_up_grey      = _img('assets/ui/buttons/arrow_up_greyed.png')
        self.arrow_down         = _img('assets/ui/buttons/arrow_down.png')
        self.arrow_down_pressed = _img('assets/ui/buttons/arrow_down_pressed.png')
        self.arrow_down_grey    = _img('assets/ui/buttons/arrow_down_greyed.png')

        # Left/right scroll arrows for the Status page's name-tag row.
        self.status_arrow_right        = _img('assets/ui/buttons/arrow_right.png')
        self.status_arrow_right_pressed = _img('assets/ui/buttons/arrow_right_pressed.png')
        self.status_arrow_right_grey    = _img('assets/ui/buttons/arrow_right_greyed.png')
        self.status_arrow_left          = _img('assets/ui/buttons/arrow_left.png')
        self.status_arrow_left_pressed  = _img('assets/ui/buttons/arrow_left_pressed.png')
        self.status_arrow_left_grey     = _img('assets/ui/buttons/arrow_left_greyed.png')

        self.inv_scroll_offset    = 0
        self.inv_scroll_max       = 0
        self.scroll_up_timer      = 0.0
        self.scroll_down_timer    = 0.0
        self.scroll_press_duration = 0.15

        # Pre-render the tiled background as one big surface to avoid seams
        raw = _img('assets/ui/textbox/background_texture.png')
        if raw:
            # Match the fonts' pixel scale exactly (font_scale, set above) rather
            # than deriving a separate scale from _S — in the original game one
            # pixel of a letter glyph equals one pixel of a background row, and
            # font_scale is a flat constant independent of RENDER_SCALE, so the
            # background has to use that same constant to stay aligned with it.
            scale  = self.font_scale
            tile_w = round(raw.get_width()  * scale)
            tile_h = round(raw.get_height() * scale)
            tile   = pygame.transform.scale(raw, (tile_w, tile_h))
            cols   = (self.screen_width  // tile_w) + 2
            rows   = (self.screen_height // tile_h) + 2
            surf   = pygame.Surface((cols * tile_w, rows * tile_h), pygame.SRCALPHA)
            for ty in range(rows):
                for tx in range(cols):
                    surf.blit(tile, (tx * tile_w, ty * tile_h))
            self.bg_texture  = surf
            self._bg_tile_w  = tile_w
            self._bg_tile_h  = tile_h
        else:
            self.bg_texture = None
            self._bg_tile_w = 1
            self._bg_tile_h = 1

    def _refresh_name_sprites(self):
        """
        Rebuild self._name_sprites from the current character roster.

        Called on init and every time the menu opens, so adding/removing a
        character (e.g. via the character creator dev tool) shows up in the
        Status page name-tag row without needing a game restart. Already-loaded
        images are kept in _name_sprite_img_cache so this doesn't re-hit disk
        for characters that haven't changed.
        """
        def _img(path):
            try:    return pygame.image.load(path).convert_alpha()
            except: return None

        roster = discover_characters()
        fresh  = {}
        for cid in roster:
            if cid in self._name_sprite_img_cache:
                sel, unsel = self._name_sprite_img_cache[cid]
            else:
                sel   = _img(f'assets/ui/textbox/names/{cid}_selected.png')
                unsel = _img(f'assets/ui/textbox/names/{cid}_unselected.png')
                self._name_sprite_img_cache[cid] = (sel, unsel)
            if sel or unsel:
                fresh[cid] = {'selected': sel, 'unselected': unsel}
        self._name_sprites = fresh
        self._display_name_cache = {}

    def _get_display_name(self, char_id):
        """
        Return the character's display name as set in the character creator
        (assets/characters/{char_id}.json), falling back to a title-cased
        version of char_id if no config exists. Cached per open() so we don't
        hit disk every frame; the cache is cleared in _refresh_name_sprites().
        """
        if char_id not in self._display_name_cache:
            try:
                name = load_config(char_id).get('display_name') or char_id.replace('_', ' ').title()
            except Exception:
                name = char_id.replace('_', ' ').title()
            self._display_name_cache[char_id] = name
        return self._display_name_cache[char_id]

    def _active_transform_form(self, player):
        """Return the bare form name (e.g. 'ssj') of the player's currently
        active transformation, or '' if not transformed. Sourced from
        TransformationSystem.current_transform_costume, which is a path like
        'base/transformations/ssj' — only set while is_transformed is True.
        """
        tf = getattr(player, 'transformation', None)
        if not tf or not getattr(tf, 'is_transformed', False):
            return ''
        full = getattr(tf, 'current_transform_costume', None) or ''
        if '/transformations/' in full:
            return full.split('/transformations/')[-1]
        return ''

    def _load_char_sprite(self, char_id, costume='base', form=''):
        cache_key = (char_id, costume, form)
        if cache_key in self._char_sprite_cache:
            return self._char_sprite_cache[cache_key]
        surf = None
        path = resolve_portrait_path(char_id, costume, form)
        if path:
            try:
                surf = pygame.image.load(str(path)).convert_alpha()
            except Exception:
                surf = None
        self._char_sprite_cache[cache_key] = surf
        return surf

    # ── Open / close ──────────────────────────────────────────────────────────

    def open(self, player=None):
        self.active              = True
        self.restricted_mode     = False
        if player is not None:
            self._refresh_name_sprites()
        self.tab_index           = 0
        self.equip_slot_index    = 0
        self.equip_browsing_items = False
        self.equip_item_index     = 0
        self.equip_item_scroll    = 0
        self.options_item_index  = 0
        self.options_editing     = False
        self.inventory_tab_index = 0
        self.journal_tab_index   = 0
        self._journal_scroll     = 0
        self.inv_scroll_offset   = 0
        self.inv_selected_index  = 0
        self.item_confirm_open   = False
        self._pending_item_id    = None
        self.equip_confirm_open      = False
        self._pending_equip_item_id  = None
        self.equip_confirm_option    = 0
        self._item_feedback_text  = ''
        self._item_feedback_timer = 0.0
        self.scroll_up_timer     = 0.0
        self.scroll_down_timer   = 0.0
        self._status_char_scroll       = 0
        self.status_scroll_left_timer  = 0.0
        self.status_scroll_right_timer = 0.0
        self.allocating_stats    = False
        self.stat_alloc_index    = 0
        self._session_stat_alloc = {lbl: 0 for lbl in STAT_ALLOC_LABELS}
        self._player              = player
        for btn in self.button_states:
            self.button_states[btn]       = False
            self.button_press_timers[btn] = 0.0
        if player is not None:
            char_id = getattr(player, 'character', 'goku')
            costume = getattr(player, 'costume', 'base')
            form    = self._active_transform_form(player)
            char_key = (char_id, costume, form)
            if char_key != self._current_char_key:
                self._current_char_key = char_key
                self._current_char_id  = char_id
                self._char_sprite      = self._load_char_sprite(char_id, costume, form)
            self._viewed_char_id = char_id
        else:
            self._viewed_char_id = None

    def open_options_only(self):
        """Standalone Options-tab mode, opened from TitleScreen's main menu.

        Same visuals/behavior as the in-game pause menu's Options tab
        (SFX/Music/Text Speed bars, Credits, Sleep — whatever's on that
        page stays as-is), but locked there: no Player is required, L/R
        can't page to Status/Inventory/Equip/Journal, and the L/R button
        sprites plus their flanking tab-name text aren't drawn. Close the
        same way as the in-game pause menu (B/ESC, or Credits/Sleep if
        those navigate elsewhere) — handle_input() still returns 'close'.
        """
        self.open(player=None)
        self.tab_index       = 3   # OPTIONS
        self.restricted_mode = True

    def close(self):
        self.active = False

    def set_mission_manager(self, mm):
        """Wire in the MissionManager so the journal page can read live quest state."""
        self._mission_manager = mm

    def set_sound_engine(self, sound_engine):
        """Call from game.py after construction to hook up volume control."""
        self._sound_engine = sound_engine
        self.options_values[0] = sound_engine.sfx_volume
        self.options_values[1] = sound_engine.music_volume

    def _apply_volume(self, idx):
        if not self._sound_engine:
            return
        if idx == 0:
            self._sound_engine.set_sfx_volume(self.options_values[0])
        elif idx == 1:
            self._sound_engine.set_music_volume(self.options_values[1])

    def _play_switch_sfx(self):
        """Play the L/R tab-switch sound, if a sound engine has been wired up."""
        if self._sound_engine:
            self._sound_engine.play_sound('switch')

    def _play_select_sfx(self):
        """Play the confirm/select sound (A button), if a sound engine has been wired up."""
        if self._sound_engine:
            self._sound_engine.play_sound('select')

    # ── Stat point allocation (Status tab) ──────────────────────────────────

    def _status_points_context(self):
        """
        Returns (stat_points, show_use_points) for the currently viewed
        character on the Status tab. Mirrors the check draw() uses to decide
        whether the A button reads "Use Points" — factored out so
        handle_input() can use the exact same logic without a player arg.
        """
        if not self._player:
            return 0, False
        active_char_id = getattr(self._player, 'character', 'unknown')
        viewed_char_id = self._viewed_char_id or active_char_id
        is_active_char = viewed_char_id == active_char_id
        stat_points     = getattr(self._player, 'stat_points', 0) if is_active_char else 0
        show_use_points = TABS[self.tab_index] == 'STATUS' and is_active_char and stat_points > 0
        return stat_points, show_use_points

    def _enter_stat_allocation(self):
        if not self._player:
            return
        self.allocating_stats    = True
        self.stat_alloc_index    = 0
        self._session_stat_alloc = {lbl: 0 for lbl in STAT_ALLOC_LABELS}
        self._press('a')
        self._play_select_sfx()

    def _exit_stat_allocation(self):
        """Confirms/accepts the current allocation. Points already spent stay
        spent — this just closes the allocation UI and resets the
        this-session tracker, so a later re-entry can't remove them anymore.
        """
        self.allocating_stats    = False
        self._session_stat_alloc = {lbl: 0 for lbl in STAT_ALLOC_LABELS}
        self._press('a')
        self._play_select_sfx()

    def _get_player_stats_dict(self):
        if self._player is None:
            return None
        stats = getattr(self._player, 'stats', None)
        if stats is None:
            stats = {}
            try:
                setattr(self._player, 'stats', stats)
            except Exception:
                return None
        return stats

    @staticmethod
    def _stat_key_for(stats, label):
        """Resolve STR/POW/END to whichever key the player's stats dict
        actually uses (full name vs. short name — see the fallbacks used
        throughout _draw_status_page)."""
        candidates = {
            'STR': ('strength', 'str'),
            'POW': ('ki_power', 'pow'),
            'END': ('vitality',  'end'),
        }[label]
        for k in candidates:
            if k in stats:
                return k
        return candidates[0]

    def _stat_alloc_add(self):
        """Right/+: spend one unspent stat point into the selected stat."""
        if getattr(self._player, 'stat_points', 0) <= 0:
            return
        stats = self._get_player_stats_dict()
        if stats is None:
            return
        label = STAT_ALLOC_LABELS[self.stat_alloc_index]
        key   = self._stat_key_for(stats, label)
        stats[key] = stats.get(key, 0) + 1
        self._player.stat_points   = getattr(self._player, 'stat_points', 0) - 1
        self._session_stat_alloc[label] += 1
        self._play_switch_sfx()

    def _stat_alloc_remove(self):
        """Left/-: give back one point, but only if it was added to this stat
        during the current allocation session — points the character already
        had before opening this menu can't be pulled back out."""
        label = STAT_ALLOC_LABELS[self.stat_alloc_index]
        if self._session_stat_alloc.get(label, 0) <= 0:
            return
        stats = self._get_player_stats_dict()
        if stats is None:
            return
        key = self._stat_key_for(stats, label)
        stats[key] = max(0, stats.get(key, 0) - 1)
        self._player.stat_points   = getattr(self._player, 'stat_points', 0) + 1
        self._session_stat_alloc[label] -= 1
        self._play_switch_sfx()

    # ── Inventory (Supplies / Story Items) ──────────────────────────────────

    def _current_inventory_category(self):
        return CATEGORY_STORY_ITEMS if self.inventory_tab_index == 1 else CATEGORY_SUPPLIES

    def _get_inventory_entries(self, player):
        """
        Returns a list of (item_id, item_data, count) for whichever
        sub-tab (Supplies / Story Items) is currently selected, sorted by
        display name. Counts come straight from player.inventory, which is
        a flat list of item_id strings (same convention used by mission
        rewards, dialogue rewards, and flag_manager's player_has_item).
        """
        inventory = getattr(player, 'inventory', None) or []
        catalog   = get_items_by_category(self._current_inventory_category())
        entries   = []
        for item_id, data in catalog.items():
            count = inventory.count(item_id)
            if count > 0:
                entries.append((item_id, data, count))
        entries.sort(key=lambda e: e[1]['name'])
        return entries

    def _get_item_icon(self, item_id):
        if item_id not in self._item_icon_cache:
            path = self._item_icon_path(item_id)
            try:
                self._item_icon_cache[item_id] = pygame.image.load(path).convert_alpha()
            except Exception:
                self._item_icon_cache[item_id] = None
        return self._item_icon_cache[item_id]

    def _item_icon_path(self, item_id):
        """Equip items (body/hands/feet/accessory) live under a per-slot
        equipment/ subfolder rather than the flat items/ folder consumables
        and story items use — e.g. 'dirty_shirt' (slot 'body') is at
        assets/sprites/items/equipment/body/dirty_shirt.png, not
        assets/sprites/items/dirty_shirt.png."""
        data = get_item(item_id) or {}
        slot = data.get('slot')
        if slot:
            return f'assets/sprites/items/equipment/{slot}/{item_id}.png'
        return f'assets/sprites/items/{item_id}.png'

    # ── Equip (item-browsing sub-mode) ───────────────────────────────────────
    #
    # Actually equipping/unequipping (and applying the item's stat bonuses)
    # is systems/item_effects.py's job — equip_item(player, item_id) and
    # unequip_item(player, slot). This menu only reads player.equipped for
    # display and, on Select, returns an 'equip_item:<item_id>' signal for
    # game.py to hand to equip_item(), the same way 'use_item:<id>' already
    # works for Inventory. It never writes to player.equipped itself.

    def _get_equip_item_name(self, item_id):
        """Display name for an equipped item_id, cached across frames."""
        if item_id is None:
            return None
        if item_id not in self._equip_name_cache:
            data = get_item(item_id) or {}
            self._equip_name_cache[item_id] = data.get('name', item_id)
        return self._equip_name_cache[item_id]

    def _get_equip_entries(self, player, slot_index):
        """Owned items that fit the given equip slot, sorted by name —
        mirrors _get_inventory_entries but filtered to that slot's own
        category (CATEGORY_EQUIP_BODY etc.)."""
        inventory = getattr(player, 'inventory', None) or []
        category  = EQUIP_SLOT_CATEGORIES[slot_index]
        catalog   = get_items_by_category(category)
        entries   = []
        for item_id, data in catalog.items():
            count = inventory.count(item_id)
            if count > 0:
                entries.append((item_id, data, count))
        entries.sort(key=lambda e: e[1]['name'])
        return entries

    def _enter_equip_browse(self):
        """Select on a slot in the 4-slot list: swap to that slot's owned
        items."""
        self.equip_browsing_items = True
        self.equip_item_index     = 0
        self.equip_item_scroll    = 0
        self._press('a')
        self._play_select_sfx()

    def _exit_equip_browse(self):
        """B on the item list: back out to the 4-slot list without closing
        the pause menu."""
        self.equip_browsing_items = False
        self._press('b')
        self._play_switch_sfx()

    def _select_equip_item(self):
        """Select on a row in the item list no longer equips immediately —
        it opens the equip-confirm popup first (mirrors _use_selected_item
        on the Inventory tab). The actual equip_item:/unequip_item:/
        drop_item: signal is returned from _confirm_equip_action() once the
        popup's own Equip/Drop option is chosen. No-ops if the list is
        empty."""
        entries = self._get_equip_entries(self._player, self.equip_slot_index) if self._player else []
        if not entries or not (0 <= self.equip_item_index < len(entries)):
            return None
        item_id = entries[self.equip_item_index][0]
        self._pending_equip_item_id = item_id
        self.equip_confirm_open     = True
        self.equip_confirm_option   = 0
        self.popup_button_states['a']       = False
        self.popup_button_states['b']       = False
        self.popup_button_press_timers['a'] = 0.0
        self.popup_button_press_timers['b'] = 0.0
        # Freeze (not flash) the underlying list's A button while the popup
        # is up: state True but timer left at 0 so update()'s countdown
        # never fires and reverts it. It's released in
        # _confirm_equip_action()/_cancel_equip_confirm() once the popup
        # closes.
        self.button_states['a']       = True
        self.button_press_timers['a'] = 0.0
        self._play_select_sfx()
        return None

    def _confirm_equip_action(self):
        """A / Select on the equip-confirm popup's highlighted option.
        Option 0 ('Equip to <Character>') is the old _select_equip_item
        body: signal game.py to equip the item (via
        systems.item_effects.equip_item), or — if it's already equipped in
        this slot — to unequip it instead (equip_item() would just reject
        a re-equip of the same item with an 'already equipped' message, so
        unequip is the useful action there) — either way, closes the popup
        and backs out to the 4-slot list. Option 1 ('Drop') signals game.py
        to drop it from the inventory instead — that one stays on the item
        list (equip_browsing_items untouched) instead of backing out: the
        item just disappears from the list on its own once game.py removes
        it from the inventory (see _get_equip_entries/_draw_equip_list,
        which already re-clamp equip_item_index/scroll to the new, shorter
        list), and the actual world-drop only happens once the whole pause
        menu closes (see game.py's handling of 'drop_item:')."""
        item_id  = self._pending_equip_item_id
        slot_key = EQUIP_SLOT_KEYS[self.equip_slot_index]
        is_drop  = self.equip_confirm_option == 1
        self.equip_confirm_open     = False
        self._pending_equip_item_id = None
        if not is_drop:
            self.equip_browsing_items = False
        self.button_states['a']       = False
        self.button_press_timers['a'] = 0.0
        self._press_popup('a')
        self._play_select_sfx()
        if item_id is None:
            return None
        if is_drop:
            return f'drop_item:{item_id}'
        equipped = getattr(self._player, 'equipped', None) or {}
        if equipped.get(slot_key) == item_id:
            return f'unequip_item:{slot_key}'
        return f'equip_item:{item_id}'

    def _cancel_equip_confirm(self):
        """B / Cancel on the equip-confirm popup — just closes it, no
        equip/drop action taken. Stays on the item list (unlike a
        successful confirm, which backs out to the 4-slot list)."""
        self._press_popup('b')
        self.equip_confirm_open     = False
        self._pending_equip_item_id = None
        self.button_states['a']       = False
        self.button_press_timers['a'] = 0.0


    def flash_item_message(self, text):
        """Called by game.py after it applies (or fails to apply) an item's
        effect, so the result shows up right in the inventory panel."""
        self._item_feedback_text  = text
        self._item_feedback_timer = self._item_feedback_duration

    def _use_selected_item(self):
        """Highlighting a row and pressing Select no longer uses the item
        immediately — it opens the smaller item-confirm popup first. The
        actual 'use_item:<id>' signal will be returned from inside that
        popup once its own Use/Cancel options are wired up."""
        entries = self._get_inventory_entries(self._player) if self._player else []
        if not entries or not (0 <= self.inv_selected_index < len(entries)):
            return None
        item_id = entries[self.inv_selected_index][0]
        self._pending_item_id  = item_id
        self.item_confirm_open = True
        self.popup_button_states['a']       = False
        self.popup_button_states['b']       = False
        self.popup_button_press_timers['a'] = 0.0
        self.popup_button_press_timers['b'] = 0.0
        self._press('a')
        self._play_select_sfx()
        return None

    def _confirm_use_item(self):
        """A / Use on the item-confirm popup. Always fires the use_item
        signal for whatever item is pending — no HP-full or other
        eligibility check here; that's game.py's call once it applies the
        effect (and flash_item_message() reports back what happened)."""
        item_id = self._pending_item_id
        self.item_confirm_open = False
        self._pending_item_id  = None
        self._press_popup('a')
        self._play_select_sfx()
        if item_id is None:
            return None
        return f'use_item:{item_id}'

    def _cancel_item_confirm(self):
        """B / Cancel on the item-confirm popup — just closes it, no item used."""
        self._press_popup('b')
        self.item_confirm_open = False
        self._pending_item_id  = None

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self, dt):
        if not self.active:
            return
        if self._item_feedback_timer > 0:
            self._item_feedback_timer = max(0.0, self._item_feedback_timer - dt)
        self._levelup_blink_timer += dt
        for btn in self.button_press_timers:
            if self.button_press_timers[btn] > 0:
                self.button_press_timers[btn] -= dt
                if self.button_press_timers[btn] <= 0:
                    self.button_states[btn] = False
        for btn in self.popup_button_press_timers:
            if self.popup_button_press_timers[btn] > 0:
                self.popup_button_press_timers[btn] -= dt
                if self.popup_button_press_timers[btn] <= 0:
                    self.popup_button_states[btn] = False
        self.scroll_up_timer   = max(0.0, self.scroll_up_timer   - dt)
        self.scroll_down_timer = max(0.0, self.scroll_down_timer - dt)
        self.status_scroll_left_timer  = max(0.0, self.status_scroll_left_timer  - dt)
        self.status_scroll_right_timer = max(0.0, self.status_scroll_right_timer - dt)

    # ── Input ─────────────────────────────────────────────────────────────────

    def handle_input(self, event):
        """
        Returns:
          'close'            — ESC / B
          'open_skills'      — S or Down on Status tab
          'open_credits'     — Z / A (or click) on the Credits row of the
                                Options tab; game.py should open the
                                CreditsScreen in response
          'use_item:<id>'    — Z / A on a highlighted row in Inventory;
                                game.py applies the effect and should call
                                flash_item_message() with the result
          None               — no action taken
        """
        if not self.active:
            return None

        # While the item-confirm popup is open, it owns all input — the
        # underlying inventory list/tabs must not move behind it. For now
        # it only knows how to close itself (B/ESC/X); Use/Cancel options
        # come in a later step.
        if self.item_confirm_open:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                z = self._click_zones

                def _hit(key):
                    r = z.get(key)
                    return r is not None and r.collidepoint(event.pos)

                if _hit('item_confirm_use'):
                    return self._confirm_use_item()
                if _hit('item_confirm_cancel'):
                    self._cancel_item_confirm()
                return None
            if event.type != pygame.KEYDOWN:
                return None
            if event.key in (pygame.K_ESCAPE, pygame.K_x):
                self._cancel_item_confirm()
                return None
            if event.key == pygame.K_z:
                return self._confirm_use_item()
            return None

        # While the equip-confirm popup is open, it owns all input — the
        # underlying item list must not move behind it. UP/DOWN move the
        # cursor between its two options ("Equip to <Character>" / "Drop"),
        # Select confirms whichever is highlighted, B/ESC/X cancels back
        # to the item list without equipping or dropping anything.
        if self.equip_confirm_open:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                z = self._click_zones

                def _hit(key):
                    r = z.get(key)
                    return r is not None and r.collidepoint(event.pos)

                if _hit('equip_confirm_option_0'):
                    self.equip_confirm_option = 0
                    return self._confirm_equip_action()
                if _hit('equip_confirm_option_1'):
                    self.equip_confirm_option = 1
                    return self._confirm_equip_action()
                if _hit('equip_confirm_select'):
                    return self._confirm_equip_action()
                if _hit('equip_confirm_cancel'):
                    self._cancel_equip_confirm()
                return None
            if event.type != pygame.KEYDOWN:
                return None
            if event.key in (pygame.K_ESCAPE, pygame.K_x):
                self._cancel_equip_confirm()
                return None
            if event.key == pygame.K_UP:
                self.equip_confirm_option = 0
                return None
            if event.key == pygame.K_DOWN:
                self.equip_confirm_option = 1
                return None
            if event.key == pygame.K_z:
                return self._confirm_equip_action()
            return None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            return self._handle_mouse_click(event.pos)

        if event.type != pygame.KEYDOWN:
            return None

        key = event.key

        if key in (pygame.K_ESCAPE, pygame.K_x):
            if self.tab_index == 2 and self.equip_browsing_items:
                self._exit_equip_browse()
                return None
            self.button_states['b']       = True
            self.button_press_timers['b'] = self.button_press_duration
            self.close()
            return 'close'

        if key == pygame.K_LEFT and not self.options_editing and not self.allocating_stats and not self.equip_browsing_items and not self.restricted_mode:
            self.tab_index = 4 if self.tab_index == 0 else self.tab_index - 1
            self._press('l')
            self._play_switch_sfx()

        elif key == pygame.K_RIGHT and not self.options_editing and not self.allocating_stats and not self.equip_browsing_items and not self.restricted_mode:
            self.tab_index = 0 if self.tab_index == 4 else self.tab_index + 1
            self._press('r')
            self._play_switch_sfx()

        elif TABS[self.tab_index] == 'STATUS' and self.allocating_stats and key == pygame.K_UP:
            self.stat_alloc_index = (self.stat_alloc_index - 1) % len(STAT_ALLOC_LABELS)

        elif TABS[self.tab_index] == 'STATUS' and self.allocating_stats and key == pygame.K_DOWN:
            self.stat_alloc_index = (self.stat_alloc_index + 1) % len(STAT_ALLOC_LABELS)

        elif TABS[self.tab_index] == 'STATUS' and self.allocating_stats and key == pygame.K_RIGHT:
            self._stat_alloc_add()

        elif TABS[self.tab_index] == 'STATUS' and self.allocating_stats and key == pygame.K_LEFT:
            self._stat_alloc_remove()

        elif TABS[self.tab_index] == 'STATUS' and self.allocating_stats and key == pygame.K_z:
            self._exit_stat_allocation()

        elif TABS[self.tab_index] == 'STATUS' and not self.allocating_stats and key == pygame.K_z:
            _, show_use_points = self._status_points_context()
            if show_use_points:
                self._enter_stat_allocation()

        elif TABS[self.tab_index] == 'INVENTORY' and key == pygame.K_UP:
            if self.inv_selected_index > 0:
                self.inv_selected_index -= 1
                if self.inv_selected_index < self.inv_scroll_offset:
                    self.inv_scroll_offset = self.inv_selected_index
                # Pressed-arrow flash fires on every successful Up press
                # (cursor moved), not only when the scroll window itself
                # shifts — a short list that fits on screen without
                # scrolling still moves the cursor and should still flash.
                self.scroll_up_timer = self.scroll_press_duration

        elif TABS[self.tab_index] == 'INVENTORY' and key == pygame.K_DOWN:
            entry_count = len(self._get_inventory_entries(self._player)) if self._player else 0
            if self.inv_selected_index < entry_count - 1:
                self.inv_selected_index += 1
                if self.inv_selected_index >= self.inv_scroll_offset + self._inv_rows_visible:
                    self.inv_scroll_offset = self.inv_selected_index - self._inv_rows_visible + 1
                self.scroll_down_timer = self.scroll_press_duration

        elif TABS[self.tab_index] == 'INVENTORY' and key == pygame.K_z:
            return self._use_selected_item()

        elif self.tab_index == 4 and key == pygame.K_UP:
            if self._journal_scroll > 0:
                self._journal_scroll -= 1
                self.scroll_up_timer = self.scroll_press_duration

        elif self.tab_index == 4 and key == pygame.K_DOWN:
            if self._journal_scroll < self.inv_scroll_max:
                self._journal_scroll += 1
                self.scroll_down_timer = self.scroll_press_duration

        elif self.tab_index == 4 and key == pygame.K_z:
            self.journal_tab_index = (self.journal_tab_index + 1) % 2
            self._journal_scroll   = 0

        elif self.tab_index == 2 and self.equip_browsing_items and key == pygame.K_UP:
            if self.equip_item_index > 0:
                self.equip_item_index -= 1
                if self.equip_item_index < self.equip_item_scroll:
                    self.equip_item_scroll = self.equip_item_index
                # Same as Inventory above: flash on every successful move,
                # not only when the scroll window shifts.
                self.scroll_up_timer = self.scroll_press_duration

        elif self.tab_index == 2 and self.equip_browsing_items and key == pygame.K_DOWN:
            entry_count = len(self._get_equip_entries(self._player, self.equip_slot_index)) if self._player else 0
            if self.equip_item_index < entry_count - 1:
                self.equip_item_index += 1
                if self.equip_item_index >= self.equip_item_scroll + self._equip_rows_visible:
                    self.equip_item_scroll = self.equip_item_index - self._equip_rows_visible + 1
                self.scroll_down_timer = self.scroll_press_duration

        elif self.tab_index == 2 and self.equip_browsing_items and key == pygame.K_z:
            return self._select_equip_item()

        elif self.tab_index == 2 and not self.equip_browsing_items and key == pygame.K_UP:
            self.equip_slot_index = max(0, self.equip_slot_index - 1)

        elif self.tab_index == 2 and not self.equip_browsing_items and key == pygame.K_DOWN:
            self.equip_slot_index = min(3, self.equip_slot_index + 1)

        elif self.tab_index == 2 and not self.equip_browsing_items and key == pygame.K_z:
            self._enter_equip_browse()

        elif self.tab_index == 3:
            if self.options_editing:
                if key in (pygame.K_z, pygame.K_x, pygame.K_ESCAPE):
                    self.options_editing = False
                elif key == pygame.K_LEFT:
                    idx = self.options_item_index
                    self.options_values[idx] = max(0.0, self.options_values[idx] - self.options_step)
                    self._apply_volume(idx)
                elif key == pygame.K_RIGHT:
                    idx = self.options_item_index
                    self.options_values[idx] = min(1.0, self.options_values[idx] + self.options_step)
                    self._apply_volume(idx)
            else:
                if key == pygame.K_z and self.options_item_index <= 2:
                    self.options_editing = True
                    self._press('a')
                    self._play_select_sfx()
                elif key == pygame.K_z and self.options_item_index == 3:
                    self._press('a')
                    self._play_select_sfx()
                    return 'open_credits'
                elif key == pygame.K_UP:
                    self.options_item_index = 3 if self.options_item_index == 4 else max(0, self.options_item_index - 1)
                elif key == pygame.K_DOWN:
                    self.options_item_index = 4 if self.options_item_index == 3 else min(3, self.options_item_index + 1)
                elif key == pygame.K_RIGHT and self.options_item_index == 3:
                    self.options_item_index = 4
                elif key == pygame.K_LEFT and self.options_item_index == 4:
                    self.options_item_index = 3

        elif TABS[self.tab_index] == 'STATUS' and not self.allocating_stats and key in (pygame.K_s, pygame.K_DOWN):
            return 'open_skills'

        return None

    def _press(self, btn):
        self.button_states[btn]       = True
        self.button_press_timers[btn] = self.button_press_duration

    def _press_popup(self, btn):
        self.popup_button_states[btn]       = True
        self.popup_button_press_timers[btn] = self.button_press_duration

    def _handle_mouse_click(self, pos):
        """Translates a left-click into the equivalent keyboard action using rects stored by draw()."""
        z = self._click_zones

        def _hit(key):
            r = z.get(key)
            return r is not None and r.collidepoint(pos)

        if _hit('b_cancel'):
            if self.tab_index == 2 and self.equip_browsing_items:
                self._exit_equip_browse()
                return None
            self._press('b')
            self.close()
            return 'close'

        if _hit('l_tab') and not self.options_editing and not self.equip_browsing_items and not self.restricted_mode:
            self.tab_index = 4 if self.tab_index == 0 else self.tab_index - 1
            self._press('l')
            self._play_switch_sfx()
            return None

        if _hit('r_tab') and not self.options_editing and not self.equip_browsing_items and not self.restricted_mode:
            self.tab_index = 0 if self.tab_index == 4 else self.tab_index + 1
            self._press('r')
            self._play_switch_sfx()
            return None

        if _hit('a_select') and self.tab_index in (1, 2, 3):
            if self.tab_index == 1:
                return self._use_selected_item()
            if self.tab_index == 2:
                if self.equip_browsing_items:
                    return self._select_equip_item()
                self._enter_equip_browse()
                return None
            if self.tab_index == 3 and self.options_item_index <= 2:
                self.options_editing = not self.options_editing
                self._press('a')
                self._play_select_sfx()
                return None
            if self.tab_index == 3 and self.options_item_index == 3:
                self._press('a')
                self._play_select_sfx()
                return 'open_credits'
            return None

        if _hit('a_use_points') and self.tab_index == 0:
            if self.allocating_stats:
                self._exit_stat_allocation()
            else:
                _, show_use_points = self._status_points_context()
                if show_use_points:
                    self._enter_stat_allocation()
            return None

        equip_browsing = self.tab_index == 2 and self.equip_browsing_items

        if _hit('scroll_up'):
            if TABS[self.tab_index] == 'INVENTORY' and self.inv_scroll_offset > 0:
                self.inv_scroll_offset -= 1
                self.inv_selected_index = min(self.inv_selected_index, self.inv_scroll_offset + self._inv_rows_visible - 1)
                self.scroll_up_timer = self.scroll_press_duration
            elif self.tab_index == 4 and self._journal_scroll > 0:
                self._journal_scroll -= 1
                self.scroll_up_timer = self.scroll_press_duration
            elif equip_browsing and self.equip_item_index > 0:
                self.equip_item_index -= 1
                self.equip_item_scroll = min(self.equip_item_scroll, self.equip_item_index)
                self.scroll_up_timer = self.scroll_press_duration
            return None

        if _hit('scroll_down'):
            if TABS[self.tab_index] == 'INVENTORY' and self.inv_scroll_offset < self.inv_scroll_max:
                self.inv_scroll_offset += 1
                self.inv_selected_index = max(self.inv_selected_index, self.inv_scroll_offset)
                self.scroll_down_timer = self.scroll_press_duration
            elif self.tab_index == 4 and self._journal_scroll < self.inv_scroll_max:
                self._journal_scroll += 1
                self.scroll_down_timer = self.scroll_press_duration
            elif equip_browsing:
                entry_count = len(self._get_equip_entries(self._player, self.equip_slot_index)) if self._player else 0
                if self.equip_item_index < entry_count - 1:
                    self.equip_item_index += 1
                    self.equip_item_scroll = max(self.equip_item_scroll, self.equip_item_index - self._equip_rows_visible + 1)
                    self.scroll_down_timer = self.scroll_press_duration
            return None

        if self.tab_index == 1:
            for i in range(len(self._inv_tabs)):
                if _hit(f'inv_tab_{i}'):
                    self.inventory_tab_index = i
                    self.inv_scroll_offset   = 0
                    self.inv_selected_index  = 0
                    return None
            for i in range(self._inv_rows_visible):
                if _hit(f'inv_row_{i}'):
                    self.inv_selected_index = self.inv_scroll_offset + i
                    return self._use_selected_item()

        if self.tab_index == 4:
            for i in range(len(self._journal_tabs)):
                if _hit(f'journal_tab_{i}'):
                    self.journal_tab_index = i
                    self._journal_scroll   = 0
                    return None

        if self.tab_index == 3:
            for i in range(5):
                if _hit(f'options_row_{i}'):
                    if self.options_item_index == i and i <= 2:
                        self.options_editing = True
                        self._press('a')
                        self._play_select_sfx()
                    elif self.options_item_index == i and i == 3:
                        self._press('a')
                        self._play_select_sfx()
                        return 'open_credits'
                    else:
                        self.options_editing    = False
                        self.options_item_index = i
                    return None

        if self.tab_index == 0:
            for cid in self._name_sprites:
                if _hit(f'name_tag_{cid}'):
                    self._viewed_char_id = cid
                    self._play_switch_sfx()
                    return None

            roster_count = len(self._name_sprites)
            if _hit('status_scroll_left'):
                if self._status_char_scroll > 0:
                    self._status_char_scroll -= 1
                    self.status_scroll_left_timer = self.scroll_press_duration
                return None
            if _hit('status_scroll_right'):
                if self._status_char_scroll < roster_count - 1:
                    self._status_char_scroll += 1
                    self.status_scroll_right_timer = self.scroll_press_duration
                return None

        if self.tab_index == 2:
            if self.equip_browsing_items:
                for i in range(self._equip_rows_visible):
                    if _hit(f'equip_item_{i}'):
                        self.equip_item_index = self.equip_item_scroll + i
                        return self._select_equip_item()
            else:
                for i in range(4):
                    if _hit(f'equip_slot_{i}'):
                        if i == self.equip_slot_index:
                            self._enter_equip_browse()
                        else:
                            self.equip_slot_index = i
                        return None

        return None

    # ══════════════════════════════════════════════════════ draw ══════════════

    def draw(self, screen, player=None, play_time=0.0):
        if not self.active:
            return

        self._click_zones = {}  # rebuilt every frame so positions stay current
        self._player      = player  # keep fresh for handle_input(), which has no player arg

        self._draw_tiled_background(screen, pygame.Rect(0, 0, self.screen_width, self.screen_height))

        # Frame sizing
        title_margin = int(self.canvas_height * 0.08)
        inner_margin = int(self.canvas_height)
        inner_w      = int(self.canvas_width * 1.1 - inner_margin)
        inner_h      = int(self.canvas_height) - 286   # ← frame height: adjust freely
        _layout_h    = int(self.canvas_height) - 221   # ← layout anchor: keep in sync
        box_x        = self.canvas_x + (self.canvas_width - inner_w) // 2
        box_y        = self.canvas_y + title_margin + 1

        drawn = self.box_sprite and self._draw_9slice_sprite(
            screen, self.box_sprite, box_x, box_y, inner_w, inner_h, corner_size=20
        )
        if not drawn:
            pygame.draw.rect(screen, self.border_outer, (box_x-6, box_y-6, inner_w+12, inner_h+12))
            pygame.draw.rect(screen, self.border_inner, (box_x-3, box_y-3, inner_w+6,  inner_h+6))
            pygame.draw.rect(screen, self.border_green, (box_x-1, box_y-1, inner_w+2,  inner_h+2))
            self._draw_tiled_background(screen, pygame.Rect(box_x, box_y, inner_w, inner_h))

        # Page content
        pad          = max(8, int(inner_w * 0.04))
        content_rect = pygame.Rect(box_x + pad, box_y + pad,
                                   inner_w - pad * 2, _layout_h - pad * 2)

        if TABS[self.tab_index] == 'STATUS':
            self._draw_status_page(screen, player, play_time, content_rect)
        elif self.tab_index == 1:
            self._draw_inventory_page(screen, player, content_rect)
        elif self.tab_index == 2:
            self._draw_equip_page(screen, player, content_rect, self.equip_slot_index)
        elif self.tab_index == 3:
            self._draw_options_page(screen, content_rect, self.options_item_index,
                                    self.options_editing, self.options_values)
        elif self.tab_index == 4:
            self._draw_journal_page(screen, content_rect)

        # Character name tag sprites (bottom of frame) — skipped entirely
        # in restricted_mode/no-player use (TitleScreen's Options screen),
        # since there's no active character to tag pre-game.
        if player is not None and self.char_name_sprites:
            current = getattr(player, 'character', 'goku')
            ns      = max(1, round(_S * 1.0))
            nx = box_x + pad
            ny = box_y + _layout_h + max(4, int(self.canvas_height * 0.01))
            for cid, surfs in self.char_name_sprites.items():
                img = surfs['selected'] if cid == current else surfs['unselected']
                if img:
                    screen.blit(pygame.transform.scale(img, (img.get_width() * ns, img.get_height() * ns)), (nx, ny))

        # Inventory sub-tabs
        if self.tab_index == 1 and self._inv_tabs:
            self._draw_sub_tabs(screen, self._inv_tabs, self.inventory_tab_index,
                                box_x + pad + 24, box_y + pad - 7, 'inv_tab_')

        # Journal sub-tabs
        if self.tab_index == 4 and self._journal_tabs:
            self._draw_sub_tabs(screen, self._journal_tabs, self.journal_tab_index,
                                box_x + pad + 24, box_y + pad - 7, 'journal_tab_')

        # Scroll arrows (inventory + journal). On the Inventory tab, only
        # draw these when there's actually something to select — an empty
        # category (e.g. "No Supplies") has nothing for the arrows/bar to
        # control, so they're skipped entirely.
        #
        # For Inventory, "can go up/down" means the selection cursor can
        # move to another item — not just whether the scroll window itself
        # shifts. With a short list that fits entirely on screen, the
        # cursor still moves between items even though inv_scroll_offset
        # never changes, so the arrows must be driven by inv_selected_index
        # against the entry count, not by the scroll offset/max.
        inv_entries    = self._get_inventory_entries(player) if self.tab_index == 1 else None
        inv_has_items  = self.tab_index != 1 or bool(inv_entries)
        if self.tab_index in (1, 4) and inv_has_items:
            if self.tab_index == 1:
                can_up   = self.inv_selected_index > 0
                can_down = self.inv_selected_index < len(inv_entries) - 1
            else:
                scroll_off = self._journal_scroll
                can_up     = scroll_off > 0
                can_down   = scroll_off < self.inv_scroll_max
            arrow_scale = max(1, int(self.canvas_height * 0.06))
            scroll_x    = box_x + inner_w - pad - arrow_scale + 3
            scroll_top  = box_y + pad + 33

            up_surf = self.arrow_up_pressed if self.scroll_up_timer > 0 else (self.arrow_up if can_up else self.arrow_up_grey)
            if up_surf:
                sf = arrow_scale / up_surf.get_height()
                up_scaled = pygame.transform.scale(up_surf, (int(up_surf.get_width() * sf), arrow_scale))
                screen.blit(up_scaled, (scroll_x, scroll_top))
                arrow_h = up_scaled.get_height()
                self._click_zones['scroll_up'] = pygame.Rect(scroll_x-4, scroll_top-4, up_scaled.get_width()+8, arrow_h+8)
            else:
                arrow_h = arrow_scale

            if self.spacing_bar:
                bar_top    = scroll_top + arrow_h + 4
                bar_bottom = scroll_top + arrow_h - 213 + int(self.canvas_height * 0.55)
                bar_h_     = max(1, bar_bottom - bar_top)
                sb = self.spacing_bar
                so_h = sb.get_height(); so_w = sb.get_width()
                tip_scale = max(1, round(arrow_scale / so_h) * 4)
                tip_px = so_h // 4; tip_h = tip_px * tip_scale
                bar_w  = so_w * tip_scale
                top_s = pygame.transform.scale(sb.subsurface((0,0,so_w,tip_px)),(bar_w,tip_h))
                mid_s = pygame.transform.scale(sb.subsurface((0,tip_px,so_w,so_h-tip_px*2)),(bar_w,max(1,bar_h_-tip_h*2)))
                bot_s = pygame.transform.scale(sb.subsurface((0,so_h-tip_px,so_w,tip_px)),(bar_w,tip_h))
                blit_x = scroll_x - 3 + (arrow_scale - bar_w) // 2
                screen.blit(top_s,(blit_x,bar_top)); screen.blit(mid_s,(blit_x,bar_top+tip_h)); screen.blit(bot_s,(blit_x,bar_bottom-tip_h))

            dn_surf = self.arrow_down_pressed if self.scroll_down_timer > 0 else (self.arrow_down if can_down else self.arrow_down_grey)
            if dn_surf:
                sf = arrow_scale / dn_surf.get_height()
                dn_scaled = pygame.transform.scale(dn_surf,(int(dn_surf.get_width()*sf),arrow_scale))
                screen.blit(dn_scaled,(scroll_x, bar_bottom+8))
                self._click_zones['scroll_down'] = pygame.Rect(scroll_x-4,bar_bottom+4,dn_scaled.get_width()+8,arrow_scale+8)

        # L / R tab buttons above the frame
        btn_scale = max(2, int(self.canvas_height * 0.06))
        lr_y      = box_y - btn_scale - max(4, int(self.canvas_height * 0.01))

        def _menu_surfs(text, color=(255,255,255)):
            surfs = []
            for ch in text:
                s = (self.menu_uppercase_font if ch.isupper() else self.menu_lowercase_font).render(ch)
                s = s.copy(); s.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
                surfs.append(s)
            return surfs

        def _blit_menu(surfs, x, y, spacing=0, offsets=None):
            max_h = max(s.get_height() for s in surfs)
            cx = x
            for i, s in enumerate(surfs):
                shadow = s.copy(); shadow.fill((0,0,0), special_flags=pygame.BLEND_RGBA_MULT)
                oy = max_h - s.get_height(); extra = offsets.get(i,0) if offsets else 0
                screen.blit(shadow,(cx+1,y+oy+extra+1)); screen.blit(s,(cx,y+oy+extra))
                cx += s.get_width() + spacing

        def _menu_w(surfs, spacing=0):
            return sum(s.get_width() for s in surfs) + spacing * (len(surfs)-1)

        labels    = [('Journal','Status','Inventory'),('Status','Inventory','Equip'),
                     ('Inventory','Equip','Options'),('Equip','Options','Journal'),
                     ('Options','Journal','Status')][self.tab_index]
        left_label, centre_label, right_label = labels

        # L/R shoulder-button sprites and their flanking "prev/next tab"
        # text are skipped in restricted_mode — TitleScreen's Options
        # screen only ever shows this one tab, so there's nothing to page
        # between and no L/R affordance to hint at. The centre title
        # ("Options") below is unaffected and always drawn.
        if not self.restricted_mode:
            l_raw = self.button_l_pressed if self.button_states['l'] else self.button_l
            l_scaled_w = 0
            if l_raw:
                sf = btn_scale / l_raw.get_height(); l_scaled_w = int(l_raw.get_width()*sf)
            self._draw_button_sprite(screen, self.button_l, self.button_l_pressed,
                                     self.button_states['l'], box_x, lr_y, '')
            self._click_zones['l_tab'] = pygame.Rect(box_x, lr_y-4, l_scaled_w+240, btn_scale+8)

            r_raw = self.button_r_pressed if self.button_states['r'] else self.button_r
            r_w   = int(r_raw.get_width()*(btn_scale/r_raw.get_height())) if r_raw else btn_scale
            r_btn_x = box_x + inner_w - r_w - 16
            self._draw_button_sprite(screen, self.button_r, self.button_r_pressed,
                                     self.button_states['r'], r_btn_x, lr_y, '')

            _side_sp  = 6
            _desc_map = {'Inventory':{8:8},'Equip':{1:8,4:8},'Options':{1:8}}

            left_surfs = _menu_surfs(left_label)
            left_y     = lr_y + (btn_scale - max(s.get_height() for s in left_surfs))//2 + 2
            _blit_menu(left_surfs, box_x+l_scaled_w+10, left_y, spacing=_side_sp, offsets=_desc_map.get(left_label))

            right_surfs = _menu_surfs(right_label)
            right_w     = _menu_w(right_surfs, _side_sp)
            right_x     = r_btn_x - right_w - 10
            right_y     = lr_y + (btn_scale - max(s.get_height() for s in right_surfs))//2 + 2
            _blit_menu(right_surfs, right_x, right_y, spacing=_side_sp, offsets=_desc_map.get(right_label))
            self._click_zones['r_tab'] = pygame.Rect(right_x-4,lr_y-4,(r_btn_x+r_w)-right_x+8,btn_scale+8)

        _c_off_map = {'Inventory':{8:12},'Equip':{1:8,4:8},'Options':{1:8}}
        _c_y_map   = {'Status':-16,'Inventory':-20,'Equip':-16,'Options':-16,'Journal':-16}
        char_surfs = []
        for ch in centre_label:
            s = (self.bold_font if ch.isupper() else self.bold_lowercase_font).render(ch)
            s = s.copy(); s.fill((255,255,0), special_flags=pygame.BLEND_RGBA_MULT)
            char_surfs.append(s)
        total_w = sum(s.get_width() for s in char_surfs)
        max_h   = max(s.get_height() for s in char_surfs)
        label_x = box_x + (inner_w - total_w)//2
        label_y = lr_y + _c_y_map.get(centre_label,-16)
        c_offs  = _c_off_map.get(centre_label,{})
        for i, s in enumerate(char_surfs):
            shadow = s.copy(); shadow.fill((0,0,0), special_flags=pygame.BLEND_RGBA_MULT)
            oy = max_h - s.get_height(); extra = c_offs.get(i,0)
            screen.blit(shadow,(label_x+1,label_y+oy+extra+1)); screen.blit(s,(label_x,label_y+oy+extra))
            label_x += s.get_width() + 5

        # Bottom buttons
        button_y = box_y + _layout_h - int(self.canvas_height*0.104) - 65
        _b_scale = max(2, int(self.canvas_height*0.06))

        if self.tab_index == 1 and self.hpepbar_black:
            bar_sc = max(1, round(self.canvas_height*0.05/self.hpepbar_black.get_height()))
            bw_ = self.hpepbar_black.get_width()*bar_sc; bh_ = self.hpepbar_black.get_height()*bar_sc
            bar_x = box_x+inner_w-pad-bw_-3; bar_y = button_y-bh_+44
            screen.blit(pygame.transform.scale(self.hpepbar_black,(bw_,bh_)),(bar_x,bar_y))
            hp=getattr(player,'hp',0); max_hp=getattr(player,'max_hp',1)
            ki=int(getattr(player,'ki',0)); max_ki=int(getattr(player,'max_ki',1))
            def _blit_clipped(surf,x,y,ratio):
                ratio=max(0.0,min(ratio,1.0)); full_w=surf.get_width(); row_h=surf.get_height()//3
                for i,off in enumerate([0,-1,-2]):
                    src_w=max(1,min(int(full_w*ratio)+off,full_w))
                    sub=surf.subsurface((0,i*row_h,src_w,row_h))
                    screen.blit(pygame.transform.scale(sub,(src_w*bar_sc,row_h*bar_sc)),(x,y+i*row_h*bar_sc))
            if self.hpbar: _blit_clipped(self.hpbar,bar_x+28,bar_y+4, hp/max(1,max_hp))
            if self.epbar: _blit_clipped(self.epbar,bar_x+12,bar_y+20, ki/max(1,max_ki))

        # On the Status tab, if the character being *viewed* is the live/
        # active one and has unspent stat points, the A/Cancel slots swap
        # roles just like they do on Inventory/Equip/Options: A moves into
        # the left slot (now reading "Use Points" instead of "Select") and
        # B/Cancel shifts to the right slot. Mirrors the same char-id check
        # _draw_status_page uses for is_active, since that page owns
        # self._viewed_char_id.
        stat_points, show_use_points = self._status_points_context()
        # Stay on the A-button layout while actively allocating even if the
        # player has just spent their last point — otherwise the "Accept"
        # button would vanish out from under them mid-allocation.
        show_status_a_button = show_use_points or (TABS[self.tab_index] == 'STATUS' and self.allocating_stats)

        # Equip tab, browsing a slot's items, with nothing owned for it:
        # A/Select has nothing to select, so it's greyed the same way
        # options_editing greys it below.
        equip_list_is_empty = (self.tab_index == 2 and self.equip_browsing_items and
                                player is not None and not self._get_equip_entries(player, self.equip_slot_index))

        if self.tab_index in (1,2,3):
            sel_x = box_x + int(inner_w-961)
            self._draw_button_sprite(screen,self.button_a,self.button_a_pressed,self.button_states['a'],sel_x,button_y,'')
            if (self.options_editing or equip_list_is_empty) and self.button_a:
                _a=self.button_a_pressed if self.button_states['a'] else self.button_a
                sf=_b_scale/_a.get_height(); aw=int(_a.get_width()*sf)
                scaled=pygame.transform.scale(_a,(aw,_b_scale))
                gs=pygame.Surface((aw,_b_scale),pygame.SRCALPHA); gs.fill((150,150,150,255))
                gs.blit(scaled,(0,0),special_flags=pygame.BLEND_RGBA_MIN); screen.blit(gs,(sel_x,button_y))
            _a=self.button_a_pressed if self.button_states['a'] else self.button_a
            _a_w=int(_a.get_width()*(_b_scale/_a.get_height())) if _a else _b_scale
            sel_surfs=_menu_surfs('Select',color=(255,255,0))
            sel_lx=sel_x+_a_w+int(5*RENDER_SCALE-8); sel_ly=button_y+(_b_scale-max(s.get_height() for s in sel_surfs))//2+2
            _blit_menu(sel_surfs,sel_lx,sel_ly,spacing=6)
            self._click_zones['a_select']=pygame.Rect(sel_x,button_y-4,_a_w+_menu_w(sel_surfs,6)+int(5*RENDER_SCALE-8)+8,_b_scale+8)
            cancel_x = box_x+int(inner_w-760)
        elif show_status_a_button:
            # "Use Points" opens stat allocation; once inside it the same
            # button reads "Accept" and confirms/exits allocation instead.
            sel_x = box_x + int(inner_w-961)
            self._draw_button_sprite(screen,self.button_a,self.button_a_pressed,self.button_states['a'],sel_x,button_y,'')
            _a=self.button_a_pressed if self.button_states['a'] else self.button_a
            _a_w=int(_a.get_width()*(_b_scale/_a.get_height())) if _a else _b_scale
            a_label = 'Accept' if self.allocating_stats else 'Use Points'
            use_surfs=_menu_surfs(a_label,color=(255,255,0))
            use_lx=sel_x+_a_w+int(5*RENDER_SCALE-8); use_ly=button_y+(_b_scale-max(s.get_height() for s in use_surfs))//2+2
            _blit_menu(use_surfs,use_lx,use_ly,spacing=6)
            self._click_zones['a_use_points']=pygame.Rect(sel_x,button_y-4,_a_w+_menu_w(use_surfs,6)+int(5*RENDER_SCALE-8)+8,_b_scale+8)
            # "Use Points"/"Accept" is wider than "Select", so — unlike the
            # fixed inner_w-760 offset the tab_index-in-(1,2,3) branch above
            # uses — cancel_x here is derived from the actual rendered label
            # width plus a gap, so B/Cancel never overlaps it regardless of
            # font metrics changing later.
            cancel_x = use_lx+_menu_w(use_surfs,6)+int(5*RENDER_SCALE)
        else:
            cancel_x = box_x+int(inner_w-961)

        self._draw_button_sprite(screen,self.button_b,self.button_b_pressed,self.button_states['b'],cancel_x,button_y,'')
        _b=self.button_b_pressed if self.button_states['b'] else self.button_b
        _b_w=int(_b.get_width()*(_b_scale/_b.get_height())) if _b else _b_scale
        can_surfs=_menu_surfs('Cancel',color=(255,255,0))
        can_lx=cancel_x+_b_w+int(5*RENDER_SCALE-8); can_ly=button_y+(_b_scale-max(s.get_height() for s in can_surfs))//2+2
        _blit_menu(can_surfs,can_lx,can_ly,spacing=6)
        self._click_zones['b_cancel']=pygame.Rect(cancel_x,button_y-4,_b_w+_menu_w(can_surfs,6)+int(5*RENDER_SCALE-8)+8,_b_scale+8)

        # Item-use confirmation popup — same bordered-box + tiled-background
        # style as the main panel, just smaller, centered on top of it.
        # Contents (Use/Cancel prompt, item name, etc.) come in a later
        # step; this just gets the window itself opening/closing correctly.
        if self.item_confirm_open:
            self._draw_item_confirm_popup(screen, pygame.Rect(box_x, box_y, inner_w, inner_h), button_y)

        # Equip-confirm popup — same bordered-box + tiled-background style,
        # opened by _select_equip_item() on the Equip tab's item list.
        if self.equip_confirm_open:
            self._draw_equip_confirm_popup(screen, pygame.Rect(box_x, box_y, inner_w, inner_h), button_y)

    def _draw_item_confirm_popup(self, screen, parent_rect, button_y=None):
        screen.set_clip(None)

        # Sized to match the reference UI: measured directly from a
        # screenshot of the real popup as ~86% of the main panel's width
        # and ~74% of its height, centered. No dimming overlay behind it —
        # the original doesn't darken the panel, it just draws the popup
        # on top of it at full brightness.
        popup_w = int(parent_rect.width  * 0.872)
        popup_h = int(parent_rect.height * 0.756)
        popup_x = parent_rect.x + (parent_rect.width  - popup_w) // 2 + 1
        popup_y = parent_rect.y + (parent_rect.height - popup_h) // 2

        # Clamp so the popup's bottom edge can never reach the Select/Cancel
        # button row, no matter what offset gets added to popup_y above.
        # Leaves a fixed 10px gap above the buttons instead of relying on
        # the centering math to happen to leave enough room.

        popup_rect = pygame.Rect(popup_x, popup_y, popup_w, popup_h)

        # Hard clip everything below to popup_rect. This is the belt-and-
        # suspenders version of the button_y clamp above: no matter what
        # popup_x/popup_y/popup_w/popup_h end up being, pygame will refuse
        # to paint a single pixel outside this rect, so the background can
        # never bleed past the border onto the buttons or anything else.
        screen.set_clip(popup_rect)

        # Same order as the main panel: background painted first, border
        # drawn on top of it (the border's middle is transparent, so the
        # background painted underneath shows through). The main panel gets
        # away with painting its background once for the whole screen
        # before the border — here we paint it just for the popup's own
        # rect first, then draw the border on top.
        #
        # The background rect is deliberately smaller than popup_rect
        # (inset by bg_inset on every side) instead of reusing popup_rect
        # directly. If the background fills the exact same rect as the
        # border, it extends all the way out to the border's own outer
        # edge — the only thing hiding that is the frame art itself being
        # opaque there, so any thin/transparent spot in the frame lets the
        # background peek through right at the boundary, which is what
        # bleeds into the inventory rows around the popup. Insetting it
        # keeps the background strictly inside the frame with margin to
        # spare, while the border below is still drawn at full popup_rect
        # size so its own thickness/position doesn't change.
        bg_inset = max(4, int(min(popup_w, popup_h) * 0.035))
        bg_rect  = popup_rect.inflate(-2 * bg_inset, -2 * bg_inset)
        self._draw_tiled_background(screen, bg_rect)

        drawn = self.box_sprite and self._draw_9slice_sprite(
            screen, self.box_sprite, popup_x, popup_y, popup_w, popup_h, corner_size=20
        )
        if not drawn:
            pygame.draw.rect(screen, self.border_outer, (popup_x-6, popup_y-6, popup_w+12, popup_h+12))
            pygame.draw.rect(screen, self.border_inner, (popup_x-3, popup_y-3, popup_w+6,  popup_h+6))
            pygame.draw.rect(screen, self.border_green, (popup_x-1, popup_y-1, popup_w+2,  popup_h+2))

        # Selected item icon + name (popup box geometry left untouched)
        item_id = self._pending_item_id
        if item_id:
            catalog = get_items_by_category(self._current_inventory_category())
            data = catalog.get(item_id) or {}
            if not data:
                # Fallback: search the other category in case the list moved
                other = CATEGORY_STORY_ITEMS if self._current_inventory_category() == CATEGORY_SUPPLIES else CATEGORY_SUPPLIES
                data = get_items_by_category(other).get(item_id) or {}
            item_name = data.get('name', item_id.replace('_', ' ').title())

            pad = max(12, int(min(popup_w, popup_h) * 0.06))
            content_x = popup_x + pad + 7
            content_y = popup_y + pad + 5

            icon = self._get_item_icon(item_id)
            icon_w = 0
            icon_h = 0
            if icon:
                scaled = pygame.transform.scale(icon, (
                    icon.get_width()  * self.font_scale,
                    icon.get_height() * self.font_scale,
                ))
                icon_w = scaled.get_width()
                icon_h = scaled.get_height()
                # Bottom-align every icon on the same baseline that Miso
                # Soup's icon sits on, rather than sharing a top y. Icons
                # are different pixel heights (e.g. dinosaur meat is
                # shorter than miso soup), so top-aligning them at
                # content_y left the shorter ones sitting visually a pixel
                # or more above where they should be. Compute Miso Soup's
                # bottom edge and push each icon's top up/down so its own
                # bottom lands there instead.
                miso_icon_for_baseline = self._get_item_icon('miso_soup')
                miso_h = (miso_icon_for_baseline.get_height() * self.font_scale) if miso_icon_for_baseline else icon_h
                baseline_y = content_y + miso_h
                icon_y = baseline_y - icon_h
                screen.blit(scaled, (content_x, icon_y))

            # Vertical position is pinned to wherever it would land for the
            # Miso Soup icon specifically, so the name never shifts up/down
            # as the player pages through items with differently-tall
            # icons. Horizontally, though, the name should sit the same
            # *distance* from the icon's right edge as it does for Miso
            # Soup — so as the actual icon's width changes, the text moves
            # with it (using the real icon_w here) rather than staying put.
            miso_icon = self._get_item_icon('miso_soup')
            name_icon_h = (miso_icon.get_height() * self.font_scale) if miso_icon else icon_h

            name_x = content_x + icon_w + (max(8, int(12 * _S)) if icon_w else 0) - 40
            name_y = content_y + max(0, (name_icon_h - self.menu_uppercase_font.get_line_height()) // 2) + 6
            # Item-confirm popup name: yellow text with its own word-gap,
            # independent of every other _blit_journal_text call (journal
            # entries, inventory rows, etc. keep the default white/8*_S).
            self._blit_journal_text(
                screen, item_name, name_x, name_y,
                color=(255, 255, 0),
                max_w=popup_w - (name_x - popup_x) - pad,
                word_gap=int(2 * _S),  # ← change this to widen/narrow the gap between words
            )

            # Description + effect text, stacked below the icon/name row.
            # Both lines are grey to read as secondary/flavor info next to
            # the yellow item name above them.
            line_h     = self.menu_uppercase_font.get_line_height()
            grey       = (150, 150, 150)
            text_x     = content_x + 10
            # desc_max_w_trim narrows just the description/effect text box
            # (independent of the icon/name layout above) so word-wrap
            # points can be tuned to match the reference UI — e.g. forcing
            # "dinosaur" onto line 2 of the Dinosaur Meat description
            # instead of squeezing onto line 1. Raise this to wrap earlier
            # (push more words to the next line), lower it to wrap later.
            desc_max_w_trim = int(15 * _S)  # ← increase/decrease this to shift where description text wraps
            text_max_w = popup_w - (content_x - popup_x) - pad - desc_max_w_trim
            # Pinned to Miso Soup's icon height (name_icon_h), not the
            # actual selected item's icon_h — otherwise shorter icons like
            # Dinosaur Meat leave less vertical space above and the
            # description's top pixel lands a row lower than it should.
            text_y     = content_y + max(name_icon_h, line_h) + max(8, int(10 * _S)) - 24

            description = data.get('description', '')
            effect_text = data.get('effect_text', '')

            if description:
                _, last_line_y = self._blit_journal_text(
                    screen, description, text_x, text_y, grey, max_w=text_max_w,
                    word_gap=int(4 * _S),  # ← change this to widen/narrow the gap between words in the description
                )
                text_y = last_line_y + line_h + max(4, int(6 * _S))

            if effect_text:
                self._blit_journal_text(
                    screen, effect_text, text_x, text_y, grey, max_w=text_max_w,
                    word_gap=int(4 * _S),  # ← change this to widen/narrow the gap between words in the effect text
                )

        # A / B buttons — "Use" and "Cancel". Positioned at the same
        # relative spot within this popup box as the main panel's own A/B
        # row sits within the big box it's drawn on top of: the main panel
        # places them at box_x+inner_w-961 ("Select"/A) and box_x+inner_w-760
        # ("Cancel"/B), at absolute y = button_y. Converting those to
        # fractions of the big box's own width/height lets the same relative
        # position be re-applied to the popup's much smaller width/height,
        # instead of reusing the same pixel offsets (which were tuned for
        # the big box and would sit outside this smaller one).
        if button_y is not None and parent_rect.width and parent_rect.height:
            main_w, main_h, main_y = parent_rect.width, parent_rect.height, parent_rect.y

            rel_a_x   = (main_w - 961) / main_w
            rel_b_x   = (main_w - 800) / main_w
            rel_btn_y = (button_y - main_y) / main_h

            btn_x_a = popup_x + int(rel_a_x * popup_w)
            btn_x_b = popup_x + int(rel_b_x * popup_w)
            btn_y   = popup_y + int(rel_btn_y * popup_h) - int(5 * _S)  # ← increase this to move the A/B row up, decrease (or negative) to move it down

            _b_scale = max(2, int(self.canvas_height * 0.06))

            def _popup_menu_surfs(text, color=(255, 255, 0)):
                surfs = []
                for ch in text:
                    s = (self.menu_uppercase_font if ch.isupper() else self.menu_lowercase_font).render(ch)
                    s = s.copy(); s.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
                    surfs.append(s)
                return surfs

            def _popup_blit_menu(surfs, bx, by, spacing=6):
                max_h = max(s.get_height() for s in surfs)
                cx = bx
                for s in surfs:
                    shadow = s.copy(); shadow.fill((0, 0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                    oy = max_h - s.get_height()
                    screen.blit(shadow, (cx + 1, by + oy + 1)); screen.blit(s, (cx, by + oy))
                    cx += s.get_width() + spacing

            def _popup_menu_w(surfs, spacing=6):
                return sum(s.get_width() for s in surfs) + spacing * (len(surfs) - 1)

            # A — "Use"
            self._draw_button_sprite(screen, self.button_a, self.button_a_pressed,
                                      self.popup_button_states['a'], btn_x_a, btn_y, '')
            _a   = self.button_a_pressed if self.popup_button_states['a'] else self.button_a
            _a_w = int(_a.get_width() * (_b_scale / _a.get_height())) if _a else _b_scale
            use_surfs = _popup_menu_surfs('Use')
            use_lx = btn_x_a + _a_w + int(5 * RENDER_SCALE - 8)
            use_ly = btn_y + (_b_scale - max(s.get_height() for s in use_surfs)) // 2 + 2
            _popup_blit_menu(use_surfs, use_lx, use_ly)
            self._click_zones['item_confirm_use'] = pygame.Rect(
                btn_x_a, btn_y - 4,
                _a_w + _popup_menu_w(use_surfs) + int(5 * RENDER_SCALE - 8) + 8, _b_scale + 8,
            )

            # B — "Cancel"
            self._draw_button_sprite(screen, self.button_b, self.button_b_pressed,
                                      self.popup_button_states['b'], btn_x_b, btn_y, '')
            _b   = self.button_b_pressed if self.popup_button_states['b'] else self.button_b
            _b_w = int(_b.get_width() * (_b_scale / _b.get_height())) if _b else _b_scale
            cancel_surfs = _popup_menu_surfs('Cancel')
            cancel_lx = btn_x_b + _b_w + int(5 * RENDER_SCALE - 8)
            cancel_ly = btn_y + (_b_scale - max(s.get_height() for s in cancel_surfs)) // 2 + 2
            _popup_blit_menu(cancel_surfs, cancel_lx, cancel_ly)
            self._click_zones['item_confirm_cancel'] = pygame.Rect(
                btn_x_b, btn_y - 4,
                _b_w + _popup_menu_w(cancel_surfs) + int(5 * RENDER_SCALE - 8) + 8, _b_scale + 8,
            )

        screen.set_clip(None)

    def _draw_equip_confirm_popup(self, screen, parent_rect, button_y=None):
        """Popup opened by _select_equip_item(): the tapped equip item's
        icon (top-left) + name (yellow), same box/background as
        _draw_item_confirm_popup, but with two selectable option rows —
        'Equip to <Character>' / 'Drop' — and a blinking cursor arrow next
        to whichever one is highlighted, instead of Use/Cancel A/B
        buttons. UP/DOWN move equip_confirm_option between the two rows;
        handle_input() reads it back out in _confirm_equip_action()."""
        screen.set_clip(None)

        popup_w = int(parent_rect.width  * 0.790)
        popup_h = int(parent_rect.height * 0.695)
        popup_x = parent_rect.x + (parent_rect.width  - popup_w) // 2 + 1
        popup_y = parent_rect.y + int(parent_rect.height * 0.06) + 33

        popup_rect = pygame.Rect(popup_x, popup_y, popup_w, popup_h)
        screen.set_clip(popup_rect)

        # Background inset from the border for the same reason as
        # _draw_item_confirm_popup: keeps it strictly inside the frame so
        # it never bleeds past a thin/transparent spot in the border art.
        bg_inset = max(4, int(min(popup_w, popup_h) * 0.035))
        bg_rect  = popup_rect.inflate(-2 * bg_inset, -2 * bg_inset)
        self._draw_tiled_background(screen, bg_rect)

        drawn = self.box_sprite and self._draw_9slice_sprite(
            screen, self.box_sprite, popup_x, popup_y, popup_w, popup_h, corner_size=20
        )
        if not drawn:
            pygame.draw.rect(screen, self.border_outer, (popup_x-6, popup_y-6, popup_w+12, popup_h+12))
            pygame.draw.rect(screen, self.border_inner, (popup_x-3, popup_y-3, popup_w+6,  popup_h+6))
            pygame.draw.rect(screen, self.border_green, (popup_x-1, popup_y-1, popup_w+2,  popup_h+2))

        item_id   = self._pending_equip_item_id
        pad       = max(12, int(min(popup_w, popup_h) * 0.06))
        content_x = popup_x + pad + 10
        content_y = popup_y + pad + 7
        icon_h    = 0

        if item_id:
            data      = get_item(item_id) or {}
            item_name = data.get('name', item_id.replace('_', ' ').title())

            icon   = self._get_item_icon(item_id)
            icon_w = 0
            if icon:
                scaled = pygame.transform.scale(icon, (
                    icon.get_width()  * self.font_scale,
                    icon.get_height() * self.font_scale,
                ))
                icon_w = scaled.get_width()
                icon_h = scaled.get_height()
                screen.blit(scaled, (content_x, content_y))

            name_x = content_x + icon_w + (max(8, int(13 * _S)) if icon_w else 0) - 40
            name_y = content_y + max(0, (icon_h - self.menu_uppercase_font.get_line_height()) // 2) + 2
            self._blit_journal_text(
                screen, item_name, name_x, name_y,
                color=(255, 255, 0),
                max_w=popup_w - (name_x - popup_x) - pad,
                word_gap=int(2 * _S),
            )

        # Two selectable options, stacked below the icon/name row. Reuses
        # the same cursor sprite + blink timer as every other selection
        # cursor in this menu (stat-allocation, inventory rows, etc.).
        char_name = self._get_display_name(getattr(self._player, 'character', 'goku')) if self._player else ''
        option_labels = [f'Equip to {char_name}', 'Drop']

        line_h  = self.menu_uppercase_font.get_line_height()
        opt_gap = max(10, int(4 * _S))
        text_x  = content_x + 9
        opt_y   = content_y + max(icon_h, line_h) + max(16, int(3 * _S))

        for i, label in enumerate(option_labels):
            is_selected = (self.equip_confirm_option == i)
            col = (0, 255, 0) if is_selected else (255,255,255)

            if is_selected and self.equip_arrow:
                blink_on = (self._levelup_blink_timer % (self._levelup_blink_interval * 2)) < self._levelup_blink_interval
                if blink_on:
                    arrow      = self.equip_arrow
                    arr_sc     = max(1, int(self.canvas_height * 0.05 / arrow.get_height()))
                    arr_scaled = pygame.transform.scale(arrow, (arrow.get_width() * arr_sc, arrow.get_height() * arr_sc))
                    arrow_gap  = -1  # distance between the arrow and the option text's left edge
                    arrow_x    = text_x - arrow_gap - arr_scaled.get_width() - 1
                    # The arrow deliberately sits left of text_x and can
                    # extend past popup_x/the border's left edge — it's
                    # meant to hang outside the box, not be squeezed inside
                    # it. screen.set_clip(popup_rect) above would chop off
                    # that overhang, so clip is lifted just for this blit
                    # and restored to popup_rect right after.
                    screen.set_clip(None)
                    screen.blit(arr_scaled, (arrow_x, opt_y + (line_h - arr_scaled.get_height()) // 2 + 4))
                    screen.set_clip(popup_rect)

            self._blit_journal_text(
                screen, label, text_x, opt_y, col,
                max_w=popup_w - (text_x - popup_x) - pad,
                word_gap=int(2 * _S),
            )

            self._click_zones[f'equip_confirm_option_{i}'] = pygame.Rect(
                popup_x + pad - 10, opt_y - 4, popup_w - 2 * (pad - 10), line_h + 8
            )
            opt_y += line_h + opt_gap

        # A / B buttons — "Select" and "Cancel". Positioned at the same
        # relative spot within this popup box as _draw_item_confirm_popup
        # positions its own Use/Cancel row (which itself mirrors the main
        # panel's own A/B row at box_x+inner_w-961 / box_x+inner_w-800,
        # y = button_y) — converted to fractions of the big box and
        # re-applied to this popup's own (smaller, differently-proportioned)
        # width/height, same technique _draw_item_confirm_popup uses.
        if button_y is not None and parent_rect.width and parent_rect.height:
            main_w, main_h, main_y = parent_rect.width, parent_rect.height, parent_rect.y

            rel_a_x   = (main_w - 961) / main_w
            rel_btn_y = (button_y - main_y) / main_h

            # B's x is no longer taken from a fixed relative fraction of
            # this popup's own (smaller/differently-proportioned) width —
            # that fraction was tuned against the item-confirm popup's
            # proportions and left the wrong gap to "Select" here. Instead
            # it's derived below from where the rendered "Select" label
            # actually ends, the same convention the main panel itself
            # uses to place Cancel after a variable-width A label (see
            # the "Use Points"/"Accept" cancel_x derivation above).
            btn_x_a = popup_x + int(rel_a_x * popup_w) + 2  # +1: nudge A one visual pixel right
            btn_y_  = popup_y + int(rel_btn_y * popup_h) - int(6 * _S)

            _b_scale = max(2, int(self.canvas_height * 0.06))

            def _popup_menu_surfs(text, color=(255, 255, 0)):
                surfs = []
                for ch in text:
                    s = (self.menu_uppercase_font if ch.isupper() else self.menu_lowercase_font).render(ch)
                    s = s.copy(); s.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
                    surfs.append(s)
                return surfs

            def _popup_blit_menu(surfs, bx, by, spacing=6):
                max_h = max(s.get_height() for s in surfs)
                cx = bx
                for s in surfs:
                    shadow = s.copy(); shadow.fill((0, 0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                    oy = max_h - s.get_height()
                    screen.blit(shadow, (cx + 1, by + oy + 1)); screen.blit(s, (cx, by + oy))
                    cx += s.get_width() + spacing

            def _popup_menu_w(surfs, spacing=6):
                return sum(s.get_width() for s in surfs) + spacing * (len(surfs) - 1)

            screen.set_clip(None)

            # A — "Select"
            self._draw_button_sprite(screen, self.button_a, self.button_a_pressed,
                                      self.popup_button_states['a'], btn_x_a, btn_y_, '')
            _a   = self.button_a_pressed if self.popup_button_states['a'] else self.button_a
            _a_w = int(_a.get_width() * (_b_scale / _a.get_height())) if _a else _b_scale
            sel_surfs = _popup_menu_surfs('Select')
            sel_lx = btn_x_a + _a_w + int(5 * RENDER_SCALE - 8)
            sel_ly = btn_y_ + (_b_scale - max(s.get_height() for s in sel_surfs)) // 2 + 2
            _popup_blit_menu(sel_surfs, sel_lx, sel_ly)
            self._click_zones['equip_confirm_select'] = pygame.Rect(
                btn_x_a, btn_y_ - 4,
                _a_w + _popup_menu_w(sel_surfs) + int(5 * RENDER_SCALE - 8) + 8, _b_scale + 8,
            )

            # B — "Cancel". x derived from the end of the rendered "Select"
            # label (sel_lx + its width) plus the same gap the main panel
            # uses between a variable-width A label and Cancel, so the
            # visual distance from Select to B stays correct regardless of
            # this popup's width.
            btn_x_b = sel_lx + _popup_menu_w(sel_surfs) + int(5 * RENDER_SCALE)
            self._draw_button_sprite(screen, self.button_b, self.button_b_pressed,
                                      self.popup_button_states['b'], btn_x_b, btn_y_, '')
            _b   = self.button_b_pressed if self.popup_button_states['b'] else self.button_b
            _b_w = int(_b.get_width() * (_b_scale / _b.get_height())) if _b else _b_scale
            cancel_surfs = _popup_menu_surfs('Cancel')
            cancel_lx = btn_x_b + _b_w + int(5 * RENDER_SCALE - 8)
            cancel_ly = btn_y_ + (_b_scale - max(s.get_height() for s in cancel_surfs)) // 2 + 2
            _popup_blit_menu(cancel_surfs, cancel_lx, cancel_ly)
            self._click_zones['equip_confirm_cancel'] = pygame.Rect(
                btn_x_b, btn_y_ - 4,
                _b_w + _popup_menu_w(cancel_surfs) + int(5 * RENDER_SCALE - 8) + 8, _b_scale + 8,
            )

        screen.set_clip(None)

    def _draw_sub_tabs(self, screen, tabs, active_idx, start_x, start_y, zone_prefix):
        h   = max(16, int(self.canvas_height * 0.05))
        gap = max(2, int(self.screen_width * 0.003)) + 10
        x   = start_x
        for i, tab in enumerate(tabs):
            surf = tab['selected'] if i == active_idx else tab['unselected']
            if surf:
                s      = max(1, round(h / surf.get_height()))
                scaled = pygame.transform.scale(surf, (surf.get_width()*s, surf.get_height()*s))
                screen.blit(scaled, (x, start_y))
                self._click_zones[f'{zone_prefix}{i}'] = pygame.Rect(x, start_y, scaled.get_width(), scaled.get_height()+4)
                x += scaled.get_width() + gap

    # ── Status page ───────────────────────────────────────────────────────────

    def _resolve_viewed_progress(self, player, char_id, is_active):
        """LVL/XP/HP/EP/STR/POW/END/SPD to show on the Status page for
        char_id. Each playable character now tracks its own independent
        progress (see Game._switch_character / Player.snapshot_progress /
        player.character_progress) instead of every character sharing one
        set of numbers, so this resolves to real values for whoever's being
        previewed — not just the currently active character.

        Precedence:
          1. is_active            → the live player object (source of truth
                                     for whoever's actually being played
                                     right now — character_progress[char_id]
                                     is only refreshed on switch/save, so it
                                     can lag behind mid-session).
          2. character_progress   → char_id has been played before this
                                     session; use their saved numbers.
          3. character-creator    → char_id is unlocked but has never been
             base config            switched to yet; seed the same fresh
                                     level-1 numbers they'd get the moment
                                     they're first switched to (see
                                     Player.fresh_progress_for_character).

        ZENIE and TIME are deliberately NOT included here — those stay
        shared/global across every character (one wallet, one save-file
        playtime), so callers should read player.zeni / the play_time
        argument directly regardless of which character is being viewed.
        """
        if is_active:
            stats = getattr(player, 'stats', {}) or {}
            return {
                'level':             getattr(player, 'level', 1),
                'hp':                int(round(getattr(player, 'hp', 0))),
                'max_hp':            int(round(getattr(player, 'max_hp', 1))),
                'ki':                int(getattr(player, 'ki', 0)),
                'max_ki':            int(getattr(player, 'max_ki', 1)),
                # Show lifetime XP collected (total_exp) rather than
                # player.exp, which resets down to the leftover amount on
                # every level-up.
                'total_exp':         getattr(player, 'total_exp', getattr(player, 'exp', 0)),
                'exp':               getattr(player, 'exp', 0),
                'exp_to_next_level': getattr(player, 'exp_to_next_level', 0),
                'stats': {
                    'strength': stats.get('strength', stats.get('str', 0)),
                    'ki_power': stats.get('ki_power', stats.get('pow', 0)),
                    'vitality': stats.get('vitality', stats.get('end', 0)),
                    'speed':    stats.get('speed',    stats.get('spd', 0)),
                },
            }

        saved = getattr(player, 'character_progress', {}).get(char_id)
        if saved is not None:
            stats = saved.get('stats', {}) or {}
            return {
                'level':             saved.get('level', 1),
                'hp':                int(round(saved.get('hp', 0))),
                'max_hp':            int(round(saved.get('max_hp', 1))),
                'ki':                int(saved.get('ki', 0)),
                'max_ki':            int(saved.get('max_ki', 1)),
                'total_exp':         saved.get('total_exp', saved.get('exp', 0)),
                'exp':               saved.get('exp', 0),
                'exp_to_next_level': saved.get('exp_to_next_level', 0),
                'stats': {
                    'strength': stats.get('strength', 0),
                    'ki_power': stats.get('ki_power', 0),
                    'vitality': stats.get('vitality', 0),
                    'speed':    stats.get('speed', 0),
                },
            }

        # Never been played this session/save — fresh level-1 numbers
        # seeded from the character creator's base config.
        try:
            cstats = load_config(char_id).get('stats', {})
        except Exception:
            cstats = {}
        max_hp = cstats.get('max_hp', 1)
        max_ki = cstats.get('max_ki', 1)
        return {
            'level':             1,
            'hp':                max_hp,
            'max_hp':            max_hp,
            'ki':                max_ki,
            'max_ki':            max_ki,
            'total_exp':         0,
            'exp':               0,
            'exp_to_next_level': 100,  # matches Player.__init__'s own no-game_config fallback
            'stats': {
                'strength': cstats.get('power', 0),
                'ki_power': cstats.get('ki_power', 0),
                'vitality': cstats.get('vitality', 0),
                'speed':    cstats.get('speed', 0),
            },
        }

    def _draw_status_page(self, screen, player, play_time, rect):
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
        sprite_col_w = w // 3
        stats_x = x + sprite_col_w - 45
        stats_w = w - sprite_col_w - 6
        active_char_id = getattr(player, 'character', 'unknown')
        char_id        = self._viewed_char_id or active_char_id
        is_active      = char_id == active_char_id
        progress       = self._resolve_viewed_progress(player, char_id, is_active)
        level          = progress['level']

        def _tint(s, c): t=s.copy(); t.fill(c,special_flags=pygame.BLEND_RGBA_MULT); return t
        def _shadow(s):  t=s.copy(); t.fill((0,0,0),special_flags=pygame.BLEND_RGBA_MULT); return t

        name_surf = _tint(self.bold_font.render(self._get_display_name(char_id).upper()), (255,255,0))
        lvl_surf  = _tint(self.bold_font.render('LVL'),           (255,0,0))
        num_surf  = _tint(self.bold_numbers_font.render(str(level)), (255,0,0))

        if self._name_sprites:
            tag_h=max(16,int(h*0.08)); gap=max(2,int(self.screen_width*0.01)); ty=y-3
            row_left  = x + 2
            row_right = x+w-13

            roster    = list(self._name_sprites.keys())
            max_scroll = max(0, len(roster)-1)
            self._status_char_scroll = max(0, min(self._status_char_scroll, max_scroll))

            # Left/right scroll arrows only appear once there's more than
            # one character to show — a single character never needs them.
            show_arrows = len(roster) > 1 and self.status_arrow_left and self.status_arrow_right

            # Now using the dedicated arrow_left/arrow_right sprites (correctly
            # oriented, not rotated), so scale to a target height like the
            # other menus' scroll arrows.
            # arrow_h controls just the arrows' size — bump the multiplier
            # (currently 1.3x tag_h) to make them bigger/smaller without
            # affecting the name-tag sprites, which stay sized to tag_h.
            arrow_h = int(tag_h * 1.2)
            def _scale_to_h(img, target_h):
                sf = target_h / img.get_height()
                return pygame.transform.scale(img,(max(1,int(img.get_width()*sf)),target_h))

            tags_left  = row_left
            tags_right = row_right

            left_scaled = None
            if show_arrows:
                left_img = (self.status_arrow_left_pressed if self.status_scroll_left_timer > 0
                            else (self.status_arrow_left if self._status_char_scroll > 0 else self.status_arrow_left_grey))
                left_scaled = _scale_to_h(left_img, arrow_h)
                tags_left = row_left + left_scaled.get_width() + gap

            right_ref_w = 0
            if show_arrows:
                right_ref_w = _scale_to_h(self.status_arrow_right, arrow_h).get_width()
                tags_right = row_right - right_ref_w - gap

            # Build the run of name tags that fit between the arrows without
            # overlapping them, starting from the current scroll index —
            # this is what decides how many names are shown at once. The
            # available span is tags_left→tags_right (bounded by the arrows,
            # or the full row if there aren't any); a name that would run
            # past that span isn't drawn at all — it stays hidden until the
            # user scrolls to it, rather than spilling over or getting
            # clipped. The lone exception is a single name wider than the
            # whole span: it's kept so something is never left blank.
            avail_w = max(0, tags_right - tags_left)
            visible = []
            total_w = 0
            for cid in roster[self._status_char_scroll:]:
                surfs = self._name_sprites[cid]
                surf  = surfs['selected'] if cid==char_id else surfs['unselected']
                if not surf:
                    continue
                s  = max(1,round(tag_h/surf.get_height()))
                tw = surf.get_width()*s
                added_w = tw if not visible else total_w+gap+tw
                if visible and added_w > avail_w:
                    break
                visible.append((cid,surf,s,tw))
                total_w = added_w

            # Center the visible run of names within the available span
            # (between the arrows, if shown) rather than hugging the left.
            start_x = tags_left + max(0, (avail_w - total_w)//2)

            if show_arrows:
                left_y = ty + (tag_h - left_scaled.get_height()) // 2
                screen.blit(left_scaled,(row_left,left_y))
                self._click_zones['status_scroll_left'] = pygame.Rect(row_left-4,left_y-4,left_scaled.get_width()+8,left_scaled.get_height()+8)

            tx = start_x
            for cid,surf,s,tw in visible:
                scaled=pygame.transform.scale(surf,(surf.get_width()*s,surf.get_height()*s))
                screen.blit(scaled,(tx,ty))
                self._click_zones[f'name_tag_{cid}'] = pygame.Rect(tx,ty,scaled.get_width(),scaled.get_height())
                tx+=tw+gap

            if show_arrows:
                can_scroll_right = (self._status_char_scroll+len(visible)) < len(roster)
                right_img = (self.status_arrow_right_pressed if self.status_scroll_right_timer > 0
                             else (self.status_arrow_right if can_scroll_right else self.status_arrow_right_grey))
                right_scaled = _scale_to_h(right_img, arrow_h)
                right_x = row_right - right_scaled.get_width()
                right_y = ty + (tag_h - right_scaled.get_height()) // 2
                screen.blit(right_scaled,(right_x,right_y))
                self._click_zones['status_scroll_right'] = pygame.Rect(right_x-4,right_y-4,right_scaled.get_width()+8,right_scaled.get_height()+8)

        name_y=y+49; gap_px=max(5,int(self.screen_width*0.005)); cx=x-6
        for surf in (name_surf,lvl_surf,num_surf):
            screen.blit(_shadow(surf),(cx+4,name_y)); screen.blit(surf,(cx,name_y)); cx+=surf.get_width()+gap_px*3

        name_h=max(name_surf.get_height(),lvl_surf.get_height(),self.bold_numbers_font.get_line_height())
        content_top=y+name_h+7
        sprite_cx=x+sprite_col_w//2-30; sprite_cy=content_top+(h-(content_top-y))//2-29

        if is_active:
            portrait_sprite = self._char_sprite
        else:
            try:
                preview_costume = load_config(char_id).get('costume', 'base')
            except Exception:
                preview_costume = 'base'
            portrait_sprite = self._load_char_sprite(char_id, preview_costume)
        if portrait_sprite:
            avail_w=sprite_col_w-8; avail_h=h-(content_top-y)-8
            int_scale=max(1,int(min(avail_w/portrait_sprite.get_width(),avail_h/portrait_sprite.get_height())))
            portrait=pygame.transform.scale(portrait_sprite,(portrait_sprite.get_width()*int_scale,portrait_sprite.get_height()*int_scale))
            screen.blit(portrait,portrait.get_rect(center=(sprite_cx,sprite_cy)))
        else:
            r=sprite_col_w//3
            pygame.draw.circle(screen,(80,80,160),(sprite_cx,sprite_cy),r)
            pygame.draw.circle(screen,(200,200,255),(sprite_cx,sprite_cy),r,2)

        bar_h=max(4,int(self.screen_height*0.008)); lh=max(20,int(self.stats_font.get_line_height()+20))
        cy=content_top+int(h-453); _label_gap=max(3,int(self.screen_width*0.004))

        def _yellow(s): c=s.copy(); c.fill((255,255,0),  special_flags=pygame.BLEND_RGBA_MULT); return c
        def _grey(s):   c=s.copy(); c.fill((180,180,180),special_flags=pygame.BLEND_RGBA_MULT); return c

        def draw_row(label, value_str, fill_ratio=None, fill_col=(100,200,100)):
            nonlocal cy
            ls=_yellow(self.stats_font.render(label)); screen.blit(ls,(stats_x,cy))
            vx=stats_x+ls.get_width()+_label_gap; parts=value_str.split('/')
            for i,part in enumerate(parts):
                if part:
                    ps=_grey(self.stats_numbers_font.render(part)); screen.blit(ps,(vx,cy)); vx+=ps.get_width()
                if i<len(parts)-1:
                    sl=_yellow(self.stats_numbers_font.render('/')); screen.blit(sl,(vx,cy)); vx+=sl.get_width()
            if fill_ratio is not None:
                bx=stats_x+int(stats_w*0.62); bw=int(stats_w*0.36); by_=cy+(lh-bar_h)//2
                pygame.draw.rect(screen,(30,30,30),(bx,by_,bw,bar_h))
                pygame.draw.rect(screen,fill_col,(bx,by_,int(bw*max(0.0,min(fill_ratio,1.0))),bar_h))
                pygame.draw.rect(screen,self.border_outer,(bx,by_,bw,bar_h),1)
            cy+=lh

        def draw_div():
            nonlocal cy
            cy += 2

        right_col_x=stats_x+int(stats_w*0.60); cy_start=cy

        if self.spacing_bar:
            sb=self.spacing_bar; so_h=sb.get_height(); so_w=sb.get_width()
            bar_tot=lh*4.35; tip_scale=max(1,round(bar_tot/so_h)); tip_px=so_h//4
            tip_h=tip_px*tip_scale; sb_w=so_w*tip_scale; mid_h=max(1,bar_tot-tip_h*2)
            top_s=pygame.transform.scale(sb.subsurface((0,0,so_w,tip_px)),(sb_w,tip_h))
            mid_s=pygame.transform.scale(sb.subsurface((0,tip_px,so_w,so_h-tip_px*2)),(sb_w,mid_h))
            bot_s=pygame.transform.scale(sb.subsurface((0,so_h-tip_px,so_w,tip_px)),(sb_w,tip_h))
            sb_x=right_col_x-sb_w//2-4 if self.tab_index==0 else right_col_x-sb_w//2+80
            sb_y=cy_start+40
            screen.blit(top_s,(sb_x,sb_y)); screen.blit(mid_s,(sb_x,sb_y+tip_h)); screen.blit(bot_s,(sb_x,sb_y+tip_h+mid_h))

        right_edge = x + w - 8  # right boundary of the content area — values right-align to this

        def draw_right_stat(label,val,row):
            ry=cy_start+48+row*lh
            is_alloc_cursor = (is_active and self.allocating_stats and label in STAT_ALLOC_LABELS
                               and STAT_ALLOC_LABELS[self.stat_alloc_index] == label)
            was_increased   = (is_active and label in STAT_ALLOC_LABELS
                               and self._session_stat_alloc.get(label, 0) > 0)
            screen.blit(_yellow(self.stats_font.render(f'{label}:')),(right_col_x+32,ry))
            label_h = self.stats_font.render(f'{label}:').get_height()
            val_color = (0,255,0) if was_increased else (180,180,180)
            val_surf = self.stats_numbers_font.render(str(val)).copy()
            val_surf.fill(val_color, special_flags=pygame.BLEND_RGBA_MULT)
            screen.blit(val_surf,(right_edge - val_surf.get_width(),ry))
            if is_alloc_cursor and self.equip_arrow:
                blink_on = (self._levelup_blink_timer % (self._levelup_blink_interval*2)) < self._levelup_blink_interval
                if blink_on:
                    arrow  = self.equip_arrow
                    # Integer pixel-perfect scale — same convention the equip
                    # page uses for this same sprite — instead of a fractional
                    # scale tied to text height, which shrank it down to
                    # blurry sub-pixel sizes.
                    arr_sc = max(1, int(self.canvas_height*0.05/arrow.get_height()))
                    scaled = pygame.transform.scale(arrow,(arrow.get_width()*arr_sc,arrow.get_height()*arr_sc))
                    screen.blit(scaled,(right_col_x+32-scaled.get_width(),ry+(label_h-scaled.get_height())//2 - 2))

        # LVL/XP/HP/EP/STR/POW/END/SPD are all per-character now (see
        # _resolve_viewed_progress / Game._switch_character /
        # Player.character_progress) — every unlocked character shows its
        # own real numbers here, not just whichever one is currently active.
        hp, max_hp = progress['hp'], progress['max_hp']
        ki, max_ki = progress['ki'], progress['max_ki']
        exp     = progress['total_exp']
        exp_nxt = progress['exp_to_next_level']
        pstats  = progress['stats']
        right_stats=[('STR',pstats.get('strength',0)),
                     ('POW',pstats.get('ki_power',0)),
                     ('END',pstats.get('vitality',0)),
                     ('SPD',pstats.get('speed',   0))]

        # ZENIE and TIME are shared/global across every character (one
        # wallet, one save-file playtime — not per-character), so these
        # always reflect the live player/session regardless of who's
        # being previewed.
        zenie = getattr(player,'zeni',0); t=int(play_time)
        hh,rem=divmod(t,3600); mm,ss=divmod(rem,60)
        time_str=f'{hh:02d}:{mm:02d}:{ss:02d}'

        draw_row('HP:',f'{hp}/{max_hp}'); draw_row('EP:',f'{ki}/{max_ki}')
        # NXT LVL should read as "how much more XP until the next level",
        # not the flat total that level costs — exp_to_next_level is the
        # latter (it's a per-level constant set once in level_up()), so
        # subtract off how much of it has already been earned this level
        # (progress['exp'], which counts up from 0 each level-up).
        remaining_to_next = max(0, exp_nxt - progress['exp'])
        draw_row('XP:',str(exp)); draw_row('NXT LVL:',str(remaining_to_next))
        draw_row('ZENIE:',str(zenie)); draw_row('TIME:',time_str)
        draw_div()
        # Both "LevelUp!" and "Pts." are only meaningful for the live/active
        # character (a previewed character has no stat_points pool). The row
        # stays visible either while there are points to spend, or while
        # actively allocating (so it doesn't vanish mid-allocation the
        # instant the last point gets spent, before Accept is pressed).
        show_points_row = is_active and (getattr(player,'stat_points',0) > 0 or self.allocating_stats)
        if show_points_row:
            # "LevelUp!" banner, one row above "Pts." — blinks via
            # self._levelup_blink_timer (advanced each frame in update()).
            # Mixed-font like the equip page labels: uppercase/non-alpha
            # chars from menu_uppercase_font, lowercase chars from
            # menu_lowercase_font. Hidden while allocating stat points —
            # the blink timer is reused for the allocation cursor arrow
            # instead during that mode (see draw_right_stat above).
            if getattr(player,'stat_points',0) > 0 and not self.allocating_stats:
                blink_on = (self._levelup_blink_timer % (self._levelup_blink_interval*2)) < self._levelup_blink_interval
                if blink_on:
                    lvlup_label = 'LevelUp!'
                    lvlup_ry = cy_start+48-lh*2
                    # Descender letters (p/q/g/y/j) need to sit lower than the
                    # rest of the row or they read as flat/capital-looking —
                    # same offset table used by the equip/options/journal pages.
                    _lvlup_desc = {'p':8,'q':8,'g':8,'y':8,'j':8}
                    lvlup_char_surfs = [
                        _tint((self.menu_uppercase_font if (ch.isupper() or not ch.isalpha()) else self.menu_lowercase_font).render(ch),(255,255,255))
                        for ch in lvlup_label
                    ]
                    lvlup_max_h = max(s.get_height() for s in lvlup_char_surfs)
                    cx = right_col_x+32
                    _lvlup_ls = 6
                    for ch, s in zip(lvlup_label, lvlup_char_surfs):
                        oy = (lvlup_max_h-s.get_height()) + _lvlup_desc.get(ch,0)
                        screen.blit(_shadow(s),(cx+1,lvlup_ry+oy+1))
                        screen.blit(s,(cx,lvlup_ry+oy))
                        cx += s.get_width()+_lvlup_ls

            # "Pts." — mixed-font label: capital P comes from stats_font
            # (matching STR/POW/END/SPD's font), but the lowercase t/s and
            # the period are pulled from the lowercase_menu font instead —
            # stats_font has no lowercase glyphs proven out, same reasoning
            # as the equip page's per-character font switching above.
            pts_ry = cy_start+48-lh
            pts_label = 'Pts.'
            _pts_ls = 6
            pts_char_surfs = [
                _yellow((self.stats_font if ch.isupper() else self.menu_lowercase_font).render(ch))
                for ch in pts_label
            ]
            pts_max_h = max(s.get_height() for s in pts_char_surfs)
            cx = right_col_x+32
            for s in pts_char_surfs:
                screen.blit(s,(cx,pts_ry+(pts_max_h-s.get_height())))
                cx += s.get_width()+_pts_ls
            # The point count itself turns green while allocating, to match
            # the selected-stat highlight color and signal "editable now".
            pts_color = (0,255,0) if self.allocating_stats else (180,180,180)
            pts_surf = self.stats_numbers_font.render(str(getattr(player,'stat_points',0))).copy()
            pts_surf.fill(pts_color, special_flags=pygame.BLEND_RGBA_MULT)
            screen.blit(pts_surf,(right_edge - pts_surf.get_width(),pts_ry))
        for row,(lbl,val) in enumerate(right_stats):
            draw_right_stat(lbl,val,row)

    # ── Equip page ────────────────────────────────────────────────────────────

    def _draw_equip_slot_list(self, screen, player, slot_x, slot_y, w, hovered_slot):
        """The default Equip-tab view: the 4 fixed slots (Body/Hands/Feet/
        Accessories), each showing what's currently equipped there."""
        slot_sprites=[self.equip_body,self.equip_hands,self.equip_feet,self.equip_accessories]
        slot_scale=max(1,round(self.canvas_height*0.07/slot_sprites[0].get_height())) if slot_sprites[0] else 1
        slot_gap=max(4,int(self.canvas_height*0.01)); slot_txt_gap=max(4,int(w*0.01))
        slot_offsets={0:(0,0),1:(0,0),2:(5,0),3:(0,4)}
        equipped_dict=getattr(player, 'equipped', None) or {}
        equipped_ids=[equipped_dict.get(key) for key in EQUIP_SLOT_KEYS]
        equipped=[self._get_equip_item_name(iid) for iid in equipped_ids]
        max_sprite_w=max((s.get_width()*slot_scale for s in slot_sprites if s),default=0)
        text_x_fixed=slot_x+max_sprite_w+slot_txt_gap

        for i,surf in enumerate(slot_sprites):
            if not surf: continue
            scaled=pygame.transform.scale(surf,(surf.get_width()*slot_scale,surf.get_height()*slot_scale))
            ox,oy=slot_offsets.get(i,(0,0)); bx_=slot_x+ox; by_=slot_y+oy
            if equipped_ids[i]:
                # Something's equipped in this slot: the frame sprite
                # (equip_body.png etc.) is just a per-category placeholder
                # background — once there's a real item icon to show, the
                # frame is redundant clutter behind it, so skip drawing it
                # entirely rather than layering the icon on top of it.
                icon = self._get_item_icon(equipped_ids[i])
                if icon:
                    icon_scaled = pygame.transform.scale(icon, (
                        icon.get_width()  * self.font_scale,
                        icon.get_height() * self.font_scale,
                    ))
                    icon_x = bx_ + (scaled.get_width()  - icon_scaled.get_width())  // 2
                    icon_y = by_ + (scaled.get_height() - icon_scaled.get_height()) // 2 + 2
                    screen.blit(icon_scaled, (icon_x, icon_y))
            else:
                screen.blit(scaled,(bx_,by_))
            if i==hovered_slot and self.equip_arrow:
                blink_on = (self._levelup_blink_timer % (self._levelup_blink_interval*2)) < self._levelup_blink_interval
                if blink_on:
                    arr_sc=max(1,int(self.canvas_height*0.05/self.equip_arrow.get_height()))
                    arr_surf=pygame.transform.scale(self.equip_arrow,(self.equip_arrow.get_width()*arr_sc,self.equip_arrow.get_height()*arr_sc))
                    screen.blit(arr_surf,(bx_-arr_surf.get_width()-max(2,int(self.canvas_width*0.005))+16,by_+(scaled.get_height()-arr_surf.get_height())//2))
            # Empty slot: "-None- (Body)" so the player still knows which
            # category this row is. Equipped: just the item's own name —
            # the "(Body)" suffix is redundant once the icon above already
            # shows what's worn there.
            if equipped[i]:
                label = equipped[i]
            else:
                label = f'-None- ({EQUIP_SLOT_LABELS[i]})'
            txt_x=text_x_fixed; txt_y=by_+(scaled.get_height()-self.menu_uppercase_font.get_line_height())//2
            _ls=6
            # Descender offsets: same convention as _blit_journal_text's
            # own _desc map, applied per-character rather than just the
            # single 'y' in "Body" the old code special-cased — any item
            # name can contain any of these letters (e.g. "Gi", "Cap").
            _desc={'p':8,'q':8,'g':8,'y':8,',':8}
            cx_=txt_x; max_h_=max((self.menu_uppercase_font if ch.isupper() or not ch.isalpha() else self.menu_lowercase_font).get_line_height() for ch in label)
            for j,ch in enumerate(label):
                font=self.menu_uppercase_font if ch.isupper() or not ch.isalpha() else self.menu_lowercase_font
                s=font.render(ch).copy(); s.fill((255,255,255),special_flags=pygame.BLEND_RGBA_MULT)
                shadow=s.copy(); shadow.fill((0,0,0),special_flags=pygame.BLEND_RGBA_MULT)
                oy_=max_h_-s.get_height(); eoy=_desc.get(ch.lower(),0)
                screen.blit(shadow,(cx_+1,txt_y+oy_+eoy+1)); screen.blit(s,(cx_,txt_y+oy_+eoy)); cx_+=s.get_width()+_ls
            self._click_zones[f'equip_slot_{i}']=pygame.Rect(bx_-10,by_-4,max_sprite_w+400,scaled.get_height()+slot_gap+4)
            slot_y+=scaled.get_height()+slot_gap

    def _draw_equip_item_list(self, screen, player, slot_x, slot_y, w, hovered_slot):
        """Sub-mode entered by pressing Select on a slot: a scrollable list
        of every owned item that fits that slot, in place of the 4 slots.
        Mirrors _draw_inventory_page's icon+text row style so the two lists
        read consistently."""
        entries = self._get_equip_entries(player, hovered_slot) if player is not None else []
        equipment = getattr(player, 'equipped', None) or {}
        equipped_id = equipment.get(EQUIP_SLOT_KEYS[hovered_slot])

        icon_h   = max(16, int(self.canvas_height * 0.07))
        row_h    = icon_h + max(4, int(self.canvas_height * 0.01))
        indent_x = slot_x
        start_y  = slot_y
        rows_visible = 4  # same 4-slot-tall budget as the slot list, so the frame stays the same size
        clip_bottom  = start_y + rows_visible * row_h

        self._equip_rows_visible = rows_visible
        self.equip_item_scroll = max(0, min(self.equip_item_scroll, max(0, len(entries) - rows_visible)))
        if self.equip_item_index >= len(entries):
            self.equip_item_index = max(0, len(entries) - 1)
        scroll = self.equip_item_scroll

        # Empty-slot case (no owned items at all) is handled one level up
        # by _draw_equip_page / _draw_equip_empty_state, which skips this
        # method entirely — entries is always non-empty by the time we
        # get here.

        # Same icon-to-text gap as _draw_item_confirm_popup's name text:
        # max(8, int(12 * _S)) - 40.
        icon_text_gap = max(8, int(13 * _S)) - 40

        # Item names always start at the same x, regardless of how wide
        # that item's own icon sprite is — using each icon's actual right
        # edge (as before) made thinner icons (e.g. Halloween Costume)
        # push their name too far left compared to wider ones (e.g.
        # Cotton Gi). Cotton Gi's icon is used as the reference: its
        # centered right edge (computed the same way icons are centered
        # per-row below) sets the fixed text column for every row.
        text_line_h = self.menu_uppercase_font.get_line_height()
        ref_icon = self._get_item_icon('cotton_gi')
        if ref_icon:
            ref_w = ref_icon.get_width() * self.font_scale
            ref_icon_x = indent_x + (icon_h - ref_w) // 2
            ref_icon_x = round(ref_icon_x / self.font_scale) * self.font_scale
            fixed_icon_right_edge = ref_icon_x + ref_w
        else:
            fixed_icon_right_edge = indent_x + (icon_h - text_line_h) // 2

        # Text rows are spaced independently of the icon rows: each row
        # steps by its own line height plus a fixed 9-sprite-pixel gap
        # (9 * font_scale real pixels, same convention as the icon nudge
        # above) rather than inheriting icon_h/row_h. Row 0 still starts
        # at the same vertical position as before so the first row's
        # icon/text alignment doesn't shift.
        text_row_step = text_line_h + 8 * self.font_scale
        text_start_y  = start_y + (icon_h - text_line_h) // 2

        screen.set_clip(pygame.Rect(indent_x, start_y, w, max(0, clip_bottom - start_y)))
        for i, (item_id, data, count) in enumerate(entries[scroll:scroll + rows_visible]):
            row_index = scroll + i
            ry = start_y + i * row_h
            is_selected = row_index == self.equip_item_index

            icon = self._get_item_icon(item_id)
            if icon:
                scaled = pygame.transform.scale(icon, (
                    icon.get_width()  * self.font_scale,
                    icon.get_height() * self.font_scale,
                ))
                prev = screen.get_clip(); screen.set_clip(None)
                icon_x = indent_x + (icon_h - scaled.get_width()) // 2 + 4
                icon_y = ry + (icon_h - scaled.get_height()) // 2
                icon_y = round(icon_y / self.font_scale) * self.font_scale + self.font_scale
                screen.blit(scaled, (icon_x, icon_y))
                screen.set_clip(prev)

            if is_selected and self.equip_arrow:
                # Frozen (always visible, no blink) while the equip confirm
                # popup is open — otherwise the arrow keeps blinking behind
                # the popup, which reads as a stray flicker under it.
                blink_on = True if self.equip_confirm_open else \
                    (self._levelup_blink_timer % (self._levelup_blink_interval * 2)) < self._levelup_blink_interval
                if blink_on:
                    arrow      = self.equip_arrow
                    arr_sc     = max(1, int(self.canvas_height * 0.05 / arrow.get_height()))
                    arr_scaled = pygame.transform.scale(arrow, (arrow.get_width() * arr_sc, arrow.get_height() * arr_sc))
                    arrow_x    = indent_x - arr_scaled.get_width() - max(2, int(self.canvas_width * 0.005)) + 16
                    prev = screen.get_clip(); screen.set_clip(None)
                    screen.blit(arr_scaled, (arrow_x, ry + (icon_h - arr_scaled.get_height()) // 2))
                    screen.set_clip(prev)

            tx = fixed_icon_right_edge + icon_text_gap
            ty = text_start_y + i * text_row_step
            col = (0, 255, 0) if is_selected else (255, 255, 255)
            is_equipped = item_id == equipped_id
            if is_equipped:
                label = data['name']
            elif count > 1:
                label = f"{data['name']} x"
            else:
                label = data['name']
            end_x, end_y = self._blit_journal_text(screen, label, tx, ty, col,
                                                     max_w=w - (tx - indent_x) - 60, word_gap=int(2 * _S))

            if is_equipped:
                # Currently-equipped row: a small "E" badge in the item's
                # place, instead of the old " (Equipped)" text suffix —
                # same position convention as the qty count below.
                e_x = end_x + int(10 * _S)
                e_y = end_y
                prev = screen.get_clip(); screen.set_clip(None)
                self._blit_journal_text(screen, 'E', e_x, e_y, (0xFF, 0x86, 0x00))
                screen.set_clip(prev)

            if count > 1 and not is_equipped:
                upper_h  = self.menu_uppercase_font.get_line_height()
                qty_surf = self.stats_numbers_font.render(str(count)).copy()
                qty_surf.fill(col, special_flags=pygame.BLEND_RGBA_MULT)
                qty_x = end_x + int(4 * _S)
                qty_y = end_y + (upper_h - qty_surf.get_height())
                prev = screen.get_clip(); screen.set_clip(None)
                screen.blit(qty_surf, (qty_x, qty_y))
                screen.set_clip(prev)

            self._click_zones[f'equip_item_{i}'] = pygame.Rect(indent_x, ry, w + 14, row_h)
        screen.set_clip(None)

    def _draw_equip_empty_state(self, screen, rect):
        """Slot has zero owned items: just 'No Equipment Available',
        centered in the full tab content area (not just the list column) —
        called by _draw_equip_page in place of the item list, divider
        bars, scroll arrows, and stat panel."""
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
        empty_text = 'No Equipment Available'
        text_w  = self._measure_journal_text_width(empty_text, word_gap=self.inv_empty_word_gap)
        line_h  = self.menu_uppercase_font.get_line_height()
        center_x = x + (w - text_w) // 2
        center_y = y + (h - line_h) // 2 - 46
        self._blit_journal_text(screen, empty_text, center_x, center_y, (255, 255, 255),
                                 max_w=w, word_gap=self.inv_empty_word_gap)

    def _draw_equip_page(self, screen, player, rect, hovered_slot=0):
        if not self.spacing_bar:
            return
        x,y,w,h=rect.x,rect.y,rect.width,rect.height; stats=getattr(player,'stats',{})
        lh=max(20,int(self.stats_font.get_line_height()+20))
        def _yellow(s): c=s.copy(); c.fill((255,255,0),  special_flags=pygame.BLEND_RGBA_MULT); return c
        def _grey(s):   c=s.copy(); c.fill((180,180,180),special_flags=pygame.BLEND_RGBA_MULT); return c

        slot_x=x+int(w*0.05)-52; slot_y=y+int(h*0.05)-44

        # A slot with zero owned items skips the whole sub-screen (item
        # rows, divider bars, scroll arrows, stat panel, description) in
        # favor of a single centered message — nothing else in the frame
        # has anything to show. A/Select gets greyed out by the button-
        # drawing code (see equip_list_is_empty there) since there's
        # nothing to select either.
        if self.equip_browsing_items and not self._get_equip_entries(player, hovered_slot):
            self._draw_equip_empty_state(screen, rect)
            return

        if self.equip_browsing_items:
            self._draw_equip_item_list(screen, player, slot_x, slot_y, w, hovered_slot)
        else:
            self._draw_equip_slot_list(screen, player, slot_x, slot_y, w, hovered_slot)

        sb=self.spacing_bar; so_h=sb.get_height(); so_w=sb.get_width()
        bar_tot=lh*4
        vert_x=x+int(w*0.35)+333; vert_y=y+int(h*0.1)-54
        # horiz_base_x anchors the horizontal bar independently of vert_x,
        # so moving the vertical divider (vert_x, above) no longer drags
        # the horizontal bar along with it. This starts out equal to
        # vert_x's own formula so the horizontal bar's on-screen position
        # is unchanged — edit this line to move the horizontal bar alone.
        horiz_base_x=x+int(w*0.35)+335
        # arrows_base_x anchors the up/down scroll arrows independently of
        # vert_x, so moving the vertical divider no longer drags the
        # arrows with it. Starts out equal to vert_x's own formula so the
        # arrows' on-screen position is unchanged — edit this line to move
        # the arrows alone.
        arrows_base_x=x+int(w*0.35)+335

        # When browsing a slot's items, the divider bar shrinks to leave
        # room for scroll arrows at its top/bottom ends (replacing the
        # separate right-edge scroll sidebar the other tabs use) — the
        # bar's total footprint (bar_tot) stays the same as the slot-list
        # view, so vert_y/the stat rows below never move.
        equip_scroll_arrows = self.equip_browsing_items
        if equip_scroll_arrows:
            equip_entries = self._get_equip_entries(player, hovered_slot)
            can_up      = self.equip_item_index > 0
            can_down    = self.equip_item_index < len(equip_entries) - 1
            arrow_scale = max(1, int(self.canvas_height * 0.06))
            arrow_gap   = 4
            bar_span    = 1
        else:
            bar_span = bar_tot + 1

        tip_scale=max(1,round(bar_tot/so_h)); tip_px=so_h//4
        tip_h=tip_px*tip_scale; vert_w=so_w*tip_scale; mid_h=max(1,bar_span-tip_h*2)
        top_s=pygame.transform.scale(sb.subsurface((0,0,so_w,tip_px)),(vert_w,tip_h))
        mid_s=pygame.transform.scale(sb.subsurface((0,tip_px,so_w,so_h-tip_px*2)),(vert_w,mid_h+20))
        bot_s=pygame.transform.scale(sb.subsurface((0,so_h-tip_px,so_w,tip_px)),(vert_w,tip_h))

        bar_y = vert_y + arrow_scale + arrow_gap if equip_scroll_arrows else vert_y
        screen.blit(top_s,(vert_x,bar_y)); screen.blit(mid_s,(vert_x,bar_y+tip_h)); screen.blit(bot_s,(vert_x,bar_y+tip_h+19+mid_h))

        if equip_scroll_arrows:
            up_surf = self.arrow_up_pressed if self.scroll_up_timer > 0 else (self.arrow_up if can_up else self.arrow_up_grey)
            if up_surf:
                sf = arrow_scale / up_surf.get_height()
                up_scaled = pygame.transform.scale(up_surf, (int(up_surf.get_width() * sf), arrow_scale))
                up_x = arrows_base_x + (vert_w - up_scaled.get_width()) // 2
                screen.blit(up_scaled, (up_x, vert_y - 4))
                self._click_zones['scroll_up'] = pygame.Rect(up_x-4, vert_y-4, up_scaled.get_width()+8, arrow_scale+8)

            bar_bottom_y = bar_y + tip_h + 20 + mid_h + tip_h
            dn_surf = self.arrow_down_pressed if self.scroll_down_timer > 0 else (self.arrow_down if can_down else self.arrow_down_grey)
            if dn_surf:
                sf = arrow_scale / dn_surf.get_height()
                dn_scaled = pygame.transform.scale(dn_surf, (int(dn_surf.get_width() * sf), arrow_scale))
                dn_x = arrows_base_x + (vert_w - dn_scaled.get_width()) // 2
                dn_y = bar_bottom_y + arrow_gap * 2 - 1
                screen.blit(dn_scaled, (dn_x, dn_y))
                self._click_zones['scroll_down'] = pygame.Rect(dn_x-4, dn_y-4, dn_scaled.get_width()+8, arrow_scale+8)

        horiz_src=pygame.transform.flip(pygame.transform.rotate(sb,90), False, True); hs_w=horiz_src.get_width(); hs_h=horiz_src.get_height()
        htip_px=hs_w//4; htip_w=htip_px*tip_scale; horiz_h=hs_h*tip_scale; horiz_w=lh*4
        hl=pygame.transform.scale(horiz_src.subsurface((0,0,htip_px,hs_h)),(htip_w,horiz_h))
        hm=pygame.transform.scale(horiz_src.subsurface((htip_px,0,hs_w-htip_px*2,hs_h)),(max(1,horiz_w-htip_w*2+720),horiz_h))
        hr=pygame.transform.scale(horiz_src.subsurface((hs_w-htip_px,0,htip_px,hs_h)),(htip_w,horiz_h))
        horiz_x=horiz_base_x-(horiz_w-vert_w)//2-570; horiz_y=vert_y-horiz_h+252
        screen.blit(hl,(horiz_x,horiz_y)); screen.blit(hm,(horiz_x+htip_w,horiz_y)); screen.blit(hr,(horiz_x+720+horiz_w-htip_w,horiz_y))

        # Hovered item's data, shared by the RqLvl stat row below and the
        # description strip under the horizontal bar — computed once here
        # instead of twice so both stay in sync with the same entry.
        hovered_item_data = {}
        if self.equip_browsing_items:
            equip_entries = self._get_equip_entries(player, hovered_slot)
            if equip_entries and 0 <= self.equip_item_index < len(equip_entries):
                hovered_item_data = equip_entries[self.equip_item_index][1]

        # Description strip: the hovered item's flavor text, shown below
        # the horizontal divider bar while browsing a slot's items.
        if self.equip_browsing_items:
            description = hovered_item_data.get('description', '')
            if description:
                desc_x = x + 8
                desc_y = horiz_y + horiz_h + max(8, int(2 * _S))
                self._blit_journal_text(screen, description, desc_x, desc_y, (255, 255, 255),
                                         max_w=w - 40, word_gap=int(4 * _S))

        stat_x=vert_x+vert_w+max(8,int(w*0.02)) + 18
        base_stats = {'STR': stats.get('strength',stats.get('str',0)), 'POW': stats.get('ki_power',stats.get('pow',0)),
                      'END': stats.get('vitality',stats.get('end',0)), 'SPD': stats.get('speed',stats.get('spd',0))}

        # Per-row hover preview: while browsing a slot's items, compare the
        # hovered item's own stat bonuses against whatever's currently
        # equipped in that slot (nothing equipped = all zeros). Each stat
        # row then displays what the total would BECOME if the hovered item
        # were equipped instead (not just the current total), colored green
        # (would go up), red (would go down), or left at the default grey
        # (unchanged). STAT_ID_FOR_LABEL maps each row's display label to
        # the stat id keys used in item['effect']['stats'] (see
        # core/items.py / systems/item_effects.py's _resolve_stat_key).
        STAT_ID_FOR_LABEL = {'STR': 'strength', 'POW': 'ki_power', 'END': 'vitality', 'SPD': 'speed'}
        stat_deltas = {}
        req_level = None
        if self.equip_browsing_items:
            equipped = getattr(player, 'equipped', {}) or {}
            equipped_id = equipped.get(EQUIP_SLOT_KEYS[hovered_slot])
            equipped_item = get_item(equipped_id) if equipped_id else None
            equipped_stats = (equipped_item or {}).get('effect', {}).get('stats', {})
            hovered_stats  = hovered_item_data.get('effect', {}).get('stats', {})
            for lbl, stat_id in STAT_ID_FOR_LABEL.items():
                stat_deltas[lbl] = hovered_stats.get(stat_id, 0) - equipped_stats.get(stat_id, 0)

        stat_rows=[(lbl, base_stats[lbl] + stat_deltas.get(lbl, 0)) for lbl in ('STR','POW','END','SPD')]
        if self.equip_browsing_items:
            # req_level field doesn't exist on item data yet, so this stays
            # None (rendered as a plain '-', no color) until that's added —
            # the green/red comparison below is wired up and ready for it.
            req_level = hovered_item_data.get('req_level')
            stat_rows.append(('RqLvl', req_level if req_level is not None else '-'))
        # The block shifts up by one row's height while browsing items so
        # the added RqLvl row fits without pushing past the bottom of the
        # divider bar; the 4-stat layout otherwise is unchanged.
        stat_y_offset = -lh + 28 if self.equip_browsing_items else 0

        def _color(s, c):
            t = s.copy(); t.fill(c, special_flags=pygame.BLEND_RGBA_MULT); return t

        def _row_color(lbl):
            # Green = this stat/requirement favors the hovered item, red =
            # it works against the player, None = no change / not
            # applicable (slot-list view, or a stat the item doesn't touch)
            # — callers fall back to the default grey value color for None.
            if lbl == 'RqLvl':
                if req_level is None:
                    return None
                player_level = getattr(player, 'level', 1)
                return (0, 255, 0) if player_level >= req_level else (255, 60, 60)
            delta = stat_deltas.get(lbl, 0)
            if delta > 0:
                return (0, 255, 0)
            if delta < 0:
                return (255, 60, 60)
            return None

        for i,(lbl,val) in enumerate(stat_rows):
            ry=vert_y+stat_y_offset+i*lh+20
            row_color  = _row_color(lbl)
            value_tint = (lambda s, c=row_color: _color(s, c)) if row_color else _grey
            if lbl.isupper():
                label_surf = self.stats_font.render(f'{lbl}:')
                screen.blit(_yellow(label_surf),(stat_x,ry))
                label_end_x = stat_x + label_surf.get_width()
            else:
                # Mixed-case label (e.g. "RqLvl:") — stats_font has no
                # lowercase glyphs, so render per-character like "Pts."
                # above, pulling uppercase letters from stats_font and
                # everything else from menu_lowercase_font.
                lbl_text = f'{lbl}:'
                # 'q' has a descender that extends below the baseline; flush
                # bottom-alignment (max_h - glyph_h) has no room for it and
                # pushes the whole letter up, so nudge it down like the 'y'
                # case in the equip-slot labels above.
                _char_offs = {j:8 for j,ch in enumerate(lbl_text) if ch=='q'}
                # Non-alpha chars (the trailing ':') route to stats_font, not
                # menu_lowercase_font — same convention as the equip-slot
                # labels above (`ch.isupper() or not ch.isalpha()`), since
                # menu_lowercase_font's folder has no colon.png and a missing
                # glyph renders as a blank 1x1 surface instead of falling back.
                char_surfs = [
                    _yellow((self.stats_font if (ch.isupper() or not ch.isalpha()) else self.menu_lowercase_font).render(ch))
                    for ch in lbl_text
                ]
                max_h = max(s.get_height() for s in char_surfs)
                cx = stat_x
                for j,s in enumerate(char_surfs):
                    screen.blit(s,(cx,ry+(max_h-s.get_height())+_char_offs.get(j,0)))
                    cx += s.get_width()+6
                label_end_x = cx - 6
            # Value sits a fixed gap past the end of this row's own label —
            # so e.g. "RqLvl:" (wider than "STR:") pushes its value further
            # right, rather than every row's value sharing one column x.
            value_gap = max(16, int(w * 0.015))
            screen.blit(value_tint(self.stats_numbers_font.render(str(val))),(label_end_x+value_gap,ry))

    # ── Journal page ──────────────────────────────────────────────────────────

    def _draw_inventory_page(self, screen, player, rect):
        """
        Scrollable item list for the Inventory tab, mirroring
        _draw_journal_page's layout: icon + text rows, clipped to the
        content area, with a cursor row and a description/feedback strip
        along the bottom.
        """
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
        entries = self._get_inventory_entries(player)

        icon_h = max(16, int(self.canvas_height * 0.09))
        row_h = icon_h
        text_gap = 43
        indent_x = x + 45
        start_y = y + int(h * 0.06) + 9
        # Text position is the fixed anchor for the row — this is the exact
        # x that lined up correctly with just Miso Soup in the inventory.
        # icon_col_w is only used to compute it; it no longer sizes or
        # centers the icon itself, so it can never move once set here.
        icon_col_w = icon_h
        text_x = indent_x + icon_col_w + text_gap - 7

        # Reserve the bottom of the panel for the selected item's
        # description (or a just-used feedback message).
        desc_lines_h = self.menu_lowercase_font.get_line_height() * 2 + 12
        clip_bottom  = y + h - 8 - desc_lines_h
        desc_y       = clip_bottom + 10

        rows_visible = max(1, (clip_bottom - start_y) // row_h)
        self._inv_rows_visible = rows_visible
        self.inv_scroll_max    = max(0, len(entries) - rows_visible)
        self.inv_scroll_offset = min(self.inv_scroll_offset, self.inv_scroll_max)
        if self.inv_selected_index >= len(entries):
            self.inv_selected_index = max(0, len(entries) - 1)
        scroll = self.inv_scroll_offset

        if not entries:
            if self._current_inventory_category() == CATEGORY_SUPPLIES:
                empty_text, empty_col = 'No Supplies', (255, 255, 0)
            else:
                empty_text, empty_col = 'No items.', (160, 160, 160)
            text_w   = self._measure_journal_text_width(empty_text, word_gap=self.inv_empty_word_gap)
            line_h   = self.menu_uppercase_font.get_line_height()
            center_x = x + (w - text_w) // 2 - 35
            center_y = start_y + max(0, (clip_bottom - start_y) // 2 - line_h // 2) - 36
            self._blit_journal_text(screen, empty_text, center_x, center_y, empty_col, max_w=w, word_gap=self.inv_empty_word_gap)
            return

        screen.set_clip(pygame.Rect(x, start_y, w, max(0, clip_bottom - start_y)))
        for i, (item_id, data, count) in enumerate(entries[scroll:scroll + rows_visible]):
            row_index = scroll + i
            ry = start_y + i * row_h
            is_selected = row_index == self.inv_selected_index

            icon = self._get_item_icon(item_id)
            if icon:
                # Use the same flat pixel scale as every other sprite/font in
                # this menu (self.font_scale) instead of deriving a scale from
                # the row height — that made each icon's crispness depend on
                # its native resolution instead of matching the rest of the UI.
                scaled = pygame.transform.scale(icon, (
                    icon.get_width()  * self.font_scale,
                    icon.get_height() * self.font_scale,
                ))
                prev = screen.get_clip(); screen.set_clip(None)
                # The icon's position is derived FROM the fixed text_x, not
                # the other way around: its right edge always sits text_gap
                # pixels before the text, so a wider/narrower icon shifts its
                # own left edge instead of ever moving the text.
                # Icons are centered within a fixed icon column
                # (icon_col_w, currently == icon_h) that sits at indent_x —
                # not pinned to either edge. A pure left-pin makes wide
                # icons (dinosaur tail, 32px) crowd the text; a pure
                # right-pin (the old code) makes them blow past the left
                # edge of the box. Centering in the column matches the
                # reference UI and keeps every icon visually balanced
                # regardless of its native width.
                icon_x = indent_x + (icon_col_w - scaled.get_width()) // 2
                icon_x = round(icon_x / self.font_scale) * self.font_scale
                # Snap the icon's top edge to the game's native pixel grid.
                # Everything here (fonts, background texture, icons) is
                # drawn at native resolution and scaled up by font_scale —
                # so any sprite's y-position needs to land on a multiple of
                # font_scale, or its "big pixels" straddle two different
                # background pixel-rows and visibly cut through a line.
                # Plain centering with // 2 doesn't guarantee that; some
                # icon heights happen to land on a multiple of font_scale
                # by luck (15px ones did) and others don't (16px).
                icon_y = ry + (icon_h - scaled.get_height()) // 2
                icon_y = round(icon_y / self.font_scale) * self.font_scale
                screen.blit(scaled, (icon_x, icon_y))
                screen.set_clip(prev)

            # Blinking cursor arrow for the hovered/selected row — reuses the
            # same sprite + blink timer as the stat-allocation and equip
            # cursors elsewhere in this menu. Sits well left of the icon
            # rather than hugging it (arrow_gap controls how far).
            if is_selected and self.equip_arrow and not self.item_confirm_open:
                blink_on = (self._levelup_blink_timer % (self._levelup_blink_interval * 2)) < self._levelup_blink_interval
                if blink_on:
                    arrow      = self.equip_arrow
                    arr_sc     = max(1, int(self.canvas_height * 0.05 / arrow.get_height()))
                    arr_scaled = pygame.transform.scale(arrow, (arrow.get_width() * arr_sc, arrow.get_height() * arr_sc))
                    arrow_gap  = 36  # distance between the arrow and the icon's left edge
                    arrow_x    = indent_x - arrow_gap - arr_scaled.get_width()
                    prev = screen.get_clip(); screen.set_clip(None)
                    screen.blit(arr_scaled, (arrow_x, ry + (icon_h - arr_scaled.get_height()) // 2 + 6))
                    screen.set_clip(prev)

            tx  = text_x
            ty  = ry + (icon_h - self.menu_uppercase_font.get_line_height()) // 2 - 2
            col = (0, 255, 0) if is_selected else (255, 255, 255)
            # "x" stays in the regular name font/line; only the digit itself
            # uses the dedicated numbers font (assets/ui/fonts/numbers).
            label = f"{data['name']} x" if count > 1 else data['name']
            end_x, end_y = self._blit_journal_text(screen, label, tx, ty, col,
                                                     max_w=w - (tx - x) - 60, word_gap=int(2 * _S))

            if count > 1:
                upper_h  = self.menu_uppercase_font.get_line_height()
                qty_surf = self.stats_numbers_font.render(str(count)).copy()
                qty_surf.fill(col, special_flags=pygame.BLEND_RGBA_MULT)
                qty_x = end_x + int(4 * _S)
                qty_y = end_y + (upper_h - qty_surf.get_height())
                prev = screen.get_clip(); screen.set_clip(None)
                screen.blit(qty_surf, (qty_x, qty_y))
                screen.set_clip(prev)

            # Click-to-select-and-use zone for this visible row.
            self._click_zones[f'inv_row_{i}'] = pygame.Rect(indent_x, ry, w + 14, row_h)
        screen.set_clip(None)

        # Description strip: shows the just-used feedback message briefly.
        # (Per-item flavor text/description removed for now.)
        if self._item_feedback_timer > 0 and self._item_feedback_text:
            self._blit_journal_text(screen, self._item_feedback_text, x, desc_y, (0, 255, 0), max_w=w)

    def _draw_journal_page(self, screen, rect):
        x,y,w,h=rect.x,rect.y,rect.width,rect.height
        mm=self._mission_manager
        if mm is None: return
        showing_completed=(self.journal_tab_index==1)
        missions=mm.get_completed_missions() if showing_completed else mm.get_active_missions()
        icon_h=self._quest_sprite_h; row_h=icon_h+10; text_gap=12
        text_x_off=0; text_y_off=-4; indent_x=x-14; start_y=y+int(h*0.06)+31
        text_col=(255,255,255); done_col=(160,255,160); clip_bottom=y+h-8
        rows_visible=max(1,(clip_bottom-start_y)//row_h)
        self.inv_scroll_max=max(0,len(missions)-rows_visible)
        self._journal_scroll=min(self._journal_scroll,self.inv_scroll_max)
        scroll=self._journal_scroll
        screen.set_clip(pygame.Rect(x,start_y,w,clip_bottom-start_y))
        for i,mission in enumerate(missions[scroll:scroll+rows_visible]):
            ry=start_y+i*row_h; qt=mission.get('quest_type','side')
            icon=self._quest_type_sprites.get(qt); icon_w=0
            if icon:
                s=max(1,round(icon_h/icon.get_height()))
                scaled=pygame.transform.scale(icon,(icon.get_width()*s,icon.get_height()*s))
                icon_w=scaled.get_width(); prev=screen.get_clip(); screen.set_clip(None)
                screen.blit(scaled,(indent_x,ry)); screen.set_clip(prev)
            tx=indent_x+icon_w+text_gap+text_x_off
            ty=ry+(icon_h-self.menu_uppercase_font.get_line_height())//2+text_y_off
            col=done_col if showing_completed else text_col
            self._blit_journal_text(screen,self._mission_current_objective(mission,showing_completed),tx,ty,col,max_w=w-(tx-x)-16)
        screen.set_clip(None)

    def _mission_current_objective(self, mission, completed=False):
        objs=mission.get('objectives',[])
        if completed: return objs[-1].get('description','Complete!') if objs else 'Complete!'
        for obj in objs:
            if not obj.get('completed',False): return obj.get('description','')
        return 'Return to NPC'

    def _measure_journal_text_width(self, text, word_gap=None):
        """
        Single-line width of `text` as _blit_journal_text would render it —
        same glyph lookup/spacing rules, but nothing is drawn. Used to
        center short strings (e.g. the inventory 'No Supplies' message)
        since _blit_journal_text itself only ever left-aligns.
        """
        wg = int(8 * _S) if word_gap is None else word_gap
        char_spacing = self.menu_uppercase_font.letter_spacing

        def _glyph(ch):
            if ch.isdigit():
                g = self.stats_numbers_font.glyphs.get(ch)
                if g:
                    return g
            elif ch.isalpha():
                font = self.menu_uppercase_font if ch.isupper() else self.menu_lowercase_font
                g = font.glyphs.get(ch.upper())
                if g:
                    return g
            for font in (self.menu_uppercase_font, self.menu_lowercase_font, self.stats_numbers_font):
                g = font.glyphs.get(ch)
                if g:
                    return g
            return None

        total_w = 0
        words = text.split(' ')
        for wi, word in enumerate(words):
            word_w = 0
            had_glyph = False
            for ch in word:
                g = _glyph(ch)
                if g:
                    word_w += g.get_width() + char_spacing
                    had_glyph = True
                else:
                    word_w += max(4, int(6 * _S))
            word_w = max(0, word_w - (char_spacing if word and had_glyph else 0))
            total_w += word_w
            if wi < len(words) - 1:
                total_w += wg
        return total_w

    def _blit_journal_text(self, screen, text, x, y, color=(255,255,255), max_w=9999, word_gap=None):
        upper_h=self.menu_uppercase_font.get_line_height(); lh=upper_h; cx=x; line_y=y
        _desc={'p':8,'q':8,'g':8,'y':8,',':8}  # ← ',' offset: raise/lower the comma by tweaking this number
        wg = int(8*_S) if word_gap is None else word_gap
        # Character-to-character spacing for this renderer. Fixed to the
        # menu letter font's own spacing rather than each glyph's source
        # font's letter_spacing — stats_numbers_font (used for digits) has
        # a much tighter native spacing meant for compact stat readouts, so
        # letting digits keep it made numbers sit closer together than the
        # surrounding letters. Using one shared value keeps digits, letters,
        # and punctuation evenly spaced with each other in this text.
        char_spacing = self.menu_uppercase_font.letter_spacing

        def _font_and_glyph(ch):
            # Digits: assets/ui/fonts/uppercase_menu has no 0–9 glyphs at all —
            # the real number sprites live in assets/ui/fonts/numbers
            # (self.stats_numbers_font), same font used for stat values
            # elsewhere. Route digits there instead of silently missing.
            if ch.isdigit():
                g = self.stats_numbers_font.glyphs.get(ch)
                if g:
                    return self.stats_numbers_font, g
            # Letters: upper/lowercase menu fonts, as before.
            elif ch.isalpha():
                font = self.menu_uppercase_font if ch.isupper() else self.menu_lowercase_font
                g = font.glyphs.get(ch.upper())
                if g:
                    return font, g
            # Punctuation and anything else: try every font that might
            # define it (uppercase menu font has period/comma/etc. entries
            # if the corresponding .png exists in its folder; fall back to
            # lowercase menu font, then numbers font, in case it lives there
            # instead).
            for font in (self.menu_uppercase_font, self.menu_lowercase_font, self.stats_numbers_font):
                g = font.glyphs.get(ch)
                if g:
                    return font, g
            return None, None

        for word in text.split(' '):
            word_w=0
            had_glyph=False
            for ch in word:
                font, g = _font_and_glyph(ch)
                if g:
                    word_w += g.get_width() + char_spacing
                    had_glyph = True
                else:
                    # No glyph anywhere for this character — still reserve
                    # a small width for it so it doesn't get squashed
                    # against its neighbors once we fall through to the
                    # "missing glyph" branch below.
                    word_w += max(4, int(6 * _S))
            word_w=max(0,word_w-(char_spacing if word and had_glyph else 0))
            if cx>x and cx+word_w>x+max_w: cx=x; line_y+=lh+2 + 14
            for ch in word:
                font, g = _font_and_glyph(ch)
                if g:
                    oy=upper_h-g.get_height()+_desc.get(ch,0)
                    tinted=g.copy(); tinted.fill(color,special_flags=pygame.BLEND_RGBA_MULT)
                    screen.blit(tinted,(cx,line_y+oy)); cx+=g.get_width()+char_spacing
                elif ch==' ':
                    cx+=wg
                else:
                    # Character has no glyph in any font — advance the
                    # cursor anyway instead of silently dropping it, so
                    # text after it doesn't run together.
                    cx+=max(4, int(6*_S))
            cx+=wg
        return cx, line_y

    # ── Options page ──────────────────────────────────────────────────────────

    def _draw_options_page(self, screen, rect, hovered_item=0, editing=False, values=None):
        x,y,w,h=rect.x,rect.y,rect.width,rect.height
        _ls=6; _word_gap=18; _line_gap=16
        lh=max(20,int(self.menu_uppercase_font.get_line_height()*2))
        _desc={'p':8,'q':8}

        def _make_words(text,color=(255,255,255)):
            result=[]
            for word in text.split(' '):
                surfs=[]
                for ch in word:
                    font=self.menu_uppercase_font if ch.isupper() or not ch.isalpha() else self.menu_lowercase_font
                    s=font.render(ch).copy(); s.fill(color,special_flags=pygame.BLEND_RGBA_MULT); surfs.append(s)
                result.append(surfs)
            return result

        def _label_w(words):
            total=0
            for wi,ws in enumerate(words):
                total+=sum(s.get_width() for s in ws)+_ls*(len(ws)-1)
                if wi<len(words)-1: total+=_word_gap
            return total

        def _blit_label(words,bx,by,char_offsets=None):
            max_h=max(s.get_height() for ws in words for s in ws); cx=bx; gi=0
            for wi,ws in enumerate(words):
                for s in ws:
                    shadow=s.copy(); shadow.fill((0,0,0),special_flags=pygame.BLEND_RGBA_MULT)
                    oy=max_h-s.get_height(); eoy=char_offsets.get(gi,0) if char_offsets else 0
                    screen.blit(shadow,(cx+1,by+oy+eoy+1)); screen.blit(s,(cx,by+oy+eoy))
                    cx+=s.get_width()+_ls; gi+=1
                if wi<len(words)-1: cx+=_word_gap-_ls; gi+=1

        settings=['Sound FX Volume','Music Volume','Text Speed']
        top_x=x+int(w*0.05)-51; top_y=y+int(h*0.05)-28
        bar_scale=max(1,int(self.canvas_height*0.03/self.optionbar_empty.get_height())) if self.optionbar_empty else 1
        bar_x_offset=int(w*0.35)+130; bar_y_offset=0

        for i,label in enumerate(settings):
            color=(0,255,0) if i==hovered_item else (255,255,255)
            words=_make_words(label,color=color)
            char_offs={}; gi=0
            for word in label.split(' '):
                for ch in word:
                    if ch in _desc: char_offs[gi]=_desc[ch]
                    gi+=1
                gi+=1
            _blit_label(words,top_x,top_y,char_offsets=char_offs if char_offs else None)
            if self.optionbar_empty and self.optionbar_filled:
                bw_=self.optionbar_empty.get_width()*bar_scale; bh_=self.optionbar_empty.get_height()*bar_scale
                bx_=top_x+bar_x_offset; by_=top_y+bar_y_offset
                screen.blit(pygame.transform.scale(self.optionbar_empty,(bw_,bh_)),(bx_,by_))
                ratio=max(0.0,min(values[i] if values else 1.0,1.0))
                full_w=self.optionbar_filled.get_width(); full_h=self.optionbar_filled.get_height()
                cap_px=2; left_px=2; mid_src=full_w-left_px-cap_px
                total_sw=max(left_px+cap_px+1,int(full_w*ratio))*bar_scale; mid_sw=max(1,total_sw-(left_px+cap_px)*bar_scale)
                ls=self.optionbar_filled.subsurface((0,0,left_px,full_h))
                ms=self.optionbar_filled.subsurface((left_px,0,mid_src,full_h))
                cs=self.optionbar_filled.subsurface((full_w-cap_px,0,cap_px,full_h))
                screen.blit(pygame.transform.scale(ls,(left_px*bar_scale,bh_)),(bx_,by_))
                screen.blit(pygame.transform.scale(ms,(mid_sw,bh_)),(bx_+left_px*bar_scale,by_))
                screen.blit(pygame.transform.scale(cs,(cap_px*bar_scale,bh_)),(bx_+left_px*bar_scale+mid_sw,by_))
            self._click_zones[f'options_row_{i}']=pygame.Rect(x,top_y-4,w,lh+8)
            top_y+=lh+_line_gap

        credits_words=_make_words('Credits',color=(0,255,0) if hovered_item==3 else (255,255,255))
        sleep_words  =_make_words('Sleep',  color=(0,255,0) if hovered_item==4 else (255,255,255))
        gap=int(w*0.15)+180; total_w_=_label_w(credits_words)+gap+_label_w(sleep_words)
        start_x_=x+(w-total_w_)//2-20; bottom_y=y+h-int(h*0.2)-124
        _blit_label(credits_words,start_x_,bottom_y)
        _blit_label(sleep_words,start_x_+_label_w(credits_words)+gap,bottom_y,char_offsets={4:8})
        self._click_zones['options_row_3']=pygame.Rect(start_x_-10,bottom_y-4,_label_w(credits_words)+20,lh+8)
        self._click_zones['options_row_4']=pygame.Rect(start_x_+_label_w(credits_words)+gap-10,bottom_y-4,_label_w(sleep_words)+20,lh+8)

    # ══════════════════════ shared visual helpers ══════════════════════════════

    def _draw_scanlines(self, screen, rect):
        sh=2
        for yy in range(rect.top,rect.bottom,sh*2):
            pygame.draw.rect(screen,self.bg_scanline_dark, pygame.Rect(rect.left,yy,   rect.width,sh))
            pygame.draw.rect(screen,self.bg_scanline_light,pygame.Rect(rect.left,yy+sh,rect.width,sh))

    def _render_text_with_shadow(self, screen, text, position, anchor='center'):
        shadow=self.bitmap_font.render(text).copy(); shadow.fill(self.text_shadow_color,special_flags=pygame.BLEND_RGBA_MULT)
        label =self.bitmap_font.render(text).copy(); label.fill(self.text_color,special_flags=pygame.BLEND_RGBA_MULT)
        sx=position[0]+self.shadow_offset[0]; sy=position[1]+self.shadow_offset[1]
        def _rect(s,pos):
            if anchor=='center':  return s.get_rect(center=pos)
            if anchor=='midleft': return s.get_rect(midleft=pos)
            return s.get_rect(topleft=pos)
        screen.blit(shadow,_rect(shadow,(sx,sy))); screen.blit(label,_rect(label,position))

    def _draw_tiled_background(self, screen, rect):
        if not self.bg_texture: self._draw_scanlines(screen,rect); return
        ox=self.bg_offset_x%self._bg_tile_w; oy=self.bg_offset_y%self._bg_tile_h
        prev=screen.get_clip(); screen.set_clip(rect)
        # Always blit from the same world origin as the full-screen background
        # so the tile pattern is continuous no matter which sub-rect is clipped
        # (e.g. the inset interior of the item-confirm popup). Aligning to
        # rect.left/top instead would shift the pattern relative to everything
        # outside that rect.
        screen.blit(self.bg_texture,(-ox,-oy)); screen.set_clip(prev)

    def _draw_9slice_sprite(self, screen, sprite, x, y, width, height, corner_size=16):
        if not sprite: return False
        sw,sh=sprite.get_width(),sprite.get_height(); border_scale=4
        cw=min(corner_size,sw//3); ch=min(corner_size,sh//3)
        # Clamp the scaled corner thickness to at most half the box's own
        # width/height. Without this, a small box (like the item-confirm
        # popup) can make mw/mh negative below, and pygame.transform.scale
        # raises ValueError on a negative size — which aborts this function
        # partway through its draw sequence (corners first, then top/bottom
        # edges, then left/right edges, then the center fill last), leaving
        # whichever slices hadn't been blitted yet simply missing. Large
        # boxes like the main pause menu panel are never affected since
        # they're nowhere near this limit.
        scw=min(int(cw*border_scale), max(1, width//2))
        sch=min(int(ch*border_scale), max(1, height//2))
        mw=max(0, width-2*scw); mh=max(0, height-2*sch)
        def _sub(rx,ry,rw,rh): return sprite.subsurface(pygame.Rect(rx,ry,rw,rh))
        def _blit(surf,dx,dy,dw,dh): screen.blit(pygame.transform.scale(surf,(dw,dh)),(dx,dy))
        _blit(_sub(0,     0,     cw,      ch),      x,          y,           scw,sch)
        _blit(_sub(sw-cw, 0,     cw,      ch),      x+width-scw,y,           scw,sch)
        _blit(_sub(0,     sh-ch, cw,      ch),      x,          y+height-sch,scw,sch)
        _blit(_sub(sw-cw, sh-ch, cw,      ch),      x+width-scw,y+height-sch,scw,sch)
        _blit(_sub(cw,    0,     sw-2*cw, ch),      x+scw,      y,           mw, sch)
        _blit(_sub(cw,    sh-ch, sw-2*cw, ch),      x+scw,      y+height-sch,mw, sch)
        _blit(_sub(0,     ch,    cw,      sh-2*ch), x,          y+sch,       scw,mh)
        _blit(_sub(sw-cw, ch,    cw,      sh-2*ch), x+width-scw,y+sch,       scw,mh)
        _blit(_sub(cw,    ch,    sw-2*cw, sh-2*ch), x+scw,      y+sch,       mw, mh)
        return True

    def _draw_button_sprite(self, screen, sprite_normal, sprite_pressed, is_pressed, x, y, label):
        sprite=sprite_pressed if (is_pressed and sprite_pressed) else sprite_normal
        btn_h=max(2,int(self.canvas_height*0.06))
        if sprite:
            sf=btn_h/sprite.get_height(); scaled=pygame.transform.scale(sprite,(int(sprite.get_width()*sf),btn_h))
            screen.blit(scaled,(x,y))
            if label: self._render_text_with_shadow(screen,label,(x+scaled.get_width()+int(5*RENDER_SCALE),y+btn_h//2),anchor='midleft')
        else:
            r=btn_h//2; col=(140,140,140) if is_pressed else (180,180,180)
            pygame.draw.circle(screen,col,(x+r,y+r),r); pygame.draw.circle(screen,(100,100,100),(x+r,y+r),r,3)
            if label: self._render_text_with_shadow(screen,label,(x+btn_h+10,y+r),anchor='midleft')