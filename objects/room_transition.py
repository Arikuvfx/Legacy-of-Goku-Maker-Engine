import pygame
from typing import Optional, Tuple, List

import dev_tools.ui_kit as uk

# Dev-mode overlay fonts for RoomTransition.draw() — cached by size so a
# room full of transitions doesn't re-parse the font file every frame for
# every transition (same fix as flying_pad.py/nimbus_cloud.py).
_FONT_CACHE = {}


def _get_font(size):
    font = _FONT_CACHE.get(size)
    if font is None:
        font = pygame.font.Font(None, size)
        _FONT_CACHE[size] = font
    return font


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

        # Cooldown: prevents instant re-trigger after a transition fires.
        # Use start_cooldown() after a transition is used, and call
        # RoomTransitionManager.start_cooldowns() when entering a new room.
        self._cooldown_until_ms: int = 0
        self.COOLDOWN_MS: int = 500

    def get_rect(self) -> pygame.Rect:
        """Get collision rectangle"""
        return pygame.Rect(self.x, self.y, self.width, self.height)

    def get_center(self) -> Tuple[int, int]:
        """Get center position"""
        return (self.x + self.width // 2, self.y + self.height // 2)

    def start_cooldown(self):
        """Arm the cooldown timer. Call this immediately after this transition
        fires so it cannot re-trigger until COOLDOWN_MS milliseconds have passed."""
        self._cooldown_until_ms = pygame.time.get_ticks() + self.COOLDOWN_MS

    def is_on_cooldown(self) -> bool:
        """Return True while the transition is still cooling down."""
        return pygame.time.get_ticks() < self._cooldown_until_ms

    def check_collision(self, player) -> bool:
        """Check if player is touching this transition"""
        # Don't trigger while cooling down — prevents instant re-trigger on spawn.
        if self.is_on_cooldown():
            return False

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
        screen.draw_rect(border_color, rect, border_width)

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
            screen.draw_rect(handle_color,
                             (int(corner_x - handle_size // 2),
                              int(corner_y - handle_size // 2),
                              int(handle_size), int(handle_size)))
            screen.draw_rect((0, 0, 0),
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
                    screen.draw_circle(color, (int(center_x), int(center_y)), radius)

            # Inner portal
            inner_radius = min(int(screen_width // 6), int(screen_height // 6))
            if inner_radius > 0:
                screen.draw_circle((150, 220, 255, 180), (int(center_x), int(center_y)), inner_radius)

        # Draw direction arrow
        arrow_length = 15 * render_scale
        arrow_color = (255, 255, 0)

        if self.exit_direction == 'up':
            screen.draw_line(arrow_color,
                             (center_x, center_y),
                             (center_x, center_y - arrow_length), 3)
            screen.draw_polygon(arrow_color, [
                (center_x, center_y - arrow_length),
                (center_x - 5 * render_scale, center_y - arrow_length + 10 * render_scale),
                (center_x + 5 * render_scale, center_y - arrow_length + 10 * render_scale)
            ])
        elif self.exit_direction == 'down':
            screen.draw_line(arrow_color,
                             (center_x, center_y),
                             (center_x, center_y + arrow_length), 3)
            screen.draw_polygon(arrow_color, [
                (center_x, center_y + arrow_length),
                (center_x - 5 * render_scale, center_y + arrow_length - 10 * render_scale),
                (center_x + 5 * render_scale, center_y + arrow_length - 10 * render_scale)
            ])
        elif self.exit_direction == 'left':
            screen.draw_line(arrow_color,
                             (center_x, center_y),
                             (center_x - arrow_length, center_y), 3)
            screen.draw_polygon(arrow_color, [
                (center_x - arrow_length, center_y),
                (center_x - arrow_length + 10 * render_scale, center_y - 5 * render_scale),
                (center_x - arrow_length + 10 * render_scale, center_y + 5 * render_scale)
            ])
        elif self.exit_direction == 'right':
            screen.draw_line(arrow_color,
                             (center_x, center_y),
                             (center_x + arrow_length, center_y), 3)
            screen.draw_polygon(arrow_color, [
                (center_x + arrow_length, center_y),
                (center_x + arrow_length - 10 * render_scale, center_y - 5 * render_scale),
                (center_x + arrow_length - 10 * render_scale, center_y + 5 * render_scale)
            ])

        # Draw dimensions text if object is large enough
        if screen_width > 50 and screen_height > 30:
            font = _get_font(18)
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
            font = _get_font(int(16 * render_scale))
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

    def start_cooldowns(self, room_name: str):
        """Put every transition in a room on cooldown.

        Call this immediately after the player enters a new room so that any
        transition they spawn on or next to cannot fire until COOLDOWN_MS ms
        have elapsed.  This prevents the classic instant-retrigger loop where
        the player walks through portal A → spawns touching portal B → gets
        teleported straight back.
        """
        for transition in self.transitions.get(room_name, []):
            transition.start_cooldown()


class _MenuFont:
    """Adapts ui_kit.BitmapFont to the plain pygame.font.Font call shape —
    render(text, antialias, color) / size(text) — at one fixed pixel
    height. Same adapter room_editor.py / dev_menu.py use, so this dialog's
    text renders with the exact same bitmap glyphs as every other dev-tool
    overlay instead of a plain TTF font."""

    def __init__(self, bitmap_font, height):
        self._font = bitmap_font
        self._height = height

    def render(self, text, antialias=True, color=(255, 255, 255)):
        return self._font.render(text, color=color, height=self._height)

    def size(self, text):
        return self._font.size(text, height=self._height)


# ---------------------------------------------------------------------------
# Small vector glyphs, drawn with the same line primitives as every other
# ui_kit icon so they read as part of the same family instead of a
# mismatched one-off (the old dialog used pygame.draw.polygon triangles).
# ---------------------------------------------------------------------------

def _draw_chevron_up(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    uk.draw_line_on(surface, color, (cx - s, cy + s * 0.5), (cx, cy - s * 0.6), width)
    uk.draw_line_on(surface, color, (cx, cy - s * 0.6), (cx + s, cy + s * 0.5), width)


def _draw_chevron_down(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    uk.draw_line_on(surface, color, (cx - s, cy - s * 0.5), (cx, cy + s * 0.6), width)
    uk.draw_line_on(surface, color, (cx, cy + s * 0.6), (cx + s, cy - s * 0.5), width)


def _draw_check_icon(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    uk.draw_line_on(surface, color, (cx - s, cy), (cx - s * 0.15, cy + s * 0.8), width)
    uk.draw_line_on(surface, color, (cx - s * 0.15, cy + s * 0.8), (cx + s, cy - s * 0.7), width)


class TransitionConfigDialog:
    """Dialog for configuring room transition properties with dropdown menus.

    Same open/close/handle_input contract and the same three dropdown
    fields (target room, exit direction, entry direction) plus Save/Cancel
    as before — only the drawing changed, over to the shared ui_kit
    "modern DBZ" look (deep navy panel, gold accent, rounded hairline-
    bordered cards) used by every other dev-tool overlay, instead of the
    old flat colored-rectangle dialog.
    """

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
        # Scroll offset (in items) for each dropdown
        self.dropdown_scroll = {
            'target_room': 0,
            'exit_direction': 0,
            'entry_direction': 0
        }
        self.directions = ['up', 'down', 'left', 'right']

        # Fonts — bitmap menu font, same family as every other dev-tool
        # overlay, instead of a plain system TTF font.
        self._bitmap_font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self.font_large = _MenuFont(self._bitmap_font, 22)
        self.font_medium = _MenuFont(self._bitmap_font, 16)
        self.font_small = _MenuFont(self._bitmap_font, 13)

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

            # Dropdown popups are a higher z-order than Save/Cancel. Handle
            # their controls first so an option click can never also activate
            # a button underneath the popup.
            for dropdown_name in ['target_room', 'exit_direction', 'entry_direction']:
                if self.dropdowns[dropdown_name]:
                    items = self._get_dropdown_items(dropdown_name)
                    max_visible = 5

                    # Scroll arrow clicks
                    up_key = f'{dropdown_name}_scroll_up'
                    down_key = f'{dropdown_name}_scroll_down'
                    if up_key in self.ui_rects and self.ui_rects[up_key].collidepoint(mouse_pos):
                        self.dropdown_scroll[dropdown_name] = max(
                            0, self.dropdown_scroll[dropdown_name] - 1
                        )
                        return None

                    if down_key in self.ui_rects and self.ui_rects[down_key].collidepoint(mouse_pos):
                        max_scroll = max(0, len(items) - max_visible)
                        self.dropdown_scroll[dropdown_name] = min(
                            max_scroll,
                            self.dropdown_scroll[dropdown_name] + 1
                        )
                        return None

                    # Item clicks are checked before Save/Cancel because the
                    # popup is visually above those buttons.
                    for actual_index, item in enumerate(items):
                        rect_name = f'{dropdown_name}_item_{actual_index}'
                        if rect_name in self.ui_rects:
                            if self.ui_rects[rect_name].collidepoint(mouse_pos):
                                self._set_dropdown_value(dropdown_name, item)
                                self.dropdowns[dropdown_name] = False
                                return None

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
                        # Toggle this dropdown and reset scroll
                        opening = not self.dropdowns[dropdown_name]
                        self.dropdowns[dropdown_name] = opening
                        if opening:
                            self.dropdown_scroll[dropdown_name] = 0
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

        elif event.type == pygame.MOUSEWHEEL:
            # Scroll whichever dropdown is open, if mouse is over it
            mouse_pos = pygame.mouse.get_pos()
            for dropdown_name in ['target_room', 'exit_direction', 'entry_direction']:
                if self.dropdowns[dropdown_name]:
                    rect_key = f'{dropdown_name}_dropdown'
                    if rect_key in self.ui_rects and self.ui_rects[rect_key].collidepoint(mouse_pos):
                        items = self._get_dropdown_items(dropdown_name)
                        max_visible = 5
                        max_scroll = max(0, len(items) - max_visible)
                        self.dropdown_scroll[dropdown_name] = max(
                            0, min(max_scroll,
                                   self.dropdown_scroll[dropdown_name] - event.y)
                        )
                        return None

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
        """Get current display value for a dropdown"""
        if dropdown_name == 'target_room':
            return self.transition.target_room or 'Select Room'
        elif dropdown_name == 'exit_direction':
            return self.transition.exit_direction
        elif dropdown_name == 'entry_direction':
            return self.transition.entry_direction
        return ''

    def _get_raw_value(self, dropdown_name: str):
        """Get the actual stored value for a dropdown (no placeholder text),
        used to mark the currently-selected row in the open dropdown list."""
        if dropdown_name == 'target_room':
            return self.transition.target_room
        elif dropdown_name == 'exit_direction':
            return self.transition.exit_direction
        elif dropdown_name == 'entry_direction':
            return self.transition.entry_direction
        return None

    # =========================================================================
    # Drawing — ui_kit styled
    # =========================================================================

    def _draw_dropdown_toggle(self, screen, rect, value_text, is_open, is_hover):
        accent = uk.Theme.GOLD

        if is_open:
            border, border_width, bg = accent, 2, (32, 36, 58, 255)
        elif is_hover:
            border, border_width, bg = uk.lerp_color(uk.Theme.CARD_BORDER, accent, 0.5), 1, uk.Theme.CARD_BG_HOVER
        else:
            border, border_width, bg = uk.Theme.CARD_BORDER, 1, uk.Theme.CARD_BG

        uk.draw_panel(screen, rect, bg=bg, border=border, border_width=border_width, radius=8, shadow=False)

        color = uk.Theme.GOLD_BRIGHT if is_open else (uk.Theme.TEXT_PRIMARY if is_hover else uk.Theme.TEXT_SECONDARY)
        val_surf = self.font_medium.render(str(value_text), True, color)
        uk.blit_surface(screen, val_surf, (rect.x + 14, rect.centery - val_surf.get_height() // 2),
                        transient=True)

        chevron_rect = pygame.Rect(0, 0, 16, 16)
        chevron_rect.midright = (rect.right - 16, rect.centery)
        chev_color = accent if (is_open or is_hover) else uk.Theme.TEXT_DIM
        if is_open:
            _draw_chevron_up(screen, chevron_rect, chev_color)
        else:
            _draw_chevron_down(screen, chevron_rect, chev_color)

        uk.register_hoverable(rect)

    def _draw_dropdown_menu(self, screen, anchor_rect, field_id, max_bottom=None):
        """Popup option list anchored under `anchor_rect`'s toggle. Returns
        the dropdown's rect; also fills in this field's *_dropdown,
        *_scroll_up, *_scroll_down and *_item_N entries in self.ui_rects.

        `max_bottom`, if given, is the lowest y the list is allowed to
        reach (e.g. the top of the Save/Cancel row) — if opening downward
        would run past it, the list opens upward from the toggle instead,
        same convention RoomEditor's dropdowns use to stay clear of the
        edge of their own dialog."""
        items = self._get_dropdown_items(field_id)
        max_visible = 5
        scroll_offset = self.dropdown_scroll[field_id]
        visible_items = items[scroll_offset: scroll_offset + max_visible]

        can_scroll_up = scroll_offset > 0
        can_scroll_down = scroll_offset + max_visible < len(items)

        arrow_h = 20
        item_h = 34
        top_arrow_h = arrow_h if can_scroll_up else 0
        bot_arrow_h = arrow_h if can_scroll_down else 0
        dropdown_height = top_arrow_h + len(visible_items) * item_h + bot_arrow_h + 10
        dropdown_rect = pygame.Rect(
            anchor_rect.x,
            anchor_rect.bottom + 6,
            anchor_rect.width,
            dropdown_height
        )

        # Entry Direction always opens downward. When necessary the popup can
        # extend into the Save/Cancel row; it is drawn afterward so it remains
        # visually above those buttons, and its click handling runs first.
        force_downward = field_id == 'entry_direction'

        if (
            not force_downward
            and max_bottom is not None
            and dropdown_rect.bottom > max_bottom
        ):
            dropdown_rect.y = anchor_rect.top - dropdown_height - 6

        uk.draw_panel(screen, dropdown_rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=1, radius=8)
        self.ui_rects[f'{field_id}_dropdown'] = dropdown_rect

        mouse_pos = pygame.mouse.get_pos()
        current_value = self._get_raw_value(field_id)
        inner_y = dropdown_rect.y + 5

        if can_scroll_up:
            arrow_rect = pygame.Rect(dropdown_rect.x + 6, inner_y, dropdown_rect.width - 12, arrow_h - 2)
            if arrow_rect.collidepoint(mouse_pos):
                uk.draw_rect_on(screen, uk.Theme.CARD_BG_HOVER[:3], arrow_rect, 0, 6)
            chev_rect = pygame.Rect(0, 0, 14, 14)
            chev_rect.center = arrow_rect.center
            _draw_chevron_up(screen, chev_rect, uk.Theme.TEXT_SECONDARY)
            self.ui_rects[f'{field_id}_scroll_up'] = arrow_rect
            uk.register_hoverable(arrow_rect)
            inner_y += arrow_h

        for i, item in enumerate(visible_items):
            actual_index = scroll_offset + i
            item_rect = pygame.Rect(dropdown_rect.x + 4, inner_y, dropdown_rect.width - 8, item_h - 2)

            is_current = (item == current_value)
            hovered = item_rect.collidepoint(mouse_pos)
            if is_current or hovered:
                row_bg = uk.Theme.CARD_BG_SELECTED[:3] if is_current else uk.Theme.CARD_BG_HOVER[:3]
                uk.draw_rect_on(screen, row_bg, item_rect.inflate(-4, -2), 0, 6)

            text_color = uk.Theme.GOLD_BRIGHT if is_current else uk.Theme.TEXT_SECONDARY
            item_surf = self.font_small.render(str(item), True, text_color)
            uk.blit_surface(screen, item_surf,
                            (item_rect.x + 10, item_rect.centery - item_surf.get_height() // 2),
                            transient=True)

            if is_current:
                chk_rect = pygame.Rect(0, 0, 14, 14)
                chk_rect.midright = (item_rect.right - 10, item_rect.centery)
                _draw_check_icon(screen, chk_rect, uk.Theme.GOLD_BRIGHT)

            self.ui_rects[f'{field_id}_item_{actual_index}'] = item_rect
            uk.register_hoverable(item_rect)
            inner_y += item_h

        if can_scroll_down:
            arrow_rect = pygame.Rect(dropdown_rect.x + 6, inner_y, dropdown_rect.width - 12, arrow_h - 2)
            if arrow_rect.collidepoint(mouse_pos):
                uk.draw_rect_on(screen, uk.Theme.CARD_BG_HOVER[:3], arrow_rect, 0, 6)
            chev_rect = pygame.Rect(0, 0, 14, 14)
            chev_rect.center = arrow_rect.center
            _draw_chevron_down(screen, chev_rect, uk.Theme.TEXT_SECONDARY)
            self.ui_rects[f'{field_id}_scroll_down'] = arrow_rect
            uk.register_hoverable(arrow_rect)

        return dropdown_rect

    def _draw_pill_button(self, screen, rect, label, highlighted, danger=False, icon_fn=None):
        accent = uk.Theme.DANGER_BRIGHT if danger else uk.Theme.GOLD
        dim_bg = (32, 22, 22, 255) if danger else (28, 33, 44, 255)

        bg = dim_bg if highlighted else (22, 26, 35, 255)
        border = accent if highlighted else uk.Theme.CARD_BORDER
        border_width = 2 if highlighted else 1

        uk.draw_panel(screen, rect, bg=bg, border=border, border_width=border_width, radius=10, shadow=False)
        if highlighted:
            uk.draw_soft_glow(screen, rect.center, max(rect.w, rect.h) // 2, accent, max_alpha=28)

        label_color = accent if highlighted else uk.Theme.TEXT_SECONDARY

        if icon_fn is not None:
            icon_rect = pygame.Rect(0, 0, 16, 16)
            icon_rect.midleft = (rect.x + 18, rect.centery)
            icon_fn(screen, icon_rect, label_color)
            label_surf = self.font_medium.render(label, True, label_color)
            uk.blit_surface(screen, label_surf,
                            (icon_rect.right + 8, rect.centery - label_surf.get_height() // 2),
                            transient=True)
        else:
            label_surf = self.font_medium.render(label, True, label_color)
            uk.blit_surface(screen, label_surf, label_surf.get_rect(center=rect.center), transient=True)

        uk.register_hoverable(rect)

    def draw(self, screen: pygame.Surface):
        """Draw the configuration dialog"""
        if not self.active or not self.transition:
            return

        self.ui_rects = {}
        screen_width, screen_height = screen.get_size()
        mouse_pos = pygame.mouse.get_pos()

        # Dim the game behind the dialog
        overlay_rect = pygame.Rect(0, 0, screen_width, screen_height)
        uk.draw_rect_on(screen, (*uk.Theme.BG_BOTTOM, 190), overlay_rect, 0, 0)

        # Dialog panel
        dialog_width = 560
        dialog_height = 460
        dialog_x = (screen_width - dialog_width) // 2
        dialog_y = (screen_height - dialog_height) // 2
        dialog_rect = pygame.Rect(dialog_x, dialog_y, dialog_width, dialog_height)

        uk.draw_panel(screen, dialog_rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=2, radius=uk.Theme.RADIUS_PANEL)

        # Title
        title_surf = self.font_large.render("CONFIGURE ROOM TRANSITION", True, uk.Theme.GOLD_BRIGHT)
        title_top = dialog_rect.y + 20
        title_rect = title_surf.get_rect(centerx=dialog_rect.centerx, y=title_top)
        uk.blit_surface(screen, title_surf, title_rect, transient=True)

        divider_y = dialog_rect.y + 58
        uk.draw_line_on(screen, uk.Theme.PANEL_BORDER,
                        (dialog_rect.x + 24, divider_y), (dialog_rect.right - 24, divider_y), 1)

        # Save / Cancel row geometry is computed up front (even though it's
        # drawn after the fields below) so an open dropdown knows where the
        # button row starts and can flip itself upward instead of being
        # hidden underneath it.
        button_y = dialog_rect.bottom - 74
        button_h = 50

        # Fields
        x = dialog_rect.x + 24
        width = dialog_rect.width - 48
        y = dialog_rect.y + 72

        fields = [
            ('target_room', 'Target Room'),
            ('exit_direction', 'Exit Direction'),
            ('entry_direction', 'Entry Direction')
        ]

        # Find if any dropdown is open and which one — fields after it are
        # skipped so its popup list never gets drawn over by a later field
        # (same convention the old dialog used).
        open_dropdown_index = -1
        for idx, (field_id, _) in enumerate(fields):
            if self.dropdowns[field_id]:
                open_dropdown_index = idx
                break

        open_dropdown_to_draw = None

        for idx, (field_id, label) in enumerate(fields):
            if open_dropdown_index >= 0 and idx > open_dropdown_index:
                continue

            label_surf = self.font_small.render(label.upper(), True, uk.Theme.TEXT_MUTED)
            uk.blit_surface(screen, label_surf, (x, y), transient=True)
            y += label_surf.get_height() + 6

            toggle_rect = pygame.Rect(x, y, width, 44)
            is_open = self.dropdowns[field_id]
            is_hover = toggle_rect.collidepoint(mouse_pos)
            self._draw_dropdown_toggle(screen, toggle_rect, self._get_dropdown_value(field_id),
                                       is_open, is_hover)
            self.ui_rects[f'{field_id}_toggle'] = toggle_rect

            if is_open:
                # Defer the popup until after Save/Cancel so the popup has the
                # correct visual z-order.
                open_dropdown_to_draw = (toggle_rect, field_id)

            y += 44 + 22

        # Save / Cancel buttons, pinned to the bottom of the dialog
        btn_gap = 16
        button_width = (width - btn_gap) // 2

        save_rect = pygame.Rect(x, button_y, button_width, button_h)
        cancel_rect = pygame.Rect(x + button_width + btn_gap, button_y, button_width, button_h)

        self._draw_pill_button(screen, save_rect, "SAVE", save_rect.collidepoint(mouse_pos),
                               danger=False)
        self._draw_pill_button(screen, cancel_rect, "CANCEL", cancel_rect.collidepoint(mouse_pos),
                               danger=True)

        self.ui_rects['save_button'] = save_rect
        self.ui_rects['cancel_button'] = cancel_rect

        # Draw the open dropdown last so it appears above Save/Cancel when
        # its downward popup reaches into their visual area.
        if open_dropdown_to_draw is not None:
            popup_anchor, popup_field_id = open_dropdown_to_draw
            self._draw_dropdown_menu(
                screen,
                popup_anchor,
                popup_field_id,
                max_bottom=button_y - 8
            )

        # Every clickable widget above (dropdown toggles, list items, scroll
        # arrows, Save/Cancel) has registered itself via uk.register_hoverable
        # so it shows the hand cursor on hover. Deliberately NOT calling
        # uk.update_hover_cursor() here: ObjectEditor.draw() always calls
        # this dialog's draw() first and then resolves the frame's cursor
        # itself right after, once every other palette/panel widget has also
        # had a chance to register — calling it again here would clear the
        # shared hover list before that later call sees it.