"""
Bitmap Font System

Renders text using individual character sprite images organised in
subdirectories.  Supports upper-case letters (A–Z), lower-case letters
(a–z), digits (0–9), and a small set of special characters (! ? . ,).

Expected directory layout
-------------------------
font_directory/
    uppercase/   A.png  B.png  ...
    lowercase/   a.png  b.png  ...
    numbers/     0.png  1.png  ...
    special/     exclamation.png  question.png  period.png  comma.png

Basic usage
-----------
    font = BitmapFont('assets/ui/font', letter_spacing=2, scale=1.0)
    surface = font.render("Hello World!")
    screen.blit(surface, (x, y))
"""

import pygame
import os


class BitmapFont:
    """
    Custom bitmap font that composites per-character sprites into a single
    surface on each render call.  Falls back to pygame's built-in font when
    the sprite directory cannot be found or yields no usable characters.
    """

    def __init__(self, font_directory, letter_spacing=2, scale=1.0):
        """
        Initialise and load all character sprites.

        Args:
            font_directory: Path to the root directory containing the
                            uppercase/, lowercase/, numbers/, and special/
                            subdirectories.
            letter_spacing: Horizontal gap between characters in pixels
                            (at the target scale).  Default: 2.
            scale:          Uniform scale factor applied to every sprite.
                            Default: 1.0 (no scaling).
        """
        self.font_directory  = font_directory
        self.letter_spacing  = letter_spacing
        self.scale           = scale
        self.letters         = {}   # char → pygame.Surface
        self.fallback_font   = None
        self._load_letters()

    # ── Sprite loading ─────────────────────────────────────────────────────────

    def _load_sprite(self, filepath):
        """
        Load a single sprite image and apply the current scale factor.

        Returns:
            The (optionally scaled) Surface, or None if loading fails.
        """
        try:
            sprite = pygame.image.load(filepath).convert_alpha()

            if self.scale != 1.0:
                new_width  = int(sprite.get_width()  * self.scale)
                new_height = int(sprite.get_height() * self.scale)
                sprite = pygame.transform.scale(sprite, (new_width, new_height))

            return sprite
        except Exception as e:
            print(f"Error loading sprite {filepath}: {e}")
            return None

    def _load_letters(self):
        """
        Walk each subdirectory and populate the internal character map.

        Prints a summary on success, or a warning if no characters were
        loaded and the fallback renderer will be used instead.
        """
        if not os.path.exists(self.font_directory):
            print(f"Warning: Font directory not found: {self.font_directory}")
            print("Bitmap font will use fallback rendering.")
            return

        # Upper-case letters
        uppercase_dir = os.path.join(self.font_directory, 'uppercase')
        if os.path.exists(uppercase_dir):
            for char in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
                filepath = os.path.join(uppercase_dir, f'{char}.png')
                if os.path.exists(filepath):
                    sprite = self._load_sprite(filepath)
                    if sprite:
                        self.letters[char] = sprite

        # Lower-case letters
        lowercase_dir = os.path.join(self.font_directory, 'lowercase')
        if os.path.exists(lowercase_dir):
            for char in 'abcdefghijklmnopqrstuvwxyz':
                filepath = os.path.join(lowercase_dir, f'{char}.png')
                if os.path.exists(filepath):
                    sprite = self._load_sprite(filepath)
                    if sprite:
                        self.letters[char] = sprite

        # Digits
        numbers_dir = os.path.join(self.font_directory, 'numbers')
        if os.path.exists(numbers_dir):
            for char in '0123456789':
                filepath = os.path.join(numbers_dir, f'{char}.png')
                if os.path.exists(filepath):
                    sprite = self._load_sprite(filepath)
                    if sprite:
                        self.letters[char] = sprite

        # Special characters
        special_dir = os.path.join(self.font_directory, 'special')
        if os.path.exists(special_dir):
            special_char_map = {
                '!': 'exclamation.png',
                '?': 'question.png',
                '.': 'period.png',
                ',': 'comma.png',
            }
            for char, filename in special_char_map.items():
                filepath = os.path.join(special_dir, filename)
                if os.path.exists(filepath):
                    sprite = self._load_sprite(filepath)
                    if sprite:
                        self.letters[char] = sprite

        if self.letters:
            print(f"Bitmap font loaded successfully: {len(self.letters)} characters.")
        else:
            print("Warning: No bitmap font characters loaded — using fallback font.")

    # ── Rendering ─────────────────────────────────────────────────────────────

    def render(self, text, color=None):
        """
        Render a single line of *text* to a new Surface.

        Characters are aligned to a shared baseline (bottom-aligned) so
        that glyphs of different heights sit on the same visual line.

        Args:
            text:  The string to render.
            color: Optional colour tint passed to the fallback renderer.
                   Has no effect when the bitmap font is active.

        Returns:
            A pygame.Surface containing the rendered text.
        """
        if not self.letters:
            if self.fallback_font is None:
                self.fallback_font = pygame.font.Font(None, int(28 * self.scale))
            return self.fallback_font.render(text, True, color or (255, 255, 255))

        # Calculate the required surface dimensions.
        total_width = 0
        max_height  = 0

        for char in text:
            if char in self.letters:
                sprite      = self.letters[char]
                total_width += sprite.get_width() + self.letter_spacing
                max_height   = max(max_height, sprite.get_height())
            elif char == ' ':
                total_width += int(8 * self.scale)
            else:
                total_width += int(6 * self.scale)  # Placeholder for unknown characters.

        # Remove the trailing inter-character gap.
        if total_width > 0:
            total_width -= self.letter_spacing

        if total_width == 0 or max_height == 0:
            return pygame.Surface((1, 1), pygame.SRCALPHA)

        text_surface = pygame.Surface((total_width, max_height), pygame.SRCALPHA)
        text_surface.fill((0, 0, 0, 0))

        # Blit each character, bottom-aligned so all glyphs share a baseline.
        x_offset = 0
        for char in text:
            if char in self.letters:
                sprite   = self.letters[char]
                y_offset = max_height - sprite.get_height()
                text_surface.blit(sprite, (x_offset, y_offset))
                x_offset += sprite.get_width() + self.letter_spacing
            elif char == ' ':
                x_offset += int(8 * self.scale)
            else:
                x_offset += int(6 * self.scale)

        return text_surface

    # ── Size helpers ──────────────────────────────────────────────────────────

    def get_text_size(self, text):
        """
        Return the pixel dimensions of the rendered *text* as (width, height).
        """
        return self.render(text).get_size()

    def get_text_width(self, text):
        """Return the pixel width of the rendered *text*."""
        return self.get_text_size(text)[0]

    def get_text_height(self, text):
        """Return the pixel height of the rendered *text*."""
        return self.get_text_size(text)[1]

    def get_line_height(self):
        """
        Return the standard line height for this font, defined as the
        tallest loaded character glyph.  Falls back to a scaled estimate
        when no characters have been loaded.
        """
        if not self.letters:
            return int(28 * self.scale)
        return max(sprite.get_height() for sprite in self.letters.values())

    # ── Multi-line ────────────────────────────────────────────────────────────

    def render_multiline(self, text, line_spacing=4, color=None):
        """
        Render *text* that may contain newline characters.

        Each line is rendered independently and then composited onto a
        single surface with consistent vertical spacing.

        Args:
            text:         The string to render (use '\\n' for line breaks).
            line_spacing: Additional pixels between lines.  Default: 4.
            color:        Optional colour tint forwarded to render().

        Returns:
            A pygame.Surface containing all lines.
        """
        lines = text.split('\n')
        if not lines:
            return pygame.Surface((1, 1), pygame.SRCALPHA)

        line_surfaces = [self.render(line, color) for line in lines]

        max_width    = max(surface.get_width() for surface in line_surfaces)
        line_height  = self.get_line_height()
        total_height = len(lines) * line_height + (len(lines) - 1) * line_spacing

        result = pygame.Surface((max_width, total_height), pygame.SRCALPHA)
        result.fill((0, 0, 0, 0))

        y_offset = 0
        for line_surface in line_surfaces:
            result.blit(line_surface, (0, y_offset))
            y_offset += line_height + line_spacing

        return result