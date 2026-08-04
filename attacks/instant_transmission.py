import pygame
from config.settings import RENDER_SCALE as _RENDER_SCALE


class _PingPongAnimation:
    """A small looping frame animation that bounces back and forth through
    its frames instead of restarting from frame 0 — e.g. for a 3-frame
    strip: 1, 2, 3, 2, 1, 2, 3, 2, 1, ... This matches how the
    target_selector / target_selected art is meant to play (source frames
    are 32x32, laid out left-to-right in a single row).
    """

    FRAME_SIZE = 32       # source frames are 32x32
    FRAME_DURATION = 0.1  # seconds per frame — tune to taste

    def __init__(self, path, scale=_RENDER_SCALE):
        self.frames = []
        self.frame_index = 0
        self.frame_timer = 0.0
        self._step = 1  # +1 advancing forward, -1 bouncing back
        self._load(path, scale)

    def _load(self, path, scale):
        try:
            sheet = pygame.image.load(path).convert_alpha()
            num_frames = max(1, sheet.get_width() // self.FRAME_SIZE)
            raw_frames = [
                sheet.subsurface(pygame.Rect(i * self.FRAME_SIZE, 0, self.FRAME_SIZE, self.FRAME_SIZE))
                for i in range(num_frames)
            ]
            w = int(self.FRAME_SIZE * scale)
            h = int(self.FRAME_SIZE * scale)
            self.frames = [pygame.transform.scale(f, (w, h)) for f in raw_frames]
        except Exception as e:
            print(f"Error loading instant transmission animation '{path}': {e}")
            self.frames = []

    def update(self, dt):
        if len(self.frames) <= 1:
            return
        self.frame_timer += dt
        if self.frame_timer >= self.FRAME_DURATION:
            self.frame_timer -= self.FRAME_DURATION
            self.frame_index += self._step
            # Bounce at either end instead of wrapping around —
            # 0,1,2,1,0,1,2,1,0,... (i.e. 1,2,3,2,1,2,3,2,1,... 1-indexed).
            if self.frame_index >= len(self.frames) - 1:
                self.frame_index = len(self.frames) - 1
                self._step = -1
            elif self.frame_index <= 0:
                self.frame_index = 0
                self._step = 1

    @property
    def current_frame(self):
        """The current frame Surface, or None if loading failed (caller
        should fall back to a drawn placeholder)."""
        if not self.frames:
            return None
        return self.frames[self.frame_index]

    def get_size(self):
        if self.frames:
            f = self.frames[0]
            return f.get_width(), f.get_height()
        return int(self.FRAME_SIZE * _RENDER_SCALE), int(self.FRAME_SIZE * _RENDER_SCALE)


class InstantTransmissionSelector:
    """Manages the on-screen target_selector cursor and the set of enemies
    marked while charging Instant Transmission.

    The cursor lives in SCREEN space (not world space) — it's meant to be
    aimed freely across the visible screen while everything is frozen,
    independent of the camera. See Player.start_targeting_instant_transmission
    (charging) and Game._update_instant_transmission (cursor movement +
    hover-select, driven every frame while targeting).
    """

    CURSOR_SPEED = 1000  # screen px/sec

    def __init__(self, screen_width, screen_height, scale=_RENDER_SCALE):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.scale = scale

        # Start centered on screen.
        self.x = screen_width / 2
        self.y = screen_height / 2

        self.selected_enemies = []  # ordered list of enemy refs, pick order preserved

        self.cursor_anim = _PingPongAnimation(
            'assets/sprites/attacks/instant_transmission/target_selector.png', scale
        )
        self.marker_anim = _PingPongAnimation(
            'assets/sprites/attacks/instant_transmission/target_selected.png', scale
        )

        # One independent marker animation state per selected enemy, so
        # each marker bounces on its own timing rather than all in lockstep
        # (keyed by id(enemy) since enemies themselves aren't hashable-safe
        # to assume unique across frames otherwise).
        self._marker_anims = {}

    def update(self, dt):
        """Advance the cursor's own animation plus every active marker's
        animation. Call this once per frame while targeting."""
        self.cursor_anim.update(dt)
        for anim in self._marker_anims.values():
            anim.update(dt)

    def _cursor_size(self):
        return self.cursor_anim.get_size()

    def move(self, dx, dy, dt):
        """Move the cursor by a normalized direction (dx, dy each in
        -1..1) at CURSOR_SPEED px/sec, clamped so the cursor's center
        never leaves [0, screen_width] x [0, screen_height] — which
        works out to at most half the cursor's own size being able to
        stick out past any edge, regardless of the cursor's actual size.
        """
        self.x += dx * self.CURSOR_SPEED * dt
        self.y += dy * self.CURSOR_SPEED * dt
        self.x = max(0, min(self.screen_width, self.x))
        self.y = max(0, min(self.screen_height, self.y))

    def get_cursor_rect(self):
        w, h = self._cursor_size()
        return pygame.Rect(int(self.x - w / 2), int(self.y - h / 2), w, h)

    def try_select(self, enemy, enemy_screen_rect):
        """Mark `enemy` as selected if the cursor overlaps its on-screen
        rect and it isn't already selected (each enemy can only be picked
        once per charge). Returns True if it was newly selected."""
        if enemy in self.selected_enemies:
            return False
        if self.get_cursor_rect().colliderect(enemy_screen_rect):
            self.selected_enemies.append(enemy)
            self._marker_anims[id(enemy)] = _PingPongAnimation(
                'assets/sprites/attacks/instant_transmission/target_selected.png', self.scale
            )
            return True
        return False

    def draw_cursor(self, screen):
        frame = self.cursor_anim.current_frame
        if frame is not None:
            rect = frame.get_rect(center=(int(self.x), int(self.y)))
            screen.blit(frame, rect)
        else:
            # Fallback: simple crosshair reticle so targeting still works
            # visually even before target_selector.png is added.
            size = 12
            color = (255, 230, 120)
            cx, cy = int(self.x), int(self.y)
            pygame.draw.circle(screen, color, (cx, cy), size, 2)
            pygame.draw.line(screen, color, (cx - size - 4, cy), (cx - size + 2, cy), 2)
            pygame.draw.line(screen, color, (cx + size - 2, cy), (cx + size + 4, cy), 2)
            pygame.draw.line(screen, color, (cx, cy - size - 4), (cx, cy - size + 2), 2)
            pygame.draw.line(screen, color, (cx, cy + size - 2), (cx, cy + size + 4), 2)

    def draw_markers(self, screen, camera):
        """Draw the target_selected marker over every enemy currently
        selected, layered in front of it. Call this after enemies have
        already been drawn for the frame."""
        from config.settings import RENDER_SCALE
        for enemy in self.selected_enemies:
            if not getattr(enemy, 'active', True):
                continue
            screen_x = (enemy.x * RENDER_SCALE) - camera.x
            screen_y = (enemy.y * RENDER_SCALE) - camera.y
            anim = self._marker_anims.get(id(enemy))
            frame = anim.current_frame if anim is not None else None
            if frame is not None:
                rect = frame.get_rect(center=(int(screen_x), int(screen_y)))
                screen.blit(frame, rect)
            else:
                pygame.draw.circle(screen, (255, 60, 60), (int(screen_x), int(screen_y)), 14, 3)


class InstantTransmissionStrike:
    """A synthetic, always-lands 'attack' representing the teleport strike
    itself. The player has already teleported directly on top of the
    target by the time this is used, so it's spawned exactly centered on
    the enemy with a generous hitbox — this hit is meant to always land,
    not to represent a real thrown/swung attack.

    NOTE: Enemy.check_collision_with_attack needs an 'instant_transmission'
    branch to actually deal damage with this — the same way destructible
    stones needed a 'genkidama' branch added. Without that branch this
    will simply never register a hit (fails safe, no crash).
    """

    def __init__(self, x, y, size=64, damage=35):
        self.x = x
        self.y = y
        self.size = size
        self.damage = damage
        self.active = True

    def get_rect(self):
        return pygame.Rect(self.x - self.size // 2, self.y - self.size // 2, self.size, self.size)