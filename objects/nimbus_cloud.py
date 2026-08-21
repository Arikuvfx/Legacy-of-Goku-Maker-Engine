import pygame
from typing import List, Optional
from config.settings import RENDER_SCALE
from core.draw_layers import LayeredDrawMixin, DrawLayer
from objects.flying_pad import FlyingPadWaypoint


class NimbusCloudWaypoint(FlyingPadWaypoint):
    """
    A single waypoint in a nimbus cloud's path.

    Identical in shape to FlyingPadWaypoint (x/y, is_boundary, target_room,
    spawn_x/spawn_y) — subclassed rather than reused directly so nimbus save
    data and flying-pad save data stay independently typed even though the
    on-disk dict shape is the same.

    to_dict / from_dict are overridden so spawn_x/spawn_y always round-trip
    through JSON. The parent FlyingPadWaypoint serializers historically
    omitted them, which made every nimbus boundary reload with spawn == exit
    and broke reverse-path room transitions after a save/load cycle.
    """

    def to_dict(self) -> dict:
        return {
            'x': self.x,
            'y': self.y,
            'is_boundary': getattr(self, 'is_boundary', False),
            'target_room': getattr(self, 'target_room', None),
            'spawn_x': getattr(self, 'spawn_x', self.x),
            'spawn_y': getattr(self, 'spawn_y', self.y),
        }

    @staticmethod
    def from_dict(data: dict) -> 'NimbusCloudWaypoint':
        wp = NimbusCloudWaypoint(
            data.get('x', 0),
            data.get('y', 0),
            is_boundary=data.get('is_boundary', False),
        )
        wp.target_room = data.get('target_room', None)
        # Use explicit membership checks so spawn at (0, 0) is preserved.
        wp.spawn_x = data['spawn_x'] if 'spawn_x' in data else data.get('x', 0)
        wp.spawn_y = data['spawn_y'] if 'spawn_y' in data else data.get('y', 0)
        return wp


