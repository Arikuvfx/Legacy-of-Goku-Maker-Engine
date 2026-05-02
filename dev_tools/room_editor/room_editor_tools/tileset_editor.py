import pygame
import pygame.gfxdraw
import os
import json
import math
from typing import List, Tuple, Optional, Set
from enum import Enum
from config.settings import RENDER_SCALE, TILE_SIZE


class TileType(Enum):
    GROUND = 0
    CLIFF = 1
    WATER = 2
    DECOR = 3


class TileDef:
    def __init__(self, tile_type, autotile=False, solid=False):
        self.tile_type = tile_type
        self.autotile = autotile
        self.solid = solid


def detect_tile_size(image: pygame.Surface) -> int:
    """Infer tile size from image dimensions — tries 16px first, then 8px."""
    w, h = image.get_size()
    for size in (16, 8):
        if w % size == 0 and h % size == 0:
            return size
    raise ValueError("Could not auto-detect tile size")


class Tile:
    """A single placed tile — tracks position, tileset source, and render layer."""

    def __init__(self, x: int, y: int, tileset_name: str, tile_x: int, tile_y: int,
                 layer: int = -100, foreground: bool = False):
        self.x = x
        self.y = y
        self.tileset_name = tileset_name
        self.tile_x = tile_x
        self.tile_y = tile_y
        self.layer = layer
        self.foreground = foreground
        self.draw_layer = layer

    def to_dict(self):
        """Pack tile data into a dict for JSON serialization."""
        return {
            'x': self.x,
            'y': self.y,
            'tileset': self.tileset_name,
            'tile_x': self.tile_x,
            'tile_y': self.tile_y,
            'layer': self.layer,
            'foreground': self.foreground
        }

    @staticmethod
    def from_dict(data):
        """Reconstruct a Tile from saved dict data."""
        layer = data.get('layer', 75 if data.get('foreground', False) else -100)
        return Tile(
            data['x'],
            data['y'],
            data.get('tileset') or data.get('tileset_name', ''),
            data['tile_x'],
            data['tile_y'],
            layer,
            data.get('foreground', False)
        )


class Tileset:
    def __init__(self, name: str, image_path: str):
        self.name = name
        self.image = None
        self.tile_width = 16
        self.tile_height = 16
        self.cols = 0
        self.rows = 0
        self._tile_transparency_cache = {}
        self._scaled_cache = {}  # key: (tile_x, tile_y, scale) → pre-scaled Surface

        try:
            self.image = pygame.image.load(image_path).convert_alpha()
            self.tile_width = detect_tile_size(self.image)
            self.tile_height = self.tile_width
            w, h = self.image.get_size()
            self.cols = w // self.tile_width
            self.rows = h // self.tile_height
            self._build_transparency_cache()
        except (pygame.error, ValueError) as e:
            print(f"Error loading tileset {name}: {e}")

    def _build_transparency_cache(self):
        """Pre-scan every tile and note which ones are fully transparent — skips empty blits at draw time."""
        if not self.image:
            return

        for ty in range(self.rows):
            for tx in range(self.cols):
                tile_surface = self.get_tile_surface(tx, ty)
                if tile_surface:
                    self._tile_transparency_cache[(tx, ty)] = self._is_tile_transparent(tile_surface)

    def _is_tile_transparent(self, surface: pygame.Surface) -> bool:
        """Returns True if every pixel has alpha == 0."""
        if not (surface.get_flags() & pygame.SRCALPHA):
            return False

        width, height = surface.get_size()
        for y in range(height):
            for x in range(width):
                r, g, b, a = surface.get_at((x, y))
                if a > 0:
                    return False

        return True

    def is_tile_empty(self, tile_x: int, tile_y: int) -> bool:
        """Returns True if the tile at (tile_x, tile_y) was fully transparent when the cache was built."""
        return self._tile_transparency_cache.get((tile_x, tile_y), False)

    def get_tile_surface(self, tile_x: int, tile_y: int) -> Optional[pygame.Surface]:
        """Extract and return a copy of the tile surface at grid coords (tile_x, tile_y)."""
        if not self.image or tile_x >= self.cols or tile_y >= self.rows or tile_x < 0 or tile_y < 0:
            return None

        rect = pygame.Rect(
            tile_x * self.tile_width,
            tile_y * self.tile_height,
            self.tile_width,
            self.tile_height
        )
        return self.image.subsurface(rect).copy()

    def get_scaled_tile_surface(self, tile_x: int, tile_y: int, scale: int) -> Optional[pygame.Surface]:
        """Return a pre-scaled tile surface, creating and caching it on first call.

        This avoids calling pygame.transform.scale() and subsurface().copy() on
        every tile every frame — the heavy work happens once and is reused.
        """
        key = (tile_x, tile_y, scale)
        if key not in self._scaled_cache:
            raw = self.get_tile_surface(tile_x, tile_y)
            if raw is None:
                self._scaled_cache[key] = None
            else:
                self._scaled_cache[key] = pygame.transform.scale(
                    raw, (self.tile_width * scale, self.tile_height * scale)
                )
        return self._scaled_cache[key]

    def invalidate_scaled_cache(self):
        """Clear the scaled surface cache (call if the tileset image is replaced at runtime)."""
        self._scaled_cache.clear()


