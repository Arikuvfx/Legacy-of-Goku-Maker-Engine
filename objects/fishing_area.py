"""
objects/fishing_area.py

FishingArea — a simple rectangular trigger area. When the player overlaps
it and presses E, game.py should open FishingArea.prompt (a FishingPrompt,
defined below) asking "Do you want to go fishing?". On "Yes", game.py
calls player.start_fishing_jump(...) (see player.py) to play the
jump-arc-then-disappear sequence. On "No" (or Esc), nothing happens.

FishingPrompt lives in this same file (rather than its own module) since
it only exists to serve FishingArea — see the bottom of this file.

Modeled after objects/world_map.py's single-class-plus-manager shape so it
drops into the room/editor save-load system the same way.
"""

import pygame
from config.settings import RENDER_SCALE
from core.draw_layers import DrawLayer


class FishingArea:
    """A rectangular area the player can stand in and interact with (E) to fish."""

    # Cardinal facing → (dx, dy) unit vector, screen/world axes (+y is down).
    # This is the per-instance jump direction set in the room editor (hover
    # the fishing area + press R to cycle) -- see ObjectEditor's direction
    # arrow indicator and game.py's _start_fishing_jump, which passes
    # area.direction straight through to player.start_fishing_jump().
    DIRECTION_VECTORS = {
        'up':    (0, -1),
        'down':  (0, 1),
        'left':  (-1, 0),
        'right': (1, 0),
    }

    def __init__(self, x, y, width=48, height=48, direction='down'):
        self.x = float(x)
        self.y = float(y)
        self.width = width
        self.height = height
        self.active = True
        self.direction = direction if direction in self.DIRECTION_VECTORS else 'down'

        # Ground-level, not y-sorted — same treatment as the flat 'world_map'
        # ground sprite variant, since this is a floor marker, not a body.
        self.draw_layer = DrawLayer.GROUND
        self.y_sort = False

        # Cached translucent placeholder surface, built lazily and only
        # rebuilt if the target size changes. GPUScreen (see gpu_renderer.py)
        # caches uploaded textures by the Python id() of the Surface object
        # passed to blit(), so re-creating a brand new Surface every frame
        # would silently leak a fresh texture into that cache every single
        # frame instead of reusing one — same fix as
        # WorldMapObject._get_scaled_sprite.
        self._placeholder_surface = None
        self._placeholder_size = None

    def get_direction_vector(self):
        """(dx, dy) unit vector for self.direction — see DIRECTION_VECTORS."""
        return self.DIRECTION_VECTORS.get(self.direction, (0, 1))

    def cycle_direction(self, step=1):
        """Advance (step=1) or retreat (step=-1) to the next cardinal
        direction, in cycle order. Bound to the room editor's 'R' key
        (Shift+R to go backward) while hovering a fishing area."""
        order = ['down', 'left', 'up', 'right']
        idx = order.index(self.direction) if self.direction in order else 0
        self.direction = order[(idx + step) % len(order)]

    def get_sort_key(self):
        y = self.y if self.y_sort else 0
        return (self.draw_layer, y)

    def get_rect(self):
        """World-space rect used for player-overlap checks (game.py) and
        editor hit-testing. Not a solid collider — this never blocks movement."""
        return pygame.Rect(
            self.x - self.width // 2,
            self.y - self.height // 2,
            self.width,
            self.height,
        )

    def get_collision_rect(self):
        """No collision — the player should be able to walk into/through this area."""
        return None

    def player_is_inside(self, player):
        """Convenience check for game.py's interaction loop."""
        player_rect = player.get_collision_rect() if hasattr(player, 'get_collision_rect') else \
            pygame.Rect(player.x - 1, player.y - 1, 2, 2)
        return self.get_rect().colliderect(player_rect)

    def update(self, dt, player=None):
        pass

    def _get_placeholder_surface(self, w, h):
        """Translucent blue water-ish box representing this area in the
        room editor. Cached per size (see the __init__ note) so draw()
        reuses the same Surface object every frame instead of allocating
        a new one."""
        if self._placeholder_size != (w, h):
            overlay = pygame.Surface((w, h), pygame.SRCALPHA)
            overlay.fill((60, 140, 220, 90))
            pygame.draw.rect(overlay, (60, 140, 220, 200), overlay.get_rect(), 2)
            self._placeholder_surface = overlay
            self._placeholder_size = (w, h)
        return self._placeholder_surface

    def draw(self, screen, camera, colors=None, dev_mode=False):
        """dev_mode=True (room editor only -- see ObjectEditor.draw_fishing_areas)
        draws a translucent box so the area is visible/selectable while
        authoring a room; ObjectEditor draws the facing-direction arrow on
        top of it separately.

        In real gameplay (dev_mode=False, the default -- includes F2 test
        mode, which reuses this exact same draw path) the area is
        completely invisible: it's a trigger zone the player walks through,
        not something meant to ever appear on screen during play.
        """
        if not dev_mode:
            return
        sx = int(self.x * RENDER_SCALE - camera.x)
        sy = int(self.y * RENDER_SCALE - camera.y)
        w = int(self.width * RENDER_SCALE)
        h = int(self.height * RENDER_SCALE)
        placeholder = self._get_placeholder_surface(w, h)
        screen.blit(placeholder, placeholder.get_rect(center=(sx, sy)))

    def to_dict(self):
        return {
            'type': 'fishing_area',
            'x': self.x, 'y': self.y,
            'width': self.width, 'height': self.height,
            'direction': self.direction,
        }

    @staticmethod
    def from_dict(data):
        return FishingArea(
            data.get('x', 0), data.get('y', 0),
            data.get('width', 48), data.get('height', 48),
            data.get('direction', 'down'),
        )


