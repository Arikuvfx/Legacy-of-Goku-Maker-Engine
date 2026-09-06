import pygame
import os
import math
from tkinter import filedialog
import tkinter as tk
from copy import deepcopy
import pygame.gfxdraw


class SpriteEditor:
    """Sprite/pixel art editor with Paint.NET-style tools: draw, import, export."""

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False
        self.current_tab = 'canvas'

        # Performance caches
        self.checkerboard_cache = {}
        self.tool_cursors = {}
        self.tool_icons = {}

        # Initialize fonts
        pygame.font.init()
        self.fonts = {
            'small': pygame.font.Font(None, 16),
            'medium': pygame.font.Font(None, 20),
            'large': pygame.font.Font(None, 24)
        }

        # Tools configuration
        self.tools = [
            {'id': 'move', 'name': 'Move', 'label': 'M', 'shortcut': 'V', 'icon': 'move.png'},
            {'id': 'select', 'name': 'Select', 'label': 'S', 'shortcut': 'S', 'icon': 'select.png'},
            {'id': 'magicwand', 'name': 'Magic Wand', 'label': 'W', 'shortcut': 'W', 'icon': 'magicwand.png'},
            {'id': 'pencil', 'name': 'Pencil', 'label': 'P', 'shortcut': 'P', 'icon': 'pencil.png'},
            {'id': 'eraser', 'name': 'Eraser', 'label': 'E', 'shortcut': 'E', 'icon': 'eraser.png'},
            {'id': 'fill', 'name': 'Fill', 'label': 'F', 'shortcut': 'F', 'icon': 'fill.png'},
            {'id': 'eyedropper', 'name': 'Eyesdropper', 'label': 'I', 'shortcut': 'I', 'icon': 'eyedropper.png'},
        ]

        # UI state
        self.show_tool_dropdown = False
        self.show_color_wheel = False
        self.show_grid = True
        self.show_canvas_size_dialog = False

        # Tool dropdown and color wheel positions
        self.tool_dropdown_pos = (10, 60)
        self.color_wheel_pos = (0, 0)
        self.color_wheel_size = 200

        # Cursor
        self.show_custom_cursor = False
        self.custom_cursor_pos = (0, 0)

        # Load assets
        self._load_tool_icons()
        self._create_tool_cursors()

        # Canvas settings
        self.zoom_levels = [4, 8, 16, 20, 24, 32, 48, 64]
        self.zoom_index = 2
        self.pixel_size = self.zoom_levels[self.zoom_index]

        # Current canvas data
        self.canvas_width = 32
        self.canvas_height = 32
        self.canvas = [[(0, 0, 0, 0) for _ in range(32)] for _ in range(32)]

        # Canvas position
        self.canvas_offset_x = 0
        self.canvas_offset_y = 0

        # Animation
        self._init_animation_state()

        # History
        self.history = []
        self.history_index = -1
        self.max_history = 1000

        # Editor state
        self.current_tool = 'pencil'
        self.current_color = (255, 255, 255)
        self.secondary_color = (0, 0, 0)
        self.is_drawing = False
        self.last_draw_pos = None

        # Recent colors
        self.recent_colors = []
        self.max_recent_colors = 12
        self.color_history = {}

        # Tool options
        self.tool_options = {
            'brush_size': 1,
            'antialiasing': False,
            'opacity': 100,
            'fill_mode': 'contiguous',
            'tolerance': 20,
        }

        # Selection
        self.selection_start = None
        self.selection_end = None
        self.selection_preview_end = None
        self.selected_pixels = []
        self.moving_selection = False
        self.selection_offset = (0, 0)

        # Paste
        self._init_paste_state()

        # Copy/paste
        self.clipboard = None
        self.clipboard_width = 0
        self.clipboard_height = 0
        self.clipboard_origin = (0, 0)
        self.paste_preview_pos = None

        # Magic wand
        self.magic_wand_selection = []

        # Move tool
        self.move_start_pos = None
        self.moving_canvas = False

        # File
        self.current_file = None
        self.unsaved_changes = False

        # UI buttons
        self.button_states = {
            'new': False, 'import': False, 'export': False,
            'canvas_tab': False, 'animation_tab': False
        }

        # Multi-canvas
        self.canvases = []
        self.current_canvas_index = 0
        self.canvas_counter = 1
        self.max_canvases = 10

        # Input dialog
        self.canvas_size_input = ""
        self.canvas_size_field = "width"
        self.temp_width = 32

        # Color wheel
        self.selected_hue = 0
        self.selected_saturation = 1.0
        self.selected_value = 1.0
        self.dragging_color_wheel = False
        self.dragging_value_slider = False

        self.show_hex_input = False
        self.hex_input_text = ""
        self.hex_input_active = False

        self.dragging_h_slider = False
        self.dragging_s_slider = False
        self.dragging_v_slider = False

        # Load color wheel icon
        self.color_wheel_icon = None
        self._load_color_wheel_icon()

        # RGB Sliders
        self.dragging_rgb_slider = None  # 'r', 'g', or 'b'

        # Initialize HSV from current color
        h, s, v = self.rgb_to_hsv(*self.current_color)
        self.selected_hue = h
        self.selected_saturation = s
        self.selected_value = v

        # Tolerance slider state
        self.dragging_tolerance_slider = False

        # Initialize colors
        self._add_to_recent_colors((255, 255, 255))
        self._add_to_recent_colors((0, 0, 0))
        self._add_to_recent_colors((255, 0, 0))
        self._add_to_recent_colors((0, 255, 0))
        self._add_to_recent_colors((0, 0, 255))

        # Create and center initial canvas
        self._create_initial_canvas()
        self._center_canvas()
        self._save_to_history()

    def _init_animation_state(self):
        """Initialize animation-related state variables"""
        self.anim_detected_animations = []
        self.anim_current_animation = None
        self.anim_current_direction = 'down'
        self.anim_playing = False
        self.anim_frame_index = 0
        self.anim_frame_time = 0
        self.anim_frame_duration = 0.1
        self.anim_speed = 1.0
        self.anim_loop = True
        self.anim_frames = []
        self.anim_sprite_width = 32
        self.anim_sprite_height = 32
        self.anim_preview_scale = 3.0
        self.anim_flip_left = True

    def _init_paste_state(self):
        """Initialize paste-related state variables"""
        self.paste_selection = None
        self.paste_selection_rect = None
        self.paste_selection_offset = (0, 0)
        self.paste_selection_original = None
        self.paste_selection_active = False
        self.paste_rotation = 0
        self.paste_scale = 1.0
        self.paste_interpolation = False
        self.paste_saved_to_history = False
        self.paste_handles = []
        self.dragging_handle = None
        self.rotation_handle_distance = 15
        self.scaling_handles_active = True

    # === Asset Loading ===

    def _load_color_wheel_icon(self):
        """Load or create color wheel icon"""
        icons_dir = "assets/ui/dev_menu/icons/"
        icon_path = os.path.join(icons_dir, "colorwheel.png")

        if os.path.exists(icon_path):
            try:
                self.color_wheel_icon = pygame.image.load(icon_path).convert_alpha()
                self.color_wheel_icon = pygame.transform.scale(self.color_wheel_icon, (30, 30))
            except:
                self._create_color_wheel_icon()
        else:
            self._create_color_wheel_icon()

    def _load_tool_icons(self):
        """Load tool icons from assets directory"""
        icons_dir = "assets/ui/dev_menu/icons/"

        for tool in self.tools:
            icon_path = os.path.join(icons_dir, tool['icon'])
            if os.path.exists(icon_path):
                try:
                    icon = pygame.image.load(icon_path).convert_alpha()
                    self.tool_icons[tool['id']] = pygame.transform.scale(icon, (28, 28))
                except:
                    self._create_fallback_icon(tool)
            else:
                self._create_fallback_icon(tool)

    def _create_fallback_icon(self, tool):
        """Create a fallback icon for a tool"""
        icon = pygame.Surface((24, 24), pygame.SRCALPHA)
        center = 12

        # Draw different icons based on tool type
        if tool['id'] == 'move':
            pygame.draw.polygon(icon, (255, 255, 255),
                                [(center, 4), (center + 8, center), (center, 20)])
        elif tool['id'] == 'select':
            pygame.draw.rect(icon, (255, 255, 255), (6, 6, 12, 12), 2)
        elif tool['id'] == 'magicwand':
            pygame.draw.circle(icon, (255, 255, 255), (center, center), 3)
            for i in range(8):
                angle = math.pi * i / 4
                x1 = center + math.cos(angle) * 5
                y1 = center + math.sin(angle) * 5
                x2 = center + math.cos(angle) * 9
                y2 = center + math.sin(angle) * 9
                pygame.draw.line(icon, (255, 255, 255), (x1, y1), (x2, y2), 1)
        elif tool['id'] == 'pencil':
            pygame.draw.rect(icon, (255, 255, 255), (6, 8, 10, 8))
            pygame.draw.polygon(icon, (255, 100, 100),
                                [(16, 8), (20, center), (16, 16)])
        elif tool['id'] == 'eraser':
            pygame.draw.rect(icon, (255, 255, 255), (6, 6, 12, 12))
            pygame.draw.line(icon, (200, 100, 100), (8, 8), (16, 16), 2)
            pygame.draw.line(icon, (200, 100, 100), (16, 8), (8, 16), 2)
        elif tool['id'] == 'fill':
            pygame.draw.polygon(icon, (255, 255, 255),
                                [(8, 8), (16, 8), (18, 12), (6, 12)])
            pygame.draw.circle(icon, (100, 150, 255), (center, 18), 3)
        elif tool['id'] == 'eyedropper':
            pygame.draw.line(icon, (255, 255, 255), (center, 4), (center, 12), 2)
            pygame.draw.circle(icon, (255, 255, 255), (center, 16), 4)
            pygame.draw.polygon(icon, (255, 255, 255),
                                [(center - 2, 4), (center + 2, 4), (center, 8)])
        else:
            text = self.fonts['small'].render(tool['label'], True, (255, 255, 255))
            text_rect = text.get_rect(center=(center, center))
            icon.blit(text, text_rect)

        self.tool_icons[tool['id']] = icon

    def _create_tool_cursors(self):
        """Create custom cursors for tools"""
        # Create cursors only for tools that need them
        cursor_tools = ['pencil', 'fill', 'eyedropper', 'magicwand', 'eraser']
        for tool_id in cursor_tools:
            if tool_id not in self.tool_cursors:
                cursor = pygame.Surface((20, 20), pygame.SRCALPHA)
                if tool_id in self.tool_icons:
                    # Scale down the icon for cursor
                    cursor.blit(pygame.transform.scale(self.tool_icons[tool_id], (16, 16)), (0, 0))
                self.tool_cursors[tool_id] = cursor

    # === Canvas Management ===

    def _create_initial_canvas(self):
        """Create the first canvas"""
        canvas_data = {
            'id': 0, 'name': 'Canvas 1', 'width': 32, 'height': 32,
            'data': [[(0, 0, 0, 0) for _ in range(32)] for _ in range(32)],
            'history': [], 'history_index': -1, 'file_path': None,
            'unsaved': False, 'zoom_index': 2, 'pixel_size': 16,
            'canvas_offset_x': 0, 'canvas_offset_y': 0,
        }
        self.canvases.append(canvas_data)
        self._load_canvas(0)

    def _load_canvas(self, index):
        """Load a canvas by index"""
        if 0 <= index < len(self.canvases):
            canvas = self.canvases[index]
            self.current_canvas_index = index
            self.canvas_width = canvas['width']
            self.canvas_height = canvas['height']
            self.canvas = canvas['data']
            self.history = canvas['history']
            self.history_index = canvas['history_index']
            self.current_file = canvas['file_path']
            self.unsaved_changes = canvas['unsaved']
            self.zoom_index = canvas.get('zoom_index', 2)
            self.pixel_size = canvas.get('pixel_size', 16)
            self.canvas_offset_x = canvas.get('canvas_offset_x', 0)
            self.canvas_offset_y = canvas.get('canvas_offset_y', 0)

            if self.canvas_offset_x == 0 and self.canvas_offset_y == 0:
                self._center_canvas()

    def _save_current_canvas(self):
        """Save current canvas data back to the list"""
        if 0 <= self.current_canvas_index < len(self.canvases):
            canvas = self.canvases[self.current_canvas_index]
            canvas.update({
                'width': self.canvas_width,
                'height': self.canvas_height,
                'data': self.canvas,
                'history': self.history,
                'history_index': self.history_index,
                'file_path': self.current_file,
                'unsaved': self.unsaved_changes,
                'zoom_index': self.zoom_index,
                'pixel_size': self.pixel_size,
                'canvas_offset_x': self.canvas_offset_x,
                'canvas_offset_y': self.canvas_offset_y,
            })

    def create_new_canvas(self, width=32, height=32):
        """Create a new canvas"""
        if len(self.canvases) >= self.max_canvases:
            print(f"⚠️ Maximum {self.max_canvases} canvases reached")
            return False

        self._save_current_canvas()

        canvas_data = {
            'id': self.canvas_counter,
            'name': f'Canvas {self.canvas_counter + 1}',
            'width': width, 'height': height,
            'data': [[(0, 0, 0, 0) for _ in range(width)] for _ in range(height)],
            'history': [], 'history_index': -1, 'file_path': None,
            'unsaved': False, 'zoom_index': 2, 'pixel_size': 16,
            'canvas_offset_x': 0, 'canvas_offset_y': 0,
        }
        self.canvases.append(canvas_data)
        self.canvas_counter += 1
        self._load_canvas(len(self.canvases) - 1)
        self._save_to_history()
        print(f"📄 Created new canvas: {canvas_data['name']}")
        return True

    def close_canvas(self, index):
        """Close a canvas by index"""
        if len(self.canvases) <= 1:
            print("⚠️ Cannot close the last canvas")
            return False

        if 0 <= index < len(self.canvases):
            self.canvases.pop(index)

            if index == self.current_canvas_index:
                new_index = max(0, index - 1) if index > 0 else 0
                self._load_canvas(new_index)
            elif index < self.current_canvas_index:
                self.current_canvas_index -= 1

            return True
        return False

    # === Core Editor Functions ===

    def _center_canvas(self):
        """Center the canvas on screen and auto-zoom to fit"""
        # Account for UI elements at the top
        menu_bar_height = 50
        tab_bar_height = 35
        status_bar_height = 30

        total_top_ui_height = menu_bar_height + tab_bar_height

        margin = 100
        available_width = self.screen_width - margin * 2
        available_height = self.screen_height - total_top_ui_height - status_bar_height - margin * 2

        # Find optimal zoom that fits within available space
        self.zoom_index = 2  # Default zoom
        self.pixel_size = self.zoom_levels[self.zoom_index]

        # Try to find a zoom level that fits
        for i in range(len(self.zoom_levels) - 1, -1, -1):
            test_size = self.zoom_levels[i]
            if (self.canvas_width * test_size <= available_width and
                    self.canvas_height * test_size <= available_height):
                self.zoom_index = i
                self.pixel_size = test_size
                break

        # Calculate centered position
        canvas_pixel_width = self.canvas_width * self.pixel_size
        canvas_pixel_height = self.canvas_height * self.pixel_size

        # Center horizontally
        self.canvas_offset_x = (self.screen_width - canvas_pixel_width) // 2

        # Center vertically between tab bar and status bar
        self.canvas_offset_y = total_top_ui_height + (
                self.screen_height - total_top_ui_height - status_bar_height - canvas_pixel_height) // 2

        # Ensure canvas is within bounds
        self._clamp_canvas_position()

    def _save_to_history(self):
        """Save current canvas state to history"""
        # Don't save if unchanged
        if self.history_index >= 0:
            current_state = self._canvas_to_string()
            last_state = self._canvas_to_string(self.history[self.history_index])
            if current_state == last_state:
                return

        # Remove forward history
        if self.history_index < len(self.history) - 1:
            self.history = self.history[:self.history_index + 1]

        # Add new state
        self.history.append(deepcopy(self.canvas))
        self.history_index += 1

        # Limit history size
        if len(self.history) > self.max_history:
            self.history.pop(0)
            self.history_index -= 1

    def _canvas_to_string(self, canvas_data=None):
        """Convert canvas to string for comparison"""
        if canvas_data is None:
            canvas_data = self.canvas
        return "".join(str(pixel) for row in canvas_data for pixel in row)

    def undo(self):
        """Undo last action"""
        if self.history_index > 0:
            self.history_index -= 1
            self.canvas = deepcopy(self.history[self.history_index])
            self.unsaved_changes = True

            if self.paste_selection_active:
                self.cancel_paste()

            print(f"↶ Undo (state {self.history_index + 1}/{len(self.history)})")
            return True
        print("✗ Nothing to undo")
        return False

    def redo(self):
        """Redo last undone action"""
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.canvas = deepcopy(self.history[self.history_index])
            self.unsaved_changes = True

            if self.paste_selection_active:
                self.cancel_paste()

            print(f"↷ Redo (state {self.history_index + 1}/{len(self.history)})")
            return True
        print("✗ Nothing to redo")
        return False

    def _add_to_recent_colors(self, color):
        """Add a color to recent colors palette"""
        if color in self.recent_colors:
            self.recent_colors.remove(color)
        self.recent_colors.append(color)

        if len(self.recent_colors) > self.max_recent_colors:
            self.recent_colors.pop(0)

        self.color_history[color] = self.color_history.get(color, 0) + 1

    # === File Operations ===

    def prompt_canvas_size(self):
        """Show dialog to set canvas size"""
        self.show_canvas_size_dialog = True
        self.canvas_size_input = str(self.canvas_width)
        self.canvas_size_field = "width"
        self.temp_width = self.canvas_width

    def apply_canvas_size(self, width, height):
        """Create new canvas with specified size"""
        self.create_new_canvas(
            max(1, min(512, width)),
            max(1, min(512, height))
        )
        self.show_canvas_size_dialog = False
        self._center_canvas()
        print(f"📐 Canvas resized to {self.canvas_width}x{self.canvas_height}")

    def import_image(self):
        """Import an image file"""
        root = tk.Tk()
        root.withdraw()

        file_path = filedialog.askopenfilename(
            title="Import Image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp *.gif *.tga"),
                ("PNG files", "*.png"),
                ("All files", "*.*")
            ]
        )
        root.destroy()

        if file_path:
            try:
                img = pygame.image.load(file_path).convert_alpha()
                img_width, img_height = img.get_size()

                self.canvas_width = img_width
                self.canvas_height = img_height
                self.canvas = [[(0, 0, 0, 0) for _ in range(img_width)] for _ in range(img_height)]

                unique_colors = set()
                for y in range(img_height):
                    for x in range(img_width):
                        color = img.get_at((x, y))
                        rgba = (color.r, color.g, color.b, color.a)
                        self.canvas[y][x] = rgba

                        if color.a > 0:
                            unique_colors.add((color.r, color.g, color.b))

                for color in unique_colors:
                    self._add_to_recent_colors(color)

                self.current_file = file_path
                self.unsaved_changes = False
                self._center_canvas()
                self._save_to_history()

                # Update canvas name
                if 0 <= self.current_canvas_index < len(self.canvases):
                    filename = os.path.splitext(os.path.basename(file_path))[0]
                    self.canvases[self.current_canvas_index]['name'] = filename

                self._save_current_canvas()

                if self.current_tab == 'animation':
                    self._detect_animations()

                print(f"✓ Imported: {os.path.basename(file_path)} ({img_width}x{img_height})")

            except Exception as e:
                print(f"✗ Error importing image: {e}")

    def export_image(self, file_path=None):
        """Export canvas as image file"""
        if not file_path:
            root = tk.Tk()
            root.withdraw()

            file_path = filedialog.asksaveasfilename(
                title="Export Image",
                defaultextension=".png",
                filetypes=[("PNG files", "*.png"), ("BMP files", "*.bmp"), ("All files", "*.*")]
            )
            root.destroy()

        if file_path:
            try:
                surface = pygame.Surface((self.canvas_width, self.canvas_height), pygame.SRCALPHA)
                surface.fill((0, 0, 0, 0))

                for y in range(self.canvas_height):
                    for x in range(self.canvas_width):
                        color = self.canvas[y][x]
                        if color[3] > 0:
                            surface.set_at((x, y), color)

                pygame.image.save(surface, file_path)
                self.current_file = file_path
                self.unsaved_changes = False
                print(f"✓ Exported: {os.path.basename(file_path)}")

            except Exception as e:
                print(f"✗ Error exporting image: {e}")

    # === Drawing Tools ===

    def get_canvas_pixel(self, mouse_x, mouse_y):
        """Convert screen coordinates to canvas pixel coordinates"""
        # First check if we're over any UI element that should block canvas interaction
        if self._is_mouse_over_blocking_ui(mouse_x, mouse_y):
            return None

        canvas_x = (mouse_x - self.canvas_offset_x) // self.pixel_size
        canvas_y = (mouse_y - self.canvas_offset_y) // self.pixel_size

        if 0 <= canvas_x < self.canvas_width and 0 <= canvas_y < self.canvas_height:
            return (canvas_x, canvas_y)
        return None

    def _is_mouse_over_blocking_ui(self, mouse_x, mouse_y):
        """Check if mouse is over UI elements that should block canvas interaction"""
        # Top menu bar area
        if 0 <= mouse_y <= 50:  # Top bar height
            return True

        # Canvas tabs area
        if self.current_tab == 'canvas' and 50 <= mouse_y <= 85:
            return True

        # Tool button area (10, 90, 36, 36)
        tool_x, tool_y, tool_size = 10, 90, 36
        if tool_x <= mouse_x <= tool_x + tool_size and tool_y <= mouse_y <= tool_y + tool_size:
            return True

        # Tool dropdown if open
        if self.show_tool_dropdown:
            dropdown_width, dropdown_item_height = 140, 32
            dropdown_height = len(self.tools) * dropdown_item_height
            dropdown_x, dropdown_y = tool_x, tool_y + tool_size + 2

            if dropdown_x <= mouse_x <= dropdown_x + dropdown_width and dropdown_y <= mouse_y <= dropdown_y + dropdown_height:
                return True

        # Recent colors palette
        palette_x = self.screen_width - 40
        palette_y = 90
        color_size, color_spacing = 24, 2

        for i, color in enumerate(self.recent_colors):
            cy = palette_y + i * (color_size + color_spacing)
            if palette_x <= mouse_x <= palette_x + color_size and cy <= mouse_y <= cy + color_size:
                return True

        # Color wheel button
        wheel_btn_y = palette_y + len(self.recent_colors) * (color_size + color_spacing) + 10
        if palette_x <= mouse_x <= palette_x + color_size and wheel_btn_y <= mouse_y <= wheel_btn_y + color_size:
            return True

        # Current color preview
        preview_x, preview_y, preview_size = self.screen_width - 120, 20, 30
        if preview_x <= mouse_x <= preview_x + preview_size and preview_y <= mouse_y <= preview_y + preview_size:
            return True

        # Tolerance slider (if visible)
        if self.current_tool == 'magicwand' and self.current_tab == 'canvas':
            slider_x = self.screen_width - 250
            slider_y = 15
            slider_width = 200
            slider_height = 20
            slider_rect = pygame.Rect(slider_x, slider_y, slider_width, slider_height)
            if slider_rect.collidepoint(mouse_x, mouse_y):
                return True

        # Color wheel dialog
        if self.show_color_wheel:
            panel_width, panel_height = 350, 320
            panel_x, panel_y = self.color_wheel_pos
            if panel_x <= mouse_x <= panel_x + panel_width and panel_y <= mouse_y <= panel_y + panel_height:
                return True

        # Canvas size dialog
        if self.show_canvas_size_dialog:
            return True

        return False

    def _clamp_canvas_position(self):
        """Keep canvas within reasonable bounds on screen"""
        margin = 50

        # Calculate total height of UI elements above the canvas
        menu_bar_height = 50
        tab_bar_height = 35
        status_bar_height = 30
        total_top_ui_height = menu_bar_height + tab_bar_height

        # Calculate canvas size in pixels
        canvas_pixel_width = self.canvas_width * self.pixel_size
        canvas_pixel_height = self.canvas_height * self.pixel_size

        # Clamp X position
        min_x = margin
        max_x = self.screen_width - canvas_pixel_width - margin

        if canvas_pixel_width < self.screen_width:
            # Center small canvas
            self.canvas_offset_x = max(min_x, min(max_x, self.canvas_offset_x))
        else:
            # Large canvas - keep it visible
            self.canvas_offset_x = max(min_x, min(max_x, self.canvas_offset_x))

    def draw_pixel(self, x, y):
        """Draw a pixel on the canvas"""
        if 0 <= x < self.canvas_width and 0 <= y < self.canvas_height:
            if self.current_tool == 'pencil':
                self.canvas[y][x] = (*self.current_color, 255)
                self.unsaved_changes = True
                self._add_to_recent_colors(self.current_color)
            elif self.current_tool == 'eraser':
                self.canvas[y][x] = (0, 0, 0, 0)
                self.unsaved_changes = True

    def flood_fill(self, x, y, target_color, replacement_color):
        """Flood fill algorithm with alpha support"""
        if target_color == replacement_color:
            return

        stack = [(x, y)]
        visited = set()

        while stack:
            cx, cy = stack.pop()

            if (cx, cy) in visited:
                continue

            if not (0 <= cx < self.canvas_width and 0 <= cy < self.canvas_height):
                continue

            current = self.canvas[cy][cx]
            if target_color != current:
                continue

            self.canvas[cy][cx] = (*replacement_color, 255)
            visited.add((cx, cy))

            stack.append((cx + 1, cy))
            stack.append((cx - 1, cy))
            stack.append((cx, cy + 1))
            stack.append((cx, cy - 1))

        self.unsaved_changes = True
        self._add_to_recent_colors(replacement_color)
        self._save_to_history()

    def magic_wand_select(self, x, y):
        """Select contiguous area with similar color"""
        if not (0 <= x < self.canvas_width and 0 <= y < self.canvas_height):
            return

        target_color = self.canvas[y][x]
        tolerance = self.tool_options['tolerance']

        if target_color[3] == 0:
            return

        stack = [(x, y)]
        visited = set()
        selection = []

        while stack:
            cx, cy = stack.pop()

            if (cx, cy) in visited:
                continue

            if not (0 <= cx < self.canvas_width and 0 <= cy < self.canvas_height):
                continue

            current = self.canvas[cy][cx]
            if current[3] == 0:
                continue

            if not self._colors_similar(target_color[:3], current[:3], tolerance):
                continue

            selection.append((cx, cy))
            visited.add((cx, cy))

            stack.append((cx + 1, cy))
            stack.append((cx - 1, cy))
            stack.append((cx, cy + 1))
            stack.append((cx, cy - 1))

        self.magic_wand_selection = selection
        return selection

    def _colors_similar(self, color1, color2, tolerance):
        """Check if two colors are similar within tolerance"""
        if color1 is None or color2 is None:
            return False

        r1, g1, b1 = color1
        r2, g2, b2 = color2
        distance = math.sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2)
        return distance <= tolerance

    # === Selection and Copy/Paste ===

    def copy_selection(self):
        """Copy selected pixels or entire canvas to clipboard"""
        if self.selection_start and self.selection_end and self.selected_pixels:
            x1, y1 = self.selection_start
            x2, y2 = self.selection_end
            min_x, min_y = min(x1, x2), min(y1, y2)

            self.clipboard = {}
            for x, y in self.selected_pixels:
                rel_x, rel_y = x - min_x, y - min_y
                self.clipboard[(rel_x, rel_y)] = self.canvas[y][x]

            self.clipboard_width = abs(x2 - x1) + 1
            self.clipboard_height = abs(y2 - y1) + 1
            self.clipboard_origin = (min_x, min_y)
            print(f"📋 Copied {len(self.selected_pixels)} pixels ({self.clipboard_width}x{self.clipboard_height})")
        else:
            self.clipboard = {}
            for y in range(self.canvas_height):
                for x in range(self.canvas_width):
                    self.clipboard[(x, y)] = self.canvas[y][x]

            self.clipboard_width = self.canvas_width
            self.clipboard_height = self.canvas_height
            self.clipboard_origin = (0, 0)
            print(f"📋 Copied entire canvas ({self.canvas_width}x{self.canvas_height})")

    def _calculate_selected_pixels(self):
        """Calculate which pixels are in the selection rectangle"""
        if not self.selection_start or not self.selection_end:
            self.selected_pixels = []
            return

        x1, y1 = self.selection_start
        x2, y2 = self.selection_end

        min_x, max_x = min(x1, x2), max(x1, x2)
        min_y, max_y = min(y1, y2), max(y1, y2)

        self.selected_pixels = []
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                if 0 <= x < self.canvas_width and 0 <= y < self.canvas_height:
                    self.selected_pixels.append((x, y))

    def _draw_line(self, x0, y0, x1, y1):
        """Draw line between two points (Bresenham's algorithm)"""
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx, sy = 1 if x0 < x1 else -1, 1 if y0 < y1 else -1
        err = dx - dy

        while True:
            self.draw_pixel(x0, y0)

            if x0 == x1 and y0 == y1:
                break

            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    # === Paste Operations ===

    def _update_paste_handles(self):
        """Update transformation handles for paste selection"""
        if not self.paste_selection_rect:
            self.paste_handles = []
            return

        rect = self.paste_selection_rect
        x, y, w, h = rect.x, rect.y, rect.width, rect.height

        handles = []
        # Corners
        handles.append({'canvas': (x, y), 'type': 'scale_both', 'cursor': pygame.SYSTEM_CURSOR_SIZENWSE})
        handles.append({'canvas': (x + w, y), 'type': 'scale_both', 'cursor': pygame.SYSTEM_CURSOR_SIZENESW})
        handles.append({'canvas': (x, y + h), 'type': 'scale_both', 'cursor': pygame.SYSTEM_CURSOR_SIZENESW})
        handles.append({'canvas': (x + w, y + h), 'type': 'scale_both', 'cursor': pygame.SYSTEM_CURSOR_SIZENWSE})
        # Sides
        handles.append({'canvas': (x + w // 2, y), 'type': 'scale_height', 'cursor': pygame.SYSTEM_CURSOR_SIZENS})
        handles.append({'canvas': (x + w // 2, y + h), 'type': 'scale_height', 'cursor': pygame.SYSTEM_CURSOR_SIZENS})
        handles.append({'canvas': (x, y + h // 2), 'type': 'scale_width', 'cursor': pygame.SYSTEM_CURSOR_SIZEWE})
        handles.append({'canvas': (x + w, y + h // 2), 'type': 'scale_width', 'cursor': pygame.SYSTEM_CURSOR_SIZEWE})
        # Rotation handle
        handles.append({'canvas': (x + w // 2, y - 2), 'type': 'rotate', 'cursor': pygame.SYSTEM_CURSOR_HAND})

        self.paste_handles = handles

    def _get_handle_screen_size(self):
        """Get handle size based on zoom level"""
        return max(6, self.pixel_size // 2)

    def _get_handle_at_screen_pos(self, mx, my):
        """Check if mouse is over a handle"""
        if not self.paste_handles or not self.paste_selection_rect:
            return None, None

        hs = self._get_handle_screen_size()
        rect = self.paste_selection_rect
        ps = self.pixel_size

        for i, h in enumerate(self.paste_handles):
            canvas_x, canvas_y = h['canvas']

            # Calculate screen position with transformations
            screen_x = self.canvas_offset_x + canvas_x * ps
            screen_y = self.canvas_offset_y + canvas_y * ps

            if self.paste_scale != 1.0 or self.paste_rotation != 0:
                # Apply transformations
                rect_center_x = self.canvas_offset_x + (rect.x + rect.width / 2) * ps
                rect_center_y = self.canvas_offset_y + (rect.y + rect.height / 2) * ps

                dx = canvas_x - (rect.x + rect.width / 2)
                dy = canvas_y - (rect.y + rect.height / 2)

                if self.paste_scale != 1.0:
                    dx *= self.paste_scale
                    dy *= self.paste_scale

                if self.paste_rotation != 0:
                    angle_rad = math.radians(self.paste_rotation)
                    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
                    dx, dy = dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a

                screen_x = rect_center_x + dx * ps
                screen_y = rect_center_y + dy * ps

            handle_rect = pygame.Rect(screen_x - hs, screen_y - hs, hs * 2, hs * 2)
            if handle_rect.collidepoint(mx, my):
                return i, h

        return None, None

    def start_paste(self, target_x=None, target_y=None):
        """Start a paste operation with movable selection"""
        if not self.clipboard:
            return False

        self._save_to_history()
        self.paste_saved_to_history = True

        if target_x is None or target_y is None:
            target_x, target_y = self.clipboard_origin if self.clipboard_origin else (0, 0)

        # Reset transformation state
        self.paste_rotation = 0
        self.paste_scale = 1.0
        self.paste_interpolation = False

        # Create paste surface
        paste_surface = pygame.Surface((self.clipboard_width, self.clipboard_height), pygame.SRCALPHA)
        paste_surface.fill((0, 0, 0, 0))

        for (rel_x, rel_y), color in self.clipboard.items():
            if 0 <= rel_x < self.clipboard_width and 0 <= rel_y < self.clipboard_height:
                paste_surface.set_at((rel_x, rel_y), color)

        self.paste_selection = paste_surface
        self.paste_selection_original = deepcopy(self.clipboard)
        self.paste_selection_rect = pygame.Rect(target_x, target_y, self.clipboard_width, self.clipboard_height)
        self.paste_selection_active = True
        self.scaling_handles_active = True
        self.dragging_handle = None
        self.moving_selection = False

        self._update_paste_handles()

        # Clear existing selection
        self.selected_pixels = []
        self.selection_start = None
        self.selection_end = None
        self.magic_wand_selection = []

        print(f"📋 Paste started at ({target_x}, {target_y})")
        return True

    def apply_paste(self):
        """Apply the paste selection to the canvas"""
        if not self.paste_selection_active or not self.paste_selection:
            return False

        if not self.paste_saved_to_history:
            self._save_to_history()
            self.paste_saved_to_history = True

        target_x = int(self.paste_selection_rect.x)
        target_y = int(self.paste_selection_rect.y)
        applied_count = 0

        # Scale pixels if needed
        if self.paste_scale != 1.0:
            scaled_pixels = self._scale_pixels_interpolated(
                self.clipboard, self.clipboard_width, self.clipboard_height, self.paste_scale
            )
        else:
            scaled_pixels = self.clipboard

        # Paste pixels
        for (rel_x, rel_y), color in scaled_pixels.items():
            x, y = target_x + rel_x, target_y + rel_y
            if 0 <= x < self.canvas_width and 0 <= y < self.canvas_height:
                self.canvas[y][x] = color
                applied_count += 1

        self.unsaved_changes = True
        print(f"✓ Paste applied: {applied_count} pixels scale: {self.paste_scale:.1f}x")

        self._save_to_history()
        self.paste_saved_to_history = False

        # Clear paste state
        self.paste_selection = None
        self.paste_selection_rect = None
        self.paste_selection_active = False
        self.scaling_handles_active = False
        self.dragging_handle = None
        self.moving_selection = False
        self.paste_handles = []
        pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)

        return True

    def cancel_paste(self):
        """Cancel the current paste operation"""
        if self.paste_saved_to_history:
            self.undo()
            self.paste_saved_to_history = False
        else:
            self.paste_selection = None
            self.paste_selection_rect = None
            self.paste_selection_active = False
            self.paste_rotation = 0
            self.paste_scale = 1.0
            self.scaling_handles_active = False
            self.dragging_handle = None
            self.moving_selection = False
            self.paste_handles = []
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)

        print("✗ Paste cancelled")

    def _scale_pixels_interpolated(self, pixels, original_width, original_height, scale_factor):
        """Scale pixels with interpolation"""
        scale_factor = max(0.1, scale_factor)
        new_width = max(1, int(original_width * scale_factor))
        new_height = max(1, int(original_height * scale_factor))

        scaled_pixels = {}
        for y in range(new_height):
            for x in range(new_width):
                orig_x = x / scale_factor
                orig_y = y / scale_factor
                nearest_x = round(orig_x)
                nearest_y = round(orig_y)

                if (nearest_x, nearest_y) in pixels:
                    color = pixels[(nearest_x, nearest_y)]
                    if color[3] > 0:
                        scaled_pixels[(x, y)] = color

        return scaled_pixels

    # === Color Utilities ===

    def hsv_to_rgb(self, h, s, v):
        """Convert HSV to RGB color"""
        if s == 0:
            return (int(v * 255), int(v * 255), int(v * 255))

        h = h % 360
        sector = h / 60
        i = int(sector)
        f = sector - i

        p = v * (1 - s)
        q = v * (1 - s * f)
        t = v * (1 - s * (1 - f))

        if i == 0:
            r, g, b = v, t, p
        elif i == 1:
            r, g, b = q, v, p
        elif i == 2:
            r, g, b = p, v, t
        elif i == 3:
            r, g, b = p, q, v
        elif i == 4:
            r, g, b = t, p, v
        else:
            r, g, b = v, p, q

        return (int(r * 255), int(g * 255), int(b * 255))

    def rgb_to_hsv(self, r, g, b):
        """Convert RGB to HSV color"""
        r, g, b = r / 255.0, g / 255.0, b / 255.0
        max_c = max(r, g, b)
        min_c = min(r, g, b)
        diff = max_c - min_c

        # Hue
        if diff == 0:
            h = 0
        elif max_c == r:
            h = (60 * ((g - b) / diff) + 360) % 360
        elif max_c == g:
            h = (60 * ((b - r) / diff) + 120) % 360
        else:
            h = (60 * ((r - g) / diff) + 240) % 360

        # Saturation
        s = 0 if max_c == 0 else (diff / max_c)

        # Value
        v = max_c

        return h, s, v

    def get_color_from_wheel(self, pos_x, pos_y, wheel_x, wheel_y, wheel_radius):
        """Get color from color wheel position"""
        dx, dy = pos_x - wheel_x, pos_y - wheel_y
        distance = math.sqrt(dx * dx + dy * dy)

        if distance > wheel_radius:
            return None

        angle = math.degrees(math.atan2(dy, dx))
        if angle < 0:
            angle += 360

        saturation = distance / wheel_radius
        return angle, saturation

    # === Input Handling ===

    def toggle(self):
        """Toggle sprite editor visibility"""
        self.active = not self.active
        if self.active:
            print("🎨 Sprite Editor opened")
            self._center_canvas()
        else:
            pygame.mouse.set_visible(True)
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)
            self.show_custom_cursor = False

    def handle_input(self, event):
        """Handle input events"""
        if not self.active:
            return None

        # Canvas size dialog
        if self.show_canvas_size_dialog:
            return self._handle_canvas_size_dialog(event)

        # Color wheel
        if self.show_color_wheel:
            return self._handle_color_wheel(event)

        # Paste selection
        if self.paste_selection_active:
            return self._handle_paste_input(event)

        # Keyboard shortcuts
        if event.type == pygame.KEYDOWN:
            return self._handle_keyboard_shortcuts(event)

        # Mouse input
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
            return self._handle_mouse_input(event)

        if event.type == pygame.MOUSEWHEEL:
            return self._handle_mouse_wheel(event)

        return None

    def _handle_canvas_size_dialog(self, event):
        """Handle canvas size dialog input"""
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                try:
                    value = int(self.canvas_size_input)
                    if self.canvas_size_field == "width":
                        self.temp_width = value
                        self.canvas_size_input = str(self.canvas_height)
                        self.canvas_size_field = "height"
                    else:
                        self.apply_canvas_size(self.temp_width, value)
                        self.show_canvas_size_dialog = False
                except:
                    self.show_canvas_size_dialog = False
            elif event.key == pygame.K_ESCAPE:
                self.show_canvas_size_dialog = False
            elif event.key == pygame.K_BACKSPACE:
                self.canvas_size_input = self.canvas_size_input[:-1]
            elif event.unicode.isdigit() and len(self.canvas_size_input) < 4:
                self.canvas_size_input += event.unicode
        return None

    def _handle_color_wheel(self, event):
        """Handle Paint.NET style color wheel input with HSV sliders"""
        # Get mouse position for all event types
        mouse_x, mouse_y = event.pos if hasattr(event, 'pos') else pygame.mouse.get_pos()

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            # Remove the mouse_x, mouse_y = event.pos line from here since we already have it above
            # panel dimensions
            panel_width = 350
            panel_height = 320
            panel_x = self.color_wheel_pos[0]
            panel_y = self.color_wheel_pos[1]

            # Wheel dimensions
            wheel_radius = 80
            wheel_center_x = panel_x + 100
            wheel_center_y = panel_y + 120

            dx = mouse_x - wheel_center_x
            dy = mouse_y - wheel_center_y
            distance = math.sqrt(dx * dx + dy * dy)

            # Check if clicking on wheel
            if distance <= wheel_radius:
                self.dragging_color_wheel = True
                angle = math.degrees(math.atan2(dy, dx))
                if angle < 0:
                    angle += 360
                saturation = min(1.0, distance / wheel_radius)

                self.selected_hue = angle
                self.selected_saturation = saturation
                self.current_color = self.hsv_to_rgb(self.selected_hue, self.selected_saturation, self.selected_value)
            else:
                # Check Value slider
                slider_x = wheel_center_x - wheel_radius
                slider_y = wheel_center_y + wheel_radius + 25
                slider_width = wheel_radius * 2
                slider_height = 16

                if (slider_x <= mouse_x <= slider_x + slider_width and
                        slider_y <= mouse_y <= slider_y + slider_height):
                    self.dragging_value_slider = True
                    progress = (mouse_x - slider_x) / slider_width
                    self.selected_value = max(0, min(1, progress))
                    self.current_color = self.hsv_to_rgb(self.selected_hue, self.selected_saturation,
                                                         self.selected_value)
                else:
                    # Check RGB sliders
                    rgb_slider_x = panel_x + 220
                    rgb_slider_width = 90
                    rgb_slider_height = 16

                    # R slider
                    r_slider_y = panel_y + 50
                    if (rgb_slider_x <= mouse_x <= rgb_slider_x + rgb_slider_width and
                            r_slider_y <= mouse_y <= r_slider_y + rgb_slider_height):
                        self.dragging_rgb_slider = 'r'
                        progress = (mouse_x - rgb_slider_x) / rgb_slider_width
                        r = int(progress * 255)
                        self.current_color = (r, self.current_color[1], self.current_color[2])
                        h, s, v = self.rgb_to_hsv(*self.current_color)
                        self.selected_hue, self.selected_saturation, self.selected_value = h, s, v

                    # G slider
                    g_slider_y = panel_y + 90
                    if (rgb_slider_x <= mouse_x <= rgb_slider_x + rgb_slider_width and
                            g_slider_y <= mouse_y <= g_slider_y + rgb_slider_height):
                        self.dragging_rgb_slider = 'g'
                        progress = (mouse_x - rgb_slider_x) / rgb_slider_width
                        g = int(progress * 255)
                        self.current_color = (self.current_color[0], g, self.current_color[2])
                        h, s, v = self.rgb_to_hsv(*self.current_color)
                        self.selected_hue, self.selected_saturation, self.selected_value = h, s, v

                    # B slider
                    b_slider_y = panel_y + 130
                    if (rgb_slider_x <= mouse_x <= rgb_slider_x + rgb_slider_width and
                            b_slider_y <= mouse_y <= b_slider_y + rgb_slider_height):
                        self.dragging_rgb_slider = 'b'
                        progress = (mouse_x - rgb_slider_x) / rgb_slider_width
                        b = int(progress * 255)
                        self.current_color = (self.current_color[0], self.current_color[1], b)
                        h, s, v = self.rgb_to_hsv(*self.current_color)
                        self.selected_hue, self.selected_saturation, self.selected_value = h, s, v

                    # Check HSV sliders
                    hsv_slider_x = panel_x + 220
                    hsv_slider_width = 90
                    hsv_slider_height = 16

                    # H slider (top)
                    h_slider_y = panel_y + 180
                    if (hsv_slider_x <= mouse_x <= hsv_slider_x + hsv_slider_width and
                            h_slider_y <= mouse_y <= h_slider_y + hsv_slider_height):
                        self.dragging_h_slider = True
                        progress = (mouse_x - hsv_slider_x) / hsv_slider_width
                        self.selected_hue = max(0, min(360, progress * 360))
                        self.current_color = self.hsv_to_rgb(self.selected_hue,
                                                             self.selected_saturation,
                                                             self.selected_value)

                    # S slider (middle)
                    s_slider_y = panel_y + 210
                    if (hsv_slider_x <= mouse_x <= hsv_slider_x + hsv_slider_width and
                            s_slider_y <= mouse_y <= s_slider_y + hsv_slider_height):
                        self.dragging_s_slider = True
                        progress = (mouse_x - hsv_slider_x) / hsv_slider_width
                        self.selected_saturation = max(0, min(1, progress))
                        self.current_color = self.hsv_to_rgb(self.selected_hue,
                                                             self.selected_saturation,
                                                             self.selected_value)

                    # V slider (bottom)
                    v_slider_y = panel_y + 240
                    if (hsv_slider_x <= mouse_x <= hsv_slider_x + hsv_slider_width and
                            v_slider_y <= mouse_y <= v_slider_y + hsv_slider_height):
                        self.dragging_v_slider = True
                        progress = (mouse_x - hsv_slider_x) / hsv_slider_width
                        self.selected_value = max(0, min(1, progress))
                        self.current_color = self.hsv_to_rgb(self.selected_hue,
                                                             self.selected_saturation,
                                                             self.selected_value)

                    # Check hex input field
                    hex_input_x = panel_x + 220
                    hex_input_y = panel_y + 270
                    hex_input_width = 90
                    hex_input_height = 25

                    if (hex_input_x <= mouse_x <= hex_input_x + hex_input_width and
                            hex_input_y <= mouse_y <= hex_input_y + hex_input_height):
                        self.hex_input_active = True
                        self.hex_input_text = f"{self.current_color[0]:02X}{self.current_color[1]:02X}{self.current_color[2]:02X}"
                    else:
                        self.hex_input_active = False

                    # Check if clicking outside panel
                    if not (panel_x <= mouse_x <= panel_x + panel_width and
                            panel_y <= mouse_y <= panel_y + panel_height):
                        self.show_color_wheel = False
                        self.dragging_color_wheel = False
                        self.dragging_value_slider = False
                        self.dragging_rgb_slider = None

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.dragging_color_wheel or self.dragging_value_slider or self.dragging_rgb_slider:
                self._add_to_recent_colors(self.current_color)
                self.dragging_color_wheel = False
                self.dragging_value_slider = False
                self.dragging_rgb_slider = None
            if self.dragging_h_slider or self.dragging_s_slider or self.dragging_v_slider:
                self._add_to_recent_colors(self.current_color)
                self.dragging_h_slider = False
                self.dragging_s_slider = False
                self.dragging_v_slider = False

        elif event.type == pygame.MOUSEMOTION:
            if self.dragging_color_wheel:
                # Dragging on wheel
                panel_x = self.color_wheel_pos[0]
                panel_y = self.color_wheel_pos[1]
                wheel_radius = 80
                wheel_center_x = panel_x + 100
                wheel_center_y = panel_y + 120

                dx = mouse_x - wheel_center_x
                dy = mouse_y - wheel_center_y
                distance = math.sqrt(dx * dx + dy * dy)

                angle = math.degrees(math.atan2(dy, dx))
                if angle < 0:
                    angle += 360
                saturation = min(1.0, distance / wheel_radius)

                self.selected_hue = angle
                self.selected_saturation = saturation
                self.current_color = self.hsv_to_rgb(self.selected_hue, self.selected_saturation, self.selected_value)

            elif self.dragging_value_slider:
                # Dragging value slider
                panel_x = self.color_wheel_pos[0]
                panel_y = self.color_wheel_pos[1]
                wheel_radius = 80
                wheel_center_x = panel_x + 100
                slider_x = wheel_center_x - wheel_radius
                slider_width = wheel_radius * 2

                progress = (mouse_x - slider_x) / slider_width
                self.selected_value = max(0, min(1, progress))
                self.current_color = self.hsv_to_rgb(self.selected_hue, self.selected_saturation, self.selected_value)

            elif self.dragging_rgb_slider:
                # Dragging RGB slider
                panel_x = self.color_wheel_pos[0]
                panel_y = self.color_wheel_pos[1]
                rgb_slider_x = panel_x + 220
                rgb_slider_width = 90

                progress = max(0, min(1, (mouse_x - rgb_slider_x) / rgb_slider_width))
                value = int(progress * 255)

                if self.dragging_rgb_slider == 'r':
                    self.current_color = (value, self.current_color[1], self.current_color[2])
                elif self.dragging_rgb_slider == 'g':
                    self.current_color = (self.current_color[0], value, self.current_color[2])
                elif self.dragging_rgb_slider == 'b':
                    self.current_color = (self.current_color[0], self.current_color[1], value)

                h, s, v = self.rgb_to_hsv(*self.current_color)
                self.selected_hue, self.selected_saturation, self.selected_value = h, s, v

            elif self.dragging_h_slider:
                panel_x = self.color_wheel_pos[0]
                hsv_slider_x = panel_x + 220
                hsv_slider_width = 90

                progress = max(0, min(1, (mouse_x - hsv_slider_x) / hsv_slider_width))
                self.selected_hue = progress * 360
                self.current_color = self.hsv_to_rgb(self.selected_hue,
                                                     self.selected_saturation,
                                                     self.selected_value)

            elif self.dragging_s_slider:
                panel_x = self.color_wheel_pos[0]
                hsv_slider_x = panel_x + 220
                hsv_slider_width = 90

                progress = max(0, min(1, (mouse_x - hsv_slider_x) / hsv_slider_width))
                self.selected_saturation = progress
                self.current_color = self.hsv_to_rgb(self.selected_hue,
                                                     self.selected_saturation,
                                                     self.selected_value)

            elif self.dragging_v_slider:
                panel_x = self.color_wheel_pos[0]
                hsv_slider_x = panel_x + 220
                hsv_slider_width = 90

                progress = max(0, min(1, (mouse_x - hsv_slider_x) / hsv_slider_width))
                self.selected_value = progress
                self.current_color = self.hsv_to_rgb(self.selected_hue,
                                                     self.selected_saturation,
                                                     self.selected_value)

        elif event.type == pygame.KEYDOWN and self.hex_input_active:
            if event.key == pygame.K_RETURN:
                # Parse hex color
                hex_str = self.hex_input_text.strip().lstrip('#')
                if len(hex_str) == 6:
                    try:
                        r = int(hex_str[0:2], 16)
                        g = int(hex_str[2:4], 16)
                        b = int(hex_str[4:6], 16)
                        self.current_color = (r, g, b)
                        h, s, v = self.rgb_to_hsv(r, g, b)
                        self.selected_hue, self.selected_saturation, self.selected_value = h, s, v
                        self._add_to_recent_colors(self.current_color)
                    except:
                        pass
                self.hex_input_active = False

            elif event.key == pygame.K_ESCAPE:
                self.hex_input_active = False

            elif event.key == pygame.K_BACKSPACE:
                self.hex_input_text = self.hex_input_text[:-1]

            elif event.unicode and len(self.hex_input_text) < 6:
                char = event.unicode.upper()
                if char in '0123456789ABCDEF':
                    self.hex_input_text += char

        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.show_color_wheel = False
            self.dragging_color_wheel = False
            self.dragging_value_slider = False
            self.dragging_rgb_slider = None

        return None

    def _handle_paste_input(self, event):
        """Handle input during paste operation"""
        # Keyboard shortcuts for paste
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                self.apply_paste()
            elif event.key == pygame.K_ESCAPE:
                self.cancel_paste()
            elif event.key == pygame.K_r and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                self.paste_rotation = (self.paste_rotation + 90) % 360
                self._update_paste_handles()
            elif event.key == pygame.K_h:
                self.scaling_handles_active = not self.scaling_handles_active
            elif event.key == pygame.K_i:
                self.paste_interpolation = not self.paste_interpolation
            elif event.key in (pygame.K_PLUS, pygame.K_EQUALS):
                return self._handle_zoom_in()
            elif event.key == pygame.K_MINUS:
                return self._handle_zoom_out()

        # Mouse wheel for zoom
        if event.type == pygame.MOUSEWHEEL:
            return self._handle_mouse_wheel(event, during_paste=True)

        # Mouse events for paste
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
            return self._handle_paste_mouse(event)

        return None

    def _handle_keyboard_shortcuts(self, event):
        """Handle keyboard shortcuts"""
        # Modifier keys
        mods = pygame.key.get_mods()

        if event.key == pygame.K_ESCAPE:
            self.active = False
            pygame.mouse.set_visible(True)
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)
            self.show_custom_cursor = False
            return 'close'

        elif event.key == pygame.K_z and mods & pygame.KMOD_CTRL:
            self.undo()

        elif event.key == pygame.K_y and mods & pygame.KMOD_CTRL:
            self.redo()

        elif event.key == pygame.K_s and not (mods & pygame.KMOD_CTRL):
            self.current_tool = 'select'
        elif event.key == pygame.K_w and not (mods & pygame.KMOD_CTRL):
            self.current_tool = 'magicwand'
        elif event.key == pygame.K_p and not (mods & pygame.KMOD_CTRL):
            self.current_tool = 'pencil'
        elif event.key == pygame.K_e and not (mods & pygame.KMOD_CTRL):
            self.current_tool = 'eraser'
        elif event.key == pygame.K_f and not (mods & pygame.KMOD_CTRL):
            self.current_tool = 'fill'
        elif event.key == pygame.K_i and not (mods & pygame.KMOD_CTRL):
            self.current_tool = 'eyedropper'
        elif event.key == pygame.K_v and not (mods & pygame.KMOD_CTRL):
            self.current_tool = 'move'

        elif event.key == pygame.K_g and not (mods & pygame.KMOD_CTRL):
            self.show_grid = not self.show_grid

        elif event.key == pygame.K_s and mods & pygame.KMOD_CTRL:
            if self.current_file:
                self.export_image(self.current_file)
            else:
                self.export_image()

        elif event.key == pygame.K_o and mods & pygame.KMOD_CTRL:
            self.import_image()

        elif event.key == pygame.K_n and mods & pygame.KMOD_CTRL:
            self.prompt_canvas_size()

        elif event.key == pygame.K_c and mods & pygame.KMOD_CTRL:
            self.copy_selection()

        elif event.key == pygame.K_v and mods & pygame.KMOD_CTRL:
            if self.clipboard:
                self.start_paste()
            else:
                print("✗ Nothing to paste")

        elif event.key == pygame.K_SPACE:
            self.is_panning = True

        elif event.key in (pygame.K_DELETE, pygame.K_BACKSPACE):
            if self.selected_pixels:
                self._save_to_history()
                for x, y in self.selected_pixels:
                    self.canvas[y][x] = (0, 0, 0, 0)
                self.unsaved_changes = True

        elif event.key == pygame.K_SPACE:
            self.is_panning = False

        elif event.key == pygame.K_h and pygame.key.get_mods() & pygame.KMOD_CTRL:
            self.show_color_wheel = True
            self.color_wheel_pos = (self.screen_width // 2 - 100, self.screen_height // 2 - 100)
            self.hex_input_active = True
            self.hex_input_text = f"{self.current_color[0]:02X}{self.current_color[1]:02X}{self.current_color[2]:02X}"

        return None

    def _handle_mouse_input(self, event):
        """Handle mouse input"""
        mouse_x, mouse_y = event.pos

        # UI clicks
        if self._handle_ui_click(mouse_x, mouse_y, event.type == pygame.MOUSEBUTTONDOWN and event.button == 1):
            return None

        # Tolerance slider interaction
        if self.current_tool == 'magicwand' and self.current_tab == 'canvas':
            if self._handle_tolerance_slider(event, mouse_x, mouse_y):
                return None

        # Canvas interaction
        if self.current_tab == 'canvas':
            return self._handle_canvas_mouse(event, mouse_x, mouse_y)

        return None

    def _handle_tolerance_slider(self, event, mouse_x, mouse_y):
        """Handle tolerance slider input"""
        # Tolerance slider dimensions
        slider_x = self.screen_width - 250
        slider_y = 15
        slider_width = 200
        slider_height = 20

        slider_rect = pygame.Rect(slider_x, slider_y, slider_width, slider_height)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if slider_rect.collidepoint(mouse_x, mouse_y):
                self.dragging_tolerance_slider = True
                # Update tolerance based on click position
                progress = (mouse_x - slider_x) / slider_width
                self.tool_options['tolerance'] = int(progress * 100)
                return True

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.dragging_tolerance_slider:
                self.dragging_tolerance_slider = False
                return True

        elif event.type == pygame.MOUSEMOTION:
            if self.dragging_tolerance_slider:
                # Clamp mouse position to slider bounds
                clamped_x = max(slider_x, min(mouse_x, slider_x + slider_width))
                progress = (clamped_x - slider_x) / slider_width
                self.tool_options['tolerance'] = int(progress * 100)
                return True

        return False

    def _handle_canvas_mouse(self, event, mouse_x, mouse_y):
        """Handle mouse input on canvas"""
        pixel_pos = self.get_canvas_pixel(mouse_x, mouse_y)

        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1:  # Left click
                if pixel_pos:
                    self._handle_left_click(pixel_pos)

            elif event.button == 3:  # Right click
                if self.current_tool == 'pencil' and pixel_pos:
                    old_tool = self.current_tool
                    self.current_tool = 'eraser'
                    self.is_drawing = True
                    self._save_to_history()
                    self.draw_pixel(*pixel_pos)
                    self.last_draw_pos = pixel_pos
                    self.current_tool = old_tool

        elif event.type == pygame.MOUSEBUTTONUP:
            if event.button == 1:
                self.is_drawing = False
                self.last_draw_pos = None
                self.moving_canvas = False
                self.move_start_pos = None

                # Reset button states
                for key in self.button_states:
                    self.button_states[key] = False

                if self.current_tool == 'select' and self.selection_start and pixel_pos:
                    self.selection_end = pixel_pos
                    self.selection_preview_end = None
                    self._calculate_selected_pixels()

        elif event.type == pygame.MOUSEMOTION:
            self._handle_mouse_motion(pixel_pos, mouse_x, mouse_y)

        return None

    def _handle_left_click(self, pixel_pos):
        """Handle left mouse click on canvas"""
        px, py = pixel_pos

        if self.current_tool == 'move':
            self.moving_canvas = True
            self.move_start_pos = pygame.mouse.get_pos()

        elif self.current_tool in ('pencil', 'eraser'):
            self.is_drawing = True
            self._save_to_history()
            self.draw_pixel(px, py)
            self.last_draw_pos = (px, py)

        elif self.current_tool == 'fill':
            target = self.canvas[py][px]
            self._save_to_history()
            self.flood_fill(px, py, target, self.current_color)

        elif self.current_tool == 'eyedropper':
            color = self.canvas[py][px]
            if color[3] > 0:
                self.current_color = color[:3]
                self._add_to_recent_colors(self.current_color)

        elif self.current_tool == 'select':
            if not (pygame.key.get_mods() & pygame.KMOD_SHIFT):
                self.selection_start = (px, py)
                self.selection_end = None
                self.selection_preview_end = (px, py)
                self.selected_pixels = []
            else:
                if self.selection_start and self.selection_end:
                    x1, y1 = self.selection_start
                    x2, y2 = self.selection_end
                    min_x = min(x1, x2, px)
                    min_y = min(y1, y2, py)
                    max_x = max(x1, x2, px)
                    max_y = max(y1, y2, py)
                    self.selection_start = (min_x, min_y)
                    self.selection_end = (max_x, max_y)
                    self._calculate_selected_pixels()

        elif self.current_tool == 'magicwand':
            self.magic_wand_select(px, py)

    def _handle_mouse_motion(self, pixel_pos, mouse_x, mouse_y):
        """Handle mouse motion on canvas"""
        # Drawing
        if self.is_drawing and self.current_tool in ('pencil', 'eraser'):
            if pixel_pos:
                px, py = pixel_pos
                if self.last_draw_pos:
                    self._draw_line(self.last_draw_pos[0], self.last_draw_pos[1], px, py)
                else:
                    self.draw_pixel(px, py)
                self.last_draw_pos = (px, py)

        # Move tool
        elif self.current_tool == 'move' and self.moving_canvas and self.move_start_pos:
            dx = mouse_x - self.move_start_pos[0]
            dy = mouse_y - self.move_start_pos[1]
            self.canvas_offset_x += dx
            self.canvas_offset_y += dy
            self.move_start_pos = (mouse_x, mouse_y)
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_HAND)

        # Selection preview
        elif self.current_tool == 'select' and pygame.mouse.get_pressed()[0] and self.selection_start:
            if pixel_pos:
                self.selection_preview_end = pixel_pos

        # Update cursor
        self._update_cursor(pixel_pos)

    def _handle_paste_mouse(self, event):
        """Handle mouse events during paste"""
        mx, my = event.pos

        if not self.paste_selection_rect:
            return None

        ps = self.pixel_size
        rect = self.paste_selection_rect

        # Screen-space rectangle
        screen_rect = pygame.Rect(
            self.canvas_offset_x + rect.x * ps,
            self.canvas_offset_y + rect.y * ps,
            rect.width * ps,
            rect.height * ps
        )

        # Check for handle
        handle_i, handle = self._get_handle_at_screen_pos(mx, my)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if handle:
                self.dragging_handle = handle_i
                pygame.mouse.set_cursor(handle['cursor'])
            elif screen_rect.collidepoint(mx, my):
                self.moving_selection = True
                self.paste_selection_offset = (mx - screen_rect.x, my - screen_rect.y)
                pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_HAND)
            else:
                self.apply_paste()

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging_handle = None
            self.moving_selection = False
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)

        elif event.type == pygame.MOUSEMOTION:
            if self.dragging_handle is not None:
                cx = (mx - self.canvas_offset_x) // ps
                cy = (my - self.canvas_offset_y) // ps
                self._handle_paste_transform(self.dragging_handle, cx, cy)
            elif self.moving_selection:
                new_x = (mx - self.paste_selection_offset[0] - self.canvas_offset_x) // ps
                new_y = (my - self.paste_selection_offset[1] - self.canvas_offset_y) // ps
                rect.x, rect.y = int(new_x), int(new_y)
                self._update_paste_handles()
            else:
                if handle:
                    pygame.mouse.set_cursor(handle['cursor'])
                elif screen_rect.collidepoint(mx, my):
                    pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_HAND)
                else:
                    pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)

        return None

    def _handle_paste_transform(self, handle_index, mouse_canvas_x, mouse_canvas_y):
        """Handle dragging of transformation handles"""
        if not self.paste_selection_rect:
            return

        rect = self.paste_selection_rect
        handle = self.paste_handles[handle_index]

        if handle['type'] == 'rotate':
            center_x, center_y = rect.centerx, rect.centery
            dx, dy = mouse_canvas_x - center_x, mouse_canvas_y - center_y
            angle = math.degrees(math.atan2(dy, dx))
            if angle < 0:
                angle += 360
            self.paste_rotation = round(angle / 5) * 5

        elif handle['type'] == 'scale_both':
            # Handle corner scaling
            if handle_index == 0:  # Top-left
                new_width = rect.right - mouse_canvas_x
                new_height = rect.bottom - mouse_canvas_y
            elif handle_index == 1:  # Top-right
                new_width = mouse_canvas_x - rect.left
                new_height = rect.bottom - mouse_canvas_y
            elif handle_index == 2:  # Bottom-left
                new_width = rect.right - mouse_canvas_x
                new_height = mouse_canvas_y - rect.top
            elif handle_index == 3:  # Bottom-right
                new_width = mouse_canvas_x - rect.left
                new_height = mouse_canvas_y - rect.top

            if new_width > 0 and new_height > 0:
                scale_x = new_width / self.clipboard_width
                scale_y = new_height / self.clipboard_height

                if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                    scale = min(scale_x, scale_y)
                    scale_x = scale_y = scale

                scale_x = min(4.0, max(0.1, scale_x))
                scale_y = min(4.0, max(0.1, scale_y))
                self.paste_scale = (scale_x + scale_y) / 2.0

                # Update rectangle based on handle
                if handle_index == 0:
                    rect.topleft = (mouse_canvas_x, mouse_canvas_y)
                elif handle_index == 1:
                    rect.topright = (mouse_canvas_x, mouse_canvas_y)
                elif handle_index == 2:
                    rect.bottomleft = (mouse_canvas_x, mouse_canvas_y)
                elif handle_index == 3:
                    rect.bottomright = (mouse_canvas_x, mouse_canvas_y)

                rect.width = int(self.clipboard_width * scale_x)
                rect.height = int(self.clipboard_height * scale_y)

        elif handle['type'] == 'scale_height':
            if handle_index == 4:  # Top-middle
                new_height = rect.bottom - mouse_canvas_y
                if new_height > 0:
                    scale_y = min(4.0, max(0.1, new_height / self.clipboard_height))
                    self.paste_scale = scale_y
                    rect.top = mouse_canvas_y
                    rect.height = int(self.clipboard_height * scale_y)
            elif handle_index == 5:  # Bottom-middle
                new_height = mouse_canvas_y - rect.top
                if new_height > 0:
                    scale_y = min(4.0, max(0.1, new_height / self.clipboard_height))
                    self.paste_scale = scale_y
                    rect.bottom = mouse_canvas_y
                    rect.height = int(self.clipboard_height * scale_y)

        elif handle['type'] == 'scale_width':
            if handle_index == 6:  # Left-middle
                new_width = rect.right - mouse_canvas_x
                if new_width > 0:
                    scale_x = min(4.0, max(0.1, new_width / self.clipboard_width))
                    self.paste_scale = scale_x
                    rect.left = mouse_canvas_x
                    rect.width = int(self.clipboard_width * scale_x)
            elif handle_index == 7:  # Right-middle
                new_width = mouse_canvas_x - rect.left
                if new_width > 0:
                    scale_x = min(4.0, max(0.1, new_width / self.clipboard_width))
                    self.paste_scale = scale_x
                    rect.right = mouse_canvas_x
                    rect.width = int(self.clipboard_width * scale_x)

        self._update_paste_handles()

    def _handle_mouse_wheel(self, event, during_paste=False):
        """Handle mouse wheel for zoom and panning"""
        keys = pygame.key.get_mods()

        if keys & pygame.KMOD_SHIFT:
            # Pan horizontally
            pan_amount = event.y * 20
            self.canvas_offset_x += pan_amount
            return None
        elif keys & pygame.KMOD_CTRL:
            # Pan vertically
            pan_amount = event.y * 20
            self.canvas_offset_y += pan_amount
            return None
        else:
            return self._handle_zoom_wheel(event)

    def _handle_zoom_wheel(self, event):
        """Handle zoom with mouse wheel"""
        mouse_x, mouse_y = pygame.mouse.get_pos()

        # Get canvas coordinates before zoom
        old_pixel_pos = self.get_canvas_pixel(mouse_x, mouse_y)

        old_zoom = self.zoom_index
        if event.y > 0 and self.zoom_index < len(self.zoom_levels) - 1:
            self.zoom_index += 1
        elif event.y < 0 and self.zoom_index > 0:
            self.zoom_index -= 1

        if old_zoom != self.zoom_index:
            old_pixel_size = self.pixel_size
            self.pixel_size = self.zoom_levels[self.zoom_index]

            # Keep mouse position centered on zoom
            if old_pixel_pos:
                px, py = old_pixel_pos
                # Calculate the pixel's screen position with old zoom
                old_screen_x = self.canvas_offset_x + px * old_pixel_size
                old_screen_y = self.canvas_offset_y + py * old_pixel_size

                # Calculate where the pixel should be with new zoom
                new_screen_x = self.canvas_offset_x + px * self.pixel_size
                new_screen_y = self.canvas_offset_y + py * self.pixel_size

                # Adjust offset to keep the pixel under the mouse
                self.canvas_offset_x += (old_screen_x - new_screen_x)
                self.canvas_offset_y += (old_screen_y - new_screen_y)
            else:
                # If mouse is outside canvas, zoom towards center
                canvas_center_x = self.canvas_offset_x + (self.canvas_width * old_pixel_size) // 2
                canvas_center_y = self.canvas_offset_y + (self.canvas_height * old_pixel_size) // 2

                # Calculate center in canvas coordinates
                center_canvas_x = (canvas_center_x - self.canvas_offset_x) // old_pixel_size
                center_canvas_y = (canvas_center_y - self.canvas_offset_y) // old_pixel_size

                # Recalculate with new zoom
                new_center_x = self.canvas_offset_x + center_canvas_x * self.pixel_size
                new_center_y = self.canvas_offset_y + center_canvas_y * self.pixel_size

                # Adjust offset to keep center
                self.canvas_offset_x += (canvas_center_x - new_center_x)
                self.canvas_offset_y += (canvas_center_y - new_center_y)

            # Clamp canvas position to keep it visible
            self._clamp_canvas_position()

        return None

    def _handle_zoom_in(self):
        """Handle zoom in"""
        if self.zoom_index < len(self.zoom_levels) - 1:
            mouse_x, mouse_y = pygame.mouse.get_pos()
            old_pixel_pos = self.get_canvas_pixel(mouse_x, mouse_y)
            old_pixel_size = self.pixel_size

            self.zoom_index += 1
            self.pixel_size = self.zoom_levels[self.zoom_index]

            # Adjust for mouse position
            if old_pixel_pos:
                px, py = old_pixel_pos
                old_screen_x = self.canvas_offset_x + px * old_pixel_size
                old_screen_y = self.canvas_offset_y + py * old_pixel_size
                new_screen_x = self.canvas_offset_x + px * self.pixel_size
                new_screen_y = self.canvas_offset_y + py * self.pixel_size

                self.canvas_offset_x += (old_screen_x - new_screen_x)
                self.canvas_offset_y += (old_screen_y - new_screen_y)

            self._clamp_canvas_position()
            print(f"🔍 Zoom: {self.pixel_size}px")
        return None

    def _handle_zoom_out(self):
        """Handle zoom out"""
        if self.zoom_index > 0:
            mouse_x, mouse_y = pygame.mouse.get_pos()
            old_pixel_pos = self.get_canvas_pixel(mouse_x, mouse_y)
            old_pixel_size = self.pixel_size

            self.zoom_index -= 1
            self.pixel_size = self.zoom_levels[self.zoom_index]

            # Adjust for mouse position
            if old_pixel_pos:
                px, py = old_pixel_pos
                old_screen_x = self.canvas_offset_x + px * old_pixel_size
                old_screen_y = self.canvas_offset_y + py * old_pixel_size
                new_screen_x = self.canvas_offset_x + px * self.pixel_size
                new_screen_y = self.canvas_offset_y + py * self.pixel_size

                self.canvas_offset_x += (old_screen_x - new_screen_x)
                self.canvas_offset_y += (old_screen_y - new_screen_y)

            self._clamp_canvas_position()
            print(f"🔍 Zoom: {self.pixel_size}px")
        return None

    def _update_cursor(self, pixel_pos):
        """Update cursor based on current tool"""
        if self.current_tab != 'canvas' or self.paste_selection_active:
            pygame.mouse.set_visible(True)
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)
            self.show_custom_cursor = False
            return

        # Get current mouse position
        mouse_x, mouse_y = pygame.mouse.get_pos()

        # Check if mouse is over blocking UI
        if self._is_mouse_over_blocking_ui(mouse_x, mouse_y):
            pygame.mouse.set_visible(True)
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)
            self.show_custom_cursor = False
            return

        if pixel_pos:
            icon_cursor_tools = ['pencil', 'fill', 'eyedropper', 'magicwand', 'eraser']

            if self.current_tool in icon_cursor_tools and self.current_tool in self.tool_cursors:
                pygame.mouse.set_visible(False)
                self.show_custom_cursor = True
                self.custom_cursor_pos = (mouse_x, mouse_y)
            elif self.current_tool == 'move':
                pygame.mouse.set_visible(True)
                pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_HAND)
                self.show_custom_cursor = False
            elif self.current_tool == 'select':
                pygame.mouse.set_visible(True)
                pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_CROSSHAIR)
                self.show_custom_cursor = False
            else:
                pygame.mouse.set_visible(True)
                pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)
                self.show_custom_cursor = False
        else:
            pygame.mouse.set_visible(True)
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)
            self.show_custom_cursor = False

    def _handle_ui_click(self, mouse_x, mouse_y, is_left_click):
        """Handle clicks on UI elements"""
        button_y, button_height = 10, 30
        buttons = [
            {'id': 'new', 'label': 'New', 'x': 20, 'w': 80},
            {'id': 'import', 'label': 'Import', 'x': 110, 'w': 90},
            {'id': 'export', 'label': 'Export', 'x': 200, 'w': 90},
        ]

        for btn in buttons:
            if btn['x'] <= mouse_x <= btn['x'] + btn['w'] and button_y <= mouse_y <= button_y + button_height:
                if is_left_click:
                    self.button_states[btn['id']] = True
                    if btn['id'] == 'new':
                        self.prompt_canvas_size()
                    elif btn['id'] == 'import':
                        self.import_image()
                    elif btn['id'] == 'export':
                        self.export_image()
                return True

        # Canvas tabs
        if self.current_tab == 'canvas' and len(self.canvases) > 0:
            tab_bar_y, tab_bar_height, tab_width = 50, 35, 120
            tab_spacing, tab_start_x = 5, 0

            for i, canvas in enumerate(self.canvases):
                tab_x = tab_start_x + i * (tab_width + tab_spacing)

                if tab_x > self.screen_width - tab_width:
                    continue

                if tab_x <= mouse_x <= tab_x + tab_width and tab_bar_y <= mouse_y <= tab_bar_y + tab_bar_height:
                    if is_left_click:
                        close_btn_x = tab_x + tab_width - 22
                        close_btn_y = tab_bar_y + 8
                        close_btn_size = 18

                        if close_btn_x <= mouse_x <= close_btn_x + close_btn_size and \
                                close_btn_y <= mouse_y <= close_btn_y + close_btn_size:
                            self.close_canvas(i)
                        else:
                            self._save_current_canvas()
                            self._load_canvas(i)
                    return True

            # New canvas button
            new_btn_x = tab_start_x + len(self.canvases) * (tab_width + tab_spacing)
            new_btn_size = 30
            if new_btn_x <= mouse_x <= new_btn_x + new_btn_size and \
                    tab_bar_y + 2 <= mouse_y <= tab_bar_y + 2 + new_btn_size:
                if is_left_click:
                    self.prompt_canvas_size()
                return True

        tab_y, tab_height, tab_width = 10, 30, 100
        tabs = [
            {'id': 'canvas_tab', 'label': 'Canvas', 'x': 310},
            {'id': 'animation_tab', 'label': 'Animation', 'x': 420},
        ]

        for tab in tabs:
            if tab['x'] <= mouse_x <= tab['x'] + tab_width and tab_y <= mouse_y <= tab_y + tab_height:
                if is_left_click:
                    if tab['id'] == 'canvas_tab':
                        self.current_tab = 'canvas'
                        self.button_states['canvas_tab'] = True
                        self.button_states['animation_tab'] = False
                    else:
                        self.current_tab = 'animation'
                        self.button_states['animation_tab'] = True
                        self.button_states['canvas_tab'] = False
                        self._detect_animations()
                return True

        # Animation tab controls
        if self.current_tab == 'animation':
            # Direction buttons
            dir_btn_y, dir_width, dir_height = 60, 60, 35
            directions = [
                {'id': 'down', 'label': 'Down', 'color': (64, 128, 255), 'x': 60},
                {'id': 'left', 'label': 'Left', 'color': (255, 165, 0), 'x': 130},
                {'id': 'right', 'label': 'Right', 'color': (50, 205, 50), 'x': 200},
                {'id': 'up', 'label': 'Up', 'color': (255, 105, 180), 'x': 270}
            ]

            for direction in directions:
                dir_x = direction['x']
                if dir_x <= mouse_x <= dir_x + dir_width and dir_btn_y <= mouse_y <= dir_btn_y + dir_height:
                    if is_left_click:
                        self.anim_current_direction = direction['id']
                        self._load_animation_frames()
                    return True

            # Flip checkbox (left direction only)
            if self.anim_current_direction == 'left':
                checkbox_x, checkbox_y, checkbox_size = 340, dir_btn_y, 25
                if checkbox_x <= mouse_x <= checkbox_x + checkbox_size and \
                        checkbox_y <= mouse_y <= checkbox_y + checkbox_size:
                    if is_left_click:
                        self.anim_flip_left = not self.anim_flip_left
                        self._load_animation_frames()
                    return True

            # Animation controls
            if self.anim_frames:
                preview_x = self.screen_width // 2
                preview_y = self.screen_height // 2 + 40
                scaled_height = int(self.anim_sprite_height * self.anim_preview_scale)

                # Play button
                frame_text = f"Frame {self.anim_frame_index + 1}/{len(self.anim_frames)}"
                frame_surface = self.fonts['medium'].render(frame_text, True, (220, 220, 220))
                play_btn_x = preview_x - (frame_surface.get_width() // 2) - 30
                play_btn_y = preview_y + scaled_height // 2 + 20
                play_btn_size = 24

                if play_btn_x <= mouse_x <= play_btn_x + play_btn_size and \
                        play_btn_y <= mouse_y <= play_btn_y + play_btn_size:
                    if is_left_click:
                        self.anim_playing = not self.anim_playing
                        if self.anim_playing:
                            self.anim_frame_time = 0
                    return True

                # Speed controls
                speed_y = preview_y + scaled_height // 2 + 50
                speed_btn_y = speed_y + 20
                minus_btn_x = preview_x - 50
                plus_btn_x = preview_x + 10

                if minus_btn_x <= mouse_x <= minus_btn_x + 40 and speed_btn_y <= mouse_y <= speed_btn_y + 25:
                    if is_left_click:
                        self.anim_speed = max(0.1, self.anim_speed - 0.1)
                    return True

                if plus_btn_x <= mouse_x <= plus_btn_x + 40 and speed_btn_y <= mouse_y <= speed_btn_y + 25:
                    if is_left_click:
                        self.anim_speed = min(5.0, self.anim_speed + 0.1)
                    return True

            return False

        # Recent colors
        palette_x = self.screen_width - 40
        palette_y = 90
        color_size, color_spacing = 24, 2

        for i, color in enumerate(self.recent_colors):
            cy = palette_y + i * (color_size + color_spacing)
            if palette_x <= mouse_x <= palette_x + color_size and cy <= mouse_y <= cy + color_size:
                if is_left_click:
                    self.current_color = color
                return True

        # Color wheel button
        wheel_btn_y = palette_y + len(self.recent_colors) * (color_size + color_spacing) + 10
        if palette_x <= mouse_x <= palette_x + color_size and wheel_btn_y <= mouse_y <= wheel_btn_y + color_size:
            if is_left_click:
                self.show_color_wheel = True
                self.color_wheel_pos = (self.screen_width // 2 - 100, self.screen_height // 2 - 100)
            return True

        # Tool button dropdown
        tool_x, tool_y, tool_size = 10, 90, 36
        if tool_x <= mouse_x <= tool_x + tool_size and tool_y <= mouse_y <= tool_y + tool_size:
            if is_left_click:
                self.show_tool_dropdown = not self.show_tool_dropdown
            return True

        # Tool dropdown menu
        if self.show_tool_dropdown:
            dropdown_width, dropdown_item_height = 140, 32
            dropdown_height = len(self.tools) * dropdown_item_height
            dropdown_x, dropdown_y = tool_x, tool_y + tool_size + 2

            if dropdown_x <= mouse_x <= dropdown_x + dropdown_width and \
                    dropdown_y <= mouse_y <= dropdown_y + dropdown_height:
                if is_left_click:
                    tool_index = (mouse_y - dropdown_y) // dropdown_item_height
                    if 0 <= tool_index < len(self.tools):
                        self.current_tool = self.tools[tool_index]['id']
                        self.show_tool_dropdown = False
                return True

            # Click outside dropdown
            if is_left_click:
                self.show_tool_dropdown = False

        return False

    # === Animation Functions ===

    def _detect_animations(self):
        """Detect animations based on sprite sheet layout"""
        if not self.current_file:
            return

        standard_animations = [
            'idle', 'walk', 'run', 'melee', 'melee2', 'melee3',
            'kiblast', 'hurt', 'death', 'charge', 'block',
            'transform', 'untransform'
        ]

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

        filename = os.path.basename(self.current_file).lower()
        detected_name = None

        for anim_name in standard_animations:
            if anim_name in filename:
                detected_name = anim_name
                break

        if not detected_name:
            detected_name = "unknown"

        self.anim_sprite_width = detected_width
        self.anim_sprite_height = detected_height
        self.anim_detected_animations = [detected_name]
        self.anim_current_animation = detected_name

        self._load_animation_frames()
        print(f"🎬 Detected: {detected_name} ({detected_width}x{detected_height})")

    def _load_animation_frames(self):
        """Load animation frames for current direction"""
        if not self.anim_current_animation:
            return

        direction_map = {'down': 0, 'left': 1, 'right': 2, 'up': 3}
        row = direction_map.get(self.anim_current_direction, 0)
        num_frames = self.canvas_width // self.anim_sprite_width

        self.anim_frames = []
        for frame_idx in range(num_frames):
            frame_x = frame_idx * self.anim_sprite_width
            frame_y = row * self.anim_sprite_height

            frame_surface = pygame.Surface((self.anim_sprite_width, self.anim_sprite_height), pygame.SRCALPHA)
            frame_surface.fill((0, 0, 0, 0))

            for y in range(self.anim_sprite_height):
                for x in range(self.anim_sprite_width):
                    canvas_x = frame_x + x
                    canvas_y = frame_y + y

                    if canvas_y < len(self.canvas) and canvas_x < len(self.canvas[canvas_y]):
                        color = self.canvas[canvas_y][canvas_x]
                        if color[3] > 0:
                            frame_surface.set_at((x, y), color)

            # Flip for left direction if enabled
            if self.anim_current_direction == 'left' and self.anim_flip_left:
                frame_surface = pygame.transform.flip(frame_surface, True, False)

            self.anim_frames.append(frame_surface)

        self.anim_frame_index = 0
        self.anim_frame_time = 0

    # === Update and Drawing ===

    def update(self, dt):
        """Update editor state"""
        if not self.active:
            return

        # Animation playback
        if self.current_tab == 'animation' and self.anim_playing and self.anim_frames:
            self.anim_frame_time += dt * self.anim_speed

            if self.anim_frame_time >= self.anim_frame_duration:
                self.anim_frame_time = 0
                self.anim_frame_index += 1

                if self.anim_frame_index >= len(self.anim_frames):
                    if self.anim_loop:
                        self.anim_frame_index = 0
                    else:
                        self.anim_frame_index = len(self.anim_frames) - 1
                        self.anim_playing = False

    def draw(self, screen):
        """Draw the sprite editor"""
        if not self.active:
            return

        # Background
        screen.fill((28, 28, 36))

        # Current tab content
        if self.current_tab == 'canvas':
            self._draw_canvas(screen)

            if self.paste_selection_active:
                self._draw_paste_selection(screen)

            self._draw_tools(screen)
            self._draw_recent_colors(screen)
            self._draw_current_color(screen)

        elif self.current_tab == 'animation':
            self._draw_animation_preview(screen)

        # Common UI
        self._draw_menu_bar(screen)
        self._draw_status_bar(screen)
        self._draw_canvas_tabs(screen)

        # Dialogs
        if self.show_canvas_size_dialog:
            self._draw_canvas_size_dialog(screen)

        if self.show_color_wheel:
            self._draw_color_wheel(screen)

        # Draw tolerance slider if magic wand is selected
        if self.current_tool == 'magicwand' and self.current_tab == 'canvas':
            self._draw_tolerance_slider(screen)

        # Custom cursor
        self._draw_custom_cursor(screen)

    def _draw_tolerance_slider(self, screen):
        """Draw tolerance slider for magic wand tool"""
        # Slider dimensions
        slider_x = self.screen_width - 250
        slider_y = 15
        slider_width = 200
        slider_height = 20

        # Background
        screen.draw_rect((45, 45, 55), (slider_x, slider_y, slider_width, slider_height))
        screen.draw_rect((80, 80, 90), (slider_x, slider_y, slider_width, slider_height), 1)

        # Calculate progress
        progress = self.tool_options['tolerance'] / 100.0

        # Fill
        fill_width = int(slider_width * progress)
        fill_color = (100, 200, 255)  # Blue color
        screen.draw_rect(fill_color, (slider_x, slider_y, fill_width, slider_height))

        # Indicator
        indicator_x = slider_x + int(slider_width * progress)
        screen.draw_line((255, 255, 255),
                         (indicator_x, slider_y - 2),
                         (indicator_x, slider_y + slider_height + 2), 3)
        screen.draw_line((0, 0, 0),
                         (indicator_x, slider_y - 2),
                         (indicator_x, slider_y + slider_height + 2), 1)

        # Label
        label = self.fonts['small'].render(f"Tolerance: {self.tool_options['tolerance']}", True, (220, 220, 220))
        screen.blit(label, (slider_x + 150 - label.get_width() - 10, slider_y + 5))

    def _draw_canvas(self, screen):
        """Draw the pixel canvas with transparency"""

        # Calculate visible range (performance optimization)
        start_x = max(0, -self.canvas_offset_x // self.pixel_size)
        start_y = max(0, -self.canvas_offset_y // self.pixel_size)
        end_x = min(self.canvas_width, (self.screen_width - self.canvas_offset_x) // self.pixel_size + 1)
        end_y = min(self.canvas_height, (self.screen_height - self.canvas_offset_y) // self.pixel_size + 1)

        # Draw visible pixels only
        for y in range(start_y, end_y):
            for x in range(start_x, end_x):
                px = self.canvas_offset_x + x * self.pixel_size
                py = self.canvas_offset_y + y * self.pixel_size

                # Checkerboard background
                if self.pixel_size not in self.checkerboard_cache:
                    pattern = pygame.Surface((self.pixel_size * 2, self.pixel_size * 2))
                    pattern.fill((42, 42, 52))
                    pygame.draw.rect(pattern, (48, 48, 58), (self.pixel_size, 0, self.pixel_size, self.pixel_size))
                    pygame.draw.rect(pattern, (48, 48, 58), (0, self.pixel_size, self.pixel_size, self.pixel_size))
                    self.checkerboard_cache[self.pixel_size] = pattern

                pattern_offset_x = (x % 2) * self.pixel_size
                pattern_offset_y = (y % 2) * self.pixel_size
                screen.blit(self.checkerboard_cache[self.pixel_size], (px, py),
                            (pattern_offset_x, pattern_offset_y, self.pixel_size, self.pixel_size))

                # Draw pixel
                if y < len(self.canvas) and x < len(self.canvas[y]):
                    color = self.canvas[y][x]
                    if color[3] > 0:
                        # Determine checkerboard color
                        if (x % 2 == 0 and y % 2 == 0) or (x % 2 == 1 and y % 2 == 1):
                            bg_color = (42, 42, 52)
                        else:
                            bg_color = (48, 48, 58)

                        # Blend with background
                        alpha = color[3] / 255.0
                        blended_r = int(color[0] * alpha + bg_color[0] * (1 - alpha))
                        blended_g = int(color[1] * alpha + bg_color[1] * (1 - alpha))
                        blended_b = int(color[2] * alpha + bg_color[2] * (1 - alpha))

                        # Solid fill, no per-pixel Surface: a fresh
                        # pygame.Surface here (as this used to do) is a
                        # brand-new Python object every call, so
                        # GPUScreen's texture cache (keyed by Surface
                        # identity) can never reuse it — every opaque
                        # pixel meant a fresh GPU texture upload every
                        # single frame. draw_rect with width=0 is a
                        # native SDL fill_rect instead: no texture at all.
                        screen.draw_rect((blended_r, blended_g, blended_b), (px, py, self.pixel_size, self.pixel_size))

                # Grid
                if self.show_grid and self.pixel_size >= 8:
                    screen.draw_rect((60, 60, 70), (px, py, self.pixel_size, self.pixel_size), 1)

        # Draw selection preview
        if self.current_tool == 'select' and self.selection_start:
            if self.selection_preview_end:
                x1, y1 = self.selection_start
                x2, y2 = self.selection_preview_end
            elif self.selection_end:
                x1, y1 = self.selection_start
                x2, y2 = self.selection_end
            else:
                return

            rect_x = self.canvas_offset_x + min(x1, x2) * self.pixel_size
            rect_y = self.canvas_offset_y + min(y1, y2) * self.pixel_size
            rect_w = (abs(x2 - x1) + 1) * self.pixel_size
            rect_h = (abs(y2 - y1) + 1) * self.pixel_size

            # Semi-transparent overlay
            selection_surface = pygame.Surface((rect_w, rect_h), pygame.SRCALPHA)
            selection_surface.fill((255, 255, 100, 30))
            screen.blit(selection_surface, (rect_x, rect_y))

            # Border
            screen.draw_rect((255, 255, 0), (rect_x, rect_y, rect_w, rect_h), 2)

        # Draw magic wand selection
        if self.current_tool == 'magicwand' and self.magic_wand_selection:

            for x, y in self.magic_wand_selection:
                px = self.canvas_offset_x + x * self.pixel_size
                py = self.canvas_offset_y + y * self.pixel_size

                selection_surface = pygame.Surface((self.pixel_size, self.pixel_size), pygame.SRCALPHA)
                selection_surface.fill((100, 200, 255, 100))
                screen.blit(selection_surface, (px, py))

    def _draw_paste_selection(self, screen):
        """Draw the paste selection with transformation handles"""
        if not self.paste_selection or not self.paste_selection_rect:
            return

        # Calculate screen size
        target_screen_width = int(self.paste_selection_rect.width * self.pixel_size * self.paste_scale)
        target_screen_height = int(self.paste_selection_rect.height * self.pixel_size * self.paste_scale)

        if target_screen_width <= 0 or target_screen_height <= 0:
            return

        # Scale surface
        scaled_surface = pygame.transform.scale(
            self.paste_selection,
            (target_screen_width, target_screen_height)
        )

        # Calculate center
        rect_center_x = self.canvas_offset_x + (
                self.paste_selection_rect.x + self.paste_selection_rect.width / 2) * self.pixel_size
        rect_center_y = self.canvas_offset_y + (
                self.paste_selection_rect.y + self.paste_selection_rect.height / 2) * self.pixel_size

        # Apply rotation
        if self.paste_rotation != 0:
            scaled_surface = pygame.transform.rotate(scaled_surface, self.paste_rotation)
            rotated_rect = scaled_surface.get_rect(center=(rect_center_x, rect_center_y))
            draw_x, draw_y = rotated_rect.x, rotated_rect.y
        else:
            draw_x = rect_center_x - target_screen_width // 2
            draw_y = rect_center_y - target_screen_height // 2

        # Draw with transparency
        temp_surface = scaled_surface.copy()
        temp_surface.set_alpha(180)
        screen.blit(temp_surface, (draw_x, draw_y))

        # Draw border
        if self.paste_rotation == 0:
            border_rect = pygame.Rect(draw_x, draw_y, target_screen_width, target_screen_height)
            self._draw_dashed_rect(screen, (255, 255, 100), border_rect, width=2, dash_length=4)

        # Draw handles
        if self.scaling_handles_active and self.paste_handles:
            handle_color = (255, 200, 0)
            hs = self._get_handle_screen_size()

            for handle in self.paste_handles:
                canvas_x, canvas_y = handle['canvas']

                # Normalize position
                rx = (canvas_x - self.paste_selection_rect.x) / self.paste_selection_rect.width
                ry = (canvas_y - self.paste_selection_rect.y) / self.paste_selection_rect.height

                # Calculate screen offset
                dx_screen = (rx - 0.5) * target_screen_width
                dy_screen = (ry - 0.5) * target_screen_height

                if self.paste_rotation != 0:
                    angle_rad = math.radians(-self.paste_rotation)
                    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
                    rot_x = dx_screen * cos_a - dy_screen * sin_a
                    rot_y = dx_screen * sin_a + dy_screen * cos_a
                    screen_x = rect_center_x + rot_x
                    screen_y = rect_center_y + rot_y
                else:
                    screen_x = rect_center_x + dx_screen
                    screen_y = rect_center_y + dy_screen

                handle_rect = pygame.Rect(screen_x - hs, screen_y - hs, hs * 2, hs * 2)
                screen.draw_rect(handle_color, handle_rect, 1)

    def _draw_dashed_rect(self, screen, color, rect, width=1, dash_length=4):
        """Draw a dashed rectangle"""
        x, y, w, h = rect

        # Top
        for i in range(0, w, dash_length * 2):
            start, end = min(i, w), min(i + dash_length, w)
            screen.draw_line(color, (x + start, y), (x + end, y), width)

        # Bottom
        for i in range(0, w, dash_length * 2):
            start, end = min(i, w), min(i + dash_length, w)
            screen.draw_line(color, (x + start, y + h), (x + end, y + h), width)

        # Left
        for i in range(0, h, dash_length * 2):
            start, end = min(i, h), min(i + dash_length, h)
            screen.draw_line(color, (x, y + start), (x, y + end), width)

        # Right
        for i in range(0, h, dash_length * 2):
            start, end = min(i, h), min(i + dash_length, h)
            screen.draw_line(color, (x + w, y + start), (x + w, y + end), width)

    def _draw_tools(self, screen):
        """Draw tool button with dropdown"""
        tool_x, tool_y, tool_size = 10, 90, 36

        # Find current tool
        current_tool = next(t for t in self.tools if t['id'] == self.current_tool)

        # Draw main button
        color, border_color = (64, 128, 255), (96, 160, 255)
        screen.draw_rect(color, (tool_x, tool_y, tool_size, tool_size))
        screen.draw_rect(border_color, (tool_x, tool_y, tool_size, tool_size), 1)

        # Draw icon
        if current_tool['id'] in self.tool_icons:
            icon = self.tool_icons[current_tool['id']]
            icon_rect = icon.get_rect(center=(tool_x + tool_size // 2, tool_y + tool_size // 2))
            screen.blit(icon, icon_rect)

        # Dropdown arrow
        arrow_points = [
            (tool_x + tool_size - 10, tool_y + tool_size - 8),
            (tool_x + tool_size - 6, tool_y + tool_size - 4),
            (tool_x + tool_size - 2, tool_y + tool_size - 8)
        ]
        screen.draw_polygon((200, 200, 200), arrow_points)

        # Tool name
        name_text = self.fonts['small'].render(current_tool['name'], True, (220, 220, 220))
        screen.blit(name_text, (tool_x + tool_size + 10, tool_y + 8))

        # Dropdown menu
        if self.show_tool_dropdown:
            dropdown_width, dropdown_item_height = 140, 32
            dropdown_height = len(self.tools) * dropdown_item_height
            dropdown_x, dropdown_y = tool_x, tool_y + tool_size + 2

            # Background
            screen.draw_rect((45, 45, 55), (dropdown_x, dropdown_y, dropdown_width, dropdown_height))
            screen.draw_rect((96, 160, 255), (dropdown_x, dropdown_y, dropdown_width, dropdown_height), 2)

            # Items
            mouse_x, mouse_y = pygame.mouse.get_pos()

            for i, tool in enumerate(self.tools):
                item_y = dropdown_y + i * dropdown_item_height
                item_rect = pygame.Rect(dropdown_x, item_y, dropdown_width, dropdown_item_height)

                # Highlight
                if item_rect.collidepoint(mouse_x, mouse_y):
                    screen.draw_rect((64, 128, 255), item_rect)
                elif tool['id'] == self.current_tool:
                    screen.draw_rect((55, 55, 65), item_rect)

                # Icon
                if tool['id'] in self.tool_icons:
                    icon = self.tool_icons[tool['id']]
                    icon_rect = icon.get_rect(center=(dropdown_x + 20, item_y + dropdown_item_height // 2))
                    screen.blit(icon, icon_rect)

                # Name
                tool_name = self.fonts['small'].render(tool['name'], True, (220, 220, 220))
                screen.blit(tool_name, (dropdown_x + 40, item_y + 8))

                # Shortcut
                shortcut = self.fonts['small'].render(f"({tool['shortcut']})", True, (150, 150, 150))
                screen.blit(shortcut, (dropdown_x + dropdown_width - 35, item_y + 8))

                # Separator
                if i < len(self.tools) - 1:
                    screen.draw_line((60, 60, 70),
                                     (dropdown_x + 5, item_y + dropdown_item_height),
                                     (dropdown_x + dropdown_width - 5, item_y + dropdown_item_height))

    def _draw_recent_colors(self, screen):
        """Draw recent colors palette"""
        palette_x = self.screen_width - 40
        palette_y = 90
        color_size, color_spacing = 24, 2

        # Title
        title = self.fonts['small'].render("Recent Colors", True, (200, 200, 200))
        screen.blit(title, (palette_x - 120, palette_y - 25))

        for i, color in enumerate(self.recent_colors):
            cy = palette_y + i * (color_size + color_spacing)

            # Color swatch
            screen.draw_rect(color, (palette_x, cy, color_size, color_size))

            # Border
            if color == self.current_color:
                screen.draw_rect((255, 255, 255),
                                 (palette_x - 2, cy - 2, color_size + 4, color_size + 4), 2)
            else:
                screen.draw_rect((60, 60, 70), (palette_x, cy, color_size, color_size), 1)

        # Color wheel button with icon
        wheel_btn_y = palette_y + len(self.recent_colors) * (color_size + color_spacing) + 10

        # Draw icon
        if self.color_wheel_icon:
            icon_rect = self.color_wheel_icon.get_rect(
                center=(palette_x + color_size // 2, wheel_btn_y + color_size // 2))
            screen.blit(self.color_wheel_icon, icon_rect)
        else:
            # Fallback text
            wheel_text = self.fonts['small'].render("W", True, (255, 255, 255))
            text_rect = wheel_text.get_rect(
                center=(palette_x + color_size // 2, wheel_btn_y + color_size // 2))
            screen.blit(wheel_text, text_rect)

        # Tooltip on hover
        mouse_x, mouse_y = pygame.mouse.get_pos()
        if (palette_x <= mouse_x <= palette_x + color_size and
                wheel_btn_y <= mouse_y <= wheel_btn_y + color_size):
            tooltip = self.fonts['small'].render("Color Picker", True, (220, 220, 220))
            screen.blit(tooltip, (palette_x - tooltip.get_width() - 5, wheel_btn_y))

    def _draw_current_color(self, screen):
        """Draw current color preview"""
        preview_x, preview_y, preview_size = self.screen_width - 120, 20, 30

        screen.draw_rect(self.current_color, (preview_x, preview_y, preview_size, preview_size))
        screen.draw_rect((255, 255, 255), (preview_x, preview_y, preview_size, preview_size), 2)

        label = self.fonts['small'].render("Current:", True, (200, 200, 200))
        screen.blit(label, (preview_x - 70, preview_y + 8))

    def _draw_menu_bar(self, screen):
        """Draw top menu bar"""
        screen.draw_rect((35, 35, 42), (0, 0, self.screen_width, 50))
        screen.draw_line((60, 60, 70), (0, 49), (self.screen_width, 49), 1)

        buttons = [
            {'id': 'new', 'label': 'New', 'x': 20, 'w': 80},
            {'id': 'import', 'label': 'Import', 'x': 110, 'w': 90},
            {'id': 'export', 'label': 'Export', 'x': 200, 'w': 90},
        ]

        for btn in buttons:
            btn_color = (74, 148, 255) if self.button_states[btn['id']] else (45, 45, 55)
            screen.draw_rect(btn_color, (btn['x'], 10, btn['w'], 30))
            screen.draw_rect((60, 60, 70), (btn['x'], 10, btn['w'], 30), 1)

            text = self.fonts['small'].render(btn['label'], True, (220, 220, 220))
            text_rect = text.get_rect(center=(btn['x'] + btn['w'] // 2, 25))
            screen.blit(text, text_rect)

        tab_width = 100
        tabs = [
            {'id': 'canvas_tab', 'label': 'Sprite Editor', 'x': 310},
            {'id': 'animation_tab', 'label': 'Animation Viewer', 'x': 420},
        ]

        for tab in tabs:
            if (tab['id'] == 'canvas_tab' and self.current_tab == 'canvas') or \
                    (tab['id'] == 'animation_tab' and self.current_tab == 'animation'):
                tab_color, border_color = (64, 128, 255), (96, 160, 255)
            else:
                tab_color, border_color = (45, 45, 55), (60, 60, 70)

            screen.draw_rect(tab_color, (tab['x'], 10, tab_width, 30))
            screen.draw_rect(border_color, (tab['x'], 10, tab_width, 30), 1)

            text = self.fonts['small'].render(tab['label'], True, (220, 220, 220))
            text_rect = text.get_rect(center=(tab['x'] + tab_width // 2, 25))
            screen.blit(text, text_rect)

    def _draw_canvas_tabs(self, screen):
        """Draw canvas tabs bar"""
        if self.current_tab != 'canvas':
            return

        tab_bar_y, tab_bar_height, tab_width = 50, 35, 120
        tab_spacing, tab_start_x = 5, 0

        # Background
        screen.draw_rect((35, 35, 42), (0, tab_bar_y, self.screen_width, tab_bar_height))
        screen.draw_line((60, 60, 70), (0, tab_bar_y + tab_bar_height - 1),
                         (self.screen_width, tab_bar_y + tab_bar_height - 1), 1)

        # Tabs
        for i, canvas in enumerate(self.canvases):
            tab_x = tab_start_x + i * (tab_width + tab_spacing)

            if tab_x > self.screen_width - tab_width:
                continue

            # Tab style
            if i == self.current_canvas_index:
                tab_color, border_color, text_color = (64, 128, 255), (96, 160, 255), (255, 255, 255)
            else:
                tab_color, border_color, text_color = (45, 45, 55), (60, 60, 70), (200, 200, 200)

            screen.draw_rect(tab_color, (tab_x, tab_bar_y + 2, tab_width, tab_bar_height - 4))
            screen.draw_rect(border_color, (tab_x, tab_bar_y + 2, tab_width, tab_bar_height - 4), 1)

            # Canvas name
            name = canvas['name']
            if len(name) > 12:
                name = name[:12] + "..."
            if canvas['unsaved']:
                name = "● " + name

            name_text = self.fonts['small'].render(name, True, text_color)
            screen.blit(name_text, (tab_x + 8, tab_bar_y + 10))

            # Close button
            if len(self.canvases) > 1:
                close_btn_x, close_btn_y = tab_x + tab_width - 22, tab_bar_y + 8
                close_btn_size = 18

                mouse_x, mouse_y = pygame.mouse.get_pos()
                if close_btn_x <= mouse_x <= close_btn_x + close_btn_size and \
                        close_btn_y <= mouse_y <= close_btn_y + close_btn_size:
                    screen.draw_rect((255, 100, 100),
                                     (close_btn_x, close_btn_y, close_btn_size, close_btn_size),
                                     border_radius=3)

                x_color = (255, 255, 255) if i == self.current_canvas_index else (200, 200, 200)
                screen.draw_line(x_color,
                                 (close_btn_x + 4, close_btn_y + 4),
                                 (close_btn_x + close_btn_size - 4, close_btn_y + close_btn_size - 4), 2)
                screen.draw_line(x_color,
                                 (close_btn_x + close_btn_size - 4, close_btn_y + 4),
                                 (close_btn_x + 4, close_btn_y + close_btn_size - 4), 2)

        # New canvas button
        new_btn_x = tab_start_x + len(self.canvases) * (tab_width + tab_spacing)
        new_btn_size, new_btn_y = 30, tab_bar_y + 2

        if new_btn_x < self.screen_width - new_btn_size:
            mouse_x, mouse_y = pygame.mouse.get_pos()
            btn_color = (64, 128, 255) if new_btn_x <= mouse_x <= new_btn_x + new_btn_size and \
                                          new_btn_y <= mouse_y <= new_btn_y + new_btn_size else (55, 55, 65)

            screen.draw_rect(btn_color, (new_btn_x, new_btn_y, new_btn_size, new_btn_size), border_radius=4)
            screen.draw_rect((100, 100, 110), (new_btn_x, new_btn_y, new_btn_size, new_btn_size),
                             1, border_radius=4)

            plus_text = self.fonts['large'].render("+", True, (220, 220, 220))
            plus_rect = plus_text.get_rect(center=(new_btn_x + new_btn_size // 2, new_btn_y + new_btn_size // 2))
            screen.blit(plus_text, plus_rect)

    def _draw_status_bar(self, screen):
        """Draw bottom status bar"""
        status_y = self.screen_height - 30
        screen.draw_rect((35, 35, 42), (0, status_y, self.screen_width, 30))
        screen.draw_line((60, 60, 70), (0, status_y), (self.screen_width, status_y), 1)

        # Info text
        info_parts = [
            f"{self.canvas_width}x{self.canvas_height}",
            f"Zoom: {self.pixel_size}px",
            f"Grid: {'ON' if self.show_grid else 'OFF'}",
        ]

        if self.current_file:
            info_parts.insert(0, f"{os.path.basename(self.current_file)}")
        if self.unsaved_changes:
            info_parts.append("●")

        # Assemble info
        info_text = " | ".join(info_parts)
        info_surface = self.fonts['small'].render(info_text, True, (200, 200, 200))
        screen.blit(info_surface, (10, status_y + 8))

    def _draw_canvas_size_dialog(self, screen):
        """Draw canvas size input dialog"""
        # Overlay
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        screen.blit(overlay, (0, 0))

        # Dialog box
        box_width, box_height = 400, 150
        box_x = (self.screen_width - box_width) // 2
        box_y = (self.screen_height - box_height) // 2

        screen.draw_rect((45, 45, 55), (box_x, box_y, box_width, box_height))
        screen.draw_rect((64, 128, 255), (box_x, box_y, box_width, box_height), 3)

        # Title
        field_label = "Width" if self.canvas_size_field == "width" else "Height"
        title = self.fonts['medium'].render(f"Enter Canvas {field_label}:", True, (255, 255, 255))
        screen.blit(title, (box_x + 20, box_y + 20))

        # Input field
        input_rect = pygame.Rect(box_x + 20, box_y + 60, box_width - 40, 40)
        screen.draw_rect((60, 60, 70), input_rect)
        screen.draw_rect((255, 255, 255), input_rect, 2)

        input_text = self.fonts['large'].render(self.canvas_size_input, True, (255, 255, 255))
        screen.blit(input_text, (box_x + 30, box_y + 68))

        # Hint
        hint = self.fonts['small'].render("Press ENTER to continue, ESC to cancel", True, (180, 180, 180))
        screen.blit(hint, (box_x + 20, box_y + 115))

    def _draw_color_wheel(self, screen):
        """Draw Paint.NET style color wheel picker with HSV sliders"""
        # Overlay
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        screen.blit(overlay, (0, 0))

        # Panel dimensions
        panel_width = 350
        panel_height = 320
        panel_x = self.color_wheel_pos[0]
        panel_y = self.color_wheel_pos[1]

        # Main panel background
        screen.draw_rect((50, 50, 58), (panel_x, panel_y, panel_width, panel_height), border_radius=8)
        screen.draw_rect((80, 80, 90), (panel_x, panel_y, panel_width, panel_height), 2, border_radius=8)

        # Title
        title = self.fonts['medium'].render("Colors", True, (220, 220, 220))
        screen.blit(title, (panel_x + 10, panel_y + 10))

        # === COLOR WHEEL ===
        wheel_radius = 80
        wheel_center_x = panel_x + 100
        wheel_center_y = panel_y + 120

        wheel_surface = pygame.Surface((wheel_radius * 2, wheel_radius * 2), pygame.SRCALPHA)

        # Draw the HSV wheel
        for y in range(wheel_radius * 2):
            for x in range(wheel_radius * 2):
                dx, dy = x - wheel_radius, y - wheel_radius
                distance = math.sqrt(dx * dx + dy * dy)

                if distance <= wheel_radius:
                    angle = math.degrees(math.atan2(dy, dx))
                    if angle < 0:
                        angle += 360

                    saturation = distance / wheel_radius
                    color = self.hsv_to_rgb(angle, saturation, self.selected_value)
                    wheel_surface.set_at((x, y), color)

        screen.blit(wheel_surface, (wheel_center_x - wheel_radius, wheel_center_y - wheel_radius))

        # Wheel border
        pygame.gfxdraw.aacircle(screen, int(wheel_center_x), int(wheel_center_y), wheel_radius, (80, 80, 90))

        # Selection indicator (crosshair)
        indicator_x = wheel_center_x + int(math.cos(math.radians(self.selected_hue)) *
                                           self.selected_saturation * wheel_radius)
        indicator_y = wheel_center_y + int(math.sin(math.radians(self.selected_hue)) *
                                           self.selected_saturation * wheel_radius)

        # Outer white circle
        screen.draw_circle((255, 255, 255), (indicator_x, indicator_y), 5, 2)
        # Inner black circle
        screen.draw_circle((0, 0, 0), (indicator_x, indicator_y), 6, 1)

        # === VALUE SLIDER (below wheel) ===
        slider_y = wheel_center_y - 10 + wheel_radius + 25
        slider_x = wheel_center_x - 6 - wheel_radius
        slider_width = wheel_radius * 2
        slider_height = 16

        # Draw gradient
        for i in range(slider_width):
            progress = i / slider_width
            color = self.hsv_to_rgb(self.selected_hue, self.selected_saturation, progress)
            screen.draw_line(color,
                             (slider_x + i, slider_y),
                             (slider_x + i, slider_y + slider_height))

        # Slider border
        screen.draw_rect((100, 100, 110), (slider_x, slider_y, slider_width, slider_height + 1), 1)

        # Value indicator
        value_pos = slider_x + int(self.selected_value * slider_width)
        screen.draw_line((255, 255, 255),
                         (value_pos, slider_y - 2),
                         (value_pos, slider_y + slider_height + 2), 3)
        screen.draw_line((0, 0, 0),
                         (value_pos, slider_y - 2),
                         (value_pos, slider_y + slider_height + 2), 1)

        # === RGB SLIDERS (right side) ===
        rgb_label_x = panel_x + 210
        rgb_slider_x = panel_x + 220
        rgb_value_x = panel_x + 315
        rgb_slider_width = 90
        rgb_slider_height = 16

        # RGB Label
        rgb_title = self.fonts['small'].render("RGB", True, (180, 180, 180))
        screen.blit(rgb_title, (rgb_label_x + 35, panel_y + 25))

        # R Slider
        r_slider_y = panel_y + 50
        r_label = self.fonts['small'].render("R:", True, (255, 100, 100))
        screen.blit(r_label, (rgb_label_x, r_slider_y + 2))

        # R gradient
        for i in range(rgb_slider_width):
            r_val = int((i / rgb_slider_width) * 255)
            color = (r_val, self.current_color[1], self.current_color[2])
            screen.draw_line(color,
                             (rgb_slider_x + i, r_slider_y),
                             (rgb_slider_x + i, r_slider_y + rgb_slider_height))

        screen.draw_rect((100, 100, 110), (rgb_slider_x, r_slider_y, rgb_slider_width, rgb_slider_height + 1),
                         1)

        # R indicator
        r_pos = rgb_slider_x + int((self.current_color[0] / 255) * rgb_slider_width)
        screen.draw_line((255, 255, 255),
                         (r_pos, r_slider_y - 2),
                         (r_pos, r_slider_y + rgb_slider_height + 2), 2)

        # R value
        r_text = self.fonts['small'].render(str(self.current_color[0]), True, (220, 220, 220))
        screen.blit(r_text, (rgb_value_x, r_slider_y + 2))

        # G Slider
        g_slider_y = panel_y + 90
        g_label = self.fonts['small'].render("G:", True, (100, 255, 100))
        screen.blit(g_label, (rgb_label_x, g_slider_y + 2))

        # G gradient
        for i in range(rgb_slider_width):
            g_val = int((i / rgb_slider_width) * 255)
            color = (self.current_color[0], g_val, self.current_color[2])
            screen.draw_line(color,
                             (rgb_slider_x + i, g_slider_y),
                             (rgb_slider_x + i, g_slider_y + rgb_slider_height))

        screen.draw_rect((100, 100, 110), (rgb_slider_x, g_slider_y, rgb_slider_width, rgb_slider_height + 1),
                         1)

        # G indicator
        g_pos = rgb_slider_x + int((self.current_color[1] / 255) * rgb_slider_width)
        screen.draw_line((255, 255, 255),
                         (g_pos, g_slider_y - 2),
                         (g_pos, g_slider_y + rgb_slider_height + 2), 2)

        # G value
        g_text = self.fonts['small'].render(str(self.current_color[1]), True, (220, 220, 220))
        screen.blit(g_text, (rgb_value_x, g_slider_y + 2))

        # B Slider
        b_slider_y = panel_y + 130
        b_label = self.fonts['small'].render("B:", True, (100, 150, 255))
        screen.blit(b_label, (rgb_label_x, b_slider_y + 2))

        # B gradient
        for i in range(rgb_slider_width):
            b_val = int((i / rgb_slider_width) * 255)
            color = (self.current_color[0], self.current_color[1], b_val)
            screen.draw_line(color,
                             (rgb_slider_x + i, b_slider_y),
                             (rgb_slider_x + i, b_slider_y + rgb_slider_height))

        screen.draw_rect((100, 100, 110), (rgb_slider_x, b_slider_y, rgb_slider_width, rgb_slider_height + 1),
                         1)

        # B indicator
        b_pos = rgb_slider_x + int((self.current_color[2] / 255) * rgb_slider_width)
        screen.draw_line((255, 255, 255),
                         (b_pos, b_slider_y - 2),
                         (b_pos, b_slider_y + rgb_slider_height + 2), 2)

        # B value
        b_text = self.fonts['small'].render(str(self.current_color[2]), True, (220, 220, 220))
        screen.blit(b_text, (rgb_value_x, b_slider_y + 2))

        # === HSV SLIDERS ===
        hsv_label_x = panel_x + 210
        hsv_slider_x = panel_x + 220
        hsv_value_x = panel_x + 315
        hsv_slider_width = 90
        hsv_slider_height = 16

        # HSV Title
        hsv_title = self.fonts['small'].render("HSV", True, (180, 180, 180))
        screen.blit(hsv_title, (hsv_label_x + 35, panel_y + 165))

        # H Slider
        h_slider_y = panel_y + 180
        h_label = self.fonts['small'].render("H:", True, (200, 200, 200))
        screen.blit(h_label, (hsv_label_x, h_slider_y + 2))

        # H gradient (rainbow)
        for i in range(hsv_slider_width):
            hue = (i / hsv_slider_width) * 360
            color = self.hsv_to_rgb(hue, 1.0, 1.0)
            screen.draw_line(color,
                             (hsv_slider_x + i, h_slider_y),
                             (hsv_slider_x + i, h_slider_y + hsv_slider_height))

        screen.draw_rect((100, 100, 110),
                         (hsv_slider_x, h_slider_y, hsv_slider_width, hsv_slider_height + 1), 1)

        # H indicator
        h_pos = hsv_slider_x + int((self.selected_hue / 360) * hsv_slider_width)
        screen.draw_line((255, 255, 255),
                         (h_pos, h_slider_y - 2),
                         (h_pos, h_slider_y + hsv_slider_height + 2), 2)

        # H value
        h_text = self.fonts['small'].render(f"{int(self.selected_hue)}°", True, (220, 220, 220))
        screen.blit(h_text, (hsv_value_x, h_slider_y + 2))

        # S Slider
        s_slider_y = panel_y + 210
        s_label = self.fonts['small'].render("S:", True, (200, 200, 200))
        screen.blit(s_label, (hsv_label_x, s_slider_y + 2))

        # S gradient (white to full saturation)
        for i in range(hsv_slider_width):
            saturation = i / hsv_slider_width
            color = self.hsv_to_rgb(self.selected_hue, saturation, 1.0)
            screen.draw_line(color,
                             (hsv_slider_x + i, s_slider_y),
                             (hsv_slider_x + i, s_slider_y + hsv_slider_height))

        screen.draw_rect((100, 100, 110),
                         (hsv_slider_x, s_slider_y, hsv_slider_width, hsv_slider_height + 1), 1)

        # S indicator
        s_pos = hsv_slider_x + int(self.selected_saturation * hsv_slider_width)
        screen.draw_line((255, 255, 255),
                         (s_pos, s_slider_y - 2),
                         (s_pos, s_slider_y + hsv_slider_height + 2), 2)

        # S value
        s_text = self.fonts['small'].render(f"{int(self.selected_saturation * 100)}%", True, (220, 220, 220))
        screen.blit(s_text, (hsv_value_x, s_slider_y + 2))

        # V Slider
        v_slider_y = panel_y + 240
        v_label = self.fonts['small'].render("V:", True, (200, 200, 200))
        screen.blit(v_label, (hsv_label_x, v_slider_y + 2))

        # V gradient (black to full value)
        for i in range(hsv_slider_width):
            value = i / hsv_slider_width
            color = self.hsv_to_rgb(self.selected_hue, self.selected_saturation, value)
            screen.draw_line(color,
                             (hsv_slider_x + i, v_slider_y),
                             (hsv_slider_x + i, v_slider_y + hsv_slider_height))

        screen.draw_rect((100, 100, 110),
                         (hsv_slider_x, v_slider_y, hsv_slider_width, hsv_slider_height + 1), 1)

        # V indicator
        v_pos = hsv_slider_x + int(self.selected_value * hsv_slider_width)
        screen.draw_line((255, 255, 255),
                         (v_pos, v_slider_y - 2),
                         (v_pos, v_slider_y + hsv_slider_height + 2), 2)

        # V value
        v_text = self.fonts['small'].render(f"{int(self.selected_value * 100)}%", True, (220, 220, 220))
        screen.blit(v_text, (hsv_value_x, v_slider_y + 2))

        # === HEX INPUT FIELD ===
        hex_input_y = panel_y + 270
        hex_label = self.fonts['small'].render("Hex:", True, (180, 180, 180))
        screen.blit(hex_label, (hsv_label_x - 13, hex_input_y + 6))

        # Input box
        hex_input_rect = pygame.Rect(hsv_slider_x, hex_input_y, hsv_slider_width, 25)
        screen.draw_rect((60, 60, 70), hex_input_rect)

        # Highlight if active
        if self.hex_input_active:
            screen.draw_rect((255, 255, 255), hex_input_rect, 2)
        else:
            screen.draw_rect((100, 100, 110), hex_input_rect, 1)

        # Display hex text
        hex_display = self.hex_input_text if self.hex_input_active else \
            f"{self.current_color[0]:02X}{self.current_color[1]:02X}{self.current_color[2]:02X}"
        hex_text = self.fonts['small'].render(hex_display, True, (220, 220, 220))
        screen.blit(hex_text, (hsv_slider_x + 5, hex_input_y + 5))

        # Cursor if active
        if self.hex_input_active:
            cursor_x = hsv_slider_x + 5 + hex_text.get_width()
            cursor_y = hex_input_y + 5
            screen.draw_line((255, 255, 255),
                             (cursor_x, cursor_y),
                             (cursor_x, cursor_y + 15), 2)

        # === COLOR PREVIEW ===
        preview_size = 48
        preview_x = panel_x + 15
        preview_y = panel_y + 300

        # Checkerboard background
        checker_size = 8
        for py in range(0, preview_size, checker_size):
            for px in range(0, preview_size, checker_size):
                color = (180, 180, 180) if (px // checker_size + py // checker_size) % 2 == 0 else (140, 140, 140)

                screen.draw_rect(color, (preview_x + px, preview_y + py - 55, checker_size, checker_size))

        # Current color
        screen.draw_rect((*self.current_color, 255), (preview_x, preview_y - 55, preview_size, preview_size))
        screen.draw_rect((255, 255, 255), (preview_x, preview_y, preview_size - 55, preview_size), 2)

        # Close hint
        hint = self.fonts['small'].render("ESC to close", True, (120, 120, 120))
        screen.blit(hint, (panel_x + 10, panel_y + panel_height - 25))

    def _draw_animation_preview(self, screen):
        """Draw animation preview tab"""
        panel_x, panel_y = 60, 60

        # Direction buttons
        dir_btn_y, dir_width, dir_height = 60, 60, 35
        directions = [
            {'id': 'down', 'label': 'Down', 'color': (64, 128, 255), 'x': panel_x},
            {'id': 'left', 'label': 'Left', 'color': (255, 165, 0), 'x': panel_x + 70},
            {'id': 'right', 'label': 'Right', 'color': (50, 205, 50), 'x': panel_x + 140},
            {'id': 'up', 'label': 'Up', 'color': (255, 105, 180), 'x': panel_x + 210}
        ]

        for direction in directions:
            dir_x = direction['x']

            # Button style
            if self.anim_current_direction == direction['id']:
                btn_color, border_color, text_color = direction['color'], (255, 255, 255), (255, 255, 255)
            else:
                btn_color, border_color, text_color = (60, 60, 70), (100, 100, 110), (200, 200, 200)

            screen.draw_rect(btn_color, (dir_x, dir_btn_y, dir_width, dir_height), border_radius=5)
            screen.draw_rect(border_color, (dir_x, dir_btn_y, dir_width, dir_height), 2, border_radius=5)

            # Text
            arrow_text = self.fonts['small'].render(direction['label'], True, text_color)
            arrow_rect = arrow_text.get_rect(center=(dir_x + dir_width // 2, dir_btn_y + dir_height // 2))
            screen.blit(arrow_text, arrow_rect)

        # Flip checkbox (left direction only)
        if self.anim_current_direction == 'left':
            checkbox_x, checkbox_y, checkbox_size = 340, dir_btn_y, 25

            # Checkbox style
            if self.anim_flip_left:
                checkbox_color, border_color = (255, 165, 0), (255, 200, 100)
            else:
                checkbox_color, border_color = (60, 60, 70), (100, 100, 110)

            screen.draw_rect(checkbox_color, (checkbox_x, checkbox_y, checkbox_size, checkbox_size),
                             border_radius=4)
            screen.draw_rect(border_color, (checkbox_x, checkbox_y, checkbox_size, checkbox_size),
                             2, border_radius=4)

            # Checkmark
            if self.anim_flip_left:
                check_color = (255, 255, 255)
                screen.draw_line(check_color,
                                 (checkbox_x + 5, checkbox_y + checkbox_size // 2),
                                 (checkbox_x + checkbox_size // 2 - 2, checkbox_y + checkbox_size - 7), 3)
                screen.draw_line(check_color,
                                 (checkbox_x + checkbox_size // 2 - 2, checkbox_y + checkbox_size - 7),
                                 (checkbox_x + checkbox_size - 7, checkbox_y + 5), 3)

            # Label
            checkbox_label = self.fonts['small'].render("Flip", True, (220, 220, 220))
            screen.blit(checkbox_label, (checkbox_x + checkbox_size + 8, checkbox_y + 6))

        # Preview area
        preview_x = self.screen_width // 2
        preview_y = self.screen_height // 2 + 40

        if self.anim_frames:
            current_frame = self.anim_frames[self.anim_frame_index]
            scaled_width = int(self.anim_sprite_width * self.anim_preview_scale)
            scaled_height = int(self.anim_sprite_height * self.anim_preview_scale)

            scaled_frame = pygame.transform.scale(current_frame, (scaled_width, scaled_height))

            # Checkerboard background
            checker_size = 16
            for y in range(0, scaled_height, checker_size):
                for x in range(0, scaled_width, checker_size):
                    color = (42, 42, 52) if (x // checker_size + y // checker_size) % 2 == 0 else (48, 48, 58)
                    screen.draw_rect(color,
                                     (preview_x - scaled_width // 2 + x,
                                      preview_y - scaled_height // 2 + y,
                                      checker_size, checker_size))

            # Draw frame
            screen.blit(scaled_frame,
                        (preview_x - scaled_width // 2,
                         preview_y - scaled_height // 2))

            # Border
            screen.draw_rect((100, 100, 110),
                             (preview_x - scaled_width // 2 - 2,
                              preview_y - scaled_height // 2 - 2,
                              scaled_width + 4, scaled_height + 4), 2)

            # Frame info and play button
            frame_text = f"Frame {self.anim_frame_index + 1}/{len(self.anim_frames)}"
            frame_surface = self.fonts['medium'].render(frame_text, True, (220, 220, 220))

            # Play button
            play_btn_x = preview_x - (frame_surface.get_width() // 2) - 30
            play_btn_y = preview_y + scaled_height // 2 + 20
            play_btn_size = 24

            btn_color = (64, 200, 64) if self.anim_playing else (64, 128, 255)
            screen.draw_rect(btn_color, (play_btn_x, play_btn_y, play_btn_size, play_btn_size), border_radius=4)
            screen.draw_rect((255, 255, 255), (play_btn_x, play_btn_y, play_btn_size, play_btn_size),
                             1, border_radius=4)

            # Play/pause icon
            if self.anim_playing:
                screen.draw_rect((255, 255, 255),
                                 (play_btn_x + 7, play_btn_y + 6, 3, 12))
                screen.draw_rect((255, 255, 255),
                                 (play_btn_x + 14, play_btn_y + 6, 3, 12))
            else:
                points = [
                    (play_btn_x + 8, play_btn_y + 6),
                    (play_btn_x + 8, play_btn_y + 18),
                    (play_btn_x + 18, play_btn_y + 12)
                ]
                screen.draw_polygon((255, 255, 255), points)

            # Frame text
            screen.blit(frame_surface,
                        (preview_x - frame_surface.get_width() // 2,
                         preview_y + scaled_height // 2 + 20))

            # Speed controls
            speed_y = preview_y + scaled_height // 2 + 50
            speed_label = self.fonts['small'].render(f"Speed: {self.anim_speed:.1f}x", True, (220, 220, 220))
            screen.blit(speed_label, (preview_x - speed_label.get_width() // 2, speed_y))

            # Speed buttons
            speed_btn_y = speed_y + 20
            minus_btn_x, plus_btn_x = preview_x - 50, preview_x + 10

            # Minus button
            screen.draw_rect((60, 60, 70), (minus_btn_x, speed_btn_y, 40, 25), border_radius=4)
            screen.draw_rect((255, 255, 255), (minus_btn_x, speed_btn_y, 40, 25), 1, border_radius=4)
            minus_text = self.fonts['medium'].render("-", True, (255, 255, 255))
            minus_rect = minus_text.get_rect(center=(minus_btn_x + 20, speed_btn_y + 12))
            screen.blit(minus_text, minus_rect)

            # Plus button
            screen.draw_rect((60, 60, 70), (plus_btn_x, speed_btn_y, 40, 25), border_radius=4)
            screen.draw_rect((255, 255, 255), (plus_btn_x, speed_btn_y, 40, 25), 1, border_radius=4)
            plus_text = self.fonts['medium'].render("+", True, (255, 255, 255))
            plus_rect = plus_text.get_rect(center=(plus_btn_x + 20, speed_btn_y + 12))
            screen.blit(plus_text, plus_rect)

            # Filmstrip
            filmstrip_y = speed_btn_y + 35
            filmstrip_frame_size = 48
            filmstrip_spacing = 4
            filmstrip_total_width = len(self.anim_frames) * (filmstrip_frame_size + filmstrip_spacing)
            filmstrip_start_x = preview_x - filmstrip_total_width // 2

            for i, frame in enumerate(self.anim_frames):
                frame_x = filmstrip_start_x + i * (filmstrip_frame_size + filmstrip_spacing)

                # Checkerboard
                for y in range(0, filmstrip_frame_size, 8):
                    for x in range(0, filmstrip_frame_size, 8):
                        color = (42, 42, 52) if (x // 8 + y // 8) % 2 == 0 else (48, 48, 58)
                        screen.draw_rect(color, (frame_x + x, filmstrip_y + y, 8, 8))

                small_frame = pygame.transform.scale(frame, (filmstrip_frame_size, filmstrip_frame_size))
                screen.blit(small_frame, (frame_x, filmstrip_y))

                # Border
                border_color = (255, 200, 0) if i == self.anim_frame_index else (80, 80, 90)
                border_width = 3 if i == self.anim_frame_index else 1
                screen.draw_rect(border_color,
                                 (frame_x, filmstrip_y, filmstrip_frame_size, filmstrip_frame_size),
                                 border_width)
        else:
            # No frames
            no_frames_text = self.fonts['large'].render("No animation loaded", True, (150, 150, 150))
            screen.blit(no_frames_text,
                        (preview_x - no_frames_text.get_width() // 2,
                         preview_y - no_frames_text.get_height() // 2))

            help_text = self.fonts['small'].render("Import a sprite sheet to preview", True, (120, 120, 120))
            screen.blit(help_text,
                        (preview_x - help_text.get_width() // 2,
                         preview_y + 20))

    def _draw_custom_cursor(self, screen):
        """Draw custom tool cursor"""
        if not self.show_custom_cursor or self.current_tool not in self.tool_cursors:
            return

        mouse_x, mouse_y = pygame.mouse.get_pos()
        cursor_surface = self.tool_cursors[self.current_tool]

        # Draw with shadow
        shadow_surface = cursor_surface.copy()
        shadow_surface.fill((0, 0, 0, 128), special_flags=pygame.BLEND_RGBA_MULT)
        screen.blit(shadow_surface, (mouse_x - 11, mouse_y - 11))
        screen.blit(cursor_surface, (mouse_x - 12, mouse_y - 12))