class NimbusCloud(LayeredDrawMixin):
    """
    Nimbus Cloud object — transports the player along a predefined path,
    same authoring model as FlyingPad (waypoints, boundary crossings,
    return clouds), but with two behavior differences handled by
    NimbusCloudController / NimbusCloudPathEditor rather than here:

      1. The PLAYER does not fly under its own animation — they walk onto
         the cloud, anchor to it in their idle pose, and the CLOUD is what
         actually moves along the path.
      2. The camera does not follow the cloud while it travels — it stays
         static per room-leg (see NimbusCloudController / the path editor).

    This class only owns the data model + rendering, exactly like FlyingPad.
    """

    def __init__(self, x: int, y: int, cloud_type: str = 'white'):
        LayeredDrawMixin.__init__(self, layer=DrawLayer.PLAYER, y_sort=False)
        self.x = x
        self.y = y
        # Authored "dock" position — kept separate from self.x/self.y so a
        # ride's live movement never clobbers where this cloud was placed.
        # NimbusCloudController restores self.x/self.y from these when a
        # same-room ride completes (see _complete_ride).
        self.origin_x = x
        self.origin_y = y
        self.width = 30
        self.height = 23
        self.cloud_type = cloud_type
        self.active = True

        # Where the player is anchored while riding, relative to (x, y).
        # Slightly above center so the player appears to stand ON the cloud
        # rather than in the middle of it.
        self.rider_offset_x = 0
        self.rider_offset_y = -14

        # Path configuration — same shape as FlyingPad.
        self.waypoints: List[NimbusCloudWaypoint] = []
        self.is_return_pad = False  # If True, this travels the reverse path.
        self.linked_pad_id = None   # ID of the cloud this returns to.
        self.source_room = ""       # Room where the path starts (for return clouds).

        # The current room this cloud is in; set by the game/room manager.
        # Changes every time a ride carries the cloud across a boundary.
        self.current_room = ""

        # The room this cloud was ORIGINALLY placed in, set once (see
        # NimbusCloudManager.add_cloud) and never overwritten afterward —
        # unlike current_room, this never changes as the cloud is ridden
        # around. A shuttle cloud (not is_return_pad) has no source_room of
        # its own, so this is what get_reversed_path() falls back to in
        # order to know which room a return ride should land in; without
        # it, that room name is not recoverable from the waypoints alone.
        self.origin_room = ""

        # The room-editor camera position that was on screen at the moment
        # this cloud was placed and its path editor opened — captured once,
        # never touched again (mirrors origin_room). NimbusCloudPathEditor
        # locks the very first leg's view to whatever's on screen at
        # placement time rather than a computed top-anchored frame (every
        # OTHER leg uses that computed frame instead — see
        # _snap_camera_to_top_anchor). Runtime playback needs this exact
        # value so a return ride back into this room recreates the same
        # frame the leg was authored against, instead of falling back to
        # the generic top-anchor formula that only applies to later legs.
        self.origin_camera_x = 0
        self.origin_camera_y = 0

        # True while a player is actively riding this cloud — prevents
        # re-triggering the interaction mid-ride.
        self.is_occupied = False

        # Sprite
        self.sprite = None
        self._load_sprite()

    def _load_sprite(self):
        """Load the nimbus cloud sprite.

        There's only one nimbus sprite on disk (no per-type variants), so
        this always loads the same file regardless of cloud_type.
        """
        try:
            sprite_path = 'assets/objects/nimbus/nimbus.png'
            self.sprite = pygame.image.load(sprite_path).convert_alpha()
            self.sprite = pygame.transform.scale(self.sprite, (self.width, self.height))
        except Exception:
            # Fallback placeholder — a simple fluffy cloud silhouette.
            self.sprite = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            cx, cy = self.width // 2, self.height // 2
            puff_color = (255, 240, 245)
            outline = (200, 150, 160)
            puffs = [
                (cx - 12, cy + 2, 10),
                (cx - 2,  cy - 4, 12),
                (cx + 10, cy + 2, 10),
                (cx,      cy + 6, 11),
            ]
            for px, py, r in puffs:
                pygame.draw.circle(self.sprite, puff_color, (px, py), r)
            for px, py, r in puffs:
                pygame.draw.circle(self.sprite, outline, (px, py), r, 1)

    def check_collision_with_player(self, player) -> bool:
        """Check if the player is standing near this cloud, close enough to board it."""
        distance = ((self.x - player.x) ** 2 + (self.y - player.y) ** 2) ** 0.5
        return distance < max(self.width, self.height) / 2 + 12

    def get_path_for_flight(self, current_room_name: str = "") -> List[NimbusCloudWaypoint]:
        """
        Get the waypoints for this ride (normal or reversed).

        Mirrors FlyingPad.get_path_for_flight exactly — return clouds
        traverse the recorded path backwards, swapping each boundary's
        spawn/exit points and target rooms accordingly.
        """
        if not self.is_return_pad or len(self.waypoints) == 0:
            return self.waypoints

        return self.get_reversed_path(current_room_name)

    def get_reversed_path(self, current_room_name: str = "") -> List[NimbusCloudWaypoint]:
        """
        Build a reversed copy of self.waypoints, correctly swapping each
        boundary's spawn/exit points AND target_room.

        Used by get_path_for_flight() for dedicated is_return_pad clouds,
        and directly by NimbusCloudController for a "shuttle" cloud (a
        single object ridden back and forth across a room boundary).

        The room this reversed path should ultimately lead back to comes
        from effective_source_room below — self.source_room when this is a
        dedicated return-pad cloud (set explicitly at creation), otherwise
        self.origin_room, the room this cloud was first placed in (set once
        by NimbusCloudManager.add_cloud and never touched again). Either
        way that's a room name recorded up front, not something derivable
        from the waypoints themselves: for a single-boundary path there is
        no way to recover "the room before the boundary" purely from
        current_room_name (the room we're currently sitting in, i.e. AFTER
        the boundary) plus the boundary's own forward target_room (also
        the room after it) — nothing in that data mentions the room before.
        """
        if len(self.waypoints) == 0:
            return self.waypoints

        effective_source_room = self.source_room or getattr(self, 'origin_room', '')

        room_sequence = []
        if effective_source_room:
            room_sequence.append(effective_source_room)
        elif current_room_name:
            # Legacy fallback for save data written before origin_room
            # existed (and lacking any source_room). Only reliable for
            # multi-boundary chains, where earlier boundaries' forward
            # target_room values double as later boundaries' "room before"
            # — for a single-boundary path this cannot determine the true
            # origin room and will incorrectly resolve back to
            # current_room_name itself.
            last_boundary_target = None
            for wp in self.waypoints:
                if wp.is_boundary and wp.target_room:
                    last_boundary_target = wp.target_room

            if last_boundary_target == current_room_name:
                forward_boundaries = [
                    (i, wp.target_room) for i, wp in enumerate(self.waypoints)
                    if wp.is_boundary and wp.target_room
                ]
                if len(forward_boundaries) == 1:
                    room_sequence = [current_room_name]
                else:
                    room_sequence = [current_room_name]
                    for idx, target in reversed(forward_boundaries):
                        if target not in room_sequence:
                            room_sequence.insert(0, target)
            else:
                room_sequence.append(current_room_name)
        else:
            room_sequence.append("UNKNOWN_ROOM")

        boundary_transitions = []
        for i, wp in enumerate(self.waypoints):
            if wp.is_boundary and wp.target_room:
                if len(room_sequence) > 0:
                    from_room = room_sequence[-1] if len(room_sequence) > len(boundary_transitions) else "UNKNOWN"
                    room_sequence.append(wp.target_room)
                    boundary_transitions.append((i, from_room, wp.target_room))

        current_room_index = len(room_sequence) - 1
        reversed_waypoints = []

        for i in range(len(self.waypoints) - 1, -1, -1):
            original_wp = self.waypoints[i]

            if original_wp.is_boundary:
                reversed_wp = NimbusCloudWaypoint(
                    original_wp.spawn_x,
                    original_wp.spawn_y,
                    is_boundary=True
                )
                reversed_wp.spawn_x = original_wp.x
                reversed_wp.spawn_y = original_wp.y

                if current_room_index > 0:
                    reversed_wp.target_room = room_sequence[current_room_index - 1]
                    current_room_index -= 1
                else:
                    if effective_source_room:
                        reversed_wp.target_room = effective_source_room
                    elif current_room_name:
                        reversed_wp.target_room = "UNKNOWN_SOURCE"
                    else:
                        reversed_wp.target_room = "ERROR_NO_SOURCE_ROOM"

                reversed_waypoints.append(reversed_wp)
            else:
                reversed_waypoints.append(NimbusCloudWaypoint(original_wp.x, original_wp.y, is_boundary=False))

        return reversed_waypoints

    def draw(self, screen: pygame.Surface, camera, colors=None, render_scale: int = None):
        """Draw the nimbus cloud."""
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
        """Draw the travel path in editor mode — same layout as FlyingPad's preview,
        with a cloud-appropriate color scheme so the two object types read distinctly
        on screen."""
        if len(self.waypoints) < 2:
            return

        path_to_draw = self.get_path_for_flight(self.current_room) if self.is_return_pad else self.waypoints

        for i in range(len(path_to_draw) - 1):
            wp1 = path_to_draw[i]
            wp2 = path_to_draw[i + 1]

            if wp1.is_boundary and (wp1.spawn_x != wp1.x or wp1.spawn_y != wp1.y):
                x1 = (wp1.spawn_x * render_scale) - camera.x
                y1 = (wp1.spawn_y * render_scale) - camera.y
            else:
                x1 = (wp1.x * render_scale) - camera.x
                y1 = (wp1.y * render_scale) - camera.y

            x2 = (wp2.x * render_scale) - camera.x
            y2 = (wp2.y * render_scale) - camera.y

            if self.is_return_pad:
                color = (255, 150, 220)  # Pink for return clouds.
            else:
                color = (255, 210, 120) if wp2.is_boundary else (170, 220, 255)
            pygame.draw.line(screen, color, (x1, y1), (x2, y2), 2)

            mid_x = (x1 + x2) // 2
            mid_y = (y1 + y2) // 2
            pygame.draw.circle(screen, color, (mid_x, mid_y), 4)

        for i, wp in enumerate(path_to_draw):
            x = (wp.x * render_scale) - camera.x
            y = (wp.y * render_scale) - camera.y

            if wp.is_boundary:
                boundary_color = (255, 150, 220) if self.is_return_pad else (255, 210, 120)
                pygame.draw.circle(screen, boundary_color, (int(x), int(y)), 8)
                pygame.draw.circle(screen, (0, 0, 0), (int(x), int(y)), 8, 2)

                if wp.target_room:
                    font = pygame.font.Font(None, 16)
                    text = font.render(wp.target_room, True, (255, 255, 255))
                    text_rect = text.get_rect(center=(x, y - 15))

                    bg_rect = text_rect.inflate(4, 2)
                    bg_surf = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
                    bg_surf.fill((0, 0, 0, 180))
                    screen.blit(bg_surf, bg_rect.topleft)
                    screen.blit(text, text_rect)

                if wp.spawn_x != wp.x or wp.spawn_y != wp.y:
                    spawn_x = (wp.spawn_x * render_scale) - camera.x
                    spawn_y = (wp.spawn_y * render_scale) - camera.y

                    spawn_color = (220, 150, 255) if self.is_return_pad else (150, 255, 220)
                    pygame.draw.circle(screen, spawn_color, (int(spawn_x), int(spawn_y)), 8)
                    pygame.draw.circle(screen, (0, 0, 0), (int(spawn_x), int(spawn_y)), 8, 2)

                    pygame.draw.line(screen, (180, 200, 180), (int(x), int(y)), (int(spawn_x), int(spawn_y)), 1)

                    font_small = pygame.font.Font(None, 16)
                    spawn_label = font_small.render("Spawn", True, (255, 255, 255))
                    spawn_rect = spawn_label.get_rect(center=(spawn_x, spawn_y + 15))

                    spawn_bg = spawn_rect.inflate(4, 2)
                    spawn_bg_surf = pygame.Surface((spawn_bg.width, spawn_bg.height), pygame.SRCALPHA)
                    spawn_bg_surf.fill((0, 0, 0, 180))
                    screen.blit(spawn_bg_surf, spawn_bg.topleft)
                    screen.blit(spawn_label, spawn_rect)
            else:
                wp_color = (220, 150, 255) if self.is_return_pad else (170, 220, 255)
                pygame.draw.circle(screen, wp_color, (int(x), int(y)), 6)
                pygame.draw.circle(screen, (0, 0, 0), (int(x), int(y)), 6, 2)

            font = pygame.font.Font(None, 14)
            num_text = font.render(str(i + 1), True, (255, 255, 255))
            num_rect = num_text.get_rect(center=(x, y))
            screen.blit(num_text, num_rect)

        font = pygame.font.Font(None, 18)
        if self.is_return_pad:
            type_text = f"RETURN → {self.source_room}" if self.source_room else "RETURN CLOUD (No source set!)"
        else:
            type_text = "NIMBUS CLOUD"

        text_color = (255, 150, 220) if self.is_return_pad else (170, 220, 255)
        type_label = font.render(type_text, True, text_color)

        cloud_screen_x = (self.x * render_scale) - camera.x
        cloud_screen_y = (self.y * render_scale) - camera.y - 30
        type_rect = type_label.get_rect(center=(cloud_screen_x, cloud_screen_y))

        bg_rect = type_rect.inflate(8, 4)
        bg_surf = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
        bg_surf.fill((0, 0, 0, 200))
        screen.blit(bg_surf, bg_rect.topleft)
        screen.blit(type_label, type_rect)

    def to_dict(self) -> dict:
        """Serialize for saving."""
        return {
            'type': 'nimbus_cloud',
            'x': self.x,
            'y': self.y,
            # Authored dock — must stay separate from live x/y. After a ride
            # to another room and a save, live x/y are in the destination;
            # without these fields, from_dict used to rewrite the dock to
            # those destination coordinates and break the return trip.
            'origin_x': self.origin_x,
            'origin_y': self.origin_y,
            'width': self.width,
            'height': self.height,
            'cloud_type': self.cloud_type,
            'rider_offset_x': self.rider_offset_x,
            'rider_offset_y': self.rider_offset_y,
            'waypoints': [wp.to_dict() for wp in self.waypoints],
            'is_return_pad': self.is_return_pad,
            'linked_pad_id': self.linked_pad_id,
            'source_room': self.source_room,
            'current_room': self.current_room,
            'origin_room': self.origin_room,
            'origin_camera_x': self.origin_camera_x,
            'origin_camera_y': self.origin_camera_y,
        }

    @staticmethod
    def from_dict(data: dict) -> 'NimbusCloud':
        """Deserialize from save data."""
        cloud = NimbusCloud(
            data.get('x', 0),
            data.get('y', 0),
            data.get('cloud_type', 'white')
        )
        cloud.width = data.get('width', 30)
        cloud.height = data.get('height', 23)
        cloud.origin_x = data.get('origin_x', data.get('x', 0))
        cloud.origin_y = data.get('origin_y', data.get('y', 0))
        cloud.rider_offset_x = data.get('rider_offset_x', 0)
        cloud.rider_offset_y = data.get('rider_offset_y', -14)
        cloud.waypoints = [NimbusCloudWaypoint.from_dict(wp) for wp in data.get('waypoints', [])]
        cloud.is_return_pad = data.get('is_return_pad', False)
        cloud.linked_pad_id = data.get('linked_pad_id')
        cloud.source_room = data.get('source_room', '')
        cloud.current_room = data.get('current_room', '')
        # origin_room is what get_reversed_path uses to target the return
        # trip. NEVER fall back to current_room when the path crosses a
        # boundary: after ride-to-destination + save, current_room is the
        # destination, and using it as origin made every post-reload return
        # trip target that same destination again (player stuck in room B).
        saved_origin = data.get('origin_room', '') or ''
        if saved_origin:
            cloud.origin_room = saved_origin
        elif cloud.source_room:
            cloud.origin_room = cloud.source_room
        else:
            has_boundary = any(
                getattr(wp, 'is_boundary', False) and getattr(wp, 'target_room', None)
                for wp in cloud.waypoints
            )
            if has_boundary:
                cloud.origin_room = ''
            else:
                cloud.origin_room = data.get('current_room', '')
        cloud.origin_camera_x = data.get('origin_camera_x', 0)
        cloud.origin_camera_y = data.get('origin_camera_y', 0)
        return cloud


