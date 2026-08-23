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
        self.attacks_path = os.path.join(app_path, "assets", "sprites", "attacks")

        # Attack-icon HUD keys map to the actual attack folder ids (the same
        # ids used in equipped_attacks / _get_allowed_ki_modes), since the
        # icons now live at assets/attacks/<folder_id>/icon.png instead of
        # the old flat files under assets/ui/hud.
        self._attack_icon_folders = {
            'attack_icon_blast':                'ki_blast',
            'attack_icon_beam':                 'kamehameha',
            'attack_icon_kamekameha':            'kamekameha',
            'attack_icon_banshee_blast':          'banshee_blast',
            'attack_icon_energy_punch':          'energy_punch',
            'attack_icon_dragon_fist':           'dragon_fist',
            'attack_icon_final_flash':           'final_flash',
            'attack_icon_big_bang_kamehameha':   'big_bang_kamehameha',
            'attack_icon_genkidama':             'genkidama',
            'attack_icon_big_bang_attack':        'big_bang_attack',
            'attack_icon_masenko':               'masenko',
            'attack_icon_burning_attack':        'burning_attack',
            'attack_icon_flame_kamehameha':      'flame_kamehameha',
            'attack_icon_ultra_volleyball':       'ultra_volleyball_attack',
            'attack_icon_sword':                 'energy_sword',
            'attack_icon_instant_transmission':  'instant_transmission',
            'attack_icon_ghost_kamikaze':         'ghost_kamikaze_attack',
        }

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
            # Transformation icon is NOT affected by the attacks-folder switch.
            'transformation_icon': HUDSprite(os.path.join(self.base_path, "transformation_icon.png")),
            # Default/fallback attack icon also stays in the HUD folder.
            'attack_icon':         HUDSprite(os.path.join(self.base_path, "attack_icon.png")),
            # Boss HP bar — background plate drawn first, fill bar cropped on top
            'boss_bar_bg':         HUDSprite(os.path.join(self.base_path, "boss_bar_bg.png")),
            'boss_bar':            HUDSprite(os.path.join(self.base_path, "boss_bar.png")),
        }

        print(f"\n=== Loading Attack Icon Sprites ===")
        print(f"Looking in: {self.attacks_path}\n")

        for hud_key, folder_id in self._attack_icon_folders.items():
            self.sprites[hud_key] = HUDSprite(
                os.path.join(self.attacks_path, folder_id, "icon.png")
            )

        print("=== HUD Loading Complete ===\n")

        # Base dimensions of each element at scale 1.0 (pixels)
        self.config = {
            'frame':              {'x': 0, 'y': 0, 'w': 338, 'h': 100},
            'attack_icon':        {'x': 0, 'y': 0, 'w': 338, 'h': 100},
            # Real per-attack icons are 64×28 native — draw at native size
            # (scaled by sc()) instead of stretching them to fill the frame.
            'attack_mode_icon':   {'x': 43, 'y': 26, 'w': 64, 'h': 28},
            'hp_bar':             {'x': 0, 'y': 0, 'w': 338, 'h': 100, 'bar_start': 139, 'bar_end': 311},
            'ki_bar':             {'x': 0, 'y': 0, 'w': 338, 'h': 100, 'bar_start': 123, 'bar_end': 295},
            'transformed_ki_bar': {'x': 0, 'y': 0, 'w': 338, 'h': 100, 'bar_start': 123, 'bar_end': 295},
            'exp_bar':            {'x': 0, 'y': 0, 'w': 338, 'h': 100, 'bar_start':  19, 'bar_end': 311},
            'transform_bar':      {'x': 0, 'y': 0, 'w': 338, 'h': 100, 'bar_start':  13, 'bar_end':  40},
            # Boss bar — sprites are 64×8 px native, scaled to match the player
            # HUD width (338 config units → same sc() factor as everything else).
            # Height is proportional: 338 * 8/64 = 42.
            'boss_bar':           {'w': 338, 'h': 42, 'bar_start': 0, 'bar_end': 338},
        }

        # Scan the frame sprite to find the actual lowest non-transparent pixel
        # row (at native resolution). The sprite may have empty padding below
        # the visible art, so using the full h=100 would place the boss bar too low.
        self._frame_visible_bottom = self._measure_visible_bottom(
            self.sprites['frame'], self.config['frame']['h']
        )
        print(f"Frame visible bottom: {self._frame_visible_bottom}px (of {self.config['frame']['h']}px total)")

        # Boss bar HP tracking — we lock the total max HP the moment bosses are
        # first spotted so that killing one boss drains its portion of the bar
        # rather than reflating the remainder.
        # _seen_boss_ids: maps id(boss) -> max_hp for every boss we've registered.
        # _locked_max_hp: sum of all registered max HPs; never decreases.
        self._seen_boss_ids = {}   # {id(boss): max_hp}
        self._locked_max_hp = 0

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

    @staticmethod
    def _measure_visible_bottom(hud_sprite, fallback_h):
        """Return the y of the lowest non-transparent pixel row in the sprite
        at native (unscaled) resolution.  Falls back to fallback_h if the
        sprite isn't loaded or is fully transparent.
        """
        surf = getattr(hud_sprite, 'sprite', None)
        if surf is None:
            return fallback_h
        w, h = surf.get_size()
        # Scan rows from the bottom upward, stop at first row with any alpha > 0.
        for row in range(h - 1, -1, -1):
            for col in range(w):
                if surf.get_at((col, row))[3] > 0:
                    return row + 1   # +1 so it's an exclusive bottom (like a height)
        return fallback_h

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

    # ── Boss bar ──────────────────────────────────────────────────────────────

    @staticmethod
    def _boss_is_aware(boss):
        """Return True when a boss is actively noticing the player.

        Checks the most common state-machine attributes used across the Enemy /
        BossEnemy hierarchy.  Falls back to True so a boss without a state
        attribute always shows its bar once it is alive.
        """
        # Prefer an explicit 'is_aware' / 'aware' flag if the AI exposes one.
        if hasattr(boss, 'is_aware'):
            return bool(boss.is_aware)
        # State-machine check — idle / patrol / wander mean the boss hasn't
        # spotted the player yet; anything else (chase, attack, …) means aware.
        if hasattr(boss, 'state') and boss.state is not None:
            return boss.state not in ('idle', 'patrol', 'wander')
        # No state info at all — show the bar to be safe.
        return True

    def draw_boss_bar(self, screen, bosses):
        """Draw the combined boss HP bar on the right side of the screen.

        Uses a locked max HP so killing one boss drains its share of the bar
        rather than reflating the remaining bosses' portion.

        Parameters
        ----------
        screen : pygame.Surface
        bosses : list — alive BossEnemy instances (hp > 0) that are noticing the player
        """
        if not bosses:
            return

        # Register any boss we haven't seen before and lock their max HP in.
        # If none of the current bosses are known it's a fresh encounter
        # (e.g. re-entering a test room), so reset before registering.
        if not any(id(b) in self._seen_boss_ids for b in bosses):
            self._seen_boss_ids = {}
            self._locked_max_hp = 0
        for b in bosses:
            bid = id(b)
            if bid not in self._seen_boss_ids:
                mhp = max(1, getattr(b, 'max_hp', 1))
                self._seen_boss_ids[bid] = mhp
                self._locked_max_hp += mhp

        if self._locked_max_hp <= 0:
            return

        # Current HP = sum of alive bosses only; max stays locked forever.
        total_hp = sum(max(0, getattr(b, 'hp', 0)) for b in bosses)

        def sc(v):
            return int(v * self.scale)

        bcfg = self.config['boss_bar']
        bw   = sc(bcfg['w'])
        bh   = sc(bcfg['h'])

        # Mirror the player HUD margin: same distance from the RIGHT edge.
        # Align boss bar bottom with the lowest visible pixel row of the player
        # frame sprite (not the full sprite height, which may have empty padding).
        bx = self.screen_width - self.hud_x - bw
        by = self.hud_y + int(self.hud_offset_y) + int(self._frame_visible_bottom * self.scale) - bh

        # ── Draw background plate ──────────────────────────────────────────
        bg_sprite = self.sprites['boss_bar_bg']
        if bg_sprite.sprite:
            screen.blit(pygame.transform.scale(bg_sprite.sprite, (bw, bh)), (bx, by))

        # ── Draw HP fill (cropped) ─────────────────────────────────────────
        fill_sprite = self.sprites['boss_bar']
        if fill_sprite.sprite:
            bar_start = sc(bcfg.get('bar_start', 0))
            bar_end   = sc(bcfg.get('bar_end', bcfg['w']))
            region_w  = bar_end - bar_start
            fill_w    = int(region_w * (total_hp / self._locked_max_hp))
            if fill_w > 0:
                scaled = pygame.transform.scale(fill_sprite.sprite, (bw, bh))
                screen.blit(scaled.subsurface((bar_start, 0, fill_w, bh)),
                            (bx + bar_start, by))

    # ── Main draw ─────────────────────────────────────────────────────────────

    def draw(self, screen, player, enemies=None, dt=0.0):
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

        # Real per-attack icons are much smaller (64×28 native) — draw them
        # at their own size/anchor instead of the old full-frame icfg.
        acfg  = self.config['attack_mode_icon']
        ax, ay = bx + sc(acfg['x']), by + sc(acfg['y'])
        aw, ah = sc(acfg['w']),       sc(acfg['h'])

        mode  = getattr(player, 'ki_attack_mode', 'blast')

        xform_opacity = 255
        if hasattr(player, 'transformation') and player.transformation:
            if not player.transformation.is_ready:
                xform_opacity = 128  # dim the icon when transform isn't charged

        if mode == 'beam' and self.sprites['attack_icon_beam'].sprite:
            self.sprites['attack_icon_beam'].draw(screen, ax, ay, aw, ah)
        elif mode == 'kamekameha' and self.sprites['attack_icon_kamekameha'].sprite:
            self.sprites['attack_icon_kamekameha'].draw(screen, ax, ay, aw, ah)
        elif mode == 'banshee_blast' and self.sprites['attack_icon_banshee_blast'].sprite:
            self.sprites['attack_icon_banshee_blast'].draw(screen, ax, ay, aw, ah)
        elif mode == 'energy_punch' and self.sprites['attack_icon_energy_punch'].sprite:
            self.sprites['attack_icon_energy_punch'].draw(screen, ax, ay, aw, ah)
        elif mode == 'dragon_fist' and self.sprites['attack_icon_dragon_fist'].sprite:
            self.sprites['attack_icon_dragon_fist'].draw(screen, ax, ay, aw, ah)
        elif mode == 'final_flash' and self.sprites['attack_icon_final_flash'].sprite:
            self.sprites['attack_icon_final_flash'].draw(screen, ax, ay, aw, ah)
        elif mode == 'big_bang_kamehameha' and self.sprites['attack_icon_big_bang_kamehameha'].sprite:
            self.sprites['attack_icon_big_bang_kamehameha'].draw(screen, ax, ay, aw, ah)
        elif mode == 'genkidama' and self.sprites['attack_icon_genkidama'].sprite:
            self.sprites['attack_icon_genkidama'].draw(screen, ax, ay, aw, ah)
        elif mode == 'big_bang_attack' and self.sprites['attack_icon_big_bang_attack'].sprite:
            self.sprites['attack_icon_big_bang_attack'].draw(screen, ax, ay, aw, ah)
        elif mode == 'masenko' and self.sprites['attack_icon_masenko'].sprite:
            self.sprites['attack_icon_masenko'].draw(screen, ax, ay, aw, ah)
        elif mode == 'burning_attack' and self.sprites['attack_icon_burning_attack'].sprite:
            self.sprites['attack_icon_burning_attack'].draw(screen, ax, ay, aw, ah)
        elif mode == 'flame_kamehameha' and self.sprites['attack_icon_flame_kamehameha'].sprite:
            self.sprites['attack_icon_flame_kamehameha'].draw(screen, ax, ay, aw, ah)
        elif mode == 'ultra_volleyball_attack' and self.sprites['attack_icon_ultra_volleyball'].sprite:
            self.sprites['attack_icon_ultra_volleyball'].draw(screen, ax, ay, aw, ah)
        elif mode == 'sword' and self.sprites['attack_icon_sword'].sprite:
            self.sprites['attack_icon_sword'].draw(screen, ax, ay, aw, ah)
        elif mode == 'instant_transmission' and self.sprites['attack_icon_instant_transmission'].sprite:
            self.sprites['attack_icon_instant_transmission'].draw(screen, ax, ay, aw, ah)
        elif mode == 'ghost_kamikaze_attack' and self.sprites['attack_icon_ghost_kamikaze'].sprite:
            self.sprites['attack_icon_ghost_kamikaze'].draw(screen, ax, ay, aw, ah)
        elif mode == 'transform' and self.sprites['transformation_icon'].sprite:
            self.sprites['transformation_icon'].draw(screen, ix, iy, iw, ih, opacity=xform_opacity)
        elif mode == 'blast' and self.sprites['attack_icon_blast'].sprite:
            self.sprites['attack_icon_blast'].draw(screen, ax, ay, aw, ah)
        else:
            self.sprites['attack_icon'].draw(screen, ix, iy, iw, ih)

        # 3. HP bar
        hp = self.config['hp_bar']
        self.draw_bar_simple(screen, bx + sc(hp['x']), by + sc(hp['y']),
                             sc(hp['w']), sc(hp['h']),
                             player.hp, player.max_hp, self.sprites['hp_bar'],
                             bar_start_x=sc(hp.get('bar_start', 0)),
                             bar_end_x=sc(hp.get('bar_end', hp['w'])))

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
                                 anim, 1.0, self.sprites['transformed_ki_bar'],
                                 bar_start_x=sc(tki_cfg.get('bar_start', 0)),
                                 bar_end_x=sc(tki_cfg.get('bar_end', tki_cfg['w'])))
        elif is_transformed:
            t = player.transformation
            self.draw_bar_simple(screen, tkx, tky, tkw, tkh,
                                 t.transformed_ki, t.max_transformed_ki,
                                 self.sprites['transformed_ki_bar'],
                                 bar_start_x=sc(tki_cfg.get('bar_start', 0)),
                                 bar_end_x=sc(tki_cfg.get('bar_end', tki_cfg['w'])))

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

        # 8. Boss HP bar — right side, bottom-aligned with player HUD.
        # Show when any alive boss is aware. The locked max HP ensures killing
        # one boss drains its share rather than reflating the others.
        if enemies is not None:
            alive_bosses = [e for e in enemies if getattr(e, 'is_boss', False)
                            and getattr(e, 'hp', 0) > 0]
            # Reset encounter tracking once all bosses are gone.
            if not alive_bosses:
                self._seen_boss_ids = {}
                self._locked_max_hp = 0
            elif any(self._boss_is_aware(b) for b in alive_bosses):
                self.draw_boss_bar(screen, alive_bosses)