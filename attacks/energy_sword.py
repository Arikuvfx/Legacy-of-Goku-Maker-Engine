import math
import pygame
from config.settings import RENDER_SCALE
from core.draw_layers import DrawLayer


class EnergySwordChargeEffect:
    """The charge-up glow shown while the player holds Q drawing the sword,
    before it switches over to the spin.

    Same shape as KamehamehaChargeEffect in beam.py: centered on the
    player's body, drawn in front for down/left/right and behind for up,
    and its frame_duration is derived from target_charge_duration so the
    whole build-up-then-pulse sequence always takes the same amount of
    time no matter how many frames the sheet has.

    charging_{attack_name}.png is assumed to be a single row of
    frame_width x frame_height frames — there's no per-direction art for
    the charge-up itself (only the spin, afterwards, needs that).

    attack_name/frame_width/frame_height/target_charge_duration/
    pulse_steps/direction_offsets are all new, optional kwargs added so
    this can be driven from data (see attacks/attack_config.py's "sword"
    archetype and dev_tools/attack_creator.py) — same additive convention
    burning_attack.py's BurningChargeEffect already uses. Every one
    defaults to exactly the value that used to be hardcoded, so an
    existing `EnergySwordChargeEffect(player)` call site behaves
    identically to before.
    """

    def __init__(self, player, scale=RENDER_SCALE, facing=None, attack_name='energy_sword',
                 frame_width=24, frame_height=27, target_charge_duration=3.0,
                 pulse_steps=2, direction_offsets=None):
        self.player = player
        # true_direction drives layering (still tucked behind the player
        # when they're actually facing up); direction drives which offset
        # is used for positioning and can be overridden via `facing` when
        # a direction has no dedicated pose yet (see player.py).
        self.true_direction = player.direction
        self.direction = facing if facing is not None else player.direction
        self.scale = scale
        self.active = True
        self.y_sort = False

        self.attack_name = attack_name
        self.sprite_path = f'assets/sprites/attacks/{self.attack_name}/charging_{self.attack_name}.png'

        self.frame_width = frame_width
        self.frame_height = frame_height

        # Charging the sword should feel deliberate — a few seconds of
        # building energy before the spin kicks off.
        self.target_charge_duration = target_charge_duration
        # Extra time (in frame_duration units) held on the final frame once
        # the run-up finishes, rather than advancing to a new frame — pads
        # out the charge duration without needing more source art.
        self.pulse_steps = pulse_steps

        self.frame_duration = 0.1  # placeholder; recalculated in _load_sprite()

        # Per-direction fine-tuning, in world units (pre-scale). Tweak
        # these once real art is in so the glow lines up with the hand.
        # None (the default) keeps the original hardcoded tuning.
        self.direction_offsets = direction_offsets if direction_offsets is not None else {
            'down':  (-20, 5),
            'left':  (-20, 5),
            'right': (20, 5),
            'up':    (-20, 5),
        }

        self.frame_timer = 0.0
        self.tick = 0

        self.frames_scaled = []
        self.frames_scaled_flipped = []
        self.frame_w_scaled = 0
        self.frame_h_scaled = 0
        self._load_sprite()

        self.draw_layer = DrawLayer.EFFECTS_BEHIND if self.true_direction == 'up' else DrawLayer.EFFECTS_FRONT

    def _load_sprite(self):
        try:
            sheet = pygame.image.load(self.sprite_path).convert_alpha()
            frames_per_row = sheet.get_width() // self.frame_width

            raw_frames = [
                sheet.subsurface(pygame.Rect(i * self.frame_width, 0, self.frame_width, self.frame_height))
                for i in range(frames_per_row)
            ]

            if raw_frames:
                rect = raw_frames[0].get_rect()
                self.frame_w_scaled = int(rect.width * self.scale)
                self.frame_h_scaled = int(rect.height * self.scale)
                self.frames_scaled = [
                    pygame.transform.scale(f, (self.frame_w_scaled, self.frame_h_scaled))
                    for f in raw_frames
                ]
                # The art is drawn facing left; mirror it once here so
                # 'right' facing doesn't just reposition the glow but
                # actually flips it to match the (auto-mirrored) body pose.
                self.frames_scaled_flipped = [
                    pygame.transform.flip(f, True, False) for f in self.frames_scaled
                ]
        except (pygame.error, FileNotFoundError) as e:
            print(f"Error loading charging energy sword sprite: {e}")
            self.frames_scaled = []
            self.frames_scaled_flipped = []

        count = len(self.frames_scaled)
        total_steps = max(count, 1) + (self.pulse_steps if count > 1 else 0)
        self.frame_duration = self.target_charge_duration / total_steps

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def _current_frame_index(self):
        count = len(self.frames_scaled)
        if count <= 1:
            return 0
        if self.tick < count:
            return self.tick
        # Run-up finished — hold on the last frame rather than flickering
        # back and forth between it and the previous one.
        return count - 1

    def get_total_duration(self):
        """Time (seconds) for the full run-up-then-hold sequence — used by
        player.py to size sword_charge_required so the spin only starts
        once this animation has actually finished playing."""
        count = len(self.frames_scaled)
        if count <= 1:
            return self.frame_duration
        return (count + self.pulse_steps) * self.frame_duration

    def update(self, dt):
        if not self.frames_scaled:
            return
        self.frame_timer += dt
        if self.frame_timer >= self.frame_duration:
            self.frame_timer -= self.frame_duration
            self.tick += 1

    def draw(self, screen, camera, colors=None):
        if not self.active or not self.frames_scaled:
            return

        offset_x, offset_y = self.direction_offsets.get(self.direction, (0, 0))
        screen_x = ((self.player.x + offset_x) * RENDER_SCALE) - camera.x
        screen_y = ((self.player.y - self.player.height / 2 + offset_y) * RENDER_SCALE) - camera.y

        frame = self.frames_scaled_flipped[self._current_frame_index()] if self.direction == 'right' \
            else self.frames_scaled[self._current_frame_index()]
        rect = frame.get_rect(center=(screen_x, screen_y))
        screen.blit(frame, rect)