class TilesetManager:
    """Holds every loaded tileset and hands them out by name."""

    def __init__(self):
        self.tilesets: dict[str, Tileset] = {}
        self.tileset_names: List[str] = []

    def load_tilesets_from_folder(self, folder_path: str):
        """Scan a folder and load every .png file as a tileset."""
        if not os.path.exists(folder_path):
            print(f"Tileset folder not found: {folder_path}")
            return

        for file_name in os.listdir(folder_path):
            if file_name.lower().endswith('.png'):
                name = os.path.splitext(file_name)[0]
                path = os.path.join(folder_path, file_name)
                self.load_tileset(name, path)

    def load_default_tilesets(self):
        """Load tilesets from the default assets/tilesets/ folder. Falls back to a placeholder if none are found."""
        default_path = "assets/tilesets/"
        self.load_tilesets_from_folder(default_path)

        if not self.tilesets:
            placeholder = Tileset("placeholder", "")
            self.tilesets["placeholder"] = placeholder
            self.tileset_names.append("placeholder")

    def load_tileset(self, name: str, image_path: str):
        """Load a single tileset by name and file path."""
        tileset = Tileset(name, image_path)
        self.tilesets[name] = tileset
        if name not in self.tileset_names:
            self.tileset_names.append(name)

    def get_tileset(self, name: str) -> Optional[Tileset]:
        """Return the named tileset, or None if it hasn't been loaded."""
        return self.tilesets.get(name)


