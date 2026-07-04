import pygame
import os
import random
from config.settings import RENDER_SCALE


# --- Shared direction tables -------------------------------------------------
# Every animation-loading method needs "which row is this direction on" info.
# Previously each method rebuilt its own copy of these dicts/lists inline
# (same values, six+ separate places). Pulling them out to module level means
# there's exactly one place to edit if a direction ever needs to change, and
# avoids re-allocating identical dicts every time a sprite sheet is loaded.
DIRECTIONS_4 = ('down', 'left', 'right', 'up')
DIRECTION_MAP_4 = {name: index for index, name in enumerate(DIRECTIONS_4)}

DIRECTIONS_8 = ('down', 'down_left', 'left', 'up_left', 'up', 'up_right', 'right', 'down_right')
DIRECTION_MAP_8 = {name: index for index, name in enumerate(DIRECTIONS_8)}


def _directions(use_8_directions):
    """Return (direction_names, direction_to_row_map, num_directions) for a sheet layout."""
    if use_8_directions:
        return DIRECTIONS_8, DIRECTION_MAP_8, 8
    return DIRECTIONS_4, DIRECTION_MAP_4, 4


class SpriteSheet:
    """Wraps a sprite sheet PNG and gives back individual frames."""

    def __init__(self, filepath):
        """Load from filepath; sheet stays None if the file doesn't exist."""
        self.sheet = None
        if os.path.exists(filepath):
            self.sheet = pygame.image.load(filepath).convert_alpha()

    def get_sprite(self, x, y, width, height):
        """Cut a single frame out of the sheet. Returns magenta placeholder if sheet is missing."""
        if not self.sheet:
            surface = pygame.Surface((width, height))
            surface.fill((255, 0, 255))
            return surface

        sprite = pygame.Surface((width, height), pygame.SRCALPHA)
        sprite.blit(self.sheet, (0, 0), (x, y, width, height))
        # convert_alpha() puts the frame in the display's native pixel format.
        # It costs a little time once here at load, but every future blit/scale
        # of this frame (i.e. every frame it's on screen) is noticeably faster
        # than blitting/scaling a plain SRCALPHA surface.
        return sprite.convert_alpha()

    def get_sprite_row(self, row, num_frames, width, height, start_x=0):
        """Pull every frame from a single row — handy for grabbing a full animation."""
        y = row * height
        return [
            self.get_sprite(start_x + (i * width), y, width, height)
            for i in range(num_frames)
        ]

    def get_all_frames(self, width, height, direction_row=0):
        """Return all frames from one row, auto-detecting the frame count from sheet width."""
        if not self.sheet:
            return []

        num_frames = self.sheet.get_width() // width
        return self.get_sprite_row(direction_row, num_frames, width, height)


class Animation:
    """Drives a flipbook of frames at a fixed rate."""

    def __init__(self, frames, frame_duration=0.1, loop=True):
        self.frames = frames
        self.frame_duration = frame_duration
        self.loop = loop
        self.current_frame = 0
        self.time_elapsed = 0
        self.finished = False

    def update(self, dt):
        if self.finished and not self.loop:
            return

        self.time_elapsed += dt

        if self.time_elapsed >= self.frame_duration:
            self.time_elapsed = 0
            self.current_frame += 1

            if self.current_frame >= len(self.frames):
                if self.loop:
                    self.current_frame = 0
                else:
                    self.current_frame = len(self.frames) - 1
                    self.finished = True

    def get_current_frame(self):
        if not self.frames:
            return None
        return self.frames[self.current_frame]

    def reset(self):
        self.current_frame = 0
        self.time_elapsed = 0
        self.finished = False


