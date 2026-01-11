import pygame
import os
import random


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
            # Return a placeholder surface if sheet not loaded
            surface = pygame.Surface((width, height))
            surface.fill((255, 0, 255))  # Magenta placeholder
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
        """
        frames: list of pygame.Surface sprites
        frame_duration: time in seconds per frame
        loop: whether animation should loop
        """
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
    Structure: assets/sprites/{character}/{costume}/{animation}.png

    Supports multiple variants for animations (e.g., different punch animations)
    Layout for variants:
    - Rows 0-3: Variant 1 (down, left, right, up)
    - Rows 4-7: Variant 2 (down, left, right, up)
    - Rows 8-11: Variant 3 (down, left, right, up)
    etc.
    """

    def __init__(self, character_name, costume_name, sprite_width, sprite_height):
        """
        character_name: e.g., 'goku', 'vegeta'
        costume_name: e.g., 'base', 'ssj', 'gi'
        sprite_width/height: size of each frame
        """
        self.character_name = character_name
        self.costume_name = costume_name
        self.sprite_width = sprite_width
        self.sprite_height = sprite_height

        # Base path for this character/costume
        self.base_path = f"assets/sprites/{character_name}/{costume_name}"

        # Animations dictionary: {name_direction: Animation or list of Animation variants}
        self.animations = {}
        self.current_animation = None
        self.current_direction = 'down'

        # Drawing offset (to center sprite on entity position)
        self.offset_x = sprite_width // 2
        self.offset_y = sprite_height // 2

    def load_animation(self, animation_name, direction, frame_duration=0.1, loop=True, num_variants=1):
        """
        Load an animation from file with support for multiple variants

        Looks for: assets/sprites/{character}/{costume}/{animation}.png

        Expected layout for variants:
        - Rows 0-3: Variant 1 (down=0, left=1, right=2, up=3)
        - Rows 4-7: Variant 2 (down=4, left=5, right=6, up=7)
        - etc.

        num_variants: How many variants to load (each variant uses 4 rows)
        """
        filepath = f"{self.base_path}/{animation_name}.png"

        if not os.path.exists(filepath):
            print(f"Warning: Animation file not found: {filepath}")
            return False

        # Load sprite sheet
        sprite_sheet = SpriteSheet(filepath)

        # Direction mapping within each 4-row variant set
        direction_map = {'down': 0, 'left': 1, 'right': 2, 'up': 3}
        direction_offset = direction_map.get(direction, 0)

        key = f"{animation_name}_{direction}"
        variants = []

        # Load each variant
        for variant_index in range(num_variants):
            # Calculate row: each variant takes 4 rows
            row = (variant_index * 4) + direction_offset

            # Get all frames from this row
            frames = sprite_sheet.get_all_frames(self.sprite_width, self.sprite_height, row)

            if not frames:
                print(f"Warning: No frames loaded from {filepath} row {row}")
                continue

            # Create animation for this variant
            animation = Animation(frames, frame_duration, loop)
            variants.append(animation)

        if not variants:
            print(f"Warning: No variants loaded for {animation_name}_{direction}")
            return False

        # Store variants
        if len(variants) == 1:
            # Single variant - store directly
            self.animations[key] = variants[0]
        else:
            # Multiple variants - store as list
            self.animations[key] = variants

        return True

    def load_animation_all_directions(self, animation_name, frame_duration=0.1, loop=True, num_variants=1):
        """Load animation for all 4 directions from a single file"""
        for direction in ['down', 'left', 'right', 'up']:
            self.load_animation(animation_name, direction, frame_duration, loop, num_variants)

    def set_animation(self, name, direction=None):
        """
        Set the current animation
        If the animation has multiple variants, randomly selects one
        """
        if direction:
            self.current_direction = direction

        key = f"{name}_{self.current_direction}"

        if key in self.animations:
            # Check if we're already playing this animation
            if self.current_animation == key:
                return

            self.current_animation = key

            # If this is a list of variants, pick a random one
            if isinstance(self.animations[key], list):
                # Store the variant index so we can access it consistently
                selected_variant = random.choice(self.animations[key])
                # Reset the selected variant
                selected_variant.reset()
            else:
                # Single animation - just reset it
                self.animations[key].reset()
        else:
            # Fallback: try to load it
            if self.load_animation(name, self.current_direction):
                self.current_animation = key
            else:
                print(f"Warning: Animation '{key}' not found")

    def update(self, dt):
        """Update current animation"""
        if self.current_animation and self.current_animation in self.animations:
            anim = self.animations[self.current_animation]

            # Handle both single animations and variant lists
            if isinstance(anim, list):
                # Update all variants (so they stay in sync if we switch)
                for variant in anim:
                    variant.update(dt)
            else:
                anim.update(dt)

    def draw(self, screen, x, y, camera=None, scale=1.0):
        """
        Draw the sprite
        x, y: world coordinates of entity center
        scale: scaling factor (1.0 = normal, 2.0 = double size, 0.5 = half size)
        """
        if not self.current_animation or self.current_animation not in self.animations:
            # Draw placeholder if no animation
            screen_x = x - camera.x if camera else x
            screen_y = y - camera.y if camera else y
            pygame.draw.rect(screen, (255, 0, 255),
                             (screen_x - self.offset_x,
                              screen_y - self.offset_y,
                              self.sprite_width,
                              self.sprite_height))
            return

        anim = self.animations[self.current_animation]

        # Get the current frame from the appropriate animation
        if isinstance(anim, list):
            # For variants, we need to pick which one to display
            # We'll use the first one that's currently active (not finished)
            # Or just the first one if all are finished or looping
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
            screen_x = x - camera.x if camera else x
            screen_y = y - camera.y if camera else y

            # Scale if needed
            if scale != 1.0:
                scaled_width = int(self.sprite_width * scale)
                scaled_height = int(self.sprite_height * scale)
                frame = pygame.transform.scale(frame, (scaled_width, scaled_height))
                offset_x = scaled_width // 2
                offset_y = scaled_height // 2
            else:
                offset_x = self.offset_x
                offset_y = self.offset_y

            # Draw sprite centered on position
            screen.blit(frame, (screen_x - offset_x,
                                screen_y - offset_y))

    def is_animation_finished(self):
        """Check if current animation has finished (for non-looping animations)"""
        if self.current_animation and self.current_animation in self.animations:
            anim = self.animations[self.current_animation]

            if isinstance(anim, list):
                # For variants, check if all are finished
                return all(variant.finished for variant in anim)
            else:
                return anim.finished
        return False


class CharacterSpriteLoader:
    """
    Helper to load all animations for a character
    """

    @staticmethod
    def load_character(character_name, costume_name, sprite_width, sprite_height):
        """
        Load a character with all standard animations
        Returns an AnimatedSprite with all animations loaded
        """
        sprite = AnimatedSprite(character_name, costume_name, sprite_width, sprite_height)

        # Standard animations with their settings
        # Format: (name, frame_duration, loop, num_variants)
        animations = [
            ('idle', 0.3, True, 1),
            ('walk', 0.1, True, 1),
            ('run', 0.08, True, 1),
            ('melee', 0.1, False, 3),  # 3 variants for punch!
            ('melee2', 0.1, False, 2),  # 2 variants for kick
            ('melee3', 0.1, False, 1),  # 1 variant for heavy melee
            ('hurt', 0.1, False, 1),
            ('death', 0.15, False, 1),
            ('charge', 0.1, True, 1),
            ('block', 0.2, True, 1),
        ]

        # Try to load each animation
        for anim_name, duration, loop, num_variants in animations:
            sprite.load_animation_all_directions(anim_name, duration, loop, num_variants)

        # Set default animation
        sprite.set_animation('idle', 'down')

        return sprite

    @staticmethod
    def list_available_characters():
        """List all available characters"""
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
        """List all available costumes for a character"""
        char_path = f"assets/sprites/{character_name}"
        if not os.path.exists(char_path):
            return []

        costumes = []
        for item in os.listdir(char_path):
            costume_path = os.path.join(char_path, item)
            if os.path.isdir(costume_path):
                costumes.append(item)
        return costumes


# Convenience function for quick setup
def create_character_sprite(character, costume='base', width=32, height=32):
    """
    Quick function to create a character sprite

    Example:
        player_sprite = create_character_sprite('goku', 'base')
        enemy_sprite = create_character_sprite('frieza', 'final_form')
    """
    return CharacterSpriteLoader.load_character(character, costume, width, height)