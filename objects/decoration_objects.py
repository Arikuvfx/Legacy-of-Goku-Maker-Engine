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

import copy
import json
import os
import sys

import pygame

from core.draw_layers import DrawLayer
from config.settings import RENDER_SCALE
from objects.collision_object import beam_block_distance_for_rect


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
        'grid_rows': 2, 'frame_count': 3,
        'sequence': [1, 2, 1, 2, 1, 2, 3, 2, 3, 2, 3, 2, 1, 1, 2, 3, 2, 1],
        'fps': 3,
        'collision_size': (32, 25),
        # NOTE: label for row 1 is a placeholder — rename once it's clear
        # what the second row actually depicts.
        'variants': ['Tree', 'Tree (Variant 2)'],
    },
}


# Decorations that are already explicitly configured above are never replaced
# by auto-discovery.  This is intentional: the tree's hand-authored animation
# sequence is part of its design and must remain exactly as it is.
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # decoration_objects.py lives in objects/, so its parent is the project root.
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DECORATIONS_ROOT = os.path.join(BASE_DIR, 'assets', 'objects', 'decorations')
_DECORATION_IMAGE_EXTENSIONS = ('.png', '.webp', '.jpg', '.jpeg')


def _resolve_asset_path(path):
    """Resolve a decoration asset path against the project root.

    Existing hardcoded paths in room data remain relative strings such as
    ``assets/objects/decorations/tree/tree.png``; the runtime simply resolves
    those against the actual project/executable directory when loading them.
    Absolute paths are passed through unchanged.
    """
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.join(BASE_DIR, path)


def _decoration_display_name(decoration_type):
    """Turn an asset/folder id into a readable editor label."""
    return decoration_type.replace('_', ' ').replace('-', ' ').strip().title()