class AnimatedSprite:
    """
    Entity sprite that plays directional animations loaded from separate files.
    Each animation lives at {base_path}/{animation_name}.png.
    """

    def __init__(self, character_name, costume_name, sprite_width, sprite_height):
        self.character_name = character_name
        self.costume_name = costume_name
        self.sprite_width = sprite_width
        self.sprite_height = sprite_height

        self.base_path = f"assets/sprites/player/{character_name}/{costume_name}"

        self.animations = {}
        self.current_animation = None
        self.current_direction = 'down'
        self.current_variant_index = 0

        self.offset_x = sprite_width // 2
        self.offset_y = sprite_height // 2

        # Draw-time render cache — see _ensure_render_cache(). Built lazily so
        # that sprites created via AnimatedSprite.__new__() (the enemy/NPC/boss
        # loaders below) don't need to remember to set this up too.
        self._scaled_frame_cache = {}
        self._scaled_width = int(sprite_width * RENDER_SCALE)
        self._scaled_height = int(sprite_height * RENDER_SCALE)

    @classmethod
    def _create_bare(cls, character_name, costume_name, sprite_width, sprite_height, base_path):
        """Build an AnimatedSprite that points at a custom base_path, without going
        through __init__ (used by EnemySpriteLoader / NPCSpriteLoader / create_boss_sprite,
        whose folder layout doesn't match assets/sprites/player/{name}/{costume}).

        Centralizing this in one place matters: the previous version had this same
        block copy-pasted three times, and two of the three copies forgot to set
        current_variant_index, which __init__ always sets. That's a latent
        AttributeError waiting to happen the first time draw()/is_animation_finished()
        hit a variant-list animation before set_animation() had run. Building bare
        sprites through one method means that can't happen again.
        """
        sprite = cls.__new__(cls)
        sprite.character_name = character_name
        sprite.costume_name = costume_name
        sprite.sprite_width = sprite_width
        sprite.sprite_height = sprite_height
        sprite.base_path = base_path
        sprite.animations = {}
        sprite.current_animation = None
        sprite.current_direction = 'down'
        sprite.current_variant_index = 0
        sprite.offset_x = sprite_width // 2
        sprite.offset_y = sprite_height // 2
        sprite._scaled_frame_cache = {}
        sprite._scaled_width = int(sprite_width * RENDER_SCALE)
        sprite._scaled_height = int(sprite_height * RENDER_SCALE)
        return sprite

    def load_animation(self, animation_name, direction, frame_duration=0.1, loop=True, num_variants=1,
                       use_8_directions=False):
        """Load one directional animation from {base_path}/{animation_name}.png.

        Sheet rows map to directions; stacked variant blocks sit below them
        (each block is num_directions rows tall).

        Returns True on success, False if the file is missing or has no frames.
        """
        filepath = f"{self.base_path}/{animation_name}.png"

        if not os.path.exists(filepath):
            return False

        sprite_sheet = SpriteSheet(filepath)

        # Support both 4-directional and 8-directional sprite sheets
        # 8-directional: down, down_left, left, up_left, up, up_right, right, down_right
        # 4-directional: down, left, right, up (legacy support)
        _, direction_map, num_directions = _directions(use_8_directions)
        direction_offset = direction_map.get(direction, 0)  # unknown direction falls back to row 0

        key = f"{animation_name}_{direction}"
        variants = []

        for variant_index in range(num_variants):
            row = (variant_index * num_directions) + direction_offset
            frames = sprite_sheet.get_all_frames(self.sprite_width, self.sprite_height, row)

            if not frames:
                continue

            animation = Animation(frames, frame_duration, loop)
            variants.append(animation)

        if not variants:
            return False

        self.animations[key] = variants[0] if len(variants) == 1 else variants
        return True

    def load_animation_all_directions(self, animation_name, frame_duration=0.1, loop=True, num_variants=1,
                                      use_8_directions=False):
        """Load an animation for every direction in one shot.

        use_8_directions=True  → down, down_left, left, up_left, up, up_right, right, down_right
        use_8_directions=False → down, left, right, up  (legacy 4-dir)
        """
        directions, _, _ = _directions(use_8_directions)
        for direction in directions:
            self.load_animation(animation_name, direction, frame_duration, loop, num_variants, use_8_directions)

    def append_animation_variants(self, animation_name, source_filename, frame_duration=0.1, loop=True, num_variants=1,
                                  use_8_directions=False):
        """Tack extra variants onto an existing animation from a different sheet file.

        Useful when overflow variants live in a separate file, e.g. melee_extra.png.
        """
        filepath = f"{self.base_path}/{source_filename}"

        if not os.path.exists(filepath):
            return False

        sprite_sheet = SpriteSheet(filepath)
        directions, direction_map, num_directions = _directions(use_8_directions)

        for direction in directions:
            direction_offset = direction_map.get(direction, 0)
            key = f"{animation_name}_{direction}"

            existing_anim = self.animations.get(key)
            if not existing_anim:
                continue

            # Convert single animation to list if needed
            if not isinstance(existing_anim, list):
                self.animations[key] = [existing_anim]

            # Load and append new variants
            for variant_index in range(num_variants):
                row = (variant_index * num_directions) + direction_offset
                frames = sprite_sheet.get_all_frames(self.sprite_width, self.sprite_height, row)

                if frames:
                    animation = Animation(frames, frame_duration, loop)
                    self.animations[key].append(animation)

        return True

    def load_animation_branching(self, animation_name, direction, frame_duration=0.1,
                                  num_endings=2, use_8_directions=False):
        """Load an animation from a sheet laid out as [start_frame, ending_1, ending_2, ...]
        on a single row (e.g. kiblast.png: start, right-hand throw, left-hand throw).

        Unlike load_animation(), this does NOT play every frame in sequence. Instead it
        builds `num_endings` separate 2-frame animations — [start, ending_1], [start, ending_2],
        etc. — and set_animation() will randomly choose ONE of them to play, so a single
        trigger shows the start frame plus exactly one ending, never all endings back to back.

        Returns True on success, False if the file is missing or doesn't have enough frames.
        """
        filepath = f"{self.base_path}/{animation_name}.png"

        if not os.path.exists(filepath):
            return False

        sprite_sheet = SpriteSheet(filepath)
        _, direction_map, _ = _directions(use_8_directions)

        row = direction_map.get(direction, 0)
        all_frames = sprite_sheet.get_all_frames(self.sprite_width, self.sprite_height, row)

        # Need at least a start frame plus every ending frame.
        if len(all_frames) < num_endings + 1:
            return False

        start_frame = all_frames[0]
        variants = [
            Animation([start_frame, all_frames[i]], frame_duration, loop=False)
            for i in range(1, num_endings + 1)
        ]

        key = f"{animation_name}_{direction}"
        self.animations[key] = variants if len(variants) > 1 else variants[0]
        return True

    def load_animation_branching_all_directions(self, animation_name, frame_duration=0.1,
                                                 num_endings=2, use_8_directions=False):
        """Branching version of load_animation_all_directions() — see load_animation_branching()."""
        directions, _, _ = _directions(use_8_directions)
        for direction in directions:
            self.load_animation_branching(animation_name, direction, frame_duration,
                                          num_endings, use_8_directions)

    def load_animation_fixed_frames(self, animation_name, direction, frame_duration=0.1,
                                     frame_indices=(0, 2), use_8_directions=False, source_name=None):
        """Load an animation that only plays specific frame indices from the row, in the
        given order — no sequential playback, no randomness. e.g. frame_indices=(0, 2)
        plays frame 0 then frame 2, skipping frame 1 entirely.

        source_name lets multiple differently-keyed animations share one sheet file —
        e.g. source_name='kiblast' reads kiblast.png but registers under 'kiblast_hold2'
        instead of looking for a nonexistent 'kiblast_hold2.png'. Defaults to animation_name.

        Returns True on success, False if the file is missing or doesn't have enough frames.
        """
        filepath = f"{self.base_path}/{source_name or animation_name}.png"

        if not os.path.exists(filepath):
            return False

        sprite_sheet = SpriteSheet(filepath)
        _, direction_map, _ = _directions(use_8_directions)

        row = direction_map.get(direction, 0)
        all_frames = sprite_sheet.get_all_frames(self.sprite_width, self.sprite_height, row)

        if not all_frames or max(frame_indices) >= len(all_frames):
            return False

        frames = [all_frames[i] for i in frame_indices]

        key = f"{animation_name}_{direction}"
        self.animations[key] = Animation(frames, frame_duration, loop=False)
        return True

    def load_animation_fixed_frames_all_directions(self, animation_name, frame_duration=0.1,
                                                    frame_indices=(0, 2), use_8_directions=False,
                                                    source_name=None):
        """Fixed-frame version of load_animation_all_directions() — see load_animation_fixed_frames()."""
        directions, _, _ = _directions(use_8_directions)
        for direction in directions:
            self.load_animation_fixed_frames(animation_name, direction, frame_duration,
                                             frame_indices, use_8_directions, source_name)

    def has_animation(self, animation_name, direction):
        """True if this animation/direction combo was successfully loaded."""
        return f"{animation_name}_{direction}" in self.animations

    def set_animation(self, animation_name, direction):
        """Switch to a different animation, resetting it only if the key actually changed."""
        key = f"{animation_name}_{direction}"

        if key not in self.animations:
            return

        # Only reset if this is actually a different animation
        if self.current_animation != key:
            self.current_animation = key
            self.current_direction = direction

            # Reset the animation(s)
            anim = self.animations[key]
            if isinstance(anim, list):
                # Randomly pick which variant plays this time (covers variants
                # from both the base sheet and any appended extra sheet, e.g.
                # melee.png + melee_extra.png) instead of always using index 0.
                self.current_variant_index = random.randrange(len(anim))
                anim[self.current_variant_index].reset()
            else:
                self.current_variant_index = 0
                anim.reset()

    def update(self, dt):
        """Tick the current animation forward by dt seconds."""
        if not self.current_animation or self.current_animation not in self.animations:
            return

        anim = self.animations[self.current_animation]

        # Handle both single animation and variant lists
        if isinstance(anim, list):
            if 0 <= self.current_variant_index < len(anim):
                anim[self.current_variant_index].update(dt)
        else:
            anim.update(dt)

    def _get_scaled_frame(self, frame):
        """Return `frame` scaled to on-screen size, reusing a cached copy when possible.

        This is the main perf fix in this file: draw() used to call
        pygame.transform.scale() on every single call, for every sprite, every
        frame — but an animation frame is a fixed Surface that only actually
        changes when the animation advances to its next frame (a handful of
        times a second, not 60 times a second). Scaling is one of the more
        expensive things pygame does per-sprite, so caching the scaled result
        keyed by the frame it came from turns "rescale N sprites every tick"
        into "rescale a frame only the first time it's shown."
        """
        cached = self._scaled_frame_cache.get(id(frame))
        if cached is not None:
            return cached

        scaled = pygame.transform.scale(frame, (self._scaled_width, self._scaled_height))
        self._scaled_frame_cache[id(frame)] = scaled
        return scaled

    def draw(self, screen, x, y, camera=None, scale=1.0, hurt_tint=0.0):
        """Draw the current frame at world position (x, y).

        hurt_tint is a 0.0-1.0 value that adds red via BLEND_RGB_ADD —
        this keeps transparent pixels clean instead of drawing a coloured box.

        Note: `scale` is accepted for backwards compatibility but isn't used —
        on-screen size is driven entirely by RENDER_SCALE plus this sprite's
        own sprite_width/sprite_height.
        """
        # Sprites created before this cache existed (or via a stale pickle/save)
        # won't have these attributes; build them on first use rather than
        # requiring every construction path to remember to do it.
        if not hasattr(self, '_scaled_frame_cache'):
            self._scaled_frame_cache = {}
            self._scaled_width = int(self.sprite_width * RENDER_SCALE)
            self._scaled_height = int(self.sprite_height * RENDER_SCALE)

        if not self.current_animation or self.current_animation not in self.animations:
            # Draw placeholder if no animation
            if camera:
                screen_x = (x * RENDER_SCALE) - camera.x
                screen_y = (y * RENDER_SCALE) - camera.y
            else:
                screen_x = x * RENDER_SCALE
                screen_y = y * RENDER_SCALE

            pygame.draw.rect(screen, (255, 0, 255),
                             (screen_x - self.offset_x * RENDER_SCALE,
                              screen_y - self.offset_y * RENDER_SCALE,
                              self.sprite_width * RENDER_SCALE,
                              self.sprite_height * RENDER_SCALE))
            return

        anim = self.animations[self.current_animation]

        if isinstance(anim, list):
            if 0 <= self.current_variant_index < len(anim):
                frame = anim[self.current_variant_index].get_current_frame()
            elif anim:
                frame = anim[0].get_current_frame()
            else:
                frame = None
        else:
            frame = anim.get_current_frame()

        if frame:
            # Convert WORLD coordinates to SCREEN coordinates
            # Formula: (world_pos * scale) - camera_screen_pos
            # This matches how spawn objects are drawn in game.py
            if camera:
                screen_x = (x * RENDER_SCALE) - camera.x
                screen_y = (y * RENDER_SCALE) - camera.y
            else:
                screen_x = x * RENDER_SCALE
                screen_y = y * RENDER_SCALE

            frame = self._get_scaled_frame(frame)

            # Hurt tint: add red to each pixel's RGB, alpha untouched → no square artifact.
            # Only copy+tint when actually flashing hurt, so the common case (no
            # tint) just blits the cached scaled surface directly with no copy at all.
            if hurt_tint > 0:
                frame = frame.copy()
                red_amount = int(hurt_tint * 180)
                frame.fill((red_amount, 0, 0), special_flags=pygame.BLEND_RGB_ADD)

            offset_x = self._scaled_width // 2
            offset_y = self._scaled_height // 2

            # Draw sprite centered on position
            screen.blit(frame, (screen_x - offset_x, screen_y - offset_y))

    def is_animation_finished(self):
        """True when the current non-looping animation has played through all its frames."""
        if self.current_animation and self.current_animation in self.animations:
            anim = self.animations[self.current_animation]

            if isinstance(anim, list):
                if 0 <= self.current_variant_index < len(anim):
                    return anim[self.current_variant_index].finished
                return all(variant.finished for variant in anim)
            else:
                return anim.finished
        return False