class FishingAreaManager:
    def __init__(self):
        self._areas: dict = {}

    def get_fishing_areas(self, room_name):
        return self._areas.get(room_name, [])

    def add_fishing_area(self, room_name, area):
        self._areas.setdefault(room_name, []).append(area)

    def remove_fishing_area(self, room_name, area):
        room = self._areas.get(room_name, [])
        if area in room:
            room.remove(area)

    def clear_room(self, room_name):
        self._areas[room_name] = []

    def save_to_dict(self):
        return {room: [a.to_dict() for a in areas]
                for room, areas in self._areas.items()}

    def load_from_dict(self, data):
        self._areas = {
            room: [FishingArea.from_dict(a) for a in areas]
            for room, areas in data.items()
        }


class FishingPrompt:
    """Yes/No textbox for the fishing-area interaction.

    This intentionally doesn't draw its own box/text — it wraps a
    ui.dialogue.DialogueChoiceMenu and drives it with a fixed ["Yes", "No"]
    option list. That gets the fishing prompt the exact same look and feel
    as every other in-game choice (the 'dialogue_choice' event action,
    etc.): the textbox.png sprite frame, the bitmap font, the pop-in scale
    animation, the per-character typewriter reveal, and the blinking
    arrow/star selection indicator — for free, and it stays in sync with
    that styling automatically if DialogueChoiceMenu's look ever changes,
    instead of drifting out of sync the way a hand-rolled pygame.font +
    colored-rect box (the old implementation here) inevitably would.

    Usage from game.py, roughly:

        from objects.fishing_area import FishingArea, FishingAreaManager, FishingPrompt
        self.fishing_prompt = FishingPrompt(self.screen_width, self.screen_height)

        # when player overlaps a FishingArea and presses E:
        self.fishing_prompt.open(
            "Do you want to go fishing?",
            on_yes=lambda: self.player.start_fishing_jump(on_complete=...),
            player=self.player,
        )

        # in the main loop, before/while handling input:
        if self.fishing_prompt.active:
            self.fishing_prompt.handle_event(event)
            # swallow other gameplay input this frame

        # each frame:
        self.fishing_prompt.update(dt)

        # in the draw pass, on top of everything else:
        self.fishing_prompt.draw(screen)
    """

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height

        # Imported here rather than at module scope purely to keep this
        # module's only "hard" top-level dependency being pygame/config/core,
        # same as the rest of this file — everything else about the wiring
        # (game.py importing both ui.dialogue and objects.fishing_area at
        # its own top level already) means there's no real circular-import
        # risk either way, this is just being conservative about it.
        from ui.dialogue import DialogueChoiceMenu

        # These override DialogueChoiceMenu's defaults *only* for this
        # instance — the shared dialogue_choice_menu used for NPC/event
        # choices elsewhere in game.py is untouched. Tweak these to change
        # the fishing prompt's text position/gaps without affecting any
        # other choice menu in the game:
        self._menu = DialogueChoiceMenu(
            screen_width, screen_height,
            row_gap=20,          # vertical gap between "Yes" and "No" (and between prompt and first option)
            pad_y_min=64,        # padding above the prompt line / below the last option
            bottom_margin=120,   # distance from the bottom of the screen to the box
            text_x_nudge=0,     # horizontal shift of option text (room for the arrow)
            arrow_spacing=-5,    # gap between the arrow/star indicator and the option text
            arrow_y_offset=5,    # ← vertical nudge of the arrow/star (+down / -up), fishing prompt only
            word_gap=3,  # ← width of the space between words
            # Match DialogueBox's box/text size exactly — same textbox.png,
            # same 0.3-of-screen-height target, same round()-to-nearest-scale
            # math as the "You found a/an X!" catch notice (and the "You
            # found nothing!" one) use, instead of DialogueChoiceMenu's
            # smaller 0.24-ratio default. Since the bitmap font is baked at
            # this same scale, the prompt's text comes out the same size too.
            box_height_ratio=0.3,
            box_scale_round=True,
        )
        self.on_yes = None
        self.on_no = None

    @property
    def active(self):
        return self._menu.active

    def open(self, question, on_yes=None, on_no=None, player=None):
        """Show the prompt. on_yes/on_no are optional zero-arg callbacks.

        player is optional but should be passed whenever available: it's
        used only to snap the player to their standing-idle pose/animation
        the moment the prompt opens (via Player.enter_idle()), so they're
        not left mid-stride while choosing Yes/No. Safe to omit — if
        player is None, or doesn't have enter_idle(), this just does
        nothing extra.
        """
        if player is not None:
            enter_idle = getattr(player, 'enter_idle', None)
            if callable(enter_idle):
                enter_idle()

        self.on_yes = on_yes
        self.on_no = on_no
        self._menu.open(["Yes", "No"], prompt=question, on_choice=self._on_choice)

    def close(self):
        self._menu.close()
        self.on_yes = None
        self.on_no = None

    def _on_choice(self, index):
        # DialogueChoiceMenu calls this with the index into ["Yes", "No"]
        # once the player confirms with E; it has already deactivated
        # itself by this point (see DialogueChoiceMenu.handle_input).
        callback = self.on_yes if index == 0 else self.on_no
        self.on_yes = None
        self.on_no = None
        if callable(callback):
            callback()

    def handle_event(self, event):
        """Feed pygame events here while self.active is True. Returns True if
        the event was consumed (so game.py can skip other input handling)."""
        if not self._menu.active:
            return False

        # DialogueChoiceMenu only knows Up/Down to move and E to confirm —
        # it has no notion of "cancel". The fishing prompt still wants Esc
        # to behave like picking "No" (same as before), so handle that
        # here before handing everything else off to the menu.
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._menu.close()
            callback, self.on_no = self.on_no, None
            self.on_yes = None
            if callable(callback):
                callback()
            return True

        self._menu.handle_input(event)
        return event.type == pygame.KEYDOWN

    def update(self, dt):
        self._menu.update(dt)

    def draw(self, screen):
        self._menu.draw(screen)