class NimbusCloudManager:
    """Manages nimbus clouds for all rooms — mirrors FlyingPadManager exactly."""

    def __init__(self):
        self.nimbus_clouds = {}  # room_name -> List[NimbusCloud]

    def get_clouds(self, room_name: str) -> List[NimbusCloud]:
        """Get all nimbus clouds for a room."""
        return self.nimbus_clouds.get(room_name, [])

    def add_cloud(self, room_name: str, cloud: NimbusCloud):
        """Add a nimbus cloud to a room."""
        if room_name not in self.nimbus_clouds:
            self.nimbus_clouds[room_name] = []
        cloud.current_room = room_name
        # origin_room is set once, the first time this cloud is ever placed
        # anywhere, and never touched again — it has to survive every later
        # add_cloud call a cross-room ride triggers, or a shuttle cloud
        # would "forget" which room it's meant to return to. See the
        # attribute comment on NimbusCloud.origin_room.
        if not cloud.origin_room:
            cloud.origin_room = room_name
        self.nimbus_clouds[room_name].append(cloud)

    def remove_cloud(self, room_name: str, cloud: NimbusCloud):
        """Remove a nimbus cloud from a room."""
        if room_name in self.nimbus_clouds:
            if cloud in self.nimbus_clouds[room_name]:
                self.nimbus_clouds[room_name].remove(cloud)

    def clear_room(self, room_name: str):
        """Clear all nimbus clouds from a room."""
        if room_name in self.nimbus_clouds:
            self.nimbus_clouds[room_name] = []