def _load_sprite_size(folder, default_w=32, default_h=32):
    """Read frame size from {folder}/sprite_size.txt if it exists.

    Expected format — just one line like:  48x48
    Falls back to default_w / default_h so every existing spritesheet
    that has no config file keeps working at 32×32 without any changes.
    """
    path = os.path.join(folder, 'sprite_size.txt')
    if os.path.isfile(path):
        try:
            with open(path) as f:
                text = f.read().strip().lower()
            w, h = text.split('x')
            return int(w), int(h)
        except Exception:
            pass
    return default_w, default_h


def _has_png(folder):
    """True if `folder` exists and has at least one .png file directly inside it."""
    if not os.path.isdir(folder):
        return False
    return any(
        f.endswith('.png') for f in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, f))
    )


def _resolve_variant_folder(direct_path, variant):
    """Pick which folder actually holds an enemy's/NPC's sprite PNGs.

    Priority order (shared by EnemySpriteLoader and NPCSpriteLoader, which
    used to each duplicate this exact logic):
      1. {direct_path}/variants/{variant}/  — variant carries its own full spriteset
      2. {direct_path}/                     — flat layout, no variant subfolders
    Returns None if direct_path doesn't exist or neither location has PNGs.
    """
    if not os.path.exists(direct_path):
        return None

    variant_path = f"{direct_path}/variants/{variant}"
    if _has_png(variant_path):
        return variant_path
    if _has_png(direct_path):
        return direct_path
    return None


