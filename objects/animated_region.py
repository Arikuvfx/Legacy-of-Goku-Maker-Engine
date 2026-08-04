from __future__ import annotations

import pygame
import pygame.gfxdraw
from typing import List

from core.draw_layers import DrawLayer


# Visual styling per region type — used by the editor overlay only.
# 'sheet' is the sprite name the runtime controller pulls its frames from.
# 'mode' picks the runtime playback style:
#   'patch' — the frame is a grid of sub-patches; the runtime crops a
#             chunk_size chunk out of the current frame at
#             (world_x % frame_size, world_y % frame_size). Used for water,
#             lava, and grass: water/lava use frame_size=64 (an 8x8 grid of
#             8x8 patches), chunk_size=8; grass uses a 128x64 sheet — 2
#             frame_size=64 frames side by side, each a 4x4 grid of
#             chunk_size=16 patches.
#   'checkerboard' — RETIRED, kept commented out below (in REGION_STYLES
#             and in the runtime draw loop) in case it's needed again.
#             Grass used this before switching to 'patch' mode above: the
#             frame IS the whole tile, no sub-patch cropping, sheet laid
#             out as a 2-row grid of batches with column-groups per row.
#   'tile' — for sheets like dirt that aren't square and aren't a
#             sub-patch grid at all: each of the sheet's frames IS a
#             complete, non-animated frame_w x frame_h tile. No time-based
#             playback, no sub-patch cropping — the runtime just tiles a
#             single frame edge-to-edge across the region, snapped to a
#             global frame_w x frame_h grid (so neighboring dirt regions
#             line up at their shared border). Which frame is shown is
#             fixed per placed region by that region's 'variant' index
#             (editor-selectable, like other objects' variant pickers —
#             default 0, the first variant in the sheet), not randomized
#             or animated. 'chunk_size' and 'anim'/'fps'/'scroll' (all
#             'patch'-only) are meaningless here and ignored.
# 'frame_w' / 'frame_h' (only read for 'tile' mode) are the pixel
# dimensions of one frame, since 'tile' sheets aren't necessarily square
# the way 'patch' sheets are. 'grid_rows' (see below) times frame_h gives
# the sheet's total height for a single-column vertical strip like dirt's
# (dirt's frame is 24x24, so its sheet is a 24-wide, 96-tall strip).
# 'grid_rows' is how many frames are stacked in the sheet — for 'patch'
# mode this is normally 1 (a plain left-to-right strip); dirt's sheet is
# a vertical strip instead, one variant per row, so it sets grid_rows to
# its variant count (4) and relies on frame_w/frame_h rather than a
# single square frame_size.
# 'anim' (only read for 'patch' mode) picks how the frame strip is stepped
# through over time:
#   'loop'     — plays frame 0, 1, 2, ... N-1, 0, 1, 2, ... in a straight
#                cycle. This is the default when 'anim' is omitted.
#   'pingpong' — bounces forward then back: 0, 1, ..., N-1, N-2, ..., 1, 0,
#                1, ... Used for lava's 1-2-3-2-1-2-3 flicker.
# 'fps' (only read for 'patch' mode) is how many frames per second the
# strip advances through — higher is a faster flicker. Defaults to the
# runtime's shared default (6, matching the tileset's own animated tiles)
# when omitted, same as water/grass. Lava sets its own so its flicker
# speed can be tuned independently.
# 'scroll' (only read for 'patch' mode) is an optional (pixels_per_sec_x,
# pixels_per_sec_y) pair, in unscaled world pixels. It continuously slides
# the sample window each chunk crops from, independent of which animation
# frame is currently showing. This has to be a smooth sub-chunk offset, not
# a jump to a different whole patch — the 8x8 patches in the sheet are
# independently-drawn art, not slices of one continuous image, so jumping
# between them reads as extra flicker rather than motion. A smooth pixel
# offset instead slides the crop window across (and blends between) the
# patches, which is what actually reads as the texture flowing. Because
# every chunk in the region gets the same offset, neighboring chunks stay
# aligned — it's the whole region's texture sliding together, not each
# patch flickering independently. Defaults to (0, 0) — no scroll — when
# omitted, matching water's original behavior (any sense of motion there
# comes from the art in the frame strip itself). Lava uses this to scroll
# down-right even though its own frames don't have that motion baked in.
# 'frames_per_batch' / 'batch_swap_ms' / 'grid_rows' were only read for the
# now-retired 'checkerboard' mode — see the commented-out grass entry
# below and the commented-out block in the runtime draw loop.
REGION_STYLES = {
    'water': {'color': (0, 120, 255), 'label': 'Water', 'sheet': 'spr_water_filler',
              'mode': 'patch', 'frame_size': 64, 'chunk_size': 8, 'anim': 'loop'},
    'ice': {'color': (90, 60, 30), 'label': "Ice", 'sheet': 'spr_ice_filler',
        'mode': 'patch', 'frame_size': 64, 'chunk_size': 16, 'anim': 'loop'},
    'lava': {'color': (255, 90, 20), 'label': 'Lava', 'sheet': 'spr_lava_filler',
             'mode': 'patch', 'frame_size': 64, 'chunk_size': 8, 'anim': 'pingpong',
             'scroll': (4, 4), 'fps': 3},
    'grass': {'color': (60, 170, 40), 'label': 'Grass', 'sheet': 'spr_OV_grass1',
              'mode': 'patch', 'frame_size': 64, 'chunk_size': 16, 'anim': 'loop', 'fps': 1},
    'highgrass': {'color': (60, 170, 40), 'label': 'High Grass', 'sheet': 'spr_highgrass_filler',
              'mode': 'tile', 'frame_w': 48, 'frame_h': 32},
    'snow': {'color': (60, 170, 40), 'label': 'Snow', 'sheet': 'spr_snow_filler',
              'mode': 'patch', 'frame_size': 64, 'chunk_size': 16, 'anim': 'loop'},
    'dirt': {'color': (120, 80, 40), 'label': 'Dirt', 'sheet': 'spr_dirt_filler',
             'mode': 'tile', 'frame_w': 24, 'frame_h': 24, 'grid_rows': 4},
    'field': {'color': (60, 170, 40), 'label': 'Field', 'sheet': 'spr_field_filler',
              'mode': 'tile', 'frame_w': 64, 'frame_h': 72},
    'stone': {'color': (60, 170, 40), 'label': 'Stone', 'sheet': 'spr_stone_filler',
              'mode': 'tile', 'frame_w': 24, 'frame_h': 24},
    'stonepath': {'color': (60, 170, 40), 'label': 'Stone Path', 'sheet': 'spr_stonepath_filler',
              'mode': 'patch', 'frame_size': 64, 'chunk_size': 16, 'anim': 'loop'},
    'mud': {'color': (90, 60, 30), 'label': 'Mud', 'sheet': 'spr_mud_filler',
            'mode': 'patch', 'frame_size': 64, 'chunk_size': 16, 'anim': 'loop'},
    'mossystone': {'color': (90, 60, 30), 'label': 'Mossy Stone', 'sheet': 'spr_mossystone_filler',
                   'mode': 'patch', 'frame_size': 64, 'chunk_size': 16, 'anim': 'loop'},
    'tiles': {'color': (210, 190, 110), 'label': 'Tiles', 'sheet': 'spr_tiles_filler',
              'mode': 'tile', 'frame_w': 64, 'frame_h': 64, 'grid_rows': 16},
    'floor': {'color': (60, 170, 40), 'label': 'Floor', 'sheet': 'spr_floor_filler',
              'mode': 'patch', 'frame_size': 64, 'chunk_size': 16, 'anim': 'loop'},
    'rustymetal': {'color': (90, 60, 30), 'label': 'Rusty Metal', 'sheet': 'spr_rustymetal_filler',
             'mode': 'patch', 'frame_size': 64, 'chunk_size': 16, 'anim': 'loop'},
    'woodplanks': {'color': (210, 190, 110), 'label': 'Wood Planks', 'sheet': 'spr_woodplanks_filler',
                   'mode': 'tile', 'frame_w': 64, 'frame_h': 64, 'grid_rows': 2},
    'sand': {'color': (210, 190, 110), 'label': 'Sand', 'sheet': 'spr_sand_filler',
             'mode': 'tile', 'frame_w': 64, 'frame_h': 64, 'grid_rows': 3},
    'hell': {'color': (210, 190, 110), 'label': 'Hell', 'sheet': 'spr_hell_filler',
             'mode': 'tile', 'frame_w': 64, 'frame_h': 64, 'grid_rows': 2},
    'buu': {'color': (210, 190, 110), 'label': 'Buu', 'sheet': 'spr_buu_filler',
             'mode': 'tile', 'frame_w': 64, 'frame_h': 64, 'grid_rows': 2},
    'babidiground': {'color': (60, 170, 40), 'label': 'Babidi Ground', 'sheet': 'spr_babidiground_filler',
              'mode': 'tile', 'frame_w': 96, 'frame_h': 56},
    'babidiground2': {'color': (60, 170, 40), 'label': 'Babidi Ground 2', 'sheet': 'spr_babidiground2_filler',
              'mode': 'tile', 'frame_w': 48, 'frame_h': 48},
    'browntiles': {'color': (60, 170, 40), 'label': 'Brown Tiles', 'sheet': 'spr_browntiles_filler',
              'mode': 'tile', 'frame_w': 48, 'frame_h': 48},
    'idek': {'color': (60, 170, 40), 'label': 'Idek', 'sheet': 'spr_idek_filler',
              'mode': 'tile', 'frame_w': 48, 'frame_h': 48},
    'whatever': {'color': (60, 170, 40), 'label': 'Whatever', 'sheet': 'spr_whatever_filler',
              'mode': 'tile', 'frame_w': 40, 'frame_h': 32},
    'clouds': {'color': (60, 170, 40), 'label': 'Clouds', 'sheet': 'spr_clouds_filler',
              'mode': 'tile', 'frame_w': 96, 'frame_h': 96, 'grid_rows': 2},
    'leafsBF': {'color': (60, 170, 40), 'label': 'Leafs BF', 'sheet': 'spr_leafsBF_filler',
              'mode': 'tile', 'frame_w': 80, 'frame_h': 80,},
    # --- Retired checkerboard-based grass config, kept for reference ---
    # 'grass': {'color': (60, 170, 40), 'label': 'Grass Region', 'sheet': 'spr_OV_grass1',
    #           'mode': 'checkerboard', 'frame_size': 16, 'chunk_size': 16,
    #           'frames_per_batch': 4, 'batch_swap_ms': 1500, 'grid_rows': 2},
}


