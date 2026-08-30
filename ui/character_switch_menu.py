"""
Character Switch Menu
---------------------
Full-screen scanline background, 9-slice border frame, sprite-based buttons,
and per-character walk/idle animation. Selected character plays their walk
cycle; unselected characters hold their idle frame.
"""

from __future__ import annotations

import pygame
import os
from config.settings import RENDER_SCALE
from core.bitmap_font import BitmapFont
from ui.pause_menu import FlatBitmapFont

_S = max(1, RENDER_SCALE)


class CharacterSwitchMenu:

    def __init__(self, screen_width, screen_height):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.active            = False
        self.selected_character = 0
        # Index of the leftmost character currently shown, once there are
        # enough characters that they can't all fit at char_min_gap spacing
        # — see draw()'s scroll-window logic. Recomputed every frame from
        # selected_character, so this starting value only matters until the
        # first draw() call.
        self.char_scroll_offset = 0

        # Canvas takes up most of the screen but leaves breathing room on all sides
        self.canvas_width  = int(screen_width  * 0.85)
        self.canvas_height = int(screen_height * 0.75)
        self.canvas_x = (screen_width  - self.canvas_width)  // 2
        self.canvas_y = (screen_height - self.canvas_height) // 2

        self.characters = self._discover_characters()

        # Tracks which buttons are currently showing their pressed sprite
        self.button_states = {'left': False, 'right': False, 'a': False, 'b': False}
        self.button_press_timers = {'left': 0.0, 'right': 0.0, 'a': 0.0, 'b': 0.0}
        self.button_press_duration = 0.1  # seconds to hold the pressed frame visible

        # Flat constant, independent of RENDER_SCALE — matches pause_menu.py's
        # own font_scale so bitmap-font text renders at the same on-screen
        # size across menus instead of drifting with the current render scale.
        # Set before _load_ui_sprites() since it uses self.font_scale to size
        # the background texture tile.
        font_scale = 4
        self.font_scale = font_scale

        self._load_ui_sprites()
        self._load_character_sprites()
        # Ground shadow under each character sprite — same
        # assets/sprites/universal/shadow.png (or shadowbig.png) sprite,
        # ellipse fallback, and scale-to-sprite-width math as
        # title_screen.py's _load_row_shadow_sprites/_get_scaled_row_shadow
        # (itself mirroring the real in-game shadow — LayerManager.
        # _load_shadow/_get_scaled_shadow in core/draw_layers.py).
        self._shadow_sprite, self._shadow_sprite_big = self._load_character_shadow_sprites()
        self._shadow_cache = {}   # (sprite_width, big) -> scaled Surface

        self.animation_speed = 0.15  # seconds per walk frame

        self.bitmap_font = BitmapFont(
            'assets/ui/fonts',
            letter_spacing=max(1, int(2 / RENDER_SCALE)),
            scale=font_scale
        )
        # Same bold tab-title fonts pause_menu.py uses for its 'STATUS' /
        # 'INVENTORY' style tab titles, so 'Switch Character' matches them
        # exactly instead of falling back to the generic button-label font.
        self.bold_font           = FlatBitmapFont('assets/ui/fonts/bold',          letter_spacing=max(4, int(10/RENDER_SCALE)), scale=font_scale)
        self.bold_lowercase_font = FlatBitmapFont('assets/ui/fonts/bold_lowercase', letter_spacing=max(4, int(10/RENDER_SCALE)), scale=font_scale)

        # Colors
        self.bg_scanline_dark  = (0,   80,  0)
        self.bg_scanline_light = (0,  100,  0)
        self.border_outer      = (255, 215, 0)
        self.border_inner      = (180, 100, 0)
        self.border_green      = (0,   255, 0)
        self.title_color       = (255, 255, 0)
        self.text_color        = (255, 255, 0)
        self.text_shadow_color = (0,   0,   0)
        self.shadow_offset = (max(1, int(2 / RENDER_SCALE)), max(1, int(2 / RENDER_SCALE)))

        # Background texture offset — shift the tile pattern if needed
        self.bg_offset_x = 0
        self.bg_offset_y = 0

    # ── Character discovery ───────────────────────────────────────────────────

    @staticmethod
    def _discover_characters():
        import json
        from dev_tools import character_creator

        # Character *existence* is authoritatively decided by
        # character_creator.discover_characters() — it scans the sprite
        # folders under assets/sprites/player/, which is also what
        # Game._handle_character_list_action() checks against before it'll
        # add an id to player.playable_characters. This used to instead
        # scan assets/characters/*.json directly: a character with a
        # sprite folder but no JSON yet (never opened/saved in the
        # character creator) could be legitimately unlocked but would
        # never appear in self.characters at all, so open()'s
        # playable_characters filter had nothing to keep and it just
        # silently vanished from the menu.
        all_ids = character_creator.discover_characters()

        # character_creator.py consolidated order + deletions into this one
        # file (see MENU_FILE / save_character_menu() there): a dict shaped
        # like {"order": [...], "removed": [...]}. This used to point at
        # assets/character_menu_order.json (a plain list) — that was the old
        # pre-consolidation location the creator migrated away from, so it
        # never got written to anymore and this menu silently fell back to
        # alphabetical order (which is why "gohan" showed before "goku").
        menu_file = 'assets/character_menu.json'
        saved_order = []
        removed = set()
        try:
            with open(menu_file, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                saved_order = [str(x) for x in data.get('order', [])]
                removed = {str(x) for x in data.get('removed', [])}
        except Exception:
            pass

        all_ids = [cid for cid in all_ids if cid not in removed]

        # Apply the saved order: known IDs first, then any new ones alphabetically
        if saved_order:
            ordered_ids = [cid for cid in saved_order if cid in all_ids]
            leftover = sorted(set(all_ids) - set(ordered_ids))
            final_ids = ordered_ids + leftover
        else:
            final_ids = sorted(all_ids)

        characters = []
        for char_id in final_ids:
            # load_config() — not a raw json.load() of the character's
            # file — because it resolves the "default" costume placeholder
            # to the character's real first discovered costume when the
            # character has never been saved in the creator. Reading the
            # JSON's "costume" field raw (the old code) took "default" at
            # face value and tried to load
            # assets/sprites/player/{id}/default/idle.png, which doesn't
            # exist as a folder — that silently failed, has_idle/has_walk
            # stayed False, and the menu fell back to the placeholder
            # circle instead of the character's actual sprite.
            cfg = character_creator.load_config(char_id)
            characters.append({
                'id': char_id,
                'name': cfg.get('display_name', char_id.capitalize()),
                'unlocked': True,
                'costume': cfg.get('costume', 'base'),
                'animation_frame': 0,
                'animation_timer': 0.0,
            })

        return characters

    # ── Asset loading ─────────────────────────────────────────────────────────

    def _load_ui_sprites(self):
        def _img(path):
            try:    return pygame.image.load(path).convert_alpha()
            except: return None

        self.button_a             = _img('assets/ui/buttons/button_a.png')
        self.button_a_pressed     = _img('assets/ui/buttons/button_a_pressed.png')
        self.button_b             = _img('assets/ui/buttons/button_b.png')
        self.button_b_pressed     = _img('assets/ui/buttons/button_b_pressed.png')
        self.arrow_left           = _img('assets/ui/buttons/arrow_left.png')
        self.arrow_left_pressed   = _img('assets/ui/buttons/arrow_left_pressed.png')
        self.arrow_right          = _img('assets/ui/buttons/arrow_right.png')
        self.arrow_right_pressed  = _img('assets/ui/buttons/arrow_right_pressed.png')
        self.arrow_left_greyed    = _img('assets/ui/buttons/arrow_left_greyed.png')
        self.arrow_right_greyed   = _img('assets/ui/buttons/arrow_right_greyed.png')
        self.box_sprite           = _img('assets/ui/textbox/border.png')  # native 227×131

        # Pre-render the tiled background as one big surface to avoid seams
        raw_tex = _img('assets/ui/textbox/background_texture.png')
        if raw_tex:
            # Match the fonts' pixel scale exactly (font_scale, set above) rather
            # than an unrelated hardcoded factor — one pixel of a letter glyph
            # should equal one pixel of a background row, same as pause_menu.py.
            scale  = self.font_scale
            tile_w = round(raw_tex.get_width()  * scale)
            tile_h = round(raw_tex.get_height() * scale)
            tile   = pygame.transform.scale(raw_tex, (tile_w, tile_h))
            cols   = (self.screen_width  // tile_w) + 2
            rows   = (self.screen_height // tile_h) + 2
            surf   = pygame.Surface((cols * tile_w, rows * tile_h), pygame.SRCALPHA)
            for ty in range(rows):
                for tx in range(cols):
                    surf.blit(tile, (tx * tile_w, ty * tile_h))
            self.bg_texture = surf
            self._bg_tile_w = tile_w
            self._bg_tile_h = tile_h
        else:
            self.bg_texture = None
            self._bg_tile_w = 1
            self._bg_tile_h = 1

    # Target on-screen height for character sprites in this menu — tune
    # this directly, same as title_screen.py's _CHARACTER_ROW_IDLE_SCALE
    # tunes its save-slot row. Expressed as a fraction of canvas_height so
    # sizing scales with the menu itself instead of being tied to
    # RENDER_SCALE the way the old flat scale_f multiplier was.
    _SPRITE_TARGET_H_FRACTION = 0.15

    @staticmethod
    def _scale_pixel_art(surface, target_height, min_scale=1):
        """Copied from title_screen.py's _scale_pixel_art. Scale a
        pixel-art surface to (approximately) `target_height` by an
        integer factor, so every source pixel maps to an identically-
        sized block on screen instead of the uneven pixel sizes a
        fractional pygame.transform.scale() factor produces (some source
        pixel rows/columns get duplicated one extra time and others
        don't, which reads as visual noise/smearing on crisp pixel art).

        Picks the integer factor whose resulting height is closest to
        target_height (never less than min_scale), then scales both axes
        by that same factor. Returns (scaled_surface, actual_height)
        since the achieved height will generally differ slightly from
        the requested one — callers doing further layout math (e.g.
        bottom-aligning against a baseline) should use the returned
        height, not the original target_height.

        Used for this menu's own character sprites too (see
        _load_character_sprites) — the whole-number-factor snapping means
        the achieved height can land a bit off _SPRITE_TARGET_H_FRACTION's
        target, but every draw call centers off the real sprite/frame
        rect, so that's a fine trade for crisp, unsmeared pixel art.
        _scale_sprite_exact below is kept around for anything that still
        wants an exact size over pixel-perfect crispness.
        """
        raw_h = surface.get_height()
        if raw_h <= 0:
            return surface, surface.get_height()
        factor = max(min_scale, round(target_height / raw_h))
        new_w  = surface.get_width()  * factor
        new_h  = raw_h * factor
        return pygame.transform.scale(surface, (new_w, new_h)), new_h

    @staticmethod
    def _scale_sprite_exact(surface, target_height):
        """Scale to the exact target_height via a plain float factor —
        no snapping to whole-number multiples. Some pixel rows/columns
        get duplicated unevenly at fractional factors (a bit of smearing
        vs _scale_pixel_art's crispness), but that's a fine trade here:
        this is a menu thumbnail, not gameplay, and being able to land on
        a precise size matters more than pixel-perfect scaling. Returns
        (scaled_surface, actual_height) to match _scale_pixel_art's
        signature.
        """
        raw_h = surface.get_height()
        if raw_h <= 0:
            return surface, surface.get_height()
        factor = target_height / raw_h
        new_w  = max(1, round(surface.get_width() * factor))
        new_h  = max(1, round(raw_h * factor))
        return pygame.transform.scale(surface, (new_w, new_h)), new_h

    def _scaled_width_for_height(self, sprite, target_h):
        """The actual on-screen width _scale_pixel_art would produce for
        `sprite` scaled to `target_h`, preserving its real aspect ratio.
        Used by draw() to size the no-overlap zone around the arrow
        sprites — arrow_h alone isn't enough since a non-square arrow
        sprite ends up wider than its own target height. Falls back to
        target_h (a square) to match _draw_arrow_sprite's own fallback box
        when there's no sprite loaded."""
        if not sprite:
            return target_h
        scaled, _ = self._scale_pixel_art(sprite, target_h)
        return scaled.get_width()

    def _load_character_shadow_sprites(self):
        """Loads assets/sprites/universal/shadow.png and shadowbig.png —
        the exact same files, loaded the exact same way (including the
        drawn-ellipse fallback if a file's missing), as
        title_screen.py's _load_row_shadow_sprites / LayerManager.
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

    # Same "~28.5% of the sprite's own on-screen width" scale and
    # baseline-nudge knob as title_screen.py's
    # _CHARACTER_ROW_SHADOW_WIDTH_RATIO/_Y_OFFSET.
    _CHARACTER_SHADOW_WIDTH_RATIO = 0.285
    _CHARACTER_SHADOW_Y_OFFSET    = -5

    def _get_scaled_character_shadow(self, sprite_width, big=False):
        """Cached shadow sprite scaled to _CHARACTER_SHADOW_WIDTH_RATIO of
        sprite_width, aspect ratio preserved — same shape as
        title_screen.py's _get_scaled_row_shadow, just keyed/cached
        locally for this menu's own sprite sizes."""
        source = self._shadow_sprite_big if big else self._shadow_sprite
        if source is None:
            return None
        key = (sprite_width, big)
        if key not in self._shadow_cache:
            orig_w = source.get_width()
            orig_h = source.get_height()
            target_w = max(8, int(sprite_width * self._CHARACTER_SHADOW_WIDTH_RATIO))
            target_h = max(4, int(orig_h * target_w / orig_w)) + 1
            self._shadow_cache[key] = pygame.transform.scale(source, (target_w, target_h))
        return self._shadow_cache[key]

    def _draw_character_shadow(self, screen, center_x, bottom_y, sprite_width, big=False):
        """Ground shadow under a character sprite, centered horizontally
        and sitting just above its bottom edge — same draw shape as
        title_screen.py's _draw_character_row_shadow. center_x:
        horizontal center of the sprite above it. bottom_y: the sprite's
        bottom edge (pre Y_OFFSET). big: True to use shadowbig.png."""
        shadow_surf = self._get_scaled_character_shadow(sprite_width, big=big)
        if shadow_surf is None:
            return
        shadow_x = round(center_x - shadow_surf.get_width() // 2)
        shadow_y = round(bottom_y - shadow_surf.get_height() // 2 + self._CHARACTER_SHADOW_Y_OFFSET)
        screen.blit(shadow_surf, (shadow_x, shadow_y))

    def _load_character_sprites(self):
        """
        Each character gets two sprite sets:
          - idle.png  (2 columns × 4 rows): used as a static thumbnail when not selected
          - walk.png  (4 columns × 4 rows): animated when the character is selected
        Both sheets are assumed to have their "facing down" frames in the top row.

        Frames are scaled with _scale_pixel_art — the same whole-number-
        factor scaling title_screen.py uses for its own sprites, so every
        source pixel maps to a same-sized block on screen instead of the
        uneven duplication a fractional factor produces (that unevenness
        is what read as "inconsistent pixels" with the old
        _scale_sprite_exact call this replaced). _SPRITE_TARGET_H_FRACTION
        is only a target now, not an exact result — actual on-screen size
        snaps to the nearest 1x/2x/3x — but every draw call already
        centers off the real sprite/frame rect (see
        _draw_character_sprite), so nothing depends on hitting the target
        exactly.
        """
        target_h = max(16, int(self.canvas_height * self._SPRITE_TARGET_H_FRACTION))

        # Minimum center-to-center spacing between two characters in the
        # switch menu's row — see draw()'s scroll-window logic. Tied to
        # target_h (the sprites' own on-screen size) rather than a flat
        # pixel constant, so it scales with the sprites instead of drifting
        # out of proportion if _SPRITE_TARGET_H_FRACTION or canvas size
        # changes. Tune the 1.5 multiplier directly to taste.
        self.char_min_gap = int(target_h * 1)

        for char in self.characters:
            cid     = char['id']
            costume = char['costume']

            # Idle — grab top-left frame only
            try:
                sheet = pygame.image.load(f'assets/sprites/player/{cid}/{costume}/idle.png').convert_alpha()
                fw = sheet.get_width()  // 2
                fh = sheet.get_height() // 4
                frame = sheet.subsurface(pygame.Rect(0, 0, fw, fh))
                char['idle_sprite'], _ = self._scale_pixel_art(frame, target_h)
                char['has_idle']       = True
            except (pygame.error, FileNotFoundError):
                char['idle_sprite'] = None
                char['has_idle']    = False

            # Walk — extract all 4 "facing down" frames from the top row
            try:
                sheet = pygame.image.load(f'assets/sprites/player/{cid}/{costume}/walk.png').convert_alpha()
                fw = sheet.get_width()  // 4
                fh = sheet.get_height() // 4
                char['walk_frames'] = [
                    self._scale_pixel_art(
                        sheet.subsurface(pygame.Rect(i * fw, 0, fw, fh)), target_h
                    )[0]
                    for i in range(4)
                ]
                char['has_walk'] = True
            except (pygame.error, FileNotFoundError):
                char['walk_frames'] = None
                char['has_walk']    = False

    # ── Public API ────────────────────────────────────────────────────────────

    def open(self, current_character='goku', playable_characters=None):
        self.active = True
        # Refresh from disk so characters added in the creator appear immediately.
        self.characters = self._discover_characters()
        # Being added/discovered on disk no longer implies playable — when
        # playable_characters is given (the save point always passes
        # Player.playable_characters), drop anything not in it entirely
        # rather than showing it greyed/unselectable. If omitted, fall back
        # to _discover_characters()'s default of everyone shown.
        if playable_characters is not None:
            playable = set(playable_characters)
            self.characters = [c for c in self.characters if c['id'] in playable]
        self._load_character_sprites()
        for i, char in enumerate(self.characters):
            if char['id'] == current_character:
                self.selected_character = i
                break
        else:
            self.selected_character = 0
        self.char_scroll_offset = 0
        for char in self.characters:
            char['animation_frame'] = 0
            char['animation_timer'] = 0.0
        for key in self.button_states:
            self.button_states[key]       = False
            self.button_press_timers[key] = 0.0

    def close(self):
        self.active = False

    def set_character_unlocked(self, character_id, unlocked=True):
        for char in self.characters:
            if char['id'] == character_id:
                char['unlocked'] = unlocked
                break

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self, dt):
        if not self.active:
            return

        for key in self.button_press_timers:
            if self.button_press_timers[key] > 0:
                self.button_press_timers[key] -= dt
                if self.button_press_timers[key] <= 0:
                    self.button_states[key] = False

        for i, char in enumerate(self.characters):
            if i == self.selected_character and char['unlocked']:
                char['animation_timer'] += dt
                if char['animation_timer'] >= self.animation_speed:
                    char['animation_timer'] = 0.0
                    if char['has_walk'] and char['walk_frames']:
                        char['animation_frame'] = (char['animation_frame'] + 1) % len(char['walk_frames'])

    # ── Input ─────────────────────────────────────────────────────────────────

    def handle_input(self, event):
        """Returns the selected character id, 'close', or None."""
        if not self.active or event.type != pygame.KEYDOWN:
            return None

        key = event.key

        if key == pygame.K_LEFT:
            if self.selected_character > 0:
                self._set_button_pressed('left')
                self.selected_character -= 1
                self._reset_char_anim(self.selected_character)

        elif key == pygame.K_RIGHT:
            if self.selected_character < len(self.characters) - 1:
                self._set_button_pressed('right')
                self.selected_character += 1
                self._reset_char_anim(self.selected_character)

        elif key == pygame.K_e:
            self._set_button_pressed('a')
            selected = self.characters[self.selected_character]
            if selected['unlocked']:
                self.close()
                return selected['id']

        elif key == pygame.K_q:
            self._set_button_pressed('b')
            self.close()
            return 'close'

        return None

    def _set_button_pressed(self, name):
        self.button_states[name]       = True
        self.button_press_timers[name] = self.button_press_duration

    def _reset_char_anim(self, idx):
        self.characters[idx]['animation_frame'] = 0
        self.characters[idx]['animation_timer'] = 0.0

    # ── Draw ──────────────────────────────────────────────────────────────────

    def draw(self, screen):
        if not self.active:
            return

        # Full-screen background
        self._draw_tiled_background(screen, pygame.Rect(0, 0, self.screen_width, self.screen_height))

        title_margin = int(self.canvas_height * 0.08)

        inner_margin = int(self.canvas_height)
        inner_w = int(self.canvas_width * 1.1 - inner_margin)
        inner_h = int(self.canvas_height) // 1.45
        box_x   = self.canvas_x + (self.canvas_width - inner_w) // 2
        box_y   = self.canvas_y + title_margin

        # Same font (bold_font/bold_lowercase_font) and the same distance
        # above the border that pause_menu.py uses for its 'STATUS' /
        # 'INVENTORY' tab titles, instead of the generic bitmap_font used
        # for button labels.
        btn_scale = max(2, int(self.canvas_height * 0.06))
        title_gap = max(4, int(self.canvas_height * 0.01))
        title_y   = box_y - btn_scale - title_gap - 16
        self._draw_bold_title(screen, "Switch Character", box_x + inner_w // 2, title_y)

        sprite_drawn = self.box_sprite and self._draw_9slice_sprite(
            screen, self.box_sprite, box_x, box_y, inner_w, inner_h, corner_size=20
        )

        if not sprite_drawn:
            # Procedural fallback borders
            pygame.draw.rect(screen, self.border_outer, (box_x-6, box_y-6, inner_w+12, inner_h+12))
            pygame.draw.rect(screen, self.border_inner, (box_x-3, box_y-3, inner_w+6,  inner_h+6))
            pygame.draw.rect(screen, self.border_green, (box_x-1, box_y-1, inner_w+2,  inner_h+2))
            self._draw_tiled_background(screen, pygame.Rect(box_x, box_y, inner_w, inner_h))

        char_y     = box_y + inner_h // 2 - 6
        # Character sprites get their own y so they can be repositioned
        # without dragging the arrow sprites along with them — the arrow
        # draw calls below still use char_y on purpose, so this offset is
        # sprite-only. Change sprite_y_offset (0 = same row as the arrows).
        sprite_y_offset = -8
        sprite_y   = char_y + sprite_y_offset
        char_count = len(self.characters)

        arrow_margin = int(inner_w * 0.08)
        left_disabled  = (self.selected_character <= 0)
        right_disabled = (self.selected_character >= char_count - 1)

        # Same derivation pause_menu.py uses for its Status page scroll
        # arrows: tag_h as a fraction of the content box's height, then
        # arrow_h as 1.2x that — just using this menu's inner_h in place
        # of pause_menu's content_rect.height.
        tag_h    = max(16, int(inner_h * 0.09))
        arrow_h  = int(tag_h)

        # Characters are laid out strictly between the two arrow sprites
        # (content_left/content_right) so a character can never sit under
        # an arrow. char_min_gap (set in _load_character_sprites, ~1.5x a
        # character sprite's on-screen height) is the minimum center-to-
        # center spacing allowed between two characters. As long as
        # spreading every character evenly across that space keeps at
        # least char_min_gap between them, they're all shown, same as
        # before. Once there isn't room for that, the row switches to a
        # scrolling window — a fixed-width run of characters spaced at
        # exactly char_min_gap that slides to keep the current selection
        # in view — the same "clamp the visible window to the cursor"
        # approach pause_menu.py uses for inv_scroll_offset following
        # inv_selected_index, rather than continuing to shrink the gap
        # until characters overlap.
        # The no-overlap zone around each arrow has to be sized off the
        # arrow sprite's actual on-screen width, not arrow_h — arrow_h is
        # only the target *height* passed to _scale_pixel_art, and that
        # scales width/height together to preserve the sprite's real aspect
        # ratio, so a non-square arrow sprite ends up wider than arrow_h.
        # Using arrow_h alone as the pad understated that width and let
        # characters sit under the arrows.
        arrow_gap     = max(4, int(inner_w * 0.03))
        left_arrow_w  = self._scaled_width_for_height(self.arrow_left,  arrow_h)
        right_arrow_w = self._scaled_width_for_height(self.arrow_right, arrow_h)
        content_left  = box_x + arrow_margin + left_arrow_w  // 2 + arrow_gap
        content_right = box_x + inner_w - arrow_margin - right_arrow_w // 2 - arrow_gap
        content_w     = max(1, content_right - content_left)

        min_gap   = max(1, self.char_min_gap)
        even_step = (content_w // (char_count + 1)) if char_count > 0 else content_w

        if char_count <= 1 or even_step >= min_gap:
            # Everything fits comfortably at or above the minimum gap —
            # spread evenly across content_w (bounded by the arrows,
            # unlike the old inner_w-wide spread) and no scrolling needed.
            self.char_scroll_offset = 0
            visible_indices = list(range(char_count))
            positions       = [content_left + even_step * (i + 1) for i in range(char_count)]
        else:
            # Too many characters to keep char_min_gap spacing for all of
            # them — scroll instead. visible_count is how many characters
            # fit at exactly min_gap spacing across content_w.
            visible_count = max(1, min(char_count, content_w // min_gap + 1))

            offset = self.char_scroll_offset
            if self.selected_character < offset:
                offset = self.selected_character
            elif self.selected_character >= offset + visible_count:
                offset = self.selected_character - visible_count + 1
            offset = max(0, min(offset, char_count - visible_count))
            self.char_scroll_offset = offset

            # Center the visible run within content_w rather than hugging
            # the left edge, same as the Status page's name-tag centering.
            span    = min_gap * (visible_count - 1)
            start_x = content_left + max(0, (content_w - span) // 2)
            visible_indices = list(range(offset, offset + visible_count))
            positions       = [start_x + min_gap * i for i in range(visible_count)]

        self._draw_arrow_sprite(
            screen,
            self.arrow_left, self.arrow_left_pressed, self.arrow_left_greyed,
            self.button_states['left'], left_disabled,
            box_x + arrow_margin, char_y, arrow_h
        )
        self._draw_arrow_sprite(
            screen,
            self.arrow_right, self.arrow_right_pressed, self.arrow_right_greyed,
            self.button_states['right'], right_disabled,
            box_x + inner_w - arrow_margin, char_y, arrow_h
        )

        for idx, cx in zip(visible_indices, positions):
            char = self.characters[idx]
            self._draw_character_sprite(screen, char, cx, sprite_y, is_selected=(idx == self.selected_character))

        # Bottom buttons
        button_y_offset = int(self.canvas_height * 0.315) + 18
        button_y     = self.canvas_y + self.canvas_height - button_y_offset
        button_margin = int(self.canvas_width * 0.213)

        select_x = self.canvas_x + button_margin
        self._draw_button_sprite(screen, self.button_a, self.button_a_pressed,
                                 self.button_states['a'], select_x, button_y, "Select")

        cancel_x = self.canvas_x + self.canvas_width - button_margin - int(self.canvas_width * 0.45)
        self._draw_button_sprite(screen, self.button_b, self.button_b_pressed,
                                 self.button_states['b'], cancel_x, button_y, "Cancel")

    # ── Drawing helpers ───────────────────────────────────────────────────────

    def _draw_scanlines(self, screen, rect):
        sh = 2
        for y in range(rect.top, rect.bottom, sh * 2):
            pygame.draw.rect(screen, self.bg_scanline_dark,  pygame.Rect(rect.left, y,      rect.width, sh))
            pygame.draw.rect(screen, self.bg_scanline_light, pygame.Rect(rect.left, y + sh, rect.width, sh))

    def _draw_bold_title(self, screen, text, center_x, y):
        """
        Renders `text` with pause_menu.py's bold tab-title fonts: uppercase
        letters from bold_font, lowercase from bold_lowercase_font, yellow
        with a 1px drop shadow. Mirrors the centre tab-title block in
        PauseMenu.draw() exactly so titles look identical across both menus.
        """
        # Rendering a lone ' ' through FlatBitmapFont.render() collapses to a
        # 1x1 dummy surface (it has no glyph, so the font never picks up a
        # height for it) — unlike pause_menu's single-word tab titles, this
        # title has a space in it, so it needs its own fixed-width gap here.
        space_w = int(8 * self.bold_font.scale)
        char_surfs = []  # list of (surf_or_None, width) — None marks a space
        for ch in text:
            if ch == ' ':
                char_surfs.append((None, space_w))
                continue
            s = (self.bold_font if ch.isupper() else self.bold_lowercase_font).render(ch)
            s = s.copy()
            s.fill((255, 255, 0), special_flags=pygame.BLEND_RGBA_MULT)
            char_surfs.append((s, s.get_width()))

        total_w = sum(w for _, w in char_surfs) + 5 * (len(char_surfs) - 1)
        max_h   = max(s.get_height() for s, _ in char_surfs if s is not None)
        x       = center_x - total_w // 2

        for s, w in char_surfs:
            if s is not None:
                shadow = s.copy()
                shadow.fill((0, 0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                oy = max_h - s.get_height()
                screen.blit(shadow, (x + 1, y + oy + 1))
                screen.blit(s,      (x,     y + oy))
            x += w + 5

    def _render_text_with_shadow(self, screen, text, position, anchor='center'):
        shadow_surf = self.bitmap_font.render(text).copy()
        shadow_surf.fill(self.text_shadow_color, special_flags=pygame.BLEND_RGBA_MULT)
        text_surf   = self.bitmap_font.render(text).copy()
        text_surf.fill(self.text_color, special_flags=pygame.BLEND_RGBA_MULT)

        sx = position[0] + self.shadow_offset[0]
        sy = position[1] + self.shadow_offset[1]

        def _rect(s, pos):
            if anchor == 'center':  return s.get_rect(center=pos)
            if anchor == 'midleft': return s.get_rect(midleft=pos)
            return s.get_rect(topleft=pos)

        screen.blit(shadow_surf, _rect(shadow_surf, (sx, sy)))
        screen.blit(text_surf,   _rect(text_surf,   position))

    def _draw_tiled_background(self, screen, rect):
        if not self.bg_texture:
            self._draw_scanlines(screen, rect)
            return
        ox = self.bg_offset_x % self._bg_tile_w
        oy = self.bg_offset_y % self._bg_tile_h
        prev = screen.get_clip()
        screen.set_clip(rect)
        # Always blit from the same world origin as the full-screen background
        # so the tile pattern is continuous no matter which sub-rect is clipped
        # (e.g. the inset interior of a fallback-border box). Anchoring to
        # rect.left/top instead would shift the pattern relative to everything
        # outside that rect.
        screen.blit(self.bg_texture, (-ox, -oy))
        screen.set_clip(prev)

    def _draw_9slice_sprite(self, screen, sprite, x, y, width, height, corner_size=16):
        """
        Scales a border sprite without distorting the corners.
        Corners are scaled by border_scale; edges stretch to fill the gap.
        """
        if not sprite:
            return False

        sw, sh       = sprite.get_width(), sprite.get_height()
        border_scale = 4.0
        cw = min(corner_size, sw // 3)
        ch = min(corner_size, sh // 3)
        # Clamp the scaled corner thickness to at most half the box's own
        # width/height. Without this, a small box can make mw/mh negative
        # below, and pygame.transform.scale raises ValueError on a negative
        # size — aborting this function partway through its draw sequence
        # (corners first, then edges, then center fill), leaving whichever
        # slices hadn't been blitted yet simply missing.
        scw = min(int(cw * border_scale), max(1, width // 2))
        sch = min(int(ch * border_scale), max(1, height // 2))
        mw  = max(0, width  - 2 * scw)
        mh  = max(0, height - 2 * sch)

        def _sub(rx, ry, rw, rh):
            return sprite.subsurface(pygame.Rect(rx, ry, rw, rh))

        tl = _sub(0,       0,       cw,          ch)
        tr = _sub(sw - cw, 0,       cw,          ch)
        bl = _sub(0,       sh - ch, cw,          ch)
        br = _sub(sw - cw, sh - ch, cw,          ch)
        te = _sub(cw,      0,       sw - 2*cw,   ch)
        be = _sub(cw,      sh - ch, sw - 2*cw,   ch)
        le = _sub(0,       ch,      cw,          sh - 2*ch)
        re = _sub(sw - cw, ch,      cw,          sh - 2*ch)
        ce = _sub(cw,      ch,      sw - 2*cw,   sh - 2*ch)

        def _blit(surf, dx, dy, dw, dh):
            screen.blit(pygame.transform.scale(surf, (dw, dh)), (dx, dy))

        _blit(tl, x,           y,           scw, sch)
        _blit(tr, x+width-scw, y,           scw, sch)
        _blit(bl, x,           y+height-sch, scw, sch)
        _blit(br, x+width-scw, y+height-sch, scw, sch)
        _blit(te, x+scw,       y,           mw,  sch)
        _blit(be, x+scw,       y+height-sch, mw,  sch)
        _blit(le, x,           y+sch,       scw, mh)
        _blit(re, x+width-scw, y+sch,       scw, mh)
        _blit(ce, x+scw,       y+sch,       mw,  mh)
        return True

    def _draw_button_sprite(self, screen, sprite_normal, sprite_pressed, is_pressed, x, y, label):
        sprite = (sprite_pressed if (is_pressed and sprite_pressed) else sprite_normal)
        btn_h  = max(2, int(self.canvas_height * 0.06))

        if sprite:
            # Whole-number scale factor (see _draw_arrow_sprite) instead of
            # a plain float scale, so button sprites stay pixel-crisp like
            # the rest of the menu. actual_h can differ slightly from the
            # requested btn_h — label placement uses the real value.
            scaled, actual_h = self._scale_pixel_art(sprite, btn_h)
            scaled_w = scaled.get_width()
            screen.blit(scaled, (x, y))
            self._render_text_with_shadow(
                screen, label,
                (x + scaled_w + int(5 * RENDER_SCALE), y + actual_h // 2),
                anchor='midleft'
            )
        else:
            r   = btn_h // 2
            col = (140, 140, 140) if is_pressed else (180, 180, 180)
            pygame.draw.circle(screen, col,             (x + r, y + r), r)
            pygame.draw.circle(screen, (100, 100, 100), (x + r, y + r), r, 3)
            self._render_text_with_shadow(screen, label, (x + btn_h + 10, y + r), anchor='midleft')

    def _draw_arrow_sprite(self, screen, sprite_normal, sprite_pressed, sprite_greyed,
                           is_pressed, is_disabled, x, y, target_h):
        """Pressed state takes priority over disabled — so you see the flash before the arrow greys out."""
        if is_pressed and sprite_pressed:
            sprite = sprite_pressed
        elif is_disabled and sprite_greyed:
            sprite = sprite_greyed
        else:
            sprite = sprite_normal

        # Snap to the nearest whole-number scale factor (same as
        # _scale_pixel_art / character sprites) instead of a plain float
        # scale — a fractional factor duplicates some source pixel
        # rows/columns unevenly, which reads as smearing/blur on crisp
        # pixel art. This matches how the background texture and fonts
        # are scaled by a flat integer factor (font_scale) for the same
        # reason. target_h is a target, not exact — actual on-screen
        # height snaps to the nearest 1x/2x/3x/etc, so we center off the
        # real scaled size returned here rather than the requested target_h.
        if sprite:
            scaled, _ = self._scale_pixel_art(sprite, target_h)
            screen.blit(scaled, scaled.get_rect(center=(x, y)))
        else:
            color = (140, 140, 140) if is_pressed else (80, 80, 80) if is_disabled else (180, 180, 180)
            box   = pygame.Rect(x - target_h//2, y - target_h//2, target_h, target_h)
            pygame.draw.rect(screen, color,             box)
            pygame.draw.rect(screen, (100, 100, 100),  box, 3)

    def _draw_character_sprite(self, screen, character, x, y, is_selected):
        """Selected → animated walk cycle. Unselected → static idle frame.
        Shadow first, so the sprite draws on top of it (same draw order
        as the real in-game shadow / title_screen.py's own character
        row — see _draw_character_shadow)."""
        if is_selected:
            if character['has_walk'] and character['walk_frames']:
                frame = character['walk_frames'][character['animation_frame']]
                rect = frame.get_rect(center=(x, y))
                self._draw_character_shadow(screen, rect.centerx, rect.bottom, rect.width)
                screen.blit(frame, rect)
                return
        else:
            if character['has_idle'] and character['idle_sprite']:
                rect = character['idle_sprite'].get_rect(center=(x, y))
                self._draw_character_shadow(screen, rect.centerx, rect.bottom, rect.width)
                screen.blit(character['idle_sprite'], rect)
                return
        self._draw_fallback_sprite(screen, character, x, y)

    def _draw_fallback_sprite(self, screen, character, x, y):
        size = int(self.canvas_height * 0.15)
        self._draw_character_shadow(screen, x, y + size // 2, size)
        if not character['unlocked']:
            r = pygame.Rect(x - size//2, y - size//2, size, size)
            pygame.draw.rect(screen, (30, 30, 30), r)
            pygame.draw.rect(screen, (80, 80, 80), r, 2)
        else:
            pygame.draw.circle(screen, (100, 100, 200), (x, y), size // 2)
            pygame.draw.circle(screen, (0,   0,   0),   (x, y), size // 2, 3)