"""
Builds the Scouter "Map" section's zone-wide minimap image.

Two things feed this, both already precise, neither requiring any new
manual work per room beyond the one-time map-paint pass:

  • room.map_paint  — designer-painted [gx, gy] cells (objects/map_paint.py),
    the walkable silhouette, drawn in the room editor's Map Paint tool.
  • room.room_transitions — exact doorway rects + target_room, already
    authored for gameplay, reused here to position rooms relative to each
    other and to draw the orange doorway strips.

Nothing here is inferred from tiles or collision — see the design
discussion this module grew out of for why those don't work.
"""

import pygame
from collections import deque

CELL_SIZE = 16  # must match objects/map_paint.py

COLOR_BG          = (5, 8, 20)
COLOR_FILL        = (33, 32, 255)    # 2120FF — interior of the painted silhouette
COLOR_OUTLINE     = (0, 255, 255)    # 00FFFF — edge cells of the painted silhouette
COLOR_TRANSITION_A = (255, 134, 0)   # FF8600
COLOR_TRANSITION_B = (255, 255, 0)   # FFFF00
COLOR_SPAWN       = (90, 255, 120)
COLOR_ENTITY      = (170, 170, 180)
COLOR_WORLD_SIGN  = (255, 70, 70)


def _nested_fill_depths(mask, grid_w, grid_h):
    """For a mask of painted (outline) cells, label every connected pocket
    of unpainted cells and compute each pocket's nesting depth: the pocket
    touching *this grid's own* border is depth 0 (outside everything), the
    pocket just inside the first outline crossed is depth 1, a pocket
    inside a second nested outline is depth 2, and so on.

    Returns (region_id, depth) where region_id[row][col] is -1 for painted
    cells and a pocket index otherwise, and depth[region_id] gives that
    pocket's nesting depth. Caller decides how to color by depth (odd/even
    parity for a simple fill-alternating look).
    """
    region_id = [[-1] * grid_w for _ in range(grid_h)]
    region_count = 0
    border_regions = set()

    for row in range(grid_h):
        for col in range(grid_w):
            if mask[row][col] or region_id[row][col] != -1:
                continue
            rid = region_count
            region_count += 1
            touches_border = False
            dq = deque([(row, col)])
            region_id[row][col] = rid
            while dq:
                r, c = dq.popleft()
                if r == 0 or r == grid_h - 1 or c == 0 or c == grid_w - 1:
                    touches_border = True
                for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                    if (0 <= nr < grid_h and 0 <= nc < grid_w
                            and not mask[nr][nc] and region_id[nr][nc] == -1):
                        region_id[nr][nc] = rid
                        dq.append((nr, nc))
            if touches_border:
                border_regions.add(rid)

    # Two pockets are adjacent if a painted cell has neighbors in both.
    adjacency = [set() for _ in range(region_count)]
    for row in range(grid_h):
        for col in range(grid_w):
            if not mask[row][col]:
                continue
            neighbor_regions = {
                region_id[nr][nc]
                for nr, nc in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1))
                if 0 <= nr < grid_h and 0 <= nc < grid_w and not mask[nr][nc]
            }
            for a in neighbor_regions:
                for b in neighbor_regions:
                    if a != b:
                        adjacency[a].add(b)

    depth = [None] * region_count
    dq = deque()
    for rid in border_regions:
        depth[rid] = 0
        dq.append(rid)
    while dq:
        rid = dq.popleft()
        for nb in adjacency[rid]:
            if depth[nb] is None:
                depth[nb] = depth[rid] + 1
                dq.append(nb)
    # A pocket that never connects back to the border (shouldn't normally
    # happen) defaults to filled rather than silently vanishing.
    for i in range(region_count):
        if depth[i] is None:
            depth[i] = 1

    return region_id, depth


