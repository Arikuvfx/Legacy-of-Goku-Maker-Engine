"""Clean, functional in-game developer tool launcher.

Designed for the engine window itself: no separate GUI window and no external
UI dependency.  The menu keeps the existing tool/action mapping but presents it
as a compact editor-style launcher with clear hierarchy, mouse + keyboard
navigation, subtle motion, and a small information footer.
"""

import math
import os
import pygame

import dev_tools.ui_kit as uk
from config.settings import (SCREEN_WIDTH, SCREEN_HEIGHT, load_display_settings,
                             display_resolution_key, effective_resolution)


CATEGORIES = [
    ('room_editor', 'Room Editor', 'Edit rooms, tiles, collisions and objects.', 'room', (244, 190, 76)),
    ('world_map_editor', 'World Map Editor', 'Build the world map and room connections.', 'map', (244, 190, 76)),
    ('sprite_editor', 'Sprite Editor', 'Inspect frames, animations and sprite sheets.', 'sprite', (244, 190, 76)),
    ('cutscene_editor', 'Cutscene Editor', 'Create timelines, dialogue and scene events.', 'cutscene', (244, 190, 76)),
    ('character_creator', 'Character Creator', 'Configure stats, attacks and character data.', 'character', (244, 190, 76)),
    ('attack_creator', 'Attack Creator', 'Create and tune attacks and projectiles.', 'attack', (244, 190, 76)),
    ('entity_creator', 'Entity Creator', 'Configure enemies, NPCs and other entities.', 'entity', (244, 190, 76)),
    ('item_creator', 'Item Creator', 'Create item definitions and item behaviour.', 'item', (244, 190, 76)),
    ('decoration_creator', 'Decoration Creator', 'Place and configure world decorations.', 'decoration', (244, 190, 76)),
]

ACTION_MAP = {item_id: f'open_{item_id}' for item_id, *_ in CATEGORIES}
COLUMNS = 3

# main<->config slide transition, played both directions: opening settings
# sends the category cards flying off to the left, cascading column by
# column, while the config list flies in from the right as one block;
# leaving settings reverses the same motion. Each card's own slide is a
# fixed-length, fixed-speed ease-in-out (CARD_SLIDE_SECONDS) - only its
# *start time* is staggered by column (CARD_STAGGER_SECONDS apart) - so
# every column moves the same way and none of them have to visibly rush to
# catch up with the others.
CARD_SLIDE_SECONDS = 0.26
CARD_STAGGER_SECONDS = 0.05
CONFIG_SLIDE_SECONDS = 0.30
# Wall-clock length of the whole transition (drives when input unblocks
# and _prev_view clears) - whichever finishes last, the config panel or
# the final (most-delayed) column.
TRANSITION_SECONDS = max(CONFIG_SLIDE_SECONDS, CARD_STAGGER_SECONDS * (COLUMNS - 1) + CARD_SLIDE_SECONDS)