# ---------------------------------------------------------------------------
# Spin effect
# ---------------------------------------------------------------------------
#
# The sword sweeps clockwise through 8 facings as the player spins. Rather
# than rotating a single sprite by an arbitrary angle every frame (which
# looks smeary on pixel art), it snaps between two source sheets — one
# drawn facing straight up for the 4 cardinal facings, one drawn facing
# up-right for the 4 diagonal facings — each pre-rotated in 90-degree
# steps at load time. That matches how the art is described: one sprite
# for diagonals, one for vertical/horizontal.
#
# Angle convention: 0 degrees = up, increasing clockwise (matches the
# player spinning clockwise on screen).
_OCTANTS = ['up', 'up_right', 'right', 'down_right', 'down', 'down_left', 'left', 'up_left']
_CARDINAL_ROTATION = {'up': 0, 'left': 90, 'down': 180, 'right': 270}
_DIAGONAL_ROTATION = {'up_right': 0, 'up_left': 90, 'down_left': 180, 'down_right': 270}

# Unit vector pointing outward for each octant — used both to push the sword
# sprite out to its orbit position around the player in draw(), and by the
# no-art fallback line so the two stay visually consistent.
_OCTANT_VECTORS = {
    'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0),
    'up_right': (0.7071, -0.7071), 'down_right': (0.7071, 0.7071),
    'down_left': (-0.7071, 0.7071), 'up_left': (-0.7071, -0.7071),
}


