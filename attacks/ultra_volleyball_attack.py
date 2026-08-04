import pygame
from config.settings import RENDER_SCALE as _RENDER_SCALE
from core.draw_layers import get_beam_layer
from attacks.beam import BeamAttack


class UltraVolleyballAttack:
    """A fixed-length, 3-segment projectile that travels as a single rigid
    unit away from the player, unlike BeamAttack (which grows/stays
    anchored to the player) or FlameKamehamehaAttack (which is anchored
    and whip-steered in place).

    Structure, chained front-to-back along the travel direction (closest
    to the direction of travel first):
      - segment 1 ("end"):   the leading tip — this is what "flies".
      - segment 2 ("middle"): trails immediately behind the tip.
      - segment 3 ("decay"):  trails behind the middle segment — the tail.

    All three segments move together at travel_speed for up to
    travel_distance world px, then the whole thing despawns if it never
    hit anything. On enemy contact, this attack does NOT push or damage —
    enemy.py's 'ultra_volleyball' branch of check_collision_with_attack
    encases the enemy instead (see Enemy.encase()), and game.py deactivates
    this attack the same frame (mirroring how a regular projectile is
    deactivated on hit — see game.py's projectile.active = False pattern).

    Sprite sheets are drawn once facing 'right' and rotated per-direction,
    the same convention FlameKamehamehaAttack uses (see its class
    docstring) — there's no per-direction row like BeamAttack's sheets.
    """

    def __init__(self, x, y, direction, scale=_RENDER_SCALE, attack_name='ultra_volleyball_attack',
                 travel_speed=220, travel_distance=220,
                 end_frame_width=16, end_frame_height=16,
                 middle_frame_width=16, middle_frame_height=16,
                 decay_frame_width=16, decay_frame_height=16):
        self.origin_x = x
        self.origin_y = y
        self.x = x  # leading tip position — this is what moves each frame
        self.y = y
        self.direction = direction
        self.scale = scale
        self.attack_name = attack_name
        self.active = True
        self.y_sort = False

        self.end_frame_width = end_frame_width
        self.end_frame_height = end_frame_height
        self.middle_frame_width = middle_frame_width
        self.middle_frame_height = middle_frame_height
        self.decay_frame_width = decay_frame_width
        self.decay_frame_height = decay_frame_height

        # Degrees to rotate the right-facing sheet frames by, per direction
        # (pygame.transform.rotate is counter-clockwise for positive
        # angles) — same table as FlameKamehamehaAttack.
        self.direction_to_angle = {'right': 0, 'up': 90, 'left': 180, 'down': 270}

        self.travel_speed = travel_speed      # world px/sec
        self.travel_distance = travel_distance  # fixed total reach, world px
        self.traveled = 0.0

        # Sprite-sheet frame playback (per-segment animation), separate
        # from the travel distance above.
        self.current_frame = 0
        self.frame_timer = 0.0
        self.frame_duration = 0.08

        self.end_sprite = None
        self.middle_sprite = None
        self.decay_sprite = None

        self.end_sprite_scaled = None
        self.middle_sprite_scaled = None
        self.decay_sprite_scaled = None

        self.end_width_scaled = 0
        self.end_height_scaled = 0
        self.middle_width_scaled = 0
        self.middle_height_scaled = 0
        self.decay_width_scaled = 0
        self.decay_height_scaled = 0

        self.load_sprites()
        self.calculate_scaled_dimensions()

        self.draw_layer = get_beam_layer(self.direction, self.direction)

    def get_sort_key(self):
        return (self.draw_layer, self.y)

    def _rotate_frames(self, frames):
        """Rotate right-facing frames to match self.direction — see
        FlameKamehamehaAttack._rotate_frames, same idea."""
        angle = self.direction_to_angle.get(self.direction, 0)
        if angle == 0 or not frames:
            return frames
        return [pygame.transform.rotate(frame, angle) for frame in frames]

    def _load_sheet(self, part_name, frame_width, frame_height):
        """Load a single-row, right-facing spritesheet for one segment,
        trim it, and rotate it to match self.direction. Returns None
        (rather than raising) if the sheet is missing, so a not-yet-drawn
        segment just doesn't render instead of crashing the attack."""
        try:
            sheet = pygame.image.load(
                f'assets/sprites/attacks/{self.attack_name}/{part_name}_{self.attack_name}.png'
            ).convert_alpha()
            frames_per_row = sheet.get_width() // frame_width
            frames = []
            for i in range(frames_per_row):
                x = i * frame_width
                frames.append(sheet.subsurface(pygame.Rect(x, 0, frame_width, frame_height)))
            frames = self._rotate_frames(BeamAttack._trim_frames(frames))
            print(f"Loaded {len(frames)} {part_name} sprites for ultra volleyball ({self.direction})")
            return frames
        except Exception as e:
            print(f"Error loading ultra volleyball {part_name} sprite: {e}")
            return None

    def load_sprites(self):
        self.end_sprite = self._load_sheet('end', self.end_frame_width, self.end_frame_height)
        self.middle_sprite = self._load_sheet('middle', self.middle_frame_width, self.middle_frame_height)
        self.decay_sprite = self._load_sheet('decay', self.decay_frame_width, self.decay_frame_height)

        self.use_sprites = any([self.end_sprite, self.middle_sprite, self.decay_sprite])
        if not self.use_sprites:
            print("No ultra volleyball sprites loaded")

    def calculate_scaled_dimensions(self):
        # Rotating 90/270 degrees swaps width/height, same as
        # FlameKamehamehaAttack.calculate_scaled_dimensions — fallback
        # dimensions need to swap too when no sheet loaded for that angle.
        angle = self.direction_to_angle.get(self.direction, 0)
        swapped = angle in (90, 270)

        def scale_list(frames, fallback_w, fallback_h):
            if not frames:
                if swapped:
                    fallback_w, fallback_h = fallback_h, fallback_w
                return None, int(fallback_w * self.scale), int(fallback_h * self.scale)
            rect = frames[0].get_rect()
            w = int(rect.width * self.scale)
            h = int(rect.height * self.scale)
            scaled = [pygame.transform.scale(f, (w, h)) for f in frames]
            return scaled, w, h

        self.end_sprite_scaled, self.end_width_scaled, self.end_height_scaled = \
            scale_list(self.end_sprite, self.end_frame_width, self.end_frame_height)
        self.middle_sprite_scaled, self.middle_width_scaled, self.middle_height_scaled = \
            scale_list(self.middle_sprite, self.middle_frame_width, self.middle_frame_height)
        self.decay_sprite_scaled, self.decay_width_scaled, self.decay_height_scaled = \
            scale_list(self.decay_sprite, self.decay_frame_width, self.decay_frame_height)

    def update(self, dt):
        if not self.active:
            return

        if self.use_sprites:
            self.frame_timer += dt
            if self.frame_timer >= self.frame_duration:
                self.frame_timer -= self.frame_duration
                self.current_frame += 1

        step = self.travel_speed * dt
        remaining = self.travel_distance - self.traveled
        step = min(step, max(remaining, 0.0))
        self.traveled += step

        dx, dy = {
            'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0),
        }.get(self.direction, (0, 0))
        self.x = self.origin_x + dx * self.traveled
        self.y = self.origin_y + dy * self.traveled

        # Reached full fixed reach without hitting anything — despawn.
        if self.traveled >= self.travel_distance:
            self.active = False

    def get_world_bounds(self):
        """World-space pygame.Rect enclosing all three chained segments at
        their current travelled position — used by enemy.py's
        'ultra_volleyball' collision branch, same contract as
        FlameKamehamehaAttack.get_world_bounds()."""
        def world_dims(w_scaled, h_scaled):
            return w_scaled / self.scale, h_scaled / self.scale

        end_w, end_h = world_dims(self.end_width_scaled, self.end_height_scaled)
        middle_w, middle_h = world_dims(self.middle_width_scaled, self.middle_height_scaled)
        decay_w, decay_h = world_dims(self.decay_width_scaled, self.decay_height_scaled)

        # Chain order: end (leading tip, at self.x/self.y) first, then
        # middle and decay trailing BEHIND it — i.e. against the travel
        # direction, the mirror image of how flame_kamehameha's stationary
        # chain extends forward from its anchor.
        segments = [(end_w, end_h), (middle_w, middle_h), (decay_w, decay_h)]

        segment_rects = []
        if self.direction in ('down', 'up'):
            going_down = self.direction == 'down'
            sign = -1 if going_down else 1  # trailing segments sit behind (opposite of travel)
            pos = self.y
            for w, h in segments:
                top = pos if sign > 0 else pos - h
                segment_rects.append(pygame.Rect(self.x - w / 2, top, w, h))
                pos += sign * h
        else:
            going_right = self.direction == 'right'
            sign = -1 if going_right else 1
            pos = self.x
            for w, h in segments:
                left = pos if sign > 0 else pos - w
                segment_rects.append(pygame.Rect(left, self.y - h / 2, w, h))
                pos += sign * w

        bounds = segment_rects[0]
        for rect in segment_rects[1:]:
            bounds = bounds.union(rect)
        return bounds

    def draw(self, screen, camera, colors=None):
        if not self.active or not self.use_sprites:
            return

        from config.settings import RENDER_SCALE
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.direction in ('down', 'up'):
            self._draw_vertical(screen, screen_x, screen_y)
        else:
            self._draw_horizontal(screen, screen_x, screen_y)

    def _draw_vertical(self, screen, screen_x, screen_y):
        going_down = self.direction == 'down'
        sign = -1 if going_down else 1
        frame_index = self.current_frame

        pos = screen_y
        for sprite_scaled, height_scaled in (
            (self.end_sprite_scaled, self.end_height_scaled),
            (self.middle_sprite_scaled, self.middle_height_scaled),
            (self.decay_sprite_scaled, self.decay_height_scaled),
        ):
            if sprite_scaled:
                frame = sprite_scaled[frame_index % len(sprite_scaled)]
                anchor = 'midbottom' if sign > 0 else 'midtop'
                rect = frame.get_rect(**{anchor: (screen_x, pos)})
                screen.blit(frame, rect)
            pos += sign * height_scaled

    def _draw_horizontal(self, screen, screen_x, screen_y):
        going_right = self.direction == 'right'
        sign = -1 if going_right else 1
        frame_index = self.current_frame

        pos = screen_x
        for sprite_scaled, width_scaled in (
            (self.end_sprite_scaled, self.end_width_scaled),
            (self.middle_sprite_scaled, self.middle_width_scaled),
            (self.decay_sprite_scaled, self.decay_width_scaled),
        ):
            if sprite_scaled:
                frame = sprite_scaled[frame_index % len(sprite_scaled)]
                anchor = 'midright' if sign > 0 else 'midleft'
                rect = frame.get_rect(**{anchor: (pos, screen_y)})
                screen.blit(frame, rect)
            pos += sign * width_scaled