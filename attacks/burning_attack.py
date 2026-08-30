"""
attacks/burning_attack.py — Burning attack: a stunning ki blast.

Travels/animates like the base Projectile (same speed, motion, and frame
timing) but uses its own spritesheet — charge_burning_attack.png — instead
of ki_blast.png, and stuns whatever enemy it hits. Chargeable by holding
the attack button: while held, the player freezes on ki_blast frame 0
(handled in player.py, not here) and BurningChargeEffect draws that same
charge sprite next to/around them. Releasing the button fires the attack,
mirroring how genkidama is charged and released in game.py.

On hit, game.py's enemy-collision loop should spawn a BurningHitEffect at
the enemy's position (in addition to calling enemy.stun(...)) — it plays
hit_burning_attack.png once at the impact point and then deactivates
itself.

NOTE ON PARAMETERIZATION: attack_name/speed/radius/frame_width/frame_height
below are all new, optional kwargs added so this whole family can be driven
from data (see attacks/attack_config.py's "projectile" archetype and
dev_tools/attack_creator.py). Every one of them defaults to exactly the
value that used to be hardcoded, so existing call sites
(`BurningAttack(x, y, direction)` / `BurningAttack(x, y, direction,
stun_duration=...)`) behave identically to before — this is purely
additive, the same convention final_flash.py/banshee_blast.py already use
on top of BeamAttack.
"""

import pygame
from attacks.projectile import Projectile
from core.draw_layers import DrawLayer


class BurningAttack(Projectile):
    """Same travel/animation as Projectile, but with its own projectile
    spritesheet (charge_burning_attack.png — the same sheet used for the
    charge-up effect) and a stun effect on hit.

    game.py's enemy-collision loop should check
    `isinstance(projectile, BurningAttack)` (the same way it already checks
    `isinstance(projectile, GenkidamaBlast)`) and call `enemy.stun(...)`
    when it connects.
    """

    def __init__(self, x, y, direction, stun_duration=1.5, attack_name='burning_attack',
                 speed=None, radius=None, frame_width=None, frame_height=None):
        super().__init__(x, y, direction)
        self.stun_duration = stun_duration
        self.attack_name = attack_name

        # Optional overrides on top of whatever Projectile.__init__ already
        # set (speed=4, radius=8) — None (the default) means "keep
        # Projectile's own value", same nullable-means-inherit convention
        # attack_config.py's FieldSpec uses everywhere else.
        if speed is not None:
            self.speed = speed
        if radius is not None:
            self.radius = radius
        if frame_width is not None:
            self.frame_width = frame_width
        if frame_height is not None:
            self.frame_height = frame_height

        # Projectile.__init__ already loaded ki_blast.png into self.frames.
        # Reload with this attack's own sheet — the same charge sprite used
        # by BurningChargeEffect doubles as the traveling projectile — using
        # the (possibly overridden) frame_width/frame_height slicing so
        # movement/rotation/draw in the parent class keep working
        # unmodified. Falls back to the inherited ki_blast frames (or
        # Projectile's fallback circle) if the new sheet is missing, same
        # as Projectile's own try/except behavior.
        try:
            spritesheet = pygame.image.load(
                f'assets/sprites/attacks/{self.attack_name}/charge_{self.attack_name}.png'
            ).convert_alpha()
            sheet_width = spritesheet.get_width()
            num_frames = sheet_width // self.frame_width

            new_frames = []
            for i in range(num_frames):
                frame = spritesheet.subsurface(
                    pygame.Rect(i * self.frame_width, 0, self.frame_width, self.frame_height)
                )
                new_frames.append(frame)

            if not new_frames:
                raise FileNotFoundError("No frames extracted")

            self.frames = new_frames
            self.current_frame = 0
        except Exception:
            # Keep whatever Projectile.__init__ already set (ki_blast frames,
            # or [] if even that failed) rather than blanking self.frames.
            pass


