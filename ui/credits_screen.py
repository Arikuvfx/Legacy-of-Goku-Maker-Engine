"""
Credits Screen
--------------
Full-screen credits sequence, opened from the pause menu's Options tab
(PauseMenu.handle_input returns 'open_credits' — see pause_menu.py).

Content is entirely data-driven from data/credits.json. That file is what
gets edited to change the credits — nothing in this module needs to
change. See that file's own comments for the format.

Each "slide" is a block of centered lines that fades in, holds, then
fades out before the next slide begins. Z/Enter/click advances a step
early (skip the fade-in, or jump straight to the next slide); ESC/X
bails out of the whole sequence immediately.

Three line styles, matching the request that drove this file's design:
  - Title lines (e.g. "GAME TITLE")    → single uppercase font, one style.
  - Role lines  (e.g. "LEAD DESIGN")   → scouter_stats font, single style.
  - Name lines  (e.g. "Jane Doe")      → uppercase/lowercase font pair,
                                          picked per character exactly
                                          like PauseMenu._draw_options_page's
                                          _make_words() does, so mixed-case
                                          names render with real lowercase
                                          letterforms instead of shouting.
A line's style is set explicitly in credits.json: {"title": "..."} or
{"role": "..."} pick those fonts. Everything else — a plain string, or
{"name": "..."} — is a name line. See credits.json's own comments for
examples.

State machine (self._phase): 'in' -> 'hold' -> 'out' -> next slide's 'in'.
Finishing the last slide's fade-out closes the screen and queues a
'close' result for the *next* handle_input() call, matching the calling
convention every other menu in this project uses (see PauseMenu, whose
handle_input() return value game.py switches on) — draw()/update() never
emit signals themselves.
"""

import json
import os
import pygame

from config.settings import RENDER_SCALE
from ui.pause_menu import FlatBitmapFont

DEFAULT_TIMING = {'fade_in': 0.8, 'hold': 2.5, 'fade_out': 0.8}
DEFAULT_PATH   = os.path.join('data', 'credits.json')

# Layout constants for mixed-case name rendering — match
# PauseMenu._draw_options_page's _ls/_word_gap exactly so a name typed in
# credits.json sits at the same letter/word spacing as the Options page.
_NAME_LETTER_SPACING = 6
_NAME_WORD_GAP       = 18

# Some glyph sets draw the comma sitting a bit high against the baseline —
# nudge it down a few px so it reads as a comma and not a stray apostrophe.
# Applies everywhere: name, role, and title lines all render per-character
# (see _render_name and _render_single_font_text) so this hooks into all of
# them the same way.
_COMMA_Y_OFFSET = 8