class AnimatedRegion:
    """A placed box marking an area that should be filled with algorithmically
    drawn animated tiles (water, tall grass, etc.) at runtime, rather than
    individually-placed static tiles. One class covers every region_type —
    'water' and 'grass' behave identically at the editor level and only
    differ in which sprite sheet the runtime controller draws from."""

    def __init__(self, x: int, y: int, width: int = 32, height: int = 32,
                 room_name: str = "", region_type: str = "water", opacity: int = 100,
                 wave_amount: int = 100, seed: int = 0, color: tuple = (255, 255, 255),
                 variant: int = 0):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.room_name = room_name
        self.region_type = region_type
        self.opacity = opacity  # 0-100, editor slider drives this; 100 = fully opaque
        self.wave_amount = wave_amount  # 0-100, fraction of chunks showing animated waves
        self.seed = seed  # reroll to reshuffle which chunks show waves
        self.color = tuple(color)  # RGB tint; (255, 255, 255) = original art colors
        # Which sheet frame a 'tile'-mode region shows (e.g. dirt's 4
        # static variants). Editor-selectable per placed region, like the
        # variant pickers on other objects; default 0 = the first variant.
        # Unused by 'patch'-mode regions (water/lava/grass).
        self.variant = max(0, variant)
        self.id = f'{region_type}_region'
        self.name = REGION_STYLES.get(region_type, {}).get('label', 'Animated Region')
        self.category = 'System'
        self.active = True
        # Regions are ground-level fill (water/grass/lava/etc.) and must
        # always draw beneath tiles/entities, same as everything else on
        # DrawLayer.GROUND.
        self.draw_layer = DrawLayer.GROUND

    def get_rect(self) -> pygame.Rect:
        return pygame.Rect(self.x, self.y, self.width, self.height)

    def to_dict(self):
        return {
            'type': 'animated_region',
            'region_type': self.region_type,
            'x': self.x,
            'y': self.y,
            'width': self.width,
            'height': self.height,
            'room': self.room_name,
            'opacity': self.opacity,
            'wave_amount': self.wave_amount,
            'seed': self.seed,
            'color': list(self.color),
            'variant': self.variant
        }

    @staticmethod
    def from_dict(data: dict, room_name: str) -> 'AnimatedRegion':
        return AnimatedRegion(
            data.get('x', 0),
            data.get('y', 0),
            data.get('width', 32),
            data.get('height', 32),
            room_name,
            data.get('region_type', 'water'),
            data.get('opacity', 100),
            data.get('wave_amount', 100),
            data.get('seed', 0),
            tuple(data.get('color', [255, 255, 255])),
            data.get('variant', 0)
        )


