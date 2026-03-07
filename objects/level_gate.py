import pygame
import math
from config.utils.gate_font import get_gate_font
from config.settings import RENDER_SCALE


class LevelGate:
    """A destructible gate that requires a minimum player level to break"""

    def __init__(self, x, y, gate_type='stone', required_level=1):
        self.x = x
        self.y = y
        self.gate_type = gate_type
        self.required_level = required_level
        self.active = True

        # Gate type configurations (width, height, max_health)
        self.gate_configs = {
            'stone': {'width': 32, 'height': 32, 'health': 1},
            'wood': {'width': 32, 'height': 32, 'health': 1},
            'makeshift wood': {'width': 32, 'height': 32, 'health': 1},
            'stone formation': {'width': 32, 'height': 32, 'health': 1},
            'metal': {'width': 32, 'height': 32, 'health': 1}
        }

        config = self.gate_configs.get(gate_type, self.gate_configs['stone'])
        self.width = config['width']
        self.height = config['height']
        self.max_health = config['health']
        self.health = self.max_health

        # Visual effects
        self.flash_timer = 0
        self.shake_offset_x = 0
        self.shake_offset_y = 0
        self.float_offset = 0
        self.float_timer = 0
        self.destruction_timer = 0
        self.is_destroying = False

        # Metal gate specific - unlocked state
        self.is_unlocked = False
        self.unlocked_sprite = None

        # Destruction animation (similar to stones)
        self.destruction_frames = []
        self.destruction_frame_index = 0
        self.destruction_frame_duration = 0.1
        self.destruction_frame_timer = 0

        # Load sprite
        self.sprite = None
        self._load_sprite()

        # Load destruction animation (not used for metal gates)
        if self.gate_type != 'metal':
            self._load_destruction_animation()

        # Layer support
        self.draw_layer = 0
        self.y_sort = True

    def _load_sprite(self):
        """Load the gate sprite based on type"""
        try:
            sprite_path = f'assets/objects/gates/{self.gate_type}_gate.png'
            self.sprite = pygame.image.load(sprite_path).convert_alpha()
            self.sprite = pygame.transform.scale(self.sprite, (self.width, self.height))

            # Load unlocked sprite for metal gates
            if self.gate_type == 'metal':
                try:
                    unlocked_path = f'assets/objects/gates/{self.gate_type}_gate_unlocked.png'
                    self.unlocked_sprite = pygame.image.load(unlocked_path).convert_alpha()
                    self.unlocked_sprite = pygame.transform.scale(self.unlocked_sprite, (self.width, self.height))
                except:
                    # Create placeholder unlocked sprite
                    self.unlocked_sprite = self._create_placeholder_unlocked_sprite()
        except:
            # Create placeholder sprite
            self.sprite = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

            # Different colors for different gate types
            colors = {
                'stone': (80, 80, 120),
                'wood': (100, 100, 140),
                'makeshift wood': (120, 120, 160),
                'stone formation': (140, 100, 180),
                'metal': (160, 160, 180)
            }
            base_color = colors.get(self.gate_type, (100, 100, 100))

            # Draw gate structure
            pygame.draw.rect(self.sprite, base_color, (0, 0, self.width, self.height))
            pygame.draw.rect(self.sprite, (50, 50, 80), (0, 0, self.width, self.height), 3)

            # Add decorative bars
            bar_count = 3 if self.gate_type in ['stone', 'wood'] else 5
            for i in range(bar_count):
                y_pos = (i + 1) * (self.height // (bar_count + 1))
                pygame.draw.line(self.sprite, (60, 60, 90),
                                 (0, y_pos), (self.width, y_pos), 2)

            # Add lock symbol for locked state
            lock_size = self.width // 4
            lock_x = self.width // 2
            lock_y = self.height // 2
            pygame.draw.circle(self.sprite, (200, 200, 50), (lock_x, lock_y), lock_size)
            pygame.draw.circle(self.sprite, (150, 150, 30), (lock_x, lock_y), lock_size, 2)

            # Create unlocked sprite for metal gates
            if self.gate_type == 'metal':
                self.unlocked_sprite = self._create_placeholder_unlocked_sprite()

    def _create_placeholder_unlocked_sprite(self):
        """Create a placeholder sprite for unlocked metal gate"""
        sprite = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        # Same base color but slightly brighter
        base_color = (180, 180, 200)

        # Draw gate structure
        pygame.draw.rect(sprite, base_color, (0, 0, self.width, self.height))
        pygame.draw.rect(sprite, (70, 70, 90), (0, 0, self.width, self.height), 3)

        # Add decorative bars
        for i in range(5):
            y_pos = (i + 1) * (self.height // 6)
            pygame.draw.line(sprite, (80, 80, 110),
                             (0, y_pos), (self.width, y_pos), 2)

        # NO lock symbol - it's unlocked!
        # Instead, draw an open lock or checkmark
        check_size = self.width // 4
        check_x = self.width // 2
        check_y = self.height // 2

        # Draw a simple checkmark
        pygame.draw.circle(sprite, (100, 255, 100), (check_x, check_y), check_size, 2)
        pygame.draw.line(sprite, (100, 255, 100),
                         (check_x - check_size // 2, check_y),
                         (check_x - check_size // 4, check_y + check_size // 2), 2)
        pygame.draw.line(sprite, (100, 255, 100),
                         (check_x - check_size // 4, check_y + check_size // 2),
                         (check_x + check_size // 2, check_y - check_size // 2), 2)

        return sprite

    def _load_destruction_animation(self):
        """Load destruction animation based on gate type"""
        # Stone gate uses stone_destruction.png
        # Wood-based gates use brown_destruction.png
        if self.gate_type == 'stone':
            anim_path = 'assets/objects/stone_destruction.png'
        else:
            anim_path = 'assets/objects/brown_destruction.png'

        try:
            sheet = pygame.image.load(anim_path).convert_alpha()

            # Base frame size is always 32x32
            frame_width = 32
            frame_height = 32
            sheet_width = sheet.get_width()
            num_frames = sheet_width // frame_width

            # Extract all frames
            for i in range(num_frames):
                frame_surface = pygame.Surface((frame_width, frame_height), pygame.SRCALPHA)
                frame_surface.blit(sheet, (0, 0), (i * frame_width, 0, frame_width, frame_height))
                self.destruction_frames.append(frame_surface)

        except:
            # Create placeholder destruction animation
            for i in range(6):
                frame = pygame.Surface((32, 32), pygame.SRCALPHA)
                alpha = int(255 * (1 - (i / 6)))
                scale = 1 - (i / 6) * 0.5
                size = int(32 * scale)

                color = (120, 90, 60, alpha) if self.gate_type != 'stone' else (100, 100, 110, alpha)
                pygame.draw.circle(frame, color, (16, 16), size // 2)
                self.destruction_frames.append(frame)

    def can_be_destroyed_by(self, player):
        """Check if player meets level requirement"""
        return player.level >= self.required_level

    def check_collision_with_player(self, player):
        """Check if player is colliding with this gate"""
        if not self.active:
            return False

        # Metal gates that are unlocked don't block
        if self.gate_type == 'metal' and self.is_unlocked:
            return False

        # Get player's hitbox
        player_rect = player.get_collision_rect()

        # Gate hitbox
        gate_rect = pygame.Rect(
            self.x - self.width // 2,
            self.y - self.height // 2,
            self.width,
            self.height
        )

        return player_rect.colliderect(gate_rect)

    def check_collision_with_attack(self, attack, attack_type, player=None):
        """
        Handle damage from attacks - only if player level is sufficient

        Args:
            attack: The attack object (melee, projectile, or beam)
            attack_type: Type of attack ('melee', 'projectile', 'beam')
            player: Optional player reference (passed from game.py for melee attacks)
        """
        if not self.active or self.is_destroying:
            return False

        # Metal gates that are unlocked can't be hit
        if self.gate_type == 'metal' and self.is_unlocked:
            return False

        # Try to get player reference from attack owner
        if player is None:
            player = getattr(attack, 'owner', None)

        # Only take damage if player meets level requirement
        if not player or not self.can_be_destroyed_by(player):
            # Player doesn't meet level requirement - show visual feedback but don't damage
            self.flash_timer = 0.05
            return False

        # Different collision detection for different attack types
        collided = False

        if attack_type == 'melee':
            # Create gate collision rectangle
            gate_rect = pygame.Rect(
                self.x - self.width // 2,
                self.y - self.height // 2,
                self.width,
                self.height
            )

            # MeleeAttack uses 'size' instead of width/height
            if hasattr(attack, 'get_rect'):
                attack_rect = attack.get_rect()
            elif hasattr(attack, 'size'):
                attack_rect = pygame.Rect(
                    attack.x - attack.size // 2,
                    attack.y - attack.size // 2,
                    attack.size,
                    attack.size
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

            collided = gate_rect.colliderect(attack_rect)
        else:
            # For projectile and beam, use circle collision
            attack_x = attack.x
            attack_y = attack.y

            # Check collision bounds
            distance = math.sqrt((self.x - attack_x) ** 2 + (self.y - attack_y) ** 2)
            hit_radius = max(self.width, self.height) / 2
            collided = distance < hit_radius

        if collided:
            # Metal gates unlock instead of taking damage
            if self.gate_type == 'metal' and attack_type == 'melee':
                self.is_unlocked = True
                self.flash_timer = 0.2  # Longer flash for unlock effect
                return True

            # Regular gates take damage
            damage = {
                'melee': 10,
                'projectile': 15,
                'beam': 5
            }.get(attack_type, 5)

            self.health -= damage
            self.flash_timer = 0.1

            # Shake effect
            self.shake_offset_x = (hash(self.flash_timer) % 3) - 1
            self.shake_offset_y = (hash(self.flash_timer * 2) % 3) - 1

            if self.health <= 0:
                self.start_destruction()

            return True

        return False

    def start_destruction(self):
        """Begin destruction animation"""
        self.is_destroying = True
        self.destruction_timer = 0
        self.destruction_frame_index = 0
        self.destruction_frame_timer = 0

    def update(self, dt):
        """Update gate state and animations"""
        if not self.active:
            return

        # Flash effect when damaged
        if self.flash_timer > 0:
            self.flash_timer -= dt
            if self.flash_timer <= 0:
                self.shake_offset_x = 0
                self.shake_offset_y = 0

        # Floating animation for level text (only if not unlocked)
        if not (self.gate_type == 'metal' and self.is_unlocked):
            self.float_timer += dt * 2
            self.float_offset = math.sin(self.float_timer) * 5

        # Destruction animation (not for metal gates)
        if self.is_destroying and self.gate_type != 'metal':
            self.destruction_frame_timer += dt

            # Advance to next frame
            if self.destruction_frame_timer >= self.destruction_frame_duration:
                self.destruction_frame_timer = 0
                self.destruction_frame_index += 1

                # Check if animation is complete
                if self.destruction_frame_index >= len(self.destruction_frames):
                    self.active = False

    def draw(self, screen, camera, colors):
        """Draw the gate and floating level requirement"""
        if not self.active:
            return

        screen_x = (self.x * RENDER_SCALE) - camera.x + self.shake_offset_x
        screen_y = (self.y * RENDER_SCALE) - camera.y + self.shake_offset_y

        scaled_width = int(self.width * RENDER_SCALE)
        scaled_height = int(self.height * RENDER_SCALE)

        # Metal gates that are destroying don't use animation
        if self.is_destroying and self.gate_type != 'metal':
            # Draw destruction animation (32x32 frames scaled by RENDER_SCALE)
            if self.destruction_frame_index < len(self.destruction_frames):
                base_anim_size = 32
                anim_render_size = int(base_anim_size * RENDER_SCALE)

                frame = self.destruction_frames[self.destruction_frame_index]
                scaled_frame = pygame.transform.scale(frame, (anim_render_size, anim_render_size))

                # Center the animation
                sprite_x = int(screen_x - anim_render_size // 2)
                sprite_y = int(screen_y - anim_render_size // 2)
                screen.blit(scaled_frame, (sprite_x, sprite_y))
        else:
            # Draw gate sprite (use unlocked sprite if metal and unlocked)
            current_sprite = self.sprite
            if self.gate_type == 'metal' and self.is_unlocked and self.unlocked_sprite:
                current_sprite = self.unlocked_sprite

            if current_sprite:
                scaled_sprite = pygame.transform.scale(current_sprite, (scaled_width, scaled_height))

                # Flash white when damaged
                if self.flash_timer > 0:
                    flash_surf = scaled_sprite.copy()
                    # Green flash for unlock, white for damage
                    if self.gate_type == 'metal' and self.is_unlocked:
                        flash_surf.fill((100, 255, 100, 150), special_flags=pygame.BLEND_RGBA_ADD)
                    else:
                        flash_surf.fill((255, 255, 255, 100), special_flags=pygame.BLEND_RGBA_ADD)
                    scaled_sprite = flash_surf

                sprite_x = int(screen_x - scaled_width // 2)
                sprite_y = int(screen_y - scaled_height // 2)
                screen.blit(scaled_sprite, (sprite_x, sprite_y))

            # Draw floating level requirement (only if not unlocked)
            if not (self.gate_type == 'metal' and self.is_unlocked):
                self._draw_level_requirement(screen, screen_x, screen_y, colors)

            # Draw health bar (only for non-metal gates)
            if self.health < self.max_health and self.gate_type != 'metal':
                self._draw_health_bar(screen, screen_x, screen_y, scaled_width, colors)

    def _draw_level_requirement(self, screen, screen_x, screen_y, colors):
        """Draw the floating level requirement text"""
        font = get_gate_font()
        level_text = f"{self.required_level}"

        # Create text with outline
        text_color = (255, 215, 0)  # Gold
        outline_color = (0, 0, 0)

        # Floating position
        float_y = screen_y - (self.height * RENDER_SCALE // 2) + 35 + self.float_offset

        # Draw outline
        for dx in [-0.1, 0, 0]:
            for dy in [-0, 0, 0]:
                if dx != 0 or dy != 0:
                    outline_surf = font.render(level_text, True, outline_color)
                    outline_rect = outline_surf.get_rect(center=(int(screen_x + dx + 2), int(float_y + dy + 2)))
                    screen.blit(outline_surf, outline_rect)

        # Draw main text
        text_surf = font.render(level_text, True, text_color)
        text_rect = text_surf.get_rect(center=(int(screen_x), int(float_y)))
        screen.blit(text_surf, text_rect)

    def _draw_health_bar(self, screen, screen_x, screen_y, scaled_width, colors):
        """Draw health bar above the gate"""
        bar_width = scaled_width
        bar_height = 6
        bar_x = int(screen_x - bar_width // 2)
        bar_y = int(screen_y - (self.height * RENDER_SCALE // 2) - 15)

        # Background
        pygame.draw.rect(screen, (50, 50, 50), (bar_x, bar_y, bar_width, bar_height))

        # Health
        health_width = int(bar_width * (self.health / self.max_health))
        health_color = (100, 255, 100) if self.health > self.max_health * 0.5 else (255, 100, 100)
        pygame.draw.rect(screen, health_color, (bar_x, bar_y, health_width, bar_height))

        # Border
        pygame.draw.rect(screen, (255, 255, 255), (bar_x, bar_y, bar_width, bar_height), 1)

    def get_sort_key(self):
        """For layer sorting"""
        return (self.draw_layer, self.y if self.y_sort else 0)


class LevelGateManager:
    """Manages all level gates in the game"""

    def __init__(self):
        self.gates = {}  # room_name -> list of gates

    def add_gate(self, room_name, gate):
        """Add a gate to a specific room"""
        if room_name not in self.gates:
            self.gates[room_name] = []
        self.gates[room_name].append(gate)

    def get_gates(self, room_name):
        """Get all gates in a room"""
        return self.gates.get(room_name, [])

    def remove_gate(self, room_name, gate):
        """Remove a gate from a room"""
        if room_name in self.gates and gate in self.gates[room_name]:
            self.gates[room_name].remove(gate)

    def clear_room(self, room_name):
        """Remove all gates from a room"""
        if room_name in self.gates:
            self.gates[room_name] = []