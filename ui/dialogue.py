import pygame
import os
import random
from config.settings import RENDER_SCALE

_S = max(1, RENDER_SCALE)


class _BitmapFont:
    """Loads per-character PNG glyphs from a folder and blits them as text."""

    def __init__(self, folder, scale=1):
        self.folder = folder
        self.scale  = scale
        self.glyphs: dict = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.folder):
            return
        chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        special = {
            ':': 'colon.png', '/': 'slash.png', '.': 'period.png',
            ',': 'comma.png', '!': 'exclamation.png', '?': 'question.png',
            '-': 'dash.png', "'": 'apostrophe.png',
            '(': 'open_paren.png', ')': 'close_paren.png',
        }
        for ch in chars:
            self._try_load(ch, f'{ch}.png')
        for ch, fname in special.items():
            self._try_load(ch, fname)

    @staticmethod
    def _trim_left_bearing(surf):
        """Crop transparent columns off the left of a glyph.

        Font sheets often give each letter a different left pad (e.g. 'T'
        has more empty space than 'i'). Without trimming, a dialogue line
        that starts with a tight glyph appears further left than one that
        starts with a padded glyph — the classic "second row is more to
        the left" look. Trimming once at load makes every line's first
        ink pixel share the same x origin.
        """
        w, h = surf.get_size()
        if w <= 1 or h <= 0:
            return surf
        # Scan left → right for the first column with any visible pixel.
        left = 0
        found = False
        for x in range(w):
            for y in range(h):
                if surf.get_at((x, y))[3] > 0:
                    left = x
                    found = True
                    break
            if found:
                break
        if not found or left == 0:
            return surf
        # Keep at least 1 px width.
        new_w = w - left
        if new_w < 1:
            return surf
        return surf.subsurface((left, 0, new_w, h)).copy()

    def _try_load(self, char, filename):
        path = os.path.join(self.folder, filename)
        if not os.path.exists(path):
            return
        try:
            img = pygame.image.load(path).convert_alpha()
            # Trim before scaling so the crop is in source-pixel units and
            # the scaled result stays sharp under integer scale factors.
            img = self._trim_left_bearing(img)
            if self.scale != 1:
                img = pygame.transform.scale(img, (
                    int(img.get_width()  * self.scale),
                    int(img.get_height() * self.scale),
                ))
            self.glyphs[char] = img
        except Exception:
            pass

    def line_height(self):
        if not self.glyphs:
            return int(12 * self.scale)
        return max(g.get_height() for g in self.glyphs.values())

    def text_width(self, text, spacing=1):
        w = sum(
            self.glyphs[ch].get_width() + spacing if ch in self.glyphs else int(6 * self.scale)
            for ch in text.upper()
        )
        return max(0, w - spacing)

    def render_to(self, screen, text, x, y, color=(255, 255, 255), spacing=1):
        cx = x
        for ch in text.upper():
            if ch in self.glyphs:
                g = self.glyphs[ch].copy()
                g.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
                screen.blit(g, (cx, y))
                cx += g.get_width() + spacing
            elif ch == ' ':
                cx += int(6 * self.scale)


