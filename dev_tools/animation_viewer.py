import pygame
import os
from tkinter import filedialog
import tkinter as tk


class AnimationPreview:
    """
    Standalone animation preview tool for sprite sheets
    Detects animations the same way sprite_system.py does
    """

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False

        # Animation state
        self.current_file = None
        self.sprite_sheet = None  # Loaded sprite sheet surface
        self.canvas_width = 0
        self.canvas_height = 0

        # Animation detection
        self.detected_animations = []
        self.current_animation = None
        self.current_direction = 'down'

        # Playback state
        self.playing = False
        self.frame_index = 0
        self.frame_time = 0
        self.frame_duration = 0.1
        self.speed = 1.0
        self.loop = True

        # Animation data
        self.frames = []  # Current direction frames
        self.sprite_width = 32
        self.sprite_height = 32
        self.preview_scale = 3.0

        # Fonts
        pygame.font.init()
        self.font_small = pygame.font.Font(None, 16)
        self.font_medium = pygame.font.Font(None, 20)
        self.font_large = pygame.font.Font(None, 24)

    def toggle(self):
        """Toggle preview visibility"""
        self.active = not self.active
        if self.active:
            print("🎬 Animation Preview opened")

    def import_sprite_sheet(self):
        """Import a sprite sheet"""
        root = tk.Tk()
        root.withdraw()

        file_path = filedialog.askopenfilename(
            title="Import Sprite Sheet",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp"),
                ("PNG files", "*.png"),
                ("All files", "*.*")
            ]
        )

        root.destroy()

        if file_path:
            try:
                self.sprite_sheet = pygame.image.load(file_path).convert_alpha()
                self.canvas_width = self.sprite_sheet.get_width()
                self.canvas_height = self.sprite_sheet.get_height()
                self.current_file = file_path

                self.detect_animations()
                print(f"✓ Loaded: {os.path.basename(file_path)} ({self.canvas_width}x{self.canvas_height})")

            except Exception as e:
                print(f"✗ Error loading sprite sheet: {e}")

    def detect_animations(self):
        """Detect animation based on sprite sheet layout"""
        if not self.sprite_sheet:
            return

        # Standard animation names
        standard_animations = [
            'idle', 'walk', 'run', 'melee', 'melee2', 'melee3',
            'kiblast', 'hurt', 'death', 'charge', 'block',
            'transform', 'untransform'
        ]

        # Try to detect sprite dimensions
        common_sizes = [32, 48, 64, 96, 128]

        detected_height = None
        for size in common_sizes:
            if self.canvas_height % 4 == 0:
                rows = self.canvas_height // size
                if rows >= 4:
                    detected_height = size
                    break

        if not detected_height:
            detected_height = self.canvas_height // 4

        detected_width = detected_height

        # Try to detect animation name from filename
        filename = os.path.basename(self.current_file).lower()
        detected_name = None

        for anim_name in standard_animations:
            if anim_name in filename:
                detected_name = anim_name
                break

        if not detected_name:
            detected_name = "unknown"

        self.sprite_width = detected_width
        self.sprite_height = detected_height
        self.detected_animations = [detected_name]
        self.current_animation = detected_name

        self.load_frames()

        print(f"🎬 Detected: {detected_name} ({detected_width}x{detected_height})")

    def load_frames(self):
        """Load animation frames for current direction"""
        if not self.sprite_sheet or not self.current_animation:
            return

        direction_map = {'down': 0, 'left': 1, 'right': 2, 'up': 3}
        row = direction_map.get(self.current_direction, 0)

        num_frames = self.canvas_width // self.sprite_width

        self.frames = []
        for frame_idx in range(num_frames):
            frame_x = frame_idx * self.sprite_width
            frame_y = row * self.sprite_height

            frame_surface = pygame.Surface(
                (self.sprite_width, self.sprite_height),
                pygame.SRCALPHA
            )
            frame_surface.fill((0, 0, 0, 0))

            # Copy pixels from sprite sheet
            frame_surface.blit(
                self.sprite_sheet,
                (0, 0),
                (frame_x, frame_y, self.sprite_width, self.sprite_height)
            )

            self.frames.append(frame_surface)

        self.frame_index = 0
        self.frame_time = 0

        print(f"📽️ Loaded {len(self.frames)} frames for {self.current_direction}")

    def handle_input(self, event):
        """Handle input events"""
        if not self.active:
            return None

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.active = False
                return 'close'
            elif event.key == pygame.K_o and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                self.import_sprite_sheet()
            elif event.key == pygame.K_SPACE:
                self.playing = not self.playing

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mouse_x, mouse_y = event.pos

            # Import button
            if 60 <= mouse_x <= 160 and 10 <= mouse_y <= 40:
                self.import_sprite_sheet()
                return True

            # Play/Pause button
            if 60 <= mouse_x <= 140 and 60 <= mouse_y <= 90:
                self.playing = not self.playing
                return True

            # Stop button
            if 150 <= mouse_x <= 210 and 60 <= mouse_y <= 90:
                self.playing = False
                self.frame_index = 0
                self.frame_time = 0
                return True

            # Speed controls
            if 60 <= mouse_x <= 100 and 105 <= mouse_y <= 130:
                self.speed = max(0.1, self.speed - 0.1)
                return True

            if 110 <= mouse_x <= 150 and 105 <= mouse_y <= 130:
                self.speed = min(5.0, self.speed + 0.1)
                return True

            # Direction buttons
            directions = ['down', 'left', 'right', 'up']
            for i, direction in enumerate(directions):
                dir_x = 60 + i * 65
                if dir_x <= mouse_x <= dir_x + 60 and 155 <= mouse_y <= 180:
                    self.current_direction = direction
                    self.load_frames()
                    return True

        return None

    def update(self, dt):
        """Update animation playback"""
        if not self.active or not self.playing or not self.frames:
            return

        self.frame_time += dt * self.speed

        if self.frame_time >= self.frame_duration:
            self.frame_time = 0
            self.frame_index += 1

            if self.frame_index >= len(self.frames):
                if self.loop:
                    self.frame_index = 0
                else:
                    self.frame_index = len(self.frames) - 1
                    self.playing = False

    def draw(self, screen):
        """Draw the animation preview"""
        if not self.active:
            return

        # Dark background
        screen.fill((28, 28, 36))

        # Top bar
        pygame.draw.rect(screen, (35, 35, 42), (0, 0, self.screen_width, 50))
        pygame.draw.line(screen, (60, 60, 70), (0, 49), (self.screen_width, 49), 1)

        # Import button
        pygame.draw.rect(screen, (64, 128, 255), (60, 10, 100, 30))
        pygame.draw.rect(screen, (255, 255, 255), (60, 10, 100, 30), 1)
        text = self.font_medium.render("Import", True, (255, 255, 255))
        text_rect = text.get_rect(center=(110, 25))
        screen.blit(text, text_rect)

        # Title
        title = self.font_large.render("Animation Preview", True, (220, 220, 220))
        screen.blit(title, (self.screen_width // 2 - title.get_width() // 2, 15))

        # Left panel - controls
        panel_x = 60
        panel_y = 60

        # Play/Pause button
        play_text = "Pause" if self.playing else "Play"
        play_color = (64, 200, 64) if self.playing else (64, 128, 255)
        pygame.draw.rect(screen, play_color, (panel_x, panel_y, 80, 30))
        pygame.draw.rect(screen, (255, 255, 255), (panel_x, panel_y, 80, 30), 1)
        text = self.font_medium.render(play_text, True, (255, 255, 255))
        text_rect = text.get_rect(center=(panel_x + 40, panel_y + 15))
        screen.blit(text, text_rect)

        # Stop button
        stop_btn_x = panel_x + 90
        pygame.draw.rect(screen, (200, 64, 64), (stop_btn_x, panel_y, 60, 30))
        pygame.draw.rect(screen, (255, 255, 255), (stop_btn_x, panel_y, 60, 30), 1)
        text = self.font_medium.render("Stop", True, (255, 255, 255))
        text_rect = text.get_rect(center=(stop_btn_x + 30, panel_y + 15))
        screen.blit(text, text_rect)

        # Speed controls
        speed_y = panel_y + 45
        speed_label = self.font_small.render(f"Speed: {self.speed:.1f}x", True, (220, 220, 220))
        screen.blit(speed_label, (panel_x, speed_y))

        speed_btn_y = speed_y + 20
        pygame.draw.rect(screen, (60, 60, 70), (panel_x, speed_btn_y, 40, 25))
        pygame.draw.rect(screen, (255, 255, 255), (panel_x, speed_btn_y, 40, 25), 1)
        text = self.font_medium.render("-", True, (255, 255, 255))
        text_rect = text.get_rect(center=(panel_x + 20, speed_btn_y + 12))
        screen.blit(text, text_rect)

        pygame.draw.rect(screen, (60, 60, 70), (panel_x + 50, speed_btn_y, 40, 25))
        pygame.draw.rect(screen, (255, 255, 255), (panel_x + 50, speed_btn_y, 40, 25), 1)
        text = self.font_medium.render("+", True, (255, 255, 255))
        text_rect = text.get_rect(center=(panel_x + 70, speed_btn_y + 12))
        screen.blit(text, text_rect)

        # Direction buttons
        dir_y = speed_btn_y + 35
        dir_label = self.font_small.render("Direction:", True, (220, 220, 220))
        screen.blit(dir_label, (panel_x, dir_y))

        dir_btn_y = dir_y + 20
        directions = ['down', 'left', 'right', 'up']
        for i, direction in enumerate(directions):
            dir_x = panel_x + i * 65
            color = (64, 128, 255) if direction == self.current_direction else (60, 60, 70)
            pygame.draw.rect(screen, color, (dir_x, dir_btn_y, 60, 25))
            pygame.draw.rect(screen, (255, 255, 255), (dir_x, dir_btn_y, 60, 25), 1)
            text = self.font_small.render(direction.capitalize(), True, (255, 255, 255))
            text_rect = text.get_rect(center=(dir_x + 30, dir_btn_y + 12))
            screen.blit(text, text_rect)

        # Preview area
        preview_x = self.screen_width // 2
        preview_y = self.screen_height // 2

        if self.frames and len(self.frames) > 0:
            current_frame = self.frames[self.frame_index]

            scaled_width = int(self.sprite_width * self.preview_scale)
            scaled_height = int(self.sprite_height * self.preview_scale)

            scaled_frame = pygame.transform.scale(current_frame, (scaled_width, scaled_height))

            # Checkerboard background
            checker_size = 16
            for y in range(0, scaled_height, checker_size):
                for x in range(0, scaled_width, checker_size):
                    if (x // checker_size + y // checker_size) % 2 == 0:
                        color = (42, 42, 52)
                    else:
                        color = (48, 48, 58)
                    pygame.draw.rect(screen, color,
                                     (preview_x - scaled_width // 2 + x,
                                      preview_y - scaled_height // 2 + y,
                                      checker_size, checker_size))

            # Draw frame
            screen.blit(scaled_frame,
                        (preview_x - scaled_width // 2,
                         preview_y - scaled_height // 2))

            # Border
            pygame.draw.rect(screen, (100, 100, 110),
                             (preview_x - scaled_width // 2 - 2,
                              preview_y - scaled_height // 2 - 2,
                              scaled_width + 4, scaled_height + 4), 2)

            # Frame info
            info_text = f"Frame {self.frame_index + 1}/{len(self.frames)}"
            info = self.font_medium.render(info_text, True, (220, 220, 220))
            screen.blit(info, (preview_x - info.get_width() // 2, preview_y + scaled_height // 2 + 20))

            # Animation name
            if self.current_animation:
                anim_name = self.font_large.render(self.current_animation, True, (180, 180, 180))
                screen.blit(anim_name, (preview_x - anim_name.get_width() // 2, preview_y - scaled_height // 2 - 40))

            # Filmstrip
            filmstrip_y = preview_y + scaled_height // 2 + 60
            filmstrip_frame_size = 48
            filmstrip_spacing = 4
            filmstrip_total_width = len(self.frames) * (filmstrip_frame_size + filmstrip_spacing)
            filmstrip_start_x = preview_x - filmstrip_total_width // 2

            for i, frame in enumerate(self.frames):
                frame_x = filmstrip_start_x + i * (filmstrip_frame_size + filmstrip_spacing)

                # Checkerboard
                for y in range(0, filmstrip_frame_size, 8):
                    for x in range(0, filmstrip_frame_size, 8):
                        if (x // 8 + y // 8) % 2 == 0:
                            color = (42, 42, 52)
                        else:
                            color = (48, 48, 58)
                        pygame.draw.rect(screen, color, (frame_x + x, filmstrip_y + y, 8, 8))

                small_frame = pygame.transform.scale(frame, (filmstrip_frame_size, filmstrip_frame_size))
                screen.blit(small_frame, (frame_x, filmstrip_y))

                # Border (highlight current)
                border_color = (255, 200, 0) if i == self.frame_index else (80, 80, 90)
                border_width = 3 if i == self.frame_index else 1
                pygame.draw.rect(screen, border_color,
                                 (frame_x, filmstrip_y, filmstrip_frame_size, filmstrip_frame_size),
                                 border_width)
        else:
            # No frames loaded
            no_frames_text = self.font_large.render("No animation loaded", True, (150, 150, 150))
            screen.blit(no_frames_text,
                        (preview_x - no_frames_text.get_width() // 2,
                         preview_y - no_frames_text.get_height() // 2))

            help_text = self.font_small.render("Click 'Import' or press Ctrl+O to load a sprite sheet", True,
                                               (120, 120, 120))
            screen.blit(help_text,
                        (preview_x - help_text.get_width() // 2,
                         preview_y + 20))

        # Instructions
        instructions = [
            "Space: Play/Pause | Ctrl+O: Import | ESC: Close",
            f"Sprite Size: {self.sprite_width}x{self.sprite_height}"
        ]

        for i, text in enumerate(instructions):
            instr = self.font_small.render(text, True, (180, 180, 180))
            screen.blit(instr, (10, self.screen_height - 40 + i * 20))

        pygame.display.flip()