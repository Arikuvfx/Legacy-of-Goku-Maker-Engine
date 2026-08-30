import pygame
import os
import sys
import time
import colorsys
from config.settings import RENDER_SCALE


class _TintedBarSprite:
    """Thin HUDSprite-alike wrapper around an already-recolored pygame
    Surface, so draw_bar_simple() (which reads bar_sprite.sprite) can treat
    a tinted ki bar exactly like a normal HUDSprite without caring where
    the surface came from."""
    __slots__ = ('sprite',)

    def __init__(self, surface):
        self.sprite = surface


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
        self.scale = 6

        if getattr(sys, 'frozen', False):
            app_path = os.path.dirname(sys.executable)
        else:
            app_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.base_path = os.path.join(app_path, "assets", "ui", "hud")
        self.attacks_path = os.path.join(app_path, "assets", "sprites", "attacks")
        self.player_sprites_path = os.path.join(app_path, "assets", "sprites", "player")

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
            # Fallback transformation icon, used when the active transformation
            # doesn't have its own icon.png in its sprite folder (see
            # _get_transformation_icon / self._transform_icon_cache below).
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
            'frame':              {'x': 0, 'y': 0, 'w': 80, 'h': 16},
            'attack_icon':        {'x': 0, 'y': 0, 'w': 338, 'h': 100},
            # Real per-attack icons are 64×28 native — draw at native size
            # (scaled by sc()) instead of stretching them to fill the frame.
            'attack_mode_icon':   {'x': 3, 'y': 3, 'w': 16, 'h': 7},
            'hp_bar':             {'x': 33, 'y': 3, 'w': 43, 'h': 3, 'bar_start': 0, 'bar_end': 43},
            'ki_bar':             {'x': 29, 'y': 7, 'w': 43, 'h': 3, 'bar_start': 0, 'bar_end': 43},
            'transformed_ki_bar': {'x': 29, 'y': 7, 'w': 43, 'h': 3, 'bar_start': 0, 'bar_end': 43},
            'exp_bar':            {'x': 3, 'y': 12, 'w': 73, 'h': 2, 'bar_start':  0, 'bar_end': 73},
            'transform_bar':      {'x': 22, 'y': 3, 'w': 7, 'h': 7, 'bar_start':  0, 'bar_end':  7},
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

        # Recolored transformed-ki-bar surfaces, keyed by (id(base_surface),
        # hex_color). Built lazily the first time a given custom ki color is
        # actually drawn, then reused every frame after that — recoloring is
        # a per-pixel loop so we don't want to redo it every draw() call.
        self._ki_color_cache = {}

        # Per-transformation icon sprites, keyed by (char_id, transform_costume).
        # Loaded lazily the first time a given transformation is drawn — see
        # _get_transformation_icon() — since the icon now lives alongside that
        # transformation's own sprites instead of a single flat HUD file.
        self._transform_icon_cache = {}

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
        if fill_w <= 0:
            return

        # Step once per REAL source pixel row (e.g. 3), not per scaled screen
        # row — otherwise a 3px bar scaled up to 18px tall draws 18 tiny
        # 1px steps, which just looks like a smooth "/" slope. Each source
        # row should be a full blocky step:
        #   ---
        #    --
        #     -
        src_rows = max(1, bar_sprite.sprite.get_height())
        row_h    = max(1, height // src_rows)
        for row in range(src_rows):
            row_y    = row * row_h
            # last row absorbs any leftover height from integer division
            this_h   = height - row_y if row == src_rows - 1 else row_h
            row_fill = max(0, fill_w - row * row_h)
            if row_fill <= 0:
                continue
            screen.blit(scaled.subsurface((bar_start_x, row_y, row_fill, this_h)),
                        (x + bar_start_x, y + row_y))

    def _get_recolored_bar_surface(self, base_surface, hex_color):
        """Recolor transformed_ki_bar.png to a custom hue, preserving each
        pixel's own lightness — replicating how the original game's ki bar
        palettes work (normal/SSJ/SSJ3 each step through the same five
        lightness steps, only the hue changes; see the ki-color hex lists
        in the transformation design notes). Result is cached per
        (source surface, color) pair since this loops pixel-by-pixel.
        """
        key = (id(base_surface), hex_color)
        cached = self._ki_color_cache.get(key)
        if cached is not None:
            return cached

        target_r = int(hex_color[1:3], 16) / 255.0
        target_g = int(hex_color[3:5], 16) / 255.0
        target_b = int(hex_color[5:7], 16) / 255.0
        target_hue, _, target_sat = colorsys.rgb_to_hls(target_r, target_g, target_b)

        w, h = base_surface.get_size()
        out = base_surface.copy()
        for py in range(h):
            for px in range(w):
                r, g, b, a = base_surface.get_at((px, py))
                if a == 0:
                    continue
                _, lightness, _ = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
                nr, ng, nb = colorsys.hls_to_rgb(target_hue, lightness, target_sat)
                out.set_at((px, py), (round(nr * 255), round(ng * 255), round(nb * 255), a))

        self._ki_color_cache[key] = out
        return out

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

    def _get_transformation_icon(self, player, form_name=None):
        """Return the HUDSprite to use for a transform ki-mode icon.

        form_name: which specific tier's icon to load (e.g. "ssj3") — pass
          this for a 'transform:<form_name>' slot so each tier shows its
          own icon.png instead of always the first-registered form's. None
          means the base 'transform' slot (historical behavior).

        Mirrors load_attack_icon()'s per-item lookup in character_creator.py:
        each transformation's icon.png now lives in that transformation's own
        sprite folder —
            assets/sprites/player/{char_id}/{active_costume}/transformations/{form}/icon.png
        — instead of the flat assets/ui/hud/transformation_icon.png. Falls
        back to the old HUD icon if the active transformation doesn't have
        its own icon.png (e.g. an older project that hasn't added one yet).
        """
        char_id = getattr(player, 'character', None)
        transformation = getattr(player, 'transformation', None)
        transform_costume = transformation.get_display_transform_costume(form_name) if transformation else None

        if not char_id or not transform_costume:
            return self.sprites['transformation_icon']

        cache_key = (char_id, transform_costume)
        cached = self._transform_icon_cache.get(cache_key)
        if cached is not None:
            return cached

        icon_path = os.path.join(self.player_sprites_path, char_id, transform_costume, "icon.png")
        icon_sprite = HUDSprite(icon_path)
        if not icon_sprite.sprite:
            # No dedicated icon.png for this transformation — fall back to
            # the shared HUD icon. Cached under this key too, since icon
            # files aren't expected to appear mid-session.
            icon_sprite = self.sprites['transformation_icon']

        self._transform_icon_cache[cache_key] = icon_sprite
        return icon_sprite

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

        # Which specific transformation tier the current mode targets, if
        # any — 'transform' (the base slot) resolves to None (meaning
        # "whichever form is registered first"), 'transform:<form_name>'
        # resolves to that form_name, and every other mode is untouched.
        transform_form = None
        is_transform_mode = mode == 'transform' or mode.startswith('transform:')
        if mode.startswith('transform:'):
            transform_form = mode.split(':', 1)[1]

        xform_opacity = 255
        if is_transform_mode and hasattr(player, 'transformation') and player.transformation:
            # Dim the icon when selecting this slot right now wouldn't
            # actually do anything — either the base charge meter isn't
            # ready yet, or (for a tier-2+ slot) its prerequisite tier
            # hasn't been reached. Full opacity also while the player is
            # currently standing in the exact form this slot targets, so
            # an active tier doesn't read as "unavailable".
            ts = player.transformation
            # Resolve which actual form_name the base 'transform' slot
            # (transform_form None) points at, so "currently active" can
            # be compared by real form_name rather than against the literal
            # None sentinel — otherwise standing in SSJ while its own slot
            # is selected would incorrectly read as "not active".
            resolved_costume = ts.get_display_transform_costume(transform_form)
            resolved_form = ts._form_name_from_costume(resolved_costume) if resolved_costume else transform_form
            if not (ts.is_transformed and ts.active_form_name == resolved_form):
                if not ts.can_start_transform(transform_form):
                    xform_opacity = 128

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
        elif is_transform_mode and self._get_transformation_icon(player, transform_form).sprite:
            self._get_transformation_icon(player, transform_form).draw(screen, ax, ay, aw, ah, opacity=xform_opacity)
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

        def _tier_bar_sprite(ki_color):
            """Only the transformed bar is customizable — the normal (non-
            transformed) ki bar above always stays the original green
            sprite. A form with no ki_color configured falls back to
            transformed_ki_bar.png's own baked-in colors, unchanged."""
            sprite = self.sprites['transformed_ki_bar']
            if ki_color and sprite.sprite:
                recolored = self._get_recolored_bar_surface(sprite.sprite, ki_color)
                return _TintedBarSprite(recolored)
            return sprite

        # A transformation can have its charge bar disabled entirely (see
        # character_creator.py's "Show Charge Bar" checkbox). In that case
        # the transform animation just plays straight through on its own
        # with no fill progress to represent, and — since the player is
        # meant to read as just using their normal ki bar for this form —
        # the transformed-ki bar also stays hidden once transformed, not
        # just during the animation.
        transform_bar_enabled = getattr(player.transformation, 'current_transform_ki_bar_enabled', True)
        if (is_transforming or is_transformed) and transform_bar_enabled:
            t = player.transformation
            tier_depth = getattr(t, 'tier_depth', 0)
            frozen_colors = getattr(t, 'frozen_tier_colors', [])
            frozen_fills = getattr(t, 'frozen_tier_fills', [])

            bar_start = sc(tki_cfg.get('bar_start', 0))
            bar_end   = sc(tki_cfg.get('bar_end', tki_cfg['w']))

            # The most-recently-completed tier gets drawn first, as a
            # frozen backdrop across the WHOLE row — frozen at the exact
            # ki fraction it actually had the instant the player advanced
            # past it (see frozen_tier_fills), so it stays completely
            # static while the current tier charges, instead of snapping
            # to "full" if it wasn't. Any earlier frozen tiers are fully
            # covered underneath it either way and don't need drawing.
            if tier_depth > 0:
                prev_color = frozen_colors[-1] if frozen_colors else None
                prev_fill  = frozen_fills[-1] if frozen_fills else 1.0
                backdrop_sprite = _tier_bar_sprite(prev_color)
                self.draw_bar_simple(screen, tkx, tky, tkw, tkh,
                                     prev_fill, 1.0, backdrop_sprite,
                                     bar_start_x=bar_start, bar_end_x=bar_end)

            # Current tier's own bar grows from the SAME starting point
            # every tier uses (the left edge of the row), layered on top of
            # the previous tier's backdrop as it fills — so SSJ's bar stays
            # visible underneath until SSJ3's own bar grows enough to cover
            # it, instead of picking up from wherever SSJ's fill happened
            # to stop.
            active_sprite = _tier_bar_sprite(getattr(t, 'current_transform_ki_color', None))
            if is_transforming:
                # Show the bar filling up during the animation
                anim = self.get_transform_animation_progress(player)
                self.draw_bar_simple(screen, tkx, tky, tkw, tkh,
                                     anim, 1.0, active_sprite,
                                     bar_start_x=bar_start, bar_end_x=bar_end)
            else:
                self.draw_bar_simple(screen, tkx, tky, tkw, tkh,
                                     t.transformed_ki, t.max_transformed_ki,
                                     active_sprite,
                                     bar_start_x=bar_start, bar_end_x=bar_end)


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