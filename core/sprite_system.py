import pygame
import os
import random
from config.settings import RENDER_SCALE


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
        return sprite

    def get_sprite_row(self, row, num_frames, width, height, start_x=0):
        """Pull every frame from a single row — handy for grabbing a full animation."""
        sprites = []
        y = row * height
        for i in range(num_frames):
            x = start_x + (i * width)
            sprites.append(self.get_sprite(x, y, width, height))
        return sprites

    def get_all_frames(self, width, height, direction_row=0):
        """Return all frames from one row, auto-detecting the frame count from sheet width."""
        if not self.sheet:
            return []

        sheet_width = self.sheet.get_width()
        num_frames = sheet_width // width

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

        self.offset_x = sprite_width // 2
        self.offset_y = sprite_height // 2

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
        if use_8_directions:
            direction_map = {
                'down': 0,
                'down_left': 1,
                'left': 2,
                'up_left': 3,
                'up': 4,
                'up_right': 5,
                'right': 6,
                'down_right': 7
            }
            num_directions = 8
        else:
            direction_map = {
                'down': 0,
                'left': 1,
                'right': 2,
                'up': 3
            }
            num_directions = 4

        direction_offset = direction_map.get(direction, 0)
        if direction not in direction_map:
            pass  # Unknown direction falls back to row 0

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

        if len(variants) == 1:
            self.animations[key] = variants[0]
        else:
            self.animations[key] = variants

        return True

    def load_animation_all_directions(self, animation_name, frame_duration=0.1, loop=True, num_variants=1,
                                      use_8_directions=False):
        """Load an animation for every direction in one shot.

        use_8_directions=True  → down, down_left, left, up_left, up, up_right, right, down_right
        use_8_directions=False → down, left, right, up  (legacy 4-dir)
        """
        if use_8_directions:
            directions = ['down', 'down_left', 'left', 'up_left', 'up', 'up_right', 'right', 'down_right']
        else:
            directions = ['down', 'left', 'right', 'up']

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

        if use_8_directions:
            direction_map = {
                'down': 0,
                'down_left': 1,
                'left': 2,
                'up_left': 3,
                'up': 4,
                'up_right': 5,
                'right': 6,
                'down_right': 7
            }
            num_directions = 8
            directions = ['down', 'down_left', 'left', 'up_left', 'up', 'up_right', 'right', 'down_right']
        else:
            direction_map = {
                'down': 0,
                'left': 1,
                'right': 2,
                'up': 3
            }
            num_directions = 4
            directions = ['down', 'left', 'right', 'up']

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
                for variant in anim:
                    variant.reset()
            else:
                anim.reset()

    def update(self, dt):
        """Tick the current animation forward by dt seconds."""
        if not self.current_animation or self.current_animation not in self.animations:
            return

        anim = self.animations[self.current_animation]

        # Handle both single animation and variant lists
        if isinstance(anim, list):
            for variant in anim:
                variant.update(dt)
        else:
            anim.update(dt)

    def draw(self, screen, x, y, camera=None, scale=1.0, hurt_tint=0.0):
        """Draw the current frame at world position (x, y).

        hurt_tint is a 0.0-1.0 value that adds red via BLEND_RGB_ADD —
        this keeps transparent pixels clean instead of drawing a coloured box.
        """
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
            frame = None
            for variant in anim:
                if not variant.finished or variant.loop:
                    frame = variant.get_current_frame()
                    break
            if not frame and anim:
                frame = anim[0].get_current_frame()
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

            # Scale the sprite
            scaled_width = int(self.sprite_width * RENDER_SCALE)
            scaled_height = int(self.sprite_height * RENDER_SCALE)
            frame = pygame.transform.scale(frame, (scaled_width, scaled_height))

            # Hurt tint: add red to each pixel's RGB, alpha untouched → no square artifact
            if hurt_tint > 0:
                frame = frame.copy()
                red_amount = int(hurt_tint * 180)
                frame.fill((red_amount, 0, 0), special_flags=pygame.BLEND_RGB_ADD)

            offset_x = scaled_width // 2
            offset_y = scaled_height // 2

            # Draw sprite centered on position
            screen.blit(frame, (screen_x - offset_x, screen_y - offset_y))

    def is_animation_finished(self):
        """True when the current non-looping animation has played through all its frames."""
        if self.current_animation and self.current_animation in self.animations:
            anim = self.animations[self.current_animation]

            if isinstance(anim, list):
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
            text = open(path).read().strip().lower()
            w, h = text.split('x')
            return int(w), int(h)
        except Exception:
            pass
    return default_w, default_h

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
            ('kiblast', 0.3, False, 1),
            ('hurt', 0.1, False, 1),
            ('death', 0.15, False, 1),
            ('charge', 0.1, True, 1),
            ('block', 0.2, True, 1),
            ('transform', 0.15, False, 1),
            ('untransform', 0.15, False, 1),
        ]

        for anim_name, duration, loop, num_variants in animations_4dir:
            sprite.load_animation_all_directions(anim_name, duration, loop, num_variants, use_8_directions=False)

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

        if not os.path.exists(direct_path):
            return None

        # PRIORITY 1: variant subfolder  assets/sprites/enemies/{type}/variants/{variant}/
        # This lets each variant carry its own spriteset (e.g. shooter/variants/gunner/).
        variant_path = f"{direct_path}/variants/{variant}"
        if os.path.exists(variant_path) and any(
            f.endswith('.png') for f in os.listdir(variant_path)
            if os.path.isfile(os.path.join(variant_path, f))
        ):
            base_path = variant_path

        # PRIORITY 2: sprites placed directly in the enemy folder (no variant subfolders)
        elif any(
            f.endswith('.png') for f in os.listdir(direct_path)
            if os.path.isfile(os.path.join(direct_path, f))
        ):
            base_path = direct_path

        else:
            return None

        sprite_width, sprite_height = _load_sprite_size(base_path, sprite_width, sprite_height)

        # Create sprite with custom base path
        sprite = AnimatedSprite.__new__(AnimatedSprite)
        sprite.character_name = enemy_type
        sprite.costume_name = variant
        sprite.sprite_width = sprite_width
        sprite.sprite_height = sprite_height
        sprite.base_path = base_path
        sprite.animations = {}
        sprite.current_animation = None
        sprite.current_direction = 'down'
        sprite.offset_x = sprite_width // 2
        sprite.offset_y = sprite_height // 2

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

        if not os.path.exists(base):
            return None

        # Prefer a variant subfolder when it exists and has PNGs in it.
        variant_path = f"{base}/variants/{variant}"
        if os.path.exists(variant_path) and any(
            f.endswith('.png') for f in os.listdir(variant_path)
            if os.path.isfile(os.path.join(variant_path, f))
        ):
            sprite_path = variant_path
        elif any(
            f.endswith('.png') for f in os.listdir(base)
            if os.path.isfile(os.path.join(base, f))
        ):
            sprite_path = base
        else:
            return None

        sprite_width, sprite_height = _load_sprite_size(sprite_path, sprite_width, sprite_height)

        # Reuse AnimatedSprite machinery — bypass __init__ and set fields directly.
        sprite = AnimatedSprite.__new__(AnimatedSprite)
        sprite.character_name    = npc_type
        sprite.costume_name      = variant
        sprite.sprite_width      = sprite_width
        sprite.sprite_height     = sprite_height
        sprite.base_path         = sprite_path
        sprite.animations        = {}
        sprite.current_animation = None
        sprite.current_direction = 'down'
        sprite.offset_x          = sprite_width  // 2
        sprite.offset_y          = sprite_height // 2

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

    # Reuse AnimatedSprite machinery — just point base_path at the boss folder
    sprite = AnimatedSprite.__new__(AnimatedSprite)
    sprite.character_name = boss_id
    sprite.costume_name = variant
    sprite.sprite_width = width
    sprite.sprite_height = height
    sprite.base_path = boss_path
    sprite.animations = {}
    sprite.current_animation = None
    sprite.current_direction = 'down'
    sprite.offset_x = width // 2
    sprite.offset_y = height // 2

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