def _ease_in_out(t):
    """Smoothstep: zero velocity at both t=0 and t=1, unlike uk.ease_out
    (which starts at full speed). Used for the transition above so a
    delayed column's motion ramps up gently from rest instead of snapping
    straight into full speed the instant its stagger delay elapses."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)

# Kept for backward compatibility with any external code that still imports
# it; no longer returned by handle_input(). Configuration used to be a
# separate overlay game.py opened on its own — it's now a screen inside
# DevMenu itself (see _view / CONFIG_OPTIONS below), styled with the same
# ui_kit cards as the main launcher.
CONFIG_ACTION = 'open_configuration'

# Live-editable numeric settings shown on the CONFIGURATION screen. Empty for
# now (mirrors the old dev menu's "contents removed for now" state) — add
# entries here to bring a field back, e.g.:
#   {'id': 'walk_speed', 'label': 'WALK SPEED', 'icon': 'config', 'value_key': 'walk_speed'}
# 'value_key' is read straight off `game_config` via getattr/setattr, and the
# live value is appended to the label automatically.
#
# Display entries use 'display' instead of 'value_key'; activating one returns
# an action string for game.py to run (see Game._handle_dev_menu_action):
#   'mode'       cycles BORDERLESS / FULLSCREEN / WINDOWED, applied live
#   'resolution' cycles NATIVE and the standard 16:9 sizes that fit this
#                desktop; saved, takes effect on the next launch
#   'original_ratio' toggles pillarboxing the test-room view to the original
#                game's 240x160 (3:2) ratio, applied live
#   'apply'      shown only when the saved resolution differs from the
#                running one; saves and relaunches the game
CONFIG_OPTIONS = [
    # 'page' entries open a sub-page of the CONFIGURATION screen instead of
    # doing something themselves (the page's entries live in the table below).
    {'id': 'graphics', 'label': 'GRAPHICS', 'page': 'graphics'},
]
# Entries shown on the GRAPHICS page (CONFIGURATION > GRAPHICS).
GRAPHICS_OPTIONS = [
    {'id': 'display_mode',       'label': 'WINDOW MODE',       'display': 'mode'},
    {'id': 'display_resolution', 'label': 'RESOLUTION',        'display': 'resolution'},
    {'id': 'display_original_ratio', 'label': 'ORIGINAL RATIO (TEST ROOMS)', 'display': 'original_ratio'},
    {'id': 'display_apply',      'label': 'APPLY AND RESTART', 'display': 'apply'},
]
_CONFIG_PAGES = {'root': CONFIG_OPTIONS, 'graphics': GRAPHICS_OPTIONS}
_CONFIG_PAGE_TITLES = {'root': 'CONFIGURATION', 'graphics': 'GRAPHICS'}
_DISPLAY_ACTIONS = {
    'mode':       'display_cycle_mode',
    'resolution': 'display_cycle_resolution',
    'original_ratio': 'display_toggle_original_ratio',
    'apply':      'restart_game',
}


class _BitmapFontView:
    """Adapts a BitmapFont to the plain pygame.font.Font call shape —
    render(text, antialias, color) / size(text) — at one fixed pixel height.

    ui_kit's ModalTextInput was written against that shape so it can be
    reused with either a real pygame.font.Font or a bitmap font like the one
    this menu uses. BitmapFont.render()'s signature already matches
    positionally (text, antialias, color, height=None), but without a height
    it renders at native (tiny) glyph size, so this wrapper pins a height per
    field the same way the card labels do."""

    def __init__(self, bitmap_font, height):
        self._font = bitmap_font
        self._height = height

    def render(self, text, antialias=True, color=(255, 255, 255)):
        return self._font.render(text, color=color, height=self._height)

    def size(self, text):
        return self._font.size(text, height=self._height)


class DevMenu:
    """In-game developer launcher with a compact, reusable layout."""

    def __init__(self, game_config, screen_width, screen_height, sound_manager=None,
                 renderer=None, window=None):
        self.config = game_config
        self.screen_width = int(screen_width)
        self.screen_height = int(screen_height)
        self.sound_manager = sound_manager
        self.renderer = renderer
        self.window = window
        self._native_mode = renderer is not None and window is not None
        self._logical_size_backup = None

        self.active = False
        self.previous_context = None
        self._last_input = 'mouse'
        self._view = 'main'  # 'main' or 'config'
        self._config_page = 'root'  # 'root' or 'graphics' (sub-page of 'config')

        # main<->config slide transition. _prev_view is the view we're
        # sliding away from; None means settled (no transition playing).
        # _view already flips to the destination the instant the switch is
        # requested (so input routing/hit-testing are correct immediately);
        # _prev_view+_transition_elapsed are purely what draw() uses to
        # still render the outgoing screen sliding out while the new one
        # slides in. See _card_offset/_config_offset.
        self._prev_view = None
        self._transition_elapsed = 0.0

        self._bg_tile = self._load_background(alpha=40)
        self._bg_offset = [0.0, 0.0]
        self._bg_speed = (16.0, 10.0)  # px/sec, diagonal drift

        self.font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)

        # "DEV TOOLS" uses a different glyph set than the card menu font -
        # BitmapFont always looks under <root>/uppercase_menu and
        # <root>/lowercase_menu, so point a second instance straight at the
        # plain uppercase/lowercase folders instead.
        self.title_font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self.title_font.uppercase_dir = os.path.join('assets', 'ui', 'fonts', 'uppercase')
        self.title_font.lowercase_dir = os.path.join('assets', 'ui', 'fonts', 'lowercase')
        self.hover_index = -1
        self.selected_index = 0
        self._card_rects = []
        self._hover_anim = [0.0] * len(CATEGORIES)
        self._focus_anim = [0.0] * len(CATEGORIES)
        self._pulse = 0.0
        self._icons = [self._load_icon(item[3], item[4], 50) for item in CATEGORIES]
        self._title_surf_main = None
        self._title_surfs_config = {}
        self._card_label_cache = [None] * len(CATEGORIES)

        # Config button, top-right of the header bar next to "DEV TOOLS".
        self._config_icon = self._load_icon('config', (244, 190, 76), 34)
        self._config_rect = pygame.Rect(0, 0, 0, 0)
        self._config_hovered = False
        self._config_hover_anim = 0.0

        # Back button, top-left of the header bar - only shown while inside
        # the CONFIGURATION screen, mirroring the gear button on the
        # opposite side. Uses the same PNG-icon loading path as the gear
        # icon (assets/ui/dev_menu/icons/back.png).
        self._back_icon = self._load_icon('back', (244, 190, 76), 34)
        self._back_rect = pygame.Rect(0, 0, 0, 0)
        self._back_hovered = False
        self._back_hover_anim = 0.0

        self._layout()

        # Configuration screen: a single-column ui_kit card list (BACK plus
        # whatever's in CONFIG_OPTIONS), reusing the same card visuals as
        # the main grid, and a modal text input for editing a value.
        self._config_menu = uk.IconGridMenu(
            items=[],
            columns=1,
            card_size=self._config_card_size(),
            gap=(0, 20),
            font_label=self.font,
            font_label_hi=self.font,
            icon_size=40,
            font_label_size=self.card_title_size,
            font_label_hi_size=self.card_title_size + 2,
        )
        self._text_input = uk.ModalTextInput(
            font_prompt=_BitmapFontView(self.font, self.subtitle_size),
            font_input=_BitmapFontView(self.font, self.card_title_size),
            font_hint=_BitmapFontView(self.font, self.card_body_size),
        )
        self._refresh_config_items()

    def _load_background(self, alpha=40, scale=5):
        """Load the dev-menu background art once, zoom it in by `scale` so
        the tile repeat isn't obvious on screen, and bake the low opacity
        in - drawing it every frame is then a single cheap blit instead of
        re-applying alpha or scaling each time."""
        path = os.path.join('assets', 'ui', 'dev_menu', 'background.png')
        try:
            raw = pygame.image.load(path).convert_alpha()
        except (FileNotFoundError, pygame.error):
            return None

        if scale and scale != 1.0:
            w, h = raw.get_size()
            nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
            if float(scale).is_integer():
                # Whole-number enlargement - point sampling keeps edges
                # crisp instead of the interpolation smoothscale does,
                # same reasoning as the icon fix in _load_icon.
                raw = pygame.transform.scale(raw, (nw, nh))
            else:
                raw = pygame.transform.smoothscale(raw, (nw, nh))

        tile = raw.copy()
        # RGBA_MULT by (255,255,255,alpha) leaves color untouched and scales
        # alpha down to alpha/255 - same trick used for icon/glow fades.
        tile.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
        return tile

    def _load_icon(self, icon_key, accent, box_size):
        """Load an existing dev-menu PNG and fit it into one consistent
        visual box without distorting its aspect ratio."""
        path = os.path.join('assets', 'ui', 'dev_menu', 'icons', f'{icon_key}.png')
        try:
            raw = pygame.image.load(path).convert_alpha()
        except (FileNotFoundError, pygame.error):
            return pygame.Surface((box_size, box_size), pygame.SRCALPHA)

        # The source PNGs don't all crop their transparent margin the same
        # way - one icon might be drawn edge-to-edge in its canvas while
        # another (e.g. the map) has extra empty padding baked in around
        # the artwork. Scaling off the raw canvas size (iw, ih) treats that
        # padding as if it were part of the icon, so a padded icon's actual
        # artwork ends up visibly smaller than a tightly-cropped icon even
        # though both canvases hit the same box_size. Crop to the actual
        # drawn pixels first so the scale is always based on real content,
        # not incidental empty space in the file.
        content_rect = raw.get_bounding_rect(min_alpha=1)
        if content_rect.width <= 0 or content_rect.height <= 0:
            content_rect = raw.get_rect()
        raw = raw.subsurface(content_rect).copy()

        iw, ih = raw.get_size()
        scale = min(box_size / max(1, iw), box_size / max(1, ih))

        if scale >= 1.0:
            # Enlarging pixel art. The source PNGs are NOT all the same
            # native resolution (a 16px sprite next to a 40px one, say), so
            # snapping straight to a whole-number multiple of each icon's
            # own native size (the old approach) makes every icon land on
            # a different final size - one icon might end up rendered at
            # 2x its tiny native size while another sits at 1x its already
            # larger native size, even though both were asked for the same
            # box_size. The visible result: wildly inconsistent icon sizes
            # across cards.
            #
            # Fix: decide the actual target pixel size from box_size only
            # (so every icon ends up the same size on screen regardless of
            # its native resolution). Two passes: first blow up to a
            # generous whole-number multiple with fast point-sampling
            # (crisp, blocky, no filtering yet), then do the final resize
            # down to the exact target size with smoothscale
            # (area-averaging) rather than a second point-sample pass.
            # That second point-sample used to be the bug here: nw/nh is
            # essentially never an exact divisor of the blown-up size, so
            # nearest-neighbor rounds each destination pixel to its
            # nearest source pixel independently - a 2px-wide stroke could
            # land as 2px in one spot and 1px a few pixels over, i.e.
            # visibly uneven line thickness. smoothscale blends each
            # destination pixel from the source pixels it actually covers
            # instead, so stroke width comes out even, while the crisp
            # blow-up first keeps it from reading as blurry.
            nw = max(1, round(iw * scale))
            nh = max(1, round(ih * scale))

            prescale = max(1, math.ceil(scale) * 2)
            big = pygame.transform.scale(raw, (iw * prescale, ih * prescale))
            scaled = pygame.transform.smoothscale(big, (nw, nh))
        else:
            # Shrinking a larger source image - smoothscale is fine here,
            # we're discarding detail rather than fabricating it.
            nw = max(1, round(iw * scale))
            nh = max(1, round(ih * scale))
            scaled = pygame.transform.smoothscale(raw, (nw, nh))

        canvas = pygame.Surface((box_size, box_size), pygame.SRCALPHA)
        canvas.blit(scaled, ((box_size - nw) // 2, (box_size - nh) // 2))
        return canvas

    def _config_card_size(self):
        """Narrow, tall cards for the CONFIGURATION screens (instead of
        full-width strips)."""
        cw = self._content_rect.width
        card_w = max(300, min(cw, round(cw * 0.34)))
        card_h = max(96, min(132, round(self.screen_height * 0.13)))
        return (card_w, card_h)

    def _layout(self):
        w, h = self.screen_width, self.screen_height
        self.header_h = max(86, round(h * 0.12))
        self.footer_h = max(42, round(h * 0.065))

        self.title_size = max(22, min(34, round(w * 0.027)))
        self.subtitle_size = max(9, min(13, round(w * 0.010)))
        self.card_title_size = max(13, min(19, round(w * 0.0145)))
        self.card_body_size = max(8, min(11, round(w * 0.008)))
        self.footer_size = max(8, min(11, round(w * 0.008)))

        margin_x = max(28, round(w * 0.055))
        gap_x = max(12, round(w * 0.014))
        gap_y = max(12, round(h * 0.018))
        available_w = w - margin_x * 2
        card_w = (available_w - gap_x * (COLUMNS - 1)) // COLUMNS
        card_h = max(112, min(158, round(h * 0.175)))

        grid_top = self.header_h + max(20, round(h * 0.028))
        grid_bottom = h - self.footer_h - max(22, round(h * 0.03))
        grid_h = grid_bottom - grid_top
        rows = math.ceil(len(CATEGORIES) / COLUMNS)
        total_h = rows * card_h + (rows - 1) * gap_y
        if total_h > grid_h:
            card_h = max(96, (grid_h - (rows - 1) * gap_y) // rows)
            total_h = rows * card_h + (rows - 1) * gap_y

        grid_start_y = grid_top + max(0, (grid_h - total_h) // 2)

        self._card_rects = []
        for i in range(len(CATEGORIES)):
            row, col = divmod(i, COLUMNS)
            x = margin_x + col * (card_w + gap_x)
            y = grid_start_y + row * (card_h + gap_y)
            self._card_rects.append(pygame.Rect(x, y, card_w, card_h))

        self._content_rect = pygame.Rect(margin_x, grid_top, available_w, grid_h)

        # Config button, right-aligned in the header, vertically centered
        # the same way the "DEV TOOLS" title is.
        config_size = max(40, round(self.header_h * 0.55))
        self._config_rect = pygame.Rect(0, 0, config_size, config_size)
        self._config_rect.right = w - margin_x
        self._config_rect.centery = self.header_h // 2

        # Mirror of the config button, pinned to the opposite (left) side.
        self._back_rect = pygame.Rect(0, 0, config_size, config_size)
        self._back_rect.left = margin_x
        self._back_rect.centery = self.header_h // 2

        # Titles never change at runtime - render both once here instead of
        # every frame in _draw_header.
        self._title_surf_main = self.title_font.render('DEV TOOLS', color=(242, 244, 248), height=self.title_size)
        self._title_surfs_config = {
            page: self.title_font.render(title, color=(242, 244, 248), height=self.title_size)
            for page, title in _CONFIG_PAGE_TITLES.items()
        }

        # Keep the configuration card list's geometry in sync with the same
        # content area the main grid uses (only exists after __init__ has
        # finished building it - guard for the first _layout() call).
        if getattr(self, '_config_menu', None) is not None:
            self._config_menu.set_card_geometry(self._config_card_size(), (0, 20))
            self._config_menu.layout(self._content_rect)

    def _resize_to(self, w, h):
        w, h = int(w), int(h)
        if (w, h) == (self.screen_width, self.screen_height):
            return
        self.screen_width, self.screen_height = w, h
        self._layout()

    # ------------------------------------------------------------------ state
    def toggle(self):
        self.close() if self.active else self.open()

    def open(self):
        self.active = True
        self.hover_index = -1
        self.selected_index = max(0, min(self.selected_index, len(CATEGORIES) - 1))
        self._view = 'main'
        self._prev_view = None
        self._transition_elapsed = 0.0
        self._text_input.close()

        if self._native_mode:
            self._logical_size_backup = self.renderer.logical_size
            self.renderer.logical_size = (0, 0)
            self._resize_to(*self.window.size)

        if self.sound_manager:
            self.previous_context = self.sound_manager.get_current_context()
            self.sound_manager.set_context_immediate('menu')

    def close(self):
        self.active = False
        self._view = 'main'
        self._prev_view = None
        self._transition_elapsed = 0.0
        self._text_input.close()

        if self._native_mode and self._logical_size_backup is not None:
            self.renderer.logical_size = self._logical_size_backup
            self._resize_to(*self._logical_size_backup)
            self._logical_size_backup = None

        if self.sound_manager:
            ctx = self.previous_context or 'exploration'
            self.sound_manager.set_context(ctx, force=True)

    # ------------------------------------------------------------------ input
    def _set_hover(self, pos):
        self.hover_index = -1
        for i, rect in enumerate(self._card_rects):
            if rect.collidepoint(pos):
                self.hover_index = i
                self.selected_index = i
                return

    def _activate_selected(self):
        if not (0 <= self.selected_index < len(CATEGORIES)):
            return None
        item_id = CATEGORIES[self.selected_index][0]
        self.close()
        return ACTION_MAP[item_id]

    def _activate_config(self):
        # Switch to the internal CONFIGURATION screen rather than closing
        # the menu - mirrors the old dev menu's 'main' -> 'config' submenu
        # navigation, just drawn with the new card style.
        if self._view != 'config':
            self._prev_view = 'main'
            self._transition_elapsed = 0.0
        self._view = 'config'
        self._config_page = 'root'
        self.hover_index = -1
        self._back_hovered = False
        self._refresh_config_items()
        return None

    # -- configuration screen ------------------------------------------------
    def _config_items(self):
        """Build the CONFIGURATION screen's card list fresh, so any
        'value_key' field shows the live value currently on game_config."""
        items = []
        disp_cfg = load_display_settings()
        for opt in _CONFIG_PAGES[self._config_page]:
            label = opt['label']
            if 'display' in opt:
                kind = opt['display']
                if kind == 'mode':
                    label = f"{label}: {disp_cfg['mode'].upper()}"
                elif kind == 'resolution':
                    label = f"{label}: {display_resolution_key(disp_cfg).upper()}"
                elif kind == 'original_ratio':
                    label = f"{label}: {'ON' if disp_cfg.get('original_ratio') else 'OFF'}"
                else:
                    # Only offer the restart when it would change something.
                    w, h = effective_resolution(disp_cfg)
                    if (w, h) == (SCREEN_WIDTH, SCREEN_HEIGHT):
                        continue
                    label = f"RESTART AT {w}X{h}"
            elif 'value_key' in opt:
                value = getattr(self.config, opt['value_key'], '?')
                label = f"{label}: {value}"
            items.append(uk.GridItem(opt['id'], label, icon=None, accent=uk.Theme.GOLD))
        return items

    def _refresh_config_items(self):
        self._config_menu.set_items(self._config_items())
        self._config_menu.layout(self._content_rect)

    def refresh_config(self):
        """Rebuild the CONFIGURATION labels (game.py calls this after it has
        applied a display action, so the new value shows immediately)."""
        self._refresh_config_items()

    def _open_config_page(self, page):
        self._config_page = page
        self._refresh_config_items()

    def _config_back(self):
        """Back button / ESC: sub-page -> CONFIGURATION, otherwise out to
        the main launcher."""
        if self._config_page != 'root':
            self._open_config_page('root')
        else:
            self._leave_config()

    def _leave_config(self):
        self._config_page = 'root'
        if self._view != 'main':
            self._prev_view = 'config'
            self._transition_elapsed = 0.0
        self._view = 'main'
        self._config_hovered = False
        self._config_menu.selected_index = -1
        self._config_menu.hover_index = -1

    def _activate_config_item(self, item_id):
        opt = next((o for pg in _CONFIG_PAGES.values() for o in pg if o['id'] == item_id), None)
        if opt and 'page' in opt:
            self._open_config_page(opt['page'])
            return None
        if opt and 'display' in opt:
            return _DISPLAY_ACTIONS[opt['display']]
        if opt and 'value_key' in opt:
            self._text_input.open(opt['value_key'])
        return None

    def _commit_text_input(self, field, text):
        if not field:
            return
        try:
            value = float(text)
        except ValueError:
            return  # Ignore non-numeric input - leave the original value intact
        setattr(self.config, field, value)

    def handle_input(self, event):
        if not self.active:
            return None

        if self._prev_view is not None:
            # main<->config slide is still animating - swallow input until
            # it settles, so a click can't land on a card/row mid-flight.
            return None

        # -- Text input mode: modal, consumes every keystroke -----------------
        if self._text_input.active:
            field = self._text_input.field  # captured before handle_event() clears it on commit/cancel
            result = self._text_input.handle_event(event)
            if result is not None:
                kind, text = result
                if kind == 'commit':
                    self._commit_text_input(field, text)
                self._refresh_config_items()
            return None

        # -- Configuration screen ----------------------------------------------
        if self._view == 'config':
            if event.type == pygame.MOUSEMOTION:
                self._back_hovered = self._back_rect.collidepoint(event.pos)
                self._config_menu.handle_event(event)
                return None

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self._back_rect.collidepoint(event.pos):
                    self._config_back()
                    return None
                item_id = self._config_menu.handle_event(event)
                if item_id is not None:
                    return self._activate_config_item(item_id)
                return None

            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self._config_back()
                return None

            item_id = self._config_menu.handle_event(event)
            if item_id is not None:
                return self._activate_config_item(item_id)
            return None

        # -- Main launcher screen -----------------------------------------------
        if event.type == pygame.MOUSEMOTION:
            self._last_input = 'mouse'
            self._config_hovered = self._config_rect.collidepoint(event.pos)
            self._set_hover(event.pos)
            return None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._last_input = 'mouse'
            if self._config_rect.collidepoint(event.pos):
                return self._activate_config()
            self._set_hover(event.pos)
            if self.hover_index >= 0:
                return self._activate_selected()
            return None

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.close()
                return 'close'

            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                return self._activate_selected()

            if event.key in (pygame.K_RIGHT, pygame.K_d):
                self._last_input = 'keyboard'
                self.hover_index = -1
                self.selected_index = min(self.selected_index + 1, len(CATEGORIES) - 1)
            elif event.key in (pygame.K_LEFT, pygame.K_a):
                self._last_input = 'keyboard'
                self.hover_index = -1
                self.selected_index = max(self.selected_index - 1, 0)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self._last_input = 'keyboard'
                self.hover_index = -1
                self.selected_index = min(self.selected_index + COLUMNS, len(CATEGORIES) - 1)
            elif event.key in (pygame.K_UP, pygame.K_w):
                self._last_input = 'keyboard'
                self.hover_index = -1
                self.selected_index = max(self.selected_index - COLUMNS, 0)
            elif pygame.K_1 <= event.key <= pygame.K_9:
                idx = event.key - pygame.K_1
                if idx < len(CATEGORIES):
                    self._last_input = 'keyboard'
                    self.hover_index = -1
                    self.selected_index = idx
                    return self._activate_selected()

        return None

    def update(self, dt):
        if not self.active:
            uk.set_text_cursor(False)
            return

        # A stray slow frame (GC pause, asset load, the rebuild costs above,
        # etc.) would otherwise show up as the background suddenly jumping
        # instead of scrolling - cap the step so it just looks briefly
        # paused rather than jittery.
        dt = min(dt, 1 / 20)

        if self._prev_view is not None:
            self._transition_elapsed += dt
            if self._transition_elapsed >= TRANSITION_SECONDS:
                self._prev_view = None

        self._pulse += dt
        if self._bg_tile is not None:
            tw, th = self._bg_tile.get_size()
            self._bg_offset[0] = (self._bg_offset[0] + self._bg_speed[0] * dt) % tw
            self._bg_offset[1] = (self._bg_offset[1] + self._bg_speed[1] * dt) % th
        for i in range(len(CATEGORIES)):
            target_hover = 1.0 if (self._last_input == 'mouse' and i == self.hover_index) else 0.0
            target_focus = 1.0 if (self._last_input == 'keyboard' and i == self.selected_index) else 0.0
            self._hover_anim[i] += (target_hover - self._hover_anim[i]) * min(1.0, dt * 12.0)
            self._focus_anim[i] += (target_focus - self._focus_anim[i]) * min(1.0, dt * 14.0)

        target_config_hover = 1.0 if self._config_hovered else 0.0
        self._config_hover_anim += (target_config_hover - self._config_hover_anim) * min(1.0, dt * 12.0)

        target_back_hover = 1.0 if self._back_hovered else 0.0
        self._back_hover_anim += (target_back_hover - self._back_hover_anim) * min(1.0, dt * 12.0)

        if self._view == 'config':
            self._config_menu.update(dt)
        self._text_input.update(dt)
        uk.set_text_cursor(self._text_input.wants_ibeam(pygame.mouse.get_pos()))

    def poll_action(self):
        return None

    # ------------------------------------------------------------------ drawing
    @staticmethod
    def _mix(c1, c2, t):
        t = max(0.0, min(1.0, t))
        return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

    def _render_text(self, surface, text, x, y, height, color):
        surf = self.font.render(text, color=color, height=height)
        uk.blit_surface(surface, surf, (int(x), int(y)), transient=True)
        return surf

    def _draw_header(self, surface):
        w = self.screen_width
        # Clean header: only the main title remains.
        uk.draw_rect_on(surface, (12, 15, 23), pygame.Rect(0, 0, w, self.header_h), 0, 0)
        uk.draw_rect_on(surface, (43, 49, 63), pygame.Rect(0, self.header_h - 1, w, 1), 0, 0)

        title_surf = (self._title_surfs_config[self._config_page]
                      if self._view == 'config' else self._title_surf_main)
        title_rect = title_surf.get_rect(centerx=w // 2, centery=self.header_h // 2)
        uk.blit_surface(surface, title_surf, title_rect, transient=False)

        # The gear button (top-right) is how you get into CONFIGURATION; the
        # back arrow (top-left) is how you leave it - only one is ever shown.
        if self._view == 'main':
            self._draw_config_button(surface)
        else:
            self._draw_back_button(surface)

    def _draw_config_button(self, surface):
        accent = (244, 190, 76)

        # Same discretization trick as the cards: snap the continuous hover
        # value to a handful of steps so ui_kit's panel/glow cache actually
        # gets hit frame to frame instead of rebuilding constantly.
        t = round(self._config_hover_anim * 20) / 20.0

        base = self._mix((22, 26, 35), (28, 33, 44), t)
        border = self._mix((52, 58, 72), accent, t * 0.78)
        uk.draw_panel(surface, self._config_rect, bg=(*base, 255), border=border,
                      border_width=1, radius=8, shadow=False)

        if t > 0.01:
            uk.draw_soft_glow(surface, self._config_rect.center, 22, accent, max_alpha=int(25 * t))

        icon_rect = self._config_icon.get_rect(center=self._config_rect.center)
        uk.blit_surface(surface, self._config_icon, icon_rect, transient=False)
        uk.register_hoverable(self._config_rect)

    def _draw_back_button(self, surface):
        accent = (244, 190, 76)
        t = round(self._back_hover_anim * 20) / 20.0

        base = self._mix((22, 26, 35), (28, 33, 44), t)
        border = self._mix((52, 58, 72), accent, t * 0.78)
        uk.draw_panel(surface, self._back_rect, bg=(*base, 255), border=border,
                      border_width=1, radius=8, shadow=False)

        if t > 0.01:
            uk.draw_soft_glow(surface, self._back_rect.center, 22, accent, max_alpha=int(25 * t))

        icon_rect = self._back_icon.get_rect(center=self._back_rect.center)
        uk.blit_surface(surface, self._back_icon, icon_rect, transient=False)
        uk.register_hoverable(self._back_rect)

    def _card_offset(self, col):
        """Horizontal slide offset for one grid column this frame, in
        pixels - 0 once settled. Every column plays the same fixed-length,
        fixed-speed ease-in-out (CARD_SLIDE_SECONDS); only the start time
        is staggered by CARD_STAGGER_SECONDS per column, so the grid reads
        as a cascade without any column having to move faster than another
        to catch up.

        Leaving (main -> config), column 0 leads: each column clears out
        of a given patch of screen before a later, longer-delayed column's
        sweep ever reaches it, so columns never visually overlap. Coming
        back (config -> main) reverses the stagger order instead of
        reusing it - with column 0 leading both ways, column 0 would
        already be sitting still at its home position while a trailing
        column's still-incoming sweep passes straight through it. Playing
        the columns back in reverse order instead makes the entrance the
        exact mirror image of the exit (same total duration, same curve),
        which retraces that same collision-free path backward."""
        if self._prev_view is None:
            return 0.0
        stagger_col = col if self._prev_view == 'main' else (COLUMNS - 1 - col)
        start = stagger_col * CARD_STAGGER_SECONDS
        elapsed_since_start = self._transition_elapsed - start
        local = 0.0 if elapsed_since_start <= 0 else min(1.0, elapsed_since_start / CARD_SLIDE_SECONDS)
        eased = _ease_in_out(local)
        w = self.screen_width
        return -w * eased if self._prev_view == 'main' else -w * (1 - eased)

    def _config_offset(self):
        """Horizontal slide offset for the CONFIGURATION list this frame -
        one block, not staggered per-row, sliding in/out from the right
        opposite the cards' cascade (see _card_offset), over its own
        CONFIG_SLIDE_SECONDS starting immediately."""
        if self._prev_view is None:
            return 0.0
        local = min(1.0, self._transition_elapsed / CONFIG_SLIDE_SECONDS)
        eased = _ease_in_out(local)
        w = self.screen_width
        return w * (1 - eased) if self._prev_view == 'main' else w * eased

    def _draw_card(self, surface, i, offset_x=0):
        item_id, label, description, icon_key, accent = CATEGORIES[i]
        rect = self._card_rects[i]
        if offset_x:
            rect = rect.move(round(offset_x), 0)
        h = self._hover_anim[i]
        f = self._focus_anim[i]

        # ui_kit caches the rounded-rect panel and the soft glow by their
        # exact color/alpha, and rebuilds (anti-aliased supersample, or a
        # gaussian blur) on a cache miss. Feeding it a continuous float
        # means almost every frame of a hover/focus transition misses the
        # cache and pays for a fresh rebuild. Snapping to 21 discrete steps
        # means most frames land on the same bucket as the frame before -
        # cache hit, no rebuild - and the visual difference is invisible.
        t = round(max(h, f) * 20) / 20.0

        lift = int(round(2 * t))
        draw_rect = rect.move(0, -lift)

        # Dark, clean card body.
        base = self._mix((22, 26, 35), (28, 33, 44), t)
        border = self._mix((52, 58, 72), accent, t * 0.78)
        uk.draw_panel(surface, draw_rect, bg=(*base, 255), border=border,
                      border_width=1, radius=10, shadow=False)

        # Icon container.
        icon_center = (draw_rect.x + 46, draw_rect.centery)
        if t > 0.01:
            uk.draw_soft_glow(surface, icon_center, 32, accent, max_alpha=int(25 * t))
        icon = self._icons[i]
        icon_rect = icon.get_rect(center=icon_center)
        uk.blit_surface(surface, icon, icon_rect, transient=False)

        # Text block: title only. Descriptions and action hints are intentionally hidden.
        tx = draw_rect.x + 86
        title_color = self._mix((215, 219, 228), (255, 255, 255), t)
        text_height = self.card_title_size + int(f)

        # Same idea as the title cache: the label string itself never
        # changes, only its color/height while animating, so only re-render
        # when that actually moved instead of on every single frame.
        key = (text_height, title_color)
        cached = self._card_label_cache[i]
        if cached is not None and cached[0] == key:
            text_surf = cached[1]
        else:
            text_surf = self.font.render(label, color=title_color, height=text_height)
            self._card_label_cache[i] = (key, text_surf)

        text_surf_rect = text_surf.get_rect(
            midleft=(tx, draw_rect.centery)
        )
        uk.blit_surface(surface, text_surf, text_surf_rect, transient=False)
        if not offset_x:
            # Only hoverable at rest - input is swallowed during the slide
            # anyway (see handle_input), so don't offer a hand cursor for a
            # card that's mid-flight and can't actually be clicked.
            uk.register_hoverable(rect)

    def _draw_background(self, surface):
        if self._bg_tile is None:
            return
        tw, th = self._bg_tile.get_size()
        start_x = -int(self._bg_offset[0])
        start_y = -int(self._bg_offset[1])
        for y in range(start_y, self.screen_height, th):
            for x in range(start_x, self.screen_width, tw):
                uk.blit_surface(surface, self._bg_tile, (x, y), transient=False)

    def _draw_footer(self, surface):
        # Keep the lower bar purely visual for now. No navigation or
        # "selected" text is shown there.
        w, h = self.screen_width, self.screen_height
        y = h - self.footer_h
        uk.draw_rect_on(surface, (12, 15, 23), pygame.Rect(0, y, w, self.footer_h), 0, 0)
        uk.draw_rect_on(surface, (43, 49, 63), pygame.Rect(0, y, w, 1), 0, 0)

    def draw(self, surface):
        if not self.active:
            return

        w, h = self.screen_width, self.screen_height
        # Flat neutral base: functional editor first, decoration second.
        uk.draw_rect_on(surface, (8, 11, 17), pygame.Rect(0, 0, w, h), 0, 0)

        # Subtle vertical tonal bands improve separation without becoming a gradient-heavy UI.
        uk.draw_rect_on(surface, (10, 13, 20), pygame.Rect(0, self.header_h, w, h - self.header_h - self.footer_h), 0, 0)

        self._draw_background(surface)

        self._draw_header(surface)

        # Header/footer stay put; only the content area (cards or config
        # list) slides. Offsets are all 0 when settled, so this draws
        # exactly like before outside of a transition.
        config_offset = self._config_offset()
        draw_main = self._view == 'main' or self._prev_view == 'main'
        draw_config = self._view == 'config' or self._prev_view == 'config'

        if draw_main:
            for i in range(len(CATEGORIES)):
                self._draw_card(surface, i, offset_x=self._card_offset(i % COLUMNS))
        if draw_config:
            # Re-layout at the offset content rect every frame the config
            # screen is on screen - cheap (a handful of items) and it's
            # what makes the card list itself slide rather than just its
            # container. Lands back on the real self._content_rect once
            # config_offset settles to 0.
            self._config_menu.layout(self._content_rect.move(round(config_offset), 0))
            self._config_menu.draw(surface)

        self._draw_footer(surface)

        self._text_input.draw(surface, w, h)

        # Resolve the frame's cursor last, now that every clickable widget
        # drawn above (category cards, the gear/back button, and the
        # CONFIGURATION list's own cards) has had a chance to register
        # itself via register_hoverable. Yields to the I-beam set in
        # update() — see update_hover_cursor's docstring.
        uk.update_hover_cursor(pygame.mouse.get_pos())