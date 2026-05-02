import pygame
import os
import sys
import math


class DevMenu:
    """
    Fullscreen sprite-based developer menu with custom font and background.
    Supports keyboard and mouse navigation, animated icons, scrolling background,
    and live config editing via text input overlay.
    """

    # -------------------------------------------------------------------------
    # Box width thresholds — used to pick a selection box size based on label length.
    # Padding accounts for the icon on the left + inner margins.
    # -------------------------------------------------------------------------
    TEXT_PADDING = 100
    BOX_WIDTHS = [
        (550, 600),  # (threshold, box_width) — widest
        (500, 550),
        (450, 500),
        (0,   450),  # default (smallest)
    ]

    def __init__(self, game_config, screen_width, screen_height, sound_manager=None):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False
        self.config = game_config
        self.sound_manager = sound_manager
        self.previous_context = None  # Saved music context restored on menu close

        # -----------------------------------------------------------------
        # Resolve asset root — works both from source and frozen (PyInstaller) builds.
        # -----------------------------------------------------------------
        if getattr(sys, 'frozen', False):
            application_path = os.path.dirname(sys.executable)
        else:
            application_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        self.assets_path = os.path.join(application_path, "assets", "ui", "dev_menu")
        self.icons_path = os.path.join(self.assets_path, "icons")

        self._load_assets()

        # -----------------------------------------------------------------
        # Menu state — 'main' is always the entry point.
        # selected_index = -1 means nothing is keyboard-selected yet;
        # hover_index = -1 means the mouse isn't over any item.
        # -----------------------------------------------------------------
        self.current_menu = 'main'
        self.selected_index = -1
        self.hover_index = -1

        # Main menu options
        self.main_options = [
            {'id': 'room_editor',     'label': 'ROOM EDITOR',     'icon': 'room'},
            {'id': 'sprite_editor',   'label': 'SPRITE EDITOR',   'icon': 'sprite'},
            {'id': 'cutscene_editor', 'label': 'CUTSCENE EDITOR', 'icon': 'cutscene'},
            {'id': 'config',          'label': 'CONFIGURATION',   'icon': 'config'},
            {'id': 'close',           'label': 'CLOSE MENU',      'icon': 'close'},
        ]

        # Config submenu
        self.config_options = [
            {'id': 'xp_config',             'label': 'XP CONFIGURATION', 'icon': 'xp'},
            {'id': 'transformation_config', 'label': 'TRANSFORMATION',   'icon': 'transform'},
            {'id': 'back',                  'label': 'BACK',              'icon': 'back'},
        ]

        # XP config — value_key links each entry to a live attribute on game_config
        self.xp_config_options = [
            {'id': 'max_level',   'label': 'Max Level',           'value_key': 'max_level'},
            {'id': 'base_xp',     'label': 'Base XP Required',    'value_key': 'base_xp_requirement'},
            {'id': 'xp_scaling',  'label': 'XP Scaling Factor',   'value_key': 'xp_scaling_factor'},
            {'id': 'stat_points', 'label': 'Stat Points/Level',   'value_key': 'stat_points_per_level'},
            {'id': 'back',        'label': 'BACK',                 'icon': 'back'},
        ]

        # -----------------------------------------------------------------
        # Text-input state — active while the user is typing a new config value.
        # -----------------------------------------------------------------
        self.editing_text = False
        self.text_input = ""
        self.editing_field = None

        # Animation timers
        self.anim_timer = 0
        self.cursor_blink = 0
        self.icon_bob_offset = [0.0] * 10  # One bob value per visible menu slot

        # Rebuilt every frame in draw() — holds rects for mouse hit-testing
        self.clickable_rects = []

    # =========================================================================
    # Asset loading
    # =========================================================================

    def _load_assets(self):
        """Load all sprite assets and fonts. Falls back gracefully if files are missing."""
        font_path = os.path.join(self.assets_path, "dev_font.ttf")

        # Use custom font when available; fall back to pygame's built-in font.
        font_cls = pygame.font.Font
        font_src = font_path if os.path.exists(font_path) else None
        self.font_title  = font_cls(font_src, 48)
        self.font_large  = font_cls(font_src, 32)
        self.font_medium = font_cls(font_src, 24)
        self.font_small  = font_cls(font_src, 18)

        # Background — scrolling sprite or procedural gradient fallback
        bg_path = os.path.join(self.assets_path, "background.png")
        if os.path.exists(bg_path):
            self.background = pygame.image.load(bg_path).convert()
            self.background = pygame.transform.scale(
                self.background, (self.screen_width, self.screen_height)
            )
            # Second copy enables seamless horizontal loop
            self.background_copy = self.background.copy()
            self.bg_scroll_x = 0
            self.bg_scroll_speed = 50  # pixels per second
        else:
            self.background = self._create_gradient_background()
            self.background_copy = None
            self.bg_scroll_x = 0
            self.bg_scroll_speed = 0

        self._load_ui_elements()

    def _create_gradient_background(self):
        """
        Procedural fallback background: dark-blue-to-black gradient overlaid
        with a subtle grid, evoking a classic debug / retro feel.
        """
        bg = pygame.Surface((self.screen_width, self.screen_height))

        for y in range(self.screen_height):
            t = 1 - y / self.screen_height  # 1 at top, 0 at bottom
            pygame.draw.line(bg, (int(10 * t), int(20 * t), int(40 * t)),
                             (0, y), (self.screen_width, y))

        grid_color = (20, 30, 50)
        for x in range(0, self.screen_width, 40):
            pygame.draw.line(bg, grid_color, (x, 0), (x, self.screen_height), 1)
        for y in range(0, self.screen_height, 40):
            pygame.draw.line(bg, grid_color, (0, y), (self.screen_width, y), 1)

        return bg

    def _load_ui_elements(self):
        """Load or generate selection-box sprites at each supported width."""
        box_path = os.path.join(self.assets_path, "selection_box.png")

        if os.path.exists(box_path):
            base = pygame.image.load(box_path).convert_alpha()
            self.selection_box        = pygame.transform.scale(base, (450, 60))
            self.selection_box_medium = pygame.transform.scale(base, (500, 60))
            self.selection_box_wide   = pygame.transform.scale(base, (550, 60))
            self.selection_box_xwide  = pygame.transform.scale(base, (600, 60))
        else:
            self.selection_box        = self._create_selection_box(450)
            self.selection_box_medium = self._create_selection_box(500)
            self.selection_box_wide   = self._create_selection_box(550)
            self.selection_box_xwide  = self._create_selection_box(600)

        self._load_icons()

    def _create_selection_box(self, width):
        """
        Procedural selection-box sprite with a layered gold glow effect.
        The outer rings fade out (decreasing alpha) to simulate ambient glow.
        """
        box = pygame.Surface((width, 60), pygame.SRCALPHA)

        # Outer glow — 5 concentric rings, increasingly transparent outward
        for i in range(5):
            pygame.draw.rect(box, (255, 215, 0, 50 - i * 10),
                             (i, i, width - i * 2, 60 - i * 2), 2)

        # Filled interior + solid border
        pygame.draw.rect(box, (255, 215, 0, 100), (5, 5, width - 10, 50))
        pygame.draw.rect(box, (255, 215, 0, 255), (5, 5, width - 10, 50), 2)

        return box

    def _load_icons(self):
        """
        Load icon PNGs from the icons directory.
        Accepts three common filename conventions (e.g. room.png / icon_room.png / room_icon.png).
        Falls back to a colored circle if no file is found, then saves it for next time.
        """
        self.icons = {}

        icon_types = ['room', 'sprite', 'cutscene', 'config', 'close', 'xp', 'transform', 'back']

        # Fallback colors keyed by icon type — used when no PNG is present
        icon_colors = {
            'room':      (100, 255, 100),
            'sprite':    (100, 200, 255),
            'cutscene':  (255, 160,  80),
            'config':    (100, 100, 255),
            'close':     (255,  50,  50),
            'xp':        (255, 215,   0),
            'transform': (255, 165,   0),
            'back':      (150, 150, 150),
        }

        os.makedirs(self.icons_path, exist_ok=True)

        for icon_type in icon_types:
            # Try each naming convention in order
            candidates = [
                f"{icon_type}.png",
                f"icon_{icon_type}.png",
                f"{icon_type}_icon.png",
            ]

            loaded = False
            for filename in candidates:
                path = os.path.join(self.icons_path, filename)
                if os.path.exists(path):
                    try:
                        icon = pygame.image.load(path).convert_alpha()
                        self.icons[icon_type] = pygame.transform.scale(icon, (32, 32))
                        loaded = True
                        break
                    except Exception:
                        pass

            if not loaded:
                # Generate a simple colored circle and cache it to disk
                fallback = self._create_icon(icon_colors.get(icon_type, (255, 255, 255)))
                self.icons[icon_type] = fallback
                try:
                    pygame.image.save(fallback,
                                      os.path.join(self.icons_path, f"{icon_type}_fallback.png"))
                except Exception:
                    pass

    def _create_icon(self, color):
        """Simple 32×32 circle icon with a white border — used as a fallback sprite."""
        icon = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.draw.circle(icon, color, (16, 16), 14)
        pygame.draw.circle(icon, (255, 255, 255), (16, 16), 14, 2)
        return icon

    # =========================================================================
    # Public interface
    # =========================================================================

    def toggle(self):
        """
        Show or hide the menu.
        On open: resets state and switches to menu music.
        On close: restores the previous music context.
        """
        self.active = not self.active

        if self.active:
            self.current_menu = 'main'
            self.selected_index = -1
            self.hover_index = -1
            self.editing_text = False
            self.icon_bob_offset = [0.0] * 10

            if self.sound_manager:
                self.previous_context = self.sound_manager.get_current_context()
                self.sound_manager.set_context_immediate('menu')
        else:
            if self.sound_manager:
                ctx = self.previous_context or 'exploration'
                self.sound_manager.set_context(ctx, force=True)

    def handle_input(self, event):
        """
        Route input events depending on the current interaction mode.
        Returns an action string (e.g. 'open_room_editor') when the caller
        needs to respond, otherwise returns None.
        """
        if not self.active:
            return None

        # -- Text input mode --------------------------------------------------
        # All keystrokes are consumed here while the user is typing a value.
        if self.editing_text:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    self._commit_text_input()
                elif event.key == pygame.K_ESCAPE:
                    self._cancel_text_input()
                elif event.key == pygame.K_BACKSPACE:
                    self.text_input = self.text_input[:-1]
                elif len(self.text_input) < 20 and event.unicode.isprintable():
                    self.text_input += event.unicode
            return None

        # -- Mouse click ------------------------------------------------------
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for entry in self.clickable_rects:
                if entry['rect'].collidepoint(event.pos):
                    self.selected_index = entry['index']
                    return self._handle_selection()

        # -- Mouse hover ------------------------------------------------------
        if event.type == pygame.MOUSEMOTION:
            self.hover_index = -1
            for entry in self.clickable_rects:
                if entry['rect'].collidepoint(event.pos):
                    self.hover_index = entry['index']
                    break

        # -- Keyboard navigation ----------------------------------------------
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if self.current_menu == 'main':
                    self.active = False
                else:
                    self._go_back()
                return None

            options = self._get_current_options()

            if event.key in (pygame.K_UP, pygame.K_w):
                # Wrap to last item when nothing is selected yet
                if self.selected_index == -1:
                    self.selected_index = len(options) - 1
                else:
                    self.selected_index = (self.selected_index - 1) % len(options)

            elif event.key in (pygame.K_DOWN, pygame.K_s):
                # Wrap to first item when nothing is selected yet
                if self.selected_index == -1:
                    self.selected_index = 0
                else:
                    self.selected_index = (self.selected_index + 1) % len(options)

            elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                if self.selected_index != -1:
                    return self._handle_selection()

        return None

    def update(self, dt):
        """
        Advance all animations.
        Called every frame with the elapsed time in seconds (dt).
        """
        if not self.active:
            return

        self.anim_timer += dt
        self.cursor_blink += dt

        # Bob the icon for the active (selected or hovered) item via a sine wave.
        # All other icons damp back to zero with a simple decay factor.
        for i in range(len(self._get_current_options())):
            if i in (self.selected_index, self.hover_index):
                self.icon_bob_offset[i] = math.sin(self.anim_timer * 3) * 5
            else:
                self.icon_bob_offset[i] *= 0.9  # Smooth settle back to rest

        # Advance the horizontally-scrolling background loop
        if self.background_copy is not None:
            self.bg_scroll_x -= self.bg_scroll_speed * dt
            if self.bg_scroll_x <= -self.screen_width:
                self.bg_scroll_x = 0

    def draw(self, screen):
        """
        Render the full dev menu onto the provided screen surface.
        Rebuilds clickable_rects each frame so hit-testing is always in sync
        with the rendered positions.
        """
        if not self.active:
            return

        self.clickable_rects = []  # Rebuilt fresh every frame

        # -- Background -------------------------------------------------------
        # Dark overlay first so the game world is dimmed behind the menu.
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 200))
        screen.blit(overlay, (0, 0))

        # Scrolling background (or static gradient fallback)
        if self.background_copy is not None:
            screen.blit(self.background,      (int(self.bg_scroll_x), 0))
            screen.blit(self.background_copy, (int(self.bg_scroll_x + self.screen_width), 0))
        else:
            screen.blit(self.background, (0, 0))

        # -- Title ------------------------------------------------------------
        title_text = self._get_menu_title()
        title_surf   = self.font_title.render(title_text, True, (255, 215, 0))
        title_shadow = self.font_title.render(title_text, True, (0, 0, 0))

        title_x = (self.screen_width - title_surf.get_width()) // 2
        screen.blit(title_shadow, (title_x + 3, 43))  # Drop shadow offset
        screen.blit(title_surf,   (title_x,     40))

        # -- Menu items -------------------------------------------------------
        options  = self._get_current_options()
        start_y  = 150
        spacing  = 80

        for i, option in enumerate(options):
            y_pos = start_y + i * spacing
            is_highlighted = (i == self.selected_index or i == self.hover_index)

            self._draw_menu_item(screen, option, i, y_pos, is_highlighted)

        # -- Text input overlay -----------------------------------------------
        if self.editing_text:
            self._draw_text_input(screen)

    # =========================================================================
    # Private helpers
    # =========================================================================

    def _get_label(self, option):
        """
        Return the display label for a menu option.
        Config fields (those with a 'value_key') append their live value.
        """
        label = option['label']
        if 'value_key' in option:
            value = getattr(self.config, option['value_key'], '?')
            label = f"{label}: {value}"
        return label

    def _calc_box_width(self, label):
        """
        Choose a selection-box width based on the rendered pixel width of the label.
        Cycles through BOX_WIDTHS thresholds, which account for icon + margin padding.
        """
        text_w = self.font_large.size(label)[0]  # Faster than rendering a full surface
        total  = text_w + self.TEXT_PADDING
        for threshold, width in self.BOX_WIDTHS:
            if total > threshold:
                return width
        return 450  # Should never reach here, but safe default

    def _get_selection_box(self, box_width):
        """Map a box_width integer to the matching prebuilt surface."""
        return {
            600: self.selection_box_xwide,
            550: self.selection_box_wide,
            500: self.selection_box_medium,
        }.get(box_width, self.selection_box)

    def _draw_menu_item(self, screen, option, index, y_pos, is_highlighted):
        """
        Draw a single menu row: selection box, icon, and label text.
        Registers a clickable rect for mouse hit-testing regardless of highlight state.
        """
        label     = self._get_label(option)
        box_width = self._calc_box_width(label) if is_highlighted else 450
        box_x     = (self.screen_width - box_width) // 2

        # Always register the row as clickable (even un-highlighted items need it)
        self.clickable_rects.append({
            'rect':  pygame.Rect(box_x, y_pos, box_width, 60),
            'index': index,
        })

        if is_highlighted:
            # Pulse the box size slightly using a sine wave for a breathing effect
            pulse = abs(int((self.anim_timer * 2) % 2 - 1) * 10)
            selection_box = self._get_selection_box(box_width)
            screen.blit(selection_box, (box_x - pulse, y_pos - pulse // 2))

        # -- Icon -------------------------------------------------------------
        if 'icon' in option and option['icon'] in self.icons:
            icon_x = box_x + 20
            icon_y = y_pos + 10 + int(self.icon_bob_offset[index])

            if is_highlighted:
                # Pulsing gold glow behind the icon
                glow_size = 40
                glow_surf = pygame.Surface((glow_size, glow_size), pygame.SRCALPHA)
                glow_alpha = int(100 + 50 * math.sin(self.anim_timer * 4))
                pygame.draw.circle(glow_surf, (255, 215, 0, glow_alpha),
                                   (glow_size // 2, glow_size // 2), glow_size // 2)
                screen.blit(glow_surf,
                            (icon_x - (glow_size - 32) // 2,
                             icon_y - (glow_size - 32) // 2))

            screen.blit(self.icons[option['icon']], (icon_x, icon_y))

        # -- Label ------------------------------------------------------------
        color  = (255, 255, 255) if is_highlighted else (180, 180, 180)
        font   = self.font_large  if is_highlighted else self.font_medium

        label_surf   = font.render(label, True, color)
        label_shadow = font.render(label, True, (0, 0, 0))

        label_x = box_x + 70
        label_y = y_pos + 15

        # If the label is too wide for its box, scale it down proportionally
        max_w = box_width - 80
        if label_surf.get_width() > max_w:
            scale = max_w / label_surf.get_width()
            new_size = (int(label_surf.get_width() * scale),
                        int(label_surf.get_height() * scale))
            label_surf   = pygame.transform.scale(label_surf,   new_size)
            label_shadow = pygame.transform.scale(label_shadow, new_size)

        # Drop shadow, then main text
        screen.blit(label_shadow, (label_x + 2, label_y + 2))
        screen.blit(label_surf,   (label_x,     label_y))

        # Subtle gold glow overlay on highlighted items
        if is_highlighted:
            glow = font.render(label, True, (255, 215, 0, 50))
            if label_surf.get_width() != glow.get_width():  # Was scaled
                glow = pygame.transform.scale(glow, label_surf.get_size())
            screen.blit(glow, (label_x, label_y))

    def _draw_text_input(self, screen):
        """
        Render the text-input overlay used when editing a config value.
        The blinking cursor is driven by cursor_blink — it toggles every 0.5 s.
        """
        # Dim the menu behind the input box
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        screen.blit(overlay, (0, 0))

        box_w = 600
        box_h = 120
        box_x = (self.screen_width - box_w) // 2
        box_y = (self.screen_height - box_h) // 2

        pygame.draw.rect(screen, (20, 20, 40), (box_x, box_y, box_w, box_h))
        pygame.draw.rect(screen, (255, 215, 0), (box_x, box_y, box_w, box_h), 3)

        # "Enter new value:" prompt
        prompt = self.font_small.render("Enter new value:", True, (200, 200, 200))
        screen.blit(prompt, (box_x + (box_w - prompt.get_width()) // 2, box_y + 20))

        # Input field background
        input_bg = pygame.Rect(box_x + 20, box_y + 50, box_w - 40, 40)
        pygame.draw.rect(screen, (40, 40, 60), input_bg)
        pygame.draw.rect(screen, (255, 255, 255), input_bg, 2)

        # Current text + blinking underscore cursor
        cursor = "_" if int(self.cursor_blink * 2) % 2 == 0 else ""
        input_surf = self.font_medium.render(self.text_input + cursor, True, (255, 255, 255))
        screen.blit(input_surf,
                    (box_x + (box_w - input_surf.get_width()) // 2, box_y + 55))

        # Keyboard hint at the bottom of the box
        hint = self.font_small.render("ENTER to confirm | ESC to cancel", True, (150, 150, 150))
        screen.blit(hint,
                    (box_x + (box_w - hint.get_width()) // 2, box_y + box_h - 30))

    def _commit_text_input(self):
        """Apply the typed value to the config field and exit text-input mode."""
        try:
            value = float(self.text_input)
            setattr(self.config, self.editing_field, value)
        except ValueError:
            pass  # Ignore non-numeric input — leave the original value intact
        self._cancel_text_input()

    def _cancel_text_input(self):
        """Discard the current text input and return to normal menu navigation."""
        self.editing_text = False
        self.text_input = ""
        self.editing_field = None

    def _get_current_options(self):
        """Return the option list for whichever submenu is currently active."""
        return {
            'main':      self.main_options,
            'config':    self.config_options,
            'xp_config': self.xp_config_options,
        }.get(self.current_menu, [])

    def _handle_selection(self):
        """
        Execute the action for the currently selected menu item.
        Returns an action string when the caller (game loop) needs to respond,
        e.g. 'open_room_editor'. Returns None for internal navigation.
        """
        options = self._get_current_options()
        if not (0 <= self.selected_index < len(options)):
            return None

        selected  = options[self.selected_index]
        option_id = selected['id']

        if self.current_menu == 'main':
            if option_id == 'room_editor':
                return 'open_room_editor'
            elif option_id == 'sprite_editor':
                return 'open_sprite_editor'
            elif option_id == 'cutscene_editor':
                return 'open_cutscene_editor'
            elif option_id == 'config':
                self._enter_menu('config')
            elif option_id == 'close':
                self.active = False

        elif self.current_menu == 'config':
            if option_id == 'xp_config':
                self._enter_menu('xp_config')
            elif option_id == 'transformation_config':
                pass  # TODO: implement transformation config submenu
            elif option_id == 'back':
                self._go_back()

        elif self.current_menu == 'xp_config':
            if option_id == 'back':
                self._go_back()
            elif 'value_key' in selected:
                # Open text-input mode pre-filled with the current value
                self.editing_text = True
                self.editing_field = selected['value_key']
                self.text_input = str(getattr(self.config, self.editing_field, ''))

        return None

    def _enter_menu(self, menu_name):
        """Switch to a submenu and reset selection state."""
        self.current_menu = menu_name
        self.selected_index = -1
        self.hover_index = -1

    def _go_back(self):
        """
        Navigate up one level in the menu hierarchy.
        xp_config → config → main.
        """
        parent = {'xp_config': 'config', 'config': 'main'}
        self.current_menu = parent.get(self.current_menu, 'main')
        self.selected_index = -1
        self.hover_index = -1

    def _get_menu_title(self):
        """Return the display title for the active submenu."""
        return {
            'main':      'DEVELOPER MENU',
            'config':    'CONFIGURATION',
            'xp_config': 'XP SYSTEM CONFIG',
            'room_editor': 'ROOM EDITOR',
        }.get(self.current_menu, 'DEV MENU')