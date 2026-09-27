import time

import pygame

import dev_tools.ui_kit as uk


class _MenuFont:
    """Adapts ui_kit.BitmapFont to the plain pygame.font.Font call shape —
    render(text, antialias, color) / size(text) — at one fixed pixel
    height. Same adapter room_editor.py and dev_menu.py use, so this menu's
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
# Small vector glyphs, drawn with the same line/circle primitives as every
# other ui_kit icon (draw_gear_icon, draw_close_icon, ...) so they read as
# part of the same family instead of a mismatched one-off.
# ---------------------------------------------------------------------------

def _draw_chevron_left(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    uk.draw_line_on(surface, color, (cx + s * 0.35, cy - s), (cx - s * 0.35, cy), width)
    uk.draw_line_on(surface, color, (cx - s * 0.35, cy), (cx + s * 0.35, cy + s), width)


def _draw_chevron_right(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    uk.draw_line_on(surface, color, (cx - s * 0.35, cy - s), (cx + s * 0.35, cy), width)
    uk.draw_line_on(surface, color, (cx + s * 0.35, cy), (cx - s * 0.35, cy + s), width)


def _draw_check_icon(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    uk.draw_line_on(surface, color, (cx - s, cy), (cx - s * 0.15, cy + s * 0.8), width)
    uk.draw_line_on(surface, color, (cx - s * 0.15, cy + s * 0.8), (cx + s, cy - s * 0.7), width)


class TransitionConfigMenu:
    """Menu for configuring room transitions.

    Same fields, navigation and confirm/cancel behavior as before — only
    the drawing changed, over to the shared ui_kit "modern DBZ" look
    (deep navy panel, gold accent, hairline card borders) used by every
    other dev-tool overlay (DevMenu, RoomEditor, NPCConfigMenu) instead of
    the old plain black-box / white-text menu.
    """

    DIRECTIONS = ['up', 'down', 'left', 'right']

    FIELD_LABELS = {
        'target_room': 'Target Room',
        'exit_direction': 'Exit Direction',
        'entry_direction': 'Entry Direction',
        'spawn_x': 'Spawn X',
        'spawn_y': 'Spawn Y',
        'width': 'Width',
        'height': 'Height',
    }

    # Fields that cycle through a fixed set of values (room / direction)
    # get little chevron hints drawn in their row; numeric fields don't.
    CHEVRON_FIELDS = {'target_room', 'exit_direction', 'entry_direction'}

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height

        self._bitmap_font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self.font_title = _MenuFont(self._bitmap_font, 22)
        self.font_label = _MenuFont(self._bitmap_font, 12)
        self.font_value = _MenuFont(self._bitmap_font, 16)
        self.font_hint = _MenuFont(self._bitmap_font, 12)

        self.active = False

        # Configuration
        self.target_room = None
        self.exit_direction = 'up'
        self.entry_direction = 'down'
        self.spawn_x = 400
        self.spawn_y = 300
        self.width = 64
        self.height = 64

        # Available rooms (will be populated from room manager)
        self.available_rooms = []

        # UI state
        self.selected_field = 0
        self.fields = ['target_room', 'exit_direction', 'entry_direction',
                       'spawn_x', 'spawn_y', 'width', 'height', 'confirm', 'cancel']
        self.editing_text = False
        self.text_input = ""
        self.text_field = ""

    def reset(self):
        """Reset configuration to defaults"""
        self.target_room = None
        self.exit_direction = 'up'
        self.entry_direction = 'down'
        self.spawn_x = 400
        self.spawn_y = 300
        self.width = 64
        self.height = 64
        self.selected_field = 0
        self.editing_text = False

    def toggle(self, available_rooms=None):
        """Toggle menu visibility"""
        self.active = not self.active
        if self.active:
            self.reset()
            if available_rooms:
                self.available_rooms = available_rooms

    def cycle_direction(self, field):
        """Cycle through direction options"""
        directions = self.DIRECTIONS
        current = getattr(self, field)
        current_index = directions.index(current)
        new_index = (current_index + 1) % len(directions)
        setattr(self, field, directions[new_index])

    def cycle_room(self, direction):
        """Cycle through available rooms"""
        if not self.available_rooms:
            return

        if self.target_room is None:
            self.target_room = self.available_rooms[0]
        else:
            try:
                current_index = self.available_rooms.index(self.target_room)
                new_index = (current_index + direction) % len(self.available_rooms)
                self.target_room = self.available_rooms[new_index]
            except ValueError:
                self.target_room = self.available_rooms[0]

    def handle_input(self, event):
        """Handle input events"""
        if not self.active:
            return None

        # Handle text input
        if self.editing_text:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    # Save value
                    try:
                        value = float(self.text_input)
                        setattr(self, self.text_field, value)
                    except:
                        pass

                    self.editing_text = False
                    self.text_input = ""
                    self.text_field = ""
                elif event.key == pygame.K_ESCAPE:
                    self.editing_text = False
                    self.text_input = ""
                    self.text_field = ""
                elif event.key == pygame.K_BACKSPACE:
                    self.text_input = self.text_input[:-1]
                else:
                    if len(self.text_input) < 10:
                        self.text_input += event.unicode
            return None

        # Handle menu navigation
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_UP:
                self.selected_field = (self.selected_field - 1) % len(self.fields)
            elif event.key == pygame.K_DOWN:
                self.selected_field = (self.selected_field + 1) % len(self.fields)
            elif event.key == pygame.K_RETURN or event.key == pygame.K_SPACE:
                field = self.fields[self.selected_field]

                if field == 'target_room':
                    self.cycle_room(1)
                elif field == 'exit_direction':
                    self.cycle_direction('exit_direction')
                elif field == 'entry_direction':
                    self.cycle_direction('entry_direction')
                elif field in ['spawn_x', 'spawn_y', 'width', 'height']:
                    self.editing_text = True
                    self.text_field = field
                    self.text_input = str(int(getattr(self, field)))
                elif field == 'confirm':
                    # Return configuration
                    if self.target_room is None:
                        return None  # Must select a room

                    config = {
                        'target_room': self.target_room,
                        'exit_direction': self.exit_direction,
                        'entry_direction': self.entry_direction,
                        'spawn_x': self.spawn_x,
                        'spawn_y': self.spawn_y,
                        'width': self.width,
                        'height': self.height
                    }
                    self.active = False
                    return config
                elif field == 'cancel':
                    self.active = False
                    return 'cancel'
            elif event.key == pygame.K_LEFT:
                field = self.fields[self.selected_field]
                if field == 'target_room':
                    self.cycle_room(-1)
                elif field == 'exit_direction':
                    self.cycle_direction('exit_direction')
                elif field == 'entry_direction':
                    self.cycle_direction('entry_direction')
            elif event.key == pygame.K_RIGHT:
                field = self.fields[self.selected_field]
                if field == 'target_room':
                    self.cycle_room(1)
                elif field == 'exit_direction':
                    self.cycle_direction('exit_direction')
                elif field == 'entry_direction':
                    self.cycle_direction('entry_direction')
            elif event.key == pygame.K_ESCAPE:
                self.active = False
                return 'cancel'

        return None

    # =========================================================================
    # Drawing — ui_kit styled
    # =========================================================================

    def _draw_field_row(self, screen, rect, value_text, selected, editing=False, chevrons=False):
        accent = uk.Theme.GOLD

        if editing:
            border, border_width = accent, 2
            bg = (32, 36, 58, 255)
        elif selected:
            border, border_width = accent, 2
            bg = (27, 31, 42, 255)
        else:
            border, border_width = uk.Theme.CARD_BORDER, 1
            bg = (20, 23, 32, 255)

        uk.draw_panel(screen, rect, bg=bg, border=border, border_width=border_width,
                      radius=8, shadow=False)

        if selected:
            uk.draw_soft_glow(screen, rect.center, max(rect.w, rect.h) // 2, accent, max_alpha=20)

        if editing:
            shown, color = self.text_input, uk.Theme.TEXT_PRIMARY
        else:
            shown, color = value_text, (uk.Theme.GOLD_BRIGHT if selected else uk.Theme.TEXT_SECONDARY)

        val_surf = self.font_value.render(shown, True, color)
        if chevrons and not editing:
            val_rect = val_surf.get_rect(center=rect.center)
        else:
            val_rect = val_surf.get_rect(x=rect.x + 14, centery=rect.centery)
        uk.blit_surface(screen, val_surf, val_rect, transient=True)

        if editing:
            # Blinking text caret, time-based since this menu isn't driven
            # by a per-frame update() call.
            if int(time.time() * 2) % 2 == 0:
                caret_x = val_rect.x + self.font_value.size(self.text_input)[0] + 2
                uk.draw_rect_on(screen, uk.Theme.TEXT_PRIMARY,
                                pygame.Rect(caret_x, rect.y + 8, 2, rect.h - 16), 0, 0)

        if chevrons and not editing:
            chev_color = accent if selected else uk.Theme.TEXT_DIM
            left_rect = pygame.Rect(rect.x + 6, rect.y, 22, rect.h)
            right_rect = pygame.Rect(rect.right - 28, rect.y, 22, rect.h)
            _draw_chevron_left(screen, left_rect, chev_color)
            _draw_chevron_right(screen, right_rect, chev_color)

    def _draw_pill_button(self, screen, rect, label, selected, danger=False, icon_fn=None, disabled=False):
        accent = uk.Theme.DANGER_BRIGHT if danger else uk.Theme.GOLD
        dim_bg = (32, 22, 22, 255) if danger else (28, 33, 44, 255)

        bg = dim_bg if selected else (22, 26, 35, 255)
        border = accent if selected else uk.Theme.CARD_BORDER
        border_width = 2 if selected else 1

        uk.draw_panel(screen, rect, bg=bg, border=border, border_width=border_width,
                      radius=10, shadow=False)
        if selected:
            uk.draw_soft_glow(screen, rect.center, max(rect.w, rect.h) // 2, accent, max_alpha=28)

        label_color = uk.Theme.TEXT_DIM if disabled else (accent if selected else uk.Theme.TEXT_SECONDARY)

        if icon_fn is not None:
            icon_rect = pygame.Rect(0, 0, 16, 16)
            icon_rect.midleft = (rect.x + 18, rect.centery)
            icon_fn(screen, icon_rect, label_color)
            label_surf = self.font_value.render(label, True, label_color)
            uk.blit_surface(screen, label_surf,
                            (icon_rect.right + 8, rect.centery - label_surf.get_height() // 2),
                            transient=True)
        else:
            label_surf = self.font_value.render(label, True, label_color)
            uk.blit_surface(screen, label_surf, label_surf.get_rect(center=rect.center), transient=True)

    def draw(self, screen):
        """Draw the configuration menu"""
        if not self.active:
            return

        # Dim the game behind the menu
        overlay_rect = pygame.Rect(0, 0, self.screen_width, self.screen_height)
        uk.draw_rect_on(screen, (*uk.Theme.BG_BOTTOM, 195), overlay_rect, 0, 0)

        # --- layout constants -------------------------------------------------
        padding = 24
        header_h = 58
        field_row_h = 40
        label_gap = 4
        row_gap = 12
        button_h = 48
        buttons_gap_above = 14
        buttons_gap_below = 18

        field_defs = [
            ('target_room', self.FIELD_LABELS['target_room'],
             self.target_room if self.target_room else "None"),
            ('exit_direction', self.FIELD_LABELS['exit_direction'], self.exit_direction.upper()),
            ('entry_direction', self.FIELD_LABELS['entry_direction'], self.entry_direction.upper()),
            ('spawn_x', self.FIELD_LABELS['spawn_x'], str(int(self.spawn_x))),
            ('spawn_y', self.FIELD_LABELS['spawn_y'], str(int(self.spawn_y))),
            ('width', self.FIELD_LABELS['width'], str(int(self.width))),
            ('height', self.FIELD_LABELS['height'], str(int(self.height))),
        ]

        label_h = self.font_label.size("A")[1]
        field_block_h = label_h + label_gap + field_row_h + row_gap
        fields_h = field_block_h * len(field_defs)

        if self.editing_text:
            hint_lines = ["Type a number and press ENTER", "ESC to cancel"]
        else:
            hint_lines = [
                "UP/DOWN: Navigate    ENTER/SPACE: Select or Cycle",
                "LEFT/RIGHT: Cycle value    ESC: Cancel",
            ]
        hint_line_h = self.font_hint.size("A")[1]
        hint_h = (hint_line_h + 4) * len(hint_lines)

        content_h = (header_h + fields_h + buttons_gap_above + button_h
                     + buttons_gap_below + hint_h)
        menu_width = 520
        menu_height = min(padding * 2 + content_h, self.screen_height - 40)
        menu_x = (self.screen_width - menu_width) // 2
        menu_y = max(20, (self.screen_height - menu_height) // 2)
        menu_rect = pygame.Rect(menu_x, menu_y, menu_width, menu_height)

        uk.draw_panel(screen, menu_rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD,
                      border_width=2, radius=uk.Theme.RADIUS_PANEL)

        # --- title --------------------------------------------------------
        title_surf = self.font_title.render("ROOM TRANSITION CONFIG", True, uk.Theme.GOLD_BRIGHT)
        icon_size = 26
        total_w = icon_size + 10 + title_surf.get_width()
        title_top = menu_rect.y + 20
        icon_rect = pygame.Rect(0, 0, icon_size, icon_size)
        icon_rect.topleft = (menu_rect.centerx - total_w // 2, title_top)
        icon_rect.centery = title_top + title_surf.get_height() // 2
        uk.draw_map_icon(screen, icon_rect, uk.Theme.GOLD)
        uk.blit_surface(screen, title_surf, (icon_rect.right + 10, title_top), transient=True)

        divider_y = menu_rect.y + header_h - 6
        uk.draw_line_on(screen, uk.Theme.PANEL_BORDER,
                        (menu_rect.x + padding, divider_y), (menu_rect.right - padding, divider_y), 1)

        # --- fields ---------------------------------------------------------
        x = menu_rect.x + padding
        width = menu_rect.width - padding * 2
        y = menu_rect.y + header_h

        for i, (field_id, label, value) in enumerate(field_defs):
            selected = (self.selected_field == i)
            editing = self.editing_text and self.text_field == field_id
            chevrons = field_id in self.CHEVRON_FIELDS

            label_surf = self.font_label.render(
                label.upper(), True, uk.Theme.GOLD if selected else uk.Theme.TEXT_MUTED)
            uk.blit_surface(screen, label_surf, (x, y), transient=True)
            y += label_surf.get_height() + label_gap

            row_rect = pygame.Rect(x, y, width, field_row_h)
            self._draw_field_row(screen, row_rect, value, selected, editing=editing, chevrons=chevrons)
            y += field_row_h + row_gap

        # --- confirm / cancel -------------------------------------------------
        y += buttons_gap_above - row_gap
        btn_gap = 16
        btn_w = (width - btn_gap) // 2
        confirm_rect = pygame.Rect(x, y, btn_w, button_h)
        cancel_rect = pygame.Rect(x + btn_w + btn_gap, y, btn_w, button_h)

        self._draw_pill_button(screen, confirm_rect, "CONFIRM AND PLACE",
                               selected=(self.selected_field == 7),
                               danger=False, icon_fn=_draw_check_icon,
                               disabled=(self.target_room is None))
        self._draw_pill_button(screen, cancel_rect, "CANCEL",
                               selected=(self.selected_field == 8), danger=True)
        y += button_h + buttons_gap_below

        # --- hint text -------------------------------------------------------
        for line in hint_lines:
            line_surf = self.font_hint.render(line, True, uk.Theme.TEXT_DIM)
            uk.blit_surface(screen, line_surf, line_surf.get_rect(centerx=menu_rect.centerx, y=y),
                            transient=True)
            y += line_surf.get_height() + 4