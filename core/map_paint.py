"""
Map-paint data: the designer-authored footprint used to generate the
Scouter minimap silhouette (see ui/scouter_room_map.py for the renderer).

This is deliberately NOT derived from collision_objects or tiles — both were
tried (see design discussion) and rejected: tiles blanket the whole canvas
for visual/background reasons unrelated to walkable space, and collision
objects mix "outer cave wall" and "random obstacle in the middle of the
room" with no way to tell them apart automatically. Instead the designer
paints the shape once, directly, in the room editor's Map Paint tool
(dev_tools/room_editor/room_editor_tools/map_paint_editor.py) — same idea as
painting tiles, just writing to this bucket instead of the tile layer.

Coordinates are grid cells at CELL_SIZE world-space pixels, matching the
16px alignment already used by room_transitions/spawn_points elsewhere in
the room JSON, so painted cells line up with doorways without needing any
snapping/fudging.
"""

from typing import Dict, Set, Tuple, List

CELL_SIZE = 16

Cell = Tuple[int, int]  # (grid_x, grid_y), i.e. world_x // CELL_SIZE, world_y // CELL_SIZE


class MapPaintManager:
    """Tracks painted minimap cells across all rooms.

    Mirrors CollisionObjectManager's shape (per-room dict, add/remove,
    save_to_dict/load_from_dict) on purpose, so anyone already familiar with
    that manager recognizes this one immediately.
    """

    def __init__(self):
        self.painted_cells: Dict[str, Set[Cell]] = {}

    # ── Query ────────────────────────────────────────────────────────────
    def get_painted_cells(self, room_name: str) -> Set[Cell]:
        return self.painted_cells.get(room_name, set())

    def is_painted(self, room_name: str, cell: Cell) -> bool:
        return cell in self.painted_cells.get(room_name, ())

    # ── Mutation ─────────────────────────────────────────────────────────
    def paint(self, room_name: str, cell: Cell):
        self.painted_cells.setdefault(room_name, set()).add(cell)

    def erase(self, room_name: str, cell: Cell):
        cells = self.painted_cells.get(room_name)
        if cells:
            cells.discard(cell)

    def paint_world_point(self, room_name: str, world_x: float, world_y: float):
        self.paint(room_name, (int(world_x) // CELL_SIZE, int(world_y) // CELL_SIZE))

    def erase_world_point(self, room_name: str, world_x: float, world_y: float):
        self.erase(room_name, (int(world_x) // CELL_SIZE, int(world_y) // CELL_SIZE))

    def clear_room(self, room_name: str):
        self.painted_cells[room_name] = set()

    # ── Bulk helpers (used by the flood-fill / rectangle-fill brush) ───────
    def paint_many(self, room_name: str, cells):
        self.painted_cells.setdefault(room_name, set()).update(cells)

    def erase_many(self, room_name: str, cells):
        existing = self.painted_cells.get(room_name)
        if existing:
            existing.difference_update(cells)

    # ── Persistence ──────────────────────────────────────────────────────
    def save_to_dict(self) -> Dict[str, List[List[int]]]:
        """[[gx, gy], ...] per room — plain JSON-safe lists, sorted for
        stable diffs when the room file is checked into version control."""
        return {
            room: sorted([list(c) for c in cells])
            for room, cells in self.painted_cells.items()
            if cells
        }

    def load_from_dict(self, data: Dict[str, List[List[int]]]):
        self.painted_cells = {
            room: {tuple(c) for c in cells}
            for room, cells in (data or {}).items()
        }

    @staticmethod
    def cells_from_room_list(raw: List[List[int]]) -> Set[Cell]:
        """Convert a room's raw JSON map_paint list (as stored on the Room
        object itself, e.g. room.map_paint) into a cell set. Used by the
        Scouter-side renderer, which reads straight off Room objects rather
        than going through a MapPaintManager."""
        return {tuple(c) for c in (raw or [])}