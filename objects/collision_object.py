import pygame
import pygame.gfxdraw
from typing import List

_DIM_FONT = None  # lazily created on first draw; avoids re-loading a font every wall/frame


class CollisionObject:
    """Invisible wall placed in the editor to block player movement."""

    def __init__(self, x: int, y: int, width: int = 32, height: int = 32, room_name: str = ""):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.room_name = room_name
        self.id = 'collision_wall'
        self.name = 'Collision Wall'
        self.category = 'System'
        self.active = True

    def check_collision_with_player(self, player) -> bool:
        return player.get_collision_rect().colliderect(self.get_rect())

    def get_rect(self) -> pygame.Rect:
        return pygame.Rect(self.x, self.y, self.width, self.height)

    # Alias so this works anywhere player.get_collision_rect() is expected
    def get_collision_rect(self) -> pygame.Rect:
        return self.get_rect()

    def get_beam_block_distance(self, attack):
        """Return the distance (SCREEN-space pixels — see below — measured
        from the beam's origin at (attack.x, attack.y) along its own
        direction) at which `attack` (a BeamAttack) should stop growing
        because this wall is in its path — or None if this wall isn't in
        the beam's way at all this frame. Mirrors the beam-rect
        construction used for beam-vs-enemy hit-testing in
        Enemy.check_collision_with_attack, so a beam stops at whichever of
        a wall or an enemy it reaches first.
        """
        if not self.active:
            return None

        # self.x/y/width/height here (and this beam rect) are WORLD-space,
        # same convention as the player and enemies, so attack.width needs
        # converting down from screen-space to match.
        world_width = attack.width / attack.scale

        # This rect exists only to answer "is this wall somewhere along the
        # beam's travel line at all" (right axis, right side, laterally
        # overlapping the corridor) — NOT "has the beam already visually
        # grown as far as this wall". It used to be sized off
        # attack.length/attack.scale, i.e. the beam's CURRENT reach, but
        # attack.length isn't actually how far the beam's tip is drawn on
        # screen: the real tip sits at attack.get_tip_world_length(), which
        # adds BeamAttack._min_reach() on top of attack.length (begin-sprite
        # footprint, tip-sprite footprint, and — for beams like
        # BigBangKamehamehaAttack that set ball_gap/circle_gap/beam_gap —
        # a further fixed offset that can be quite large). Sizing this rect
        # off attack.length alone meant the wall only started registering
        # as "in reach" once attack.length itself had grown that far, while
        # the beam's actual rendered tip — already ahead of attack.length by
        # the full _min_reach() amount — had by then already visually grown
        # through the wall's near edge and, for beams with a large
        # _min_reach() like Big Bang Kamehameha, sometimes all the way to
        # (or past) its far edge before any obstruction was ever reported
        # and growth got capped. A wall's position along the beam's path
        # doesn't depend on how far the beam has grown so far, so this uses
        # a generously large fixed reach instead, to always catch it.
        reach = 100000

        if attack.direction == 'up':
            beam_rect = pygame.Rect(attack.x - world_width // 2, attack.y - reach,
                                     world_width, reach)
        elif attack.direction == 'down':
            beam_rect = pygame.Rect(attack.x - world_width // 2, attack.y,
                                     world_width, reach)
        elif attack.direction == 'left':
            beam_rect = pygame.Rect(attack.x - reach, attack.y - world_width // 2,
                                     reach, world_width)
        elif attack.direction == 'right':
            beam_rect = pygame.Rect(attack.x, attack.y - world_width // 2,
                                     reach, world_width)
        else:
            return None

        wall_rect = self.get_rect()
        if not beam_rect.colliderect(wall_rect):
            return None

        if attack.direction == 'up':
            world_distance = attack.y - wall_rect.bottom
        elif attack.direction == 'down':
            world_distance = wall_rect.top - attack.y
        elif attack.direction == 'left':
            world_distance = attack.x - wall_rect.right
        elif attack.direction == 'right':
            world_distance = wall_rect.left - attack.x
        else:
            return None

        # report_obstruction (and attack.length/max_length) live in
        # screen-space, so scale this world-space distance back up before
        # handing it back — otherwise the beam freezes attack.scale times
        # short of where the wall actually is.
        return max(0, world_distance) * attack.scale

    def to_dict(self):
        return {
            'type': 'collision_wall',
            'x': self.x,
            'y': self.y,
            'width': self.width,
            'height': self.height,
            'room': self.room_name
        }

    @staticmethod
    def from_dict(data: dict, room_name: str) -> 'CollisionObject':
        return CollisionObject(
            data.get('x', 0),
            data.get('y', 0),
            data.get('width', 32),
            data.get('height', 32),
            room_name
        )