class DialogueBox:
    ANIM_COLS = 1
    ANIM_FPS  = 12

    def __init__(self, screen_width, screen_height):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.active        = False
        self.current_text  = ""
        self.npc_name      = "NPC"
        self.is_final      = False
        self.received_item = None

        self._portrait_key   = None
        self._is_narrator    = False
        self._portrait_cache = {}
        self._player_ref     = None   # set via set_player() after construction

        self._sheet   = None
        self._frame_w = 0
        self._frame_h = 0

        self._anim_t   = 0.0
        self._anim_dur = 0.18
        self._state    = 'hidden'
        self._on_close = None

        # Typewriter reveal
        self._chars_shown        = 0
        self._char_timer         = 0.0
        self._char_delay         = 0.04
        self._chars_per_tick_min = 2
        self._chars_per_tick_max = 5

        self._load_sheet()

        font_scale = 5
        self._font_upper   = _BitmapFont('assets/ui/fonts/uppercase', scale=font_scale)
        self._font_lower   = _BitmapFont('assets/ui/fonts/lowercase', scale=font_scale)
        self._font_numbers = _BitmapFont('assets/ui/fonts/numbers',   scale=font_scale)
        self._fallback     = pygame.font.Font(None, max(14, int(20 / _S)))

    def _load_sheet(self):
        path = 'assets/ui/textbox/textbox.png'
        if not os.path.exists(path):
            return
        try:
            sheet = pygame.image.load(path).convert_alpha()
            self._frame_w = sheet.get_width() // self.ANIM_COLS
            self._frame_h = sheet.get_height()
            self._sheet   = sheet
        except Exception:
            pass

    def _get_frame(self, idx):
        if not self._sheet:
            return None
        idx = max(0, min(idx, self.ANIM_COLS - 1))
        return self._sheet.subsurface(pygame.Rect(idx * self._frame_w, 0, self._frame_w, self._frame_h))

    def _load_portrait(self, key):
        if key in self._portrait_cache:
            return self._portrait_cache[key]
        surf = None
        path = f'assets/portraits/{key}.png'
        if os.path.exists(path):
            try:
                surf = pygame.image.load(path).convert_alpha()
            except Exception:
                pass
        self._portrait_cache[key] = surf
        return surf

    def _has_bitmap_font(self):
        return bool(self._font_upper.glyphs or self._font_lower.glyphs or self._font_numbers.glyphs)

    # Extra downward nudge for descender glyphs so their bottoms clear the
    # baseline the same way as in the source font sheets. Tweak these if
    # p/q/g/y sit too high or low relative to other letters.
    _DESCENDER_OFFSETS = {'p': 15, 'q': 15, 'g': 15, 'y': 15}

    def _render_text_line(self, screen, text, x, y, color=(255, 255, 255), spacing=1):
        """Blit a mixed-case line bottom-aligned to the *shared* font baseline.

        Uses the global line height (tallest glyph across the whole font), not
        the tallest glyph on *this* line. Per-line max_h made rows with only
        short lowercase sit higher than rows that contained a capital — the
        second row looked "weirdly aligned" next to the first.
        """
        base_h = self._line_height()
        cx = x
        for ch in text:
            font = (self._font_numbers if ch.isdigit()
                    else self._font_upper if (ch.isupper() or not ch.isalpha())
                    else self._font_lower)
            if ch in font.glyphs:
                g = font.glyphs[ch].copy()
                g.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
                oy = base_h - g.get_height() + self._DESCENDER_OFFSETS.get(ch, 0)
                screen.blit(g, (cx, y + oy))
                cx += g.get_width() + spacing
            elif ch == ' ':
                cx += int(6 * max(1, _S))

    def _line_height(self):
        """Tallest glyph across upper/lower/number sheets — the shared baseline
        every dialogue line is measured against."""
        lh = max(self._font_upper.line_height(), self._font_lower.line_height(),
                 self._font_numbers.line_height())
        return lh if lh > 0 else self._fallback.get_linesize()

    def _text_width(self, text, spacing=1):
        w = 0
        for ch in text:
            font = (self._font_numbers if ch.isdigit() else self._font_upper if (ch.isupper() or not ch.isalpha()) else self._font_lower)
            if ch in font.glyphs:
                w += font.glyphs[ch].get_width() + spacing
            elif ch == ' ':
                w += int(6 * max(1, _S))
        return max(0, w - spacing)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_player(self, player):
        """Give the dialogue box a reference to the player so it can pick
        transformation-aware portraits automatically (e.g. goku → goku_ssj)."""
        self._player_ref = player

    def _resolve_portrait_key(self, key):
        """Return the best portrait key for the current player state.

        If the requested portrait matches the player's character name AND the
        player is currently transformed, this tries the ``<key>_ssj`` portrait
        first.  If that file doesn't exist it falls back to the original key so
        nothing breaks when the SSJ portrait hasn't been added yet.
        """
        if not key:
            return key
        player = getattr(self, '_player_ref', None)
        if not player:
            return key
        character = getattr(player, 'character', None)
        if not character or key != character:
            return key
        ts = getattr(player, 'transformation', None)
        if not ts or not ts.is_transformed:
            return key
        ssj_key  = f'{key}_ssj'
        ssj_path = os.path.join('assets', 'portraits', f'{ssj_key}.png')
        return ssj_key if os.path.exists(ssj_path) else key

    def show(self, text, npc_name="NPC", is_final=False, item=None, portrait_key=None,
             on_close=None, is_narrator=False):
        """is_narrator=True floats the box in the vertical middle of the
        screen (true narration lines only). Everything else — portrait
        speech, and portrait-less info lines like level-up notices —
        stays anchored at the bottom like a normal speaker box."""
        self.current_text  = text
        self.npc_name      = npc_name
        self.is_final      = is_final
        self.received_item = item
        self._portrait_key = self._resolve_portrait_key(portrait_key)
        self._is_narrator  = is_narrator
        self._on_close      = on_close
        if not self.active:
            self.active   = True
            self._state   = 'opening'
            self._anim_t  = 0.0
        # Always reset typewriter on new text
        self._chars_shown = 0
        self._char_timer  = 0.0

    def hide(self):
        if self._state in ('open', 'opening'):
            self._state  = 'closing'
            self._anim_t = 0.0

    # ── Layout / capacity helpers ───────────────────────────────────────────
    # These mirror the geometry math in draw() so callers (e.g. the cutscene
    # editor) can check whether a string will fit in the box *before* it's
    # ever shown, instead of only finding out at runtime when draw() quietly
    # drops whatever doesn't fit in the first MAX_LINES lines.
    MAX_LINES = 4

    def _box_layout(self, portrait_key=None):
        """Return (box_w, box_h, portrait_w, max_w) for the current screen
        size and an optional portrait, matching draw()'s layout exactly."""
        frame    = self._get_frame(0)
        target_h = max(1, int(self.screen_height * 0.3))

        if frame:
            sf    = max(1, round(target_h / self._frame_h)) if self._frame_h > 0 else 1
            box_w = self._frame_w * sf
            box_h = self._frame_h * sf
        else:
            box_w, box_h = int(self.screen_width * 0.6), target_h

        portrait_w = 0
        key = self._resolve_portrait_key(portrait_key) if portrait_key else None
        if key:
            surf = self._load_portrait(key)
            if surf and surf.get_height() > 0:
                # Same integer scale as draw() so layout matches on-screen pixels.
                ps = max(1, round(box_h / surf.get_height()))
                portrait_w = surf.get_width() * ps

        pad   = max(6, int(box_w * 0.04))
        max_w = box_w - (8 + pad) * 2
        return box_w, box_h, portrait_w, max_w

    def wrap_text(self, text, max_w=None, spacing=4, portrait_key=None):
        """Word-wrap *text* to *max_w* pixels. Same algorithm draw() uses.

        Hard newlines ('\\n' from the cutscene editor's dialogue text field)
        become forced line breaks. Leading/trailing spaces on each visual
        line are stripped so a wrapped row never starts further left/right
        than its neighbours because of accidental whitespace.
        """
        if max_w is None:
            _, _, _, max_w = self._box_layout(portrait_key)
        lines = []

        def _width(s):
            if self._has_bitmap_font():
                return self._text_width(s, spacing)
            return self._fallback.size(s)[0]

        # Split on hard breaks first, then soft-wrap each paragraph.
        for paragraph in (text or '').split('\n'):
            words = [w for w in paragraph.split(' ') if w != '']
            if not words:
                lines.append('')
                continue
            cur_line = []
            for word in words:
                test = word if not cur_line else ' '.join(cur_line + [word])
                if _width(test) <= max_w:
                    cur_line.append(word)
                    continue
                if cur_line:
                    lines.append(' '.join(cur_line))
                    cur_line = []
                # Single word wider than the box — put it on its own line
                # rather than looping forever; draw will simply clip.
                cur_line = [word]
            if cur_line:
                lines.append(' '.join(cur_line))
        return lines

    def fits_box(self, text, portrait_key=None, max_lines=None):
        """True if *text* renders fully within max_lines — i.e. nothing
        would be cut off the bottom of the box."""
        max_lines = self.MAX_LINES if max_lines is None else max_lines
        return len(self.wrap_text(text, portrait_key=portrait_key)) <= max_lines

    def update(self, dt):
        if self._state == 'hidden':
            return

        self._anim_t = min(self._anim_t + dt, self._anim_dur)

        if self._state == 'opening' and self._anim_t >= self._anim_dur:
            self._state = 'open'
        elif self._state == 'closing' and self._anim_t >= self._anim_dur:
            self._state = 'hidden'
            self.active = False
            cb, self._on_close = self._on_close, None
            if cb:
                cb()

        if self._state == 'open' and self._chars_shown < len(self.current_text):
            self._char_timer += dt
            if self._char_timer >= self._char_delay:
                self._char_timer -= self._char_delay
                self._chars_shown = min(
                    self._chars_shown + random.randint(self._chars_per_tick_min, self._chars_per_tick_max),
                    len(self.current_text)
                )

    def draw(self, screen, colors):
        if self._state == 'hidden' or not self.active:
            return

        # Ease-out progress for open/close animation
        if self._state == 'opening':
            t = self._anim_t / self._anim_dur if self._anim_dur > 0 else 1.0
        elif self._state == 'closing':
            t = 1.0 - (self._anim_t / self._anim_dur if self._anim_dur > 0 else 1.0)
        else:
            t = 1.0
        progress = 1.0 - (1.0 - t) ** 2

        frame = self._get_frame(0)
        target_h = max(1, int(self.screen_height * 0.3))

        if frame:
            sf    = max(1, round(target_h / self._frame_h)) if self._frame_h > 0 else 1
            box_w = self._frame_w * sf
            box_h = self._frame_h * sf
            scaled_box = pygame.transform.scale(frame, (box_w, box_h))
        else:
            box_w, box_h = int(self.screen_width * 0.6), target_h
            scaled_box = None

        # True narrator lines float in the vertical middle of the screen
        # instead of the usual bottom-anchored speaker position. Portrait
        # boxes and portrait-less "info" boxes (e.g. level-up notices) both
        # stay bottom-anchored — only explicit narration centers.
        is_narrator = self._is_narrator
        if is_narrator:
            box_y = (self.screen_height - box_h) // 2
        else:
            box_y = self.screen_height - box_h - max(60, int(self.screen_height * 0.08))

        # Portrait (optional) — integer nearest-neighbour scale so every
        # source pixel maps to the same number of dest pixels (avoids the
        # "some columns thinner than others" look from non-integer scale).
        portrait_surf = None
        portrait_w    = 0
        portrait_draw = None
        if self._portrait_key:
            portrait_surf = self._load_portrait(self._portrait_key)
        if portrait_surf and portrait_surf.get_height() > 0:
            ps = max(1, round(box_h / portrait_surf.get_height()))
            portrait_w = portrait_surf.get_width() * ps
            portrait_h = portrait_surf.get_height() * ps
            portrait_draw = pygame.transform.scale(
                portrait_surf, (portrait_w, portrait_h))

        total_w    = portrait_w + box_w
        start_x    = (self.screen_width - total_w) // 2

        temp = pygame.Surface((total_w, box_h), pygame.SRCALPHA)

        if portrait_draw is not None:
            # Centre vertically if integer scale didn't land exactly on box_h.
            py = (box_h - portrait_draw.get_height()) // 2
            temp.blit(portrait_draw, (0, py))

        if scaled_box:
            temp.blit(scaled_box, (portrait_w, 0))
        else:
            pygame.draw.rect(temp, colors['DARK_GRAY'], pygame.Rect(portrait_w, 0, box_w, box_h))
            pygame.draw.rect(temp, colors['CYAN'],      pygame.Rect(portrait_w, 0, box_w, box_h), 3)

        # Text — only render when fully open (skip during transition)
        if self._state == 'open':
            pad      = max(6, int(box_w * 0.04))
            lh       = self._line_height()
            # Horizontal gap between glyphs (also used by wrap_text).
            spacing  = 4
            # Extra pixels between successive lines. Raise this if rows feel
            # cramped; lower it if they feel too far apart.
            line_gap = 20
            # Vertical offset applied to the whole text block after it's
            # positioned (negative pulls it up toward the top border).
            # Always applied — for portrait boxes it offsets the fixed
            # top-anchored start; for centered no-portrait boxes it offsets
            # the computed center point. All rows share the same baseline
            # math, so this only shifts the whole block — not row 2
            # relative to row 1.
            top_nudge = -5
            max_w    = box_w - (8 + pad) * 2
            visible  = self.current_text[:self._chars_shown]

            lines = self.wrap_text(visible, max_w, spacing)[:self.MAX_LINES]

            # Boxes with no portrait (narrator lines and portrait-less info
            # lines like level-up notices) get a full-width box, so instead
            # of the speaker layout — left-aligned text hugging the top,
            # next to/after the portrait — the block is centred as a whole:
            # horizontally per-line within the box, and vertically as a
            # block within the box height. This is purely a text-layout
            # concern and is independent of `is_narrator` (which only
            # controls whether the box itself floats mid-screen or sits
            # bottom-anchored).
            no_portrait = not self._portrait_key

            # Same left edge for every row — never per-line adjusted.
            text_left = portrait_w + 8 + pad
            ty = pad + top_nudge

            if no_portrait and lines:
                block_h = len(lines) * lh + max(0, len(lines) - 1) * line_gap
                ty = (box_h - block_h) // 2 + top_nudge

            for line in lines:
                # Guard against any residual leading whitespace so row 2
                # can't drift left/right relative to row 1.
                line = line.lstrip()
                if no_portrait:
                    line_w = (self._text_width(line, spacing) if self._has_bitmap_font()
                              else self._fallback.size(line)[0])
                    line_x = portrait_w + max(0, (box_w - line_w) // 2)
                else:
                    line_x = text_left
                if self._has_bitmap_font():
                    self._render_text_line(temp, line, line_x, ty, spacing=spacing)
                else:
                    temp.blit(self._fallback.render(line, True, colors['WHITE']),
                              (line_x, ty))
                ty += lh + line_gap

        # Open/close presentation.
        #
        # Fully open/closed progress → blit 1:1. The old path always ran
        # temp through a double pygame.transform.scale (down to lo_w then
        # back up to draw_w). At progress≈1, integer truncation made
        # lo_w = total_w - 1 (or similar), so every open frame was a
        # non-integer upscale — classic "some pixel rows/cols thinner
        # than others" artifact on the box art and portrait.
        #
        # During the transition, only discrete step sizes are used and
        # the surface is scaled once to that exact size (no second stretch
        # to a mismatched continuous size), so pixels stay uniform.
        if progress >= 0.999:
            screen.blit(temp, (start_x, box_y))
        else:
            PIXEL_STEPS = 6
            step = max(1, min(PIXEL_STEPS, int(round(progress * PIXEL_STEPS))))
            draw_w = max(1, (total_w * step) // PIXEL_STEPS)
            draw_h = max(1, (box_h  * step) // PIXEL_STEPS)
            final = pygame.transform.scale(temp, (draw_w, draw_h))
            screen.blit(final,
                        (start_x + (total_w - draw_w) // 2,
                         box_y   + (box_h   - draw_h) // 2))


class DialogueChoiceMenu:
    """Selection menu for the 'dialogue_choice' event action — an optional
    prompt line plus a vertical list of options, navigated with Up/Down
    (or W/S) and confirmed with E. Same controls/animation language as
    objects.save_point.SavePointMenu, generalized to N options."""

    def __init__(self, screen_width, screen_height):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.active  = False
        self.prompt  = ""
        self.options = []          # list[str]
        self.selected_option = 0
        self._on_choice = None

        self.menu_sprite  = None
        self.arrow_sprite = None
        self._load_sprites()

        font_scale = 6
        self._font_upper   = _BitmapFont('assets/ui/fonts/uppercase', scale=font_scale)
        self._font_lower   = _BitmapFont('assets/ui/fonts/lowercase', scale=font_scale)
        self._font_numbers = _BitmapFont('assets/ui/fonts/numbers',   scale=font_scale)
        self._fallback     = pygame.font.Font(None, max(14, int(20 / _S)))

        self.text_color  = (255, 255, 255)
        self.arrow_color = (255, 215, 0)

        self.scale_progress = 0.0
        self.scale_speed = 8.0
        self.is_opening = False

        self._chars_shown = []      # per-option typewriter progress
        self._prompt_chars_shown = 0
        self.typewriter_speed = 25.0
        self._typewriter_timer = 0.0
        self.typewriter_complete = False

        self.arrow_blink_timer = 0.0
        self.arrow_blink_speed = 4
        self.arrow_visible = True

    def _load_sprites(self):
        try:
            self.menu_sprite = pygame.image.load('assets/ui/textbox/small_box.png').convert_alpha()
        except Exception:
            self.menu_sprite = None
        try:
            self.arrow_sprite = pygame.image.load('assets/ui/textbox/arrow.png').convert_alpha()
        except Exception:
            self.arrow_sprite = None

    # ── mixed-case rendering helpers (mirrors DialogueBox's own) ────────────
    _DESCENDER_OFFSETS = {'p': 10, 'q': 10, 'g': 10, 'y': 15}

    def _font_for(self, ch):
        return (self._font_numbers if ch.isdigit()
                else self._font_upper if (ch.isupper() or not ch.isalpha())
                else self._font_lower)

    def _has_bitmap_font(self):
        return bool(self._font_upper.glyphs or self._font_lower.glyphs or self._font_numbers.glyphs)

    def _line_height(self):
        lh = max(self._font_upper.line_height(), self._font_lower.line_height(), self._font_numbers.line_height())
        return lh if lh > 0 else self._fallback.get_linesize()

    def _text_width(self, text, spacing=1):
        w = 0
        for ch in text:
            font = self._font_for(ch)
            if ch in font.glyphs:
                w += font.glyphs[ch].get_width() + spacing
            elif ch == ' ':
                w += int(6 * max(1, _S))
        return max(0, w - spacing)

    def _render_line(self, screen, text, x, y, color, spacing=1):
        max_h = 0
        for ch in text:
            font = self._font_for(ch)
            if ch in font.glyphs:
                max_h = max(max_h, font.glyphs[ch].get_height())
        cx = x
        for ch in text:
            font = self._font_for(ch)
            if ch in font.glyphs:
                g = font.glyphs[ch].copy()
                g.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
                oy = max_h - g.get_height() + self._DESCENDER_OFFSETS.get(ch, 0)
                screen.blit(g, (cx, y + oy))
                cx += g.get_width() + spacing
            elif ch == ' ':
                cx += int(6 * max(1, _S))

    # ── Public API ───────────────────────────────────────────────────────────

    def open(self, options, prompt="", on_choice=None):
        """options: list[str] of option labels. on_choice(index) is called
        once the player confirms with E."""
        self.options    = list(options)
        self.prompt     = prompt or ""
        self._on_choice = on_choice
        self.selected_option = 0
        self.active      = True
        self.is_opening  = True
        self.scale_progress = 0.0
        self._chars_shown        = [0] * len(self.options)
        self._prompt_chars_shown = 0
        self._typewriter_timer   = 0.0
        self.typewriter_complete = False
        self.arrow_blink_timer   = 0.0
        self.arrow_visible       = True

    def close(self):
        self.active     = False
        self.is_opening  = False
        self._on_choice  = None

    def update(self, dt):
        if not self.active:
            return

        if self.is_opening and self.scale_progress < 1.0:
            self.scale_progress += self.scale_speed * dt
            if self.scale_progress >= 1.0:
                self.scale_progress = 1.0
                self.is_opening = False

        if self.scale_progress >= 1.0 and not self.typewriter_complete:
            self._typewriter_timer += dt
            chars_to_show = int(self._typewriter_timer * self.typewriter_speed)

            remaining = chars_to_show
            all_complete = True

            if self.prompt:
                if remaining >= len(self.prompt):
                    self._prompt_chars_shown = len(self.prompt)
                    remaining -= len(self.prompt)
                else:
                    self._prompt_chars_shown = remaining
                    remaining = 0
                    all_complete = False

            if all_complete:
                char_count = 0
                for i, option in enumerate(self.options):
                    option_length = len(option)
                    if char_count + option_length <= remaining:
                        self._chars_shown[i] = option_length
                        char_count += option_length
                    elif char_count < remaining:
                        self._chars_shown[i] = remaining - char_count
                        all_complete = False
                        break
                    else:
                        self._chars_shown[i] = 0
                        all_complete = False

            if all_complete:
                self.typewriter_complete = True

        self.arrow_blink_timer += dt
        blink_period = 1.0 / self.arrow_blink_speed
        if self.arrow_blink_timer >= blink_period:
            self.arrow_visible = not self.arrow_visible
            self.arrow_blink_timer = 0.0

    def handle_input(self, event):
        """Mirrors SavePointMenu.handle_input's shape, but this menu also
        calls on_choice(index) itself and closes on confirm, since the
        caller (EventRunner's dialogue_choice handler) has no per-frame
        poll loop to react to a return value the way Game does for the
        save point menu."""
        if not self.active or event.type != pygame.KEYDOWN:
            return None

        if event.key in (pygame.K_UP, pygame.K_w):
            if self.options:
                self.selected_option = (self.selected_option - 1) % len(self.options)
            return None

        if event.key in (pygame.K_DOWN, pygame.K_s):
            if self.options:
                self.selected_option = (self.selected_option + 1) % len(self.options)
            return None

        if event.key == pygame.K_e:
            if not self.typewriter_complete:
                # Early press just skips the typewriter reveal, same
                # convention as DialogueBox.
                self._prompt_chars_shown = len(self.prompt)
                self._chars_shown = [len(o) for o in self.options]
                self.typewriter_complete = True
                return None
            if self.options:
                index = self.selected_option
                cb, self._on_choice = self._on_choice, None
                self.active     = False
                self.is_opening = False
                if cb:
                    cb(index)
                return index

        return None

    def draw(self, screen):
        if not self.active:
            return

        scale_factor = self._ease_out_back(self.scale_progress)

        _sprite_w, _sprite_h = 144, 40
        base_scale = max(1, int(self.screen_height * 0.24 / _sprite_h))
        lh       = self._line_height()
        pad_y    = max(24, lh // 2)
        row_gap  = max(20, lh // 2)
        row_h    = lh + row_gap
        prompt_h = (lh + row_gap) if self.prompt else 0

        menu_width      = _sprite_w * base_scale
        baseline_height = _sprite_h * base_scale   # comfortable height the box art was designed for
        content_height  = pad_y * 2 + prompt_h + row_h * max(1, len(self.options))
        menu_height     = max(baseline_height, content_height)

        # Centre the (possibly shorter) content vertically inside the box
        # instead of hugging the top when the baseline height wins.
        extra_y = max(0, menu_height - content_height)

        current_width  = int(menu_width  * scale_factor)
        current_height = int(menu_height * scale_factor)

        menu_x = (self.screen_width - current_width) // 2
        menu_y = self.screen_height - current_height - 120

        if current_width > 0 and current_height > 0:
            if self.menu_sprite:
                scaled_sprite = pygame.transform.scale(self.menu_sprite, (current_width, current_height))
                screen.blit(scaled_sprite, (menu_x, menu_y))
            else:
                pygame.draw.rect(screen, (20, 20, 20), (menu_x, menu_y, current_width, current_height),
                                  border_radius=6)
                pygame.draw.rect(screen, (255, 215, 0), (menu_x, menu_y, current_width, current_height),
                                  3, border_radius=6)

        if self.scale_progress < 0.8:
            return

        menu_center_x = menu_x + current_width // 2
        text_y = menu_y + pad_y + int(extra_y // 2 * scale_factor)

        if self.prompt:
            display_prompt = self.prompt[:self._prompt_chars_shown]
            if display_prompt:
                if self._has_bitmap_font():
                    pw = self._text_width(display_prompt, spacing=6)
                    self._render_line(screen, display_prompt, menu_center_x - pw // 2, text_y,
                                       self.text_color, spacing=6)
                else:
                    surf = self._fallback.render(display_prompt, True, self.text_color)
                    screen.blit(surf, (menu_center_x - surf.get_width() // 2, text_y))
            text_y += prompt_h

        for i, option in enumerate(self.options):
            chars_to_show = self._chars_shown[i] if i < len(self._chars_shown) else 0
            display_text = option[:chars_to_show]
            option_y = text_y + i * row_h
            if not display_text:
                continue

            if self._has_bitmap_font():
                tw = self._text_width(display_text, spacing=6)
            else:
                tw = self._fallback.size(display_text)[0]
            text_x = menu_center_x - tw // 2 + 12  # nudge right, leaves room for the arrow on the left

            if i == self.selected_option and chars_to_show > 0 and self.arrow_visible:
                arrow_spacing = 10
                lh = self._line_height() if self._has_bitmap_font() else self._fallback.get_linesize()
                if self.arrow_sprite:
                    arrow_scale = lh / self.arrow_sprite.get_height()
                    scaled_arrow = pygame.transform.scale(
                        self.arrow_sprite,
                        (int(self.arrow_sprite.get_width() * arrow_scale),
                         int(self.arrow_sprite.get_height() * arrow_scale))
                    )
                    arrow_x = text_x - scaled_arrow.get_width() - arrow_spacing
                    arrow_y = option_y + (lh - scaled_arrow.get_height()) // 2
                    screen.blit(scaled_arrow, (arrow_x, arrow_y))
                else:
                    star_x = text_x - arrow_spacing
                    star_y = option_y + row_h // 2
                    scale = max(1, base_scale) * 1.2
                    star_points = [
                        (star_x, star_y - int(6 * scale)),
                        (star_x + int(3 * scale), star_y - int(2 * scale)),
                        (star_x + int(8 * scale), star_y - int(2 * scale)),
                        (star_x + int(4 * scale), star_y + int(1 * scale)),
                        (star_x + int(6 * scale), star_y + int(6 * scale)),
                        (star_x, star_y + int(3 * scale)),
                        (star_x - int(6 * scale), star_y + int(6 * scale)),
                        (star_x - int(4 * scale), star_y + int(1 * scale)),
                        (star_x - int(8 * scale), star_y - int(2 * scale)),
                        (star_x - int(3 * scale), star_y - int(2 * scale)),
                    ]
                    pygame.draw.polygon(screen, self.arrow_color, star_points)

            if self._has_bitmap_font():
                self._render_line(screen, display_text, text_x, option_y, self.text_color, spacing=6)
            else:
                surf = self._fallback.render(display_text, True, self.text_color)
                screen.blit(surf, (text_x, option_y))

    @staticmethod
    def _ease_out_back(t):
        c1 = 1.70158
        c3 = c1 + 1
        return 1 + c3 * pow(t - 1, 3) + c1 * pow(t - 1, 2)