class BurningChargeEffect:
    """Visual-only effect shown next to the player while the burning attack
    is being charged (attack button held down).

    Per spec, the player's own sprite just holds ki_blast frame 0 during the
    charge — that's player.py's job. This class only draws the extra
    "charging" sprite alongside the player, the same way
    genkidama_charge_effect / current_charge_effect sit next to the player
    for the other ki attacks.
    """

    def __init__(self, player, attack_name='burning_attack', frame_width=16, frame_height=16,
                 frame_duration=0.08, direction_offsets=None, target_charge_duration=1.0):
        self.player = player
        self.active = True
        self.attack_name = attack_name

        self.current_frame = 0
        self.frame_timer = 0
        self.frame_duration = frame_duration
        self.frame_width = frame_width
        self.frame_height = frame_height

        # Purely for callers that drive a "charge for N seconds, then
        # auto-fire while still held" loop (dev_tools/attack_creator.py's
        # fire state machine, same contract as
        # beam.KamehamehaChargeEffect.get_total_duration()) — the sprite
        # itself just loops for as long as this effect exists regardless
        # of this value; player.py's own hold-to-charge/release-to-fire
        # wiring is free to ignore get_total_duration() entirely and fire
        # on release instead, same as it always has.
        self._target_charge_duration = target_charge_duration

        self.frames = []
        try:
            spritesheet = pygame.image.load(
                f'assets/sprites/attacks/{self.attack_name}/charge_{self.attack_name}.png'
            ).convert_alpha()
            sheet_width = spritesheet.get_width()
            num_frames = sheet_width // self.frame_width
            for i in range(num_frames):
                frame = spritesheet.subsurface(
                    pygame.Rect(i * self.frame_width, 0, self.frame_width, self.frame_height)
                )
                self.frames.append(frame)
            if not self.frames:
                raise FileNotFoundError("No frames extracted")
        except Exception:
            self.frames = []

        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self.y_sort = False

        # Per-direction draw offset, in world pixels (pre-RENDER_SCALE),
        # relative to the player's position. Tweak these to line the charge
        # sprite up with the player's hands/ki_blast pose for each facing.
        # (x, y) — positive x is right, positive y is down. Defaults to the
        # burning_attack-tuned values below; pass your own dict (e.g. from
        # a data-driven config) to retune per attack_name.
        self.direction_offsets = direction_offsets if direction_offsets is not None else {
            'up':    (6, 3),
            'down':  (7, -2),
            'left':  (12, 0),
            'right': (-12, 0),
        }

    def get_total_duration(self):
        """See the target_charge_duration note in __init__ — lets generic
        charge-loop drivers (attack_creator.py) treat this the same way
        they treat KamehamehaChargeEffect."""
        return self._target_charge_duration

    def get_offset(self):
        return self.direction_offsets.get(self.player.direction, (0, 0))

    @property
    def x(self):
        offset_x, _ = self.get_offset()
        return self.player.x + offset_x

    @property
    def y(self):
        _, offset_y = self.get_offset()
        return self.player.y + offset_y

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def update(self, dt=0.016):
        # Left/right: the charge sprite sits beside the player at roughly
        # the same depth, so it should tuck behind them like a held object
        # would. Up/down: keep it in front, same as before.
        if self.player.direction in ('left', 'right'):
            self.draw_layer = DrawLayer.EFFECTS_BEHIND
        else:
            self.draw_layer = DrawLayer.EFFECTS_FRONT

        if not self.frames:
            return
        self.frame_timer += dt
        if self.frame_timer >= self.frame_duration:
            self.frame_timer = 0
            self.current_frame = (self.current_frame + 1) % len(self.frames)

    def draw(self, screen, camera, colors):
        from config.settings import RENDER_SCALE
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.frames:
            current_sprite = self.frames[self.current_frame]
            scaled_w = self.frame_width * RENDER_SCALE
            scaled_h = self.frame_height * RENDER_SCALE
            scaled_sprite = pygame.transform.scale(current_sprite, (scaled_w, scaled_h))
            sprite_rect = scaled_sprite.get_rect(center=(int(screen_x), int(screen_y)))
            screen.blit(scaled_sprite, sprite_rect)
        else:
            # Fallback glow until charge.png is added
            pygame.draw.circle(screen, colors['YELLOW'], (int(screen_x), int(screen_y)), 10 * RENDER_SCALE, width=2)


