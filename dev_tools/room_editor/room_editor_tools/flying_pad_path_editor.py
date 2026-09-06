import pygame
from typing import Optional, List, Tuple
from objects.flying_pad import FlyingPad, FlyingPadWaypoint


class FlyingPadPathEditor:
    """Editor for configuring flying pad waypoint paths"""

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False
        self.confirm_button_rect = None

        # Current editing state
        self.flying_pad: Optional[FlyingPad] = None
        self.waypoints: List[FlyingPadWaypoint] = []
        self.current_room_name = ""
        self.available_rooms = []

        # CRITICAL: Store room dimensions for boundary detection
        self.room_width = 0
        self.room_height = 0

        # NEW: Store the initial room where the pad was placed
        self.initial_room_name = ""

        # Track which waypoint index starts the current room segment
        self.current_room_segment_start = 0

        # UI state
        self.editing_mode = 'placing'  # 'placing', 'boundary_config', 'spawn_placement', 'complete'
        self.selected_boundary_room = ""
        self.hover_waypoint_index = -1
        self.pending_boundary_waypoint = None  # Waypoint waiting for spawn placement

        # Mouse preview state
        self.preview_mouse_x = 0
        self.preview_mouse_y = 0

        # Toolbar reference for hiding/showing
        self.toolbar = None
        self.toolbar_was_visible = False

        # Fonts
        self.font_large = pygame.font.Font(None, 32)
        self.font_medium = pygame.font.Font(None, 24)
        self.font_small = pygame.font.Font(None, 18)

        # Colors
        self.colors = {
            'bg': (20, 20, 30, 240),
            'panel': (35, 35, 55),
            'accent': (100, 200, 255),
            'waypoint': (100, 200, 255),
            'boundary': (255, 200, 0),
            'preview': (150, 150, 255, 180),  # Semi-transparent preview color
            'preview_boundary': (255, 230, 100, 180),  # Semi-transparent boundary preview
            'text': (255, 255, 255),
            'text_dim': (180, 180, 200),
        }

        # Room dropdown state
        self.dropdown_open = False
        self.dropdown_rect = None
        self.room_item_rects = []
        self.dropdown_menu_rect = None   # screen rect of the open room list (for scroll hit-testing)
        self.dropdown_scroll_offset = 0  # index of first visible room in the list
        self.dropdown_visible_rows = 6   # how many rows are shown at once

        # Return pad checkbox
        self.create_return_pad = False
        self.return_pad_checkbox_rect = None

    def set_toolbar(self, toolbar):
        """Set reference to the editor toolbar for hiding/showing"""
        self.toolbar = toolbar

    def open(self, flying_pad: FlyingPad, room_name: str, available_rooms: List[str],
             room_width: int = 2400, room_height: int = 1800):
        """Open the path editor for a flying pad"""
        self.active = True
        self.flying_pad = flying_pad
        self.waypoints = flying_pad.waypoints.copy() if flying_pad.waypoints else []
        self.current_room_name = room_name
        self.available_rooms = [r for r in available_rooms if r != room_name]
        self.editing_mode = 'placing'
        self.selected_boundary_room = ""
        self.dropdown_open = False
        self.dropdown_scroll_offset = 0

        # Reset room segment tracking
        self.current_room_segment_start = 0
        self.pending_boundary_waypoint = None

        # Reset return pad checkbox
        self.create_return_pad = False

        # Store room dimensions
        self.room_width = room_width
        self.room_height = room_height

        # NEW: Store the initial room name for returning later
        self.initial_room_name = room_name

        # Hide toolbar if it exists
        if self.toolbar:
            self.toolbar_was_visible = self.toolbar.visible
            self.toolbar.visible = False

    def close(self):
        """Close the editor"""
        self.active = False
        self.flying_pad = None
        self.waypoints = []
        self.dropdown_open = False
        self.initial_room_name = ""

        # Reset room segment tracking
        self.current_room_segment_start = 0
        self.pending_boundary_waypoint = None

        # Restore toolbar visibility
        if self.toolbar and self.toolbar_was_visible:
            self.toolbar.visible = True
            self.toolbar_was_visible = False

    def _get_current_room_waypoints(self) -> List[FlyingPadWaypoint]:
        """Get only the waypoints that belong to the current room segment"""
        # Return waypoints from current_room_segment_start onwards
        if self.current_room_segment_start >= len(self.waypoints):
            return []
        return self.waypoints[self.current_room_segment_start:]

    def _get_spawn_point_for_current_room(self) -> Optional[Tuple[int, int]]:
        """Get the spawn point coordinates if we're in a room after a transition"""
        if self.current_room_segment_start > 0:
            # We've transitioned to a new room, find the boundary waypoint before this segment
            boundary_index = self.current_room_segment_start - 1
            if 0 <= boundary_index < len(self.waypoints):
                boundary_wp = self.waypoints[boundary_index]
                if boundary_wp.is_boundary:
                    return (boundary_wp.spawn_x, boundary_wp.spawn_y)
        return None

    def _clamp_to_room_bounds(self, x: int, y: int) -> Tuple[int, int]:
        """Clamp coordinates to stay within room bounds"""
        clamped_x = max(0, min(x, self.room_width))
        clamped_y = max(0, min(y, self.room_height))
        return clamped_x, clamped_y

    def _is_at_room_boundary(self, x: int, y: int) -> bool:
        """Whether (x, y) is close enough to a room edge to trigger a
        room-boundary waypoint. Pulled out into its own method (instead of
        being inlined at each call site) so subclasses with a different
        notion of "edge" — e.g. NimbusCloudPathEditor, whose camera is
        locked and so can only ever reach a boundary that's actually
        visible — can override it in one place.
        """
        boundary_threshold = 10
        return (
                x <= boundary_threshold or
                x >= self.room_width - boundary_threshold or
                y <= boundary_threshold or
                y >= self.room_height - boundary_threshold
        )

    def handle_input(self, event, mouse_world_x: int, mouse_world_y: int,
                     world_width: int, world_height: int) -> Optional[str]:
        """
        Handle input events
        Returns: 'save:room_name', 'cancel', 'transition:room_name', or None
        """
        if not self.active:
            return None

        mouse_pos = pygame.mouse.get_pos()

        # Handle boundary room configuration mode
        if self.editing_mode == 'boundary_config':
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                # Check room dropdown
                if self.dropdown_rect and self.dropdown_rect.collidepoint(mouse_pos):
                    self.dropdown_open = not self.dropdown_open
                    if self.dropdown_open:
                        self.dropdown_scroll_offset = 0
                    return None

                # Check dropdown items (room_item_rects holds only the
                # currently-visible rows, so map back to the real index
                # using the scroll offset)
                if self.dropdown_open:
                    for row, rect in enumerate(self.room_item_rects):
                        if rect.collidepoint(mouse_pos):
                            i = self.dropdown_scroll_offset + row
                            if 0 <= i < len(self.available_rooms):
                                self.selected_boundary_room = self.available_rooms[i]
                                self.dropdown_open = False
                            return None

                # Check confirm button
                if self.confirm_button_rect and self.confirm_button_rect.collidepoint(mouse_pos):
                    # Store the pending boundary waypoint and target room
                    if self.waypoints:
                        last_wp = self.waypoints[-1]
                        last_wp.is_boundary = True
                        last_wp.target_room = self.selected_boundary_room
                        self.pending_boundary_waypoint = last_wp

                    # Store the target room to transition to
                    target_room = self.selected_boundary_room

                    # Enter spawn placement mode (will transition to room first)
                    self.editing_mode = 'spawn_placement'

                    # Return transition command to switch rooms
                    return f'transition:{target_room}'

            elif event.type == pygame.MOUSEWHEEL:
                if self.dropdown_open and self.dropdown_menu_rect and \
                        self.dropdown_menu_rect.collidepoint(mouse_pos):
                    max_offset = max(0, len(self.available_rooms) - self.dropdown_visible_rows)
                    # event.y > 0 is scroll up, < 0 is scroll down
                    self.dropdown_scroll_offset -= event.y
                    self.dropdown_scroll_offset = max(0, min(self.dropdown_scroll_offset, max_offset))
                    return None

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    # Cancel boundary placement
                    if self.waypoints:
                        self.waypoints.pop()
                    self.editing_mode = 'placing'
                    self.selected_boundary_room = ""

            return None

        # Handle spawn point placement mode (after room transition)
        if self.editing_mode == 'spawn_placement':
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                # CLAMP mouse position to room bounds
                clamped_x, clamped_y = self._clamp_to_room_bounds(mouse_world_x, mouse_world_y)

                # Check if clicking at ROOM boundary (using clamped coordinates)
                at_boundary = self._is_at_room_boundary(clamped_x, clamped_y)

                if at_boundary:
                    # Set spawn point on the pending boundary waypoint
                    if self.pending_boundary_waypoint:
                        self.pending_boundary_waypoint.spawn_x = clamped_x
                        self.pending_boundary_waypoint.spawn_y = clamped_y

                    # Mark that the next waypoint will be the start of this room's segment
                    self.current_room_segment_start = len(self.waypoints)

                    # Clear state and return to normal placement
                    self.editing_mode = 'placing'
                    self.selected_boundary_room = ""
                    self.pending_boundary_waypoint = None
                    return None
                else:
                    # Not at boundary - show feedback (handled in draw)
                    return None

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    # Cancel spawn placement - remove boundary waypoint
                    if self.waypoints and self.pending_boundary_waypoint:
                        self.waypoints.remove(self.pending_boundary_waypoint)
                    self.editing_mode = 'placing'
                    self.selected_boundary_room = ""
                    self.pending_boundary_waypoint = None
                    # Return to previous room
                    return f'transition:{self.initial_room_name}'

            return None

        # Normal waypoint placement mode
        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1:  # Left click
                # Check if clicking on return pad checkbox
                mouse_pos = pygame.mouse.get_pos()
                if self.return_pad_checkbox_rect and self.return_pad_checkbox_rect.collidepoint(mouse_pos):
                    self.create_return_pad = not self.create_return_pad
                    return None

                # Otherwise, place waypoint
                # CLAMP mouse position to room bounds
                clamped_x, clamped_y = self._clamp_to_room_bounds(mouse_world_x, mouse_world_y)

                # Check if clicking at ROOM boundary (using clamped coordinates)
                at_boundary = self._is_at_room_boundary(clamped_x, clamped_y)

                if at_boundary and len(self.available_rooms) > 0:
                    # Add boundary waypoint and enter config mode
                    wp = FlyingPadWaypoint(clamped_x, clamped_y, is_boundary=True)
                    self.waypoints.append(wp)
                    self.editing_mode = 'boundary_config'
                else:
                    # Add normal waypoint
                    wp = FlyingPadWaypoint(clamped_x, clamped_y, is_boundary=False)
                    self.waypoints.append(wp)

            elif event.button == 3:  # Right click - finish and save
                if len(self.waypoints) >= 1:
                    # Save waypoints to flying pad
                    if self.flying_pad:
                        self.flying_pad.waypoints = self.waypoints

                    # Return save command WITH room name and return pad flag
                    return_room = self.initial_room_name
                    create_return = self.create_return_pad
                    self.close()
                    return f'save:{return_room}:{"return_pad" if create_return else "no_return"}'

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                # NEW: Also return to initial room on cancel
                return_room = self.initial_room_name
                self.close()
                return f'cancel:{return_room}'

            elif event.key == pygame.K_BACKSPACE or event.key == pygame.K_DELETE:
                # Remove last waypoint
                if self.waypoints:
                    self.waypoints.pop()

        return None

    def update(self, dt: float, mouse_world_x: int, mouse_world_y: int):
        """Update editor state"""
        if not self.active:
            return

        # Clamp mouse position for hover detection and preview
        clamped_x, clamped_y = self._clamp_to_room_bounds(mouse_world_x, mouse_world_y)

        # Store for preview drawing
        self.preview_mouse_x = clamped_x
        self.preview_mouse_y = clamped_y

        # Update hover detection for waypoints (using clamped coordinates)
        self.hover_waypoint_index = -1
        for i, wp in enumerate(self.waypoints):
            dist = ((wp.x - clamped_x) ** 2 + (wp.y - clamped_y) ** 2) ** 0.5
            if dist < 10:
                self.hover_waypoint_index = i
                break

    def draw(self, screen: pygame.Surface, camera, render_scale: int = 2):
        """Draw the path editor UI"""
        if not self.active:
            return

        # Draw current room's path only
        self._draw_current_room_path(screen, camera, render_scale)

        # Draw preview of next waypoint (only in placement mode)
        if self.editing_mode == 'placing':
            self._draw_next_waypoint_preview(screen, camera, render_scale)
        elif self.editing_mode == 'spawn_placement':
            self._draw_spawn_point_preview(screen, camera, render_scale)

        # Draw instruction overlay
        if self.editing_mode == 'placing':
            self._draw_placement_instructions(screen)
        elif self.editing_mode == 'boundary_config':
            self._draw_boundary_config(screen)
        elif self.editing_mode == 'spawn_placement':
            self._draw_spawn_placement_instructions(screen)

    def _draw_current_room_path(self, screen: pygame.Surface, camera, render_scale: int):
        """Draw the path for the current room only"""
        current_room_waypoints = self._get_current_room_waypoints()

        if len(current_room_waypoints) == 0:
            # No waypoints in current room yet
            # But if we have a spawn point, draw it
            spawn_point = self._get_spawn_point_for_current_room()
            if spawn_point:
                x = (spawn_point[0] * render_scale) - camera.x
                y = (spawn_point[1] * render_scale) - camera.y

                # Draw spawn marker
                screen.draw_circle((100, 255, 100), (int(x), int(y)), 8)
                screen.draw_circle((0, 0, 0), (int(x), int(y)), 8, 2)

                # Label
                font = pygame.font.Font(None, 16)
                text = font.render("Spawn", True, (255, 255, 255))
                text_rect = text.get_rect(center=(x, y + 15))

                bg_rect = text_rect.inflate(4, 2)
                bg_surf = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
                bg_surf.fill((0, 0, 0, 180))
                screen.blit(bg_surf, bg_rect.topleft)
                screen.blit(text, text_rect)
            return

        # Draw lines between waypoints in current room
        # If we have a spawn point, draw line from spawn to first waypoint
        spawn_point = self._get_spawn_point_for_current_room()

        if spawn_point and len(current_room_waypoints) > 0:
            wp1_x = (spawn_point[0] * render_scale) - camera.x
            wp1_y = (spawn_point[1] * render_scale) - camera.y

            wp2 = current_room_waypoints[0]
            wp2_x = (wp2.x * render_scale) - camera.x
            wp2_y = (wp2.y * render_scale) - camera.y

            # Draw line from spawn to first waypoint
            color = (100, 200, 255)
            screen.draw_line(color, (wp1_x, wp1_y), (wp2_x, wp2_y), 2)

            # Draw arrow at midpoint
            mid_x = (wp1_x + wp2_x) // 2
            mid_y = (wp1_y + wp2_y) // 2
            screen.draw_circle(color, (mid_x, mid_y), 4)

            # Draw spawn point
            screen.draw_circle((100, 255, 100), (int(wp1_x), int(wp1_y)), 8)
            screen.draw_circle((0, 0, 0), (int(wp1_x), int(wp1_y)), 8, 2)

            font = pygame.font.Font(None, 16)
            text = font.render("Spawn", True, (255, 255, 255))
            text_rect = text.get_rect(center=(wp1_x, wp1_y + 15))

            bg_rect = text_rect.inflate(4, 2)
            bg_surf = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
            bg_surf.fill((0, 0, 0, 180))
            screen.blit(bg_surf, bg_rect.topleft)
            screen.blit(text, text_rect)

        # Draw lines between waypoints
        for i in range(len(current_room_waypoints) - 1):
            wp1 = current_room_waypoints[i]
            wp2 = current_room_waypoints[i + 1]

            # If wp1 is a boundary with a different spawn point, start from spawn
            if wp1.is_boundary and (wp1.spawn_x != wp1.x or wp1.spawn_y != wp1.y):
                x1 = (wp1.spawn_x * render_scale) - camera.x
                y1 = (wp1.spawn_y * render_scale) - camera.y
            else:
                x1 = (wp1.x * render_scale) - camera.x
                y1 = (wp1.y * render_scale) - camera.y

            x2 = (wp2.x * render_scale) - camera.x
            y2 = (wp2.y * render_scale) - camera.y

            # Draw line
            color = (255, 200, 0) if wp2.is_boundary else (100, 200, 255)
            screen.draw_line(color, (x1, y1), (x2, y2), 2)

            # Draw arrow at midpoint
            mid_x = (x1 + x2) // 2
            mid_y = (y1 + y2) // 2
            screen.draw_circle(color, (mid_x, mid_y), 4)

        # Draw waypoint markers
        for i, wp in enumerate(current_room_waypoints):
            x = (wp.x * render_scale) - camera.x
            y = (wp.y * render_scale) - camera.y

            if wp.is_boundary:
                # Boundary waypoint
                screen.draw_circle((255, 200, 0), (int(x), int(y)), 8)
                screen.draw_circle((0, 0, 0), (int(x), int(y)), 8, 2)

                if wp.target_room:
                    font = pygame.font.Font(None, 16)
                    text = font.render(wp.target_room, True, (255, 255, 255))
                    text_rect = text.get_rect(center=(x, y - 15))

                    bg_rect = text_rect.inflate(4, 2)
                    bg_surf = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
                    bg_surf.fill((0, 0, 0, 180))
                    screen.blit(bg_surf, bg_rect.topleft)
                    screen.blit(text, text_rect)
            else:
                # Normal waypoint
                screen.draw_circle((100, 200, 255), (int(x), int(y)), 6)
                screen.draw_circle((0, 0, 0), (int(x), int(y)), 6, 2)

            # Draw waypoint number (relative to current room)
            font = pygame.font.Font(None, 14)
            num_text = font.render(str(i + 1), True, (255, 255, 255))
            num_rect = num_text.get_rect(center=(x, y))
            screen.blit(num_text, num_rect)

    def _draw_next_waypoint_preview(self, screen: pygame.Surface, camera, render_scale: int):
        """Draw a preview of where the next waypoint will be placed"""
        current_room_waypoints = self._get_current_room_waypoints()
        spawn_point = self._get_spawn_point_for_current_room()

        # Determine the starting point for the preview line
        if len(current_room_waypoints) == 0:
            # No waypoints in current room yet
            if spawn_point:
                # Draw from spawn point to mouse
                last_x = (spawn_point[0] * render_scale) - camera.x
                last_y = (spawn_point[1] * render_scale) - camera.y
            else:
                # Very first waypoint - just draw preview at mouse
                preview_x = (self.preview_mouse_x * render_scale) - camera.x
                preview_y = (self.preview_mouse_y * render_scale) - camera.y

                # Check if at boundary
                at_boundary = self._is_at_room_boundary(self.preview_mouse_x, self.preview_mouse_y)

                # Draw preview waypoint
                if at_boundary and len(self.available_rooms) > 0:
                    # Boundary preview
                    screen.draw_circle(self.colors['preview_boundary'],
                                       (int(preview_x), int(preview_y)), 8)
                    screen.draw_circle((255, 255, 255),
                                       (int(preview_x), int(preview_y)), 8, 2)
                else:
                    # Normal preview
                    screen.draw_circle(self.colors['preview'],
                                       (int(preview_x), int(preview_y)), 6)
                    screen.draw_circle((255, 255, 255),
                                       (int(preview_x), int(preview_y)), 6, 2)
                return
        else:
            # Get last waypoint in current room
            last_wp = current_room_waypoints[-1]

            # If the last waypoint is a boundary with a different spawn point,
            # the preview should start from the spawn point
            if last_wp.is_boundary and (last_wp.spawn_x != last_wp.x or last_wp.spawn_y != last_wp.y):
                last_x = (last_wp.spawn_x * render_scale) - camera.x
                last_y = (last_wp.spawn_y * render_scale) - camera.y
            else:
                last_x = (last_wp.x * render_scale) - camera.x
                last_y = (last_wp.y * render_scale) - camera.y

        # Current mouse position (screen coordinates)
        preview_x = (self.preview_mouse_x * render_scale) - camera.x
        preview_y = (self.preview_mouse_y * render_scale) - camera.y

        # Check if at boundary
        at_boundary = self._is_at_room_boundary(self.preview_mouse_x, self.preview_mouse_y)

        # Choose color based on boundary status
        if at_boundary and len(self.available_rooms) > 0:
            line_color = self.colors['preview_boundary']
            circle_color = self.colors['preview_boundary']
            circle_size = 8
        else:
            line_color = self.colors['preview']
            circle_color = self.colors['preview']
            circle_size = 6

        # Draw preview line from last position to mouse
        # Create a surface with per-pixel alpha for the line
        line_surface = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        pygame.draw.line(line_surface, line_color,
                         (int(last_x), int(last_y)),
                         (int(preview_x), int(preview_y)), 2)
        screen.blit(line_surface, (0, 0))

        # Draw preview waypoint at mouse position
        circle_surface = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        pygame.draw.circle(circle_surface, circle_color,
                           (int(preview_x), int(preview_y)), circle_size)
        pygame.draw.circle(circle_surface, (255, 255, 255, 200),
                           (int(preview_x), int(preview_y)), circle_size, 2)
        screen.blit(circle_surface, (0, 0))

        # Draw preview waypoint number (relative to current room)
        font = pygame.font.Font(None, 14)
        num_text = font.render(str(len(current_room_waypoints) + 1), True, (255, 255, 255))
        num_rect = num_text.get_rect(center=(preview_x, preview_y))
        screen.blit(num_text, num_rect)

    def _draw_placement_instructions(self, screen: pygame.Surface):
        """Draw instructions for waypoint placement"""
        # This panel is built at literal real-window size and blitted at a
        # literal (0, 0)-relative position -- exactly the pattern that used
        # to force Map Paint's zoom-lock (see room_editor.py's
        # _zoom_locked() docstring). Unwrap back to the real screen so this
        # HUD overlay stays fixed-size/fixed-position regardless of the
        # continuous editor zoom that _draw_current_room_path() above
        # (correctly) does respect.
        screen = getattr(screen, '_screen', screen)
        # Semi-transparent panel at top
        panel_height = 180  # Increased to show boundary warning
        panel = pygame.Surface((self.screen_width, panel_height), pygame.SRCALPHA)
        panel.fill((20, 20, 30, 230))
        screen.blit(panel, (0, 0))

        # Title
        title = self.font_large.render("Flying Pad Path Editor", True, self.colors['accent'])
        screen.blit(title, (20, 10))

        # Instructions
        instructions = [
            "Left Click: Add waypoint",
            f"Left Click at room edge: Add room transition point",
            "Right Click: Finish path and return",
            "Backspace: Remove last waypoint",
            "ESC: Cancel and return"
        ]

        y = 50
        for inst in instructions:
            text = self.font_small.render(inst, True, self.colors['text_dim'])
            screen.blit(text, (20, y))
            y += 20

        # Important warning
        warning = self.font_small.render(
            "Mouse is clamped to room bounds - waypoints cannot be placed outside",
            True,
            self.colors['boundary']
        )
        screen.blit(warning, (20, y + 5))

        # Waypoint counter
        counter = self.font_medium.render(
            f"Waypoints: {len(self.waypoints)}",
            True,
            self.colors['accent']
        )
        screen.blit(counter, (self.screen_width - 200, 10))

        # Current room info
        room_info = self.font_small.render(
            f"Current: {self.current_room_name} ({self.room_width}x{self.room_height})",
            True,
            self.colors['text_dim']
        )
        screen.blit(room_info, (self.screen_width - 400, 40))

        # NEW: Show initial room (where we'll return to)
        initial_room_info = self.font_small.render(
            f"Starting room: {self.initial_room_name}",
            True,
            self.colors['waypoint']
        )
        screen.blit(initial_room_info, (self.screen_width - 400, 60))

        # Return pad checkbox
        checkbox_x = self.screen_width - 400
        checkbox_y = 85
        checkbox_size = 20

        # Create checkbox rect for click detection
        self.return_pad_checkbox_rect = pygame.Rect(checkbox_x, checkbox_y, checkbox_size, checkbox_size)

        # Draw checkbox background
        screen.draw_rect((50, 50, 70), self.return_pad_checkbox_rect)
        screen.draw_rect(self.colors['accent'], self.return_pad_checkbox_rect, 2)

        # Draw checkmark if checked
        if self.create_return_pad:
            # Draw X mark
            screen.draw_line(self.colors['accent'],
                             (checkbox_x + 4, checkbox_y + 4),
                             (checkbox_x + checkbox_size - 4, checkbox_y + checkbox_size - 4), 3)
            screen.draw_line(self.colors['accent'],
                             (checkbox_x + checkbox_size - 4, checkbox_y + 4),
                             (checkbox_x + 4, checkbox_y + checkbox_size - 4), 3)

        # Checkbox label
        checkbox_label = self.font_small.render(
            "Create return pad at path end",
            True,
            self.colors['text']
        )
        screen.blit(checkbox_label, (checkbox_x + checkbox_size + 10, checkbox_y + 2))

    def _draw_boundary_config(self, screen: pygame.Surface):
        """Draw room selection dialog for boundary waypoints"""
        # Same fixed real-window-size/position pattern as
        # _draw_placement_instructions above -- unwrap to the real screen
        # so this modal dialog doesn't get scaled/repositioned by the
        # continuous editor zoom.
        screen = getattr(screen, '_screen', screen)
        # Overlay
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        screen.blit(overlay, (0, 0))

        # Dialog box
        dialog_width = 500
        dialog_height = 300
        dialog_x = (self.screen_width - dialog_width) // 2
        dialog_y = (self.screen_height - dialog_height) // 2

        dialog_surface = pygame.Surface((dialog_width, dialog_height), pygame.SRCALPHA)
        dialog_surface.fill(self.colors['bg'])
        pygame.draw.rect(dialog_surface, self.colors['accent'],
                         (0, 0, dialog_width, dialog_height), 3)

        # Title
        title = self.font_large.render("Select Target Room", True, self.colors['accent'])
        dialog_surface.blit(title, (20, 20))

        # Room dropdown
        dropdown_y = 80
        dropdown_height = 40
        self.dropdown_rect = pygame.Rect(
            dialog_x + 20,
            dialog_y + dropdown_y,
            dialog_width - 40,
            dropdown_height
        )

        # Draw dropdown button
        dropdown_color = self.colors['panel'] if not self.dropdown_open else (50, 50, 70)
        pygame.draw.rect(dialog_surface, dropdown_color,
                         (20, dropdown_y, dialog_width - 40, dropdown_height))
        pygame.draw.rect(dialog_surface, self.colors['accent'],
                         (20, dropdown_y, dialog_width - 40, dropdown_height), 2)

        # Dropdown text
        dropdown_text = self.selected_boundary_room if self.selected_boundary_room else "Select room..."
        text_surf = self.font_medium.render(dropdown_text, True, self.colors['text'])
        dialog_surface.blit(text_surf, (30, dropdown_y + 10))

        # Dropdown arrow
        arrow_points = [
            (dialog_width - 40, dropdown_y + 15),
            (dialog_width - 30, dropdown_y + 15),
            (dialog_width - 35, dropdown_y + 25)
        ]
        pygame.draw.polygon(dialog_surface, self.colors['text_dim'], arrow_points)

        # Draw dropdown menu if open
        self.room_item_rects = []
        self.dropdown_menu_rect = None
        if self.dropdown_open:
            row_height = 35
            total_rooms = len(self.available_rooms)
            visible_rows = min(self.dropdown_visible_rows, total_rooms) if total_rooms else 0
            menu_height = max(visible_rows * row_height, 1)

            # Clamp scroll offset in case the room list shrank since we last scrolled
            max_offset = max(0, total_rooms - self.dropdown_visible_rows)
            self.dropdown_scroll_offset = max(0, min(self.dropdown_scroll_offset, max_offset))

            menu_surface = pygame.Surface((dialog_width - 40, menu_height), pygame.SRCALPHA)
            menu_surface.fill((30, 30, 40))

            mouse_pos = pygame.mouse.get_pos()
            first_index = self.dropdown_scroll_offset
            last_index = min(first_index + self.dropdown_visible_rows, total_rooms)

            item_y = 0
            for i in range(first_index, last_index):
                room = self.available_rooms[i]
                item_rect = pygame.Rect(
                    dialog_x + 20,
                    dialog_y + dropdown_y + dropdown_height + 5 + item_y,
                    dialog_width - 40,
                    30
                )
                self.room_item_rects.append(item_rect)

                # Hover effect
                if item_rect.collidepoint(mouse_pos):
                    pygame.draw.rect(menu_surface, (50, 50, 70),
                                     (0, item_y, dialog_width - 40, 30))

                # Room name
                room_text = self.font_small.render(room, True, self.colors['text'])
                menu_surface.blit(room_text, (10, item_y + 7))

                item_y += row_height

            pygame.draw.rect(menu_surface, self.colors['accent'],
                             (0, 0, dialog_width - 40, menu_height), 2)

            # Scrollbar, drawn only when there are more rooms than fit
            if total_rooms > self.dropdown_visible_rows:
                track_x = dialog_width - 40 - 6
                track_rect = pygame.Rect(track_x, 2, 4, menu_height - 4)
                pygame.draw.rect(menu_surface, (60, 60, 80), track_rect)

                thumb_h = max(20, int(menu_height * (self.dropdown_visible_rows / total_rooms)))
                thumb_travel = menu_height - thumb_h
                thumb_y = int(thumb_travel * (self.dropdown_scroll_offset / max_offset)) if max_offset else 0
                thumb_rect = pygame.Rect(track_x, thumb_y, 4, thumb_h)
                pygame.draw.rect(menu_surface, self.colors['accent'], thumb_rect)

            menu_screen_pos = (dialog_x + 20, dialog_y + dropdown_y + dropdown_height + 5)
            dialog_surface.blit(menu_surface, (20, dropdown_y + dropdown_height + 5))
            self.dropdown_menu_rect = pygame.Rect(
                menu_screen_pos[0], menu_screen_pos[1], dialog_width - 40, menu_height
            )

        # Confirm button (only if room selected)
        self.confirm_button_rect = None
        if self.selected_boundary_room and not self.dropdown_open:
            button_y = dialog_height - 70
            button_width = 150
            button_height = 45
            button_x = dialog_width // 2 - button_width // 2

            # Create the rect in SCREEN coordinates
            self.confirm_button_rect = pygame.Rect(
                dialog_x + button_x,
                dialog_y + button_y,
                button_width,
                button_height
            )

            # Draw button on dialog surface (local coordinates)
            button_rect_local = pygame.Rect(button_x, button_y, button_width, button_height)
            pygame.draw.rect(dialog_surface, self.colors['accent'], button_rect_local)
            pygame.draw.rect(dialog_surface, (255, 255, 255), button_rect_local, 2)

            confirm_text = self.font_medium.render("Confirm", True, (0, 0, 0))
            text_rect = confirm_text.get_rect(center=button_rect_local.center)
            dialog_surface.blit(confirm_text, text_rect)

        screen.blit(dialog_surface, (dialog_x, dialog_y))

    def _draw_spawn_point_preview(self, screen: pygame.Surface, camera, render_scale: int):
        """Draw preview of spawn point placement"""
        # Draw the exit point (boundary waypoint from previous room)
        if self.pending_boundary_waypoint:
            exit_x = (self.pending_boundary_waypoint.x * render_scale) - camera.x
            exit_y = (self.pending_boundary_waypoint.y * render_scale) - camera.y

            # Draw exit marker
            screen.draw_circle((255, 200, 0), (int(exit_x), int(exit_y)), 8)
            screen.draw_circle((0, 0, 0), (int(exit_x), int(exit_y)), 8, 2)

            # Label exit point
            font = pygame.font.Font(None, 16)
            text = font.render("Exit", True, (255, 255, 255))
            text_rect = text.get_rect(center=(exit_x, exit_y - 15))

            bg_rect = text_rect.inflate(4, 2)
            bg_surf = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
            bg_surf.fill((0, 0, 0, 180))
            screen.blit(bg_surf, bg_rect.topleft)
            screen.blit(text, text_rect)

        # Clamp mouse to room bounds
        clamped_x, clamped_y = self._clamp_to_room_bounds(self.preview_mouse_x, self.preview_mouse_y)

        preview_x = (clamped_x * render_scale) - camera.x
        preview_y = (clamped_y * render_scale) - camera.y

        # Check if at boundary
        at_boundary = self._is_at_room_boundary(clamped_x, clamped_y)

        # Choose color based on whether at boundary
        if at_boundary:
            circle_color = (100, 255, 100, 180)  # Green - valid placement
            line_color = (100, 255, 100, 150)
        else:
            circle_color = (255, 100, 100, 180)  # Red - invalid placement
            line_color = (255, 100, 100, 150)

        # Draw line from exit to spawn preview (if we have an exit point)
        if self.pending_boundary_waypoint:
            line_surface = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
            pygame.draw.line(line_surface, line_color,
                             (int(exit_x), int(exit_y)),
                             (int(preview_x), int(preview_y)), 2)
            screen.blit(line_surface, (0, 0))

        # Draw preview spawn point
        circle_surface = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        pygame.draw.circle(circle_surface, circle_color,
                           (int(preview_x), int(preview_y)), 8)
        pygame.draw.circle(circle_surface, (255, 255, 255, 200),
                           (int(preview_x), int(preview_y)), 8, 2)
        screen.blit(circle_surface, (0, 0))

        # Draw "SPAWN" label
        font = pygame.font.Font(None, 16)
        text = font.render("SPAWN", True, (255, 255, 255))
        text_rect = text.get_rect(center=(preview_x, preview_y + 15))

        bg_rect = text_rect.inflate(4, 2)
        bg_surf = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
        bg_surf.fill((0, 0, 0, 180))
        screen.blit(bg_surf, bg_rect.topleft)
        screen.blit(text, text_rect)

    def _draw_spawn_placement_instructions(self, screen: pygame.Surface):
        """Draw instructions for spawn point placement"""
        # Same fixed real-window-size/position pattern as
        # _draw_placement_instructions above -- unwrap to the real screen
        # so this HUD panel doesn't get scaled/repositioned by the
        # continuous editor zoom.
        screen = getattr(screen, '_screen', screen)
        # Semi-transparent panel at top
        panel_height = 160
        panel = pygame.Surface((self.screen_width, panel_height), pygame.SRCALPHA)
        panel.fill((20, 60, 20, 230))  # Slightly green tint
        screen.blit(panel, (0, 0))

        # Title
        title = self.font_large.render("Place Spawn Point", True, (100, 255, 100))
        screen.blit(title, (20, 10))

        # Instructions
        instructions = [
            "Click at room edge to place spawn point",
            "This is where the player will appear in this room",
            "The spawn point must be at the room boundary",
            "",
            "ESC: Cancel and return to previous room"
        ]

        y = 50
        for inst in instructions:
            if inst:  # Skip empty lines in rendering
                text = self.font_small.render(inst, True, (200, 255, 200))
                screen.blit(text, (20, y))
            y += 20

        # Room info
        room_info = self.font_small.render(
            f"Destination: {self.current_room_name}",
            True,
            (200, 255, 200)
        )
        screen.blit(room_info, (self.screen_width - 300, 10))