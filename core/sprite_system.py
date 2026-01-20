import pygame
import os
import random
from config.settings import RENDER_SCALE


class SpriteSheet:
    """Handles loading and extracting sprites from a sprite sheet"""

    def __init__(self, filepath):
        self.sheet = None
        if os.path.exists(filepath):
            self.sheet = pygame.image.load(filepath).convert_alpha()
        else:
            print(f"Warning: Sprite sheet not found: {filepath}")

    def get_sprite(self, x, y, width, height):
        """Extract a single sprite from the sheet"""
        if not self.sheet:
            surface = pygame.Surface((width, height))
            surface.fill((255, 0, 255))
            return surface

        sprite = pygame.Surface((width, height), pygame.SRCALPHA)
        sprite.blit(self.sheet, (0, 0), (x, y, width, height))
        return sprite

    def get_sprite_row(self, row, num_frames, width, height, start_x=0):
        """Get all sprites from a row (for animations)"""
        sprites = []
        y = row * height
        for i in range(num_frames):
            x = start_x + (i * width)
            sprites.append(self.get_sprite(x, y, width, height))
        return sprites

    def get_all_frames(self, width, height, direction_row=0):
        """Get all frames from a single animation sheet (one row per direction)"""
        if not self.sheet:
            return []

        sheet_width = self.sheet.get_width()
        num_frames = sheet_width // width

        return self.get_sprite_row(direction_row, num_frames, width, height)


class Animation:
    """Handles sprite animation"""

    def __init__(self, frames, frame_duration=0.1, loop=True):
        self.frames = frames
        self.frame_duration = frame_duration
        self.loop = loop
        self.current_frame = 0
        self.time_elapsed = 0
        self.finished = False

    def update(self, dt):
        """Update animation"""
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
        """Get the current frame surface"""
        if not self.frames:
            return None
        return self.frames[self.current_frame]

    def reset(self):
        """Reset animation to first frame"""
        self.current_frame = 0
        self.time_elapsed = 0
        self.finished = False


class AnimatedSprite:
    """
    Entity with multiple animations loaded from separate files
    """

    def __init__(self, character_name, costume_name, sprite_width, sprite_height):
        self.character_name = character_name
        self.costume_name = costume_name
        self.sprite_width = sprite_width
        self.sprite_height = sprite_height

        self.base_path = f"assets/sprites/{character_name}/{costume_name}"

        self.animations = {}
        self.current_animation = None
        self.current_direction = 'down'

        self.offset_x = sprite_width // 2
        self.offset_y = sprite_height // 2

    def load_animation(self, animation_name, direction, frame_duration=0.1, loop=True, num_variants=1):
        filepath = f"{self.base_path}/{animation_name}.png"

        if not os.path.exists(filepath):
            print(f"Warning: Animation file not found: {filepath}")
            return False

        sprite_sheet = SpriteSheet(filepath)
        direction_map = {'down': 0, 'left': 1, 'right': 2, 'up': 3}
        direction_offset = direction_map.get(direction, 0)

        key = f"{animation_name}_{direction}"
        variants = []

        for variant_index in range(num_variants):
            row = (variant_index * 4) + direction_offset
            frames = sprite_sheet.get_all_frames(self.sprite_width, self.sprite_height, row)

            if not frames:
                print(f"Warning: No frames loaded from {filepath} row {row}")
                continue

            animation = Animation(frames, frame_duration, loop)
            variants.append(animation)

        if not variants:
            print(f"Warning: No variants loaded for {animation_name}_{direction}")
            return False

        if len(variants) == 1:
            self.animations[key] = variants[0]
        else:
            self.animations[key] = variants

        return True

    def load_animation_all_directions(self, animation_name, frame_duration=0.1, loop=True, num_variants=1):
        for direction in ['down', 'left', 'right', 'up']:
            self.load_animation(animation_name, direction, frame_duration, loop, num_variants)

    def set_animation(self, name, direction=None):
        if direction:
            self.current_direction = direction

        key = f"{name}_{self.current_direction}"

        if key in self.animations:
            if self.current_animation == key:
                return

            self.current_animation = key

            if isinstance(self.animations[key], list):
                selected_variant = random.choice(self.animations[key])
                selected_variant.reset()
            else:
                self.animations[key].reset()
        else:
            if self.load_animation(name, self.current_direction):
                self.current_animation = key
            else:
                print(f"Warning: Animation '{key}' not found")

    def update(self, dt):
        """Update current animation"""
        if self.current_animation and self.current_animation in self.animations:
            anim = self.animations[self.current_animation]

            if isinstance(anim, list):
                for variant in anim:
                    variant.update(dt)
            else:
                anim.update(dt)

    def draw(self, screen, x, y, camera=None, scale=RENDER_SCALE):
        """
        Draw the sprite
        x, y: WORLD coordinates of entity center
        camera: Camera object (camera.x and camera.y are in SCREEN coordinates)
        scale: RENDER_SCALE value for consistent scaling
        """
        if not self.current_animation or self.current_animation not in self.animations:
            # Convert to screen coordinates: (world_pos * scale) - camera_screen_pos
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

            offset_x = scaled_width // 2
            offset_y = scaled_height // 2

            # Draw sprite centered on position
            screen.blit(frame, (screen_x - offset_x, screen_y - offset_y))

    def is_animation_finished(self):
        """Check if current animation has finished"""
        if self.current_animation and self.current_animation in self.animations:
            anim = self.animations[self.current_animation]

            if isinstance(anim, list):
                return all(variant.finished for variant in anim)
            else:
                return anim.finished
        return False


class CharacterSpriteLoader:
    """Helper to load all animations for a character"""

    @staticmethod
    def load_character(character_name, costume_name, sprite_width, sprite_height):
        sprite = AnimatedSprite(character_name, costume_name, sprite_width, sprite_height)

        animations = [
            ('idle', 0.3, True, 1),
            ('walk', 0.1, True, 1),
            ('run', 0.08, True, 1),
            ('melee', 0.1, False, 3),
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

        for anim_name, duration, loop, num_variants in animations:
            sprite.load_animation_all_directions(anim_name, duration, loop, num_variants)

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
            if os.path.isdir(char_path):
                characters.append(item)
        return characters

    @staticmethod
    def list_available_costumes(character_name):
        char_path = f"assets/sprites/{character_name}"
        if not os.path.exists(char_path):
            return []

        costumes = []
        for item in os.listdir(char_path):
            costume_path = os.path.join(char_path, item)
            if os.path.isdir(costume_path):
                costumes.append(item)
        return costumes


def create_character_sprite(character, costume='base', width=32, height=32):
    """Quick function to create a character sprite"""
    return CharacterSpriteLoader.load_character(character, costume, width, height)