def _uniq_transitions(room):
    """room_transitions commonly lists each doorway from both sides —
    de-dupe by its own rect+target so each strip is only drawn once.

    room.room_transitions holds live RoomTransition objects at runtime
    (plain x/y/width/height/target_room/... attributes — see
    RoomPersistence.deserialize_room_transitions), not dicts, so this reads
    them with getattr rather than dict subscripting."""
    seen = set()
    out = []
    for t in getattr(room, 'room_transitions', None) or []:
        key = (t.x, t.y, t.width, t.height, getattr(t, 'target_room', None))
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def build_zone_layout(start_room, room_lookup):
    """BFS outward from start_room along room_transitions, aligning each
    connected room's origin so matching transition rects coincide in a
    shared coordinate space.

    room_lookup: callable(room_name) -> Room|None

    Returns {room_name: (origin_x, origin_y)} for every room reachable from
    start_room. Rooms with no transitions back to something already placed
    are simply never visited — a room only ever appears once its position
    relative to something else is actually known.
    """
    origins = {start_room.name: (0, 0)}
    visited_rooms = {start_room.name: start_room}
    queue = [start_room]

    while queue:
        room = queue.pop(0)
        ox, oy = origins[room.name]
        for t in _uniq_transitions(room):
            target_name = getattr(t, 'target_room', None)
            if not target_name or target_name in origins:
                continue
            target_room = room_lookup(target_name)
            if target_room is None:
                continue
            match = next(
                (mt for mt in _uniq_transitions(target_room)
                 if getattr(mt, 'target_room', None) == room.name),
                None
            )
            if match is None:
                # No transition back — can't position it relative to this
                # room from data alone, so it's skipped rather than guessed.
                continue
            origins[target_name] = (
                ox + (t.x - match.x),
                oy + (t.y - match.y),
            )
            visited_rooms[target_name] = target_room
            queue.append(target_room)

    return origins, visited_rooms


