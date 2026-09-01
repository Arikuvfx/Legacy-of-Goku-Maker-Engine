"""
Title Screen
------------
First thing the player sees on boot — replaces the old "drop straight into
the default dev room" behavior. Game.__init__ opens this in 'title' mode;
Game.handle_events()/update()/draw() all early-return to this widget while
that mode is active (see game.py's game_mode gating), so nothing else in
the engine runs until the player actually starts a game.

Two phases, driven by self._phase:

  'intro' — the boot picture sequence (see _build_intro_phases' docstring
            for the exact fade/crossfade timing). Any key/click skips
            straight to the menu. Fully optional and data-driven — with
            no images configured, this phase is skipped entirely.

  'menu'  — the actual title menu. Has its own sub-pages:
              'main'        START / OPTIONS
              'mode_select' SINGLE PLAYER / MULTIPLAYER (reached via START,
                             same box, same position)
            'save_select' also doubles as a character picker for slots
                             with more than one unlocked character:
                             LEFT/RIGHT (or A/D) cycles which one will
                             actually be loaded (see
                             _cycle_picked_character/get_selected_
                             character), shown by overlaying
                             assets/ui/title/select.png on that entry and
                             playing its down-facing walk cycle in
                             _draw_slot_character_row (show_picker=True)
                             instead of a static idle frame.
            OPTIONS doesn't draw anything itself — it opens the shared
            PauseMenu's Options tab in "restricted" mode (see
            PauseMenu.open_options_only), so SFX/Music/Text Speed bars,
            Credits, and Sleep all behave exactly as they do from the
            in-game pause menu, just without L/R (nothing else to page to
            pre-game) or a Player. See set_pause_menu() below — Game wires
            this up once, right after constructing both menus.

Content (title text, intro pictures, main menu background/music, and which
room/cutscene "New Game" leads to) is data-driven from data/game_flow.json
— same philosophy as credits.json. This module doesn't need to change to
reskin any of that; see game_flow.json's own comments for the format:

  {
    "title_text": "GAME TITLE",                 // optional, "" to hide
    "intro_cutscene": "...",                     // unchanged, used by
                                                  // Game._start_new_game()
    "main_menu": {
      "background": "assets/ui/title/main_bg.png",
      "music": "title_theme",                    // stem, same as room_music
      "intro_images": [
        "assets/ui/title/boot_1.png",
        "assets/ui/title/boot_2.png",
        "assets/ui/title/boot_3.png",
        "assets/ui/title/boot_4.png"
      ]
    }
  }

Same calling convention as every other menu in this project (see
CreditsScreen, PauseMenu): handle_input() returns a short string
('quit') or None for anything that should happen immediately.

'new_game' is the one exception: confirming it on the save-select page
doesn't return a signal from handle_input() at all. Instead it starts a
fade-to-black (see _confirm_save_slot/_exit_pending), and update()
ramps that fade closed each frame. Only once it's fully black does
consume_exit_signal() — polled by game.py once per frame alongside
update() — hand back 'new_game'. game.py is what turns that into an
actual scene change (see Game._start_new_game()), so the cut to the
intro cutscene never happens until the menu has completely faded out.
"""

import json
import os
import pygame

# Fixed in place of config.settings.RENDER_SCALE — this screen's own sizing
# is cosmetic UI scale, not a world/camera transform, so it's pinned here at
# the engine's current default (4) instead of tracking that setting.
# Changing RENDER_SCALE elsewhere no longer resizes anything in this file.
RENDER_SCALE = 4
from ui.pause_menu import FlatBitmapFont

DEFAULT_PATH = os.path.join('data', 'game_flow.json')
DEFAULT_TITLE_TEXT = 'GAME TITLE'

# Main-menu pages, each a list of (result_key, label) pairs. Both pages
# render through the exact same box/arrow/font — see _draw_option_box.
_MAIN_OPTIONS = [('start', 'START'), ('options', 'OPTIONS')]
_MODE_OPTIONS = [('single', 'SINGLE PLAYER'), ('multi', 'MULTIPLAYER')]

# All labels across both pages, used only to size the option box so it's
# identical for the START/OPTIONS page and the SINGLE PLAYER/MULTIPLAYER
# page, instead of each page shrinking/growing the box to fit its own
# widest label.
_ALL_OPTION_LABELS  = [label for _, label in _MAIN_OPTIONS + _MODE_OPTIONS]
_MAIN_OPTION_LABELS = [label for _, label in _MAIN_OPTIONS]

# SAVE SELECT slot list — placeholder labels until real save-file data
# exists (see _confirm_save_slot). _SAVE_VIEWPORT_SIZE is how many rows
# are visible in the frame at once; UP/DOWN slide _save_scroll_offset to
# keep the selected slot inside that window — see
# _handle_save_select_keydown for the exact rule.
_SAVE_SLOT_LABELS   = ['Game 1', 'Game 2', 'Game 3']
_SAVE_VIEWPORT_SIZE = 2

# L/R shoulder buttons on the SAVE SELECT page toggle it between "Select
# Game" (pick a slot to play) and "Delete Game" (pick a slot to erase) —
# see _handle_save_select_keydown/_draw_save_select_title. Bound to the
# same Q/E keys as everywhere else L/R shows up in this project's WASD +
# Z/X + Q/E scheme; update these if PauseMenu's own L/R binding differs.
_KEY_L = (pygame.K_q,)
_KEY_R = (pygame.K_e,)

# ── Intro timing (seconds) — tune freely, nothing else depends on these ────
_INTRO_HOLD        = 1.4   # each picture's fully-visible hold time
_INTRO_FADE_BLACK  = 0.6   # pic 1's fade-to-black, and the last pic's fade-to-black
_INTRO_CROSSFADE   = 0.6   # pic-over-pic crossfades (no black in between)
_INTRO_BLACK_PAUSE = 0.25  # brief hold on black before the menu fades in
_MENU_FADE_IN      = 0.6   # the main menu's own fade-in from black
_MENU_FADE_OUT     = 0.4   # save-select's fade-to-black after confirming
                            # New Game, before game.py switches scenes

# How long the "can't pick that yet" flash lasts when MULTIPLAYER is
# selected — see _confirm_menu_selection. Purely a placeholder until
# multiplayer actually exists; change/remove once it does.
_DENIED_FLASH_TIME = 0.4

_BOX_Y_RATIO = 0.68   # main-menu option box's top edge, as a fraction of screen height
_ARROW_BLINK_INTERVAL = 0.2   # seconds visible, then seconds hidden — matches PauseMenu's cursor arrow


