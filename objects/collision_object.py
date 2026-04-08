import pygame
import pygame.gfxdraw
from typing import List


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
    """Only visible in dev mode — draws the red hatched overlay with corner handles."""
    if not dev_mode:
        return

    sx = (collision_obj.x * render_scale) - camera_x
    sy = (collision_obj.y * render_scale) - camera_y
    sw = collision_obj.width  * render_scale
    sh = collision_obj.height * render_scale

    rect = pygame.Rect(int(sx), int(sy), int(sw), int(sh))

    # Semi-transparent fill
    alpha = 150 if selected else 100
    fill_color = (255, 100, 0, alpha) if selected else (255, 0, 0, alpha)
    fill_surf = pygame.Surface((int(sw), int(sh)), pygame.SRCALPHA)
    fill_surf.fill(fill_color)
    screen.blit(fill_surf, (int(sx), int(sy)))

    border_color = (255, 165, 0) if selected else (255, 0, 0)
    border_width = 3 if selected else 2
    pygame.draw.rect(screen, border_color, rect, border_width)

    # Diagonal hatch lines
    line_color = (255, 140, 0, 150) if selected else (200, 0, 0, 100)
    line_surf = pygame.Surface((int(sw), int(sh)), pygame.SRCALPHA)
    spacing = 16 * render_scale
    for i in range(int(-sh), int(sw + sh), int(spacing)):
        pygame.draw.line(line_surf, line_color, (i, 0), (i + sh, sh), 1)
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
    if sw > 50 and sh > 30:
        font = pygame.font.Font(None, 18)
        label = font.render(f"{collision_obj.width} x {collision_obj.height}", True, (255, 255, 255))
        label_rect = label.get_rect(center=(sx + sw // 2, sy + sh // 2))
        bg = pygame.Surface((label_rect.width + 8, label_rect.height + 4), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 180))
        screen.blit(bg, (label_rect.x - 4, label_rect.y - 2))
        screen.blit(label, label_rect)