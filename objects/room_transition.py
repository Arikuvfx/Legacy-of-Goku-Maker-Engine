import pygame
from typing import Optional, Tuple, List


class RoomTransition:
    """Portal object that triggers transitions between rooms"""

    def __init__(self, x: int, y: int, width: int = 16, height: int = 16):
        self.x = x  # Top-left position
        self.y = y  # Top-left position
        self.width = width
        self.height = height
        self.active = True

        # Transition configuration
        self.target_room = ""
        self.entry_direction = 'down'
        self.exit_direction = 'up'
        self.spawn_x = 0
        self.spawn_y = 0

        # For destination spawn transitions
        self.spawn_width = width  # Default to same size
        self.spawn_height = height  # Default to same size

        # For tracking entry position
        self.last_collision_x = 0
        self.last_collision_y = 0

    def get_rect(self) -> pygame.Rect:
        """Get collision rectangle"""
        return pygame.Rect(self.x, self.y, self.width, self.height)

    def get_center(self) -> Tuple[int, int]:
        """Get center position"""
        return (self.x + self.width // 2, self.y + self.height // 2)

    def check_collision(self, player) -> bool:
        """Check if player is touching this transition"""
        player_rect = pygame.Rect(
            player.x - player.width // 2,
            player.y - player.height // 2,
            player.width,
            player.height
        )
        transition_rect = self.get_rect()

        # Check for collision
        if not transition_rect.colliderect(player_rect):
            return False

        # Store the player's center position for entry calculation
        self.last_collision_x = player.x
        self.last_collision_y = player.y

        return True

    def check_collision_with_point(self, x: int, y: int) -> bool:
        """Check if a point is inside this transition"""
        return self.get_rect().collidepoint(x, y)

    def draw(self, screen: pygame.Surface, camera, render_scale: int = 2, dev_mode: bool = True,
             selected: bool = False):
        """Draw transition portal"""
        if not self.active:
            return

        # Calculate screen position
        screen_x = (self.x * render_scale) - camera.x
        screen_y = (self.y * render_scale) - camera.y
        screen_width = self.width * render_scale
        screen_height = self.height * render_scale

        rect = pygame.Rect(int(screen_x), int(screen_y), int(screen_width), int(screen_height))

        # Draw semi-transparent fill (blue color)
        alpha = 100 if not selected else 150
        fill_color = (0, 100, 255, alpha) if not selected else (50, 150, 255, alpha)
        fill_surface = pygame.Surface((int(screen_width), int(screen_height)), pygame.SRCALPHA)
        fill_surface.fill(fill_color)
        screen.blit(fill_surface, (int(screen_x), int(screen_y)))

        # Draw border
        border_color = (0, 150, 255) if not selected else (100, 200, 255)
        border_width = 2 if not selected else 3
        pygame.draw.rect(screen, border_color, rect, border_width)

        # Draw diagonal lines pattern
        line_color = (0, 120, 200, 100) if not selected else (80, 180, 255, 150)
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
        handle_color = (100, 200, 255) if selected else (0, 180, 255)

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

        # Draw center portal effect
        center_x = screen_x + screen_width // 2
        center_y = screen_y + screen_height // 2

        # Only draw portal effect if large enough
        if screen_width > 40 and screen_height > 40:
            # Outer glow
            for i in range(5, 0, -1):
                radius = min(int(screen_width // 4), int(screen_height // 4)) - i * 2
                if radius > 0:
                    alpha = 40 - i * 5
                    color = (100, 200, 255, alpha)
                    pygame.draw.circle(screen, color, (int(center_x), int(center_y)), radius)

            # Inner portal
            inner_radius = min(int(screen_width // 6), int(screen_height // 6))
            if inner_radius > 0:
                pygame.draw.circle(screen, (150, 220, 255, 180), (int(center_x), int(center_y)), inner_radius)

        # Draw direction arrow
        arrow_length = 15 * render_scale
        arrow_color = (255, 255, 0)

        if self.exit_direction == 'up':
            pygame.draw.line(screen, arrow_color,
                             (center_x, center_y),
                             (center_x, center_y - arrow_length), 3)
            pygame.draw.polygon(screen, arrow_color, [
                (center_x, center_y - arrow_length),
                (center_x - 5 * render_scale, center_y - arrow_length + 10 * render_scale),
                (center_x + 5 * render_scale, center_y - arrow_length + 10 * render_scale)
            ])
        elif self.exit_direction == 'down':
            pygame.draw.line(screen, arrow_color,
                             (center_x, center_y),
                             (center_x, center_y + arrow_length), 3)
            pygame.draw.polygon(screen, arrow_color, [
                (center_x, center_y + arrow_length),
                (center_x - 5 * render_scale, center_y + arrow_length - 10 * render_scale),
                (center_x + 5 * render_scale, center_y + arrow_length - 10 * render_scale)
            ])
        elif self.exit_direction == 'left':
            pygame.draw.line(screen, arrow_color,
                             (center_x, center_y),
                             (center_x - arrow_length, center_y), 3)
            pygame.draw.polygon(screen, arrow_color, [
                (center_x - arrow_length, center_y),
                (center_x - arrow_length + 10 * render_scale, center_y - 5 * render_scale),
                (center_x - arrow_length + 10 * render_scale, center_y + 5 * render_scale)
            ])
        elif self.exit_direction == 'right':
            pygame.draw.line(screen, arrow_color,
                             (center_x, center_y),
                             (center_x + arrow_length, center_y), 3)
            pygame.draw.polygon(screen, arrow_color, [
                (center_x + arrow_length, center_y),
                (center_x + arrow_length - 10 * render_scale, center_y - 5 * render_scale),
                (center_x + arrow_length - 10 * render_scale, center_y + 5 * render_scale)
            ])

        # Draw dimensions text if object is large enough
        if screen_width > 50 and screen_height > 30:
            font = pygame.font.Font(None, 18)
            dims_text = f"{self.width} x {self.height}"
            text_surface = font.render(dims_text, True, (255, 255, 255))
            text_rect = text_surface.get_rect(center=(center_x, center_y + 20))

            # Draw text background
            bg_rect = text_rect.inflate(8, 4)
            bg_surface = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
            bg_surface.fill((0, 0, 0, 180))
            screen.blit(bg_surface, bg_rect.topleft)

            screen.blit(text_surface, text_rect)

        # Draw target room name if set
        if self.target_room and screen_width > 60:
            font = pygame.font.Font(None, int(16 * render_scale))
            text = font.render(f"→ {self.target_room}", True, (255, 255, 255))
            text_rect = text.get_rect(
                centerx=center_x,
                top=screen_y + screen_height + 5
            )

            bg_rect = text_rect.inflate(8, 4)
            bg_surface = pygame.Surface((bg_rect.width, bg_rect.height), pygame.SRCALPHA)
            bg_surface.fill((0, 0, 0, 180))
            screen.blit(bg_surface, bg_rect.topleft)

            screen.blit(text, text_rect)

    def to_dict(self) -> dict:
        """Serialize transition for saving"""
        return {
            'type': 'room_transition',
            'x': self.x,
            'y': self.y,
            'width': self.width,
            'height': self.height,
            'target_room': self.target_room,
            'exit_direction': self.exit_direction,
            'entry_direction': self.entry_direction,
            'spawn_x': self.spawn_x,
            'spawn_y': self.spawn_y,
            'spawn_width': getattr(self, 'spawn_width', self.width),  # Save spawn dimensions
            'spawn_height': getattr(self, 'spawn_height', self.height)  # Save spawn dimensions
        }

    @staticmethod
    def from_dict(data: dict) -> 'RoomTransition':
        """Deserialize transition from save data"""
        transition = RoomTransition(
            data.get('x', 0),
            data.get('y', 0),
            data.get('width', 16),
            data.get('height', 16)
        )
        transition.target_room = data.get('target_room', '')
        transition.exit_direction = data.get('exit_direction', 'up')
        transition.entry_direction = data.get('entry_direction', 'down')
        transition.spawn_x = data.get('spawn_x', 0)
        transition.spawn_y = data.get('spawn_y', 0)
        transition.spawn_width = data.get('spawn_width', transition.width)  # Load spawn dimensions
        transition.spawn_height = data.get('spawn_height', transition.height)  # Load spawn dimensions
        return transition


class RoomTransitionManager:
    """Manages room transitions for all rooms"""

    def __init__(self):
        self.transitions = {}

    def get_transitions(self, room_name: str) -> list:
        """Get all transitions for a room"""
        return self.transitions.get(room_name, [])

    def add_transition(self, room_name: str, transition: RoomTransition):
        """Add a transition to a room"""
        if room_name not in self.transitions:
            self.transitions[room_name] = []
        self.transitions[room_name].append(transition)

    def remove_transition(self, room_name: str, transition: RoomTransition):
        """Remove a transition from a room"""
        if room_name in self.transitions:
            if transition in self.transitions[room_name]:
                self.transitions[room_name].remove(transition)

    def clear_room(self, room_name: str):
        """Clear all transitions from a room"""
        if room_name in self.transitions:
            self.transitions[room_name] = []


class TransitionConfigDialog:
    """Dialog for configuring room transition properties with dropdown menus"""

    def __init__(self, screen_width, screen_height, y_offset=0):
        self.active = False
        self.transition = None
        self.available_rooms = []

        # Dropdown states
        self.dropdowns = {
            'target_room': False,
            'exit_direction': False,
            'entry_direction': False
        }
        self.directions = ['up', 'down', 'left', 'right']

        # Fonts
        self.font_large = pygame.font.Font(None, 32)
        self.font_medium = pygame.font.Font(None, 24)
        self.font_small = pygame.font.Font(None, 18)

        # Colors
        self.colors = {
            'bg': (20, 20, 30, 240),
            'panel': (35, 35, 55),
            'panel_hover': (45, 45, 65),
            'accent': (0, 150, 255),
            'accent_hover': (50, 180, 255),
            'text': (255, 255, 255),
            'text_dim': (180, 180, 200),
            'dropdown_bg': (30, 30, 40),
            'dropdown_hover': (50, 50, 70)
        }

        # UI rects for click detection
        self.ui_rects = {}


    def open(self, transition: RoomTransition, available_rooms: list, current_room_name: str = ""):
        """Open dialog to configure a transition"""
        self.active = True
        self.transition = transition
        # Filter out current room from available rooms
        self.available_rooms = [room for room in available_rooms if room != current_room_name]
        # Close all dropdowns
        for key in self.dropdowns:
            self.dropdowns[key] = False

    def close(self):
        """Close the dialog"""
        self.active = False
        self.transition = None
        for key in self.dropdowns:
            self.dropdowns[key] = False

    def handle_input(self, event) -> Optional[str]:
        """Handle input events, returns 'save' or 'cancel'"""
        if not self.active:
            return None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mouse_pos = event.pos

            # Check if clicking save button
            if 'save_button' in self.ui_rects:
                if self.ui_rects['save_button'].collidepoint(mouse_pos):
                    self.close()
                    return 'save'

            # Check if clicking cancel button
            if 'cancel_button' in self.ui_rects:
                if self.ui_rects['cancel_button'].collidepoint(mouse_pos):
                    self.close()
                    return 'cancel'

            # Check dropdown toggles
            for dropdown_name in ['target_room', 'exit_direction', 'entry_direction']:
                rect_name = f'{dropdown_name}_toggle'
                if rect_name in self.ui_rects:
                    if self.ui_rects[rect_name].collidepoint(mouse_pos):
                        # Close other dropdowns
                        for key in self.dropdowns:
                            if key != dropdown_name:
                                self.dropdowns[key] = False
                        # Toggle this dropdown
                        self.dropdowns[dropdown_name] = not self.dropdowns[dropdown_name]
                        return None

            # Check dropdown item clicks
            for dropdown_name in ['target_room', 'exit_direction', 'entry_direction']:
                if self.dropdowns[dropdown_name]:
                    items = self._get_dropdown_items(dropdown_name)
                    for i, item in enumerate(items):
                        rect_name = f'{dropdown_name}_item_{i}'
                        if rect_name in self.ui_rects:
                            if self.ui_rects[rect_name].collidepoint(mouse_pos):
                                self._set_dropdown_value(dropdown_name, item)
                                self.dropdowns[dropdown_name] = False
                                return None

            # Close dropdowns if clicking outside
            clicked_on_dropdown = False
            for rect_name in self.ui_rects:
                if 'dropdown' in rect_name or 'toggle' in rect_name:
                    if self.ui_rects[rect_name].collidepoint(mouse_pos):
                        clicked_on_dropdown = True
                        break

            if not clicked_on_dropdown:
                for key in self.dropdowns:
                    self.dropdowns[key] = False

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.close()
                return 'cancel'

        return None

    def _get_dropdown_items(self, dropdown_name: str) -> List[str]:
        """Get items for a dropdown"""
        if dropdown_name == 'target_room':
            return self.available_rooms if self.available_rooms else ['No rooms available']
        elif dropdown_name in ['exit_direction', 'entry_direction']:
            return self.directions
        return []

    def get_relative_entry_position(self, player_x, player_y):
        """Calculate relative position within transition where player entered"""
        if self.width <= 0 or self.height <= 0:
            return 0.5, 0.5  # Default center if size is invalid

        # Calculate relative position (0 to 1)
        rel_x = (player_x - self.x) / self.width
        rel_y = (player_y - self.y) / self.height

        # Clamp to valid range
        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))

        return rel_x, rel_y

    def _set_dropdown_value(self, dropdown_name: str, value: str):
        """Set the value for a dropdown field"""
        if dropdown_name == 'target_room':
            self.transition.target_room = value
        elif dropdown_name == 'exit_direction':
            self.transition.exit_direction = value
        elif dropdown_name == 'entry_direction':
            self.transition.entry_direction = value

    def _get_dropdown_value(self, dropdown_name: str) -> str:
        """Get current value for a dropdown"""
        if dropdown_name == 'target_room':
            return self.transition.target_room or 'Select Room'
        elif dropdown_name == 'exit_direction':
            return self.transition.exit_direction
        elif dropdown_name == 'entry_direction':
            return self.transition.entry_direction
        return ''

    def draw(self, screen: pygame.Surface):
        """Draw the configuration dialog"""
        if not self.active or not self.transition:
            return

        self.ui_rects = {}

        # Semi-transparent overlay
        overlay = pygame.Surface((screen.get_width(), screen.get_height()), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        screen.blit(overlay, (0, 0))

        # Dialog box
        dialog_width = 600
        dialog_height = 500
        dialog_x = (screen.get_width() - dialog_width) // 2
        dialog_y = (screen.get_height() - dialog_height) // 2

        dialog_surface = pygame.Surface((dialog_width, dialog_height), pygame.SRCALPHA)
        dialog_surface.fill(self.colors['bg'])
        pygame.draw.rect(dialog_surface, self.colors['accent'], (0, 0, dialog_width, dialog_height), 3)

        # Title
        title = self.font_large.render("Configure Room Transition", True, self.colors['accent'])
        dialog_surface.blit(title, (20, 20))

        y_pos = 80
        mouse_pos = pygame.mouse.get_pos()
        adjusted_mouse = (mouse_pos[0] - dialog_x, mouse_pos[1] - dialog_y)

        # Draw each dropdown field
        fields = [
            ('target_room', 'Target Room'),
            ('exit_direction', 'Exit Direction'),
            ('entry_direction', 'Entry Direction')
        ]

        # Find if any dropdown is open and which one
        open_dropdown_index = -1
        for idx, (field_id, _) in enumerate(fields):
            if self.dropdowns[field_id]:
                open_dropdown_index = idx
                break

        for idx, (field_id, label) in enumerate(fields):
            # Skip drawing fields below an open dropdown
            if open_dropdown_index >= 0 and idx > open_dropdown_index:
                continue

            # Label
            label_surf = self.font_medium.render(label + ":", True, self.colors['text_dim'])
            dialog_surface.blit(label_surf, (20, y_pos))

            # Dropdown toggle button
            toggle_rect = pygame.Rect(20, y_pos + 30, dialog_width - 40, 40)
            is_hover = toggle_rect.collidepoint(adjusted_mouse)
            bg_color = self.colors['panel_hover'] if is_hover else self.colors['panel']

            pygame.draw.rect(dialog_surface, bg_color, toggle_rect, border_radius=5)
            pygame.draw.rect(dialog_surface, self.colors['accent'], toggle_rect, 2, border_radius=5)

            # Display current value
            value_text = self._get_dropdown_value(field_id)
            value_surf = self.font_medium.render(str(value_text), True, self.colors['text'])
            dialog_surface.blit(value_surf, (toggle_rect.x + 10, toggle_rect.y + 8))

            # Dropdown arrow
            arrow_x = toggle_rect.right - 30
            arrow_y = toggle_rect.centery
            arrow_points = [
                (arrow_x, arrow_y - 5),
                (arrow_x + 10, arrow_y - 5),
                (arrow_x + 5, arrow_y + 5)
            ]
            pygame.draw.polygon(dialog_surface, self.colors['text_dim'], arrow_points)

            # Store rect for click detection
            global_toggle_rect = toggle_rect.move(dialog_x, dialog_y)
            self.ui_rects[f'{field_id}_toggle'] = global_toggle_rect

            y_pos += 80

            # Draw dropdown menu if open
            if self.dropdowns[field_id]:
                items = self._get_dropdown_items(field_id)
                dropdown_height = min(len(items) * 35 + 10, 200)
                dropdown_rect = pygame.Rect(20, toggle_rect.bottom + 5, dialog_width - 40, dropdown_height)

                # Dropdown background
                dropdown_surface = pygame.Surface((dropdown_rect.width, dropdown_rect.height), pygame.SRCALPHA)
                dropdown_surface.fill(self.colors['dropdown_bg'])
                pygame.draw.rect(dropdown_surface, self.colors['accent'],
                                 (0, 0, dropdown_rect.width, dropdown_rect.height), 2)

                # Draw items
                item_y = 5
                for i, item in enumerate(items):
                    item_rect = pygame.Rect(5, item_y, dropdown_rect.width - 10, 30)
                    item_global_rect = item_rect.move(dialog_x + dropdown_rect.x, dialog_y + dropdown_rect.y)

                    is_item_hover = item_rect.collidepoint(
                        adjusted_mouse[0] - dropdown_rect.x,
                        adjusted_mouse[1] - dropdown_rect.y
                    )

                    if is_item_hover:
                        pygame.draw.rect(dropdown_surface, self.colors['dropdown_hover'], item_rect, border_radius=3)

                    item_text = self.font_small.render(item, True, self.colors['text'])
                    dropdown_surface.blit(item_text, (item_rect.x + 10, item_rect.y + 7))

                    self.ui_rects[f'{field_id}_item_{i}'] = item_global_rect
                    self.ui_rects[f'{field_id}_dropdown'] = dropdown_rect.move(dialog_x, dialog_y)
                    item_y += 35

                dialog_surface.blit(dropdown_surface, dropdown_rect.topleft)

        # Buttons at bottom
        button_y = dialog_height - 80
        button_width = 200

        # Save button
        save_x = 50
        save_rect = pygame.Rect(save_x, button_y, button_width, 50)
        save_is_hover = save_rect.collidepoint(adjusted_mouse)
        save_color = self.colors['accent_hover'] if save_is_hover else (100, 255, 100)

        pygame.draw.rect(dialog_surface, save_color, save_rect, border_radius=5)
        pygame.draw.rect(dialog_surface, self.colors['accent'], save_rect, 2, border_radius=5)

        save_text = self.font_large.render("Save", True, (0, 0, 0) if save_is_hover else (255, 255, 255))
        save_text_rect = save_text.get_rect(center=save_rect.center)
        dialog_surface.blit(save_text, save_text_rect)

        self.ui_rects['save_button'] = save_rect.move(dialog_x, dialog_y)

        # Cancel button
        cancel_x = dialog_width - button_width - 50
        cancel_rect = pygame.Rect(cancel_x, button_y, button_width, 50)
        cancel_is_hover = cancel_rect.collidepoint(adjusted_mouse)
        cancel_color = (255, 120, 120) if cancel_is_hover else (255, 100, 100)

        pygame.draw.rect(dialog_surface, cancel_color, cancel_rect, border_radius=5)
        pygame.draw.rect(dialog_surface, self.colors['accent'], cancel_rect, 2, border_radius=5)

        cancel_text = self.font_large.render("Cancel", True, (0, 0, 0) if cancel_is_hover else (255, 255, 255))
        cancel_text_rect = cancel_text.get_rect(center=cancel_rect.center)
        dialog_surface.blit(cancel_text, cancel_text_rect)

        self.ui_rects['cancel_button'] = cancel_rect.move(dialog_x, dialog_y)

        screen.blit(dialog_surface, (dialog_x, dialog_y))