class CharacterSpriteLoader:
    """Loads all standard animations for a playable character."""

    @staticmethod
    def load_character(character_name, costume_name, sprite_width, sprite_height):
        folder = f"assets/sprites/player/{character_name}/{costume_name}"
        sprite_width, sprite_height = _load_sprite_size(folder, sprite_width, sprite_height)
        sprite = AnimatedSprite(character_name, costume_name, sprite_width, sprite_height)

        # Standard 4-directional animations
        animations_4dir = [
            ('idle', 0.3, True, 1),
            ('walk', 0.1, True, 1),
            ('run', 0.13, True, 1),
            ('melee', 0.1, False, 2),  # Load first 2 variants from melee.png
            ('melee2', 0.1, False, 2),
            ('melee3', 0.1, False, 1),
            ('hurt', 0.1, False, 1),
            ('death', 0.15, False, 1),
            ('charge', 0.1, True, 1),
            ('block', 0.2, True, 1),
            ('transform', 0.15, False, 1),
            ('untransform', 0.15, False, 1),
        ]

        for anim_name, duration, loop, num_variants in animations_4dir:
            sprite.load_animation_all_directions(anim_name, duration, loop, num_variants, use_8_directions=False)

        # Idle-wait animations: after standing still for a while (see
        # Player.IDLE_WAIT_DELAY), the character turns to face the camera and
        # plays a wait pose. idle_transition covers however many lead-in frames
        # it takes to reach the loop point (this can vary per character) and
        # plays once; idle_loop is the actual looping wait pose it hands off
        # to. Both are always shown facing down regardless of the player's
        # real facing direction, so only the down row needs to exist —
        # loaded directly rather than via *_all_directions. Entirely optional:
        # if idle_transition.png/idle_loop.png don't exist for this character,
        # has_animation() simply returns False and the player just never
        # leaves regular idle.
        sprite.load_animation('idle_transition', 'down', frame_duration=0.15, loop=False, num_variants=1)
        sprite.load_animation('idle_wait', 'down', frame_duration=0.5, loop=True, num_variants=1)

        # kiblast.png is laid out as [start, right-hand throw, left-hand throw] on one row.
        # A single Q press always shows frame 0 followed by frame 1 — fixed, not random.
        sprite.load_animation_fixed_frames_all_directions('kiblast', frame_duration=0.3,
                                                           frame_indices=(0, 1), use_8_directions=False)

        # Hold-fire follow-up animations: while Q stays held after the first shot,
        # the player alternates frame 2 → frame 1 → frame 2 → ... firing a blast
        # on each switch (see Player._advance_blast_or_idle). Both read from the
        # same kiblast.png sheet — source_name is required since there's no
        # separate kiblast_hold1.png / kiblast_hold2.png file on disk.
        sprite.load_animation_fixed_frames_all_directions('kiblast_hold2', frame_duration=0.3,
                                                           frame_indices=(2,), use_8_directions=False,
                                                           source_name='kiblast')
        sprite.load_animation_fixed_frames_all_directions('kiblast_hold1', frame_duration=0.3,
                                                           frame_indices=(1,), use_8_directions=False,
                                                           source_name='kiblast')

        # Load third melee variant from melee_extra.png (optional)
        sprite.append_animation_variants('melee', 'melee_extra.png', frame_duration=0.1, loop=False, num_variants=1,
                                         use_8_directions=False)

        # 8-directional animations (like flying)
        animations_8dir = [
            ('flying', 0.1, True, 1),
        ]

        for anim_name, duration, loop, num_variants in animations_8dir:
            sprite.load_animation_all_directions(anim_name, duration, loop, num_variants, use_8_directions=True)

        sprite.set_animation('idle', 'down')

        return sprite

    @staticmethod
    def list_available_characters():
        sprites_path = "assets/sprites"
        if not os.path.exists(sprites_path):
            return []

        characters = []
        for item in os.listdir(sprites_path):
            char_path = os.path.join(sprites_path, item)
            if os.path.isdir(char_path) and item != 'enemies':
                characters.append(item)
        return characters

    @staticmethod
    def list_available_costumes(character_name):
        char_path = f"assets/sprites/player/{character_name}"
        if not os.path.exists(char_path):
            return []

        costumes = []
        for item in os.listdir(char_path):
            costume_path = os.path.join(char_path, item)
            if os.path.isdir(costume_path):
                costumes.append(item)
        return costumes


