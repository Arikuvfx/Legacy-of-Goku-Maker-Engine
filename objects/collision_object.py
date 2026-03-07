import pygame
import pygame.gfxdraw
from typing import Optional, List, Tuple


class CollisionObject:
    """
    Invisible wall object that prevents player movement
    Can be stretched to any size during placement
    """

    def __init__(self, x: int, y: int, width: int = 32, height: int = 32, room_name: str = ""):
        self.x = x  # Top-left corner x
        self.y = y  # Top-left corner y
        self.width = width
        self.height = height
        self.room_name = room_name
        self.id = 'collision_wall'
        self.name = 'Collision Wall'
        self.category = 'System'
        self.active = True

    def check_collision_with_player(self, player) -> bool:
        """Check if player collides with this collision wall"""
        # Get player's directional hitbox
        player_rect = player.get_collision_rect()

        # Collision wall box
        wall_rect = pygame.Rect(self.x, self.y, self.width, self.height)

        # AABB collision detection
        return player_rect.colliderect(wall_rect)

    def get_rect(self) -> pygame.Rect:
        """Get pygame Rect for this collision object"""
        return pygame.Rect(self.x, self.y, self.width, self.height)

    def to_dict(self):
        """Serialize collision object for saving"""
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
        """Deserialize collision object from save data"""
        return CollisionObject(
            data.get('x', 0),
            data.get('y', 0),
            data.get('width', 32),
            data.get('height', 32),
            room_name
        )


class CollisionObjectManager:
    """
    Manages collision objects for all rooms
    """

    def __init__(self):
        # Dictionary mapping room names to lists of collision objects
        self.collision_objects: dict[str, List[CollisionObject]] = {}

    def get_collision_objects(self, room_name: str) -> List[CollisionObject]:
        """Get all collision objects for a room"""
        return self.collision_objects.get(room_name, [])

    def add_collision_object(self, collision_obj: CollisionObject) -> CollisionObject:
        """Add a collision object to a room"""
        if collision_obj.room_name not in self.collision_objects:
            self.collision_objects[collision_obj.room_name] = []

        self.collision_objects[collision_obj.room_name].append(collision_obj)
        return collision_obj

    def remove_collision_object(self, collision_obj: CollisionObject):
        """Remove a collision object from a room"""
        if collision_obj.room_name in self.collision_objects:
            if collision_obj in self.collision_objects[collision_obj.room_name]:
                self.collision_objects[collision_obj.room_name].remove(collision_obj)

    def clear_room(self, room_name: str):
        """Clear all collision objects from a room"""
        if room_name in self.collision_objects:
            self.collision_objects[room_name] = []

    def save_to_dict(self) -> dict:
        """Save all collision objects to dictionary"""
        return {
            room_name: [obj.to_dict() for obj in objects]
            for room_name, objects in self.collision_objects.items()
        }

    def load_from_dict(self, data: dict):
        """Load collision objects from dictionary"""
        self.collision_objects = {}
        for room_name, objects_data in data.items():
            self.collision_objects[room_name] = [
                CollisionObject.from_dict(obj_data, room_name)
                for obj_data in objects_data
            ]


def draw_collision_object(screen, collision_obj: CollisionObject, camera_x: int, camera_y: int,
                          render_scale: int, dev_mode: bool = True, selected: bool = False):
    """
    Draw a collision object on screen

    Args:
        screen: Pygame screen surface
        collision_obj: The collision object to draw
        camera_x: Camera X position
        camera_y: Camera Y position
        render_scale: Rendering scale factor
        dev_mode: Whether to show collision objects (only visible in dev mode)
        selected: Whether this object is currently selected
    """
    if not dev_mode:
        return

    # Calculate screen position
    screen_x = (collision_obj.x * render_scale) - camera_x
    screen_y = (collision_obj.y * render_scale) - camera_y
    screen_width = collision_obj.width * render_scale
    screen_height = collision_obj.height * render_scale

    rect = pygame.Rect(int(screen_x), int(screen_y), int(screen_width), int(screen_height))

    # Draw semi-transparent fill
    alpha = 100 if not selected else 150
    fill_color = (255, 0, 0, alpha) if not selected else (255, 100, 0, alpha)
    fill_surface = pygame.Surface((int(screen_width), int(screen_height)), pygame.SRCALPHA)
    fill_surface.fill(fill_color)
    screen.blit(fill_surface, (int(screen_x), int(screen_y)))

    # Draw border
    border_color = (255, 0, 0) if not selected else (255, 165, 0)
    border_width = 2 if not selected else 3
    pygame.draw.rect(screen, border_color, rect, border_width)

    # Draw diagonal lines pattern
    line_color = (200, 0, 0, 100) if not selected else (255, 140, 0, 150)
    line_surface = pygame.Surface((int(screen_width), int(screen_height)), pygame.SRCALPHA)

    spacing = 16 * render_scale
    # Draw diagonal lines from top-left to bottom-right
    for i in range(int(-screen_height), int(screen_width + screen_height), int(spacing)):
        start_x = i
        start_y = 0
        end_x = i + screen_height
        end_y = screen_height
        pygame.draw.line(line_surface, line_color, (start_x, start_y), (end_x, end_y), 1)

    screen.blit(line_surface, (int(screen_x), int(screen_y)))

    # Draw corner handles
    handle_size = 6 * render_scale
    handle_color = (255, 255, 0) if selected else (255, 200, 0)

    corners = [
        (screen_x, screen_y),  # Top-left
        (screen_x + screen_width, screen_y),  # Top-right
        (screen_x, screen_y + screen_height),  # Bottom-left
        (screen_x + screen_width, screen_y + screen_height)  # Bottom-right
    ]

    for corner_x, corner_y in corners:
        pygame.draw.rect(screen, handle_color,
                         (int(corner_x - handle_size // 2),
                          int(corner_y - handle_size // 2),
                          int(handle_size), int(handle_size)))
        pygame.draw.rect(screen, (0, 0, 0),
                         (int(corner_x - handle_size // 2),
                          int(corner_y - handle_size // 2),
                          int(handle_size), int(handle_size)), 1)

    # Draw dimensions text if object is large enough
    if screen_width > 50 and screen_height > 30:
        font = pygame.font.Font(None, 18)
        dims_text = f"{collision_obj.width} x {collision_obj.height}"
        text_surface = font.render(dims_text, True, (255, 255, 255))
        text_rect = text_surface.get_rect(center=(screen_x + screen_width // 2,
                                                  screen_y + screen_height // 2))

        # Draw text background
        bg_rect = text_rect.inflate(8, 4)
        bg_surface = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
        bg_surface.fill((0, 0, 0, 180))
        screen.blit(bg_surface, bg_rect.topleft)

        screen.blit(text_surface, text_rect)