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
from config.settings import RENDER_SCALE
from core.bitmap_font import BitmapFont

_S = max(1, RENDER_SCALE)

TABS = ['STATUS', 'INVENTORY', 'EQUIP', 'OPTIONS', 'JOURNAL']


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
        self.options_item_index = 0   # 0–4: SFX, Music, TextSpeed, Credits, Sleep
        self.options_editing    = False
        self.options_values     = [1, 1, 1]  # 0.0–1.0 per bar
        self.options_step       = 0.2         # ← how much left/right changes a bar

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
        self._char_sprite       = None
        self.char_name_sprites  = {}

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

        # Character name tag sprites (selected / unselected variant per character)
        self._name_sprites = {}
        for cid in ('goku', 'gohan', 'vegeta'):
            sel   = _img(f'assets/ui/textbox/names/{cid}_selected.png')
            unsel = _img(f'assets/ui/textbox/names/{cid}_unselected.png')
            if sel or unsel:
                self._name_sprites[cid] = {'selected': sel, 'unselected': unsel}

        # Inventory sub-tab sprites
        self._inv_tabs = [
            {'id': 'supplies',   'selected': _img('assets/ui/textbox/inventory/supplies_selected.png'),   'unselected': _img('assets/ui/textbox/inventory/supplies_unselected.png')},
            {'id': 'storyitems', 'selected': _img('assets/ui/textbox/inventory/storyitems_selected.png'), 'unselected': _img('assets/ui/textbox/inventory/storyitems_unselected.png')},
        ]
        self.inventory_tab_index = 0

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

        self.inv_scroll_offset    = 0
        self.inv_scroll_max       = 0
        self.scroll_up_timer      = 0.0
        self.scroll_down_timer    = 0.0
        self.scroll_press_duration = 0.15

        # Pre-render the tiled background as one big surface to avoid seams
        raw = _img('assets/ui/textbox/background_texture.png')
        if raw:
            scale  = round(_S * 1.5)
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

    def _load_char_sprite(self, char_id, costume='base'):
        if char_id in self._char_sprite_cache:
            return self._char_sprite_cache[char_id]
        surf = None
        try:
            surf = pygame.image.load(f'assets/portraits/{char_id}.png').convert_alpha()
        except Exception:
            pass
        self._char_sprite_cache[char_id] = surf
        return surf

    # ── Open / close ──────────────────────────────────────────────────────────

    def open(self, player):
        self.active              = True
        self.tab_index           = 0
        self.equip_slot_index    = 0
        self.options_item_index  = 0
        self.options_editing     = False
        self.inventory_tab_index = 0
        self.journal_tab_index   = 0
        self._journal_scroll     = 0
        self.inv_scroll_offset   = 0
        self.scroll_up_timer     = 0.0
        self.scroll_down_timer   = 0.0
        for btn in self.button_states:
            self.button_states[btn]       = False
            self.button_press_timers[btn] = 0.0
        char_id = getattr(player, 'character', 'goku')
        if char_id != self._current_char_id:
            self._current_char_id = char_id
            self._char_sprite     = self._load_char_sprite(char_id)

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

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self, dt):
        if not self.active:
            return
        for btn in self.button_press_timers:
            if self.button_press_timers[btn] > 0:
                self.button_press_timers[btn] -= dt
                if self.button_press_timers[btn] <= 0:
                    self.button_states[btn] = False
        self.scroll_up_timer   = max(0.0, self.scroll_up_timer   - dt)
        self.scroll_down_timer = max(0.0, self.scroll_down_timer - dt)

    # ── Input ─────────────────────────────────────────────────────────────────

    def handle_input(self, event):
        """
        Returns:
          'close'       — ESC / B
          'open_skills' — S or Down on Status tab
          None          — no action taken
        """
        if not self.active:
            return None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            return self._handle_mouse_click(event.pos)

        if event.type != pygame.KEYDOWN:
            return None

        key = event.key

        if key in (pygame.K_ESCAPE, pygame.K_x):
            self.button_states['b']       = True
            self.button_press_timers['b'] = self.button_press_duration
            self.close()
            return 'close'

        if key == pygame.K_LEFT and not self.options_editing:
            self.tab_index = 4 if self.tab_index == 0 else self.tab_index - 1
            self._press('l')
            self._play_switch_sfx()

        elif key == pygame.K_RIGHT and not self.options_editing:
            self.tab_index = 0 if self.tab_index == 4 else self.tab_index + 1
            self._press('r')
            self._play_switch_sfx()

        elif TABS[self.tab_index] == 'INVENTORY' and key == pygame.K_UP:
            if self.inv_scroll_offset > 0:
                self.inv_scroll_offset -= 1
                self.scroll_up_timer = self.scroll_press_duration

        elif TABS[self.tab_index] == 'INVENTORY' and key == pygame.K_DOWN:
            if self.inv_scroll_offset < self.inv_scroll_max:
                self.inv_scroll_offset += 1
                self.scroll_down_timer = self.scroll_press_duration

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

        elif self.tab_index == 2 and key == pygame.K_UP:
            self.equip_slot_index = max(0, self.equip_slot_index - 1)

        elif self.tab_index == 2 and key == pygame.K_DOWN:
            self.equip_slot_index = min(3, self.equip_slot_index + 1)

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
                elif key == pygame.K_UP:
                    self.options_item_index = 3 if self.options_item_index == 4 else max(0, self.options_item_index - 1)
                elif key == pygame.K_DOWN:
                    self.options_item_index = 4 if self.options_item_index == 3 else min(3, self.options_item_index + 1)
                elif key == pygame.K_RIGHT and self.options_item_index == 3:
                    self.options_item_index = 4
                elif key == pygame.K_LEFT and self.options_item_index == 4:
                    self.options_item_index = 3

        elif TABS[self.tab_index] == 'STATUS' and key in (pygame.K_s, pygame.K_DOWN):
            return 'open_skills'

        return None

    def _press(self, btn):
        self.button_states[btn]       = True
        self.button_press_timers[btn] = self.button_press_duration

    def _handle_mouse_click(self, pos):
        """Translates a left-click into the equivalent keyboard action using rects stored by draw()."""
        z = self._click_zones

        def _hit(key):
            r = z.get(key)
            return r is not None and r.collidepoint(pos)

        if _hit('b_cancel'):
            self._press('b')
            self.close()
            return 'close'

        if _hit('l_tab') and not self.options_editing:
            self.tab_index = 4 if self.tab_index == 0 else self.tab_index - 1
            self._press('l')
            self._play_switch_sfx()
            return None

        if _hit('r_tab') and not self.options_editing:
            self.tab_index = 0 if self.tab_index == 4 else self.tab_index + 1
            self._press('r')
            self._play_switch_sfx()
            return None

        if _hit('a_select') and self.tab_index in (1, 2, 3):
            if self.tab_index == 3 and self.options_item_index <= 2:
                self.options_editing = not self.options_editing
                self._press('a')
                self._play_select_sfx()
            return None

        if _hit('scroll_up'):
            if TABS[self.tab_index] == 'INVENTORY' and self.inv_scroll_offset > 0:
                self.inv_scroll_offset -= 1
                self.scroll_up_timer = self.scroll_press_duration
            elif self.tab_index == 4 and self._journal_scroll > 0:
                self._journal_scroll -= 1
                self.scroll_up_timer = self.scroll_press_duration
            return None

        if _hit('scroll_down'):
            if TABS[self.tab_index] == 'INVENTORY' and self.inv_scroll_offset < self.inv_scroll_max:
                self.inv_scroll_offset += 1
                self.scroll_down_timer = self.scroll_press_duration
            elif self.tab_index == 4 and self._journal_scroll < self.inv_scroll_max:
                self._journal_scroll += 1
                self.scroll_down_timer = self.scroll_press_duration
            return None

        if self.tab_index == 1:
            for i in range(len(self._inv_tabs)):
                if _hit(f'inv_tab_{i}'):
                    self.inventory_tab_index = i
                    self.inv_scroll_offset   = 0
                    return None

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
                    else:
                        self.options_editing    = False
                        self.options_item_index = i
                    return None

        if self.tab_index == 2:
            for i in range(4):
                if _hit(f'equip_slot_{i}'):
                    self.equip_slot_index = i
                    return None

        return None

    # ══════════════════════════════════════════════════════ draw ══════════════

    def draw(self, screen, player, play_time=0.0):
        if not self.active:
            return

        self._click_zones = {}  # rebuilt every frame so positions stay current

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
        elif self.tab_index == 2:
            self._draw_equip_page(screen, player, content_rect, self.equip_slot_index)
        elif self.tab_index == 3:
            self._draw_options_page(screen, content_rect, self.options_item_index,
                                    self.options_editing, self.options_values)
        elif self.tab_index == 4:
            self._draw_journal_page(screen, content_rect)

        # Character name tag sprites (bottom of frame)
        if self.char_name_sprites:
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

        # Scroll arrows (inventory + journal)
        if self.tab_index in (1, 4):
            scroll_off  = self._journal_scroll if self.tab_index == 4 else self.inv_scroll_offset
            can_up      = scroll_off > 0
            can_down    = scroll_off < self.inv_scroll_max
            arrow_scale = max(1, int(self.canvas_height * 0.06))
            scroll_x    = box_x + inner_w - pad - arrow_scale + 3
            scroll_top  = box_y + pad + 33

            up_surf = (self.arrow_up_pressed if self.scroll_up_timer > 0 else self.arrow_up) if can_up else self.arrow_up_grey
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

            dn_surf = (self.arrow_down_pressed if self.scroll_down_timer > 0 else self.arrow_down) if can_down else self.arrow_down_grey
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
        labels    = [('Journal','Status','Inventory'),('Status','Inventory','Equip'),
                     ('Inventory','Equip','Options'),('Equip','Options','Journal'),
                     ('Options','Journal','Status')][self.tab_index]
        left_label, centre_label, right_label = labels

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

        if self.tab_index in (1,2,3):
            sel_x = box_x + int(inner_w-961)
            self._draw_button_sprite(screen,self.button_a,self.button_a_pressed,self.button_states['a'],sel_x,button_y,'')
            if self.options_editing and self.button_a:
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
        else:
            cancel_x = box_x+int(inner_w-961)

        self._draw_button_sprite(screen,self.button_b,self.button_b_pressed,self.button_states['b'],cancel_x,button_y,'')
        _b=self.button_b_pressed if self.button_states['b'] else self.button_b
        _b_w=int(_b.get_width()*(_b_scale/_b.get_height())) if _b else _b_scale
        can_surfs=_menu_surfs('Cancel',color=(255,255,0))
        can_lx=cancel_x+_b_w+int(5*RENDER_SCALE-8); can_ly=button_y+(_b_scale-max(s.get_height() for s in can_surfs))//2+2
        _blit_menu(can_surfs,can_lx,can_ly,spacing=6)
        self._click_zones['b_cancel']=pygame.Rect(cancel_x,button_y-4,_b_w+_menu_w(can_surfs,6)+int(5*RENDER_SCALE-8)+8,_b_scale+8)

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

    def _draw_status_page(self, screen, player, play_time, rect):
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
        sprite_col_w = w // 3
        stats_x = x + sprite_col_w - 45
        stats_w = w - sprite_col_w - 6
        char_id = getattr(player, 'character', 'unknown')
        level   = getattr(player, 'level', 1)

        def _tint(s, c): t=s.copy(); t.fill(c,special_flags=pygame.BLEND_RGBA_MULT); return t
        def _shadow(s):  t=s.copy(); t.fill((0,0,0),special_flags=pygame.BLEND_RGBA_MULT); return t

        name_surf = _tint(self.bold_font.render(char_id.upper()), (255,255,0))
        lvl_surf  = _tint(self.bold_font.render('LVL'),           (255,0,0))
        num_surf  = _tint(self.bold_numbers_font.render(str(level)), (255,0,0))

        if self._name_sprites:
            tag_h=max(16,int(h*0.08)); gap=max(2,int(self.screen_width*0.003)); tx,ty=x-6,y-3
            for cid,surfs in self._name_sprites.items():
                surf=surfs['selected'] if cid==char_id else surfs['unselected']
                if surf:
                    s=max(1,round(tag_h/surf.get_height()))
                    scaled=pygame.transform.scale(surf,(surf.get_width()*s,surf.get_height()*s))
                    screen.blit(scaled,(tx,ty)); tx+=scaled.get_width()+gap

        name_y=y+49; gap_px=max(5,int(self.screen_width*0.005)); cx=x-6
        for surf in (name_surf,lvl_surf,num_surf):
            screen.blit(_shadow(surf),(cx+4,name_y)); screen.blit(surf,(cx,name_y)); cx+=surf.get_width()+gap_px*3

        name_h=max(name_surf.get_height(),lvl_surf.get_height(),num_surf.get_height())
        content_top=y+name_h+7
        sprite_cx=x+sprite_col_w//2-30; sprite_cy=content_top+(h-(content_top-y))//2-29

        if self._char_sprite:
            avail_w=sprite_col_w-8; avail_h=h-(content_top-y)-8
            int_scale=max(1,int(min(avail_w/self._char_sprite.get_width(),avail_h/self._char_sprite.get_height())))
            portrait=pygame.transform.scale(self._char_sprite,(self._char_sprite.get_width()*int_scale,self._char_sprite.get_height()*int_scale))
            screen.blit(portrait,portrait.get_rect(center=(sprite_cx,sprite_cy)))
        else:
            r=sprite_col_w//3
            pygame.draw.circle(screen,(80,80,160),(sprite_cx,sprite_cy),r)
            pygame.draw.circle(screen,(200,200,255),(sprite_cx,sprite_cy),r,2)

        bar_h=max(4,int(self.screen_height*0.008)); lh=max(20,int(self.stats_font.get_line_height()+20))
        cy=content_top+int(h-453); stats=getattr(player,'stats',{}); _label_gap=max(3,int(self.screen_width*0.004))

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
            bar_tot=lh*4; tip_scale=max(1,round(bar_tot/so_h)); tip_px=so_h//4
            tip_h=tip_px*tip_scale; sb_w=so_w*tip_scale; mid_h=max(1,bar_tot-tip_h*2)
            top_s=pygame.transform.scale(sb.subsurface((0,0,so_w,tip_px)),(sb_w,tip_h))
            mid_s=pygame.transform.scale(sb.subsurface((0,tip_px,so_w,so_h-tip_px*2)),(sb_w,mid_h))
            bot_s=pygame.transform.scale(sb.subsurface((0,so_h-tip_px,so_w,tip_px)),(sb_w,tip_h))
            sb_x=right_col_x-sb_w//2-4 if self.tab_index==0 else right_col_x-sb_w//2+80
            sb_y=cy_start+40
            screen.blit(top_s,(sb_x,sb_y)); screen.blit(mid_s,(sb_x,sb_y+tip_h)); screen.blit(bot_s,(sb_x,sb_y+tip_h+mid_h))

        def draw_right_stat(label,val,row):
            ry=cy_start+48+row*lh
            screen.blit(_yellow(self.stats_font.render(f'{label}:')),(right_col_x+32,ry))
            screen.blit(_grey(self.stats_numbers_font.render(str(val))),(right_col_x+270,ry))

        hp,max_hp=getattr(player,'hp',0),getattr(player,'max_hp',1)
        ki,max_ki=int(getattr(player,'ki',0)),int(getattr(player,'max_ki',1))
        exp=getattr(player,'exp',0); exp_nxt=getattr(player,'exp_to_next_level',0)
        zenie=getattr(player,'zenie',0); t=int(play_time)
        hh,rem=divmod(t,3600); mm,ss=divmod(rem,60)

        draw_row('HP:',f'{hp}/{max_hp}'); draw_row('EP:',f'{ki}/{max_ki}')
        draw_row('XP:',str(exp)); draw_row('NXT LVL:',str(exp_nxt))
        draw_row('ZENIE:',str(zenie)); draw_row('TIME:',f'{hh:02d}:{mm:02d}:{ss:02d}')
        draw_div()
        for row,(lbl,val) in enumerate([('STR',stats.get('strength',stats.get('str',0))),
                                         ('POW',stats.get('ki_power',stats.get('pow',0))),
                                         ('END',stats.get('vitality',stats.get('end',0))),
                                         ('SPD',stats.get('speed',   stats.get('spd',0)))]):
            draw_right_stat(lbl,val,row)

    # ── Equip page ────────────────────────────────────────────────────────────

    def _draw_equip_page(self, screen, player, rect, hovered_slot=0):
        if not self.spacing_bar:
            return
        x,y,w,h=rect.x,rect.y,rect.width,rect.height; stats=getattr(player,'stats',{})
        lh=max(20,int(self.stats_font.get_line_height()+20))
        def _yellow(s): c=s.copy(); c.fill((255,255,0),  special_flags=pygame.BLEND_RGBA_MULT); return c
        def _grey(s):   c=s.copy(); c.fill((180,180,180),special_flags=pygame.BLEND_RGBA_MULT); return c

        slot_sprites=[self.equip_body,self.equip_hands,self.equip_feet,self.equip_accessories]
        slot_scale=max(1,round(self.canvas_height*0.07/slot_sprites[0].get_height())) if slot_sprites[0] else 1
        slot_x=x+int(w*0.05)-52; slot_y=y+int(h*0.05)-44
        slot_gap=max(4,int(self.canvas_height*0.01)); slot_txt_gap=max(4,int(w*0.01))
        slot_offsets={0:(0,0),1:(0,0),2:(5,0),3:(0,4)}
        slot_names=['Body','Hands','Feet','Accessories']; equipped=[None,None,None,None]
        max_sprite_w=max((s.get_width()*slot_scale for s in slot_sprites if s),default=0)
        text_x_fixed=slot_x+max_sprite_w+slot_txt_gap

        for i,surf in enumerate(slot_sprites):
            if not surf: continue
            scaled=pygame.transform.scale(surf,(surf.get_width()*slot_scale,surf.get_height()*slot_scale))
            ox,oy=slot_offsets.get(i,(0,0)); bx_=slot_x+ox; by_=slot_y+oy
            screen.blit(scaled,(bx_,by_))
            if i==hovered_slot and self.equip_arrow:
                arr_sc=max(1,int(self.canvas_height*0.05/self.equip_arrow.get_height()))
                arr_surf=pygame.transform.scale(self.equip_arrow,(self.equip_arrow.get_width()*arr_sc,self.equip_arrow.get_height()*arr_sc))
                screen.blit(arr_surf,(bx_-arr_surf.get_width()-max(2,int(self.canvas_width*0.005))+16,by_+(scaled.get_height()-arr_surf.get_height())//2))
            item_name=equipped[i] if equipped[i] else '-None-'; label=f'{item_name} ({slot_names[i]})'
            txt_x=text_x_fixed; txt_y=by_+(scaled.get_height()-self.menu_uppercase_font.get_line_height())//2+2
            _ls=6; _ng=4; _char_offs={}
            if slot_names[i]=='Body' and 'y' in label: _char_offs[label.index('y')]=8
            cx_=txt_x; max_h_=max((self.menu_uppercase_font if ch.isupper() or not ch.isalpha() else self.menu_lowercase_font).get_line_height() for ch in label)
            for j,ch in enumerate(label):
                if j==len(item_name)+1: cx_+=_ng
                font=self.menu_uppercase_font if ch.isupper() or not ch.isalpha() else self.menu_lowercase_font
                s=font.render(ch).copy(); s.fill((255,255,255),special_flags=pygame.BLEND_RGBA_MULT)
                shadow=s.copy(); shadow.fill((0,0,0),special_flags=pygame.BLEND_RGBA_MULT)
                oy_=max_h_-s.get_height(); eoy=_char_offs.get(j,0)
                screen.blit(shadow,(cx_+1,txt_y+oy_+eoy+1)); screen.blit(s,(cx_,txt_y+oy_+eoy)); cx_+=s.get_width()+_ls
            self._click_zones[f'equip_slot_{i}']=pygame.Rect(bx_-10,by_-4,max_sprite_w+400,scaled.get_height()+slot_gap+4)
            slot_y+=scaled.get_height()+slot_gap

        sb=self.spacing_bar; so_h=sb.get_height(); so_w=sb.get_width()
        bar_tot=lh*4; tip_scale=max(1,round(bar_tot/so_h)); tip_px=so_h//4
        tip_h=tip_px*tip_scale; vert_w=so_w*tip_scale; mid_h=max(1,bar_tot-tip_h*2)
        top_s=pygame.transform.scale(sb.subsurface((0,0,so_w,tip_px)),(vert_w,tip_h))
        mid_s=pygame.transform.scale(sb.subsurface((0,tip_px,so_w,so_h-tip_px*2)),(vert_w,mid_h+20))
        bot_s=pygame.transform.scale(sb.subsurface((0,so_h-tip_px,so_w,tip_px)),(vert_w,tip_h))
        vert_x=x+int(w*0.35)+335; vert_y=y+int(h*0.1)-54
        screen.blit(top_s,(vert_x,vert_y)); screen.blit(mid_s,(vert_x,vert_y+tip_h)); screen.blit(bot_s,(vert_x,vert_y+tip_h+20+mid_h))

        horiz_src=pygame.transform.rotate(sb,90); hs_w=horiz_src.get_width(); hs_h=horiz_src.get_height()
        htip_px=hs_w//4; htip_w=htip_px*tip_scale; horiz_h=hs_h*tip_scale; horiz_w=lh*4
        hl=pygame.transform.scale(horiz_src.subsurface((0,0,htip_px,hs_h)),(htip_w,horiz_h))
        hm=pygame.transform.scale(horiz_src.subsurface((htip_px,0,hs_w-htip_px*2,hs_h)),(max(1,horiz_w-htip_w*2+720),horiz_h))
        hr=pygame.transform.scale(horiz_src.subsurface((hs_w-htip_px,0,htip_px,hs_h)),(htip_w,horiz_h))
        horiz_x=vert_x-(horiz_w-vert_w)//2-570; horiz_y=vert_y-horiz_h+252
        screen.blit(hl,(horiz_x,horiz_y)); screen.blit(hm,(horiz_x+htip_w,horiz_y)); screen.blit(hr,(horiz_x+720+horiz_w-htip_w,horiz_y))

        stat_x=vert_x+vert_w+max(8,int(w*0.02))
        for i,(lbl,val) in enumerate([('STR',stats.get('strength',stats.get('str',0))),('POW',stats.get('ki_power',stats.get('pow',0))),
                                       ('END',stats.get('vitality',stats.get('end',0))),('SPD',stats.get('speed',stats.get('spd',0)))]):
            ry=vert_y+i*lh+20
            screen.blit(_yellow(self.stats_font.render(f'{lbl}:')),(stat_x,ry))
            screen.blit(_grey(self.stats_numbers_font.render(str(val))),(stat_x+70+max(60,int(w*0.08)),ry))

    # ── Journal page ──────────────────────────────────────────────────────────

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

    def _blit_journal_text(self, screen, text, x, y, color=(255,255,255), max_w=9999):
        upper_h=self.menu_uppercase_font.get_line_height(); lh=upper_h; cx=x; line_y=y
        _desc={'p':8,'q':8,'g':8,'y':8}
        for word in text.split(' '):
            word_w=0
            for ch in word:
                font=self.menu_uppercase_font if (ch.isupper() or not ch.isalpha()) else self.menu_lowercase_font
                g=font.glyphs.get(ch.upper())
                if g: word_w+=g.get_width()+font.letter_spacing
            word_w=max(0,word_w-(font.letter_spacing if word else 0))
            if cx>x and cx+word_w>x+max_w: cx=x; line_y+=lh+2
            for ch in word:
                font=self.menu_uppercase_font if (ch.isupper() or not ch.isalpha()) else self.menu_lowercase_font
                g=font.glyphs.get(ch.upper())
                if g:
                    oy=upper_h-g.get_height()+_desc.get(ch,0)
                    tinted=g.copy(); tinted.fill(color,special_flags=pygame.BLEND_RGBA_MULT)
                    screen.blit(tinted,(cx,line_y+oy)); cx+=g.get_width()+font.letter_spacing
                elif ch==' ': cx+=int(8*_S)
            cx+=int(8*_S)

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
        screen.blit(self.bg_texture,(rect.left-ox,rect.top-oy)); screen.set_clip(prev)

    def _draw_9slice_sprite(self, screen, sprite, x, y, width, height, corner_size=16):
        if not sprite: return False
        sw,sh=sprite.get_width(),sprite.get_height(); border_scale=4
        cw=min(corner_size,sw//3); ch=min(corner_size,sh//3)
        scw=int(cw*border_scale); sch=int(ch*border_scale)
        mw=width-2*scw; mh=height-2*sch
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