class EnemySpriteLoader:
    """Loads all standard animations for an enemy, handling both old and new folder layouts."""

    @staticmethod
    def load_enemy(enemy_type, variant='default', sprite_width=32, sprite_height=32):
        """Load an enemy sprite, checking two folder structures in priority order:

        1. Variant subfolder  assets/sprites/enemies/{type}/variants/{variant}/
           (used when a variant has its own full spriteset, e.g. shooter/variants/gunner/)
        2. Flat folder        assets/sprites/enemies/{type}/
           (sprites sitting directly in the enemy folder, no variant subdirectories)
        """
        direct_path = f"assets/sprites/enemies/{enemy_type}"
        base_path = _resolve_variant_folder(direct_path, variant)
        if base_path is None:
            return None

        sprite_width, sprite_height = _load_sprite_size(base_path, sprite_width, sprite_height)

        sprite = AnimatedSprite._create_bare(enemy_type, variant, sprite_width, sprite_height, base_path)

        # Enemy animations (4-directional)
        animations = [
            ('idle', 0.3, True, 1),
            ('walk', 0.15, True, 1),
            ('attack', 0.1, False, 1),  # Ranged/generic attack animation
            ('melee', 0.1, False, 1),   # Melee swing animation (melee.png)
            ('kiblast', 0.3, False, 1), # Ki blast projectile animation (kiblast.png)
            ('hurt', 0.1, False, 1),
            ('death', 0.15, False, 1),
        ]

        for anim_name, duration, loop, num_variants in animations:
            sprite.load_animation_all_directions(anim_name, duration, loop, num_variants, use_8_directions=False)

        sprite.set_animation('idle', 'down')

        return sprite

    @staticmethod
    def list_available_enemies():
        enemies_path = "assets/sprites/enemies"
        if not os.path.exists(enemies_path):
            return []

        enemies = []
        for item in os.listdir(enemies_path):
            enemy_path = os.path.join(enemies_path, item)
            if os.path.isdir(enemy_path):
                enemies.append(item)
        return enemies

    @staticmethod
    def list_available_variants(enemy_type):
        variants_path = f"assets/sprites/enemies/{enemy_type}/variants"
        if not os.path.exists(variants_path):
            return []

        variants = []
        for item in os.listdir(variants_path):
            variant_path = os.path.join(variants_path, item)
            if os.path.isdir(variant_path):
                variants.append(item)
        return variants


