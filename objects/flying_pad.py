import pygame
from typing import List, Tuple, Optional
from config.settings import RENDER_SCALE
from core.draw_layers import LayeredDrawMixin, DrawLayer


class FlyingPadWaypoint:
    """A single waypoint in a flying path"""

    def __init__(self, x: int, y: int, is_boundary: bool = False):
        self.x = x
        self.y = y
        self.is_boundary = is_boundary  # If True, this crosses to another room
        self.target_room = ""  # Room name if this is a boundary waypoint

        # Separate spawn point for boundary waypoints
        self.spawn_x = x  # Where player spawns in destination room
        self.spawn_y = y

    def to_dict(self):
        return {
            'x': self.x,
            'y': self.y,
            'is_boundary': self.is_boundary,
            'target_room': self.target_room,
            'spawn_x': self.spawn_x,
            'spawn_y': self.spawn_y
        }

    @staticmethod
    def from_dict(data):
        wp = FlyingPadWaypoint(data['x'], data['y'], data.get('is_boundary', False))
        wp.target_room = data.get('target_room', '')
        wp.spawn_x = data.get('spawn_x', data['x'])
        wp.spawn_y = data.get('spawn_y', data['y'])
        return wp


class FlyingPad(LayeredDrawMixin):
    """Flying pad object that transports player along a predefined path"""

    def __init__(self, x: int, y: int, pad_type: str = 'stone'):
        LayeredDrawMixin.__init__(self, layer=DrawLayer.PLAYER, y_sort=True)
        self.x = x
        self.y = y
        self.width = 32
        self.height = 32
        self.pad_type = pad_type
        self.active = True

        # Path configuration
        self.waypoints: List[FlyingPadWaypoint] = []
        self.is_return_pad = False  # If True, this flies the reverse path
        self.linked_pad_id = None  # ID of the pad this returns to
        self.source_room = ""  # Name of the room where the path starts (for return pads)

        # The current room this pad is in; set by the game/room manager
        self.current_room = ""

        # Sprite
        self.sprite = None
        self._load_sprite()

    def _load_sprite(self):
        """Load the flying pad sprite"""
        try:
            sprite_path = f'assets/objects/flying_pads/{self.pad_type}_flyingpad.png'
            self.sprite = pygame.image.load(sprite_path).convert_alpha()
            self.sprite = pygame.transform.scale(self.sprite, (self.width, self.height))
        except:
            # Fallback placeholder
            self.sprite = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            self.sprite.fill((100, 200, 255))
            pygame.draw.rect(self.sprite, (0, 0, 0), (0, 0, self.width, self.height), 2)
            # Draw arrow pattern
            center_x = self.width // 2
            center_y = self.height // 2
            points = [
                (center_x, center_y - 10),
                (center_x - 8, center_y + 5),
                (center_x + 8, center_y + 5)
            ]
            pygame.draw.polygon(self.sprite, (255, 255, 255), points)

    def check_collision_with_player(self, player) -> bool:
        """Check if player is standing on this pad"""
        distance = ((self.x - player.x) ** 2 + (self.y - player.y) ** 2) ** 0.5
        return distance < max(self.width, self.height) / 2

    def infer_source_room_from_context(self, current_room_name: str) -> str:
        """
        Infer the source room for a return pad based on current context.

        Logic:
        - If this is a return pad in room 'b'
        - And forward waypoints show a boundary going TO 'b'
        - Then the source room is whatever came before that boundary
        - We can infer: if we're in 'b', and boundary goes to 'b', then source is NOT 'b'

        For the forward path from room 'a' to room 'b':
          waypoints[1] = boundary with target_room='b'

        For the return path (reverse):
          We're in room 'b'
          We want to reverse back through the boundary
          The boundary should take us back to 'a'
        """
        if not self.is_return_pad or not self.waypoints:
            return ""

        # Find the first boundary waypoint in the FORWARD path
        # This tells us where the forward path WENT TO
        first_boundary_target = None
        for wp in self.waypoints:
            if wp.is_boundary and wp.target_room:
                first_boundary_target = wp.target_room
                break

        if first_boundary_target and current_room_name:
            # If we're already in the target room of the first boundary,
            # the source room cannot be determined from waypoints alone —
            # it must be set explicitly when the return pad is created.
            if current_room_name == first_boundary_target:
                pass  # Source room must be set by the caller

        return ""

    def get_path_for_flight(self, current_room_name: str = "") -> List[FlyingPadWaypoint]:
        """
        Get the waypoints for this flight (normal or reversed).

        For return pads, this creates a properly reversed path where:
        - Waypoints are traversed in reverse order
        - Each boundary's spawn point becomes the new boundary exit point
        - Each boundary's exit point becomes the new spawn point
        - Boundary target rooms are properly set to go backwards through rooms

        Args:
            current_room_name: The room this pad is currently in (used for inference)
        """
        if not self.is_return_pad or len(self.waypoints) == 0:
            return self.waypoints

        # Try to infer source room if not set
        effective_source_room = self.source_room
        if not effective_source_room and current_room_name:
            inferred = self.infer_source_room_from_context(current_room_name)
            if inferred:
                effective_source_room = inferred

        # Build the sequence of rooms in forward direction
        room_sequence = []

        if effective_source_room:
            room_sequence.append(effective_source_room)
        elif current_room_name:
            # Find what room the forward path ended in
            last_boundary_target = None
            for wp in self.waypoints:
                if wp.is_boundary and wp.target_room:
                    last_boundary_target = wp.target_room

            if last_boundary_target == current_room_name:
                # Collect all boundary transitions from forward path
                forward_boundaries = []
                for i, wp in enumerate(self.waypoints):
                    if wp.is_boundary and wp.target_room:
                        forward_boundaries.append((i, wp.target_room))

                if len(forward_boundaries) == 1:
                    # Single boundary: X -> current_room, but X is unknown without source_room
                    room_sequence = [current_room_name]
                else:
                    # Multiple boundaries — trace backwards
                    room_sequence = [current_room_name]
                    for idx, target in reversed(forward_boundaries):
                        if target not in room_sequence:
                            room_sequence.insert(0, target)
            else:
                room_sequence.append(current_room_name)
        else:
            room_sequence.append("UNKNOWN_ROOM")

        # Build complete room sequence by following boundaries in forward direction
        boundary_transitions = []
        for i, wp in enumerate(self.waypoints):
            if wp.is_boundary and wp.target_room:
                if len(room_sequence) > 0:
                    from_room = room_sequence[-1] if len(room_sequence) > len(boundary_transitions) else "UNKNOWN"
                    room_sequence.append(wp.target_room)
                    boundary_transitions.append((i, from_room, wp.target_room))

        # For reverse path, start from the last room
        current_room_index = len(room_sequence) - 1

        reversed_waypoints = []

        # Reverse through all waypoints
        for i in range(len(self.waypoints) - 1, -1, -1):
            original_wp = self.waypoints[i]

            if original_wp.is_boundary:
                reversed_wp = FlyingPadWaypoint(
                    original_wp.spawn_x,  # Spawn point becomes exit point
                    original_wp.spawn_y,
                    is_boundary=True
                )

                # Exit point becomes spawn point
                reversed_wp.spawn_x = original_wp.x
                reversed_wp.spawn_y = original_wp.y

                # Set target room to go backwards
                if current_room_index > 0:
                    reversed_wp.target_room = room_sequence[current_room_index - 1]
                    current_room_index -= 1
                else:
                    # Fallback — source room unclear
                    if effective_source_room:
                        reversed_wp.target_room = effective_source_room
                    elif current_room_name:
                        reversed_wp.target_room = "UNKNOWN_SOURCE"
                    else:
                        reversed_wp.target_room = "ERROR_NO_SOURCE_ROOM"

                reversed_waypoints.append(reversed_wp)
            else:
                # Regular waypoint
                reversed_wp = FlyingPadWaypoint(original_wp.x, original_wp.y, is_boundary=False)
                reversed_waypoints.append(reversed_wp)

        return reversed_waypoints

    def draw(self, screen: pygame.Surface, camera, colors=None, render_scale: int = None):
        """Draw the flying pad"""
        if not self.active:
            return
        if render_scale is None:
            render_scale = RENDER_SCALE

        screen_x = (self.x * render_scale) - camera.x
        screen_y = (self.y * render_scale) - camera.y

        if self.sprite:
            scaled_width = self.width * render_scale
            scaled_height = self.height * render_scale
            scaled_sprite = pygame.transform.scale(self.sprite, (scaled_width, scaled_height))

            sprite_x = int(screen_x - scaled_width // 2)
            sprite_y = int(screen_y - scaled_height // 2)
            screen.blit(scaled_sprite, (sprite_x, sprite_y))

    def draw_path_preview(self, screen: pygame.Surface, camera, render_scale: int = 2):
        """Draw the flight path in editor mode"""
        if len(self.waypoints) < 2:
            return

        # If this is a return pad, show the reversed path
        path_to_draw = self.get_path_for_flight(self.current_room) if self.is_return_pad else self.waypoints

        # Draw lines between waypoints
        for i in range(len(path_to_draw) - 1):
            wp1 = path_to_draw[i]
            wp2 = path_to_draw[i + 1]

            # Determine the start point of the line
            if wp1.is_boundary and (wp1.spawn_x != wp1.x or wp1.spawn_y != wp1.y):
                x1 = (wp1.spawn_x * render_scale) - camera.x
                y1 = (wp1.spawn_y * render_scale) - camera.y
            else:
                x1 = (wp1.x * render_scale) - camera.x
                y1 = (wp1.y * render_scale) - camera.y

            x2 = (wp2.x * render_scale) - camera.x
            y2 = (wp2.y * render_scale) - camera.y

            # Draw line
            if self.is_return_pad:
                color = (255, 100, 255)  # Purple for return pads
            else:
                color = (255, 200, 0) if wp2.is_boundary else (100, 200, 255)
            pygame.draw.line(screen, color, (x1, y1), (x2, y2), 2)

            # Draw arrow at midpoint
            mid_x = (x1 + x2) // 2
            mid_y = (y1 + y2) // 2
            pygame.draw.circle(screen, color, (mid_x, mid_y), 4)

        # Draw waypoint markers
        for i, wp in enumerate(path_to_draw):
            x = (wp.x * render_scale) - camera.x
            y = (wp.y * render_scale) - camera.y

            if wp.is_boundary:
                boundary_color = (255, 100, 255) if self.is_return_pad else (255, 200, 0)
                pygame.draw.circle(screen, boundary_color, (int(x), int(y)), 8)
                pygame.draw.circle(screen, (0, 0, 0), (int(x), int(y)), 8, 2)

                # Draw room name if set
                if wp.target_room:
                    font = pygame.font.Font(None, 16)
                    text = font.render(wp.target_room, True, (255, 255, 255))
                    text_rect = text.get_rect(center=(x, y - 15))

                    bg_rect = text_rect.inflate(4, 2)
                    bg_surf = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
                    bg_surf.fill((0, 0, 0, 180))
                    screen.blit(bg_surf, bg_rect.topleft)
                    screen.blit(text, text_rect)

                # Draw spawn point indicator
                if wp.spawn_x != wp.x or wp.spawn_y != wp.y:
                    spawn_x = (wp.spawn_x * render_scale) - camera.x
                    spawn_y = (wp.spawn_y * render_scale) - camera.y

                    spawn_color = (200, 100, 255) if self.is_return_pad else (100, 255, 100)
                    pygame.draw.circle(screen, spawn_color, (int(spawn_x), int(spawn_y)), 8)
                    pygame.draw.circle(screen, (0, 0, 0), (int(spawn_x), int(spawn_y)), 8, 2)

                    pygame.draw.line(screen, (150, 200, 150), (int(x), int(y)), (int(spawn_x), int(spawn_y)), 1)

                    font_small = pygame.font.Font(None, 16)
                    spawn_label = font_small.render("Spawn", True, (255, 255, 255))
                    spawn_rect = spawn_label.get_rect(center=(spawn_x, spawn_y + 15))

                    spawn_bg = spawn_rect.inflate(4, 2)
                    spawn_bg_surf = pygame.Surface((spawn_bg.width, spawn_bg.height), pygame.SRCALPHA)
                    spawn_bg_surf.fill((0, 0, 0, 180))
                    screen.blit(spawn_bg_surf, spawn_bg.topleft)
                    screen.blit(spawn_label, spawn_rect)
            else:
                wp_color = (200, 100, 255) if self.is_return_pad else (100, 200, 255)
                pygame.draw.circle(screen, wp_color, (int(x), int(y)), 6)
                pygame.draw.circle(screen, (0, 0, 0), (int(x), int(y)), 6, 2)

            # Draw waypoint number
            font = pygame.font.Font(None, 14)
            num_text = font.render(str(i + 1), True, (255, 255, 255))
            num_rect = num_text.get_rect(center=(x, y))
            screen.blit(num_text, num_rect)

        # Draw pad type indicator
        font = pygame.font.Font(None, 18)
        if self.is_return_pad:
            if self.source_room:
                pad_type_text = f"RETURN → {self.source_room}"
            else:
                pad_type_text = "RETURN PAD (No source set!)"
        else:
            pad_type_text = "FLYING PAD"

        text_color = (255, 100, 255) if self.is_return_pad else (100, 200, 255)
        type_label = font.render(pad_type_text, True, text_color)

        pad_screen_x = (self.x * render_scale) - camera.x
        pad_screen_y = (self.y * render_scale) - camera.y - 30
        type_rect = type_label.get_rect(center=(pad_screen_x, pad_screen_y))

        bg_rect = type_rect.inflate(8, 4)
        bg_surf = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
        bg_surf.fill((0, 0, 0, 200))
        screen.blit(bg_surf, bg_rect.topleft)
        screen.blit(type_label, type_rect)

    def to_dict(self) -> dict:
        """Serialize for saving"""
        return {
            'type': 'flying_pad',
            'x': self.x,
            'y': self.y,
            'width': self.width,
            'height': self.height,
            'pad_type': self.pad_type,
            'waypoints': [wp.to_dict() for wp in self.waypoints],
            'is_return_pad': self.is_return_pad,
            'linked_pad_id': self.linked_pad_id,
            'source_room': self.source_room,
            'current_room': self.current_room
        }

    @staticmethod
    def from_dict(data: dict) -> 'FlyingPad':
        """Deserialize from save data"""
        pad = FlyingPad(
            data.get('x', 0),
            data.get('y', 0),
            data.get('pad_type', 'stone')
        )
        pad.width = data.get('width', 32)
        pad.height = data.get('height', 32)
        pad.waypoints = [FlyingPadWaypoint.from_dict(wp) for wp in data.get('waypoints', [])]
        pad.is_return_pad = data.get('is_return_pad', False)
        pad.linked_pad_id = data.get('linked_pad_id')
        pad.source_room = data.get('source_room', '')
        pad.current_room = data.get('current_room', '')
        return pad


class FlyingPadManager:
    """Manages flying pads for all rooms"""

    def __init__(self):
        self.flying_pads = {}  # room_name -> List[FlyingPad]

    def get_pads(self, room_name: str) -> List[FlyingPad]:
        """Get all flying pads for a room"""
        return self.flying_pads.get(room_name, [])

    def add_pad(self, room_name: str, pad: FlyingPad):
        """Add a flying pad to a room"""
        if room_name not in self.flying_pads:
            self.flying_pads[room_name] = []
        # Set the current_room field when adding
        pad.current_room = room_name
        self.flying_pads[room_name].append(pad)

    def remove_pad(self, room_name: str, pad: FlyingPad):
        """Remove a flying pad from a room"""
        if room_name in self.flying_pads:
            if pad in self.flying_pads[room_name]:
                self.flying_pads[room_name].remove(pad)

    def clear_room(self, room_name: str):
        """Clear all flying pads from a room"""
        if room_name in self.flying_pads:
            self.flying_pads[room_name] = []