class EnergySwordSpinEffect:
    """The spinning slash that plays automatically once the energy sword
    charge finishes. Lives on the player for a fixed, free duration (no
    further ki cost) — player.py owns the timer and calls update() every
    frame, moving right along with the player at walking speed.

    Hits are omnidirectional (a small radius around the player, since the
    player is spinning) rather than a single facing-based rect like melee.
    Because this persists across many frames the way a beam does, per-enemy
    hit ticking is handled here (can_hit/register_hit) rather than relying
    on the attack itself to only be checked once — game.py should only
    call enemy.check_collision_with_attack() when can_hit(enemy) is True,
    then call register_hit(enemy) immediately after a successful hit.

    assets/sprites/attacks/{attack_name}/sword_cardinal.png / sword_diagonal.png
    are each assumed to be a single row of frame_width x frame_height frames,
    drawn facing 'up' and 'up_right' respectively — see the rotation
    tables above.

    attack_name/frame_width/frame_height/hit_radius/octant_offsets/
    duration/direction_offsets are all new, optional kwargs (same
    additive convention as EnergySwordChargeEffect above) so this is
    drivable from attacks/attack_config.py's "sword" archetype.
    duration=None (the default) keeps the original behavior — player.py
    owns the timer and is responsible for ending the spin itself. Pass a
    number of seconds to have this object end itself instead (used by the
    attack creator's preview, which has no player.py driving it) — see
    update(). direction_offsets=None (the default) keeps the original
    behavior — the spin centers exactly on the player with no nudge; pass
    a {direction: (x, y)} dict (keyed by the player's facing when the
    spin started, which doesn't change mid-spin) to offset it, same
    convention EnergySwordChargeEffect's own direction_offsets uses.

    no_release_cancel is read by dev_tools/attack_creator.py's generic
    fire-release handler: unlike a beam's decay sweep or a chain's
    stop(), letting go of the button mid-spin should NOT cut it short —
    the spin is a fixed, free, autoplay beat once it starts (see the
    class docstring above) — so the creator skips its usual
    start_decay()/stop() fallback whenever this is True.
    """

    HIT_RADIUS = 34
    no_release_cancel = True

    def __init__(self, player, scale=RENDER_SCALE, damage=15,
                 rotations_per_second=2.0, hit_interval=0.2, clockwise=True,
                 attack_name='energy_sword', frame_width=24, frame_height=32,
                 hit_radius=None, octant_offsets=None, duration=None,
                 direction_offsets=None):
        self.player = player
        self.scale = scale
        self.active = True
        self.y_sort = False

        self.attack_name = attack_name
        self.CARDINAL_SPRITE_PATH = f'assets/sprites/attacks/{self.attack_name}/sword_cardinal.png'
        self.DIAGONAL_SPRITE_PATH = f'assets/sprites/attacks/{self.attack_name}/sword_diagonal.png'

        self.frame_width = frame_width
        self.frame_height = frame_height
        # None keeps the class-level HIT_RADIUS default (34).
        self.hit_radius = hit_radius if hit_radius is not None else self.HIT_RADIUS

        self.damage = damage
        self.hit_interval = hit_interval
        self._enemy_cooldowns = {}  # id(enemy) -> seconds remaining before it can be hit again

        # None (the default) = no self-timeout, matching the original
        # behavior where player.py's own timer ends the spin. A number
        # counts down in update() and sets active=False on expiry.
        self.duration = duration
        self._elapsed = 0.0

        # Which way the spin travels through the 8 octants — set once at
        # construction from the direction the player was facing when they
        # charged (see Player.start_sword_spin()). True = clockwise
        # (charged facing left), False = counter-clockwise (charged facing
        # right). See current_octant() for how this reverses traversal.
        self.clockwise = clockwise

        # 8 facing-steps per rotation, stepped through at whatever rate
        # gives rotations_per_second full spins.
        self.step_duration = (1.0 / rotations_per_second) / 8 if rotations_per_second > 0 else 0.06
        self.step_timer = 0.0
        self.octant_tick = 0  # advances by 1 every step_duration; current_octant() maps this to a direction

        # Local shimmer frame (only matters if a sheet has more than one
        # frame) — independent of the octant stepping above.
        self.local_frame_timer = 0.0
        self.local_frame_duration = 0.08
        self.local_frame_index = 0

        # World position — refreshed every update() so the hitbox and
        # draw position always track the (possibly moving) player, offset
        # by direction_offsets below (keyed by the player's facing at
        # spin time, which doesn't change mid-spin).
        self.direction_offsets = direction_offsets if direction_offsets is not None else {}
        ox, oy = self.direction_offsets.get(player.direction, (0, 0))
        self.x = player.x + ox
        self.y = player.y + oy

        # Per-octant (offset_x, offset_y) in world units, pre-scale, from
        # the player's true position — NOT a shared radius. A single
        # vertical_offset + uniform radius doesn't work here: shifting the
        # ring's center up before radiating out symmetrically makes 'up'
        # reach further from the player than 'down' does (they're both
        # orbit_radius from the shifted center, but very different
        # distances from the player's actual feet/anchor). Tune each
        # octant independently instead so up/down/diagonals can all be
        # made to actually look equidistant against the real art.
        # None (the default) keeps the original hardcoded tuning.
        self.octant_offsets = octant_offsets if octant_offsets is not None else {
            'up':         (0, -25),
            'up_right':   (18, -18),
            'right':      (25, 0),
            'down_right': (18, 18),
            'down':       (0, 25),
            'down_left':  (-18, 18),
            'left':       (-25, 0),
            'up_left':    (-18, -18),
        }

        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self._cardinal_frames = {}  # direction name -> list[Surface], pre-rotated
        self._diagonal_frames = {}
        self._load_sprites()

    # -- loading ----------------------------------------------------------

    def _load_sheet(self, path):
        try:
            sheet = pygame.image.load(path).convert_alpha()
            frames_per_row = sheet.get_width() // self.frame_width
            raw_frames = [
                sheet.subsurface(pygame.Rect(i * self.frame_width, 0, self.frame_width, self.frame_height))
                for i in range(frames_per_row)
            ]
            w = int(self.frame_width * self.scale)
            h = int(self.frame_height * self.scale)
            return [pygame.transform.scale(f, (w, h)) for f in raw_frames]
        except (pygame.error, FileNotFoundError) as e:
            print(f"Error loading energy sword sprite {path}: {e}")
            return []

    def _load_sprites(self):
        cardinal_base = self._load_sheet(self.CARDINAL_SPRITE_PATH)
        diagonal_base = self._load_sheet(self.DIAGONAL_SPRITE_PATH)

        for name, rotation in _CARDINAL_ROTATION.items():
            self._cardinal_frames[name] = [
                pygame.transform.rotate(f, rotation) for f in cardinal_base
            ]
        for name, rotation in _DIAGONAL_ROTATION.items():
            self._diagonal_frames[name] = [
                pygame.transform.rotate(f, rotation) for f in diagonal_base
            ]

        self._frame_count = max(len(cardinal_base), len(diagonal_base), 1)

    # -- facing helpers -----------------------------------------------------

    def current_octant(self):
        # Both directions start at _OCTANTS[0] ('up'). Clockwise walks the
        # list forward (up -> up_right -> right -> ...); counter-clockwise
        # walks it backward (up -> up_left -> left -> ...) by indexing with
        # a negated tick, since _OCTANTS itself is laid out in clockwise
        # order (see the module docstring above it).
        tick = self.octant_tick if self.clockwise else -self.octant_tick
        return _OCTANTS[tick % 8]

    def _frames_for_current_octant(self):
        octant = self.current_octant()
        if octant in _CARDINAL_ROTATION:
            return self._cardinal_frames.get(octant, [])
        return self._diagonal_frames.get(octant, [])

    # -- per-frame update ---------------------------------------------------

    def update(self, dt):
        if not self.active:
            return

        if self.duration is not None:
            self._elapsed += dt
            if self._elapsed >= self.duration:
                self.active = False
                return

        ox, oy = self.direction_offsets.get(self.player.direction, (0, 0))
        self.x = self.player.x + ox
        self.y = self.player.y + oy

        self.step_timer += dt
        while self.step_timer >= self.step_duration and self.step_duration > 0:
            self.step_timer -= self.step_duration
            self.octant_tick += 1

        if self._frame_count > 1:
            self.local_frame_timer += dt
            if self.local_frame_timer >= self.local_frame_duration:
                self.local_frame_timer -= self.local_frame_duration
                self.local_frame_index = (self.local_frame_index + 1) % self._frame_count

    def tick_cooldowns(self, dt):
        """Advance per-enemy hit cooldowns; call once per frame from player.py."""
        if not self._enemy_cooldowns:
            return
        expired = []
        for key, remaining in self._enemy_cooldowns.items():
            remaining -= dt
            if remaining <= 0:
                expired.append(key)
            else:
                self._enemy_cooldowns[key] = remaining
        for key in expired:
            del self._enemy_cooldowns[key]

    def can_hit(self, enemy):
        return id(enemy) not in self._enemy_cooldowns

    def register_hit(self, enemy):
        self._enemy_cooldowns[id(enemy)] = self.hit_interval

    def get_center(self):
        return (self.x, self.y)

    def get_sort_key(self):
        return (self.draw_layer, self.y)

    # -- drawing --------------------------------------------------------

    def draw(self, screen, camera, colors=None):
        if not self.active:
            return

        octant = self.current_octant()
        offset_x, offset_y = self.octant_offsets.get(octant, (0, 0))

        # True player-center screen position — the anchor both the orbited
        # sprite and the fallback line are drawn relative to.
        center_x = (self.x * RENDER_SCALE) - camera.x
        center_y = (self.y * RENDER_SCALE) - camera.y

        frames = self._frames_for_current_octant()
        if frames:
            # Push the sprite out from the player using this octant's own
            # tuned offset, so it reads as a held blade swinging around
            # the player, not a sprite stamped on top of them.
            screen_x = center_x + offset_x * RENDER_SCALE
            screen_y = center_y + offset_y * RENDER_SCALE
            idx = min(self.local_frame_index, len(frames) - 1)
            frame = frames[idx]
            rect = frame.get_rect(center=(int(screen_x), int(screen_y)))
            screen.blit(frame, rect)
        else:
            # Fallback while art is missing — draw a bright line pointing
            # in the current octant so the spin is still visible/testable.
            vx, vy = _OCTANT_VECTORS.get(octant, (0, -1))
            length = self.hit_radius * RENDER_SCALE
            end = (int(center_x + vx * length), int(center_y + vy * length))
            pygame.draw.line(screen, (140, 220, 255), (int(center_x), int(center_y)), end, 3)
            pygame.draw.circle(screen, (200, 240, 255), (int(center_x), int(center_y)), 4)