class NPCSpriteLoader:
    """Loads idle/walk animations for a placed NPC.

    Folder layout mirrors EnemySpriteLoader:
      Direct:   assets/sprites/npcs/{npc_type}/
      Variant:  assets/sprites/npcs/{npc_type}/variants/{variant}/
    """

    @staticmethod
    def load_npc(npc_type='generic', variant='default', sprite_width=32, sprite_height=32):
        base = f"assets/sprites/npc/{npc_type}"
        sprite_path = _resolve_variant_folder(base, variant)
        if sprite_path is None:
            return None

        sprite_width, sprite_height = _load_sprite_size(sprite_path, sprite_width, sprite_height)

        sprite = AnimatedSprite._create_bare(npc_type, variant, sprite_width, sprite_height, sprite_path)

        for anim_name, duration, loop in [
            ('idle', 0.3,  True),
            ('walk', 0.12, True),
        ]:
            sprite.load_animation_all_directions(anim_name, duration, loop,
                                                 num_variants=1,
                                                 use_8_directions=False)

        sprite.set_animation('idle', 'down')
        return sprite

    @staticmethod
    def list_available_npcs():
        path = "assets/sprites/npc"
        if not os.path.exists(path):
            return []
        return [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]

    @staticmethod
    def list_available_variants(npc_type='generic'):
        path = f"assets/sprites/npc/{npc_type}/variants"
        if not os.path.exists(path):
            return []
        return [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]


