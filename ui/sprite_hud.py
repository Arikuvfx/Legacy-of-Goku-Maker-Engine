import pygame
import os
import sys
import time
from config.settings import RENDER_SCALE


class HUDSprite:
    """Wraps a single sprite file with a draw helper that handles scaling and opacity."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.sprite   = None
        if os.path.exists(filepath):
            try:
                self.sprite = pygame.image.load(filepath).convert_alpha()
                print(f"✓ Loaded: {os.path.basename(filepath)}")
            except Exception as e:
                print(f"✗ Error loading {filepath}: {e}")
        else:
            print(f"✗ Not found: {filepath}")

    def draw(self, screen, x, y, width=None, height=None, opacity=255):
        if not self.sprite:
            return False
        surf = pygame.transform.scale(self.sprite, (width, height)) if (width and height) else self.sprite
        if opacity < 255:
            surf = surf.copy()
            surf.set_alpha(opacity)
        screen.blit(surf, (x, y))
        return True


class SpriteHUD:
    """
    Legacy-of-Goku style HUD — everything is a sprite, no vector drawing.

    Tweak self.scale to resize the whole HUD at once:
      0.5 = compact, 1.0 = normal, 1.5 = large
    """

    def __init__(self, screen_width, screen_height):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.visible = True

        self.hud_x = 10
        self.hud_y = 10
        self.hud_offset_y = 0.0  # animated by cutscene start/stop
        self._hud_slide_out = False
        self._hud_slide_in  = False

        # Change this one value to resize everything
        self.scale = 0.7 * 2

        if getattr(sys, 'frozen', False):
            app_path = os.path.dirname(sys.executable)
        else:
            app_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.base_path = os.path.join(app_path, "assets", "ui", "hud")

        print(f"\n=== Loading HUD Sprites ===")
        print(f"Looking in: {self.base_path}")
        print(f"HUD Scale:  {self.scale * 100:.0f}%\n")

        self.sprites = {
            'frame':               HUDSprite(os.path.join(self.base_path, "frame.png")),
            'hp_bar':              HUDSprite(os.path.join(self.base_path, "hp_bar.png")),
            'ki_bar':              HUDSprite(os.path.join(self.base_path, "ki_bar.png")),
            'transformed_ki_bar':  HUDSprite(os.path.join(self.base_path, "transformed_ki_bar.png")),
            'exp_bar':             HUDSprite(os.path.join(self.base_path, "exp_bar.png")),
            'transform_bar':       HUDSprite(os.path.join(self.base_path, "transform_bar.png")),
            'attack_icon_blast':   HUDSprite(os.path.join(self.base_path, "attack_icon_blast.png")),
            'attack_icon_beam':    HUDSprite(os.path.join(self.base_path, "attack_icon_beam.png")),
            'transformation_icon': HUDSprite(os.path.join(self.base_path, "transformation_icon.png")),
            'attack_icon':         HUDSprite(os.path.join(self.base_path, "attack_icon.png")),
        }

        print("=== HUD Loading Complete ===\n")

        # Base dimensions of each element at scale 1.0 (pixels)
        self.config = {
            'frame':              {'x': 0, 'y': 0, 'w': 338, 'h': 100},
            'attack_icon':        {'x': 0, 'y': 0, 'w': 338, 'h': 100},
            'hp_bar':             {'x': 0, 'y': 0, 'w': 338, 'h': 100, 'bar_start': 123, 'bar_end': 294},
            'ki_bar':             {'x': 0, 'y': 0, 'w': 338, 'h': 100, 'bar_start': 123, 'bar_end': 294},
            'transformed_ki_bar': {'x': 0, 'y': 0, 'w': 338, 'h': 100, 'bar_start': 123, 'bar_end': 294},
            'exp_bar':            {'x': 0, 'y': 0, 'w': 338, 'h': 100, 'bar_start':  19, 'bar_end': 310},
            'transform_bar':      {'x': 0, 'y': 0, 'w': 338, 'h': 100, 'bar_start':  13, 'bar_end':  40},
        }

        pygame.font.init()
        self.font_small  = pygame.font.Font(None, 18)
        self.font_medium = pygame.font.Font(None, 22)
        self.font_large  = pygame.font.Font(None, 26)

        self.colors = {
            'text':             (255, 255, 255),
            'shadow':           (0,   0,   0),
            'stat_points':      (255, 215, 0),   # gold — unspent stat points indicator
            'transform_ready':  (255, 255, 255),
            'transform_fill':   (255, 215, 0),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def draw_text_with_shadow(self, screen, text, x, y, font, color, shadow_offset=2):
        off = max(1, int(shadow_offset * self.scale))
        screen.blit(font.render(text, True, self.colors['shadow']), (x + off, y + off))
        screen.blit(font.render(text, True, color),                  (x,       y))

    def draw_bar_simple(self, screen, x, y, width, height, current, maximum,
                        bar_sprite, bar_start_x=0, bar_end_x=None):
        """
        Crops the bar sprite to represent the fill amount.
        bar_start_x / bar_end_x mark the pixel region of the actual bar graphic
        within the scaled sprite — everything outside that region is frame art.
        """
        if maximum <= 0 or not bar_sprite.sprite:
            return
        if bar_end_x is None:
            bar_end_x = width

        region_w  = bar_end_x - bar_start_x
        fill_w    = int(region_w * (current / maximum))
        scaled    = pygame.transform.scale(bar_sprite.sprite, (width, height))
        if fill_w > 0:
            screen.blit(scaled.subsurface((bar_start_x, 0, fill_w, height)),
                        (x + bar_start_x, y))

    def draw_transform_bar_with_shine(self, screen, x, y, width, height,
                                       progress, is_ready, shine_alpha, bar_sprite):
        if not bar_sprite.sprite:
            return
        fill_w = int(width * progress)
        if fill_w > 0:
            scaled = pygame.transform.scale(bar_sprite.sprite, (width, height))
            screen.blit(scaled.subsurface((0, 0, fill_w, height)), (x, y))

    def get_transform_animation_progress(self, player):
        if hasattr(player, 'transformation') and player.transformation:
            return player.transformation.transform_animation_progress
        return 0.0

    # ── Main draw ─────────────────────────────────────────────────────────────

    def draw(self, screen, player):
        if not self.visible:
            return

        bx = self.hud_x
        by = self.hud_y + int(self.hud_offset_y)

        def sc(v):
            return int(v * self.scale)

        # 1. Frame
        cfg = self.config['frame']
        self.sprites['frame'].draw(screen, bx, by, sc(cfg['w']), sc(cfg['h']))

        # 2. Attack mode icon
        icfg  = self.config['attack_icon']
        ix, iy = bx + sc(icfg['x']), by + sc(icfg['y'])
        iw, ih = sc(icfg['w']),       sc(icfg['h'])
        mode  = getattr(player, 'ki_attack_mode', 'blast')

        xform_opacity = 255
        if hasattr(player, 'transformation') and player.transformation:
            if not player.transformation.is_ready:
                xform_opacity = 128  # dim the icon when transform isn't charged

        if mode == 'beam' and self.sprites['attack_icon_beam'].sprite:
            self.sprites['attack_icon_beam'].draw(screen, ix, iy, iw, ih)
        elif mode == 'transform' and self.sprites['transformation_icon'].sprite:
            self.sprites['transformation_icon'].draw(screen, ix, iy, iw, ih, opacity=xform_opacity)
        elif mode == 'blast' and self.sprites['attack_icon_blast'].sprite:
            self.sprites['attack_icon_blast'].draw(screen, ix, iy, iw, ih)
        else:
            self.sprites['attack_icon'].draw(screen, ix, iy, iw, ih)

        # 3. HP bar
        hp = self.config['hp_bar']
        self.draw_bar_simple(screen, bx + sc(hp['x']), by + sc(hp['y']),
                             sc(hp['w']), sc(hp['h']),
                             player.hp, player.max_hp, self.sprites['hp_bar'])

        # 4. Ki bar (always draw the base bar, then overlay transformed bar if needed)
        ki = self.config['ki_bar']
        self.draw_bar_simple(screen, bx + sc(ki['x']), by + sc(ki['y']),
                             sc(ki['w']), sc(ki['h']),
                             player.ki, player.max_ki, self.sprites['ki_bar'],
                             bar_start_x=sc(ki.get('bar_start', 0)),
                             bar_end_x=sc(ki.get('bar_end', ki['w'])))

        is_transforming = hasattr(player, 'transformation') and player.transformation and player.transformation.is_transforming
        is_transformed  = hasattr(player, 'transformation') and player.transformation and player.transformation.is_transformed
        tki_cfg = self.config['transformed_ki_bar']
        tkx, tky = bx + sc(tki_cfg['x']), by + sc(tki_cfg['y'])
        tkw, tkh = sc(tki_cfg['w']),       sc(tki_cfg['h'])

        if is_transforming:
            # Show the bar filling up during the animation
            anim = self.get_transform_animation_progress(player)
            self.draw_bar_simple(screen, tkx, tky, tkw, tkh,
                                 anim, 1.0, self.sprites['transformed_ki_bar'])
        elif is_transformed:
            t = player.transformation
            self.draw_bar_simple(screen, tkx, tky, tkw, tkh,
                                 t.transformed_ki, t.max_transformed_ki,
                                 self.sprites['transformed_ki_bar'])

        # 5. EXP bar
        exp = self.config['exp_bar']
        self.draw_bar_simple(screen, bx + sc(exp['x']), by + sc(exp['y']),
                             sc(exp['w']), sc(exp['h']),
                             player.exp, player.exp_to_next_level, self.sprites['exp_bar'])

        # 6. Transformation charge bar
        if hasattr(player, 'transformation') and player.transformation:
            tcfg = self.config['transform_bar']
            self.draw_transform_bar_with_shine(
                screen, bx + sc(tcfg['x']), by + sc(tcfg['y']),
                sc(tcfg['w']), sc(tcfg['h']),
                player.transformation.progress,
                player.transformation.is_ready,
                player.transformation.get_shine_alpha(),
                self.sprites['transform_bar']
            )

        # 7. Unspent stat points — pulsing gold circle in the corner
        if player.stat_points > 0:
            pulse = abs(int((time.time() * 3) % 2 - 1) * 80) + 175
            pulse_color = (pulse, pulse, 0)

            sx = bx + sc(self.config['frame']['w']) - sc(30)
            sy = by + sc(self.config['frame']['h']) - sc(25)

            base_r = sc(12)
            for r in range(sc(18), base_r, max(1, -sc(2))):
                alpha = 50 - (sc(18) - r) * 8
                glow = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
                pygame.draw.circle(glow, (*pulse_color, alpha), (r, r), r)
                screen.blit(glow, (sx - r, sy - r))

            pygame.draw.circle(screen, pulse_color,           (sx, sy), base_r)
            pygame.draw.circle(screen, self.colors['stat_points'], (sx, sy), base_r, max(1, sc(2)))
            pygame.draw.circle(screen, self.colors['shadow'], (sx, sy), sc(10), 1)

            label = str(player.stat_points)
            tw    = self.font_large.size(label)[0]
            self.draw_text_with_shadow(screen, label,
                                       sx - tw // 2, sy - sc(8),
                                       self.font_large, (255, 255, 255))