class BurningHitEffect:
    """Visual-only effect played once at the point of impact when a
    BurningAttack connects with an enemy.

    Unlike BurningChargeEffect (which follows the player and loops for as
    long as the attack button is held), this plays through
    hit_burning_attack.png exactly once at a fixed (x, y) — the enemy's
    position at the moment of the hit — and then sets `active = False` so
    game.py's effect list can drop it, the same way non-looping effects
    elsewhere in the game clean themselves up.

    game.py's enemy-collision loop should spawn one of these (alongside the
    existing `enemy.stun(...)` call) wherever it currently checks
    `isinstance(projectile, BurningAttack)`, e.g.:
        effects.append(BurningHitEffect(enemy.x, enemy.y))
    """

    def __init__(self, x, y, attack_name='burning_attack', frame_width=32, frame_height=32,
                 frame_duration=0.15):
        self.x = x
        self.y = y
        self.active = True
        self.attack_name = attack_name

        self.current_frame = 0
        self.frame_timer = 0
        self.frame_duration = frame_duration
        self.frame_width = frame_width
        self.frame_height = frame_height

        self.frames = []
        try:
            spritesheet = pygame.image.load(
                f'assets/sprites/attacks/{self.attack_name}/hit_{self.attack_name}.png'
            ).convert_alpha()
            sheet_width = spritesheet.get_width()
            num_frames = sheet_width // self.frame_width
            for i in range(num_frames):
                frame = spritesheet.subsurface(
                    pygame.Rect(i * self.frame_width, 0, self.frame_width, self.frame_height)
                )
                self.frames.append(frame)
            if not self.frames:
                raise FileNotFoundError("No frames extracted")
        except Exception:
            self.frames = []

        self.draw_layer = DrawLayer.EFFECTS_FRONT
        self.y_sort = False

        # No sprite sheet to time playback against — use a fixed fallback
        # duration so the placeholder glow still disappears on its own.
        self._fallback_timer = 0
        self._fallback_duration = 0.3

    def get_sort_key(self):
        return (self.draw_layer, 0)

    def update(self, dt=0.016):
        if not self.frames:
            # No spritesheet yet — show the fallback glow briefly, then
            # deactivate so the effect doesn't linger forever.
            self._fallback_timer += dt
            if self._fallback_timer >= self._fallback_duration:
                self.active = False
            return

        self.frame_timer += dt
        if self.frame_timer >= self.frame_duration:
            self.frame_timer = 0
            self.current_frame += 1
            if self.current_frame >= len(self.frames):
                # Play once, then deactivate — this is an impact effect,
                # not a loop.
                self.current_frame = len(self.frames) - 1
                self.active = False

    def draw(self, screen, camera, colors):
        if not self.active:
            return

        from config.settings import RENDER_SCALE
        screen_x = (self.x * RENDER_SCALE) - camera.x
        screen_y = (self.y * RENDER_SCALE) - camera.y

        if self.frames:
            current_sprite = self.frames[self.current_frame]
            scaled_w = self.frame_width * RENDER_SCALE
            scaled_h = self.frame_height * RENDER_SCALE
            scaled_sprite = pygame.transform.scale(current_sprite, (scaled_w, scaled_h))
            sprite_rect = scaled_sprite.get_rect(center=(int(screen_x), int(screen_y)))
            screen.blit(scaled_sprite, sprite_rect)
        else:
            # Fallback glow until hit_burning_attack.png is added
            pygame.draw.circle(screen, colors['YELLOW'], (int(screen_x), int(screen_y)), 10 * RENDER_SCALE, width=2)