class TitleScreen:

    def __init__(self, screen_width, screen_height, path=DEFAULT_PATH):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.path          = path
        self.active         = False

        self.title_text      = DEFAULT_TITLE_TEXT
        self._main_menu_data = {}
        self._load(path)

        self._t = 0.0   # accumulated time since open(), currently unused by
                         # drawing but kept for any future timing effects

        self.title_color       = (255, 255, 0)
        self.text_color        = (255, 255, 255)
        self.text_hover_color  = (0, 255, 0)
        # Selected row's color while SAVE SELECT is in "Delete Game" mode
        # (see self._save_delete_mode) — pure red, distinct from the
        # softer red used by the MULTIPLAYER "can't pick that yet" flash.
        self.text_hover_delete_color = (255, 0, 0)
        self.text_shadow_color = (0, 0, 0)
        self.shadow_offset     = (2, 2)
        self.bg_color          = (0, 0, 0)

        # Wired in by Game after construction — see set_pause_menu/
        # set_sound_engine/set_sound_manager below. All optional: with none
        # of them set, the menu still works, just silently and without an
        # OPTIONS screen (falls back to doing nothing on OPTIONS confirm).
        self._pause_menu    = None
        self._sound_engine  = None
        self._sound_manager = None
        self._save_data_provider = None   # set via set_save_data_provider; see that method's docstring

        # Fonts — same folders/scale as CreditsScreen/PauseMenu so the title
        # screen matches the rest of the UI's look exactly.
        font_scale = 8
        menu_font_scale = 7
        self.menu_font_scale = menu_font_scale
        self.title_font = FlatBitmapFont('assets/ui/fonts/uppercase', letter_spacing=max(6, int(10 / RENDER_SCALE)), scale=font_scale)
        self.role_font  = FlatBitmapFont('assets/ui/fonts/scouter_stats', letter_spacing=max(6, int(10 / RENDER_SCALE)), scale=menu_font_scale)

        # Smaller than role_font — used only by the SAVE SELECT slot list
        # (see _draw_save_slot_list), which sits inside a tighter frame
        # than the main START/OPTIONS box.
        self.save_slot_font_scale = max(3, menu_font_scale - 3)
        self.save_slot_font = FlatBitmapFont('assets/ui/fonts/scouter_stats', letter_spacing=max(4, int(8 / RENDER_SCALE)), scale=self.save_slot_font_scale)

        # scouter_stats has no 0.png..9.png of its own, so the "1"/"2"/"3"
        # in "Game 1"/"Game 2"/"Game 3" would otherwise be silently skipped
        # by FlatBitmapFont.render() (unknown chars contribute no
        # width/glyph at all). Borrow the digits from the dmg_font sprite
        # sheet instead — same "borrow a glyph from another font folder"
        # trick PauseMenu uses for the stats "/" character. render()
        # already bottom-aligns every glyph against the tallest one in the
        # string, so once the digit exists in .glyphs it lines up with
        # "GAME" on its own — nothing about the existing letters changes.
        self._load_save_slot_digit_glyphs()

        self._load_main_menu_assets()

        self._option_hit_rects    = []   # populated by _draw_option_box, read by handle_input
        self._save_slot_hit_rects = []   # populated by _draw_save_slot_list, read by handle_input
        self._save_lr_hit_rects   = {}   # populated by _draw_save_select, read by handle_input — {'l': Rect, 'r': Rect}

        # A/B/L/R "pressed" sprite flash on the SAVE SELECT page — same
        # shape as PauseMenu's own button_states/button_press_timers (see
        # PauseMenu._press): a button flips to its pressed sprite for
        # button_press_duration seconds after being triggered, then falls
        # back to normal in update(). Kept as TitleScreen's own dict
        # rather than reusing PauseMenu's, since PauseMenu is only
        # borrowed here for its sprites/fonts (via set_pause_menu) and its
        # own button_states already track its OPTIONS-tab input, which
        # runs independently whenever self._options_open is True.
        self.button_states         = {'a': False, 'b': False, 'l': False, 'r': False}
        self.button_press_timers   = {'a': 0.0,   'b': 0.0,   'l': 0.0,   'r': 0.0}
        self.button_press_duration = 0.1

        # Scroll-arrow "pressed" flash on the SAVE SELECT list — same
        # shape as PauseMenu's own scroll_up_timer/scroll_down_timer:
        # a successful UP/DOWN fires the relevant timer, and
        # _draw_save_select_scrollbar shows arrow_up_pressed/
        # arrow_down_pressed for as long as it's running, same
        # scroll_press_duration PauseMenu uses everywhere else.
        self.scroll_up_timer       = 0.0
        self.scroll_down_timer     = 0.0
        self.scroll_press_duration = 0.15

        # In-game "Save Game" overlay (see open_save_overlay/
        # close_save_overlay below) — the save-pad flow (Game._start_save_flow)
        # borrows this exact SAVE SELECT frame instead of duplicating the
        # frame/font/scroll code, just re-labelled and locked down (no L/R,
        # no scrolling, a solid rather than blinking selector arrow — see
        # every self._save_overlay_active check below). self.active gets
        # flipped on/off around it the same way it's flipped on/off around
        # the title menu itself.
        self._save_overlay_active     = False
        self._save_overlay_characters = {}
        # Which character_id (and its current costume/transformation) is
        # the one actually being played right now, set by
        # open_save_overlay — lets _draw_slot_character_row show a live
        # idle-down sprite for that one entry instead of its static
        # icon.png (see _get_character_idle_icon).
        self._save_overlay_current_character = None
        self._save_overlay_current_costume   = 'base'
        self._character_icon_cache      = {}
        self._character_idle_icon_cache = {}
        # Down-facing walk-cycle frames, keyed the same way as
        # _character_idle_icon_cache — used only by _draw_slot_character_row's
        # show_picker branch (the real, non-overlay SAVE SELECT list) to
        # animate whichever character is currently picked. See
        # _get_character_walk_down_frames.
        self._character_walk_frame_cache = {}

        # select.png overlay for the SAVE SELECT character row (see
        # _draw_slot_character_row's show_picker branch) — drawn on top of
        # whichever character the player has picked to load as. Loaded once
        # here; None if the asset is missing, in which case the row just
        # skips drawing it (the walk animation still plays).
        self._select_overlay_icon = self._load_select_overlay()

        # Ground-shadow sprites for the SAVE SELECT character row (see
        # _draw_character_row_shadow) — the exact same assets/scaling
        # logic as the real in-game shadow (LayerManager._load_shadow/
        # _get_scaled_shadow in core/draw_layers.py), just reimplemented
        # here since that one needs a camera/world position this
        # screen-space UI row doesn't have.
        self._row_shadow_sprite, self._row_shadow_sprite_big = self._load_row_shadow_sprites()
        self._row_shadow_cache = {}   # (sprite_width, big) -> scaled Surface, mirrors LayerManager's own cache

        # Which character_id the player has picked to load as, per save
        # slot — {slot_index: character_id}. Populated lazily (see
        # _picked_character_for_slot) the first time a slot with save data
        # is drawn/navigated, defaulting to that save's current_character.
        # Reset each time the title screen is (re)opened — see open().
        self._save_char_picks = {}

        # Room name / play time shown on the in-game "Save Game" screen
        # (see _draw_save_overlay_info), set by open_save_overlay same as
        # the character-row fields above. Saga has no way to be set
        # anywhere in the game yet, so it has no matching field here —
        # its line always shows _SAVE_OVERLAY_SAGA_PLACEHOLDER instead.
        self._save_overlay_room_name = ''
        self._save_overlay_play_time = 0.0

        # Raw (unscaled) glyphs from assets/ui/fonts/numbers, used for
        # numeric values on the "Save Game" screen (currently just Play
        # Time) — see _get_number_glyph/_render_number_text. Cached per
        # character, same "load once, cache forever" approach as
        # _character_icon_cache above.
        self._numbers_glyph_cache = {}

        # Last-drawn SAVE SELECT / "Save Game" frame rect, in screen space —
        # set at the top of _draw_save_select each frame. Lets other UI
        # (e.g. Game._draw_saving_popup) center itself against the actual
        # bordered box instead of guessing/duplicating its geometry.
        self._save_select_frame_rect  = None

        # Rect Game._draw_saving_popup's "Saving..." box is about to be
        # drawn at, handed in each frame via set_save_popup_occlusion_rect
        # right before Game calls draw() — lets _draw_save_slot_divider
        # clip its bar around exactly where the popup is about to land
        # instead of drawing straight through it. None outside the
        # save-pad flow (see close_save_overlay), so the normal
        # title-menu SAVE SELECT list is never affected.
        self._save_popup_occlusion_rect = None

    # ── Data loading ─────────────────────────────────────────────────────────

    def _load(self, path):
        """Pulls title_text/main_menu out of data/game_flow.json if present.
        Missing file / missing key / bad JSON all just fall back to
        defaults rather than crashing — this file is meant to be
        hand-edited, same reasoning as CreditsScreen._load."""
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            self.title_text      = data.get('title_text', DEFAULT_TITLE_TEXT)
            self._main_menu_data = data.get('main_menu', {}) or {}
        except Exception as e:
            print(f'[TitleScreen] failed to parse {path}: {e}')

    def reload(self):
        """Re-reads game_flow.json and reloads main-menu images/background
        without recreating the whole widget — handy for previewing edits
        without relaunching. Safe to call while the intro/menu is active;
        takes effect from the next open()."""
        self._load(self.path)
        self._load_main_menu_assets()

    def _load_save_slot_digit_glyphs(self):
        """Borrows 0.png..9.png from the dmg_font sprite sheet into
        save_slot_font.glyphs so digits in "Game 1"/"Game 2"/"Game 3" have
        something to render (scouter_stats itself has no digit sprites).

        dmg_font's raw PNGs are baked on a solid black canvas rather than
        true transparency, so loading them as-is would draw a black block
        behind every number. _strip_black_to_transparent turns any
        near-black pixel into alpha=0 first; only then do we scale (nearest-
        neighbor, same as everywhere else pixel art is scaled in this
        project) to match save_slot_font_scale so the digit sits at the
        same size as the surrounding letters.

        Only touches save_slot_font — role_font/menu fonts never render
        digits, so they're left exactly as they were.
        """
        digit_dir = os.path.join('assets', 'ui', 'fonts', 'dmg_font')
        if not os.path.exists(digit_dir):
            return

        # dmg_font's digit PNGs aren't the same native resolution as
        # scouter_stats' letters, so scaling them by save_slot_font_scale
        # (like the letters were scaled) does NOT give them a matching
        # height — it made the digits taller than the letters, which
        # inflated line_h in _draw_save_slot_list (row height is
        # `max(s.get_height() for s in surfs)`) and pushed every row/
        # divider below it downward. Target the ALREADY-LOADED letters'
        # height instead, so a digit glyph can never exceed it.
        if not self.save_slot_font.glyphs:
            return
        target_h = max(g.get_height() for g in self.save_slot_font.glyphs.values())

        for digit in '0123456789':
            path = os.path.join(digit_dir, f'{digit}.png')
            if not os.path.exists(path):
                continue
            try:
                glyph = self._strip_black_to_transparent(path)

                # dmg_font's canvas has extra padding baked in around the
                # digit (room for the outline effect damage_number.py
                # draws around it). Matching raw canvas height to
                # target_h still leaves the actual ink sitting smaller/
                # off-baseline than the letters, since render() bottom-
                # aligns by CANVAS height, not by where the ink is. Crop
                # to the digit's real bounding box first so the canvas
                # *is* the ink — then scaling to target_h and bottom-
                # aligning lines it up with "GAME" the same way any
                # tightly-cropped letter glyph would.
                bbox = glyph.get_bounding_rect()
                if bbox.width == 0 or bbox.height == 0:
                    continue
                glyph = glyph.subsurface(bbox).copy()

                raw_w, raw_h = glyph.get_size()
                scale_factor = target_h / raw_h
                new_w = max(1, round(raw_w * scale_factor))
                glyph = pygame.transform.scale(glyph, (new_w, target_h))
                self.save_slot_font.glyphs[digit] = glyph
            except Exception as e:
                print(f'[TitleScreen] could not load save-slot digit glyph {path}: {e}')

    @staticmethod
    def _strip_black_to_transparent(path, black_threshold=24):
        """Loads a sprite and makes any near-black pixel fully transparent.

        dmg_font's digit PNGs are drawn on a plain black background (no
        real alpha channel), so a straight convert_alpha() load leaves a
        black square around every number. Any pixel whose R/G/B are all
        <= black_threshold is treated as background and gets alpha 0;
        everything else (the actual digit pixels) is left untouched, so
        this is safe even if a future digit sprite gets a dark outline —
        only true near-black is stripped, not just any dark shade.
        """
        surf = pygame.image.load(path).convert_alpha()
        w, h = surf.get_size()
        for y in range(h):
            for x in range(w):
                r, g, b, a = surf.get_at((x, y))
                if r <= black_threshold and g <= black_threshold and b <= black_threshold:
                    surf.set_at((x, y), (r, g, b, 0))
        return surf

    def _load_main_menu_assets(self):
        data = self._main_menu_data or {}
        self._music_track = data.get('music', '')

        self._menu_bg = None
        bg_path = data.get('background', '')
        if bg_path:
            if os.path.exists(bg_path):
                try:
                    img = pygame.image.load(bg_path).convert()
                    self._menu_bg = pygame.transform.scale(img, (self.screen_width, self.screen_height))
                except Exception as e:
                    print(f'[TitleScreen] could not load main menu background {bg_path}: {e}')
            else:
                print(f'[TitleScreen] main menu background not found: {bg_path}')

        self._intro_images = []
        for p in data.get('intro_images', []) or []:
            if not os.path.exists(p):
                print(f'[TitleScreen] intro image not found, skipping: {p}')
                continue
            try:
                img = pygame.image.load(p).convert_alpha()
                self._intro_images.append(_fit_contain(img, self.screen_width, self.screen_height))
            except Exception as e:
                print(f'[TitleScreen] could not load intro image {p}: {e}')

        self._intro_phases = self._build_intro_phases(self._intro_images)

    def _load_select_overlay(self):
        """assets/ui/title/select.png — the highlight drawn over whichever
        character the player has picked to load as on the SAVE SELECT
        list (see _draw_slot_character_row's show_picker branch). Missing
        file is non-fatal: the row just draws without it."""
        path = os.path.join('assets', 'ui', 'title', 'select.png')
        if not os.path.exists(path):
            print(f'[TitleScreen] select overlay not found: {path}')
            return None
        try:
            return pygame.image.load(path).convert_alpha()
        except Exception as e:
            print(f'[TitleScreen] could not load select overlay {path}: {e}')
            return None

    def _load_row_shadow_sprites(self):
        """Loads assets/sprites/universal/shadow.png and shadowbig.png —
        the exact same files, loaded the exact same way (including the
        drawn-ellipse fallback if a file's missing), as LayerManager.
        _load_shadow in core/draw_layers.py. Returns (small, big)."""
        try:
            small = pygame.image.load('assets/sprites/universal/shadow.png').convert_alpha()
        except Exception:
            small = pygame.Surface((32, 12), pygame.SRCALPHA)
            pygame.draw.ellipse(small, (0, 0, 0, 80), small.get_rect())

        try:
            big = pygame.image.load('assets/sprites/universal/shadowbig.png').convert_alpha()
        except Exception:
            big = pygame.Surface((64, 20), pygame.SRCALPHA)
            pygame.draw.ellipse(big, (0, 0, 0, 80), big.get_rect())

        return small, big

    def _build_intro_phases(self, images):
        """
        Builds the boot sequence as a flat list of phase dicts, each either:
          {'type': 'hold', 'img': idx_or_None, 'duration': secs}
          {'type': 'fade', 'frm': idx_or_None, 'to': idx_or_None,
           'duration': secs, 'start_music': bool}
        (idx_or_None of None means "black" for that side of the fade.)

        Reproduces the intended boot sequence:
          pic[0]      shown, then fades to black
          pic[1]      fades in FROM black — music starts the instant this
                      phase begins
          pic[2..n-1] each crossfades directly over the previous picture,
                      no black in between
          pic[-1]     fades to black once its hold ends
        Then a brief hold on black before the menu itself fades in
        (see update()/_enter_intro_phase()).

        Degrades gracefully: zero images returns [] (TitleScreen.open()
        then skips straight to the menu); exactly one image fades in from
        black (music starts there) and fades back to black, since there's
        nothing to crossfade against.
        """
        if not images:
            return []

        phases = [
            {'type': 'hold', 'img': 0, 'duration': _INTRO_HOLD},
            {'type': 'fade', 'frm': 0, 'to': None, 'duration': _INTRO_FADE_BLACK},
        ]

        if len(images) > 1:
            phases.append({'type': 'fade', 'frm': None, 'to': 1, 'duration': _INTRO_FADE_BLACK, 'start_music': True})
            phases.append({'type': 'hold', 'img': 1, 'duration': _INTRO_HOLD})
            for i in range(2, len(images)):
                phases.append({'type': 'fade', 'frm': i - 1, 'to': i, 'duration': _INTRO_CROSSFADE})
                phases.append({'type': 'hold', 'img': i, 'duration': _INTRO_HOLD})
            last = len(images) - 1
            phases.append({'type': 'fade', 'frm': last, 'to': None, 'duration': _INTRO_FADE_BLACK})
        else:
            # Only one picture configured — start the music on its
            # fade-to-black instead, so a single-logo intro isn't silent.
            phases[-1]['start_music'] = True

        phases.append({'type': 'hold', 'img': None, 'duration': _INTRO_BLACK_PAUSE})
        return phases

    # ── Wiring (called once by Game after construction) ────────────────────

    def set_pause_menu(self, pause_menu):
        """Share Game's existing PauseMenu instance so OPTIONS can open its
        Options tab directly (see PauseMenu.open_options_only) — reuses the
        exact same fonts/box art/volume bars/arrow asset instead of a
        second copy, and inherits whatever SFX/Music volume Game already
        wired up via pause_menu.set_sound_engine()."""
        self._pause_menu = pause_menu

    def set_sound_manager(self, sound_manager):
        """For starting the intro theme once the 2nd boot picture begins
        fading in (or immediately, for menus with no intro configured)."""
        self._sound_manager = sound_manager

    def set_sound_engine(self, sound_engine):
        """For the menu cursor move/confirm SFX — reuses the same 'switch'/
        'select' sound keys PauseMenu uses, so cursor sounds match
        everywhere in the game."""
        self._sound_engine = sound_engine

    def set_save_data_provider(self, provider):
        """provider(slot_index) -> summary dict or None. TitleScreen never
        touches disk itself for save data — Game hands in this callback
        (see Game._get_save_slot_summary) so _slot_has_save_data and the
        normal SAVE SELECT list (_draw_save_slot_list) can tell an
        occupied slot from an empty one, and _confirm_save_slot can decide
        whether to start a new game or load an existing one.

        Expected summary dict shape (all keys optional, missing ones fall
        back the same way open_save_overlay's own params do):
          {'room_name': str, 'play_time': float, 'characters': [ids...],
           'current_character': id or None, 'current_costume': str}
        """
        self._save_data_provider = provider

    # ── Open/close ───────────────────────────────────────────────────────────

    def open(self):
        self.active           = True
        self._t                = 0.0
        self._menu_page        = 'main'
        self._menu_index       = 0
        self._options_open     = False
        self._denied_flash_t   = 0.0
        self._music_started    = False
        self._save_slot_index    = 0
        self._save_scroll_offset = 0
        self._save_delete_mode   = False   # toggled by L/R on the save-select page
        # Which character the player's picked to load as, per slot — see
        # _picked_character_for_slot/get_selected_character. Cleared on
        # every fresh open() so a stale pick from a previous session (or a
        # save that's since been deleted) can never leak in.
        self._save_char_picks    = {}
        for btn in self.button_states:
            self.button_states[btn]       = False
            self.button_press_timers[btn] = 0.0
        self.scroll_up_timer   = 0.0
        self.scroll_down_timer = 0.0

        # Set by _confirm_save_slot when New Game is confirmed. Holds the
        # signal ('new_game') that consume_exit_signal() will hand back to
        # game.py, but only once _menu_fade_alpha has ramped all the way
        # down to 0 — i.e. once the save-select screen is fully faded to
        # black. Keeps the "confirm -> fade out -> THEN switch scenes"
        # ordering entirely on this side, so game.py never has to peek at
        # fade internals.
        self._exit_pending = None

        if self._intro_phases:
            self._phase = 'intro'
            self._enter_intro_phase(0)
        else:
            self._phase           = 'menu'
            self._menu_fade_alpha = 255.0   # nothing to fade in from without an intro
            self._start_music()

    def close(self):
        self.active = False
        if self._options_open and self._pause_menu:
            self._pause_menu.close()
        self._options_open = False

    # ── In-game "Save Game" overlay ─────────────────────────────────────────
    # Reached from a save pad (see save_point.py's SavePointMenu 'save'
    # result and Game._start_save_flow), not from the title menu. Game.py
    # drives update()/draw() for this itself during gameplay — normally
    # both early-return unless self.game_mode == 'title', so this can't
    # just piggyback on the usual per-frame calls.

    def open_save_overlay(self, slot_index, slot_characters=None,
                           current_character=None, current_costume='base',
                           room_name='', play_time=0.0):
        """Repurposes the SAVE SELECT frame as the "Save Game" screen.
        Scrolls straight to slot_index so the slot actually being saved is
        guaranteed visible, forces SELECT mode (no Delete Game), and sets
        self._save_overlay_active so the draw methods below can tell this
        apart from the normal title-menu SAVE SELECT flow:
          - title reads "Save Game" instead of Select/Delete Game
            (_draw_save_select_title)
          - no L/R sprites, and L/R can't toggle delete mode
            (_draw_save_select / handle_input)
          - both scroll arrows stay greyed regardless of scroll position,
            and the selected row's arrow is solid instead of blinking,
            since there's nothing to navigate here
            (_draw_save_select_scrollbar / _draw_save_slot_list)
          - each visible slot's "New Game" placeholder line is replaced
            with that slot's unlocked-character sprites, left to right
            (_draw_save_slot_list)

        slot_characters: optional {slot_index: [character_id, ...]} — only
        the slot actually being saved needs an entry; every other slot
        just renders no sprites, same as it renders no "New Game" line
        here.

        current_character / current_costume: which of those character_ids
        is the one actually being played right now, and its current
        costume/transformation — lets _draw_slot_character_row show a
        live idle-down sprite for just that entry instead of a static
        icon.png (see _get_character_idle_icon). Both optional; with
        current_character=None every entry just uses its icon.png/
        fallback square as before.

        room_name / play_time: shown on the Room and Time lines drawn
        in place of "New Game" (see _draw_save_overlay_info). Both
        optional; room_name='' shows '???' and play_time=0.0 shows
        00:00:00 rather than crashing on missing data.
        """
        self.active               = True
        self._phase                = 'menu'
        self._options_open         = False
        self._exit_pending         = None
        self._menu_page            = 'save_select'
        self._menu_fade_alpha      = 255.0   # no fade-in — it should just be there
        self._save_delete_mode     = False
        self._save_slot_index      = max(0, min(slot_index, len(_SAVE_SLOT_LABELS) - 1))

        # Same "keep the selection inside the viewport" clamp
        # _handle_save_select_keydown uses, just applied once up front
        # instead of incrementally.
        self._save_scroll_offset = max(0, len(_SAVE_SLOT_LABELS) - _SAVE_VIEWPORT_SIZE)
        if self._save_slot_index < self._save_scroll_offset:
            self._save_scroll_offset = self._save_slot_index
        elif self._save_slot_index >= self._save_scroll_offset + _SAVE_VIEWPORT_SIZE:
            self._save_scroll_offset = self._save_slot_index - _SAVE_VIEWPORT_SIZE + 1
        self._save_scroll_offset = max(0, self._save_scroll_offset)

        self._save_overlay_characters = dict(slot_characters or {})
        self._save_overlay_current_character = current_character
        self._save_overlay_current_costume   = current_costume or 'base'
        self._save_overlay_room_name  = room_name or ''
        self._save_overlay_play_time  = play_time or 0.0
        self._save_overlay_active     = True

        for btn in self.button_states:
            self.button_states[btn]       = False
            self.button_press_timers[btn] = 0.0
        self.scroll_up_timer   = 0.0
        self.scroll_down_timer = 0.0

    def close_save_overlay(self):
        """Reverses open_save_overlay() — hands the screen back to
        whatever was drawing before (gameplay), same as the title screen
        itself sitting at self.active = False the entire time the player
        is in-game."""
        self.active                     = False
        self._save_overlay_active       = False
        self._save_overlay_characters   = {}
        self._save_overlay_current_character = None
        self._save_overlay_current_costume   = 'base'
        self._save_overlay_room_name    = ''
        self._save_overlay_play_time    = 0.0
        self._save_popup_occlusion_rect = None

    def get_save_select_frame_rect(self):
        """The SAVE SELECT / "Save Game" bordered box's screen-space rect,
        as of the last time _draw_save_select ran (see the stash there).
        Used by Game._draw_saving_popup to center the "Saving..." popup
        inside the actual frame instead of the whole screen. Returns None
        if the frame hasn't been drawn yet this session."""
        return self._save_select_frame_rect

    def set_save_popup_occlusion_rect(self, rect):
        """Called by Game right before title_screen.draw() each frame the
        save-pad "Saving..." popup is up, so _draw_save_slot_divider knows
        where to clip its bar around (see that method). Pass None to
        clear it — done automatically in close_save_overlay so a stale
        rect can never bleed into the normal title-menu SAVE SELECT."""
        self._save_popup_occlusion_rect = rect

    def get_selected_save_slot(self):
        """The slot index last confirmed via the real title-menu SAVE
        SELECT flow (_confirm_save_slot). Game._start_new_game reads this
        once, right after closing the title screen, so later save-pad
        saves (see open_save_overlay) know which slot is "current" and
        scroll to it. Defaults to 0 until a slot's ever actually been
        confirmed."""
        return self._save_slot_index

    def get_selected_character(self):
        """Which character_id the player picked to load as on the slot
        last confirmed via the real title-menu SAVE SELECT flow (see
        _handle_save_select_keydown's LEFT/RIGHT handling and
        _picked_character_for_slot) — read the same way/at the same time
        as get_selected_save_slot(). None if that slot has no save data
        (nothing to pick between) or no save-data provider is wired in."""
        return self._save_char_picks.get(self._save_slot_index)

    def consume_exit_signal(self):
        """Polled once per frame by game.py (alongside update()) while the
        title screen is up. Returns 'new_game' or 'load_game' (see
        _confirm_save_slot) the instant the post-confirm fade-to-black
        finishes — i.e. once the screen is solid black — and None every
        other frame, including all the frames the fade is still in
        progress. This is what makes the confirm fully fade out before
        game.py ever switches game_mode/closes the title screen, instead
        of cutting to the next scene the instant RETURN/Z was pressed."""
        if self._exit_pending is not None and self._menu_fade_alpha <= 0.0:
            signal, self._exit_pending = self._exit_pending, None
            return signal
        return None

    def _start_music(self):
        if self._music_started:
            return
        self._music_started = True
        if self._sound_manager and self._music_track:
            self._sound_manager.play_music(self._music_track)

    def _play_switch_sfx(self):
        if self._sound_engine:
            self._sound_engine.play_sound('switch')

    def _play_select_sfx(self):
        if self._sound_engine:
            self._sound_engine.play_sound('select')

    def _press(self, btn):
        """Flashes a SAVE SELECT button sprite to its pressed variant —
        same shape as PauseMenu._press. Ticked back down in update()."""
        self.button_states[btn]       = True
        self.button_press_timers[btn] = self.button_press_duration

    # ── Update ───────────────────────────────────────────────────────────────

    def update(self, dt):
        if not self.active:
            return
        self._t += dt

        if self._phase == 'intro':
            self._update_intro(dt)
            return

        # 'menu' phase
        if self._exit_pending is not None:
            # New Game was confirmed — ramp back down to black instead of
            # the usual fade-in. Reuses the same _menu_fade_alpha veil
            # _draw_save_select/_draw_menu already draw, just running it
            # in reverse. consume_exit_signal() is what actually reports
            # this back to game.py once it hits 0.
            self._menu_fade_alpha = max(0.0, self._menu_fade_alpha - dt / _MENU_FADE_OUT * 255.0)
        elif self._menu_fade_alpha < 255:
            self._menu_fade_alpha = min(255.0, self._menu_fade_alpha + dt / _MENU_FADE_IN * 255.0)
        if self._denied_flash_t > 0:
            self._denied_flash_t = max(0.0, self._denied_flash_t - dt)
        for btn in self.button_press_timers:
            if self.button_press_timers[btn] > 0:
                self.button_press_timers[btn] -= dt
                if self.button_press_timers[btn] <= 0:
                    self.button_states[btn] = False
        self.scroll_up_timer   = max(0.0, self.scroll_up_timer   - dt)
        self.scroll_down_timer = max(0.0, self.scroll_down_timer - dt)
        if self._options_open and self._pause_menu:
            self._pause_menu.update(dt)

    def _enter_intro_phase(self, idx):
        self._intro_phase_idx = idx
        self._intro_phase_t   = 0.0
        if idx >= len(self._intro_phases):
            self._phase           = 'menu'
            self._menu_fade_alpha = 0.0
            return
        phase = self._intro_phases[idx]
        if phase.get('start_music'):
            self._start_music()

    def _update_intro(self, dt):
        if self._intro_phase_idx >= len(self._intro_phases):
            self._enter_intro_phase(self._intro_phase_idx)   # transitions to 'menu'
            return
        self._intro_phase_t += dt
        phase = self._intro_phases[self._intro_phase_idx]
        if self._intro_phase_t >= phase['duration']:
            self._enter_intro_phase(self._intro_phase_idx + 1)

    def _skip_intro(self):
        self._start_music()
        self._phase            = 'menu'
        self._menu_fade_alpha  = 255.0   # snap straight to the menu, no fade
        self._intro_phase_idx  = len(self._intro_phases)

    # ── Input ────────────────────────────────────────────────────────────────

    def _current_options(self):
        return _MAIN_OPTIONS if self._menu_page == 'main' else _MODE_OPTIONS

    def handle_input(self, event):
        """
        Returns:
          'new_game' — player chose SINGLE PLAYER
          'quit'     — player backed all the way out with ESC on the
                       START/OPTIONS page
          None       — no action taken (includes every OPTIONS-tab
                       interaction — that's between the player and
                       PauseMenu until it reports 'close')
        """
        if not self.active:
            return None

        if self._save_overlay_active:
            # The in-game "Save Game" screen (see open_save_overlay) is
            # display-only — Game.py doesn't feed it events either way,
            # but this is a belt-and-suspenders guard against the normal
            # SAVE SELECT keys/mouse handling firing on it.
            return None

        if self._phase == 'intro':
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self._skip_intro()
            return None

        if self._options_open:
            result = self._pause_menu.handle_input(event) if self._pause_menu else None
            if result == 'close':
                self._options_open = False
            return None

        if self._menu_page == 'save_select':
            if event.type == pygame.KEYDOWN:
                return self._handle_save_select_keydown(event.key)
            if event.type == pygame.MOUSEMOTION:
                mx, my = event.pos
                for i, rect in enumerate(self._save_slot_hit_rects):
                    if rect.collidepoint(mx, my):
                        self._save_slot_index = self._save_scroll_offset + i
                        break
                return None
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my = event.pos
                for btn, rect in self._save_lr_hit_rects.items():
                    if rect.collidepoint(mx, my):
                        self._toggle_save_delete_mode(btn)
                        return None
                for i, rect in enumerate(self._save_slot_hit_rects):
                    if rect.collidepoint(mx, my):
                        self._save_slot_index = self._save_scroll_offset + i
                        return self._confirm_save_slot()
                return None
            return None

        if event.type == pygame.KEYDOWN:
            return self._handle_menu_keydown(event.key)

        if event.type == pygame.MOUSEMOTION:
            mx, my = event.pos
            for i, rect in enumerate(self._option_hit_rects):
                if rect.collidepoint(mx, my):
                    self._menu_index = i
                    break
            return None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            for i, rect in enumerate(self._option_hit_rects):
                if rect.collidepoint(mx, my):
                    self._menu_index = i
                    return self._confirm_menu_selection()
            return None

        return None

    def _handle_menu_keydown(self, key):
        options = self._current_options()

        if key in (pygame.K_UP, pygame.K_w):
            self._menu_index = (self._menu_index - 1) % len(options)
            self._play_switch_sfx()
            return None
        if key in (pygame.K_DOWN, pygame.K_s):
            self._menu_index = (self._menu_index + 1) % len(options)
            self._play_switch_sfx()
            return None
        if key in (pygame.K_RETURN, pygame.K_z):
            return self._confirm_menu_selection()
        if key == pygame.K_ESCAPE:
            if self._menu_page == 'mode_select':
                self._menu_page  = 'main'
                self._menu_index = 0
                return None
            return 'quit'
        return None

    def _toggle_save_delete_mode(self, btn):
        """Toggle "Select Game" <-> "Delete Game" on the SAVE SELECT list —
        shared by the L/R keyboard shortcut (_handle_save_select_keydown)
        and clicking the L/R button sprites directly (handle_input's
        save_select MOUSEBUTTONDOWN branch, via self._save_lr_hit_rects).

        btn: 'l' or 'r' — either one flips the same self._save_delete_mode
        flag; this only decides which button sprite flashes pressed.
        """
        self._save_delete_mode = not self._save_delete_mode
        self._press(btn)
        self._play_switch_sfx()

    def _handle_save_select_keydown(self, key):
        """UP/DOWN move the selected save slot; the visible window
        (self._save_scroll_offset) only slides when the new selection
        would otherwise fall outside the currently-shown
        _SAVE_VIEWPORT_SIZE rows. e.g. with slots 1/2 visible and slot 1
        selected: DOWN selects slot 2 without scrolling (already on
        screen); DOWN again selects slot 3, which isn't visible, so the
        window slides down by one row — now showing slots 2/3, with slot
        2 landing in the row slot 1 used to occupy.

        RETURN/Z confirms the selected slot (see _confirm_save_slot).
        ESCAPE/X backs out to mode_select, same as before."""
        count = len(_SAVE_SLOT_LABELS)

        if key in (pygame.K_UP, pygame.K_w):
            if self._save_slot_index > 0:
                self._save_slot_index   -= 1
                self._save_scroll_offset = min(self._save_scroll_offset, self._save_slot_index)
                self.scroll_up_timer     = self.scroll_press_duration
                self._play_switch_sfx()
            return None
        if key in (pygame.K_DOWN, pygame.K_s):
            if self._save_slot_index < count - 1:
                self._save_slot_index   += 1
                self._save_scroll_offset = max(
                    self._save_scroll_offset,
                    self._save_slot_index - _SAVE_VIEWPORT_SIZE + 1
                )
                self.scroll_down_timer   = self.scroll_press_duration
                self._play_switch_sfx()
            return None
        if key in (pygame.K_RETURN, pygame.K_z):
            return self._confirm_save_slot()
        if key in (pygame.K_LEFT, pygame.K_a, pygame.K_RIGHT, pygame.K_d):
            self._cycle_picked_character(-1 if key in (pygame.K_LEFT, pygame.K_a) else 1)
            return None
        if key in _KEY_L or key in _KEY_R:
            # Toggle "Select Game" <-> "Delete Game" — see
            # _draw_save_select_title (title text) and
            # _draw_save_slot_list (hover color). Doesn't touch which
            # slot is selected or the scroll window.
            self._toggle_save_delete_mode('l' if key in _KEY_L else 'r')
            return None
        if key in (pygame.K_ESCAPE, pygame.K_x):
            self._press('b')
            self._play_switch_sfx()
            self._menu_page = 'mode_select'
            return None
        return None

    def _picked_character_for_slot(self, slot_index, summary):
        """Returns the character_id the player's currently picked to load
        as on `slot_index`, deriving and caching a default the first time
        this slot's ever asked about (see self._save_char_picks). Default
        is that save's own current_character — falling back to the first
        entry in its roster if current_character isn't (or is no longer)
        one of its unlocked characters — so the row opens on whichever
        character the save was last actually played as, exactly like
        before this picker existed, until the player explicitly moves it
        with LEFT/RIGHT (see _cycle_picked_character).

        summary: this slot's dict from the save-data provider (see
        set_save_data_provider) — same shape _draw_save_slot_list already
        has in hand, so this never re-fetches it itself."""
        if slot_index in self._save_char_picks:
            return self._save_char_picks[slot_index]

        characters = (summary or {}).get('characters') or []
        pick       = (summary or {}).get('current_character')
        if pick not in characters:
            pick = characters[0] if characters else None

        self._save_char_picks[slot_index] = pick
        return pick

    def _cycle_picked_character(self, direction):
        """LEFT/RIGHT (or A/D) on the SAVE SELECT list — moves the pick
        for the currently-selected slot (self._save_slot_index) one step
        through that slot's roster, wrapping around at either end.
        direction: -1 for LEFT/A, +1 for RIGHT/D.

        No-ops silently (no sound, nothing changes) on an empty slot or a
        slot with only one unlocked character — there's nothing to pick
        between yet, same "nothing to do" shape as _confirm_save_slot's
        DELETE-mode-on-an-empty-slot branch."""
        summary = self._save_data_provider(self._save_slot_index) if self._save_data_provider else None
        characters = (summary or {}).get('characters') or []
        if len(characters) < 2:
            return

        current_pick = self._picked_character_for_slot(self._save_slot_index, summary)
        idx = characters.index(current_pick) if current_pick in characters else 0
        self._save_char_picks[self._save_slot_index] = characters[(idx + direction) % len(characters)]
        self._play_switch_sfx()

    def _slot_has_save_data(self, slot_index):
        """Whether the given save slot has anything on it. Backed by
        whatever Game wired in via set_save_data_provider — with no
        provider set (or nothing on this slot), every slot reads as
        empty, same fallback _draw_save_select uses to grey out the
        A/Select button."""
        if self._save_data_provider is None:
            return False
        return self._save_data_provider(slot_index) is not None

    def _confirm_save_slot(self):
        """RETURN/Z (or a slot click) on the SAVE SELECT page. Behavior
        depends on which mode L/R has toggled the page into (see
        self._save_delete_mode):

          SELECT mode — confirms the slot. An empty slot starts a new
          game exactly as before; a slot with data (see
          _slot_has_save_data/set_save_data_provider) loads it instead —
          consume_exit_signal() hands back 'load_game' rather than
          'new_game', and Game._start_loaded_game reads get_selected_
          save_slot() to know which one.

          DELETE mode — no-ops on an empty slot: no A-button flash, no
          sound, nothing happens — there's nothing there to delete.
          Same no-op-when-empty shape as PauseMenu._select_equip_item
          on an empty equip list. Swap in the real delete-confirmation
          flow for the has-save case once one exists.
        """
        if self._save_delete_mode:
            if not self._slot_has_save_data(self._save_slot_index):
                return None
            # TODO: open a real delete-confirmation flow. Deleting isn't
            # wired up yet even though save data can now exist — this
            # branch is reachable but still a no-op for now.
            return None

        self._press('a')
        self._play_select_sfx()
        # Don't hand the signal back yet — kick off the fade-to-black
        # (see update()'s _exit_pending branch) and let
        # consume_exit_signal() report it once that finishes, so game.py
        # only switches scenes once the menu is fully faded out.
        self._exit_pending = 'load_game' if self._slot_has_save_data(self._save_slot_index) else 'new_game'
        return None

    def _confirm_menu_selection(self):
        options = self._current_options()
        key     = options[self._menu_index][0]

        if self._menu_page == 'main':
            if key == 'start':
                self._play_select_sfx()
                self._menu_page  = 'mode_select'
                self._menu_index = 0
            elif key == 'options':
                self._play_select_sfx()
                if self._pause_menu:
                    self._options_open = True
                    self._pause_menu.open_options_only()
                else:
                    print('[TitleScreen] OPTIONS pressed but no PauseMenu wired in — '
                          'call title_screen.set_pause_menu(pause_menu) from Game.__init__')
            return None

        # mode_select
        if key == 'single':
            self._play_select_sfx()
            self._menu_page          = 'save_select'
            self._save_slot_index    = 0
            self._save_scroll_offset = 0
            self._save_delete_mode   = False   # always re-enter on the Select side
            return None
        elif key == 'multi':
            # Multiplayer doesn't exist yet — flash the label instead of
            # silently doing nothing. Swap this out once it's real.
            self._denied_flash_t = _DENIED_FLASH_TIME
        return None

    # ── Draw ─────────────────────────────────────────────────────────────────

    def _render_text(self, font, text, color=None):
        """Single font.render() call — same pattern as CreditsScreen's
        _render_role/_render_title for text with no comma in it (menu
        labels and the title never contain one, so no comma-split needed
        here)."""
        surf = font.render(text).copy()
        surf.fill(color if color is not None else self.text_color, special_flags=pygame.BLEND_RGBA_MULT)
        return surf

    def draw(self, screen):
        if not self.active:
            return
        if self._phase == 'intro':
            self._draw_intro(screen)
        else:
            self._draw_menu(screen)

    def _draw_intro(self, screen):
        screen.fill((0, 0, 0))
        if self._intro_phase_idx >= len(self._intro_phases):
            return
        phase  = self._intro_phases[self._intro_phase_idx]
        center = (self.screen_width // 2, self.screen_height // 2)

        if phase['type'] == 'hold':
            img = self._intro_images[phase['img']] if phase['img'] is not None else None
            if img:
                screen.blit(img, img.get_rect(center=center))
            return

        # 'fade' — draw the 'from' side first (or nothing, for black),
        # then the 'to' side on top at rising alpha (or a rising black
        # veil, when fading out to black).
        t   = min(1.0, self._intro_phase_t / phase['duration']) if phase['duration'] > 0 else 1.0
        frm = self._intro_images[phase['frm']] if phase['frm'] is not None else None
        to  = self._intro_images[phase['to']]  if phase['to']  is not None else None

        if frm:
            screen.blit(frm, frm.get_rect(center=center))
        if to:
            fading = to.copy()
            fading.set_alpha(int(255 * t))
            screen.blit(fading, fading.get_rect(center=center))
        elif frm:
            veil = pygame.Surface((self.screen_width, self.screen_height))
            veil.fill((0, 0, 0))
            veil.set_alpha(int(255 * t))
            screen.blit(veil, (0, 0))

    def _draw_menu_background(self, screen):
        if self._menu_bg:
            screen.blit(self._menu_bg, (0, 0))
        else:
            screen.fill(self.bg_color)

    def _draw_menu(self, screen):
        if self._options_open and self._pause_menu:
            # PauseMenu paints its own full-screen tiled background first —
            # nothing from the main menu would be visible underneath it
            # anyway, so skip drawing it.
            self._pause_menu.draw(screen)
            return

        if self._menu_page == 'save_select':
            self._draw_save_select(screen)
            if self._menu_fade_alpha < 255:
                veil = pygame.Surface((self.screen_width, self.screen_height))
                veil.fill((0, 0, 0))
                veil.set_alpha(255 - int(self._menu_fade_alpha))
                screen.blit(veil, (0, 0))
            return

        self._draw_menu_background(screen)

        if self.title_text:
            title_surf = self._render_text(self.title_font, self.title_text, color=self.title_color)
            title_rect = title_surf.get_rect(center=(self.screen_width // 2, int(self.screen_height * 0.22)))
            shadow_surf = title_surf.copy()
            shadow_surf.fill(self.text_shadow_color, special_flags=pygame.BLEND_RGBA_MULT)
            screen.blit(shadow_surf, title_rect.move(self.shadow_offset))
            screen.blit(title_surf, title_rect)

        self._draw_option_box(screen)

        if self._menu_fade_alpha < 255:
            veil = pygame.Surface((self.screen_width, self.screen_height))
            veil.fill((0, 0, 0))
            veil.set_alpha(255 - int(self._menu_fade_alpha))
            screen.blit(veil, (0, 0))

    # ── SAVE SELECT (reached from SINGLE PLAYER) ────────────────────────────
    #
    # Reuses PauseMenu's own border art, L/R shoulder-button sprites, and
    # A/B footer instead of duplicating assets — same reasoning as OPTIONS
    # reusing PauseMenu.open_options_only(). Unlike OPTIONS this isn't a
    # PauseMenu tab though (there's no player/tab list to page through
    # here), so the frame is drawn directly against the shared PauseMenu
    # instance's loaded sprites/fonts rather than going through its draw().
    #
    # Frame: border, L/R sprites (no flanking tab-name text, since there's
    # nothing to page between yet), the "Select Game" title, the scrolling
    # save-slot list (see _draw_save_slot_list), and the A-Select/B-Cancel
    # footer.

    def _draw_save_select(self, screen):
        pm = self._pause_menu
        if pm is None:
            # Shouldn't happen in practice (Game wires this up right after
            # constructing both menus — see set_pause_menu), but degrade to
            # a plain fill rather than crashing if it's ever missing.
            print('[TitleScreen] SAVE SELECT opened but no PauseMenu wired in — '
                  'call title_screen.set_pause_menu(pause_menu) from Game.__init__')
            screen.fill(self.bg_color)
            return

        pm._draw_tiled_background(screen, pygame.Rect(0, 0, self.screen_width, self.screen_height))

        # Same frame geometry PauseMenu.draw() uses, off the shared canvas
        # so the box lands in exactly the same place/size as every other
        # bordered menu in the game.
        title_margin = int(pm.canvas_height * 0.08)
        inner_margin = int(pm.canvas_height)
        inner_w      = int(pm.canvas_width * 1.1 - inner_margin)
        inner_h      = int(pm.canvas_height) - 286
        _layout_h    = int(pm.canvas_height) - 221
        box_x        = pm.canvas_x + (pm.canvas_width - inner_w) // 2
        box_y        = pm.canvas_y + title_margin + 1

        # Stash the frame rect every frame so get_save_select_frame_rect()
        # always reflects exactly where this box just got drawn — same
        # geometry whether this is the title-menu SAVE SELECT or the
        # in-game "Save Game" overlay (open_save_overlay), since both go
        # through this same method.
        self._save_select_frame_rect = pygame.Rect(box_x, box_y, inner_w, inner_h)

        drawn = pm.box_sprite and pm._draw_9slice_sprite(
            screen, pm.box_sprite, box_x, box_y, inner_w, inner_h, corner_size=20
        )
        if not drawn:
            pygame.draw.rect(screen, pm.border_outer, (box_x-6, box_y-6, inner_w+12, inner_h+12))
            pygame.draw.rect(screen, pm.border_inner, (box_x-3, box_y-3, inner_w+6,  inner_h+6))
            pygame.draw.rect(screen, pm.border_green, (box_x-1, box_y-1, inner_w+2,  inner_h+2))
            pm._draw_tiled_background(screen, pygame.Rect(box_x, box_y, inner_w, inner_h))

        # L/R shoulder-button sprites above the frame, same position as
        # PauseMenu's own — but with no flanking prev/next label text,
        # since this screen doesn't page between anything (yet). They're
        # here purely so the frame reads as the same kind of bordered menu
        # as everywhere else.
        btn_scale = max(2, int(pm.canvas_height * 0.06))
        lr_y      = box_y - btn_scale - max(4, int(pm.canvas_height * 0.01))
        # The "Save Game" overlay (see open_save_overlay) has nothing to
        # page between — no Delete Game mode, nothing to switch pages on —
        # so the L/R sprites are dropped entirely instead of showing
        # buttons that don't do anything. lr_y is still computed above
        # since _draw_save_select_title anchors off it either way.
        if not self._save_overlay_active:
            l_raw = pm.button_l_pressed if self.button_states['l'] else pm.button_l
            l_w   = int(l_raw.get_width() * (btn_scale / l_raw.get_height())) if l_raw else btn_scale
            pm._draw_button_sprite(screen, pm.button_l, pm.button_l_pressed, self.button_states['l'], box_x, lr_y, '')
            r_raw   = pm.button_r
            r_w     = int(r_raw.get_width() * (btn_scale / r_raw.get_height())) if r_raw else btn_scale
            r_btn_x = box_x + inner_w - r_w - 16
            pm._draw_button_sprite(screen, pm.button_r, pm.button_r_pressed, self.button_states['r'], r_btn_x, lr_y, '')

            # Clickable bounds for the two sprites just drawn — same
            # padding convention as the scroll-arrow hit rects below
            # (±4px around the sprite) — read back in handle_input's
            # save_select MOUSEBUTTONDOWN branch. No flanking label text
            # to extend into here (unlike PauseMenu's l_tab/r_tab zones),
            # since this screen has nothing to page between — just the
            # Select/Delete Game toggle these buttons perform.
            self._save_lr_hit_rects = {
                'l': pygame.Rect(box_x - 4, lr_y - 4, l_w + 8, btn_scale + 8),
                'r': pygame.Rect(r_btn_x - 4, lr_y - 4, r_w + 8, btn_scale + 8),
            }
        else:
            self._save_lr_hit_rects = {}

        self._draw_save_select_title(screen, pm, box_x, inner_w, lr_y)

        # A/Select, B/Cancel footer sits at the bottom of the frame — the
        # slot list is vertically centered in the space between the top
        # padding and this row, so compute it first.
        button_y = box_y + _layout_h - int(pm.canvas_height * 0.104) - 65
        _b_scale = max(2, int(pm.canvas_height * 0.06))

        pad = max(8, int(inner_w * 0.04))
        content_top    = box_y + pad
        content_bottom = button_y - int(pm.canvas_height * 0.02)
        self._draw_save_slot_list(screen, pm, box_x, inner_w, content_top, content_bottom)

        # Right-side scroll arrows + track, same sprites/track PauseMenu
        # uses on Inventory/Journal — but positioned higher, since those
        # tabs leave room at the top of the frame for a sub-tab row this
        # screen doesn't have.
        self._draw_save_select_scrollbar(screen, pm, box_x, box_y, inner_w, pad)

        # A/Select shows greyed only in DELETE mode when the currently-
        # selected slot has nothing on it — there's nothing to delete
        # there (see _confirm_save_slot). In SELECT mode the A button
        # stays active even on an empty slot, since selecting one there
        # is what starts a new game.
        #
        # PauseMenu has no separate "grey A" sprite asset — it draws the
        # normal/pressed sprite as usual, then tints it grey by blitting
        # a (150,150,150) surface over it with BLEND_RGBA_MIN (clamps
        # each channel down to that ceiling). Same technique it uses for
        # options_editing/equip_list_is_empty — see PauseMenu.draw.
        has_save   = self._slot_has_save_data(self._save_slot_index)
        a_disabled = self._save_delete_mode and not has_save

        sel_x = box_x + int(inner_w - 961)
        pm._draw_button_sprite(screen, pm.button_a, pm.button_a_pressed, self.button_states['a'], sel_x, button_y, '')
        _a = pm.button_a_pressed if self.button_states['a'] else pm.button_a
        if a_disabled and _a:
            sf = _b_scale / _a.get_height()
            aw = int(_a.get_width() * sf)
            scaled = pygame.transform.scale(_a, (aw, _b_scale))
            grey_surf = pygame.Surface((aw, _b_scale), pygame.SRCALPHA)
            grey_surf.fill((150, 150, 150, 255))
            grey_surf.blit(scaled, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            screen.blit(grey_surf, (sel_x, button_y))
        _a_w = int(_a.get_width() * (_b_scale / _a.get_height())) if _a else _b_scale
        sel_surfs = self._menu_label_surfs(pm, 'Select')
        sel_lx    = sel_x + _a_w + int(5 * RENDER_SCALE - 8)
        sel_ly    = button_y + (_b_scale - max(s.get_height() for s in sel_surfs)) // 2 + 2
        self._blit_menu_label(screen, sel_surfs, sel_lx, sel_ly, spacing=6)

        cancel_x = box_x + int(inner_w - 760)
        pm._draw_button_sprite(screen, pm.button_b, pm.button_b_pressed, self.button_states['b'], cancel_x, button_y, '')
        _b   = pm.button_b_pressed if self.button_states['b'] else pm.button_b
        _b_w = int(_b.get_width() * (_b_scale / _b.get_height())) if _b else _b_scale
        can_surfs = self._menu_label_surfs(pm, 'Cancel')
        can_lx    = cancel_x + _b_w + int(5 * RENDER_SCALE - 8)
        can_ly    = button_y + (_b_scale - max(s.get_height() for s in can_surfs)) // 2 + 2
        self._blit_menu_label(screen, can_surfs, can_lx, can_ly, spacing=6)

    def _draw_save_select_scrollbar(self, screen, pm, box_x, box_y, inner_w, pad):
        """Right-side up/down arrows + spacing-bar track, same assets and
        layout math as PauseMenu's Inventory/Journal scrollbar (see
        PauseMenu.draw). can_up/can_down are placeholders until the actual
        save-slot list and scroll state exist — up starts greyed (already
        at the top), down stays active as a stand-in for "more slots
        below". Click zones come back once there's real slot data to
        scroll through.

        Positioned higher than PauseMenu's own version: Inventory/Journal
        start their arrow ~33px down to leave room for a sub-tab row this
        screen doesn't have, so ours starts right at the top padding.
        """
        # Both arrows stay greyed on the "Save Game" overlay (see
        # open_save_overlay) regardless of scroll position — there's
        # nothing to scroll to since the player can't navigate it at all.
        can_up   = (not self._save_overlay_active) and self._save_scroll_offset > 0
        can_down = (not self._save_overlay_active) and \
            (self._save_scroll_offset + _SAVE_VIEWPORT_SIZE) < len(_SAVE_SLOT_LABELS)

        arrow_scale = max(1, int(pm.canvas_height * 0.06))
        scroll_x    = box_x + inner_w - pad - arrow_scale + 3
        scroll_top  = box_y + pad - 11

        up_surf = pm.arrow_up_pressed if self.scroll_up_timer > 0 else (pm.arrow_up if can_up else pm.arrow_up_grey)
        arrow_h = arrow_scale
        if up_surf:
            sf = arrow_scale / up_surf.get_height()
            up_scaled = pygame.transform.scale(up_surf, (int(up_surf.get_width() * sf), arrow_scale))
            screen.blit(up_scaled, (scroll_x, scroll_top))
            arrow_h = up_scaled.get_height()

        bar_top    = scroll_top + arrow_h + 4
        bar_bottom = scroll_top + arrow_h - 153 + int(pm.canvas_height * 0.55)
        if pm.spacing_bar:
            bar_h_ = max(1, bar_bottom - bar_top)
            sb = pm.spacing_bar
            so_h = sb.get_height(); so_w = sb.get_width()
            tip_scale = max(1, round(arrow_scale / so_h) * 4)
            tip_px = so_h // 4; tip_h = tip_px * tip_scale
            bar_w  = so_w * tip_scale
            top_s = pygame.transform.scale(sb.subsurface((0, 0, so_w, tip_px)), (bar_w, tip_h))
            mid_s = pygame.transform.scale(sb.subsurface((0, tip_px, so_w, so_h - tip_px*2)), (bar_w, max(1, bar_h_ - tip_h*2)))
            bot_s = pygame.transform.scale(sb.subsurface((0, so_h - tip_px, so_w, tip_px)), (bar_w, tip_h))
            blit_x = scroll_x - 3 + (arrow_scale - bar_w) // 2
            screen.blit(top_s, (blit_x, bar_top))
            screen.blit(mid_s, (blit_x, bar_top + tip_h))
            screen.blit(bot_s, (blit_x, bar_bottom - tip_h))

        dn_surf = pm.arrow_down_pressed if self.scroll_down_timer > 0 else (pm.arrow_down if can_down else pm.arrow_down_grey)
        if dn_surf:
            sf = arrow_scale / dn_surf.get_height()
            dn_scaled = pygame.transform.scale(dn_surf, (int(dn_surf.get_width() * sf), arrow_scale))
            screen.blit(dn_scaled, (scroll_x, bar_bottom + 8))

    def _draw_save_slot_list(self, screen, pm, box_x, inner_w, content_top, content_bottom):
        """Draws up to _SAVE_VIEWPORT_SIZE save-slot rows inside the
        frame, windowed around self._save_slot_index via
        self._save_scroll_offset (see _handle_save_select_keydown for the
        scroll rule). Each row is the slot's own label (save_slot_font,
        top-left aligned, blinking selector arrow to its left when
        selected) plus a status line underneath it: "New Game" for an
        empty slot, or that slot's actual Saga/Room/Time info and
        unlocked-character roster for one with save data (see
        set_save_data_provider/_confirm_save_slot), centered horizontally
        in the frame using PauseMenu's own menu_uppercase/menu_lowercase
        font (same as the A/B footer labels) for the "New Game" case.
        Both lines of a row share that row's white/green color, so the
        whole row (label + status) turns green together on
        selection/hover. Populates self._save_slot_hit_rects
        (viewport-relative, index 0 = topmost visible row) for mouse
        hover/click."""
        labels = _SAVE_SLOT_LABELS
        font   = self.save_slot_font
        surfs  = [self._render_label(font, label) for label in labels]
        line_h = max(s.get_height() for s in surfs)

        sub_letter_gap = 6
        sub_word_gap   = 16

        def _new_game_surfs(color):
            words = []
            for word in 'New Game'.split(' '):
                word_surfs = []
                for ch in word:
                    s = (pm.menu_uppercase_font if ch.isupper() else pm.menu_lowercase_font).render(ch)
                    s = s.copy(); s.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
                    word_surfs.append(s)
                words.append(word_surfs)
            return words

        # Sized once, purely to know how much extra row height the
        # "New Game" line needs — color doesn't affect dimensions, each
        # row rebuilds its own colored copy further down. This also
        # doubles as the band_h handed to _draw_slot_character_row on
        # the "Save Game" overlay (see below), so it's deliberately left
        # untouched by the overlay's info block sizing right after —
        # changing it would shift the character row's already-tuned
        # position.
        _sizing_words = _new_game_surfs(self.text_color)
        sub_h         = max(s.get_height() for w in _sizing_words for s in w)
        sub_gap       = int(sub_h * 0.5)   # gap between "Game N" and its "New Game"/info line

        # Row height/spacing is deliberately IDENTICAL between the normal
        # Select Game menu and the in-game Save Game overlay — always
        # sized off sub_h alone, regardless of self._save_overlay_active
        # or which slot is being saved. The Saga/Room/Time block
        # (_draw_save_overlay_info) draws inside that same fixed-height
        # row rather than growing it, so the divider and every row below
        # the saved slot land at the exact same y as they would in the
        # normal menu. (Previously this grew per-row to fit the taller
        # info block, but that pushed every row below the saved slot
        # lower than its normal-menu counterpart — removed.)
        row_content_h = line_h + sub_gap + sub_h
        row_gap       = int(line_h * 5.8) + 3   # breathing room between rows — tune this to widen/narrow the gap
        row_h         = row_content_h + row_gap

        start   = self._save_scroll_offset
        visible = list(enumerate(labels))[start:start + _SAVE_VIEWPORT_SIZE]

        arrow_src = pm.equip_arrow if pm else None
        arrow_scaled = None
        arrow_w = 0
        if arrow_src:
            arrow_scale = max(1, self.save_slot_font_scale)
            arrow_scaled = pygame.transform.scale(
                arrow_src,
                (arrow_src.get_width() * arrow_scale, arrow_src.get_height() * arrow_scale)
            )
            arrow_w = arrow_scaled.get_width()

        # Top-left aligned: fixed left inset (room for the arrow, plus a
        # small gap, plus the frame's own padding) instead of centering
        # each label, and the list starts right at the top of the content
        # area instead of being vertically centered in it.
        arrow_gap = max(4, int(1 * RENDER_SCALE)) - 3
        pad_x     = max(8, int(inner_w * 0.04)) - 90
        arrow_x   = box_x + pad_x + 19
        text_x    = arrow_x + arrow_w + arrow_gap
        list_y    = content_top + 1

        self._save_slot_hit_rects = []
        y = list_y
        for i, (slot_i, _label) in enumerate(visible):
            surf = surfs[slot_i]
            is_selected = (slot_i == self._save_slot_index)
            # Selected row is red while L/R has toggled the page into
            # "Delete Game" mode, green otherwise — matches the title
            # (see _draw_save_select_title).
            hover_color = self.text_hover_delete_color if self._save_delete_mode else self.text_hover_color
            color = hover_color if is_selected else self.text_color

            colored = surf.copy()
            colored.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
            shadow = colored.copy()
            shadow.fill(self.text_shadow_color, special_flags=pygame.BLEND_RGBA_MULT)

            bbox = surf.get_bounding_rect()
            draw_x = text_x - bbox.left

            screen.blit(shadow, (draw_x + self.shadow_offset[0], y + self.shadow_offset[1]))
            screen.blit(colored, (draw_x, y))

            if is_selected and arrow_scaled:
                # Solid (never blinking) on the "Save Game" overlay (see
                # open_save_overlay) — there's no cursor to draw attention
                # to since the player isn't navigating anything here.
                blink_on = self._save_overlay_active or \
                    (self._t % (_ARROW_BLINK_INTERVAL * 2)) < _ARROW_BLINK_INTERVAL
                if blink_on:
                    arrow_y = y + (line_h - arrow_scaled.get_height()) // 2 + 4
                    screen.blit(arrow_scaled, (arrow_x, arrow_y))

            if self._save_overlay_active:
                # In-game Save Game screen: the slot's Saga/Room/Time info
                # (_draw_save_overlay_info) takes over the exact spot the
                # "New Game" line used to sit at, and this slot's unlocked-
                # character sprites (_draw_slot_character_row) are drawn
                # from that same anchor exactly as before — its band_h is
                # still sub_h alone (see above), so its already-tuned
                # position doesn't move just because the info text next to
                # it got taller. Only the slot actually being saved
                # (self._save_slot_index, set by open_save_overlay) gets
                # the Saga/Room/Time text and character sprites — every
                # other visible slot draws neither, same as it draws no
                # "New Game" line here.
                row_center_x = box_x + inner_w // 2
                row_y        = y + line_h + sub_gap - 18
                if slot_i == self._save_slot_index:
                    self._draw_save_overlay_info(
                        screen, pm, box_x, inner_w, row_y, color,
                        occlusion_rect=self._save_popup_occlusion_rect,
                    )
                characters   = self._save_overlay_characters.get(slot_i)
                self._draw_slot_character_row(
                    screen, characters, row_center_x, row_y, sub_h,
                    current_character=self._save_overlay_current_character,
                    current_costume=self._save_overlay_current_costume,
                )
            else:
                # Normal (non-overlay) SAVE SELECT list: a slot with real
                # save data (see set_save_data_provider) shows that save's
                # actual Saga/Room/Time info and unlocked-character roster
                # — same renderers the in-game "Save Game" overlay uses
                # (_draw_save_overlay_info / _draw_slot_character_row),
                # just fed this slot's own summary instead of the overlay's
                # single active-slot state. An empty slot still falls back
                # to "New Game" exactly as before.
                summary = self._save_data_provider(slot_i) if self._save_data_provider else None
                row_y = y + line_h + sub_gap - 18

                if summary is not None:
                    self._draw_save_overlay_info(
                        screen, pm, box_x, inner_w, row_y, color,
                        room_name=summary.get('room_name', ''),
                        play_time=summary.get('play_time', 0.0),
                    )
                    row_center_x = box_x + inner_w // 2
                    self._draw_slot_character_row(
                        screen, summary.get('characters'), row_center_x, row_y, sub_h,
                        current_character=summary.get('current_character'),
                        current_costume=summary.get('current_costume', 'base'),
                        picked_character=self._picked_character_for_slot(slot_i, summary),
                        show_picker=True,
                    )
                else:
                    # This slot's own "New Game" status line — centered
                    # horizontally in the frame, same color as the slot
                    # label above it.
                    ng_words   = _new_game_surfs(color)
                    ng_word_ws = [sum(s.get_width() for s in w) + sub_letter_gap * (len(w) - 1) for w in ng_words]
                    ng_total_w = sum(ng_word_ws) + sub_word_gap * (len(ng_words) - 1)
                    ng_x       = box_x + (inner_w - ng_total_w) // 2
                    ng_y       = row_y

                    lx = ng_x
                    for w in ng_words:
                        for s in w:
                            sh = s.copy(); sh.fill(self.text_shadow_color, special_flags=pygame.BLEND_RGBA_MULT)
                            oy = sub_h - s.get_height()
                            screen.blit(sh, (lx + self.shadow_offset[0], ng_y + oy + self.shadow_offset[1]))
                            screen.blit(s, (lx, ng_y + oy))
                            lx += s.get_width() + sub_letter_gap
                        lx += sub_word_gap - sub_letter_gap

            self._save_slot_hit_rects.append(pygame.Rect(box_x, y, inner_w, row_h))

            # Divider between this row and the next visible one — same
            # spacing_bar art as PauseMenu's Equip-tab horizontal divider
            # (see its own horiz_src build in _draw_equip_page), just
            # rotated/scaled to this row's width and dropped right below
            # this slot's own content. Anchored to THIS row's content
            # bottom (row_content_h) rather than row_h/row_gap, so
            # widening row_gap only stretches the empty space below the
            # bar instead of dragging the bar down with it. Skipped after
            # the last visible row since there's nothing below it to
            # divide from. self._save_popup_occlusion_rect (set by Game
            # right before draw() while the in-game "Save Game" popup is
            # up — see set_save_popup_occlusion_rect) lets the bar clip
            # itself around wherever that popup is about to land instead
            # of running straight through it.
            if i < len(visible) - 1:
                divider_y     = y + row_content_h + int(row_gap * 0.8) + 1
                divider_right = box_x + inner_w - max(8, int(inner_w * 0.11))
                self._draw_save_slot_divider(
                    screen, pm, text_x, divider_right, divider_y,
                    occlusion_rect=self._save_popup_occlusion_rect
                )

            y += row_h

    def _draw_save_slot_divider(self, screen, pm, left_x, right_x, y, occlusion_rect=None):
        """Horizontal bar dropped between two save-slot rows so
        consecutive slots read as separate entries instead of running
        together — built from the same spacing_bar art as PauseMenu's
        Equip-tab horizontal divider, just rotated 90° (tip caps land
        left/right instead of top/bottom) and stretched to this row's
        width instead of that tab's fixed short bar.

        This is always ONE continuous bar spanning left_x to right_x —
        tip caps only ever land at those two true ends. occlusion_rect,
        when given and it actually overlaps this bar's row, doesn't
        shorten the bar or add new caps; it just clips the same
        continuous artwork so nothing draws inside that rect, leaving a
        flat cut on either side of whatever's drawn there (namely
        Game._draw_saving_popup's "Saving..." box) rather than the bar
        reading as two separate capped pieces."""
        sb = pm.spacing_bar if pm else None
        if not sb:
            return
        horiz_src = pygame.transform.flip(pygame.transform.rotate(sb, 90), False, True)
        hs_w, hs_h = horiz_src.get_size()
        bar_w = max(1, right_x - left_x)

        tip_scale = max(1, self.save_slot_font_scale)
        tip_px    = hs_w // 4
        tip_w     = tip_px * tip_scale
        if bar_w <= tip_w * 2:
            tip_w = max(1, bar_w // 2)
        mid_w = max(1, bar_w - tip_w * 2)
        bar_h = hs_h * tip_scale

        hl = pygame.transform.scale(horiz_src.subsurface((0, 0, tip_px, hs_h)), (tip_w, bar_h))
        hm = pygame.transform.scale(horiz_src.subsurface((tip_px, 0, hs_w - tip_px * 2, hs_h)), (mid_w, bar_h))
        hr = pygame.transform.scale(horiz_src.subsurface((hs_w - tip_px, 0, tip_px, hs_h)), (tip_w, bar_h))

        bar_rect = pygame.Rect(left_x, y, bar_w, bar_h)
        cut_rects = None
        if occlusion_rect is not None and bar_rect.colliderect(occlusion_rect):
            # Two clip windows either side of the occlusion rect — same
            # bar_rect clamped left/right, rather than two new rects with
            # their own caps. Only kept if they leave a sliver actually
            # still inside bar_rect (colliderect above already guarantees
            # at least one of these is non-empty).
            cut_rects = [
                pygame.Rect(bar_rect.left, bar_rect.top, max(0, min(bar_rect.right, occlusion_rect.left) - bar_rect.left), bar_rect.height),
                pygame.Rect(max(bar_rect.left, occlusion_rect.right), bar_rect.top, max(0, bar_rect.right - max(bar_rect.left, occlusion_rect.right)), bar_rect.height),
            ]
            cut_rects = [r for r in cut_rects if r.width > 0]

        prior_clip = screen.get_clip()

        def _blit_bar():
            screen.blit(hl, (left_x, y))
            screen.blit(hm, (left_x + tip_w, y))
            screen.blit(hr, (left_x + tip_w + mid_w, y))

        if cut_rects is None:
            _blit_bar()
        else:
            for clip_rect in cut_rects:
                screen.set_clip(clip_rect)
                _blit_bar()
            screen.set_clip(prior_clip)

    def _get_character_icon(self, character_id):
        """Small icon sprite for a character, shown left-to-right under a
        save slot's label on the in-game "Save Game" screen (see
        open_save_overlay / _draw_slot_character_row). Cached per
        character_id.

        Convention: assets/characters/<character_id>/icon.png — adjust
        this path if the project ends up keeping character icons
        somewhere else. Falls back to a plain tinted square with the
        character's first letter (same "never crash on a missing sprite"
        philosophy as SavePoint._load_sprite / TitleScreen's own menu
        sprite loaders) so a missing icon file never breaks the save
        screen."""
        if character_id in self._character_icon_cache:
            return self._character_icon_cache[character_id]

        icon = None
        path = os.path.join('assets', 'characters', character_id, 'icon.png')
        if os.path.exists(path):
            try:
                icon = pygame.image.load(path).convert_alpha()
            except Exception:
                icon = None

        if icon is None:
            size = 32
            icon = pygame.Surface((size, size), pygame.SRCALPHA)
            pygame.draw.rect(icon, (90, 90, 90), (0, 0, size, size), border_radius=4)
            pygame.draw.rect(icon, (200, 200, 200), (0, 0, size, size), 2, border_radius=4)
            letter = (character_id[:1] or '?').upper()
            if letter in self.role_font.glyphs or self.role_font.fallback_font:
                glyph = self.role_font.render(letter)
                gx = (size - glyph.get_width()) // 2
                gy = (size - glyph.get_height()) // 2
                icon.blit(glyph, (gx, gy))

        self._character_icon_cache[character_id] = icon
        return icon

    def _get_number_glyph(self, char):
        """Raw (unscaled) glyph for `char` from assets/ui/fonts/numbers —
        the dedicated numeric font for the "Save Game" screen's Play Time
        value (see _render_number_text / _draw_save_overlay_info), kept
        separate from the dmg_font digits _load_save_slot_digit_glyphs
        borrows for "Game 1"/"Game 2"/etc. Cached per character.

        Non-digit separators like ':' don't make valid filenames as
        themselves on every platform, so they're looked up under a
        plain-word filename instead (colon.png). Missing font folder or
        missing individual glyph file both just return None — same
        "never crash on a missing sprite" approach as everywhere else —
        so callers can skip that character rather than fail."""
        if char in self._numbers_glyph_cache:
            return self._numbers_glyph_cache[char]

        filenames = {':': 'colon', '/': 'slash', '-': 'dash'}
        filename = filenames.get(char, char)
        path = os.path.join('assets', 'ui', 'fonts', 'numbers', f'{filename}.png')

        glyph = None
        if os.path.exists(path):
            try:
                glyph = pygame.image.load(path).convert_alpha()
            except Exception:
                glyph = None

        self._numbers_glyph_cache[char] = glyph
        return glyph

    def _render_number_text(self, text, target_h, gap=2):
        """Composes `text` (digits, plus ':' etc.) into a single Surface
        using assets/ui/fonts/numbers, each character snapped to the
        same whole-number pixel-art scale via _scale_pixel_art so every
        glyph lands at (approximately) target_h — same "integer scale,
        no smearing" reasoning as the idle character icons — and lines
        up cleanly with whatever text surface it's being placed next to.
        Characters with no glyph on disk (see _get_number_glyph) are
        just skipped rather than breaking the whole string."""
        parts = []
        for ch in text:
            raw = self._get_number_glyph(ch)
            if raw is None:
                continue
            scaled, _ = self._scale_pixel_art(raw, target_h)
            parts.append(scaled)

        if not parts:
            return pygame.Surface((0, max(1, target_h)), pygame.SRCALPHA)

        max_h   = max(s.get_height() for s in parts)
        total_w = sum(s.get_width() for s in parts) + gap * (len(parts) - 1)
        out = pygame.Surface((total_w, max_h), pygame.SRCALPHA)
        x = 0
        for s in parts:
            out.blit(s, (x, max_h - s.get_height()))  # bottom-align
            x += s.get_width() + gap
        return out

    def _render_menu_text(self, pm, text, color):
        """Composes `text` into a single Surface using PauseMenu's own
        per-character menu_uppercase/menu_lowercase fonts — the exact
        fonts/technique Game._draw_saving_popup uses for "Saving..." —
        so text built with this (the Saga/Room/Time labels and values on
        the "Save Game" screen, see _draw_save_overlay_info) matches
        that popup's lettering, descenders included. Returns an empty
        surface if no PauseMenu is wired in yet (see set_pause_menu)."""
        if pm is None or not text:
            return pygame.Surface((0, 0), pygame.SRCALPHA)

        # Same per-letter descender nudge _draw_saving_popup uses for
        # "Saving..." — how far below the baseline each of these needs
        # to drop to read as a proper descender instead of a squashed-up
        # glyph. Without this, e.g. the 'g' in "Saga" or 'y' in a future
        # label would sit flush with the baseline like every other
        # letter instead of hanging below it.
        descenders = {'p': 8, 'q': 8, 'g': 8, 'y': 8, ',': 8}

        letter_spacing = pm.menu_uppercase_font.letter_spacing
        surfs = []
        for ch in text:
            font = pm.menu_uppercase_font if ch.isupper() else pm.menu_lowercase_font
            s = font.render(ch).copy()
            s.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
            surfs.append((ch, s))

        max_h    = max(s.get_height() for _, s in surfs)
        max_desc = max((descenders.get(ch, 0) for ch, _ in surfs), default=0)
        total_w  = sum(s.get_width() for _, s in surfs) + letter_spacing * (len(surfs) - 1)
        total_h  = max_h + max_desc

        out = pygame.Surface((total_w, total_h), pygame.SRCALPHA)
        x = 0
        for ch, s in surfs:
            oy = (max_h - s.get_height()) + descenders.get(ch, 0)
            out.blit(s, (x, oy))
            x += s.get_width() + letter_spacing
        return out

    @staticmethod
    def _scale_pixel_art(surface, target_height, min_scale=1):
        """Scale a pixel-art surface to (approximately) `target_height` by
        an integer factor, so every source pixel maps to an identically-
        sized block on screen instead of the uneven pixel sizes a
        fractional pygame.transform.scale() factor produces (some source
        pixel rows/columns get duplicated one extra time and others don't,
        which reads as visual noise/smearing on crisp pixel art).

        Picks the integer factor whose resulting height is closest to
        target_height (never less than min_scale), then scales both axes
        by that same factor. Returns (scaled_surface, actual_height) since
        the achieved height will generally differ slightly from the
        requested one — callers doing further layout math (e.g. bottom-
        aligning against a baseline) should use the returned height, not
        the original target_height.
        """
        raw_h = surface.get_height()
        if raw_h <= 0:
            return surface, surface.get_height()
        factor = max(min_scale, round(target_height / raw_h))
        new_w  = surface.get_width()  * factor
        new_h  = raw_h * factor
        return pygame.transform.scale(surface, (new_w, new_h)), new_h

    def _get_character_idle_icon(self, character_id, costume='base'):
        """First frame of <character_id>'s idle-facing-down animation, in
        `costume` — used by _draw_slot_character_row for whichever entry
        is the character currently being played, instead of that
        character's static icon.png, so the save-slot row shows their
        actual current form (base/transformed) rather than a fixed
        picture. Cached per (character_id, costume) pair.

        Reads the sheet the same way CharacterSpriteLoader does (see
        core.sprite_system.CharacterSpriteLoader.load_character): frame
        size comes from {folder}/sprite_size.txt if present, else the
        32x32 default, and 'down' is always row 0 of a standard
        4-directional sheet (DIRECTIONS_4 in core.sprite_system), so the
        first frame is just the sheet's own top-left corner — no need to
        spin up a full AnimatedSprite/Animation just to read one frame.

        Falls back to _get_character_icon's own icon.png/grey-square
        handling if idle.png doesn't exist yet for this character/
        costume — same "never crash on a missing sprite" approach as
        everywhere else here."""
        cache_key = (character_id, costume)
        if cache_key in self._character_idle_icon_cache:
            return self._character_idle_icon_cache[cache_key]

        folder = os.path.join('assets', 'sprites', 'player', character_id, costume)
        path   = os.path.join(folder, 'idle.png')

        icon = None
        if os.path.exists(path):
            try:
                from core.sprite_system import _load_sprite_size
                frame_w, frame_h = _load_sprite_size(folder)
                sheet = pygame.image.load(path).convert_alpha()
                icon  = sheet.subsurface(pygame.Rect(0, 0, frame_w, frame_h)).copy()
            except Exception:
                icon = None

        if icon is None:
            icon = self._get_character_icon(character_id)

        self._character_idle_icon_cache[cache_key] = icon
        return icon

    # Playback speed (frames per second) for the picked character's walk
    # cycle in _draw_slot_character_row — a lightweight, self-contained
    # frame-stepper driven off self._t, not the full gameplay
    # AnimatedSprite/Animation system (same "don't spin up more than this
    # needs" reasoning as _get_character_idle_icon's single-frame read).
    _CHARACTER_WALK_FPS = 8

    def _get_character_walk_down_frames(self, character_id, costume='base'):
        """All frames of <character_id>'s facing-down walk cycle, in
        `costume` — used by _draw_slot_character_row's show_picker branch
        so the character the player's currently picked to load as visibly
        walks in place instead of standing still on its idle frame.

        Reads walk.png the same way _get_character_idle_icon reads
        idle.png: frame size from {folder}/sprite_size.txt (via
        core.sprite_system._load_sprite_size, 32x32 default), and 'down'
        is row 0 of a standard 4-directional sheet — so the walk cycle is
        just that row's frames, sliced left to right by frame width.

        Falls back to a single-frame list wrapping
        _get_character_idle_icon's own icon.png/grey-square handling if
        walk.png doesn't exist yet for this character/costume, so a
        character with no walk sheet still draws something (standing
        still) instead of crashing or going blank.

        Cached per (character_id, costume) pair, same as
        _character_idle_icon_cache."""
        cache_key = (character_id, costume)
        if cache_key in self._character_walk_frame_cache:
            return self._character_walk_frame_cache[cache_key]

        folder = os.path.join('assets', 'sprites', 'player', character_id, costume)
        path   = os.path.join(folder, 'walk.png')

        frames = None
        if os.path.exists(path):
            try:
                from core.sprite_system import _load_sprite_size
                frame_w, frame_h = _load_sprite_size(folder)
                sheet = pygame.image.load(path).convert_alpha()
                frame_count = max(1, sheet.get_width() // frame_w)
                frames = [
                    sheet.subsurface(pygame.Rect(i * frame_w, 0, frame_w, frame_h)).copy()
                    for i in range(frame_count)
                ]
            except Exception:
                frames = None

        if not frames:
            frames = [self._get_character_idle_icon(character_id, costume)]

        self._character_walk_frame_cache[cache_key] = frames
        return frames

    # Nudges the whole character-icon row from its default centered
    # position — negative ROW_OFFSET_X moves it left, positive moves it
    # right; negative ROW_OFFSET_Y moves it up, positive moves it down.
    # Tune these directly; nothing else on this screen depends on them.
    _CHARACTER_ROW_OFFSET_X = -420
    _CHARACTER_ROW_OFFSET_Y = 104
    # The idle-down sprite (_get_character_idle_icon) is a full-body
    # frame, not a square icon like the icon.png entries next to it, so
    # it reads much smaller at the same pixel height. This multiplies
    # just that one entry's height on top of the row's normal icon_h —
    # bump it up/down to taste without touching icon.png-based entries.
    _CHARACTER_ROW_IDLE_SCALE = 3

    # Flattened ground-shadow drawn under each entry in the SAVE SELECT
    # character row (see _draw_character_row_shadow) — same
    # assets/sprites/universal/shadow.png (or shadowbig.png) sprite and
    # the same "~32% of the sprite's own width" scale used by the real
    # in-game shadow (LayerManager._get_scaled_shadow), just applied to
    # this row's already-enlarged icon width instead of an
    # entity_width * RENDER_SCALE figure, so it scales right alongside
    # however big _CHARACTER_ROW_IDLE_SCALE has made the character.
    # Y_OFFSET nudges it up (negative) / down (positive) from the
    # sprite's bottom edge — tune this one directly, same as
    # LayerManager's own shadow_y_offset knob does per-entity in-game.
    _CHARACTER_ROW_SHADOW_WIDTH_RATIO = 0.285
    _CHARACTER_ROW_SHADOW_Y_OFFSET    =  -5

    def _get_scaled_row_shadow(self, sprite_width, big=False):
        """Cached shadow sprite scaled to _CHARACTER_ROW_SHADOW_WIDTH_RATIO
        of sprite_width (the character icon's own on-screen width), aspect
        ratio preserved — same shape as LayerManager._get_scaled_shadow,
        just keyed/cached locally since this row has its own sprite sizes."""
        source = self._row_shadow_sprite_big if big else self._row_shadow_sprite
        if source is None:
            return None
        key = (sprite_width, big)
        if key not in self._row_shadow_cache:
            orig_w = source.get_width()
            orig_h = source.get_height()
            target_w = max(8, int(sprite_width * self._CHARACTER_ROW_SHADOW_WIDTH_RATIO))
            target_h = max(4, int(orig_h * target_w / orig_w)) + 1
            self._row_shadow_cache[key] = pygame.transform.scale(source, (target_w, target_h))
        return self._row_shadow_cache[key]

    def _draw_character_row_shadow(self, screen, center_x, bottom_y, sprite_width, big=False):
        """Ground shadow under a SAVE SELECT row entry, centered
        horizontally on the sprite and sitting just above its bottom
        edge — same draw shape as LayerManager._draw_shadow, minus the
        camera/world-position math that one needs and this screen-space
        UI row doesn't. center_x: horizontal center of the sprite above
        it. bottom_y: the sprite's bottom edge (pre Y_OFFSET). big: True
        to use shadowbig.png instead of shadow.png (mirrors an entity's
        own shadow_size == 'big' in-game)."""
        shadow_surf = self._get_scaled_row_shadow(sprite_width, big=big)
        if shadow_surf is None:
            return
        shadow_x = round(center_x - shadow_surf.get_width() // 2)
        shadow_y = round(bottom_y - shadow_surf.get_height() // 2 + self._CHARACTER_ROW_SHADOW_Y_OFFSET)
        screen.blit(shadow_surf, (shadow_x, shadow_y))

    # Saga can't be named/selected anywhere in the game yet — its line
    # on the "Save Game" screen (_draw_save_overlay_info) always shows
    # this instead of a real value, same spot a real saga name would
    # occupy once that system exists.
    _SAVE_OVERLAY_SAGA_PLACEHOLDER = 'Not Set'

    def _draw_slot_character_row(self, screen, characters, center_x, y, band_h,
                                  current_character=None, current_costume='base',
                                  picked_character=None, show_picker=False):
        """Draws `characters` (a list of character_id strings) as icons
        left to right, horizontally centered on center_x (offset by
        _CHARACTER_ROW_OFFSET_X/Y), vertically centered in a band_h-tall
        band starting at y. No-ops (draws nothing) if characters is
        falsy — used by _draw_save_slot_list for slots with no character
        data on the "Save Game" overlay.

        current_character: if one of `characters` matches this id, that
        one entry is drawn using current_costume; every entry (current or
        not) is drawn from its own live idle-down sprite via
        _get_character_idle_icon — falling back to icon.png/grey square
        internally only if that character has no idle.png yet — so a
        second, third, etc. unlocked character shows their actual sprite
        here instead of a placeholder grey box.

        show_picker: True only from the real (non-overlay) SAVE SELECT
        list (see _draw_save_slot_list) — the in-game "Save Game" overlay
        (open_save_overlay) always leaves this False and renders exactly
        as before. When True, whichever entry matches picked_character
        (falling back to current_character if picked_character is None)
        is drawn walking in place through its down-facing walk cycle (see
        _get_character_walk_down_frames) instead of standing still on its
        idle frame, with select.png (see _load_select_overlay)
        overlaid on top of it — this is the entry the player's chosen to
        load as (see _picked_character_for_slot/_cycle_picked_character).
        Every other entry in the row still just shows its static idle
        icon, same as always.

        Layout: the first character's position is anchored exactly the
        way it always was when it was the only entry in the row (centered
        on center_x, then nudged by _CHARACTER_ROW_OFFSET_X/Y) — it does
        NOT shift when more characters are added. Additional characters
        are simply appended to its right, left to right, so the "current"
        sprite's placement stays fixed regardless of roster size."""
        if not characters:
            return

        if picked_character is None:
            picked_character = current_character

        icon_h = max(16, int(band_h * 1.6))
        requested_h = int(icon_h * self._CHARACTER_ROW_IDLE_SCALE)

        select_scaled = None
        if show_picker and self._select_overlay_icon is not None:
            # Same whole-number pixel-art scaling as the character icons
            # themselves, so the overlay's pixels stay crisp alongside
            # them rather than being fractionally stretched/smeared.
            select_scaled, _ = self._scale_pixel_art(self._select_overlay_icon, requested_h)

        scaled = []
        for character_id in characters:
            costume   = current_costume if character_id == current_character else 'base'
            is_picked = show_picker and character_id == picked_character
            if is_picked:
                # Step through the walk cycle off the menu's own running
                # clock (self._t, advanced each frame in update()) — same
                # "everyone reads the same clock" approach as the option
                # box's blinking selector arrow.
                frames  = self._get_character_walk_down_frames(character_id, costume)
                frame_i = int(self._t * self._CHARACTER_WALK_FPS) % len(frames)
                icon    = frames[frame_i]
            else:
                icon = self._get_character_idle_icon(character_id, costume)
            # The idle/walk icon is a raw pixel-art sprite frame, so it's
            # scaled with _scale_pixel_art (snaps to a whole-number
            # factor) rather than a fractional pygame.transform.scale,
            # which would otherwise duplicate some source pixel rows/
            # columns more than others and read as "smeared" pixel art.
            scaled_icon, _ = self._scale_pixel_art(icon, requested_h)
            scaled.append((scaled_icon, is_picked))

        gap   = max(4, int(icon_h * 0.15)) - 60
        # Anchor on the FIRST icon alone — same math as when the row only
        # ever had one entry — instead of centering the whole row on its
        # combined width, so the first (current) character never moves as
        # more characters get added; they just extend to its right.
        x     = center_x - scaled[0][0].get_width() // 2 + self._CHARACTER_ROW_OFFSET_X
        row_y = y + max(0, (band_h - icon_h) // 2) + self._CHARACTER_ROW_OFFSET_Y
        for s, is_picked in scaled:
            # Bottom-align each icon on the row's normal icon_h baseline
            # so every enlarged idle sprite grows upward from the same
            # ground line, instead of centering on a taller box and
            # drifting down into the text below it.
            blit_y = row_y + (icon_h - s.get_height())
            # Shadow first, so the sprite draws on top of it (same
            # draw-order as the real in-game shadow — see
            # _draw_character_row_shadow).
            self._draw_character_row_shadow(
                screen, x + s.get_width() // 2, blit_y + s.get_height(), s.get_width()
            )
            screen.blit(s, (x, blit_y))
            if is_picked and select_scaled:
                # Centered on top of the picked entry's own icon, not the
                # row/band as a whole, so it stays correctly aligned
                # regardless of where LEFT/RIGHT has moved the pick to.
                sel_x = x + (s.get_width()  - select_scaled.get_width())  // 2
                sel_y = blit_y + (s.get_height() - select_scaled.get_height()) // 2
                screen.blit(select_scaled, (sel_x, sel_y))
            x += s.get_width() + gap

    # Gap between the Saga/Room/Time lines on the "Save Game" screen —
    # tune this directly, nothing else depends on it. Row spacing/the
    # divider position never depend on this — see _draw_save_slot_list,
    # which always sizes rows off sub_h alone regardless of this value.
    _SAVE_OVERLAY_INFO_LINE_GAP = 16

    def _draw_save_overlay_info(self, screen, pm, box_x, inner_w, top_y, color,
                                 occlusion_rect=None, room_name=None, play_time=None):
        """Saga / Room / Play Time, top to bottom — what now sits where
        the "New Game" line used to be once the "Save Game" overlay is
        active (see the self._save_overlay_active branch in
        _draw_save_slot_list), each centered horizontally in the frame
        the same way "New Game" was.

        Labels ("Saga: ", "Room: ") and the Saga/Room values are built
        with _render_menu_text — PauseMenu's own menu font, matching
        Game._draw_saving_popup's "Saving..." text. The Play Time line
        has no label, just its value (e.g. "0:00"), built entirely with
        _render_number_text (assets/ui/fonts/numbers).

        Saga can't be set anywhere in the game yet (see
        _SAVE_OVERLAY_SAGA_PLACEHOLDER), so that line's "value" is
        always the same placeholder text rather than real save data.

        room_name / play_time: defaults to self._save_overlay_room_name /
        self._save_overlay_play_time (the actively-being-saved slot) when
        left None. The normal (non-overlay) SAVE SELECT list passes a
        specific slot's own values here instead, via
        _get_save_slot_summary, so this same renderer can show any
        occupied slot's info — not just the one currently being saved.

        occlusion_rect: same self._save_popup_occlusion_rect
        _draw_save_slot_divider clips around (see that function's own
        docstring) — the "Saving..." popup's box, set each frame by
        Game via set_save_popup_occlusion_rect. This text sits behind
        that popup, not through it, so any part of a line that falls
        under it is skipped the same left/right-split way the divider
        clips itself."""
        if pm is None:
            return

        if room_name is None:
            room_name = self._save_overlay_room_name
        if play_time is None:
            play_time = self._save_overlay_play_time

        total_seconds = max(0, int(play_time))
        hh, rem = divmod(total_seconds, 3600)
        mm, _ss = divmod(rem, 60)
        # No seconds, no leading zero on the hour, e.g. "0:00".
        time_str = f'{hh}:{mm:02d}'

        lines = [
            ('Saga: ', self._SAVE_OVERLAY_SAGA_PLACEHOLDER, False),
            ('Room: ', room_name or '???', False),
            # No "Time: " label on this line anymore — just the value.
            ('', time_str, True),
        ]

        # Reference height for the Time line's digits — since that line
        # no longer has a label to size off of, use the "Room: " label's
        # height instead so the numbers font renders at the same scale
        # as the Saga/Room lines rather than shrinking to almost nothing.
        ref_h = max(1, self._render_menu_text(pm, 'Room: ', color).get_height())

        # Build every line's surfaces up front so we can find one common
        # left edge for the whole block instead of centering each line
        # on its own width (which left Saga/Room/Time each starting at a
        # different x — a ragged column instead of an aligned one).
        built = []
        max_total_w = 0
        for label, value, is_numeric in lines:
            label_surf = self._render_menu_text(pm, label, color)
            if is_numeric:
                # Same per-letter gap the Saga/Room labels/values use (see
                # _render_menu_text) — _render_number_text defaults to its
                # own tighter gap otherwise, which read as uneven next to
                # the label lines above it.
                value_surf = self._render_number_text(value, ref_h, gap=pm.menu_uppercase_font.letter_spacing)
                # The numbers font is its own asset, but every other line
                # here gets tinted to the row's white/hover color (see
                # _render_menu_text), so tint the digits to match too —
                # same treatment _draw_save_slot_list gives the borrowed
                # dmg_font digits in "Game 1"/"Game 2"/etc.
                value_surf = value_surf.copy()
                value_surf.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
            else:
                value_surf = self._render_menu_text(pm, value, color)

            total_w = label_surf.get_width() + value_surf.get_width()
            max_total_w = max(max_total_w, total_w)
            built.append((label_surf, value_surf))

        # Same centering formula the old "New Game" line used
        # (box_x + (inner_w - width) // 2), just applied once to the
        # widest of the three lines rather than per-line — that shared x
        # is then reused for every line so Saga/Room/Time all line up
        # under one another.
        block_x = box_x + (inner_w - max_total_w) // 2

        y = top_y
        for label_surf, value_surf in built:
            line_h = max(label_surf.get_height(), value_surf.get_height(), 1)
            x      = block_x

            for surf in (label_surf, value_surf):
                shadow = surf.copy()
                shadow.fill(self.text_shadow_color, special_flags=pygame.BLEND_RGBA_MULT)
                oy = line_h - surf.get_height()
                self._blit_occluded(screen, shadow, (x + self.shadow_offset[0], y + oy + self.shadow_offset[1]), occlusion_rect)
                self._blit_occluded(screen, surf, (x, y + oy), occlusion_rect)
                x += surf.get_width()

            y += line_h + self._SAVE_OVERLAY_INFO_LINE_GAP

    def _blit_occluded(self, screen, surf, pos, occlusion_rect):
        """screen.blit(surf, pos), except any part of surf that falls
        under occlusion_rect is skipped instead of drawn — same
        left/right clip-window split _draw_save_slot_divider uses to
        keep its bar from drawing through the "Saving..." popup, just
        generalized to an arbitrary surface instead of a horizontal bar.
        occlusion_rect=None (nothing to clip around, e.g. no popup up
        right now) just blits normally."""
        if not surf.get_width() or not surf.get_height():
            return
        rect = pygame.Rect(pos, surf.get_size())
        if occlusion_rect is None or not rect.colliderect(occlusion_rect):
            screen.blit(surf, pos)
            return

        prior_clip = screen.get_clip()
        cut_rects = [
            pygame.Rect(rect.left, rect.top, max(0, min(rect.right, occlusion_rect.left) - rect.left), rect.height),
            pygame.Rect(max(rect.left, occlusion_rect.right), rect.top, max(0, rect.right - max(rect.left, occlusion_rect.right)), rect.height),
        ]
        for cr in cut_rects:
            if cr.width <= 0:
                continue
            screen.set_clip(cr)
            screen.blit(surf, pos)
        screen.set_clip(prior_clip)

    def _menu_label_surfs(self, pm, text, color=(255, 255, 0)):
        """Per-character surfaces for a single-word footer label (A-Select/
        B-Cancel), using PauseMenu's own menu_uppercase/menu_lowercase
        fonts — mirrors the _menu_surfs() closure PauseMenu.draw() defines
        for its own A/B footer."""
        surfs = []
        for ch in text:
            s = (pm.menu_uppercase_font if ch.isupper() else pm.menu_lowercase_font).render(ch)
            s = s.copy(); s.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
            surfs.append(s)
        return surfs

    def _blit_menu_label(self, screen, surfs, x, y, spacing=0):
        max_h = max(s.get_height() for s in surfs)
        cx = x
        for s in surfs:
            shadow = s.copy(); shadow.fill((0, 0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            oy = max_h - s.get_height()
            screen.blit(shadow, (cx + 1, y + oy + 1))
            screen.blit(s, (cx, y + oy))
            cx += s.get_width() + spacing

    def _draw_save_select_title(self, screen, pm, box_x, inner_w, lr_y):
        """Centered "Select Game" / "Delete Game" title above the frame,
        same font/shadow treatment as PauseMenu's own tab title (see
        PauseMenu.draw's centre_label) — split word-by-word with a wider
        gap between them, since PauseMenu's own tab titles ("Status",
        "Inventory", ...) are always a single word and never need that.

        L/R (see _handle_save_select_keydown) toggles self._save_delete_mode,
        which swaps the text to "Delete Game" — stays yellow, same as
        "Select Game"; only the selected row's color changes in delete
        mode (see _draw_save_slot_list)."""
        letter_gap = 5
        word_gap   = 16
        if self._save_overlay_active:
            title_text = 'Save Game'
        else:
            title_text = 'Delete Game' if self._save_delete_mode else 'Select Game'

        def _word_surfs(word):
            surfs = []
            for ch in word:
                s = (pm.bold_font if ch.isupper() else pm.bold_lowercase_font).render(ch)
                s = s.copy(); s.fill((255, 255, 0), special_flags=pygame.BLEND_RGBA_MULT)
                surfs.append(s)
            return surfs

        words   = [_word_surfs(w) for w in title_text.split(' ')]
        word_ws = [sum(s.get_width() for s in w) + letter_gap * (len(w) - 1) for w in words]
        total_w = sum(word_ws) + word_gap * (len(words) - 1)
        max_h   = max(s.get_height() for w in words for s in w)

        label_x = box_x + (inner_w - total_w) // 2
        label_y = lr_y - 16
        for wi, surfs in enumerate(words):
            for s in surfs:
                shadow = s.copy(); shadow.fill((0, 0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                oy = max_h - s.get_height()
                screen.blit(shadow, (label_x + 1, label_y + oy + 1))
                screen.blit(s, (label_x, label_y + oy))
                label_x += s.get_width() + letter_gap
            label_x += word_gap - letter_gap

    def _render_label(self, font, text):
        """Like font.render(), but words are packed closer together than
        the font's built-in space width (fine for single words like STR/
        DEF elsewhere, but too wide for a two-word menu label like
        "SINGLE PLAYER"). Falls back straight to font.render() for
        single-word labels."""
        words = text.upper().split(' ')
        if len(words) == 1:
            return font.render(text)
        word_gap = int(3 * self.menu_font_scale)
        word_surfs = [font.render(w) for w in words]
        total_w = sum(s.get_width() for s in word_surfs) + word_gap * (len(word_surfs) - 1)
        max_h = max(s.get_height() for s in word_surfs)
        surf = pygame.Surface((total_w, max_h), pygame.SRCALPHA)
        x = 0
        for s in word_surfs:
            surf.blit(s, (x, max_h - s.get_height()))
            x += s.get_width() + word_gap
        return surf

    def _draw_option_box(self, screen):
        """The ~40%-opacity, lower-centered option box shared by the
        START/OPTIONS page and the SINGLE PLAYER/MULTIPLAYER page — same
        box, same position, just different labels (see _current_options).
        Selection is shown with the pause menu's own selector arrow to the
        left of the highlighted label, matching the rest of the game's UI."""
        options  = self._current_options()
        surfs    = [self._render_label(self.role_font, label) for _, label in options]
        line_h   = max(s.get_height() for s in surfs)
        line_gap = int(line_h)

        # Box is always sized off the START/OPTIONS page's labels (not
        # whichever page is showing), so it stays the same, START/OPTIONS-
        # sized box on both pages instead of growing for the longer
        # SINGLE PLAYER/MULTIPLAYER labels.
        all_surfs   = [self._render_label(self.role_font, label) for label in _MAIN_OPTION_LABELS]
        max_label_w = max(s.get_width() for s in all_surfs)

        arrow_src = self._pause_menu.equip_arrow if self._pause_menu else None
        arrow_scaled = None
        if arrow_src:
            # Same integer scale as the menu text font (menu_font_scale) —
            # not a ratio to line_h — so one arrow source pixel renders at
            # exactly the same screen size as one font source pixel.
            arrow_scale = max(1, self.menu_font_scale)
            arrow_scaled = pygame.transform.scale(
                arrow_src,
                (arrow_src.get_width() * arrow_scale, arrow_src.get_height() * arrow_scale)
            )

        pad_x = int(36 * RENDER_SCALE)
        pad_y = int(14 * RENDER_SCALE)
        box_w = max_label_w + pad_x * 2
        box_h = line_h * len(options) + line_gap * (len(options) - 1) + pad_y * 2
        box_x = (self.screen_width - box_w) // 2
        box_y = int(self.screen_height * _BOX_Y_RATIO)

        box_surf = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        box_surf.fill((0, 0, 0, 102))   # ~40% opacity
        screen.blit(box_surf, (box_x, box_y))

        # Labels are individually centered within the box (equal padding on
        # both sides), rather than all left-aligned at a shared x — so a
        # short label like "BACK" sits centered under a longer one above it.
        # The arrow sits just to the left of whichever label is selected,
        # instead of reserving its own gutter in the box width, which is
        # what was throwing the box off-center relative to the text.
        label_area_x = box_x + pad_x
        label_area_w = box_w - pad_x * 2

        self._option_hit_rects = []
        y = box_y + pad_y
        for i, ((key, _), surf) in enumerate(zip(options, surfs)):
            is_selected = (i == self._menu_index)

            color = self.text_hover_color if is_selected else self.text_color
            if key == 'multi' and self._denied_flash_t > 0:
                color = (255, 60, 60)

            colored = surf.copy()
            colored.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
            shadow = colored.copy()
            shadow.fill(self.text_shadow_color, special_flags=pygame.BLEND_RGBA_MULT)

            # Center on the glyphs' actual visible pixels (bounding_rect),
            # not the raw surface size — bitmap-font tiles can carry
            # different blank padding per starting letter (e.g. "S" vs
            # "O"), so surf.get_width() alone put the visible text off
            # from true-center and threw off the arrow gap per row.
            bbox = surf.get_bounding_rect()
            text_x = label_area_x + (label_area_w - bbox.width) // 2 - bbox.left
            visual_left = text_x + bbox.left
            screen.blit(shadow, (text_x + self.shadow_offset[0], y + self.shadow_offset[1]))
            screen.blit(colored, (text_x, y))

            self._option_hit_rects.append(pygame.Rect(box_x, y, box_w, line_h))

            if is_selected and arrow_scaled:
                # Same on/off blink as the pause menu's cursor arrow —
                # self._t is the menu's running clock, advanced each frame
                # in update().
                blink_on = (self._t % (_ARROW_BLINK_INTERVAL * 2)) < _ARROW_BLINK_INTERVAL
                if blink_on:
                    arrow_y = y + (line_h - arrow_scaled.get_height()) // 2
                    arrow_gap = int(1) - 1
                    screen.blit(arrow_scaled, (visual_left - arrow_scaled.get_width() - arrow_gap, arrow_y))

            y += line_h + line_gap


def _fit_contain(img, target_w, target_h):
    """Scales an intro picture to fit inside the screen without cropping or
    stretching (letterboxed on transparent/black if its aspect ratio
    doesn't match the screen's), then pads it onto a full-screen surface
    so every intro image can be blitted/centered/cross-faded identically
    regardless of its original size."""
    iw, ih = img.get_size()
    if iw <= 0 or ih <= 0:
        return pygame.Surface((target_w, target_h), pygame.SRCALPHA)
    scale = min(target_w / iw, target_h / ih)
    new_w, new_h = max(1, int(iw * scale)), max(1, int(ih * scale))
    scaler = getattr(pygame.transform, 'smoothscale', pygame.transform.scale)
    scaled = scaler(img, (new_w, new_h))
    canvas = pygame.Surface((target_w, target_h), pygame.SRCALPHA)
    canvas.blit(scaled, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return canvas