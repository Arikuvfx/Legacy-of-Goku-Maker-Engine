import pygame
import os


class HUDSprite:
    """Loads and manages HUD sprite elements"""

    def __init__(self, filepath):
        self.sprite = None
        self.filepath = filepath
        if os.path.exists(filepath):
            try:
                self.sprite = pygame.image.load(filepath).convert_alpha()
                print(f"✓ Loaded: {os.path.basename(filepath)}")
            except Exception as e:
                print(f"✗ Error loading {filepath}: {e}")
        else:
            print(f"✗ Not found: {filepath}")

    def draw(self, screen, x, y, width=None, height=None):
        """Draw the sprite at position with optional scaling"""
        if not self.sprite:
            return False

        if width and height:
            scaled = pygame.transform.scale(self.sprite, (width, height))
            screen.blit(scaled, (x, y))
        else:
            screen.blit(self.sprite, (x, y))
        return True


class SpriteHUD:
    """
    Legacy of Goku style HUD system using ONLY sprites

    Adjust self.scale to resize the entire HUD:
    - 0.5 = 50% size (compact)
    - 1.0 = 100% size (normal)
    - 1.5 = 150% size (large)
    """

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.visible = True

        # HUD positioning (top-left corner)
        self.hud_x = 10
        self.hud_y = 10

        # HUD scaling factor - CHANGE THIS TO RESIZE THE ENTIRE HUD
        self.scale = 0.7  # 1.0 = 100%, 0.5 = 50%, 1.5 = 150%, etc.

        # Build absolute path to assets
        import sys
        if getattr(sys, 'frozen', False):
            application_path = os.path.dirname(sys.executable)
        else:
            application_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        self.base_path = os.path.join(application_path, "assets", "ui", "hud")

        print(f"\n=== Loading HUD Sprites ===")
        print(f"Looking in: {self.base_path}")
        print(f"HUD Scale: {self.scale * 100:.0f}%\n")

        # Load all sprites
        self.sprites = {
            'frame': HUDSprite(os.path.join(self.base_path, "frame.png")),
            'hp_bar': HUDSprite(os.path.join(self.base_path, "hp_bar.png")),
            'ki_bar': HUDSprite(os.path.join(self.base_path, "ki_bar.png")),
            'exp_bar': HUDSprite(os.path.join(self.base_path, "exp_bar.png")),
            'transform_bar': HUDSprite(os.path.join(self.base_path, "transform_bar.png")),
            'attack_icon_blast': HUDSprite(os.path.join(self.base_path, "attack_icon_blast.png")),
            'attack_icon_beam': HUDSprite(os.path.join(self.base_path, "attack_icon_beam.png")),
            'attack_icon': HUDSprite(os.path.join(self.base_path, "attack_icon.png")),
        }

        print(f"\n=== HUD Loading Complete ===\n")

        # HUD layout configuration (base dimensions before scaling)
        self.config = {
            'frame': {'x': 0, 'y': 0, 'w': 338, 'h': 100},
            'attack_icon': {'x': 0, 'y': 0, 'w': 338, 'h': 100},
            'hp_bar': {'x': 0, 'y': 0, 'w': 338, 'h': 100},
            'ki_bar': {'x': 0, 'y': 0, 'w': 338, 'h': 100},
            'exp_bar': {'x': 0, 'y': 0, 'w': 338, 'h': 100},
            'transform_bar': {'x': 0, 'y': 0, 'w': 338, 'h': 100},
        }

        # Fonts for text overlay
        pygame.font.init()
        self.font_small = pygame.font.Font(None, 18)
        self.font_medium = pygame.font.Font(None, 22)
        self.font_large = pygame.font.Font(None, 26)

        # Colors for text
        self.colors = {
            'text': (255, 255, 255),
            'shadow': (0, 0, 0),
            'stat_points': (255, 215, 0),  # Gold
            'transform_ready': (255, 255, 255),  # White
            'transform_fill': (255, 215, 0),  # Gold
        }

    def draw_text_with_shadow(self, screen, text, x, y, font, color, shadow_offset=2):
        """Draw text with a shadow for better readability"""
        scaled_offset = max(1, int(shadow_offset * self.scale))
        shadow_surf = font.render(text, True, self.colors['shadow'])
        screen.blit(shadow_surf, (x + scaled_offset, y + scaled_offset))
        text_surf = font.render(text, True, color)
        screen.blit(text_surf, (x, y))

    def draw_bar_simple(self, screen, x, y, width, height, current, maximum, bar_sprite):
        """Draw a bar by cropping the sprite based on current/max ratio"""
        if maximum > 0 and bar_sprite.sprite:
            fill_percentage = current / maximum
            fill_width = int(width * fill_percentage)

            if fill_width > 0:
                temp_surface = pygame.transform.scale(bar_sprite.sprite, (width, height))
                filled_portion = temp_surface.subsurface((0, 0, fill_width, height))
                screen.blit(filled_portion, (x, y))

    def draw_transform_bar_with_shine(self, screen, x, y, width, height, progress, is_ready, shine_alpha, bar_sprite):
        """Draw transformation bar with special shine effect when ready"""
        if bar_sprite.sprite:
            fill_width = int(width * progress)

            if fill_width > 0:
                # Draw the filled portion
                temp_surface = pygame.transform.scale(bar_sprite.sprite, (width, height))
                filled_portion = temp_surface.subsurface((0, 0, fill_width, height))
                screen.blit(filled_portion, (x, y))

            # Add shine effect when ready
            if is_ready and shine_alpha > 0:
                # Create shine overlay
                shine_surface = pygame.Surface((width, height), pygame.SRCALPHA)
                shine_surface.fill((255, 255, 255, shine_alpha))
                screen.blit(shine_surface, (x, y))

                # Add extra glow effect
                glow_surface = pygame.Surface((width + 10, height + 10), pygame.SRCALPHA)
                pygame.draw.rect(glow_surface, (255, 255, 0, shine_alpha // 2),
                                 (0, 0, width + 10, height + 10), 5)
                screen.blit(glow_surface, (x - 5, y - 5))

    def draw(self, screen, player):
        """Draw the complete HUD using only sprites"""
        if not self.visible:
            return

        base_x = self.hud_x
        base_y = self.hud_y

        def scaled(value):
            return int(value * self.scale)

        # 1. Draw main frame
        frame_cfg = self.config['frame']
        if self.sprites['frame'].sprite:
            self.sprites['frame'].draw(
                screen, base_x, base_y,
                scaled(frame_cfg['w']), scaled(frame_cfg['h'])
            )

        # 2. Draw attack_icon based on player's current attack mode
        icon_cfg = self.config['attack_icon']
        icon_x = base_x + scaled(icon_cfg['x'])
        icon_y = base_y + scaled(icon_cfg['y'])

        attack_mode = player.ki_attack_mode if hasattr(player, 'ki_attack_mode') else 'blast'

        if attack_mode == 'beam' and self.sprites['attack_icon_beam'].sprite:
            self.sprites['attack_icon_beam'].draw(
                screen, icon_x, icon_y,
                scaled(icon_cfg['w']), scaled(icon_cfg['h'])
            )
        elif attack_mode == 'blast' and self.sprites['attack_icon_blast'].sprite:
            self.sprites['attack_icon_blast'].draw(
                screen, icon_x, icon_y,
                scaled(icon_cfg['w']), scaled(icon_cfg['h'])
            )
        elif self.sprites['attack_icon'].sprite:
            self.sprites['attack_icon'].draw(
                screen, icon_x, icon_y,
                scaled(icon_cfg['w']), scaled(icon_cfg['h'])
            )

        # 3. Draw HP Bar
        hp_cfg = self.config['hp_bar']
        hp_x = base_x + scaled(hp_cfg['x'])
        hp_y = base_y + scaled(hp_cfg['y'])

        self.draw_bar_simple(
            screen, hp_x, hp_y, scaled(hp_cfg['w']), scaled(hp_cfg['h']),
            player.hp, player.max_hp,
            self.sprites['hp_bar']
        )

        # 4. Draw Ki Bar
        ki_cfg = self.config['ki_bar']
        ki_x = base_x + scaled(ki_cfg['x'])
        ki_y = base_y + scaled(ki_cfg['y'])

        self.draw_bar_simple(
            screen, ki_x, ki_y, scaled(ki_cfg['w']), scaled(ki_cfg['h']),
            player.ki, player.max_ki,
            self.sprites['ki_bar']
        )

        # 5. Draw EXP Bar
        exp_cfg = self.config['exp_bar']
        exp_x = base_x + scaled(exp_cfg['x'])
        exp_y = base_y + scaled(exp_cfg['y'])

        self.draw_bar_simple(
            screen, exp_x, exp_y, scaled(exp_cfg['w']), scaled(exp_cfg['h']),
            player.exp, player.exp_to_next_level,
            self.sprites['exp_bar']
        )

        # 6. Draw Transformation Bar (if enabled and player has transformation system)
        if hasattr(player, 'transformation') and player.transformation:
            transform_cfg = self.config['transform_bar']
            transform_x = base_x + scaled(transform_cfg['x'])
            transform_y = base_y + scaled(transform_cfg['y'])

            shine_alpha = player.transformation.get_shine_alpha()

            self.draw_transform_bar_with_shine(
                screen, transform_x, transform_y,
                scaled(transform_cfg['w']), scaled(transform_cfg['h']),
                player.transformation.progress,
                player.transformation.is_ready,
                shine_alpha,
                self.sprites['transform_bar']
            )

        # 7. Stat points indicator (if unspent points exist)
        if player.stat_points > 0:
            import time
            pulse = abs(int((time.time() * 3) % 2 - 1) * 80) + 175
            pulse_color = (pulse, pulse, 0)

            stat_x = base_x + scaled(self.config['frame']['w']) - scaled(30)
            stat_y = base_y + scaled(self.config['frame']['h']) - scaled(25)

            # Glow effect
            base_radius = scaled(12)
            for radius in range(scaled(18), base_radius, max(1, -scaled(2))):
                alpha = 50 - (scaled(18) - radius) * 8
                s = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
                pygame.draw.circle(s, (*pulse_color, alpha), (radius, radius), radius)
                screen.blit(s, (stat_x - radius, stat_y - radius))

            # Main circle
            pygame.draw.circle(screen, pulse_color, (stat_x, stat_y), base_radius)
            pygame.draw.circle(screen, self.colors['stat_points'], (stat_x, stat_y), base_radius, max(1, scaled(2)))
            pygame.draw.circle(screen, self.colors['shadow'], (stat_x, stat_y), scaled(10), 1)

            # Number
            stat_text = str(player.stat_points)
            text_width = self.font_large.size(stat_text)[0]
            self.draw_text_with_shadow(
                screen, stat_text,
                stat_x - text_width // 2,
                stat_y - scaled(8),
                self.font_large, (255, 255, 255)
            )