class TilesetEditor:
    """Tileset palette and tile-painting editor."""

    LAYER_PRESETS = [
        ("Ground", -100),
        ("Floor Decor", -75),
        ("Shadows", -50),
        ("Background", -25),
        ("Base", 0),
        ("Foreground", 75),
        ("Top", 100),
        ("Custom...", None)
    ]

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width = screen_width
        self.screen_height = screen_height

        self.tileset_manager = TilesetManager()
        self.tileset_manager.load_default_tilesets()

        self.palette_scroll_x = 0
        self.palette_scroll_y = 0

        self.active = False
        self.current_tileset_index = 0
        self.selected_tile_x = 0
        self.selected_tile_y = 0

        self.selection_start_x = 0
        self.selection_start_y = 0
        self.selection_end_x = 0
        self.selection_end_y = 0
        self.is_multi_selecting = False

        # ── Layer state ──────────────────────────────────────────────────────
        self.current_layer_preset_index = 0
        self.current_layer = -100
        self.custom_layer_value = -100
        self.delete_underlying = True
        self.hide_other_layers = False
        self.layer_dropdown_open = False
        self.layer_input_active = False
        self.layer_input_text = ""

        self.foreground_mode = False

        # ── Palette geometry ─────────────────────────────────────────────────
        self.palette_width = 600
        self.palette_x = screen_width - self.palette_width
        self.palette_y = 100
        self.palette_content_height = 500
        self.tileset_area_y_offset = 35

        self.grid_cell_size = 32
        self.show_grid = True

        self.font_large = pygame.font.Font(None, 32)
        self.font_medium = pygame.font.Font(None, 24)
        self.font_small = pygame.font.Font(None, 18)

        self.colors = {
            'bg': (20, 20, 30),
            'panel': (30, 30, 45),
            'panel_light': (45, 45, 65),
            'accent': (255, 215, 0),
            'selection': (100, 150, 255),
            'text': (255, 255, 255),
            'text_dim': (180, 180, 200),
            'success': (100, 255, 100),
            'danger': (255, 100, 100),
            'grid': (255, 255, 255, 80),
            'grid_dim': (255, 255, 255, 30),
            'button': (60, 60, 80),
            'button_hover': (80, 80, 100),
            'checkbox': (100, 100, 120)
        }

        self.grid_size = 16
        self.room_tiles: dict[str, List[Tile]] = {}

        self.is_dragging = False
        self.is_erasing = False
        self.drag_start_pos = None
        self.is_palette_dragging = False

        self.ui_rects = {}

    def toggle(self):
        """Open or close the tileset editor."""
        self.active = not self.active

    def get_current_tileset(self) -> Optional[Tileset]:
        """Return the active tileset object, or None if the list is empty."""
        names = self.tileset_manager.tileset_names
        if names and 0 <= self.current_tileset_index < len(names):
            return self.tileset_manager.get_tileset(names[self.current_tileset_index])
        return None

    def _get_selection_bounds(self):
        """Normalize selection coords into (min_x, max_x, min_y, max_y)."""
        min_x = min(self.selection_start_x, self.selection_end_x)
        max_x = max(self.selection_start_x, self.selection_end_x)
        min_y = min(self.selection_start_y, self.selection_end_y)
        max_y = max(self.selection_start_y, self.selection_end_y)
        return min_x, max_x, min_y, max_y

    def _is_in_palette(self, mouse_x: int, mouse_y: int) -> bool:
        """Returns True when the mouse is over the palette panel."""
        return mouse_x >= self.palette_x and mouse_y >= self.palette_y

    def _is_in_ui_rect(self, mouse_x: int, mouse_y: int, rect_name: str) -> bool:
        """Returns True when the mouse is inside a named UI rect."""
        if rect_name in self.ui_rects:
            rect = self.ui_rects[rect_name]
            return rect.collidepoint(mouse_x, mouse_y)
        return False

    def handle_input(self, event, camera_x: int, camera_y: int, current_room_name: str):
        """Route keyboard and mouse events while the editor is active."""
        if not self.active:
            return

        keys = pygame.key.get_pressed()
        ctrl_pressed = keys[pygame.K_LCTRL] or keys[pygame.K_RCTRL]
        shift_pressed = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]

        if event.type == pygame.KEYDOWN:
            # Handle layer input text entry
            if self.layer_input_active:
                if event.key == pygame.K_RETURN:
                    try:
                        self.custom_layer_value = int(self.layer_input_text)
                        self.current_layer = self.custom_layer_value
                        self.layer_input_active = False
                    except ValueError:
                        pass
                elif event.key == pygame.K_ESCAPE:
                    self.layer_input_active = False
                elif event.key == pygame.K_BACKSPACE:
                    self.layer_input_text = self.layer_input_text[:-1]
                elif event.key == pygame.K_MINUS or event.unicode == '-':
                    if not self.layer_input_text:
                        self.layer_input_text += '-'
                elif event.unicode.isdigit():
                    self.layer_input_text += event.unicode
                return

            if event.key == pygame.K_TAB:
                names = self.tileset_manager.tileset_names
                if names:
                    self.current_tileset_index = (self.current_tileset_index + 1) % len(names)
                    # Reset selection and scroll so the new tileset opens clean
                    self.selected_tile_x = 0
                    self.selected_tile_y = 0
                    self.selection_start_x = 0
                    self.selection_start_y = 0
                    self.selection_end_x = 0
                    self.selection_end_y = 0
                    self.palette_scroll_x = 0
                    self.palette_scroll_y = 0

            elif event.key == pygame.K_g:
                self.show_grid = not self.show_grid

            # Cycle through layer presets, skipping Custom... which has no fixed value
            elif event.key == pygame.K_l:
                next_index = (self.current_layer_preset_index + 1) % len(self.LAYER_PRESETS)
                while self.LAYER_PRESETS[next_index][1] is None:
                    next_index = (next_index + 1) % len(self.LAYER_PRESETS)
                self.current_layer_preset_index = next_index
                _, preset_value = self.LAYER_PRESETS[self.current_layer_preset_index]
                self.current_layer = preset_value
            # Arrow keys: plain = move cursor, Shift = extend selection, Ctrl = move without resetting selection
            elif event.key == pygame.K_LEFT:
                if not shift_pressed:
                    self.selected_tile_x = max(0, self.selected_tile_x - 1)
                    if not ctrl_pressed:
                        self.selection_start_x = self.selected_tile_x
                        self.selection_start_y = self.selected_tile_y
                        self.selection_end_x = self.selected_tile_x
                        self.selection_end_y = self.selected_tile_y
                else:
                    self.selection_end_x = max(self.selection_start_x, self.selection_end_x - 1)

            elif event.key == pygame.K_RIGHT:
                tileset = self.get_current_tileset()
                if tileset:
                    if not shift_pressed:
                        self.selected_tile_x = min(tileset.cols - 1, self.selected_tile_x + 1)
                        if not ctrl_pressed:
                            self.selection_start_x = self.selected_tile_x
                            self.selection_start_y = self.selected_tile_y
                            self.selection_end_x = self.selected_tile_x
                            self.selection_end_y = self.selected_tile_y
                    else:
                        self.selection_end_x = min(tileset.cols - 1, self.selection_end_x + 1)

            elif event.key == pygame.K_UP:
                if not shift_pressed:
                    self.selected_tile_y = max(0, self.selected_tile_y - 1)
                    if not ctrl_pressed:
                        self.selection_start_x = self.selected_tile_x
                        self.selection_start_y = self.selected_tile_y
                        self.selection_end_x = self.selected_tile_x
                        self.selection_end_y = self.selected_tile_y
                else:
                    self.selection_end_y = max(self.selection_start_y, self.selection_end_y - 1)

            elif event.key == pygame.K_DOWN:
                tileset = self.get_current_tileset()
                if tileset:
                    if not shift_pressed:
                        self.selected_tile_y = min(tileset.rows - 1, self.selected_tile_y + 1)
                        if not ctrl_pressed:
                            self.selection_start_x = self.selected_tile_x
                            self.selection_start_y = self.selected_tile_y
                            self.selection_end_x = self.selected_tile_x
                            self.selection_end_y = self.selected_tile_y
                    else:
                        self.selection_end_y = min(tileset.rows - 1, self.selection_end_y + 1)

            elif event.key == pygame.K_DELETE or event.key == pygame.K_x:
                mouse_x, mouse_y = pygame.mouse.get_pos()
                world_x = (mouse_x + camera_x) // RENDER_SCALE
                world_y = (mouse_y + camera_y) // RENDER_SCALE
                self._delete_tile_at_position(world_x, world_y, current_room_name)

        elif event.type == pygame.MOUSEBUTTONDOWN:
            mouse_x, mouse_y = event.pos

            if self._is_in_ui_rect(mouse_x, mouse_y, 'layer_dropdown'):
                self.layer_dropdown_open = not self.layer_dropdown_open
                return

            if self.layer_dropdown_open:
                for i, (name, value) in enumerate(self.LAYER_PRESETS):
                    if self._is_in_ui_rect(mouse_x, mouse_y, f'layer_option_{i}'):
                        self.current_layer_preset_index = i
                        if value is not None:
                            self.current_layer = value
                        else:
                            self.layer_input_active = True
                            self.layer_input_text = str(self.current_layer)
                        self.layer_dropdown_open = False
                        return
                self.layer_dropdown_open = False
                return

            if self._is_in_ui_rect(mouse_x, mouse_y, 'delete_checkbox'):
                self.delete_underlying = not self.delete_underlying
                return

            if self._is_in_ui_rect(mouse_x, mouse_y, 'hide_layers_checkbox'):
                self.hide_other_layers = not self.hide_other_layers
                return

            # Pygame 1.x scroll convention: button 4 = scroll up, button 5 = scroll down
            # (MOUSEWHEEL event is preferred in newer pygame but both work)
            if event.button == 4:
                if shift_pressed:
                    self.palette_scroll_x = max(0, self.palette_scroll_x - self.grid_cell_size)
                else:
                    self.palette_scroll_y = max(0, self.palette_scroll_y - self.grid_cell_size)
            elif event.button == 5:
                tileset = self.get_current_tileset()
                if tileset:
                    if shift_pressed:
                        max_scroll_x = max(0, tileset.cols * self.grid_cell_size - self.palette_width + 40)
                        self.palette_scroll_x = min(max_scroll_x, self.palette_scroll_x + self.grid_cell_size)
                    else:
                        max_scroll_y = max(0, tileset.rows * self.grid_cell_size - self.palette_content_height)
                        self.palette_scroll_y = min(max_scroll_y, self.palette_scroll_y + self.grid_cell_size)

            if event.button == 1:
                if self._is_in_palette(mouse_x, mouse_y):
                    self._handle_palette_click(mouse_x, mouse_y, ctrl_pressed)
                    self.is_palette_dragging = True
                else:
                    self.is_dragging = True
                    world_x = (mouse_x + camera_x) // RENDER_SCALE
                    world_y = (mouse_y + camera_y) // RENDER_SCALE
                    self.drag_start_pos = (world_x, world_y)
                    self._place_tiles(world_x, world_y, current_room_name)

            elif event.button == 3:
                if not self._is_in_palette(mouse_x, mouse_y):
                    self.is_erasing = True
                    world_x = (mouse_x + camera_x) // RENDER_SCALE
                    world_y = (mouse_y + camera_y) // RENDER_SCALE
                    self._delete_tile_at_position(world_x, world_y, current_room_name)

        elif event.type == pygame.MOUSEBUTTONUP:
            if event.button == 1:
                self.is_dragging = False
                self.drag_start_pos = None
                self.is_palette_dragging = False
            elif event.button == 3:
                self.is_erasing = False

        elif event.type == pygame.MOUSEMOTION:
            if self.is_dragging and not self._is_in_palette(event.pos[0], event.pos[1]):
                mouse_x, mouse_y = event.pos
                world_x = (mouse_x + camera_x) // RENDER_SCALE
                world_y = (mouse_y + camera_y) // RENDER_SCALE
                self._place_tiles(world_x, world_y, current_room_name)
            elif self.is_erasing and not self._is_in_palette(event.pos[0], event.pos[1]):
                mouse_x, mouse_y = event.pos
                world_x = (mouse_x + camera_x) // RENDER_SCALE
                world_y = (mouse_y + camera_y) // RENDER_SCALE
                self._delete_tile_at_position(world_x, world_y, current_room_name)
            elif self.is_palette_dragging:
                mouse_x, mouse_y = event.pos
                if self._is_in_palette(mouse_x, mouse_y):
                    self._handle_palette_drag(mouse_x, mouse_y)

    def _palette_to_tile_coords(self, mouse_x: int, mouse_y: int):
        """Convert a screen mouse position to tileset grid coords, accounting for scroll.

        Returns (tile_x, tile_y) or None if the position is outside the tileset area.
        """
        tileset_x = self.palette_x + 20
        tileset_y = self.palette_y + self.tileset_area_y_offset
        rel_x = mouse_x - tileset_x + self.palette_scroll_x
        rel_y = mouse_y - tileset_y + self.palette_scroll_y

        if rel_x < 0 or rel_y < 0:
            return None

        return rel_x // self.grid_cell_size, rel_y // self.grid_cell_size

    def _handle_palette_click(self, mouse_x: int, mouse_y: int, ctrl_pressed: bool):
        """Select a tile (or keep the anchor point when Ctrl is held) from a palette click."""
        tileset = self.get_current_tileset()
        if not tileset:
            return

        coords = self._palette_to_tile_coords(mouse_x, mouse_y)
        if coords is None:
            return

        tile_x, tile_y = coords
        if not (0 <= tile_x < tileset.cols and 0 <= tile_y < tileset.rows):
            return

        self.selected_tile_x = tile_x
        self.selected_tile_y = tile_y

        # Ctrl+click extends the existing selection; plain click resets it to a single tile
        if not ctrl_pressed:
            self.selection_start_x = tile_x
            self.selection_start_y = tile_y
            self.selection_end_x = tile_x
            self.selection_end_y = tile_y

    def _handle_palette_drag(self, mouse_x: int, mouse_y: int):
        """Extend the selection rectangle as the user drags across the palette."""
        tileset = self.get_current_tileset()
        if not tileset:
            return

        coords = self._palette_to_tile_coords(mouse_x, mouse_y)
        if coords is None:
            return

        # Clamp to valid tile range so dragging past the edge doesn't overflow
        tile_x = min(tileset.cols - 1, max(0, coords[0]))
        tile_y = min(tileset.rows - 1, max(0, coords[1]))

        self.selection_end_x = tile_x
        self.selection_end_y = tile_y

    def _place_tiles(self, world_x: int, world_y: int, room_name: str):
        """Stamp the current selection pattern into the room at the snapped world position.

        Each tile in the multi-tile selection is offset relative to the top-left of the
        selection and placed at the corresponding grid cell. Empty (fully transparent) tiles
        in the selection are silently skipped so they don't erase valid tiles underneath.
        If delete_underlying is on, existing tiles on the same layer and cell are removed
        before the new tile is inserted.
        """
        tileset = self.get_current_tileset()
        if not tileset:
            return

        # Snap the drop point to the nearest grid cell
        grid_x = (world_x // self.grid_size) * self.grid_size
        grid_y = (world_y // self.grid_size) * self.grid_size

        if room_name not in self.room_tiles:
            self.room_tiles[room_name] = []

        min_x, max_x, min_y, max_y = self._get_selection_bounds()

        for ty in range(min_y, max_y + 1):
            for tx in range(min_x, max_x + 1):
                if tileset.is_tile_empty(tx, ty):
                    continue

                offset_x = (tx - min_x) * self.grid_size
                offset_y = (ty - min_y) * self.grid_size
                tile_x = grid_x + offset_x
                tile_y = grid_y + offset_y

                if self.delete_underlying:
                    # Remove any existing tile on this exact cell and layer before placing
                    self.room_tiles[room_name] = [
                        t for t in self.room_tiles[room_name]
                        if not (t.x == tile_x and t.y == tile_y and t.layer == self.current_layer)
                    ]

                new_tile = Tile(
                    tile_x, tile_y,
                    tileset.name,
                    tx, ty,
                    self.current_layer,
                    self.current_layer >= 75  # treat layer 75+ as foreground
                )
                self.room_tiles[room_name].append(new_tile)

        # Notify listeners (e.g. auto-save) that the room content changed
        if callable(getattr(self, 'on_tile_changed', None)):
            self.on_tile_changed(room_name)

    def _delete_tile_at_position(self, world_x: int, world_y: int, room_name: str):
        """Remove all tiles at the snapped grid cell on the current layer."""
        if room_name not in self.room_tiles:
            return

        grid_x = (world_x // self.grid_size) * self.grid_size
        grid_y = (world_y // self.grid_size) * self.grid_size

        self.room_tiles[room_name] = [
            tile for tile in self.room_tiles[room_name]
            if not (tile.x == grid_x and tile.y == grid_y and tile.layer == self.current_layer)
        ]

        # Notify listeners (e.g. auto-save) that the room content changed
        if callable(getattr(self, 'on_tile_changed', None)):
            self.on_tile_changed(room_name)

    def draw_tile_preview(self, screen: pygame.Surface, camera_x: int, camera_y: int):
        """Draw a semi-transparent ghost of the selected tile pattern under the cursor."""
        if not self.active:
            return

        mouse_x, mouse_y = pygame.mouse.get_pos()
        if self._is_in_palette(mouse_x, mouse_y):
            return

        tileset = self.get_current_tileset()
        if not tileset:
            return

        world_x = (mouse_x + camera_x) // RENDER_SCALE
        world_y = (mouse_y + camera_y) // RENDER_SCALE
        grid_x = (world_x // self.grid_size) * self.grid_size
        grid_y = (world_y // self.grid_size) * self.grid_size

        min_x, max_x, min_y, max_y = self._get_selection_bounds()
        scaled_width = tileset.tile_width * RENDER_SCALE
        scaled_height = tileset.tile_height * RENDER_SCALE

        for ty in range(min_y, max_y + 1):
            for tx in range(min_x, max_x + 1):
                if tileset.is_tile_empty(tx, ty):
                    continue

                # Use the cache to avoid repeated transform.scale calls per frame
                scaled_tile = tileset.get_scaled_tile_surface(tx, ty, RENDER_SCALE)
                if not scaled_tile:
                    continue

                offset_x = (tx - min_x) * self.grid_size
                offset_y = (ty - min_y) * self.grid_size
                tile_world_x = grid_x + offset_x
                tile_world_y = grid_y + offset_y

                screen_x = int((tile_world_x * RENDER_SCALE) - camera_x)
                screen_y = int((tile_world_y * RENDER_SCALE) - camera_y)

                # Copy so we can set alpha without mutating the cached surface
                preview_surface = scaled_tile.copy()
                preview_surface.set_alpha(128)
                screen.blit(preview_surface, (screen_x, screen_y))

                # Accent border so it reads clearly over any background
                pygame.draw.rect(screen, self.colors['accent'],
                                 (screen_x, screen_y, scaled_width, scaled_height), 2)

    def draw_tiles(self, screen: pygame.Surface, camera_x: int, camera_y: int,
                   room_name: str, layer: str = 'background'):
        """Draw all tiles for a room at the specified rendering pass ('background' or 'foreground').

        Tiles with layer >= 0 are drawn in the foreground pass; everything below 0 is background.
        When hide_other_layers is on, only tiles matching current_layer are drawn.
        Uses the tileset's scaled surface cache to avoid per-frame transform.scale() calls.
        """
        if room_name not in self.room_tiles:
            return

        for tile in self.room_tiles[room_name]:
            # Optionally dim everything except the active editing layer
            if self.hide_other_layers and tile.layer != self.current_layer:
                continue

            # Split world tiles into two draw passes so foreground tiles render on top
            if layer == 'background' and tile.layer >= 0:
                continue
            if layer == 'foreground' and tile.layer < 0:
                continue

            tileset = self.tileset_manager.get_tileset(tile.tileset_name)
            if not tileset:
                continue

            screen_x = (tile.x * RENDER_SCALE) - camera_x
            screen_y = (tile.y * RENDER_SCALE) - camera_y

            scaled_width = tileset.tile_width * RENDER_SCALE
            scaled_height = tileset.tile_height * RENDER_SCALE

            # Skip tiles that are entirely off-screen
            if not (-scaled_width <= screen_x <= self.screen_width and
                    -scaled_height <= screen_y <= self.screen_height):
                continue

            # Retrieve from cache — avoids subsurface + transform.scale every frame
            scaled_tile = tileset.get_scaled_tile_surface(tile.tile_x, tile.tile_y, RENDER_SCALE)
            if not scaled_tile:
                continue

            screen.blit(scaled_tile, (int(screen_x), int(screen_y)))

    def draw_palette(self, screen: pygame.Surface):
        """Draw the tileset palette UI with layer controls"""
        if not self.active:
            return

        tileset = self.get_current_tileset()
        if not tileset:
            return

        palette_height = 940
        palette_rect = pygame.Rect(self.palette_x, self.palette_y,
                                   self.palette_width, palette_height)

        # Background
        palette_bg = pygame.Surface((self.palette_width, palette_height), pygame.SRCALPHA)
        palette_bg.fill((*self.colors['bg'], 230))
        screen.blit(palette_bg, (self.palette_x, self.palette_y))
        pygame.draw.rect(screen, self.colors['accent'], palette_rect, 2)

        # Title
        title_text = self.font_medium.render(f"Tileset: {tileset.name}", True, self.colors['text'])
        screen.blit(title_text, (self.palette_x + 20, self.palette_y + 10))

        # Tileset dimensions
        dims_text = f"{tileset.cols}x{tileset.rows} tiles ({tileset.tile_width}px)"
        dims_surf = self.font_small.render(dims_text, True, self.colors['text_dim'])
        screen.blit(dims_surf, (self.palette_x + 280, self.palette_y + 15))

        # Draw tileset with grid
        tileset_y = self.palette_y + self.tileset_area_y_offset
        tileset_x = self.palette_x + 20

        # Create clipping rect for scrollable area
        clip_rect = pygame.Rect(tileset_x, tileset_y,
                                self.palette_width - 40, self.palette_content_height)
        screen.set_clip(clip_rect)

        # Draw the tileset image scaled up
        if tileset.image:
            scaled_width = tileset.cols * self.grid_cell_size
            scaled_height = tileset.rows * self.grid_cell_size
            scaled_tileset = pygame.transform.scale(tileset.image, (scaled_width, scaled_height))

            draw_x = tileset_x - self.palette_scroll_x
            draw_y = tileset_y - self.palette_scroll_y
            screen.blit(scaled_tileset, (draw_x, draw_y))

            # Draw grid overlay
            for row in range(tileset.rows + 1):
                y = draw_y + row * self.grid_cell_size
                pygame.draw.line(screen, self.colors['grid_dim'][:3],
                                 (draw_x, y), (draw_x + scaled_width, y), 1)

            for col in range(tileset.cols + 1):
                x = draw_x + col * self.grid_cell_size
                pygame.draw.line(screen, self.colors['grid_dim'][:3],
                                 (x, draw_y), (x, draw_y + scaled_height), 1)

            # Draw selection rectangle
            min_x, max_x, min_y, max_y = self._get_selection_bounds()
            sel_width = max_x - min_x + 1
            sel_height = max_y - min_y + 1
            sel_x = draw_x + min_x * self.grid_cell_size
            sel_y = draw_y + min_y * self.grid_cell_size
            sel_w = sel_width * self.grid_cell_size
            sel_h = sel_height * self.grid_cell_size

            # Selection fill
            sel_surf = pygame.Surface((sel_w, sel_h), pygame.SRCALPHA)
            sel_surf.fill((*self.colors['selection'], 60))
            screen.blit(sel_surf, (sel_x, sel_y))

            # Selection border
            pygame.draw.rect(screen, self.colors['accent'],
                             (sel_x, sel_y, sel_w, sel_h), 3)

        screen.set_clip(None)

        # Selection info
        sel_y = self.palette_y + 45
        min_x, max_x, min_y, max_y = self._get_selection_bounds()
        sel_width = max_x - min_x + 1
        sel_height = max_y - min_y + 1
        if sel_width > 1 or sel_height > 1:
            sel_text = f"Selection: {sel_width}x{sel_height}"
            sel_surf = self.font_small.render(sel_text, True, self.colors['selection'])
            screen.blit(sel_surf, (self.palette_x + 20, sel_y))

        # Controls below the palette content
        controls_y = tileset_y + self.palette_content_height + 10

        # Delete underlying checkbox
        checkbox_y = controls_y
        checkbox_x = self.palette_x + 20
        checkbox_size = 18

        checkbox_rect = pygame.Rect(checkbox_x, checkbox_y, checkbox_size, checkbox_size)
        self.ui_rects['delete_checkbox'] = checkbox_rect

        pygame.draw.rect(screen, self.colors['checkbox'], checkbox_rect)
        pygame.draw.rect(screen, self.colors['accent'], checkbox_rect, 1)

        if self.delete_underlying:
            # Draw checkmark
            pygame.draw.line(screen, self.colors['success'],
                             (checkbox_x + 3, checkbox_y + 9),
                             (checkbox_x + 7, checkbox_y + 13), 2)
            pygame.draw.line(screen, self.colors['success'],
                             (checkbox_x + 7, checkbox_y + 13),
                             (checkbox_x + 15, checkbox_y + 5), 2)

        checkbox_label = self.font_small.render("Replace tiles on same layer", True, self.colors['text_dim'])
        screen.blit(checkbox_label, (checkbox_x + 25, checkbox_y + 2))

        # "Hide other layers" checkbox — dims all tiles except the active layer while editing
        hide_checkbox_y = controls_y + 25
        hide_checkbox_x = self.palette_x + 20

        hide_checkbox_rect = pygame.Rect(hide_checkbox_x, hide_checkbox_y, checkbox_size, checkbox_size)
        self.ui_rects['hide_layers_checkbox'] = hide_checkbox_rect

        pygame.draw.rect(screen, self.colors['checkbox'], hide_checkbox_rect)
        pygame.draw.rect(screen, self.colors['accent'], hide_checkbox_rect, 1)

        if self.hide_other_layers:
            # Draw checkmark
            pygame.draw.line(screen, self.colors['success'],
                             (hide_checkbox_x + 3, hide_checkbox_y + 9),
                             (hide_checkbox_x + 7, hide_checkbox_y + 13), 2)
            pygame.draw.line(screen, self.colors['success'],
                             (hide_checkbox_x + 7, hide_checkbox_y + 13),
                             (hide_checkbox_x + 15, hide_checkbox_y + 5), 2)

        hide_checkbox_label = self.font_small.render("Hide other layers", True, self.colors['text_dim'])
        screen.blit(hide_checkbox_label, (hide_checkbox_x + 25, hide_checkbox_y + 2))

        # Layer controls — label on the right half, dropdown beside it
        layer_y = controls_y + 50

        layer_label = self.font_small.render("Tile Layer:", True, self.colors['text_dim'])
        screen.blit(layer_label, (self.palette_x + 340, layer_y))

        dropdown_x = self.palette_x + 420
        dropdown_y = layer_y - 5
        dropdown_width = 150
        dropdown_height = 28

        self.ui_rects['layer_dropdown'] = pygame.Rect(dropdown_x, dropdown_y, dropdown_width, dropdown_height)

        pygame.draw.rect(screen, self.colors['button'], self.ui_rects['layer_dropdown'])
        pygame.draw.rect(screen, self.colors['accent'], self.ui_rects['layer_dropdown'], 1)

        # Current layer text
        if self.layer_input_active:
            layer_display = f"Custom: {self.layer_input_text}_"
        else:
            preset_name, _ = self.LAYER_PRESETS[self.current_layer_preset_index]
            if preset_name == "Custom...":
                layer_display = f"Custom: {self.current_layer}"
            else:
                layer_display = f"{preset_name} ({self.current_layer})"

        layer_text = self.font_small.render(layer_display, True, self.colors['text'])
        text_width = layer_text.get_width()
        # Left-align when text overflows; otherwise centre it with a small indent
        if text_width > dropdown_width - 16:
            screen.blit(layer_text, (dropdown_x + 4, dropdown_y + 6))
        else:
            screen.blit(layer_text, (dropdown_x + 8, dropdown_y + 6))

        # Dropdown arrow
        arrow_x = dropdown_x + dropdown_width - 20
        arrow_y = dropdown_y + 14
        pygame.draw.polygon(screen, self.colors['text'], [
            (arrow_x, arrow_y - 4),
            (arrow_x + 8, arrow_y - 4),
            (arrow_x + 4, arrow_y + 2)
        ])

        # Draw dropdown menu if open
        if self.layer_dropdown_open:
            menu_y = dropdown_y + dropdown_height
            for i, (name, value) in enumerate(self.LAYER_PRESETS):
                option_rect = pygame.Rect(dropdown_x, menu_y + i * 25, dropdown_width, 25)
                self.ui_rects[f'layer_option_{i}'] = option_rect

                # Highlight hovered option
                mouse_pos = pygame.mouse.get_pos()
                if option_rect.collidepoint(mouse_pos):
                    pygame.draw.rect(screen, self.colors['button_hover'], option_rect)
                else:
                    pygame.draw.rect(screen, self.colors['button'], option_rect)

                pygame.draw.rect(screen, self.colors['accent'], option_rect, 1)

                display_text = name if value is None else f"{name} ({value})"
                option_text = self.font_small.render(display_text, True, self.colors['text'])
                screen.blit(option_text, (dropdown_x + 8, menu_y + i * 25 + 5))

        # Instructions
        instructions = [
            "TAB: Switch Tileset",
            "L: Cycle Layer Presets",
            "G: Toggle Grid",
            "Arrows: Navigate Tiles",
            "Shift+Arrows: Extend Selection",
            "Click Dropdown: Choose Layer",
            "Click/Drag Palette: Select",
            "Scroll: Pan Tileset",
            "Click World: Place Pattern",
            "Right Click: Delete Tile",
            "F2: Close Editor"
        ]

        inst_y = layer_y + 35  # sits just below the layer dropdown row
        for inst in instructions:
            inst_surf = self.font_small.render(inst, True, self.colors['text_dim'])
            screen.blit(inst_surf, (self.palette_x + 20, inst_y))
            inst_y += 18

    def draw_grid(self, screen: pygame.Surface, camera_x: int, camera_y: int,
                  room_width: int, room_height: int):
        """Overlay a tile-aligned grid on the world viewport — only draws lines in the visible frustum."""
        if not self.show_grid or not self.active:
            return

        visible_x_start = camera_x // RENDER_SCALE
        visible_y_start = camera_y // RENDER_SCALE
        visible_x_end = (camera_x + self.screen_width) // RENDER_SCALE
        visible_y_end = (camera_y + self.screen_height) // RENDER_SCALE

        # Draw vertical lines
        start_x = (visible_x_start // self.grid_size) * self.grid_size
        for x in range(start_x, visible_x_end + self.grid_size, self.grid_size):
            screen_x = (x * RENDER_SCALE) - camera_x
            if -10 <= screen_x <= self.screen_width + 10:
                pygame.draw.line(screen, self.colors['grid'][:3],
                                 (int(screen_x), 0), (int(screen_x), self.screen_height), 1)

        # Draw horizontal lines
        start_y = (visible_y_start // self.grid_size) * self.grid_size
        for y in range(start_y, visible_y_end + self.grid_size, self.grid_size):
            screen_y = (y * RENDER_SCALE) - camera_y
            if -10 <= screen_y <= self.screen_height + 10:
                pygame.draw.line(screen, self.colors['grid'][:3],
                                 (0, int(screen_y)), (self.screen_width, int(screen_y)), 1)

    def save_room_tiles(self, room_name: str, filepath: str):
        """Serialize all tiles for a room to a JSON file."""
        if room_name not in self.room_tiles:
            return

        data = {
            'room': room_name,
            'tiles': [tile.to_dict() for tile in self.room_tiles[room_name]]
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    def load_room_tiles(self, room_name: str, filepath: str):
        """Deserialize tiles for a room from a JSON file. Initialises to an empty list on any error."""
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)

            self.room_tiles[room_name] = [Tile.from_dict(tile_data) for tile_data in data['tiles']]
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            print(f"Error loading room tiles: {e}")
            self.room_tiles[room_name] = []