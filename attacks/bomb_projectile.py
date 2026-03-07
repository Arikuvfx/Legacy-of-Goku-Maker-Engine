import pygame
import math
import random
from config.settings import RENDER_SCALE
from core.draw_layers import DrawLayer


class BombProjectile:
    """
    Parabolic bomb projectile spawned by Shooter enemies.

    Lifecycle:
      1. STATE_FLYING  — travels in an arc from spawn point to target position.
      2. STATE_LANDED  — sits on the ground for a short fuse delay.
      3. STATE_EXPLODED — detonates, spawning an ExplosionEffect and dealing AoE damage.
    """

    STATE_FLYING   = 'flying'
    STATE_LANDED   = 'landed'
    STATE_EXPLODED = 'exploded'

    EXPLOSION_RADIUS = 35  # World-unit radius for area-of-effect damage.

    def __init__(self, start_x, start_y, target_x, target_y, damage=15, flight_time=1.0, player=None):
        self.start_x = start_x
        self.start_y = start_y
        self.target_x = target_x
        self.target_y = target_y

        self.x = start_x
        self.y = start_y

        # Visual dimensions
        self.width  = 16
        self.height = 16
        self.radius = 8

        # Combat
        self.damage          = damage
        self.active          = True
        self.has_hit         = False
        self.pending_explosion = None  # Set to an ExplosionEffect instance on detonation.

        # State machine
        self.state = self.STATE_FLYING

        # Flight parameters
        self.flight_time          = flight_time
        self.elapsed_time         = 0.0
        self.arc_height           = 80
        self.horizontal_distance  = target_x - start_x
        self.vertical_distance    = target_y - start_y

        # Fuse timer — how long the bomb rests on the ground before exploding.
        self.fuse_duration = random.uniform(0.5, 0.5)
        self.fuse_timer    = 0.0

        # Player reference kept so detonation always has a valid target.
        # Refreshed every update() call.
        self._fuse_player = player

        # Depth sorting — participates in the y-sort pass so it renders
        # correctly in front of / behind the player and enemies.
        self.draw_layer = DrawLayer.PLAYER
        self.y_sort     = True

        # Sprite / animation
        self.spritesheet     = None
        self.frame_width     = 16
        self.frame_height    = 16
        self.current_frame   = 0
        self.animation_timer = 0
        self.frame_duration  = 0.1
        self.total_frames    = 4
        self._load_bomb_sprite()

    # ── Sprite loading ─────────────────────────────────────────────────────────

    def get_sort_key(self):
        """
        Sort key for the depth/y-sort pass.
        Uses the bottom edge of the sprite so depth switches when the
        bomb's base crosses the player's feet rather than its centre.
        """
        return (self.draw_layer, self.y + self.height // 2)

    def _load_bomb_sprite(self):
        """Load the bomb spritesheet from disk; falls back to a primitive shape on failure."""
        try:
            path = 'assets/sprites/enemies/shooter/bomb.png'
            self.spritesheet = pygame.image.load(path).convert_alpha()
            self.total_frames = max(1, self.spritesheet.get_width() // self.frame_width)
        except (pygame.error, FileNotFoundError):
            self.spritesheet = None

    # ── Update ─────────────────────────────────────────────────────────────────

    def update(self, dt, player=None):
        """
        Advance the bomb one simulation step.

        Args:
            dt:     Delta time in seconds.
            player: Optional player reference — refreshes the internal
                    reference so the detonation handler stays current.
        """
        if not self.active:
            return

        if player is not None:
            self._fuse_player = player

        # Advance the sprite animation every frame regardless of state.
        if self.spritesheet:
            self.animation_timer += dt
            if self.animation_timer >= self.frame_duration:
                self.animation_timer = 0
                self.current_frame = (self.current_frame + 1) % self.total_frames

        if self.state == self.STATE_FLYING:
            self._update_flying(dt)
        elif self.state == self.STATE_LANDED:
            self._update_fuse(dt)

    def _update_flying(self, dt):
        """
        Move the bomb along its parabolic arc.
        Transitions to STATE_LANDED once the arc is complete.
        """
        self.elapsed_time += dt
        progress = min(self.elapsed_time / self.flight_time, 1.0)

        # Horizontal position interpolates linearly; vertical gets the arc offset.
        self.x = self.start_x + self.horizontal_distance * progress
        linear_y    = self.start_y + self.vertical_distance * progress
        arc_offset  = self.arc_height * 4 * progress * (1.0 - progress)
        self.y      = linear_y - arc_offset

        if progress >= 1.0:
            self.x = self.target_x
            self.y = self.target_y
            self.state      = self.STATE_LANDED
            self.fuse_timer = 0.0

    def _update_fuse(self, dt):
        """Count down the fuse timer and detonate when it expires."""
        self.fuse_timer += dt
        if self.fuse_timer >= self.fuse_duration:
            self._detonate()

    def _detonate(self):
        """
        Trigger the explosion: spawn the visual effect and immediately
        deal area damage to the player.
        """
        self.state            = self.STATE_EXPLODED
        self.active           = False
        self.has_hit          = True
        self.pending_explosion = ExplosionEffect(self.x, self.y)

        if self._fuse_player is not None:
            self.check_explosion_damage(self._fuse_player)

    # ── Damage ─────────────────────────────────────────────────────────────────

    def check_explosion_damage(self, player):
        """
        Deal damage to *player* if they are within the explosion radius.
        Bypasses the standard bullet-hit invulnerability window so the
        explosion always registers.

        Returns:
            True if the player was within range and took damage.
        """
        dx = player.x - self.x
        dy = player.y - self.y
        distance = math.sqrt(dx * dx + dy * dy)

        if distance < self.EXPLOSION_RADIUS:
            player.take_damage(self.damage, 0, 0, ignore_invulnerability=True, no_knockback=True)
            player.hurt_tint = 1.0
            return True

        return False

    def check_collision_with_player(self, player):
        """
        Legacy stub — damage is now handled exclusively through
        check_explosion_damage() after detonation.
        """
        return False

    # ── Draw ───────────────────────────────────────────────────────────────────

    def draw(self, screen, camera, colors=None):
        """Dispatch to the appropriate draw routine for the current state."""
        if self.state == self.STATE_FLYING:
            self._draw_flying(screen, camera)
        elif self.state == self.STATE_LANDED:
            self._draw_landed(screen, camera)

    def _draw_flying(self, screen, camera):
        """Render the animated bomb sprite (or fallback circle) while in flight."""
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.spritesheet:
            frame_x      = self.current_frame * self.frame_width
            frame_surface = self.spritesheet.subsurface(
                pygame.Rect(frame_x, 0, self.frame_width, self.frame_height))
            scaled = pygame.transform.scale(
                frame_surface,
                (int(self.width * RENDER_SCALE), int(self.height * RENDER_SCALE))
            )
            screen.blit(scaled, (
                int(screen_x - (self.width  * RENDER_SCALE) // 2),
                int(screen_y - (self.height * RENDER_SCALE) // 2)
            ))
        else:
            pygame.draw.circle(screen, (30, 30, 30),
                               (int(screen_x), int(screen_y)),
                               int(self.radius * RENDER_SCALE))
            pygame.draw.circle(screen, (255, 100, 50),
                               (int(screen_x), int(screen_y - 4 * RENDER_SCALE)),
                               int(2 * RENDER_SCALE))

    def _draw_landed(self, screen, camera):
        """Render the bomb sitting stationary on the ground."""
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.spritesheet:
            frame_surface = self.spritesheet.subsurface(
                pygame.Rect(self.current_frame * self.frame_width, 0,
                            self.frame_width, self.frame_height))
            scaled = pygame.transform.scale(
                frame_surface,
                (int(self.width * RENDER_SCALE), int(self.height * RENDER_SCALE))
            )
            screen.blit(scaled, (
                int(screen_x - (self.width  * RENDER_SCALE) // 2),
                int(screen_y - (self.height * RENDER_SCALE) // 2)
            ))
        else:
            pygame.draw.circle(screen, (30, 30, 30),
                               (int(screen_x), int(screen_y)),
                               int(self.radius * RENDER_SCALE))


class ExplosionEffect:
    """
    Purely visual explosion that plays the bomb_explosion spritesheet
    and then marks itself inactive once the animation is complete.
    """

    SPRITE_PATH  = 'assets/sprites/enemies/shooter/bomb_explosion.png'
    FRAME_WIDTH  = 32
    FRAME_HEIGHT = 32

    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.active = True

        self.draw_layer = DrawLayer.PLAYER
        self.y_sort     = True

        # Sprite animation
        self.spritesheet     = None
        self.total_frames    = 1
        self.current_frame   = 0
        self.frame_duration  = 0.06
        self.animation_timer = 0.0
        self._load_sprite()

        # Fallback circle animation used when the sprite cannot be loaded
        self.max_radius         = 30
        self._fallback_duration = 0.3
        self._fallback_timer    = self._fallback_duration

    def get_sort_key(self):
        """Sort key for the depth/y-sort pass."""
        return (self.draw_layer, self.y)

    def _load_sprite(self):
        """Load the explosion spritesheet; falls back to an expanding circle on failure."""
        try:
            sheet            = pygame.image.load(self.SPRITE_PATH).convert_alpha()
            self.spritesheet = sheet
            self.total_frames = max(1, sheet.get_width() // self.FRAME_WIDTH)
        except (pygame.error, FileNotFoundError):
            self.spritesheet = None

    def update(self, dt):
        """
        Advance the explosion animation.
        Sets active=False once the final frame (or fallback timer) is reached.

        Args:
            dt: Delta time in seconds.
        """
        if not self.active:
            return

        if self.spritesheet:
            self.animation_timer += dt
            if self.animation_timer >= self.frame_duration:
                self.animation_timer = 0
                self.current_frame += 1
                if self.current_frame >= self.total_frames:
                    self.active = False
        else:
            self._fallback_timer -= dt
            if self._fallback_timer <= 0:
                self.active = False

    def draw(self, screen, camera, colors=None):
        """
        Render the explosion at its world position.

        Args:
            screen: Pygame surface to draw onto.
            camera: Active Camera instance for world-to-screen conversion.
            colors: Unused; kept for interface consistency.
        """
        if not self.active:
            return

        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.spritesheet:
            frame_surface = self.spritesheet.subsurface(
                pygame.Rect(self.current_frame * self.FRAME_WIDTH, 0,
                            self.FRAME_WIDTH, self.FRAME_HEIGHT))
            scaled_w = int(self.FRAME_WIDTH  * RENDER_SCALE)
            scaled_h = int(self.FRAME_HEIGHT * RENDER_SCALE)
            scaled   = pygame.transform.scale(frame_surface, (scaled_w, scaled_h))
            screen.blit(scaled, (int(screen_x - scaled_w // 2), int(screen_y - scaled_h // 2)))
        else:
            progress = 1.0 - (self._fallback_timer / self._fallback_duration)
            radius   = int(self.max_radius * progress * RENDER_SCALE)
            pygame.draw.circle(screen, (255, 150, 50), (int(screen_x), int(screen_y)), radius, 3)
            if radius > 5:
                pygame.draw.circle(screen, (255, 255, 100), (int(screen_x), int(screen_y)), radius // 2, 2)