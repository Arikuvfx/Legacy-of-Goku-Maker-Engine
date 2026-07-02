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

_S = max(1, RENDER_SCALE)


class CharacterSwitchMenu:

    def __init__(self, screen_width, screen_height):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.active            = False
        self.selected_character = 0

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

        self._load_ui_sprites()
        self._load_character_sprites()

        self.animation_speed = 0.15  # seconds per walk frame

        font_scale = max(1, int(RENDER_SCALE))
        self.bitmap_font = BitmapFont(
            'assets/ui/fonts',
            letter_spacing=max(1, int(2 / RENDER_SCALE)),
            scale=font_scale
        )

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
        chars_dir = 'assets/characters'
        order_file = 'assets/character_menu_order.json'

        if not os.path.isdir(chars_dir):
            return []

        # Load the custom order saved by the character creator (may not exist yet)
        saved_order = []
        try:
            with open(order_file, 'r') as f:
                data = json.load(f)
            if isinstance(data, list):
                saved_order = [str(x) for x in data]
        except Exception:
            pass

        # Parse every character JSON, keyed by ID
        configs = {}
        for filename in os.listdir(chars_dir):
            if not filename.endswith('.json') or filename.startswith('_'):
                continue
            path = os.path.join(chars_dir, filename)
            try:
                with open(path, 'r') as f:
                    cfg = json.load(f)
            except Exception:
                continue
            if not isinstance(cfg, dict):
                continue
            char_id = cfg.get('id') or filename[:-5]
            configs[char_id] = cfg

        # Apply the saved order: known IDs first, then any new ones alphabetically
        if saved_order:
            ordered_ids = [cid for cid in saved_order if cid in configs]
            leftover = sorted(set(configs) - set(ordered_ids))
            final_ids = ordered_ids + leftover
        else:
            final_ids = sorted(configs)

        characters = []
        for char_id in final_ids:
            cfg = configs[char_id]
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

        raw_tex = _img('assets/ui/textbox/background_texture.png')
        if raw_tex:
            # Scale up the source tile before tiling — bigger = coarser pattern
            texture_scale = 4.5
            self.bg_texture = pygame.transform.scale(raw_tex, (
                int(raw_tex.get_width()  * texture_scale),
                int(raw_tex.get_height() * texture_scale),
            ))
        else:
            self.bg_texture = None

    def _load_character_sprites(self):
        """
        Each character gets two sprite sets:
          - idle.png  (2 columns × 4 rows): used as a static thumbnail when not selected
          - walk.png  (4 columns × 4 rows): animated when the character is selected
        Both sheets are assumed to have their "facing down" frames in the top row.
        """
        for char in self.characters:
            cid     = char['id']
            costume = char['costume']
            scale_f = max(2, int(RENDER_SCALE * 1.5))

            # Idle — grab top-left frame only
            try:
                sheet = pygame.image.load(f'assets/sprites/player/{cid}/{costume}/idle.png').convert_alpha()
                fw = sheet.get_width()  // 2
                fh = sheet.get_height() // 4
                frame = sheet.subsurface(pygame.Rect(0, 0, fw, fh))
                char['idle_sprite'] = pygame.transform.scale(frame, (fw * scale_f, fh * scale_f))
                char['has_idle']    = True
            except (pygame.error, FileNotFoundError):
                char['idle_sprite'] = None
                char['has_idle']    = False

            # Walk — extract all 4 "facing down" frames from the top row
            try:
                sheet = pygame.image.load(f'assets/sprites/player/{cid}/{costume}/walk.png').convert_alpha()
                fw = sheet.get_width()  // 4
                fh = sheet.get_height() // 4
                char['walk_frames'] = [
                    pygame.transform.scale(
                        sheet.subsurface(pygame.Rect(i * fw, 0, fw, fh)),
                        (fw * scale_f, fh * scale_f)
                    )
                    for i in range(4)
                ]
                char['has_walk'] = True
            except (pygame.error, FileNotFoundError):
                char['walk_frames'] = None
                char['has_walk']    = False

    # ── Public API ────────────────────────────────────────────────────────────

    def open(self, current_character='goku'):
        self.active = True
        # Refresh from disk so characters added in the creator appear immediately.
        self.characters = self._discover_characters()
        self._load_character_sprites()
        for i, char in enumerate(self.characters):
            if char['id'] == current_character:
                self.selected_character = i
                break
        else:
            self.selected_character = 0
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
            self._set_button_pressed('left')
            if self.selected_character > 0:
                self.selected_character -= 1
                self._reset_char_anim(self.selected_character)

        elif key == pygame.K_RIGHT:
            self._set_button_pressed('right')
            if self.selected_character < len(self.characters) - 1:
                self.selected_character += 1
                self._reset_char_anim(self.selected_character)

        elif key in (pygame.K_z, pygame.K_RETURN):
            self._set_button_pressed('a')
            selected = self.characters[self.selected_character]
            if selected['unlocked']:
                self.close()
                return selected['id']

        elif key in (pygame.K_x, pygame.K_ESCAPE):
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
        self._render_text_with_shadow(
            screen, "Switch Character",
            (self.screen_width // 2, self.canvas_y - int(50 / _S) + title_margin),
            anchor='center'
        )

        inner_margin = int(self.canvas_height)
        inner_w = int(self.canvas_width * 1.1 - inner_margin)
        inner_h = int(self.canvas_height) // 1.4
        box_x   = self.canvas_x + (self.canvas_width - inner_w) // 2
        box_y   = self.canvas_y + title_margin

        sprite_drawn = self.box_sprite and self._draw_9slice_sprite(
            screen, self.box_sprite, box_x, box_y, inner_w, inner_h, corner_size=20
        )

        if not sprite_drawn:
            # Procedural fallback borders
            pygame.draw.rect(screen, self.border_outer, (box_x-6, box_y-6, inner_w+12, inner_h+12))
            pygame.draw.rect(screen, self.border_inner, (box_x-3, box_y-3, inner_w+6,  inner_h+6))
            pygame.draw.rect(screen, self.border_green, (box_x-1, box_y-1, inner_w+2,  inner_h+2))
            self._draw_tiled_background(screen, pygame.Rect(box_x, box_y, inner_w, inner_h))

        char_y     = box_y + inner_h // 2
        char_count = len(self.characters)
        char_step  = inner_w // (char_count + 1)

        arrow_margin = int(inner_w * 0.08)
        left_disabled  = (self.selected_character <= 0)
        right_disabled = (self.selected_character >= char_count - 1)

        self._draw_arrow_sprite(
            screen,
            self.arrow_left, self.arrow_left_pressed, self.arrow_left_greyed,
            self.button_states['left'], left_disabled,
            box_x + arrow_margin, char_y
        )
        self._draw_arrow_sprite(
            screen,
            self.arrow_right, self.arrow_right_pressed, self.arrow_right_greyed,
            self.button_states['right'], right_disabled,
            box_x + inner_w - arrow_margin, char_y
        )

        for i, char in enumerate(self.characters):
            cx = box_x + char_step * (i + 1)
            self._draw_character_sprite(screen, char, cx, char_y, is_selected=(i == self.selected_character))

        # Bottom buttons
        button_y_offset = int(self.canvas_height * 0.315)
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

        tw = self.bg_texture.get_width()
        th = self.bg_texture.get_height()
        ox = self.bg_offset_x % tw
        oy = self.bg_offset_y % th

        # Tile across the target rect, clipping any overhang
        prev_clip = screen.get_clip()
        screen.set_clip(rect)
        start_x = rect.left - ox
        start_y = rect.top  - oy
        for ty in range((rect.height // th) + 3):
            for tx in range((rect.width  // tw) + 3):
                screen.blit(self.bg_texture, (start_x + tx * tw, start_y + ty * th))
        screen.set_clip(prev_clip)

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
        scw = int(cw * border_scale)
        sch = int(ch * border_scale)
        mw  = width  - 2 * scw
        mh  = height - 2 * sch

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
            scale_f  = btn_h / sprite.get_height()
            scaled_w = int(sprite.get_width() * scale_f)
            scaled   = pygame.transform.scale(sprite, (scaled_w, btn_h))
            screen.blit(scaled, (x, y))
            self._render_text_with_shadow(
                screen, label,
                (x + scaled_w + int(5 * RENDER_SCALE), y + btn_h // 2),
                anchor='midleft'
            )
        else:
            r   = btn_h // 2
            col = (140, 140, 140) if is_pressed else (180, 180, 180)
            pygame.draw.circle(screen, col,             (x + r, y + r), r)
            pygame.draw.circle(screen, (100, 100, 100), (x + r, y + r), r, 3)
            self._render_text_with_shadow(screen, label, (x + btn_h + 10, y + r), anchor='midleft')

    def _draw_arrow_sprite(self, screen, sprite_normal, sprite_pressed, sprite_greyed,
                           is_pressed, is_disabled, x, y):
        """Pressed state takes priority over disabled — so you see the flash before the arrow greys out."""
        if is_pressed and sprite_pressed:
            sprite = sprite_pressed
        elif is_disabled and sprite_greyed:
            sprite = sprite_greyed
        else:
            sprite = sprite_normal

        size = max(20, int(self.canvas_height * 0.06))

        if sprite:
            scaled = pygame.transform.scale(sprite, (size, size))
            screen.blit(scaled, scaled.get_rect(center=(x, y)))
        else:
            color = (140, 140, 140) if is_pressed else (80, 80, 80) if is_disabled else (180, 180, 180)
            box   = pygame.Rect(x - size//2, y - size//2, size, size)
            pygame.draw.rect(screen, color,             box)
            pygame.draw.rect(screen, (100, 100, 100),  box, 3)

    def _draw_character_sprite(self, screen, character, x, y, is_selected):
        """Selected → animated walk cycle. Unselected → static idle frame."""
        if is_selected:
            if character['has_walk'] and character['walk_frames']:
                frame = character['walk_frames'][character['animation_frame']]
                screen.blit(frame, frame.get_rect(center=(x, y)))
                return
        else:
            if character['has_idle'] and character['idle_sprite']:
                screen.blit(character['idle_sprite'],
                            character['idle_sprite'].get_rect(center=(x, y)))
                return
        self._draw_fallback_sprite(screen, character, x, y)

    def _draw_fallback_sprite(self, screen, character, x, y):
        size = int(self.canvas_height * 0.15)
        if not character['unlocked']:
            r = pygame.Rect(x - size//2, y - size//2, size, size)
            pygame.draw.rect(screen, (30, 30, 30), r)
            pygame.draw.rect(screen, (80, 80, 80), r, 2)
        else:
            pygame.draw.circle(screen, (100, 100, 200), (x, y), size // 2)
            pygame.draw.circle(screen, (0,   0,   0),   (x, y), size // 2, 3)