def _zone_bounds(origins, rooms_by_name):
    """Shared world-space bounds computation — same min/max walk
    render_zone_surface used to do inline. Pulled out so callers that need
    to place something (e.g. the player marker) in the exact same
    coordinate space as the rendered Surface don't have to duplicate this
    logic and risk it drifting out of sync. Returns
    (min_x, min_y, max_x, max_y, grid_w, grid_h); grid_w/grid_h match the
    Surface size render_zone_surface returns for these same origins."""
    all_x0, all_y0, all_x1, all_y1 = [], [], [], []
    for name, (ox, oy) in origins.items():
        room = rooms_by_name[name]
        all_x0.append(ox); all_y0.append(oy)
        all_x1.append(ox + room.width); all_y1.append(oy + room.height)
    min_x, min_y = min(all_x0), min(all_y0)
    max_x, max_y = max(all_x1), max(all_y1)
    grid_w = max(1, (max_x - min_x) // CELL_SIZE + 1)
    grid_h = max(1, (max_y - min_y) // CELL_SIZE + 1)
    return min_x, min_y, max_x, max_y, grid_w, grid_h


def world_to_grid(origins, rooms_by_name, room_name, world_x, world_y):
    """Maps a room-local (world_x, world_y) — e.g. player.x/player.y,
    already in the same room-local units as map_paint/room_transitions —
    into the same pixel-grid space the Surface returned by
    render_zone_surface uses for these `origins`. Returns a float
    (gx, gy), NOT floored to int like the internal per-cell gx()/gy()
    closures below: a marker needs to slide smoothly across cells as the
    player moves, not snap cell-to-cell like the painted silhouette does.

    Returns None if room_name isn't part of this layout (e.g. the player
    is in a room that wasn't reachable from the zone's start room).
    """
    if room_name not in origins:
        return None
    min_x, min_y, _max_x, _max_y, _grid_w, _grid_h = _zone_bounds(origins, rooms_by_name)
    ox, oy = origins[room_name]
    gx = (ox + world_x - min_x) / CELL_SIZE
    gy = (oy + world_y - min_y) / CELL_SIZE
    return gx, gy


def render_zone_surface(origins, rooms_by_name, current_room_name=None, pulse_t=0.0):
    """Rasterize every room's painted silhouette into one Surface in the
    shared coordinate space `origins` describes.

    current_room_name/pulse_t are accepted but no longer used to draw
    anything (the room-bounds rectangle highlight was removed) — kept in
    the signature so ScouterMenu._draw_map_section doesn't need updating
    if a different current-room indicator gets added later.
    """
    if not origins:
        return pygame.Surface((1, 1))

    min_x, min_y, max_x, max_y, grid_w, grid_h = _zone_bounds(origins, rooms_by_name)

    def gx(world_x):
        return int((world_x - min_x) // CELL_SIZE)

    def gy(world_y):
        return int((world_y - min_y) // CELL_SIZE)

    # Fill and outline color are both computed per room and stamped into
    # this shared canvas below.
    img = pygame.Surface((grid_w, grid_h), pygame.SRCALPHA)

    # Fill has to be computed per room, not against the whole zone canvas.
    # A room's outer wall is only ever painted to close against that
    # room's own width/height — it was never meant to seal against the
    # rest of the zone. If fill were flood-filled from the shared canvas's
    # outer edge, the "outside" flood would leak straight through the
    # paint-free gaps at doorways into every connected room's floor
    # (since a doorway is, by definition, unpainted), leaving only fully
    # self-contained shapes — like a "house" outline sitting inside a
    # room — as sealed pockets. That's backwards from what's wanted: the
    # room floor should fill, and nested house interiors should not.
    # Computing each room against its own local bounds (so its outer wall
    # is what closes against "outside", not a doorway gap to a neighbour)
    # fixes that, then the colored result is stamped into the shared grid.
    # (dx, dy) in cells to push something *into* the room that owns it,
    # given the direction the player exits through the doorway — i.e. the
    # opposite of travel, back toward that room's own floor. Used below
    # for both the wall-jamb nudge and (further down) the transition strip
    # nudge — same trick, same reason: build_zone_layout aligns matching
    # doorway rects to the exact same world cells, so anything stamped at
    # true position from both sides lands on the same pixels.
    _INTERIOR_SHIFT = {
        'up': (0, 1),
        'down': (0, -1),
        'left': (1, 0),
        'right': (-1, 0),
    }

    # For each matched doorway pair, one side is the "owner" (drawn at its
    # true position) and one is the "target" (nudged into its own interior)
    # — see the transition-drawing pass below for the full rationale. Wall
    # jambs need the same owner/target split as the strip itself, or the
    # jambs and the (already-nudged) strip disagree about where the seam
    # is. Precomputed once here, keyed by the target room's own transition,
    # so both this loop and the transition loop below draw a consistent
    # picture: {(room_name, t.x, t.y, t.width, t.height): (dx, dy)}.
    shift_by_transition = {}
    _seen_pairs = set()
    for name in origins:
        room = rooms_by_name[name]
        for t in _uniq_transitions(room):
            target_name = getattr(t, 'target_room', None)
            target_room = rooms_by_name.get(target_name) if target_name else None
            if target_room is None or target_name not in origins:
                continue
            match = next(
                (mt for mt in _uniq_transitions(target_room)
                 if getattr(mt, 'target_room', None) == room.name),
                None
            )
            if match is None:
                continue
            pair_key = frozenset((
                (name, t.x, t.y, t.width, t.height),
                (target_name, match.x, match.y, match.width, match.height),
            ))
            if pair_key in _seen_pairs:
                continue
            _seen_pairs.add(pair_key)

            # Canonical, start-room-independent owner: whichever room name
            # sorts first is always the "true position" side, and the
            # other side always gets nudged into its own interior. This
            # used to be decided by BFS visitation order instead (whichever
            # room the `for name in origins` loop reached first) -- but
            # that order depends on which room the player is standing in
            # when the zone layout is built, so the exact same doorway
            # pair could come out with either side nudged depending on
            # current room. Sorting by name makes every pair resolve the
            # same way no matter where the BFS started.
            if name < target_name:
                other_room, other_t = target_name, match
            else:
                other_room, other_t = name, t

            shift_by_transition[
                (other_room, other_t.x, other_t.y, other_t.width, other_t.height)
            ] = _INTERIOR_SHIFT.get(getattr(other_t, 'exit_direction', None), (0, 0))

    # Nudged jamb cells get stamped in a second pass, after every room's
    # own normal fill+outline is down. If stamped inline, a jamb nudged
    # into this same room's own interior lands on a cell that room's own
    # loop hasn't reached yet — once it does, that cell's ordinary FILL
    # (it's genuinely that room's floor) paints straight over the nudged
    # wall pixel. Deferring guarantees the jamb wins.
    deferred_jamb_pixels = []

    for name, (ox, oy) in origins.items():
        room = rooms_by_name[name]
        base_gx, base_gy = gx(ox), gy(oy)
        room_gw = max(1, int(room.width) // CELL_SIZE + 1)
        room_gh = max(1, int(room.height) // CELL_SIZE + 1)

        local_mask = [[False] * room_gw for _ in range(room_gh)]
        for cell in (getattr(room, 'map_paint', None) or []):
            lx, ly = int(cell[0]), int(cell[1])
            if 0 <= lx < room_gw and 0 <= ly < room_gh:
                local_mask[ly][lx] = True

        # Real wall cells only (map_paint, before doorways get sealed
        # below) — used to find the jamb cells immediately flanking each
        # doorway, as opposed to the doorway gap itself.
        painted_mask = [row[:] for row in local_mask]

        # For each doorway, remember which local cells sit within one cell
        # of it (its jambs) and, if this room is the "target" side of that
        # pair, how far to nudge them so they follow the doorway strip's
        # own nudge instead of colliding with the neighbour room's jamb.
        jamb_shift = [[None] * room_gw for _ in range(room_gh)]

        # Doorways (room_transitions) are, by definition, gaps in the
        # painted outline — that's how you walk from one room into the
        # next, so the designer never paints over them. Left alone, those
        # gaps let the "outside" flood leak straight through into the
        # room's own floor, which makes the floor read as unfilled (the
        # bug this whole per-room pass exists to fix). For containment
        # purposes only, seal each transition's rect as a wall segment —
        # it still gets drawn as the orange transition strip afterward,
        # which takes priority over both outline and fill colors.
        for t in _uniq_transitions(room):
            tx0, ty0 = int(t.x) // CELL_SIZE, int(t.y) // CELL_SIZE
            tx1 = int(t.x + t.width) // CELL_SIZE
            ty1 = int(t.y + t.height) // CELL_SIZE
            for ty in range(ty0, max(ty1, ty0 + 1)):
                for tx in range(tx0, max(tx1, tx0 + 1)):
                    if 0 <= tx < room_gw and 0 <= ty < room_gh:
                        local_mask[ty][tx] = True

            shift = shift_by_transition.get(
                (name, t.x, t.y, t.width, t.height)
            )
            if shift is None or shift == (0, 0):
                continue
            # Mark the 1-cell margin around this doorway so any real wall
            # cell in it (a jamb, not the gap itself) gets nudged the same
            # way this room's matching strip below is nudged.
            for ty in range(ty0 - 1, max(ty1, ty0 + 1) + 1):
                for tx in range(tx0 - 1, max(tx1, tx0 + 1) + 1):
                    if 0 <= tx < room_gw and 0 <= ty < room_gh and painted_mask[ty][tx]:
                        jamb_shift[ty][tx] = shift

        region_id, depth = _nested_fill_depths(local_mask, room_gw, room_gh)

        for row in range(room_gh):
            for col in range(room_gw):
                cx, cy = base_gx + col, base_gy + row
                shift = jamb_shift[row][col]
                if shift is not None:
                    # Real wall cell being relocated by a doorway nudge —
                    # queue it for the deferred pass instead of drawing it
                    # (or anything else) at this cell now. Keep the true
                    # (un-nudged) position too: the nudge exists only to
                    # dodge the neighbour room's own jamb landing on the
                    # same cell, and if that neighbour turns out to have no
                    # geometry there at all (its room simply doesn't extend
                    # that far), nudging away leaves the true cell — and
                    # the wall it should show — empty instead of avoiding
                    # anything. Decided once every room's own pass is in.
                    deferred_jamb_pixels.append(
                        ((cx, cy), (cx + shift[0], cy + shift[1]))
                    )
                    continue
                if not (0 <= cx < grid_w and 0 <= cy < grid_h):
                    continue
                if local_mask[row][col]:
                    img.set_at((cx, cy), COLOR_OUTLINE)
                elif depth[region_id[row][col]] % 2 == 1:
                    img.set_at((cx, cy), COLOR_FILL)

    # Now stamp the jambs, after every room's ordinary fill+outline has
    # already been laid down, so they aren't clobbered by a room's own
    # floor color landing on the same (shifted) cell. Prefer the nudged
    # position — that's what keeps the jamb visually continuous with its
    # own (already-nudged) doorway strip — but only if the true position
    # actually holds something from the neighbour room that the nudge was
    # meant to dodge. If the true cell is still empty (alpha 0), the
    # neighbour never reached it — e.g. this room's grid runs a row or two
    # short of the other side's — so draw the wall at its true position
    # instead of vanishing it into a gap neither side ever fills.
    for (true_cx, true_cy), (nudged_cx, nudged_cy) in deferred_jamb_pixels:
        true_in_bounds = 0 <= true_cx < grid_w and 0 <= true_cy < grid_h
        true_occupied = true_in_bounds and img.get_at((true_cx, true_cy))[3] != 0
        if true_occupied:
            if 0 <= nudged_cx < grid_w and 0 <= nudged_cy < grid_h:
                img.set_at((nudged_cx, nudged_cy), COLOR_OUTLINE)
        elif true_in_bounds:
            img.set_at((true_cx, true_cy), COLOR_OUTLINE)

    # Transitions — orange, drawn from each room's own (de-duped) list so a
    # doorway always renders even if the neighbour room isn't in `origins`
    # (e.g. it belongs to a different group / wasn't reachable).
    #
    # A doorway's two sides (this room's exit rect and the target room's
    # matching entry rect) are *deliberately* aligned by build_zone_layout
    # to land on the exact same world-space cells — that's what makes the
    # rooms line up correctly. But that means drawing both, unmodified,
    # just paints the same pixels twice: only one strip ever shows.
    #
    # For display only, each matched pair is drawn as two touching strips
    # instead of one overlapping one: the "owning" room's rect stays at its
    # true position, and the neighbour's coincident rect is nudged one cell
    # into the neighbour's own interior (using its exit_direction to know
    # which way "interior" is). Doesn't affect alignment/collision — this
    # is purely how the pair gets rasterized into the minimap.
    def _stamp_transition_rect(rox, roy, rect, dx_cells=0, dy_cells=0):
        x0, y0 = gx(rox + rect.x), gy(roy + rect.y)
        x1 = gx(rox + rect.x + rect.width)
        y1 = gy(roy + rect.y + rect.height)
        for cy in range(y0, max(y1, y0 + 1)):
            for cx in range(x0, max(x1, x0 + 1)):
                px, py = cx + dx_cells, cy + dy_cells
                if 0 <= px < grid_w and 0 <= py < grid_h:
                    # Checkerboard the strip between the two transition
                    # colors so it reads as a hazard-stripe pattern rather
                    # than a flat block.
                    color = COLOR_TRANSITION_A if (px + py) % 2 == 0 else COLOR_TRANSITION_B
                    img.set_at((px, py), color)

    # Owner/target roles (and the nudge amount for targets) were already
    # worked out once above, alongside the wall-jamb nudge, so the strip
    # and its jambs always agree on where the seam falls.
    drawn_pairs = set()

    for name, (ox, oy) in origins.items():
        room = rooms_by_name[name]
        for t in _uniq_transitions(room):
            target_name = getattr(t, 'target_room', None)
            target_room = rooms_by_name.get(target_name) if target_name else None

            if target_room is None or target_name not in origins:
                # No connected neighbour to pair against (out of this
                # group, or unreachable) — draw the lone strip as-is.
                _stamp_transition_rect(ox, oy, t)
                continue

            match = next(
                (mt for mt in _uniq_transitions(target_room)
                 if getattr(mt, 'target_room', None) == room.name),
                None
            )
            if match is None:
                _stamp_transition_rect(ox, oy, t)
                continue

            pair_key = frozenset((
                (name, t.x, t.y, t.width, t.height),
                (target_name, match.x, match.y, match.width, match.height),
            ))
            if pair_key in drawn_pairs:
                # Already drawn from the other room's side of this loop.
                continue
            drawn_pairs.add(pair_key)

            # Each side is stamped at its true position unless
            # shift_by_transition says it's the non-owner side of this
            # pair, in which case it gets nudged one cell into its own
            # interior — same canonical owner/non-owner split computed
            # once above (name-sorted, not BFS-order-dependent), so
            # whichever side is nudged here always matches whichever
            # side had its wall jambs nudged in the pass above.
            own_dx, own_dy = shift_by_transition.get(
                (name, t.x, t.y, t.width, t.height), (0, 0)
            )
            _stamp_transition_rect(ox, oy, t, own_dx, own_dy)

            target_ox, target_oy = origins[target_name]
            other_dx, other_dy = shift_by_transition.get(
                (target_name, match.x, match.y, match.width, match.height), (0, 0)
            )
            _stamp_transition_rect(target_ox, target_oy, match, other_dx, other_dy)

    # Icons (spawn points, entities, world-map signs) are intentionally not
    # drawn on the minimap — the zone silhouette + doorway strips are all
    # that's shown.

    return img


def get_zone_map_surface(current_room, room_manager, pulse_t=0.0):
    """Convenience one-call entry point for callers (ScouterMenu) that just
    want 'the current zone's map, with this room highlighted' and don't
    need direct access to the layout graph. Rebuilds the layout every call
    — cheap for the room counts this genre deals with, but callers drawing
    every frame should still cache the returned Surface themselves keyed on
    (current_room.group, current_room.name) and only re-render on pulse
    changes; see ScouterMenu._draw_map_section."""
    origins, rooms_by_name = build_zone_layout(
        current_room, room_manager.get_room_by_name
    )
    return render_zone_surface(origins, rooms_by_name, current_room.name, pulse_t)