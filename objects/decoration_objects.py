"""
objects/decoration_object.py

Ambient scenery objects — trees, bushes, and similar animated decorations
placed via the Object Editor's Decorations category.

Sprite-sheet layout mirrors the 'tile'-mode convention objects/animated_region.py's
REGION_STYLES already uses for things like dirt (frame_w x frame_h grid,
grid_rows stacked vertically = one row per variant): a decoration_type's
sheet is grid_rows rows tall, each row frame_count frames wide, laid out
left to right. Row v holds variant v's frames. Every decoration type today
has exactly one row/variant, but the layout is ready for more without any
code changes — same as animated_region.py's own comment about new region
types "just working" once a sheet is dropped in.

Unlike AnimatedRegion (which fills an area with a generically-looped or
pingponged texture), a Decoration plays a single hand-authored frame
*sequence* — see DECORATION_STYLES['sequence'] below — since ambient scenery
like a tree swaying doesn't move at an even, generic cadence.

Y-SORT / OCCLUSION NOTE: a Decoration shares DrawLayer.NPCS with the player
(same scheme DestructibleStone already uses) so LayerManager's normal
(layer, y) sort naturally draws it in front of or behind the player. That
alone is enough for the trunk, but a tall canopy overlapping the player from
above needs the same pixel-accurate ghost-silhouette treatment foreground
tiles already get (see Game._draw_player_silhouette_if_occluded) — that
wiring lives in game.py, not here.
"""

import pygame

from core.draw_layers import DrawLayer
from config.settings import RENDER_SCALE


# Per-decoration-type sprite sheet + animation configuration.
#   'sheet_path'    — where the sheet lives on disk.
#   'frame_w'/'frame_h' — pixel size of a single frame.
#   'grid_rows'     — how many variants are stacked vertically in the sheet
#                      (see module docstring). Defaults to 1.
#   'frame_count'   — how many frames wide each variant row is.
#   'sequence'      — the exact frame order to step through, 1-based (frame
#                      '1' is the leftmost frame in the row) so it reads the
#                      same as it was specified. Loops once the end is
#                      reached. Hand-authored, not a generic loop/pingpong —
#                      a frame can repeat back-to-back or double back
#                      however looks right, unlike AnimatedRegion's 'anim'.
#   'fps'           — how many steps through 'sequence' per second.
#   'collision_size'— (width, height) of the small blocking hitbox placed at
#                      the decoration's base/trunk — deliberately much
#                      smaller than the full sprite so the player can walk
#                      behind/under a wide canopy without being blocked by
#                      it, same as a real tree only blocking at the trunk.
#   'variants'      — display names for a future per-row variant picker, one
#                      entry per grid_rows row. A single-variant type still
#                      needs exactly one entry here.
DECORATION_STYLES = {
    'tree': {
        'label': 'Tree',
        'sheet_path': 'assets/objects/decorations/tree/tree.png',
        'frame_w': 80, 'frame_h': 92,
        'grid_rows': 1, 'frame_count': 3,
        'sequence': [1, 2, 1, 2, 1, 2, 3, 2, 3, 2, 3, 2, 1, 1, 2, 3, 2, 1],
        'fps': 3,
        'collision_size': (32, 25),
        'variants': ['Tree'],
    },
}


