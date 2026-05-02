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

    def _try_load(self, char, filename):
        path = os.path.join(self.folder, filename)
        if not os.path.exists(path):
            return
        try:
            img = pygame.image.load(path).convert_alpha()
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
        self._portrait_cache = {}

        self._sheet   = None
        self._frame_w = 0
        self._frame_h = 0

        self._anim_t   = 0.0
        self._anim_dur = 0.18
        self._state    = 'hidden'

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

    # Descender glyphs (p, q, g) need a downward nudge so they sit on the baseline
    _DESCENDER_OFFSETS = {'p': 10, 'q': 10, 'g': 10, 'y': 15}

    def _render_text_line(self, screen, text, x, y, color=(255, 255, 255), spacing=1):
        """Blit a mixed-case line bottom-aligned to a shared baseline."""
        max_h = 0
        for ch in text:
            font = (self._font_numbers if ch.isdigit() else self._font_upper if (ch.isupper() or not ch.isalpha()) else self._font_lower)
            if ch in font.glyphs:
                max_h = max(max_h, font.glyphs[ch].get_height())

        cx = x
        for ch in text:
            font = (self._font_numbers if ch.isdigit() else self._font_upper if (ch.isupper() or not ch.isalpha()) else self._font_lower)
            if ch in font.glyphs:
                g = font.glyphs[ch].copy()
                g.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
                oy = max_h - g.get_height() + self._DESCENDER_OFFSETS.get(ch, 0)
                screen.blit(g, (cx, y + oy))
                cx += g.get_width() + spacing
            elif ch == ' ':
                cx += int(6 * max(1, _S))

    def _line_height(self):
        lh = max(self._font_upper.line_height(), self._font_lower.line_height(), self._font_numbers.line_height())
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

    def show(self, text, npc_name="NPC", is_final=False, item=None, portrait_key=None):
        self.current_text  = text
        self.npc_name      = npc_name
        self.is_final      = is_final
        self.received_item = item
        self._portrait_key = portrait_key
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

    def update(self, dt):
        if self._state == 'hidden':
            return

        self._anim_t = min(self._anim_t + dt, self._anim_dur)

        if self._state == 'opening' and self._anim_t >= self._anim_dur:
            self._state = 'open'
        elif self._state == 'closing' and self._anim_t >= self._anim_dur:
            self._state = 'hidden'
            self.active = False

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

        box_y = self.screen_height - box_h - max(60, int(self.screen_height * 0.08))

        # Portrait (optional)
        portrait_surf = None
        portrait_w    = 0
        if self._portrait_key:
            portrait_surf = self._load_portrait(self._portrait_key)
        if portrait_surf:
            portrait_w = int(portrait_surf.get_width() * box_h / portrait_surf.get_height())

        total_w    = portrait_w + box_w
        start_x    = (self.screen_width - total_w) // 2
        box_x      = start_x + portrait_w

        temp = pygame.Surface((total_w, box_h), pygame.SRCALPHA)

        if portrait_surf:
            temp.blit(pygame.transform.scale(portrait_surf, (portrait_w, box_h)), (0, 0))

        if scaled_box:
            temp.blit(scaled_box, (portrait_w, 0))
        else:
            pygame.draw.rect(temp, colors['DARK_GRAY'], pygame.Rect(portrait_w, 0, box_w, box_h))
            pygame.draw.rect(temp, colors['CYAN'],      pygame.Rect(portrait_w, 0, box_w, box_h), 3)

        # Text — only render when fully open (skip during transition)
        if self._state == 'open':
            pad      = max(6, int(box_w * 0.04))
            lh       = self._line_height()
            spacing  = 4
            max_w    = box_w - (8 + pad) * 2
            visible  = self.current_text[:self._chars_shown]

            # Word-wrap
            lines    = []
            cur_line = []
            for word in visible.split(' '):
                test = ' '.join(cur_line + [word])
                tw   = self._text_width(test, spacing) if self._has_bitmap_font() else self._fallback.size(test)[0]
                if tw <= max_w:
                    cur_line.append(word)
                else:
                    if cur_line:
                        lines.append(' '.join(cur_line))
                    cur_line = [word]
            if cur_line:
                lines.append(' '.join(cur_line))

            ty = pad - 7
            for line in lines[:4]:
                tx = portrait_w + 8 + pad
                if self._has_bitmap_font():
                    self._render_text_line(temp, line, tx, ty, spacing=spacing)
                else:
                    temp.blit(self._fallback.render(line, True, colors['WHITE']), (tx, ty))
                ty += lh + 2

        # Pixelated scale-in effect: downscale to a low-res version then upscale back
        PIXEL_STEPS = 6
        draw_w = max(1, int(total_w * progress))
        draw_h = max(1, int(box_h  * progress))
        lo_w   = max(1, int(total_w / PIXEL_STEPS * max(1, round(progress * PIXEL_STEPS))))
        lo_h   = max(1, int(box_h   / PIXEL_STEPS * max(1, round(progress * PIXEL_STEPS))))

        final = pygame.transform.scale(
            pygame.transform.scale(temp, (lo_w, lo_h)),
            (draw_w, draw_h)
        )

        screen.blit(final,
                    (start_x + (total_w - draw_w) // 2,
                     box_y   + (box_h   - draw_h) // 2))