class CollisionObjectManager:
    """Tracks collision walls across all rooms."""

    def __init__(self):
        self.collision_objects: dict[str, List[CollisionObject]] = {}

    def get_collision_objects(self, room_name: str) -> List[CollisionObject]:
        return self.collision_objects.get(room_name, [])

    def add_collision_object(self, collision_obj: CollisionObject) -> CollisionObject:
        self.collision_objects.setdefault(collision_obj.room_name, []).append(collision_obj)
        return collision_obj

    def remove_collision_object(self, collision_obj: CollisionObject):
        room = self.collision_objects.get(collision_obj.room_name, [])
        if collision_obj in room:
            room.remove(collision_obj)

    def clear_room(self, room_name: str):
        self.collision_objects[room_name] = []

    def report_beam_obstructions(self, room_name: str, attack):
        """Check every collision wall in `room_name` against `attack` (a
        BeamAttack) and report the nearest blocking distance to it, so the
        beam freezes at the wall instead of passing through.

        Call this once per beam per frame, anytime before attack.update(dt)
        is called that frame — same contract as BeamAttack.report_obstruction.
        Pairs with the equivalent per-enemy check already done inside
        Enemy.check_collision_with_attack, so a beam correctly stops at
        whichever of a wall or an enemy it reaches first.
        """
        if not hasattr(attack, 'report_obstruction'):
            return
        for wall in self.get_collision_objects(room_name):
            distance = wall.get_beam_block_distance(attack)
            if distance is not None:
                attack.report_obstruction(distance)

    def save_to_dict(self) -> dict:
        return {
            room: [obj.to_dict() for obj in objs]
            for room, objs in self.collision_objects.items()
        }

    def load_from_dict(self, data: dict):
        self.collision_objects = {
            room: [CollisionObject.from_dict(obj, room) for obj in objs]
            for room, objs in data.items()
        }


def draw_collision_object(screen, collision_obj: CollisionObject, camera_x: int, camera_y: int,
                          render_scale: int, dev_mode: bool = True, selected: bool = False):
    """Only visible in dev mode — draws the red hatched overlay with corner handles.

    fill/hatch/label surfaces are cached on collision_obj and only rebuilt when
    size/selected/render_scale change, since with many walls placed the editor
    was rebuilding and re-blitting fresh SRCALPHA surfaces (plus a fresh Font)
    for every wall, every frame.
    """
    if not dev_mode:
        return

    global _DIM_FONT
    if _DIM_FONT is None:
        _DIM_FONT = pygame.font.Font(None, 18)

    sx = (collision_obj.x * render_scale) - camera_x
    sy = (collision_obj.y * render_scale) - camera_y
    sw = collision_obj.width  * render_scale
    sh = collision_obj.height * render_scale

    rect = pygame.Rect(int(sx), int(sy), int(sw), int(sh))

    cache_key = (int(sw), int(sh), selected, render_scale)
    cache = getattr(collision_obj, '_draw_cache', None)
    if cache is None or cache[0] != cache_key:
        fill_surf, line_surf, label_bundle = _build_collision_overlay(
            collision_obj, int(sw), int(sh), render_scale, selected)
        cache = (cache_key, fill_surf, line_surf, label_bundle)
        collision_obj._draw_cache = cache

    _, fill_surf, line_surf, label_bundle = cache

    screen.blit(fill_surf, (int(sx), int(sy)))

    border_color = (255, 165, 0) if selected else (255, 0, 0)
    border_width = 3 if selected else 2
    pygame.draw.rect(screen, border_color, rect, border_width)

    screen.blit(line_surf, (int(sx), int(sy)))

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

    # Dimension label — skip if the box is too small to fit text
    if sw > 50 and sh > 30 and label_bundle is not None:
        bg, label, label_offset = label_bundle
        label_x = sx + sw // 2 - label_offset[0]
        label_y = sy + sh // 2 - label_offset[1]
        screen.blit(bg, (label_x - 4, label_y - 2))
        screen.blit(label, (label_x, label_y))


def _build_collision_overlay(collision_obj, sw, sh, render_scale, selected):
    """Builds the fill/hatch/label surfaces once per (size, selected, scale) combo."""
    alpha = 150 if selected else 100
    fill_color = (255, 100, 0, alpha) if selected else (255, 0, 0, alpha)
    fill_surf = pygame.Surface((sw, sh), pygame.SRCALPHA)
    fill_surf.fill(fill_color)

    line_color = (255, 140, 0, 150) if selected else (200, 0, 0, 100)
    line_surf = pygame.Surface((sw, sh), pygame.SRCALPHA)
    spacing = 16 * render_scale
    for i in range(-sh, sw + sh, int(spacing)):
        pygame.draw.line(line_surf, line_color, (i, 0), (i + sh, sh), 1)

    label_bundle = None
    if sw > 50 and sh > 30:
        label = _DIM_FONT.render(f"{collision_obj.width} x {collision_obj.height}", True, (255, 255, 255))
        bg = pygame.Surface((label.get_width() + 8, label.get_height() + 4), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 180))
        label_offset = (label.get_width() // 2, label.get_height() // 2)
        label_bundle = (bg, label, label_offset)

    return fill_surf, line_surf, label_bundle