class Decoration:
    """A placed, animated scenery object (tree, bush, etc.).

    Anchor convention: (x, y) is the point at the BASE of the object —
    where a tree's trunk meets the ground — not the sprite's center. A
    center anchor would put roughly half the canopy's height on the
    "wrong" side of the Y-sort compare for a sprite this much taller than
    it is wide, which is exactly the kind of thing that makes tall
    foliage sort incorrectly against the player. The sprite is drawn
    bottom-center aligned to (x, y) (same anchor convention already used
    for the 'world_map_sign' variant — see WorldMapObject.draw() /
    ObjectEditor's placement-preview code); the collision rect is a small
    box centered a little above (x, y), roughly where the trunk actually
    is, not under the whole canopy.
    """

    # Sliced (unscaled) frame surfaces, shared by every instance of a given
    # (decoration_type, variant) — same idea as DestructibleStone's
    # per-type sprite loading, just shared at the class level since many
    # trees in a room use identical art.
    _frame_cache: dict = {}

    # RENDER_SCALE-scaled current-frame surfaces, keyed by
    # (decoration_type, variant, frame_index, render_scale) — shared at the
    # class level for the same reason: every tree of the same type/variant
    # showing the same frame at the same scale needs the identical Surface.
    _scaled_cache: dict = {}

    def __init__(self, x, y, decoration_type='tree', variant=0, room_name=''):
        self.x = x
        self.y = y
        self.decoration_type = decoration_type
        self.variant = variant
        self.room_name = room_name
        self.active = True
        self.category = 'Decorations'

        style = DECORATION_STYLES.get(decoration_type, DECORATION_STYLES['tree'])
        # Full sprite frame size — used for drawing/placement, NOT collision
        # (see get_collision_rect for the smaller trunk hitbox).
        self.width  = style['frame_w']
        self.height = style['frame_h']
        self._fps       = style.get('fps', 6)
        self._sequence   = style.get('sequence', [1])
        self._collision_w, self._collision_h = style.get('collision_size', (16, 12))

        # Animation playback state — steps through self._sequence (a list
        # of 1-based frame numbers), not a raw frame index, so the same
        # frame can repeat back-to-back (e.g. [..., 1, 1, ...]) without any
        # special-casing.
        self._seq_index  = 0
        self._anim_timer = 0.0

        self.frames = self._load_frames(decoration_type, variant)

        # LAYER SYSTEM INTEGRATION — same DrawLayer/Y-sort scheme
        # DestructibleStone already uses, so decorations slot straight into
        # the existing y-sorted draw pass alongside the player/NPCs/enemies.
        self.draw_layer = DrawLayer.NPCS
        self.y_sort = True

    @classmethod
    def _load_frames(cls, decoration_type, variant):
        cache_key = (decoration_type, variant)
        if cache_key in cls._frame_cache:
            return cls._frame_cache[cache_key]

        style = DECORATION_STYLES.get(decoration_type, DECORATION_STYLES['tree'])
        frame_w      = style['frame_w']
        frame_h      = style['frame_h']
        frame_count  = style['frame_count']
        frames = []

        try:
            sheet = pygame.image.load(style['sheet_path']).convert_alpha()
            row_y = variant * frame_h
            if row_y + frame_h <= sheet.get_height():
                for i in range(frame_count):
                    fx = i * frame_w
                    if fx + frame_w <= sheet.get_width():
                        frames.append(sheet.subsurface((fx, row_y, frame_w, frame_h)).copy())
        except (pygame.error, OSError, FileNotFoundError):
            frames = []

        if not frames:
            # Asset not on disk yet — a simple green placeholder, repeated
            # as a single frame so sequence/animation logic still runs
            # without index errors (current_frame_index() clamps to it).
            placeholder = pygame.Surface((frame_w, frame_h), pygame.SRCALPHA)
            trunk_w = max(4, frame_w // 10)
            pygame.draw.rect(placeholder, (101, 67, 33),
                              (frame_w // 2 - trunk_w // 2, frame_h - frame_h // 3, trunk_w, frame_h // 3))
            pygame.draw.ellipse(placeholder, (34, 139, 34),
                                 (frame_w // 6, 0, frame_w * 2 // 3, frame_h * 2 // 3))
            pygame.draw.rect(placeholder, (0, 0, 0), (0, 0, frame_w, frame_h), 2)
            frames = [placeholder]

        cls._frame_cache[cache_key] = frames
        return frames

    def get_sort_key(self):
        """(layer, y) — sorted by the base/trunk position, see class docstring."""
        return (self.draw_layer, self.y)

    def current_frame_index(self):
        """0-based index into self.frames for whatever self._sequence
        currently points at, clamped so a placeholder single-frame list
        never raises IndexError even if 'sequence' references frame 3."""
        seq_num = self._sequence[self._seq_index % len(self._sequence)]
        return max(0, min(len(self.frames) - 1, seq_num - 1))

    def update(self, dt):
        if not self.active or len(self._sequence) <= 1 or self._fps <= 0:
            return
        self._anim_timer += dt
        step_duration = 1.0 / self._fps
        # while, not if — catches up if dt ever spikes past multiple steps
        # (e.g. a hitch), same defensive habit DestructibleStone's shake
        # timer doesn't need but frame-strip playback generally does.
        while self._anim_timer >= step_duration:
            self._anim_timer -= step_duration
            self._seq_index = (self._seq_index + 1) % len(self._sequence)

    def _scaled_frame(self, render_scale):
        """Current frame surface scaled to render_scale, cached at the
        class level (see _scaled_cache) so repeated draws and the
        silhouette-occlusion check never rescale the same frame twice."""
        cache_key = (self.decoration_type, self.variant, self.current_frame_index(), render_scale)
        cached = Decoration._scaled_cache.get(cache_key)
        if cached is not None:
            return cached
        frame = self.frames[self.current_frame_index()]
        w = max(1, int(self.width  * render_scale))
        h = max(1, int(self.height * render_scale))
        scaled = pygame.transform.scale(frame, (w, h))
        Decoration._scaled_cache[cache_key] = scaled
        return scaled

    def get_render_info(self, camera, render_scale=RENDER_SCALE):
        """Returns (scaled_surface, screen_x, screen_y) for the current
        frame, top-left aligned — exactly what draw() blits and what the
        silhouette-occlusion check needs to build a matching mask. Kept as
        one shared method so the two call sites can never drift apart."""
        scaled = self._scaled_frame(render_scale)
        screen_base_x = (self.x * render_scale) - camera.x
        screen_base_y = (self.y * render_scale) - camera.y  # base/trunk point
        screen_x = int(screen_base_x - scaled.get_width() // 2)
        screen_y = int(screen_base_y - scaled.get_height())  # bottom-aligned
        return scaled, screen_x, screen_y

    def draw(self, screen, camera, colors):
        if not self.active or not self.frames:
            return
        scaled, screen_x, screen_y = self.get_render_info(camera, RENDER_SCALE)
        screen.blit(scaled, (screen_x, screen_y))

    def get_collision_rect(self):
        """Small blocking rect near the trunk/base — deliberately much
        smaller than the full canopy sprite (see DECORATION_STYLES'
        'collision_size') so the player can walk behind/under the canopy
        and only actually gets blocked right at the trunk."""
        if not self.active:
            return None
        return pygame.Rect(
            int(self.x - self._collision_w // 2),
            int(self.y - 10 - self._collision_h),
            int(self._collision_w),
            int(self._collision_h),
        )

    def to_dict(self):
        return {
            'type': 'decoration',
            'decoration_type': self.decoration_type,
            'x': self.x,
            'y': self.y,
            'variant': self.variant,
        }

    @staticmethod
    def from_dict(data: dict) -> 'Decoration':
        return Decoration(
            data.get('x', 0),
            data.get('y', 0),
            data.get('decoration_type', 'tree'),
            data.get('variant', 0),
        )