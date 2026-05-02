"""
core/cutscene_actor.py

Wraps a real game entity (Player, Enemy, BossEnemy) for use inside a cutscene.

AI and input are suppressed by simply not calling the entity's normal update() —
CutsceneActor.update() drives the sprite and any active tweens instead.
The entity draws itself normally, so every sprite/costume/transform works
without any extra rendering code.
"""

import math
import pygame
from config.settings import RENDER_SCALE


class _MoveTween:
    """Linear interpolation between two world positions."""

    def __init__(self, entity, target_x, target_y, duration):
        self.entity   = entity
        self.start_x  = float(entity.x)
        self.start_y  = float(entity.y)
        self.end_x    = float(target_x)
        self.end_y    = float(target_y)
        self.duration = float(duration)
        self.elapsed  = 0.0
        self.finished = False

    def update(self, dt):
        self.elapsed += dt
        t = min(1.0, self.elapsed / self.duration)
        self.entity.x = self.start_x + (self.end_x - self.start_x) * t
        self.entity.y = self.start_y + (self.end_y - self.start_y) * t
        if t >= 1.0:
            self.finished = True


class _FlyTween:
    """Arc-path movement for flying entities.

    World x/y move linearly from start to end (same as _MoveTween).
    A purely visual vertical offset — fly_offset_y in world units — follows a
    sine curve that peaks at arc_height at the midpoint and returns to zero on
    landing. The offset is applied during draw() so the shadow stays anchored
    at the true ground position throughout the flight.
    """

    def __init__(self, entity, target_x, target_y, duration, arc_height: float = 48.0):
        self.entity       = entity
        self.start_x      = float(entity.x)
        self.start_y      = float(entity.y)
        self.end_x        = float(target_x)
        self.end_y        = float(target_y)
        self.duration     = float(duration)
        self.arc_height   = float(arc_height)
        self.elapsed      = 0.0
        self.finished     = False
        self.fly_offset_y = 0.0  # current visual lift in world units

    def update(self, dt):
        self.elapsed += dt
        t = min(1.0, self.elapsed / self.duration)

        # Move the world position linearly — the shadow follows this.
        self.entity.x = self.start_x + (self.end_x - self.start_x) * t
        self.entity.y = self.start_y + (self.end_y - self.start_y) * t

        # Sine arc gives a smooth rise and fall over the full flight.
        self.fly_offset_y = self.arc_height * math.sin(t * math.pi)

        if t >= 1.0:
            self.fly_offset_y = 0.0
            self.finished     = True


