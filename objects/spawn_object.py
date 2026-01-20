import pygame
import pygame.gfxdraw
import os
from typing import Optional


class SpawnObject:
    """
    Special object that marks the player spawn point in a room
    Only one spawn point can exist per room
    """

    def __init__(self, x: int, y: int, room_name: str):
        self.x = x
        self.y = y
        self.room_name = room_name
        self.width = 32
        self.height = 32
        self.id = 'spawn_point'
        self.name = 'Spawn Point'
        self.category = 'System'
        self.sprite = None

        # Try to load sprite, fall back to generated one
        self._load_or_generate_sprite()

    def _load_or_generate_sprite(self):
        """Load spawn point sprite or generate one"""
        sprite_path = "assets/objects/spawn_point.png"

        if os.path.exists(sprite_path):
            try:
                self.sprite = pygame.image.load(sprite_path).convert_alpha()
                # Resize to standard size if needed
                if self.sprite.get_size() != (self.width, self.height):
                    self.sprite = pygame.transform.scale(self.sprite, (self.width, self.height))
            except pygame.error:
                self._generate_sprite()
        else:
            self._generate_sprite()

    def _generate_sprite(self):
        """Generate a placeholder spawn point sprite"""
        self.sprite = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        # Draw concentric circles (like a spawn marker)
        center_x = self.width // 2
        center_y = self.height // 2

        # Outer glow
        pygame.gfxdraw.filled_circle(self.sprite, center_x, center_y, 15, (100, 255, 100, 150))

        # Main circle
        pygame.gfxdraw.filled_circle(self.sprite, center_x, center_y, 12, (100, 255, 100))
        pygame.gfxdraw.aacircle(self.sprite, center_x, center_y, 12, (80, 200, 80))

        # Inner circle
        pygame.gfxdraw.filled_circle(self.sprite, center_x, center_y, 8, (150, 255, 150))

        # Center dot
        pygame.gfxdraw.filled_circle(self.sprite, center_x, center_y, 3, (255, 255, 255))

        # Draw crosshair
        line_length = 6
        line_color = (255, 255, 255)

        # Horizontal line
        pygame.draw.line(self.sprite, line_color,
                         (center_x - line_length, center_y),
                         (center_x + line_length, center_y), 2)

        # Vertical line
        pygame.draw.line(self.sprite, line_color,
                         (center_x, center_y - line_length),
                         (center_x, center_y + line_length), 2)

    def to_dict(self):
        """Serialize spawn object for saving"""
        return {
            'type': 'spawn_point',
            'x': self.x,
            'y': self.y,
            'room': self.room_name
        }

    @staticmethod
    def from_dict(data: dict, room_name: str) -> 'SpawnObject':
        """Deserialize spawn object from save data"""
        return SpawnObject(
            data.get('x', 0),
            data.get('y', 0),
            room_name
        )


class SpawnObjectManager:
    """
    Manages spawn objects for all rooms
    Ensures only one spawn point per room
    """

    def __init__(self):
        # Dictionary mapping room names to spawn objects
        self.spawn_points = {}  # room_name -> SpawnObject

    def has_spawn_point(self, room_name: str) -> bool:
        """Check if a room already has a spawn point"""
        return room_name in self.spawn_points

    def get_spawn_point(self, room_name: str) -> Optional[SpawnObject]:
        """Get the spawn point for a room"""
        return self.spawn_points.get(room_name)

    def place_spawn_point(self, x: int, y: int, room_name: str) -> SpawnObject:
        """
        Place or move the spawn point in a room
        Replaces existing spawn point if present
        """
        spawn = SpawnObject(x, y, room_name)
        self.spawn_points[room_name] = spawn
        return spawn

    def remove_spawn_point(self, room_name: str):
        """Remove spawn point from a room"""
        if room_name in self.spawn_points:
            del self.spawn_points[room_name]

    def get_spawn_position(self, room_name: str) -> tuple:
        """Get spawn position for a room, returns (x, y) or (0, 0) if no spawn"""
        if room_name in self.spawn_points:
            spawn = self.spawn_points[room_name]
            return (spawn.x, spawn.y)
        return (0, 0)

    def save_to_dict(self) -> dict:
        """Save all spawn points to dictionary"""
        return {
            room_name: spawn.to_dict()
            for room_name, spawn in self.spawn_points.items()
        }

    def load_from_dict(self, data: dict):
        """Load spawn points from dictionary"""
        self.spawn_points = {}
        for room_name, spawn_data in data.items():
            self.spawn_points[room_name] = SpawnObject.from_dict(spawn_data, room_name)