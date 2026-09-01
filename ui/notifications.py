import pygame
import os
import sys


class LevelUpNotification:
    # Native size of a single frame in levelup.png (16x16 spritesheet, one
    # row of frames laid out left-to-right — same convention as map_jump.png
    # in player.py's CharacterSpriteLoader-relative sheets).
    FRAME_W = 16
    FRAME_H = 16
    FRAME_DURATION = 0.2   # seconds per animation frame, loops while active
    # Drawn at the source frame's own native size (like every other HUD
    # icon, e.g. attack_mode_icon) and then scaled by sprite_hud.scale only —
    # do NOT pre-stretch this above FRAME_W/H, or it ends up several times
    # bigger than the HUD frame it sits next to.
    ICON_NATIVE_SIZE = FRAME_W
    ICON_GAP = 8           # native-space gap between the HUD frame and the icon

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.font_large = pygame.font.Font(None, 48)
        self.font_medium = pygame.font.Font(None, 32)
        self.active = False
        self.timer = 0
        self.duration = 3.0  # seconds
        self.level = 1
        self.stat_points = 0

        # Animation state
        self._frame_idx = 0
        self._frame_timer = 0.0
        self._scaled_cache = {}

        self._icon_frames = self._load_levelup_frames()

    def _load_levelup_frames(self):
        """Load levelup.png from the same HUD sprite folder as frame.png
        (assets/ui/hud) and slice it into FRAME_W x FRAME_H frames.

        Mirrors the try/except + print convention used by HUDSprite in
        ui/sprite_hud.py and the map_jump.png loader in player.py, so a
        missing/broken sheet degrades gracefully instead of crashing.
        """
        if getattr(sys, 'frozen', False):
            app_path = os.path.dirname(sys.executable)
        else:
            app_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base_path = os.path.join(app_path, "assets", "ui", "hud")
        path = os.path.join(base_path, "levelup.png")

        frames = []
        try:
            sheet = pygame.image.load(path).convert_alpha()
            num_frames = max(1, sheet.get_width() // self.FRAME_W)
            frames = [
                sheet.subsurface(pygame.Rect(i * self.FRAME_W, 0, self.FRAME_W, self.FRAME_H))
                for i in range(num_frames)
            ]
            print(f"✓ Loaded: levelup.png ({num_frames} frames)")
        except Exception as e:
            print(f"✗ Could not load {path}: {e}")

        return frames

    def show(self, level, stat_points):
        self.active = True
        self.timer = self.duration
        self.level = level
        self.stat_points = stat_points
        # Restart the animation from the first frame each time so replays
        # (leveling up again while a notification is still fading) look right.
        self._frame_idx = 0
        self._frame_timer = 0.0

    def update(self, dt):
        if self.active:
            self.timer -= dt
            if self.timer <= 0:
                self.active = False

        # The HUD icon animates continuously for as long as it's shown
        # (independent of the center-screen popup's timer above), since it's
        # meant to stay next to the HUD rather than fade out with the popup.
        if self._icon_frames:
            self._frame_timer += dt
            while self._frame_timer >= self.FRAME_DURATION:
                self._frame_timer -= self.FRAME_DURATION
                self._frame_idx = (self._frame_idx + 1) % len(self._icon_frames)

    def _get_scaled_icon_frame(self, hud_scale):
        """Pre-scaled-frame cache, keyed by (frame index, hud scale) — same
        source frame at the same HUD scale always comes out the same size,
        so we scale once instead of every draw() call. The cache key includes
        hud_scale so it stays correct if the HUD is ever resized at runtime.
        """
        key = (self._frame_idx, hud_scale)
        scaled = self._scaled_cache.get(key)
        if scaled is None:
            size_px = max(1, int(self.ICON_NATIVE_SIZE * hud_scale))
            scaled = pygame.transform.scale(self._icon_frames[self._frame_idx], (size_px, size_px))
            self._scaled_cache[key] = scaled
        return scaled

    def draw(self, screen, colors, sprite_hud=None, player=None):
        # ── Animated levelup.png icon — pinned right next to the player's
        # HUD frame. This is NOT tied to the popup's timer: it stays put for
        # as long as the player has unspent stat points, same trigger the
        # old pulsing-circle-with-a-number indicator used (now replaced by
        # this icon), so it doesn't disappear after a few seconds.
        if self._icon_frames and sprite_hud is not None and player is not None \
                and getattr(player, 'stat_points', 0) > 0:
            icon = self._get_scaled_icon_frame(sprite_hud.scale)

            cfg = sprite_hud.config['frame']
            frame_w = int(cfg['w'] * sprite_hud.scale)
            frame_h = int(cfg['h'] * sprite_hud.scale)
            gap     = int(self.ICON_GAP * sprite_hud.scale)

            hud_x = sprite_hud.hud_x
            hud_y = sprite_hud.hud_y + int(sprite_hud.hud_offset_y)

            icon_x = hud_x + frame_w + gap
            icon_y = hud_y + (frame_h - icon.get_height()) // 2

            screen.blit(icon, (icon_x, icon_y))

        if not self.active:
            return

        # Create surface for text
        level_text = self.font_large.render("LEVEL UP!", True, colors['YELLOW'])
        details_text = self.font_medium.render(f"Level {self.level} | +{self.stat_points} Stat Points", True, colors['WHITE'])

        # Center on screen
        level_rect = level_text.get_rect(center=(self.screen_width // 2, self.screen_height // 2 - 40))
        details_rect = details_text.get_rect(center=(self.screen_width // 2, self.screen_height // 2 + 20))

        # Draw background
        bg_rect = pygame.Rect(level_rect.left - 20, level_rect.top - 20,
                             max(level_rect.width, details_rect.width) + 40, 120)
        pygame.draw.rect(screen, colors['BLACK'], bg_rect)
        pygame.draw.rect(screen, colors['YELLOW'], bg_rect, 3)

        # Draw text
        screen.blit(level_text, level_rect)
        screen.blit(details_text, details_rect)