class AnimatedRegionManager:
    """Tracks water/grass regions across all rooms. Mirrors
    CollisionObjectManager's interface — regions keyed by room_name, plus
    a region_type-filtered accessor for the runtime controller."""

    def __init__(self):
        self.regions: dict[str, List[AnimatedRegion]] = {}

    def get_regions(self, room_name: str) -> List[AnimatedRegion]:
        return self.regions.get(room_name, [])

    def get_regions_by_type(self, room_name: str, region_type: str) -> List[AnimatedRegion]:
        return [r for r in self.get_regions(room_name) if r.region_type == region_type]

    def add_region(self, region: AnimatedRegion) -> AnimatedRegion:
        self.regions.setdefault(region.room_name, []).append(region)
        return region

    def remove_region(self, region: AnimatedRegion):
        room = self.regions.get(region.room_name, [])
        if region in room:
            room.remove(region)

    def clear_room(self, room_name: str):
        self.regions[room_name] = []

    def save_to_dict(self) -> dict:
        return {
            room: [obj.to_dict() for obj in objs]
            for room, objs in self.regions.items()
        }

    def load_from_dict(self, data: dict):
        self.regions = {
            room: [AnimatedRegion.from_dict(obj, room) for obj in objs]
            for room, objs in data.items()
        }


