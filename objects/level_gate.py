import pygame
import math
from config.utils.gate_font import get_gate_font
from config.settings import RENDER_SCALE


class LevelGate:
    """
    Destructible gate that only breaks if the player meets the level requirement.
    Metal gates are a special case — they unlock on melee instead of shattering.
    """

    def __init__(self, x, y, gate_type='stone', required_level=1):
        self.x = x
        self.y = y
        self.gate_type = gate_type
        self.required_level = required_level
        self.active = True

        gate_configs = {
            'stone':           {'width': 32, 'height': 32, 'health': 1},
            'wood':            {'width': 32, 'height': 32, 'health': 1},
            'makeshift wood':  {'width': 32, 'height': 32, 'health': 1},
            'stone formation': {'width': 71, 'height': 68, 'health': 1},
            'metal':           {'width': 32, 'height': 32, 'health': 1},
        }

        config = gate_configs.get(gate_type, gate_configs['stone'])
        self.width      = config['width']
        self.height     = config['height']
        self.max_health = config['health']
        self.health     = self.max_health

        # Visual state
        self.flash_timer    = 0
        self.shake_offset_x = 0
        self.shake_offset_y = 0
        self.float_offset   = 0
        self.float_timer    = 0

        # Destruction animation
        self.is_destroying            = False
        self.destruction_timer        = 0
        self.destruction_frames       = []
        self.destruction_frame_index  = 0
        self.destruction_frame_duration = 0.1
        self.destruction_frame_timer  = 0

        # Metal-only: unlocked state (separate sprite, no destruction)
        self.is_unlocked    = False
        self.unlocked_sprite = None

        self.sprite = None
        self._load_sprite()

        if self.gate_type != 'metal':
            self._load_destruction_animation()

        # Sorting support
        self.draw_layer = 0
        self.y_sort = True

    # ── Sprite loading ────────────────────────────────────────────────────────

    def _load_sprite(self):
        try:
            path = f'assets/objects/gates/{self.gate_type}_gate.png'
            self.sprite = pygame.transform.scale(
                pygame.image.load(path).convert_alpha(),
                (self.width, self.height)
            )
            if self.gate_type == 'metal':
                try:
                    upath = f'assets/objects/gates/{self.gate_type}_gate_unlocked.png'
                    self.unlocked_sprite = pygame.transform.scale(
                        pygame.image.load(upath).convert_alpha(),
                        (self.width, self.height)
                    )
                except Exception:
                    self.unlocked_sprite = self._create_placeholder_unlocked_sprite()
        except Exception:
            self._build_placeholder_sprite()

    def _build_placeholder_sprite(self):
        """Fallback colored rectangle with bars when the asset isn't found."""
        self.sprite = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        color_map = {
            'stone':           (80, 80, 120),
            'wood':            (100, 100, 140),
            'makeshift wood':  (120, 120, 160),
            'stone formation': (140, 100, 180),
            'metal':           (160, 160, 180),
        }
        base = color_map.get(self.gate_type, (100, 100, 100))
        pygame.draw.rect(self.sprite, base,          (0, 0, self.width, self.height))
        pygame.draw.rect(self.sprite, (50, 50, 80),  (0, 0, self.width, self.height), 3)

        bar_count = 3 if self.gate_type in ('stone', 'wood') else 5
        for i in range(bar_count):
            y = (i + 1) * (self.height // (bar_count + 1))
            pygame.draw.line(self.sprite, (60, 60, 90), (0, y), (self.width, y), 2)

        # Little lock circle in the centre
        lx, ly = self.width // 2, self.height // 2
        r = self.width // 4
        pygame.draw.circle(self.sprite, (200, 200, 50), (lx, ly), r)
        pygame.draw.circle(self.sprite, (150, 150, 30), (lx, ly), r, 2)

        if self.gate_type == 'metal':
            self.unlocked_sprite = self._create_placeholder_unlocked_sprite()

    def _create_placeholder_unlocked_sprite(self):
        """Same as the locked placeholder but with a checkmark instead of a lock."""
        surf = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        pygame.draw.rect(surf, (180, 180, 200), (0, 0, self.width, self.height))
        pygame.draw.rect(surf, (70, 70, 90),    (0, 0, self.width, self.height), 3)
        for i in range(5):
            y = (i + 1) * (self.height // 6)
            pygame.draw.line(surf, (80, 80, 110), (0, y), (self.width, y), 2)

        cx, cy = self.width // 2, self.height // 2
        r = self.width // 4
        green = (100, 255, 100)
        pygame.draw.circle(surf, green, (cx, cy), r, 2)
        pygame.draw.line(surf, green, (cx - r//2, cy),            (cx - r//4, cy + r//2), 2)
        pygame.draw.line(surf, green, (cx - r//4, cy + r//2),     (cx + r//2, cy - r//2), 2)
        return surf

    def _load_destruction_animation(self):
        path = 'assets/objects/stone_destruction.png' if self.gate_type == 'stone' \
               else 'assets/objects/brown_destruction.png'
        try:
            sheet = pygame.image.load(path).convert_alpha()
            fw = 32
            for i in range(sheet.get_width() // fw):
                frame = pygame.Surface((fw, fw), pygame.SRCALPHA)
                frame.blit(sheet, (0, 0), (i * fw, 0, fw, fw))
                self.destruction_frames.append(frame)
        except Exception:
            # Simple fade-out fallback
            base = (120, 90, 60) if self.gate_type != 'stone' else (100, 100, 110)
            for i in range(6):
                frame = pygame.Surface((32, 32), pygame.SRCALPHA)
                alpha = int(255 * (1 - i / 6))
                size  = int(32 * (1 - i / 6 * 0.5))
                pygame.draw.circle(frame, (*base, alpha), (16, 16), size // 2)
                self.destruction_frames.append(frame)

    # ── Collision / damage ────────────────────────────────────────────────────

    def can_be_destroyed_by(self, player) -> bool:
        return player.level >= self.required_level

    def get_collision_rect(self):
        """Return the gate's solid rect, or None when it should not block movement.

        The player's check_collision_with_obstacles loop calls get_collision_rect()
        on every obstacle.  Returning None signals 'no collision this frame' without
        the caller needing to know anything about gate state.
        """
        if not self.active or self.is_destroying:
            return None
        if self.gate_type == 'metal' and self.is_unlocked:
            return None
        return pygame.Rect(
            self.x - self.width  // 2,
            self.y - self.height // 2,
            self.width, self.height,
        )

    def check_collision_with_player(self, player) -> bool:
        if not self.active:
            return False
        if self.gate_type == 'metal' and self.is_unlocked:
            return False
        gate_rect = pygame.Rect(
            self.x - self.width  // 2,
            self.y - self.height // 2,
            self.width, self.height
        )
        return player.get_collision_rect().colliderect(gate_rect)

    def check_collision_with_attack(self, attack, attack_type, player=None):
        """
        Returns True if the attack lands and the gate reacts.
        Undershooting the level requirement shows a brief flash but deals no damage.
        """
        if not self.active or self.is_destroying:
            return False
        if self.gate_type == 'metal' and self.is_unlocked:
            return False

        if player is None:
            player = getattr(attack, 'owner', None)

        if not player or not self.can_be_destroyed_by(player):
            # Flash to communicate "nope, too weak"
            self.flash_timer = 0.05
            return False

        gate_rect = pygame.Rect(
            self.x - self.width  // 2,
            self.y - self.height // 2,
            self.width, self.height
        )

        if attack_type == 'melee':
            if hasattr(attack, 'get_rect'):
                attack_rect = attack.get_rect()
            elif hasattr(attack, 'size'):
                s = attack.size
                attack_rect = pygame.Rect(attack.x - s//2, attack.y - s//2, s, s)
            else:
                d = 32
                attack_rect = pygame.Rect(attack.x - d//2, attack.y - d//2, d, d)
            collided = gate_rect.colliderect(attack_rect)
        else:
            # Projectiles and beams use distance-based check
            dist = math.hypot(self.x - attack.x, self.y - attack.y)
            collided = dist < max(self.width, self.height) / 2

        if not collided:
            return False

        # Metal gate unlocks on melee hit instead of taking damage
        if self.gate_type == 'metal' and attack_type == 'melee':
            self.is_unlocked = True
            self.flash_timer = 0.2
            return True

        damage = {'melee': 10, 'projectile': 15, 'beam': 5}.get(attack_type, 5)
        self.health -= damage
        self.flash_timer    = 0.1
        self.shake_offset_x = (hash(self.flash_timer)     % 3) - 1
        self.shake_offset_y = (hash(self.flash_timer * 2) % 3) - 1

        if self.health <= 0:
            self.start_destruction()

        return True

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start_destruction(self):
        self.is_destroying           = True
        self.destruction_timer       = 0
        self.destruction_frame_index = 0
        self.destruction_frame_timer = 0

    def update(self, dt):
        if not self.active:
            return

        if self.flash_timer > 0:
            self.flash_timer -= dt
            if self.flash_timer <= 0:
                self.shake_offset_x = self.shake_offset_y = 0

        # Level text bobbing (skip once metal gate is unlocked)
        if not (self.gate_type == 'metal' and self.is_unlocked):
            self.float_timer += dt * 2
            self.float_offset = math.sin(self.float_timer) * 5

        if self.is_destroying and self.gate_type != 'metal':
            self.destruction_frame_timer += dt
            if self.destruction_frame_timer >= self.destruction_frame_duration:
                self.destruction_frame_timer = 0
                self.destruction_frame_index += 1
                if self.destruction_frame_index >= len(self.destruction_frames):
                    self.active = False

    # ── Drawing ───────────────────────────────────────────────────────────────

    def draw(self, screen, camera, colors):
        if not self.active:
            return

        sx = (self.x * RENDER_SCALE) - camera.x + self.shake_offset_x
        sy = (self.y * RENDER_SCALE) - camera.y + self.shake_offset_y
        sw = int(self.width  * RENDER_SCALE)
        sh = int(self.height * RENDER_SCALE)

        if self.is_destroying and self.gate_type != 'metal':
            if self.destruction_frame_index < len(self.destruction_frames):
                base = 32
                size = int(base * RENDER_SCALE)
                scaled = pygame.transform.scale(self.destruction_frames[self.destruction_frame_index], (size, size))
                screen.blit(scaled, (int(sx - size // 2), int(sy - size // 2)))
        else:
            current = (self.unlocked_sprite
                       if self.gate_type == 'metal' and self.is_unlocked and self.unlocked_sprite
                       else self.sprite)
            if current:
                scaled = pygame.transform.scale(current, (sw, sh))
                if self.flash_timer > 0:
                    flash = scaled.copy()
                    if self.gate_type == 'metal' and self.is_unlocked:
                        flash.fill((100, 255, 100, 150), special_flags=pygame.BLEND_RGBA_ADD)
                    else:
                        flash.fill((255, 255, 255, 100), special_flags=pygame.BLEND_RGBA_ADD)
                    scaled = flash
                screen.blit(scaled, (int(sx - sw // 2), int(sy - sh // 2)))

            if not (self.gate_type == 'metal' and self.is_unlocked):
                self._draw_level_requirement(screen, sx, sy, colors)

            if self.health < self.max_health and self.gate_type != 'metal':
                self._draw_health_bar(screen, sx, sy, sw, colors)

    def _draw_level_requirement(self, screen, sx, sy, colors):
        font = get_gate_font()
        text = str(self.required_level)
        fy   = sy - 4 + self.float_offset
        scale = 1.5

        outline = font.render(text, True, (0, 0, 0))
        outline = pygame.transform.scale(outline, (int(outline.get_width() * scale), int(outline.get_height() * scale)))
        screen.blit(outline, outline.get_rect(center=(int(sx + 2), int(fy + 2))))

        label = font.render(text, True, (255, 215, 0))
        label = pygame.transform.scale(label, (int(label.get_width() * scale), int(label.get_height() * scale)))
        screen.blit(label, label.get_rect(center=(int(sx), int(fy))))

    def _draw_health_bar(self, screen, sx, sy, sw, colors):
        bh = max(2, int(6 / RENDER_SCALE))
        bx = int(sx - sw // 2)
        by = int(sy - (self.height * RENDER_SCALE // 2) - int(15 / RENDER_SCALE))
        pygame.draw.rect(screen, (50, 50, 50),    (bx, by, sw, bh))
        fill_w = int(sw * (self.health / self.max_health))
        color  = (100, 255, 100) if self.health > self.max_health * 0.5 else (255, 100, 100)
        pygame.draw.rect(screen, color,           (bx, by, fill_w, bh))
        pygame.draw.rect(screen, (255, 255, 255), (bx, by, sw, bh), 1)

    def get_sort_key(self):
        return (self.draw_layer, self.y if self.y_sort else 0)


class LevelGateManager:
    """Tracks all gates per room."""

    def __init__(self):
        self.gates: dict[str, list] = {}

    def add_gate(self, room_name, gate):
        self.gates.setdefault(room_name, []).append(gate)

    def get_gates(self, room_name):
        return self.gates.get(room_name, [])

    def remove_gate(self, room_name, gate):
        if room_name in self.gates and gate in self.gates[room_name]:
            self.gates[room_name].remove(gate)

    def clear_room(self, room_name):
        self.gates[room_name] = []