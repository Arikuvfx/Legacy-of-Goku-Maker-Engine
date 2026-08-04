import pygame
import math
import random
from config.settings import RENDER_SCALE


class DestructibleStone:

    def __init__(self, x, y, stone_type='small'):
        self.x = x
        self.y = y
        self.stone_type = stone_type
        self.active = True

        # Set properties based on stone type
        if stone_type == 'small':
            self.max_health = 1
            self.width = 16
            self.height = 16
            self.sprite_path = 'assets/objects/stones/small_stone.png'
        elif stone_type == 'medium':
            self.max_health = 2
            self.width = 24
            self.height = 24
            self.sprite_path = 'assets/objects/stones/medium_stone.png'
        elif stone_type == 'big':
            self.max_health = 3
            self.width = 32
            self.height = 32
            self.sprite_path = 'assets/objects/stones/big_stone.png'

        self.health = self.max_health

        # Load sprite
        try:
            self.sprite = pygame.image.load(self.sprite_path).convert_alpha()
            self.sprite = pygame.transform.scale(self.sprite, (self.width, self.height))
        except:
            # Create placeholder if sprite doesn't exist
            self.sprite = self._create_placeholder()

        # Load destruction animation spritesheet
        self.destruction_frames = []
        self.destruction_frame_index = 0
        self.destruction_frame_duration = 0.1  # 100ms per frame
        self.destruction_frame_timer = 0
        self._load_destruction_animation()

        # Shake effect
        self.is_shaking = False
        self.shake_timer = 0
        self.shake_duration = 0.3  # 300ms shake
        self.shake_intensity = 3
        self.shake_offset_x = 0
        self.shake_offset_y = 0

        # Destruction state
        self.is_destroying = False
        self.destroy_timer = 0

        # Collision
        self.solid = True  # Blocks player movement

        # LAYER SYSTEM INTEGRATION
        from core.draw_layers import DrawLayer
        self.draw_layer = DrawLayer.NPCS  # Same layer as player for Y-sorting
        self.y_sort = True  # Enable Y-sorting for depth

        # Track which attacks have already hit this stone with timestamps
        self.hit_by_attacks = {}  # {attack_id: timestamp}
        self.hit_cleanup_interval = 0.5  # Clean up old attack IDs every 0.5 seconds

    def _load_destruction_animation(self):
        """Load destruction animation frames from spritesheet at a fixed 32x32 size"""
        try:
            # Try to load destruction spritesheet (shared by all stone types)
            sheet = pygame.image.load('assets/objects/stone_destruction.png').convert_alpha()

            # Base frame size is always 32x32
            frame_width = 32
            frame_height = 32
            sheet_width = sheet.get_width()
            num_frames = sheet_width // frame_width

            # Extract all frames
            for i in range(num_frames):
                frame_surface = pygame.Surface((frame_width, frame_height), pygame.SRCALPHA)
                # Blit the specific frame from the sheet onto the surface
                frame_surface.blit(sheet, (0, 0), (i * frame_width, 0, frame_width, frame_height))

                # Append the 32x32 frame directly without scaling it to self.width/height
                self.destruction_frames.append(frame_surface)

        except:
            # Create placeholder destruction animation (simple fade frames)
            for i in range(6):  # 6 frame animation
                frame = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
                alpha = int(255 * (1 - (i / 6)))

                # Draw shrinking stone
                scale = 1 - (i / 6) * 0.5
                size = int(self.width * scale)

                if self.stone_type == 'small':
                    color = (120, 120, 130, alpha)
                elif self.stone_type == 'medium':
                    color = (100, 100, 110, alpha)
                else:
                    color = (80, 80, 90, alpha)

                pygame.draw.circle(frame, color, (self.width // 2, self.height // 2), size // 2)
                self.destruction_frames.append(frame)

    def get_sort_key(self):
        """
        Returns sorting key for layer manager.
        This enables dynamic depth sorting based on Y position.
        """
        return (self.draw_layer, self.y)

    def _create_placeholder(self):
        """Create a placeholder sprite if image doesn't exist"""
        sprite = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        # Color based on size
        if self.stone_type == 'small':
            color = (120, 120, 130)
        elif self.stone_type == 'medium':
            color = (100, 100, 110)
        else:
            color = (80, 80, 90)

        # Draw stone shape
        points = [
            (self.width // 2, 2),
            (self.width - 3, self.height // 3),
            (self.width - 2, self.height - 3),
            (self.width // 3, self.height - 2),
            (2, self.height // 2)
        ]
        pygame.draw.polygon(sprite, color, points)
        pygame.draw.polygon(sprite, (60, 60, 70), points, 2)

        # Add some detail
        pygame.draw.circle(sprite, (140, 140, 150),
                           (self.width // 3, self.height // 3), 3)

        return sprite

    def _cleanup_old_attacks(self, current_time):
        """Remove attack IDs older than cleanup interval"""
        # Remove attacks that are older than the cleanup interval
        expired_attacks = [
            attack_id for attack_id, timestamp in self.hit_by_attacks.items()
            if current_time - timestamp > self.hit_cleanup_interval
        ]
        for attack_id in expired_attacks:
            del self.hit_by_attacks[attack_id]

    def check_collision_with_attack(self, attack, attack_type='melee', current_time=None):
        """Check if attack hits this stone"""
        if not self.active or self.is_destroying:
            return False

        # Respond to melee, projectile, and genkidama attacks. Beam/masenko-style
        # attacks aren't handled here — add another branch if stones should
        # react to those too.
        if attack_type not in ('melee', 'projectile', 'genkidama'):
            return False

        # Use pygame time if current_time not provided
        if current_time is None:
            current_time = pygame.time.get_ticks() / 1000.0

        # Clean up old attack IDs
        self._cleanup_old_attacks(current_time)

        # Check if this attack has already hit this stone recently
        attack_id = id(attack)
        if attack_id in self.hit_by_attacks:
            return False

        # Create stone collision rectangle
        stone_rect = pygame.Rect(
            self.x - self.width // 2,
            self.y - self.height // 2,
            self.width,
            self.height
        )

        # MeleeAttack uses 'size' instead of width/height; Projectile-style
        # attacks (ki blasts, genkidama) use 'radius' instead.
        # Check if attack has a get_rect method, size, or radius attribute.
        if hasattr(attack, 'get_rect'):
            attack_rect = attack.get_rect()
        elif hasattr(attack, 'size'):
            attack_rect = pygame.Rect(
                attack.x - attack.size // 2,
                attack.y - attack.size // 2,
                attack.size,
                attack.size
            )
        elif hasattr(attack, 'radius'):
            attack_rect = pygame.Rect(
                attack.x - attack.radius,
                attack.y - attack.radius,
                attack.radius * 2,
                attack.radius * 2
            )
        else:
            # Fallback: use default size
            default_size = 32
            attack_rect = pygame.Rect(
                attack.x - default_size // 2,
                attack.y - default_size // 2,
                default_size,
                default_size
            )

        if stone_rect.colliderect(attack_rect):
            # Mark this attack as having hit this stone with current timestamp
            self.hit_by_attacks[attack_id] = current_time
            if attack_type == 'genkidama':
                self.take_damage(self.health)  # always lethal, regardless of stone_type
            else:
                self.take_damage(1)
            return True

        return False

    def take_damage(self, amount):
        """Take damage and trigger effects"""
        self.health -= amount

        if self.health <= 0:
            # Start destruction
            self._start_destruction()
        else:
            # Start shake (only for medium and big stones)
            if self.stone_type in ['medium', 'big']:
                self.is_shaking = True
                self.shake_timer = 0

    def _start_destruction(self):
        """Start the destruction animation"""
        self.is_destroying = True
        self.destruction_frame_index = 0
        self.destruction_frame_timer = 0
        self.solid = False  # No longer blocks movement

    def update(self, dt):
        """Update stone state"""
        if not self.active:
            return

        # Update shake effect
        if self.is_shaking:
            self.shake_timer += dt

            if self.shake_timer >= self.shake_duration:
                self.is_shaking = False
                self.shake_offset_x = 0
                self.shake_offset_y = 0
            else:
                # Calculate shake offset (diminishes over time)
                progress = self.shake_timer / self.shake_duration
                intensity = self.shake_intensity * (1 - progress)

                # Rapid oscillation
                freq = 30  # Hz
                self.shake_offset_x = math.sin(self.shake_timer * freq * math.pi * 2) * intensity
                self.shake_offset_y = math.cos(self.shake_timer * freq * math.pi * 2) * intensity

        # Update destruction animation
        if self.is_destroying:
            self.destruction_frame_timer += dt

            # Advance to next frame when timer exceeds duration
            if self.destruction_frame_timer >= self.destruction_frame_duration:
                self.destruction_frame_timer = 0
                self.destruction_frame_index += 1

                # Check if animation is complete
                if self.destruction_frame_index >= len(self.destruction_frames):
                    self.active = False  # Remove stone when animation finishes

    def draw(self, screen, camera, colors):
        """
        Draw the stone using RENDER_SCALE from settings

        Coordinate conversion: (world_pos * RENDER_SCALE) - camera_screen_pos
        """
        if not self.active:
            return

        # Calculate screen position using RENDER_SCALE
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        # Apply shake offset (already in world units, so scale it)
        screen_x += self.shake_offset_x * RENDER_SCALE
        screen_y += self.shake_offset_y * RENDER_SCALE

        # Scale sprite using RENDER_SCALE
        scaled_width = int(self.width * RENDER_SCALE)
        scaled_height = int(self.height * RENDER_SCALE)

        if self.is_destroying:
            # Draw current destruction animation frame
            if self.destruction_frame_index < len(self.destruction_frames):
                base_anim_size = 32
                anim_render_size = int(base_anim_size * RENDER_SCALE)

                if self.destruction_frame_index < len(self.destruction_frames):
                    frame = self.destruction_frames[self.destruction_frame_index]
                    # Scale the 32x32 frame by RENDER_SCALE only
                    scaled_frame = pygame.transform.scale(frame, (anim_render_size, anim_render_size))

                    # Center the 32x32 animation over the stone's center
                    sprite_x = int(screen_x - anim_render_size // 2)
                    sprite_y = int(screen_y - anim_render_size // 2)
                    screen.blit(scaled_frame, (sprite_x, sprite_y))
        else:
            # Normal render
            scaled_sprite = pygame.transform.scale(self.sprite, (scaled_width, scaled_height))

            sprite_x = int(screen_x - scaled_width // 2)
            sprite_y = int(screen_y - scaled_height // 2)
            screen.blit(scaled_sprite, (sprite_x, sprite_y))

    def get_collision_rect(self):
        """Return this stone's collision rect for movement-blocking, or None
        while it's not currently solid (destroyed / mid-destruction).

        player.py's generic obstacle-blocking check
        (check_collision_with_obstacles) only recognizes obstacles that
        expose get_collision_rect() — that's how CollisionObject plugs into
        it. DestructibleStone previously only offered
        check_collision_with_player(), which that generic loop never calls
        (it doesn't know about this class specifically), so every stone was
        silently skipped via hasattr() and the player walked straight
        through them despite self.solid being True. Same rect construction
        as check_collision_with_player/check_collision_with_attack below,
        just exposed under the name the generic loop actually looks for.
        """
        if not self.active or not self.solid:
            return None
        return pygame.Rect(
            self.x - self.width // 2,
            self.y - self.height // 2,
            self.width,
            self.height
        )

    def check_collision_with_player(self, player):
        """Check if player collides with this stone (for blocking movement)"""
        if not self.active or not self.solid:
            return False

        # Get player's hitbox
        player_rect = player.get_collision_rect()

        # Stone hitbox (centered circle approximated as rect)
        stone_rect = pygame.Rect(
            self.x - self.width // 2,
            self.y - self.height // 2,
            self.width,
            self.height
        )

        return player_rect.colliderect(stone_rect)