class CutsceneActor:
    """Controls a game entity during a cutscene."""

    def __init__(self, actor_id, entity):
        self.actor_id = actor_id
        self.entity   = entity
        self._tween   = None  # active _MoveTween or _FlyTween, or None

    # ── Position pass-throughs ────────────────────────────────────────────────

    @property
    def x(self):
        return self.entity.x

    @x.setter
    def x(self, value):
        self.entity.x = value

    @property
    def y(self):
        return self.entity.y

    @y.setter
    def y(self, value):
        self.entity.y = value

    @property
    def fly_offset_y(self) -> float:
        """Current visual lift in world units (0.0 when not flying)."""
        if isinstance(self._tween, _FlyTween):
            return self._tween.fly_offset_y
        return 0.0

    # ── Animation & movement API ──────────────────────────────────────────────

    def set_animation(self, state, direction='down'):
        """Set animation state and facing direction immediately."""
        self.entity.direction = direction
        if hasattr(self.entity, 'sprite') and self.entity.sprite:
            self.entity.sprite.set_animation(state, direction)
        if hasattr(self.entity, 'current_animation_state'):
            self.entity.current_animation_state = state

    def face(self, direction):
        """Change facing direction without restarting the current animation."""
        self.entity.direction = direction

    def move_to(self, target_x, target_y, duration=1.0, anim_state='walk', direction=None):
        """Tween entity to (target_x, target_y) over duration seconds.

        Auto-picks walk direction from the movement vector unless direction is
        specified explicitly. Pass duration <= 0 for an instant warp.
        """
        if duration <= 0:
            self.entity.x = float(target_x)
            self.entity.y = float(target_y)
            return

        # Derive direction from whichever axis has the larger delta.
        if direction is None:
            dx = target_x - self.entity.x
            dy = target_y - self.entity.y
            if abs(dx) >= abs(dy):
                direction = 'right' if dx >= 0 else 'left'
            else:
                direction = 'down' if dy >= 0 else 'up'

        self.set_animation(anim_state, direction)
        self._tween = _MoveTween(self.entity, target_x, target_y, duration)

    def teleport(self, x, y):
        """Instant position change with no animation change."""
        self.entity.x = float(x)
        self.entity.y = float(y)
        self._tween   = None

    @staticmethod
    def _fly_direction_from_vector(dx: float, dy: float) -> str:
        """Map a movement vector to an 8-way direction string.

        Uses the same atan2 angle logic as FlyingController._update_player_direction
        so cutscene fly directions match normal in-game flying exactly.
        """
        angle = math.degrees(math.atan2(dy, dx))
        if angle < 0:
            angle += 360

        if   angle >= 337.5 or angle < 22.5:  return 'right'
        elif 22.5  <= angle < 67.5:            return 'down_right'
        elif 67.5  <= angle < 112.5:           return 'down'
        elif 112.5 <= angle < 157.5:           return 'down_left'
        elif 157.5 <= angle < 202.5:           return 'left'
        elif 202.5 <= angle < 247.5:           return 'up_left'
        elif 247.5 <= angle < 292.5:           return 'up'
        else:                                  return 'up_right'

    def fly_to(self, target_x, target_y, duration=1.0,
               arc_height: float = 48.0, direction=None):
        """Fly entity to (target_x, target_y) over duration seconds.

        The sprite rises to arc_height world units at the midpoint and lands
        smoothly. The shadow stays on the ground the whole time. Pass
        duration <= 0 for an instant warp, or arc_height=0 for a flat glide.
        """
        if duration <= 0:
            self.entity.x = float(target_x)
            self.entity.y = float(target_y)
            return

        if direction is None:
            dx = target_x - self.entity.x
            dy = target_y - self.entity.y
            direction = self._fly_direction_from_vector(dx, dy)

        self.set_animation('flying', direction)
        self._tween = _FlyTween(self.entity, target_x, target_y, duration, arc_height)

    # ── Frame update ──────────────────────────────────────────────────────────

    def update(self, dt):
        # Keep the sprite animation ticking.
        if hasattr(self.entity, 'sprite') and self.entity.sprite:
            self.entity.sprite.update(dt)

        if not self._tween:
            return

        self._tween.update(dt)
        if not self._tween.finished:
            return

        # Tween finished — settle into idle.
        was_flying  = isinstance(self._tween, _FlyTween)
        self._tween = None

        landing_dir = self.entity.direction
        if was_flying:
            # Collapse diagonals to their nearest cardinal so idle looks natural
            # (mirrors FlyingController._complete_flight behaviour).
            if landing_dir in ('up_right', 'up_left'):
                landing_dir = 'up'
            elif landing_dir in ('down_right', 'down_left'):
                landing_dir = 'down'
            self.entity.direction = landing_dir

        self.set_animation('idle', landing_dir)

    # ── Rendering ─────────────────────────────────────────────────────────────

    # Shadow sprites are loaded once and shared across all CutsceneActor instances.
    # We keep our own cache here because cutscene actors bypass the LayerManager
    # entirely, so we can't rely on LayerManager's cache being available.
    _shadow_sprite     = None  # shadow.png    (small)
    _shadow_sprite_big = None  # shadowbig.png (big)
    _shadow_cache: dict = {}   # (shadow_w, big) -> scaled Surface

    @classmethod
    def _ensure_shadow_sprites(cls):
        """Load shadow sprites from disk exactly once; fall back to a plain ellipse."""
        if cls._shadow_sprite is not None:
            return

        # Small shadow
        try:
            cls._shadow_sprite = pygame.image.load(
                'assets/sprites/universal/shadow.png'
            ).convert_alpha()
        except Exception:
            s = pygame.Surface((32, 12), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (0, 0, 0, 80), s.get_rect())
            cls._shadow_sprite = s

        # Big shadow (used by larger entities like bosses)
        try:
            cls._shadow_sprite_big = pygame.image.load(
                'assets/sprites/universal/shadowbig.png'
            ).convert_alpha()
        except Exception:
            s = pygame.Surface((64, 20), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (0, 0, 0, 80), s.get_rect())
            cls._shadow_sprite_big = s

    @classmethod
    def _get_scaled_shadow(cls, shadow_w: int, big: bool = False):
        """Return a cached shadow surface scaled to the entity width.

        Uses the same 0.32 scale factor as LayerManager._get_scaled_shadow()
        so shadows look identical to normal gameplay.
        """
        source = cls._shadow_sprite_big if big else cls._shadow_sprite
        if source is None:
            return None

        key = (shadow_w, big)
        if key not in cls._shadow_cache:
            orig_w    = source.get_width()
            orig_h    = source.get_height()
            target_w  = max(8, int(shadow_w * RENDER_SCALE * 0.32))
            target_h  = max(4, int(orig_h * target_w / orig_w))
            cls._shadow_cache[key] = pygame.transform.scale(source, (target_w, target_h))

        return cls._shadow_cache[key]

    def _draw_shadow(self, screen, camera):
        """Draw the ground shadow, replicating LayerManager._draw_shadow() exactly.

        Entities don't draw their own shadows — that's normally handled by the
        LayerManager. Since cutscene actors bypass it, we reproduce the logic here.
        """
        obj = self.entity

        # Only shadow entity types that the LayerManager would shadow.
        type_name = type(obj).__name__
        if not any(t in type_name for t in ('Player', 'Enemy', 'BossEnemy', 'NPC')):
            return

        self._ensure_shadow_sprites()

        use_big       = getattr(obj, 'shadow_size', 'small') == 'big'
        entity_height = getattr(obj, 'height', 32)
        shadow_w      = getattr(obj, 'shadow_width', getattr(obj, 'width', 32))

        shadow_surf = self._get_scaled_shadow(shadow_w, big=use_big)
        if shadow_surf is None:
            return

        # Position formula matches LayerManager._draw_shadow() exactly.
        feet_x  = (obj.x * RENDER_SCALE) - camera.x + 0.7
        feet_y  = (obj.y * RENDER_SCALE) - camera.y + (entity_height * RENDER_SCALE) // 2.25
        feet_y += getattr(obj, 'shadow_y_offset', 0)

        sx = int(feet_x - shadow_surf.get_width()  // 2)
        sy = int(feet_y - shadow_surf.get_height() // 2)
        screen.blit(shadow_surf, (sx, sy))

    # HUD helpers to suppress during a cutscene draw call.
    # Without this, health bars, aggro rings, name tags, etc. would bleed into
    # the cinematic view. Add new names here if new indicator helpers are added
    # to any entity class.
    _INDICATOR_METHODS = (
        '_draw_health_bar', 'draw_health_bar',
        '_draw_indicator',  'draw_indicator',
        '_draw_aggro',      'draw_aggro',
        '_draw_name_tag',   'draw_name_tag',
        '_draw_hud',        'draw_hud',
        '_draw_status_bar', 'draw_status_bar',
    )

    def draw(self, screen, camera, colors):
        """Draw the wrapped entity with cutscene-safe rendering.

        Steps:
          1. Draw the shadow at the true ground position (before any fly offset).
          2. Shift entity.y upward by the current arc lift so the sprite floats.
          3. Temporarily replace known HUD helpers with no-ops so game-state
             indicators (health bars, aggro rings, etc.) don't bleed into the scene.
          4. Call entity.draw() with in_cutscene=True so entities can branch on
             that flag themselves if needed.
          5. Restore entity.y and all suppressed methods.
        """
        entity = self.entity

        # 1. Shadow always stays at the real ground position, even during flight.
        self._draw_shadow(screen, camera)

        # 2. Lift the sprite for the fly arc (shadow above is already at ground).
        fly_off = self.fly_offset_y
        if fly_off:
            entity.y -= fly_off

        # 3. Swap out HUD helpers with no-ops for the duration of this draw call.
        _noop = lambda *a, **kw: None  # noqa: E731
        _restored: dict = {}
        for name in self._INDICATOR_METHODS:
            if name in entity.__dict__:
                # Instance-level override — save and replace.
                _restored[name] = ('instance', entity.__dict__[name])
                entity.__dict__[name] = _noop
            elif getattr(type(entity), name, None) is not None:
                # Class-level method — shadow it on the instance.
                _restored[name] = ('class', None)
                setattr(entity, name, _noop)

        # 4. Draw the entity itself.
        if hasattr(entity, 'draw'):
            entity.in_cutscene = True
            try:
                entity.draw(screen, camera, colors)
            finally:
                entity.in_cutscene = False

        # 5. Restore fly offset and all suppressed methods.
        if fly_off:
            entity.y += fly_off

        for name, (origin, original) in _restored.items():
            if origin == 'instance':
                entity.__dict__[name] = original
            else:
                # Remove the instance shadow so the class method is visible again.
                entity.__dict__.pop(name, None)