class CreditsScreen:

    def __init__(self, screen_width, screen_height, path=DEFAULT_PATH):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.path          = path
        self.active         = False

        # Each entry: {'lines': [...], 'fade_in': f, 'hold': h, 'fade_out': f}
        # A line is a plain string / {'name': ...} (mixed-case name font),
        # {'role': ...} (scouter_stats font), or {'title': ...} (single
        # uppercase font). '' is a blank spacer line.
        self.slides          = []
        self.slide_index      = 0
        self._phase           = 'in'   # 'in' | 'hold' | 'out'
        self._phase_t          = 0.0
        # Set once the final slide finishes fading out. update() stops
        # advancing anything once this is True; handle_input() reports
        # 'close' the next time it's called and then clears the flag.
        self._pending_close    = False

        self.text_color        = (255, 255, 255)
        self.text_shadow_color = (0, 0, 0)
        self.shadow_offset     = (2, 2)
        self.bg_color          = (0, 0, 0)
        self.line_gap          = int(10 * RENDER_SCALE)

        # Fonts — same folders/scale as PauseMenu so credits text matches
        # the rest of the UI's look exactly (see PauseMenu.__init__).
        font_scale = 8
        _ls = max(5, int(10 / RENDER_SCALE))
        self.name_uppercase_font = FlatBitmapFont('assets/ui/fonts/uppercase', letter_spacing=_ls, scale=font_scale)
        self.name_lowercase_font = FlatBitmapFont('assets/ui/fonts/lowercase', letter_spacing=_ls, scale=font_scale)
        # NOTE: assumes the scouter's stats font lives at
        # assets/ui/fonts/scouter_stats. If ScouterMenu loads it from a
        # different folder, update this path to match — FlatBitmapFont
        # silently falls back to a plain pygame font (no crash, just the
        # wrong look) if the folder doesn't exist, so a typo here won't be
        # obvious except visually.
        self.role_font = FlatBitmapFont('assets/ui/fonts/scouter_stats', letter_spacing=max(6, int(10/RENDER_SCALE)), scale=font_scale)
        self._role_letter_spacing = max(6, int(10 / RENDER_SCALE))
        # Title lines ('GAME TITLE', 'THANKS FOR PLAYING', ...) — its own
        # single-style renderer (see _render_title) rather than piggybacking
        # on the name font just because titles happen to be typed in caps.
        # Reuses the same uppercase glyph set as names (no new asset
        # folder needed) — swap this path if you'd rather point titles at
        # a dedicated title font.
        self.title_font = FlatBitmapFont('assets/ui/fonts/uppercase', letter_spacing=max(6, int(10/RENDER_SCALE)), scale=font_scale)
        self._title_letter_spacing = max(6, int(10 / RENDER_SCALE))

        self._load(path)

    # ── Data loading ─────────────────────────────────────────────────────────

    def _load(self, path):
        """Parses data/credits.json into self.slides. Falls back to a
        single explanatory slide (rather than crashing) if the file is
        missing or malformed, since this file is meant to be hand-edited
        by non-programmers and typos should be recoverable, not fatal."""
        fallback_reason = None
        data = None

        if not os.path.exists(path):
            fallback_reason = 'NO CREDITS FILE FOUND'
        else:
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
            except Exception as e:
                print(f'[CreditsScreen] failed to parse {path}: {e}')
                fallback_reason = 'CREDITS FILE ERROR'

        if fallback_reason:
            self.slides = [{'lines': [fallback_reason], **DEFAULT_TIMING}]
            return

        timing = {**DEFAULT_TIMING, **(data.get('timing') or {})}
        slides = []
        for raw in (data.get('slides') or []):
            if isinstance(raw, list):
                lines, slide_timing = raw, dict(timing)
            elif isinstance(raw, dict):
                lines = raw.get('lines', [])
                slide_timing = {
                    'fade_in':  raw.get('fade_in',  timing['fade_in']),
                    'hold':     raw.get('hold',     timing['hold']),
                    'fade_out': raw.get('fade_out', timing['fade_out']),
                }
            else:
                continue
            slides.append({'lines': self._expand_lines(lines), **slide_timing})

        self.slides = slides or [{'lines': ['NO CREDITS TO SHOW'], **DEFAULT_TIMING}]

    def _expand_lines(self, lines):
        """A single line entry can contain '\\n' to break onto multiple
        rendered lines without needing separate array entries, e.g.
        "My Cat\\nFor Sitting On The Keyboard" becomes two centered lines.
        Style (role/title/name) carries over to every piece. '' stays a
        single blank spacer line, not something to split."""
        expanded = []
        for line in lines:
            if isinstance(line, str):
                if line == '':
                    expanded.append(line)
                else:
                    expanded.extend(line.split('\n'))
            elif isinstance(line, dict):
                for key in ('role', 'title', 'name'):
                    if key in line:
                        expanded.extend({key: part} for part in str(line[key]).split('\n'))
                        break
                else:
                    expanded.append(line)
            else:
                expanded.append(line)
        return expanded

    def reload(self):
        """Re-reads data/credits.json from disk. Useful for iterating on
        the credits text without restarting the game (e.g. wire this up
        to a dev-tools hotkey)."""
        self._load(self.path)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def open(self):
        self.active          = True
        self.slide_index      = 0
        self._phase           = 'in'
        self._phase_t          = 0.0
        self._pending_close    = False

    def close(self):
        self.active = False

    # ── Update ───────────────────────────────────────────────────────────────

    def update(self, dt):
        if not self.active or self._pending_close:
            return
        slide = self.slides[self.slide_index]
        self._phase_t += dt

        if self._phase == 'in':
            if self._phase_t >= slide['fade_in']:
                self._phase, self._phase_t = 'hold', 0.0
        elif self._phase == 'hold':
            if self._phase_t >= slide['hold']:
                self._phase, self._phase_t = 'out', 0.0
        elif self._phase == 'out':
            if self._phase_t >= slide['fade_out']:
                self._advance_slide()

    def _advance_slide(self):
        if self.slide_index >= len(self.slides) - 1:
            # Last slide's fade-out just finished. Stop drawing/updating
            # now, but wait for handle_input() to actually report 'close'
            # so game.py finds out the same way it does for every other
            # menu — via handle_input()'s return value, not by polling
            # .active every frame.
            self._pending_close = True
            self.active          = False
            return
        self.slide_index += 1
        self._phase, self._phase_t = 'in', 0.0

    # ── Input ────────────────────────────────────────────────────────────────

    def handle_input(self, event):
        """
        Returns:
          'close' — the sequence finished on its own, or the player
                     backed out early with ESC/X
          None    — no action taken
        """
        if self._pending_close:
            self._pending_close = False
            return 'close'
        if not self.active:
            return None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._skip_forward()
            return None

        if event.type != pygame.KEYDOWN:
            return None

        if event.key in (pygame.K_ESCAPE, pygame.K_x):
            self.close()
            return 'close'

        if event.key in (pygame.K_z, pygame.K_RETURN):
            self._skip_forward()
            return None

        return None

    def _skip_forward(self):
        """Z / Enter / click: jump straight to the held state if still
        fading in, or straight to the next slide if already holding or
        fading out — so mashing confirm blows through the whole sequence
        instead of only ever nudging the current fade."""
        if self._phase == 'in':
            self._phase, self._phase_t = 'hold', 0.0
        else:
            self._advance_slide()

    # ── Line rendering ───────────────────────────────────────────────────────
    # Every renderer here returns a single flat Surface with the drop-shadow
    # already baked in, so draw() below never needs to know which font style
    # produced it — it just measures and centers Surfaces.

    def _add_shadow(self, surf):
        w, h = surf.get_size()
        out = pygame.Surface((w + self.shadow_offset[0], h + self.shadow_offset[1]), pygame.SRCALPHA)
        shadow = surf.copy()
        shadow.fill(self.text_shadow_color, special_flags=pygame.BLEND_RGBA_MULT)
        out.blit(shadow, self.shadow_offset)
        out.blit(surf, (0, 0))
        return out

    def _render_single_font_text(self, font, letter_spacing, text):
        """Renders text through a single font, matching the font's own
        render(text) call for spacing/kerning exactly as before — EXCEPT
        wherever there's a comma, which gets split out and rendered on
        its own so it can be nudged down by _COMMA_Y_OFFSET. Text with no
        comma is untouched: a single font.render(text) call, same as the
        original _render_role/_render_title, so word gaps stay exactly
        as the font produces them."""
        if ',' not in text:
            surf = font.render(text).copy()
            surf.fill(self.text_color, special_flags=pygame.BLEND_RGBA_MULT)
            return surf

        pieces = []  # list of (surface, extra_y_offset)
        parts = text.split(',')
        for i, part in enumerate(parts):
            if part != '':
                s = font.render(part).copy()
                s.fill(self.text_color, special_flags=pygame.BLEND_RGBA_MULT)
                pieces.append((s, 0))
            if i < len(parts) - 1:
                c = font.render(',').copy()
                c.fill(self.text_color, special_flags=pygame.BLEND_RGBA_MULT)
                pieces.append((c, _COMMA_Y_OFFSET))

        if not pieces:
            return pygame.Surface((1, 1), pygame.SRCALPHA)

        max_h = max(s.get_height() for s, _ in pieces)
        pad_bottom = _COMMA_Y_OFFSET
        # Only the seams where we split (around each comma) use this flat
        # letter_spacing gap; everything else inside a piece keeps the
        # font's own internal spacing since it was rendered as one string.
        total_w = sum(s.get_width() for s, _ in pieces) + letter_spacing * max(0, len(pieces) - 1)

        base = pygame.Surface((max(1, total_w), max_h + pad_bottom), pygame.SRCALPHA)
        cx = 0
        for s, extra_y in pieces:
            y = max_h - s.get_height() + extra_y
            base.blit(s, (cx, y))
            cx += s.get_width() + letter_spacing
        return base

    def _render_role(self, text):
        """Role lines ('LEAD DESIGN', 'PROGRAMMING', ...) — single font.
        Rendered as one font.render() call unless there's a comma, in
        which case it's split around the comma (see
        _render_single_font_text) so the comma can be nudged down."""
        surf = self._render_single_font_text(self.role_font, self._role_letter_spacing, text)
        return self._add_shadow(surf)

    def _render_title(self, text):
        """Title lines ('GAME TITLE', 'THANKS FOR PLAYING', ...) — single
        font, same approach as _render_role. Deliberately NOT routed
        through _render_name: that renderer's per-character upper/lower
        switching happens to look right on already-all-caps text, but
        it's coincidental, not an actual title style, and breaks the
        moment a title is typed in mixed case."""
        surf = self._render_single_font_text(self.title_font, self._title_letter_spacing, text)
        return self._add_shadow(surf)

    def _render_name(self, text):
        """Name lines — mixed-case, picking the uppercase_menu or
        lowercase_menu font per character exactly like
        PauseMenu._draw_options_page's _make_words()/_blit_label() do, so
        e.g. 'Jane Doe' renders with a real lowercase 'ane' and 'oe'
        instead of the whole name shouting in caps."""
        words = []
        for word in text.split(' '):
            glyphs = []
            for ch in word:
                font = self.name_uppercase_font if (ch.isupper() or not ch.isalpha()) else self.name_lowercase_font
                g = font.render(ch).copy()
                g.fill(self.text_color, special_flags=pygame.BLEND_RGBA_MULT)
                glyphs.append((ch, g))
            words.append(glyphs)

        max_h = max((g.get_height() for ws in words for _, g in ws), default=0)
        if max_h == 0:
            return pygame.Surface((1, 1), pygame.SRCALPHA)

        # Extra bottom padding so a comma nudged down by _COMMA_Y_OFFSET
        # doesn't get clipped off the bottom of the surface.
        pad_bottom = _COMMA_Y_OFFSET

        total_w = 0
        for wi, ws in enumerate(words):
            total_w += sum(g.get_width() for _, g in ws) + _NAME_LETTER_SPACING * max(0, len(ws) - 1)
            if wi < len(words) - 1:
                total_w += _NAME_WORD_GAP

        base = pygame.Surface((max(1, total_w), max_h + pad_bottom), pygame.SRCALPHA)
        cx = 0
        for wi, ws in enumerate(words):
            for ch, g in ws:
                y = max_h - g.get_height()
                if ch == ',':
                    y += _COMMA_Y_OFFSET
                base.blit(g, (cx, y))
                cx += g.get_width() + _NAME_LETTER_SPACING
            if wi < len(words) - 1:
                cx += _NAME_WORD_GAP - _NAME_LETTER_SPACING
        return self._add_shadow(base)

    def _render_line(self, line):
        """Dispatches a single credits.json line entry to the role, title,
        or name renderer. {'role': '...'} -> role font; {'title': '...'}
        -> title font; plain string, {'name': '...'}, or anything else ->
        name font (mixed case)."""
        if isinstance(line, dict) and 'role' in line:
            return self._render_role(str(line['role']))
        if isinstance(line, dict) and 'title' in line:
            return self._render_title(str(line['title']))
        if isinstance(line, dict) and 'name' in line:
            return self._render_name(str(line['name']))
        return self._render_name(str(line))

    # ── Draw ─────────────────────────────────────────────────────────────────

    def draw(self, screen):
        if not self.active:
            return
        screen.fill(self.bg_color)

        slide = self.slides[self.slide_index]
        alpha = self._current_alpha(slide)

        # Blank-line spacer height: use the name font's line height as the
        # reference, since it's what most lines use.
        blank_h = self.name_uppercase_font.get_line_height()

        rendered = []
        max_w, total_h = 0, 0
        for line in slide['lines']:
            if line == '':
                rendered.append(None)
                total_h += blank_h + self.line_gap
                continue
            surf = self._render_line(line)
            rendered.append(surf)
            max_w    = max(max_w, surf.get_width())
            total_h += surf.get_height() + self.line_gap
        total_h = max(0, total_h - self.line_gap)

        block = pygame.Surface((max(1, max_w), max(1, total_h)), pygame.SRCALPHA)
        y = 0
        for surf in rendered:
            lh = surf.get_height() if surf else blank_h
            if surf:
                bx = (max_w - surf.get_width()) // 2
                block.blit(surf, (bx, y))
            y += lh + self.line_gap

        block.set_alpha(alpha)
        rect = block.get_rect(center=(self.screen_width // 2, self.screen_height // 2))
        screen.blit(block, rect)

    def _current_alpha(self, slide):
        if self._phase == 'in':
            t = self._phase_t / slide['fade_in'] if slide['fade_in'] > 0 else 1.0
            return int(255 * min(1.0, max(0.0, t)))
        if self._phase == 'out':
            t = self._phase_t / slide['fade_out'] if slide['fade_out'] > 0 else 1.0
            return int(255 * min(1.0, max(0.0, 1.0 - t)))
        return 255