_label_font = None


def draw_animated_region(screen, region: AnimatedRegion, camera_x: int, camera_y: int,
                          render_scale: int, dev_mode: bool = True, selected: bool = False):
    """Editor-only overlay — colored by region_type, with corner handles like
    the collision box overlay, so it's visually distinct at a glance."""
    if not dev_mode:
        return

    style = REGION_STYLES.get(region.region_type, {'color': (200, 200, 200)})
    base_color = style['color']

    sx = (region.x * render_scale) - camera_x
    sy = (region.y * render_scale) - camera_y
    sw = region.width  * render_scale
    sh = region.height * render_scale

    rect = pygame.Rect(int(sx), int(sy), int(sw), int(sh))

    alpha = 150 if selected else 100
    fill_color = tuple(min(255, c + 60) for c in base_color) + (alpha,) if selected else base_color + (alpha,)

    # The region box can be dragged far larger than the viewport, but only the
    # portion overlapping the screen is ever visible. Clip to that overlap
    # before allocating/filling a surface — otherwise a big region means
    # allocating and filling a multi-megapixel SRCALPHA surface every single
    # frame, most of which is off-screen and never seen.
    visible = rect.clip(screen.get_rect())
    if visible.width > 0 and visible.height > 0:
        fill_surf = pygame.Surface((visible.width, visible.height), pygame.SRCALPHA)
        fill_surf.fill(fill_color)
        screen.blit(fill_surf, (visible.x, visible.y))

    border_color = tuple(min(255, c + 60) for c in base_color) if selected else base_color
    border_width = 3 if selected else 2
    pygame.draw.rect(screen, border_color, rect, border_width)

    # Corner drag handles
    handle = 6 * render_scale
    handle_color = (255, 255, 0) if selected else (255, 200, 0)
    corners = [
        (sx,      sy),
        (sx + sw, sy),
        (sx,      sy + sh),
        (sx + sw, sy + sh),
    ]
    for cx, cy in corners:
        hx = int(cx - handle // 2)
        hy = int(cy - handle // 2)
        pygame.draw.rect(screen, handle_color, (hx, hy, int(handle), int(handle)))
        pygame.draw.rect(screen, (0, 0, 0),    (hx, hy, int(handle), int(handle)), 1)

    # Dimension + type label — skip if the box is too small to fit text
    if sw > 50 and sh > 30:
        global _label_font
        if _label_font is None:
            _label_font = pygame.font.Font(None, 18)
        font = _label_font
        label_text = f"{style.get('label', region.region_type)}  {region.width}x{region.height}"
        label = font.render(label_text, True, (255, 255, 255))
        label_rect = label.get_rect(center=(sx + sw // 2, sy + sh // 2))
        bg = pygame.Surface((label_rect.width + 8, label_rect.height + 4), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 180))
        screen.blit(bg, (label_rect.x - 4, label_rect.y - 2))
        screen.blit(label, label_rect)