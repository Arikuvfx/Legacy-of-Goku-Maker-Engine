"""
Map Paint tool — lets a designer paint the Scouter minimap silhouette for a
room directly, the same click-and-drag way tiles get painted. Whatever gets
painted here is exactly what shows up on the in-game map (see
ui/scouter_room_map.py), no inference involved.

Wiring lives in RoomEditor (dev_tools/room_editor/room_editor.py):
  • toolbar gets a 'map_paint' entry (editor_toolbar.py)
  • RoomEditor.toggle()/_handle_view_room_input() treat this the same way
    as tileset_editor/object_editor (mutually-exclusive panel, stroke undo)
  • RoomEditor._draw_view_room() dims the normal tile render while this is
    active, then calls this class's draw()
  • RoomEditor._sync_room_to_editor()/_save_current_room() mirror
    room.map_paint <-> self.manager.painted_cells[room_name], the same way
    room.collision_objects already mirrors ObjectEditor.collision_manager
"""

import pygame

from core.map_paint import MapPaintManager, CELL_SIZE


class MapPaintEditor:
    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False

        self.manager = MapPaintManager()
        self.current_room_name = ""

        # Stroke state — which button is held (1 = paint, 3 = erase) and
        # which cells it has already touched this stroke, so a single drag
        # doesn't re-toggle a cell it already painted if the mouse wobbles
        # back over it before release.
        self._stroke_button = None
        self._stroke_touched = set()

        self.colors = {
            'grid':        (60, 60, 80, 90),
            'painted':     (40, 90, 255, 140),
            'painted_line': (80, 160, 255, 200),
            'dim_overlay': (10, 10, 20, 165),  # drawn over the normal tile render
        }

    def toggle(self):
        self.active = not self.active
        self._stroke_button = None
        self._stroke_touched = set()

    # ── Input ────────────────────────────────────────────────────────────
    def handle_input(self, event, camera_x, camera_y, room_name):
        if not self.active or not room_name:
            return
        self.current_room_name = room_name

        if event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 3):
            self._stroke_button = event.button
            self._stroke_touched = set()
            self._paint_at(event.pos, camera_x, camera_y, room_name, event.button)

        elif event.type == pygame.MOUSEMOTION and self._stroke_button is not None:
            self._paint_at(event.pos, camera_x, camera_y, room_name, self._stroke_button)

        elif event.type == pygame.MOUSEBUTTONUP and event.button == self._stroke_button:
            self._stroke_button = None
            self._stroke_touched = set()

        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._stroke_button = None
            self._stroke_touched = set()

    def _paint_at(self, screen_pos, camera_x, camera_y, room_name, button):
        from config.settings import RENDER_SCALE  # local import matches existing editor modules' pattern

        world_x = (screen_pos[0] + camera_x) / RENDER_SCALE
        world_y = (screen_pos[1] + camera_y) / RENDER_SCALE
        cell = (int(world_x) // CELL_SIZE, int(world_y) // CELL_SIZE)

        if cell in self._stroke_touched:
            return
        self._stroke_touched.add(cell)

        if button == 1:
            self.manager.paint(room_name, cell)
        elif button == 3:
            self.manager.erase(room_name, cell)

    # ── Bucket-fill convenience: fills every cell inside a rectangle in one
    # call, so a designer doesn't have to drag-scrub large open rooms cell
    # by cell. Exposed for a future toolbar button; not wired to input yet.
    def fill_rect(self, room_name, world_x0, world_y0, world_x1, world_y1, erase=False):
        gx0, gy0 = int(world_x0) // CELL_SIZE, int(world_y0) // CELL_SIZE
        gx1, gy1 = int(world_x1) // CELL_SIZE, int(world_y1) // CELL_SIZE
        cells = {
            (gx, gy)
            for gx in range(min(gx0, gx1), max(gx0, gx1) + 1)
            for gy in range(min(gy0, gy1), max(gy0, gy1) + 1)
        }
        if erase:
            self.manager.erase_many(room_name, cells)
        else:
            self.manager.paint_many(room_name, cells)

    # ── Draw ─────────────────────────────────────────────────────────────
    def draw_dim_overlay(self, screen):
        """Call BEFORE drawing painted cells, right after the room's normal
        tile surface is blit — tones the room down so painted cells (and
        the grid) read clearly on top, per the original request."""
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill(self.colors['dim_overlay'])
        screen.blit(overlay, (0, 0))

    def draw(self, screen, camera_x, camera_y, room_width, room_height):
        if not self.active:
            return

        from config.settings import RENDER_SCALE

        cell_screen = CELL_SIZE * RENDER_SCALE

        # Grid — only within the room's own bounds, not the whole screen.
        grid_surf = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        x0 = -int(camera_x) % cell_screen
        for sx in range(int(x0), self.screen_width, int(cell_screen)):
            pygame.draw.line(grid_surf, self.colors['grid'], (sx, 0), (sx, self.screen_height))
        y0 = -int(camera_y) % cell_screen
        for sy in range(int(y0), self.screen_height, int(cell_screen)):
            pygame.draw.line(grid_surf, self.colors['grid'], (0, sy), (self.screen_width, sy))
        screen.blit(grid_surf, (0, 0))

        # Painted cells
        cells = self.manager.get_painted_cells(self.current_room_name)
        if not cells:
            return
        paint_surf = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        for gx, gy in cells:
            sx = gx * cell_screen - camera_x
            sy = gy * cell_screen - camera_y
            if sx < -cell_screen or sy < -cell_screen or sx > self.screen_width or sy > self.screen_height:
                continue
            rect = pygame.Rect(int(sx), int(sy), int(cell_screen), int(cell_screen))
            pygame.draw.rect(paint_surf, self.colors['painted'], rect)
            pygame.draw.rect(paint_surf, self.colors['painted_line'], rect, 1)
        screen.blit(paint_surf, (0, 0))