def create_character_sprite(character, costume='base', width=32, height=32):
    """Shorthand for CharacterSpriteLoader.load_character."""
    return CharacterSpriteLoader.load_character(character, costume, width, height)


def create_enemy_sprite(enemy_type, variant='default', width=32, height=32):
    """Shorthand for EnemySpriteLoader.load_enemy."""
    return EnemySpriteLoader.load_enemy(enemy_type, variant, width, height)


def create_npc_sprite(npc_type, variant='default', width=32, height=32):
    """Shorthand for NPCSpriteLoader.load_npc."""
    return NPCSpriteLoader.load_npc(npc_type, variant, width, height)


def create_boss_sprite(boss_id, variant='default', width=48, height=48):
    """Load a boss sprite from assets/sprites/enemies/boss/{boss_id}/.

    Returns None gracefully if the folder doesn't exist yet.
    """
    boss_path = f"assets/sprites/enemies/boss/{boss_id}"

    if not os.path.exists(boss_path):
        return None

    width, height = _load_sprite_size(boss_path, width, height)

    sprite = AnimatedSprite._create_bare(boss_id, variant, width, height, boss_path)

    animations = [
        ('idle',    0.3,  True,  1),
        ('walk',    0.15, True,  1),
        ('attack',  0.1,  False, 1),
        ('melee',   0.1,  False, 1),
        ('kiblast', 0.3,  False, 1), # Ki blast projectile animation (kiblast.png)
        ('hurt',    0.1,  False, 1),
        ('death',   0.15, False, 1),
    ]

    for anim_name, duration, loop, num_variants in animations:
        sprite.load_animation_all_directions(anim_name, duration, loop, num_variants, use_8_directions=False)

    sprite.set_animation('idle', 'down')
    return sprite