def _safe_json_load(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _find_decoration_image(folder, decoration_type, metadata):
    """Resolve the sprite/spritesheet used by an auto-discovered decoration."""
    configured = metadata.get('sheet_path') or metadata.get('sheet') or metadata.get('sprite')
    if isinstance(configured, str) and configured.strip():
        configured = configured.strip()
        if os.path.isabs(configured):
            candidates = [configured]
        else:
            candidates = [os.path.join(folder, configured), _resolve_asset_path(configured)]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate

    preferred = os.path.join(folder, f'{decoration_type}.png')
    if os.path.isfile(preferred):
        return preferred

    try:
        files = sorted(
            name for name in os.listdir(folder)
            if name.lower().endswith(_DECORATION_IMAGE_EXTENSIONS)
            and os.path.isfile(os.path.join(folder, name))
        )
    except OSError:
        files = []

    return os.path.join(folder, files[0]) if files else None


def _normalise_discovered_style(decoration_type, folder, metadata):
    """Build a runtime decoration style from a folder + optional JSON manifest.

    A PNG dropped into a decoration folder needs no metadata: it becomes one
    static frame.  A sidecar ``decoration.json`` is only needed for custom
    spritesheet layouts, animation, variants, or a manual collision rectangle.
    """
    image_path = _find_decoration_image(folder, decoration_type, metadata)
    if not image_path:
        return None

    try:
        image = pygame.image.load(image_path)
        image_w, image_h = image.get_width(), image.get_height()
    except (pygame.error, OSError, FileNotFoundError):
        return None

    frame_w = int(metadata.get('frame_w', image_w))
    frame_h = int(metadata.get('frame_h', image_h))
    frame_w = max(1, min(frame_w, image_w))
    frame_h = max(1, min(frame_h, image_h))

    max_columns = max(1, image_w // frame_w)
    max_rows = max(1, image_h // frame_h)
    frame_count = max(1, min(int(metadata.get('frame_count', 1)), max_columns))
    grid_rows = max(1, min(int(metadata.get('grid_rows', 1)), max_rows))

    raw_variants = metadata.get('variants')
    variants = []
    if isinstance(raw_variants, list):
        for item in raw_variants:
            if isinstance(item, str):
                variants.append(item)
            elif isinstance(item, dict):
                variants.append(str(item.get('name') or item.get('label') or f'Variant {len(variants) + 1}'))

    if not variants:
        base_label = metadata.get('label') or _decoration_display_name(decoration_type)
        variants = [base_label]
        for i in range(1, grid_rows):
            variants.append(f'{base_label} (Variant {i + 1})')
    elif len(variants) < grid_rows:
        base_label = metadata.get('label') or _decoration_display_name(decoration_type)
        while len(variants) < grid_rows:
            variants.append(f'{base_label} (Variant {len(variants) + 1})')
    else:
        variants = variants[:grid_rows]

    sequence = metadata.get('sequence', [1])
    if not isinstance(sequence, list) or not sequence:
        sequence = [1]
    sequence = [max(1, min(int(n), frame_count)) for n in sequence]

    style = {
        'label': metadata.get('label') or _decoration_display_name(decoration_type),
        # Keep paths relative to the project root so saved room files remain
        # portable just like the existing hardcoded tree path.
        'sheet_path': (
            os.path.relpath(image_path, start=BASE_DIR).replace(os.sep, '/')
            if not os.path.isabs(image_path) else image_path
        ),
        'frame_w': frame_w,
        'frame_h': frame_h,
        'grid_rows': grid_rows,
        'frame_count': frame_count,
        'sequence': sequence,
        'fps': max(0, float(metadata.get('fps', 0))),
        'variants': variants,
        'auto_discovered': True,
        # No collision_size means Decoration will infer a small base hitbox
        # from the visible pixels in the lower part of the sprite.
    }

    if 'collision_size' in metadata:
        try:
            w, h = metadata['collision_size']
            style['collision_size'] = (int(w), int(h))
        except (TypeError, ValueError):
            pass

    if 'collision_rect' in metadata:
        try:
            x, y, w, h = metadata['collision_rect']
            style['collision_rect'] = (int(x), int(y), int(w), int(h))
        except (TypeError, ValueError):
            pass

    return style


def discover_decoration_styles(root=DECORATIONS_ROOT):
    """Discover new decoration types from the assets tree.

    Each subfolder becomes one decoration type. The folder may contain a PNG
    named after the folder (preferred) or any other supported image. A folder
    can optionally contain ``decoration.json`` for advanced animation/layout
    settings. Existing definitions in ``DECORATION_STYLES`` always win.

    Root-level PNGs are also supported as a convenience: ``assets/.../rock.png``
    becomes decoration type ``rock`` when that type is not already configured.
    """
    discovered = {}
    if not os.path.isdir(root):
        return discovered

    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return discovered

    # First support the recommended folder-per-decoration layout.
    for entry in entries:
        folder = os.path.join(root, entry)
        if not os.path.isdir(folder):
            continue

        decoration_type = entry.strip().lower()
        if not decoration_type or decoration_type in DECORATION_STYLES:
            continue

        metadata = _safe_json_load(os.path.join(folder, 'decoration.json'))
        style = _normalise_discovered_style(decoration_type, folder, metadata)
        if style:
            discovered[decoration_type] = style

    # Also allow a simple root-level PNG for quick static decorations.
    for entry in entries:
        path = os.path.join(root, entry)
        if not os.path.isfile(path) or not entry.lower().endswith(_DECORATION_IMAGE_EXTENSIONS):
            continue

        decoration_type = os.path.splitext(entry)[0].strip().lower()
        if not decoration_type or decoration_type in DECORATION_STYLES or decoration_type in discovered:
            continue

        style = _normalise_discovered_style(decoration_type, root, {'label': _decoration_display_name(decoration_type)})
        if style:
            discovered[decoration_type] = style

    return discovered


# Keep a pristine copy of every hand-authored definition. Auto-discovery
# may add new decoration types, but it must never replace a built-in entry.
_HARDCODED_DECORATION_STYLES = copy.deepcopy(DECORATION_STYLES)


def _apply_builtin_collision_overrides():
    """Apply optional collision-only overrides to built-in decorations.

    The creator is allowed to edit the Tree's collision visually, but its
    hand-authored animation/sheet configuration stays completely protected.
    Only collision_rect/collision_size are read from the sidecar manifest for
    built-ins.
    """
    for decoration_type in _HARDCODED_DECORATION_STYLES:
        folder = os.path.join(DECORATIONS_ROOT, decoration_type)
        if not os.path.isdir(folder):
            continue
        metadata = _safe_json_load(os.path.join(folder, 'decoration.json'))
        if not metadata:
            continue

        style = DECORATION_STYLES.get(decoration_type)
        if style is None:
            continue

        if 'collision_rect' in metadata:
            try:
                x, y, w, h = metadata['collision_rect']
                if int(w) > 0 and int(h) > 0:
                    style['collision_rect'] = (int(x), int(y), int(w), int(h))
                    # A manual rect is authoritative over the legacy size.
                    style.pop('collision_size', None)
            except (TypeError, ValueError):
                pass
        elif 'collision_size' in metadata:
            try:
                w, h = metadata['collision_size']
                if int(w) > 0 and int(h) > 0:
                    style['collision_size'] = (int(w), int(h))
                    style.pop('collision_rect', None)
            except (TypeError, ValueError):
                pass


def reload_decoration_styles():
    """Reload discovered decoration manifests without replacing built-ins.

    The global dictionary is mutated in-place so modules such as ObjectEditor
    that imported DECORATION_STYLES keep seeing the refreshed catalogue.
    """
    DECORATION_STYLES.clear()
    DECORATION_STYLES.update(copy.deepcopy(_HARDCODED_DECORATION_STYLES))
    _apply_builtin_collision_overrides()
    discovered = discover_decoration_styles()
    DECORATION_STYLES.update(discovered)

    # New definitions can change the frame cache; existing Decoration objects
    # retain their own runtime state, but newly-created instances must use the
    # freshly saved sheet/sequence immediately.
    decoration_cls = globals().get('Decoration')
    if decoration_cls is not None:
        decoration_cls._frame_cache.clear()
        decoration_cls._scaled_cache.clear()

    return DECORATION_STYLES


# Initial catalogue load. Built-ins are copied first and optional collision
# overrides are applied before adding newly-discovered decoration types.
DECORATION_STYLES.clear()
DECORATION_STYLES.update(copy.deepcopy(_HARDCODED_DECORATION_STYLES))
_apply_builtin_collision_overrides()
DECORATION_STYLES.update(discover_decoration_styles())


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

        # Hardcoded decorations such as the tree keep their existing
        # collision_size behavior. Auto-discovered decorations get a small
        # base hitbox inferred from their visible pixels unless a manifest
        # supplied collision_rect/collision_size explicitly.
        self._collision_rect = style.get('collision_rect')
        if self._collision_rect is None and style.get('auto_discovered') and 'collision_size' not in style:
            self._collision_rect = self._infer_auto_collision_rect(self.frames)

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
            sheet = pygame.image.load(_resolve_asset_path(style['sheet_path'])).convert_alpha()
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

    @staticmethod
    def _infer_auto_collision_rect(frames):
        """Infer a compact collision rectangle near the sprite's base.

        Only the lower 35%% of each frame is considered, which avoids letting a
        wide canopy/fan of leaves become a blocking wall. The result is capped
        so unusual art still gets a small, character-sized obstacle.
        """
        if not frames:
            return None

        frame = frames[0]
        frame_w, frame_h = frame.get_width(), frame.get_height()
        bottom_start = min(frame_h - 1, max(0, int(frame_h * 0.65)))
        region_h = max(1, frame_h - bottom_start)

        try:
            region = frame.subsurface((0, bottom_start, frame_w, region_h))
            bbox = region.get_bounding_rect(min_alpha=32)
        except (pygame.error, ValueError):
            bbox = pygame.Rect(0, 0, 0, 0)

        if bbox.width <= 0 or bbox.height <= 0:
            # No visible pixels near the base. Use a conservative fallback.
            width = max(4, int(frame_w * 0.22))
            height = max(4, int(frame_h * 0.14))
            return (
                max(0, (frame_w - width) // 2),
                max(0, frame_h - height - 2),
                width,
                height,
            )

        x = bbox.x
        y = bottom_start + bbox.y
        width = bbox.width
        height = bbox.height

        # Keep the inferred obstacle deliberately smaller than the visible
        # lower silhouette. This is scenery collision, not a pixel-perfect
        # mask, and prevents large leaves/grass from feeling like walls.
        max_width = max(4, int(frame_w * 0.45))
        max_height = max(4, int(frame_h * 0.30))
        width = min(width, max_width)
        height = min(height, max_height)

        center_x = x + bbox.width / 2.0
        x = int(round(center_x - width / 2.0))
        y = min(y, frame_h - height - 1)
        x = max(0, min(x, frame_w - width))
        y = max(0, min(y, frame_h - height))

        return (x, y, width, height)

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

    def get_visual_rect(self):
        """WORLD-space pygame.Rect for the full sprite footprint — bottom-
        center anchored at (self.x, self.y), same convention as
        get_render_info()/draw() (unscaled, since this is world space, not
        screen space).

        Deliberately NOT the same rect as get_collision_rect(): that one
        is a small trunk-only hitbox by design (see its own docstring —
        so the player can walk behind/under a wide canopy), which makes it
        the wrong rect for anything that needs to know where the sprite is
        actually DRAWN — e.g. LayerManager._apply_decoration_occlusion
        (draw_layers.py), which tests whether an attack visually overlaps
        the canopy, not just the narrow trunk.
        """
        if not self.active:
            return None
        return pygame.Rect(
            self.x - self.width / 2,
            self.y - self.height,
            self.width,
            self.height,
        )

    def get_collision_rect(self):
        """Small blocking rect near the trunk/base — deliberately much
        smaller than the full canopy sprite (see DECORATION_STYLES'
        'collision_size') so the player can walk behind/under the canopy
        and only actually gets blocked right at the trunk."""
        if not self.active:
            return None

        # Auto-discovered/manual frame-local rectangle. Frame-local x/y are
        # measured from the sprite's top-left corner; the world anchor is the
        # sprite's bottom-center point at (self.x, self.y).
        if self._collision_rect is not None:
            rx, ry, rw, rh = self._collision_rect
            return pygame.Rect(
                int(self.x - self.width / 2 + rx),
                int(self.y - self.height + ry),
                int(rw),
                int(rh),
            )

        # Existing hardcoded behavior (notably the tree) stays untouched.
        return pygame.Rect(
            int(self.x - self._collision_w // 2),
            int(self.y - 10 - self._collision_h),
            int(self._collision_w),
            int(self._collision_h),
        )

    def get_beam_block_distance(self, attack):
        """Same contract as CollisionObject.get_beam_block_distance — return
        the SCREEN-space distance at which `attack` (a BeamAttack) should
        stop growing because this decoration's trunk hitbox (see
        get_collision_rect) is in its path, or None if it isn't this
        frame. Uses the small trunk rect, not the full canopy sprite —
        same "only the trunk actually blocks" reasoning as
        get_collision_rect's docstring — so a beam correctly grazes past a
        wide canopy but stops at the trunk.
        """
        rect = self.get_collision_rect()
        if rect is None:
            return None
        return beam_block_distance_for_rect(rect, attack)

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