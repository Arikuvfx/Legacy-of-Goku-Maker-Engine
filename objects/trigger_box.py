"""
objects/trigger_box.py — Trigger Box room objects.

Two variants, matching the spec:
  OverlapTriggerBox — fires as soon as the player's rect overlaps the box.
  KeyTriggerBox      — fires only if the player is overlapping AND presses
                       the interact key ("E").

Both support `once=True` (fire a single time, then stay inert) or
`once=False` (fire every frame/press the condition holds — useful for
things like a switch-style pressure plate).

Each box also carries its own `conditions`/`actions` — the exact lists
core.event_editor.EventEditorWindow builds and hands back through its
on_save callback. `conditions` are an *extra* gate evaluated on top of the
overlap/key + once logic (e.g. "only fire if flag X is set"); `actions`
are what should actually run once the box fires. Wire the popup up to a
box with open_event_editor(); wire firing up in the room's update loop:

    for box in room.trigger_boxes:
        if box.should_fire(self.player, keys_pressed=self.pressed_keys,
                            evaluate_conditions=self.flag_manager.evaluate_conditions):
            self.flag_manager.mark_box_triggered(box.box_id)
            self.run_actions(box.actions)   # however your action runner is named

To open the editor for a box (e.g. from a dev-mode object inspector):

    box.open_event_editor(self.event_editor)
    # each frame while self.event_editor.active:
    self.event_editor.handle_input(event)
    self.event_editor.draw(screen)
"""

import pygame

# draw_trigger_box() below used to build a fresh pygame.font.Font every
# call — i.e. every box, every frame, in dev mode. Font construction
# re-parses the font file and is far pricier than reusing one you already
# have, so this is cached module-wide instead (same fix as
# collision_object.py's _DIM_FONT / animated_region.py's _label_font).
_LABEL_FONT = None


class TriggerBox:
    """Base class — do not instantiate directly, use OverlapTriggerBox or
    KeyTriggerBox."""

    def __init__(self, box_id, x, y, width, height, once=True,
                 conditions=None, actions=None, always_run=False):
        self.box_id = box_id
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.once = once
        self.triggered = False   # latched True after firing, if once=True

        # Overlap bookkeeping for once=False boxes: tracks whether the
        # player was inside the box as of the last check, and whether the
        # box has already fired during the *current* continuous overlap.
        # Without this, an OverlapTriggerBox fires on every single frame
        # the player stands in it, which — for actions like dialogue_box —
        # re-opens the dialogue every frame and makes it impossible to
        # close. `_fired_this_entry` is cleared the instant the player
        # leaves the box, so a once=False box still fires again on the
        # *next* entry, just not every frame of the same visit.
        self.inside = False
        self._fired_this_entry = False

        # When True, the box skips its overlap/key check entirely — so its
        # x/y/width/height become irrelevant to firing. Lets you drop a
        # small marker box anywhere instead of dragging/resizing one to
        # cover the whole room. Still respects `once` and any attached
        # `conditions`, so it behaves like a passive "run every frame
        # until conditions/once say stop" trigger. Because it never goes
        # through the overlap-tracking above, an always_run box keeps its
        # original every-frame-refire behavior (needed for things like a
        # room-music trigger that should keep re-applying each frame).
        self.always_run = always_run

        # Built by core.event_editor.EventEditorWindow — see
        # open_event_editor()/should_fire() below.
        self.conditions = conditions if conditions is not None else []
        self.actions = actions if actions is not None else []

    @property
    def rect(self):
        return pygame.Rect(self.x, self.y, self.width, self.height)

    def _overlaps(self, player):
        if hasattr(player, 'get_collision_rect'):
            player_rect = player.get_collision_rect()
        else:
            player_rect = getattr(player, 'rect', None)
        if player_rect is None:
            return False
        return self.rect.colliderect(player_rect)

    def check(self, player, keys_pressed=None):
        """Return True exactly when the box should fire this frame. Subclasses
        implement _should_fire(); this handles the shared once-only latch.

        This is the original, condition-free entry point — kept as-is for
        callers that don't care about self.conditions. Use should_fire()
        instead if the box has conditions attached via the event editor,
        since check() alone would burn a once-only box's single fire even
        when its conditions end up failing."""
        if self.once and self.triggered:
            return False

        fired = True if self.always_run else self._should_fire(player, keys_pressed)
        if fired:
            if self.once:
                self.triggered = True
            self._fired_this_entry = True
        return fired

    def would_fire(self, player, keys_pressed=None):
        """Read-only probe: same overlap/key + once check as check(), but
        never latches self.triggered. Lets a caller peek at whether the box
        wants to fire before deciding — e.g. to evaluate self.conditions
        first — without spending a once-only box's one shot on a frame
        whose conditions turn out not to hold.

        Note: this does update the box's per-frame overlap bookkeeping
        (self.inside), since that has to track the player's real position
        every frame regardless of whether the box ultimately fires. Only
        self.triggered / self._fired_this_entry (the "did it actually
        fire" latches) are left untouched here — see commit()."""
        if self.once and self.triggered:
            return False
        if self.always_run:
            return True
        return self._should_fire(player, keys_pressed)

    def commit(self):
        """Latch the once-only flag (and the per-entry flag), as check()
        would on a successful fire. Call only after would_fire() and any
        attached conditions have both already passed."""
        if self.once:
            self.triggered = True
        self._fired_this_entry = True

    def should_fire(self, player, keys_pressed=None, evaluate_conditions=None):
        """The condition-aware entry point: fires only when the box's own
        overlap/key + once logic passes AND self.conditions (built by the
        event editor's ConditionBuilder) also passes.

        `evaluate_conditions`, if given, is called as
        evaluate_conditions(self.conditions, player) -> bool, matching
        whatever your flag_manager already uses to satisfy the condition
        dicts core.event_editor builds (flag_is()/check_item()/etc.).
        Pass None (or leave conditions empty) to skip condition checking
        entirely and behave like plain check(), just without double-firing
        a once-only box on a frame where conditions fail.
        """
        if not self.would_fire(player, keys_pressed):
            return False
        if evaluate_conditions is not None and self.conditions:
            if not evaluate_conditions(self.conditions, player):
                return False
        self.commit()
        return True

    def open_event_editor(self, event_editor_window):
        """Pop the shared EventEditorWindow open, pre-filled with whatever
        conditions/actions are already attached to this box, and wire its
        Save button to write the result straight back onto this box.

            box.open_event_editor(self.event_editor)   # e.g. on a click
            # each frame while self.event_editor.active:
            self.event_editor.handle_input(event)
            self.event_editor.draw(screen)
        """
        event_editor_window.open(
            title="Trigger Box: %s" % (self.box_id or "<unnamed>"),
            existing_conditions=self.conditions,
            existing_actions=self.actions,
            on_save=self._on_event_editor_save,
        )

    def _on_event_editor_save(self, conditions, actions):
        self.conditions = conditions
        self.actions = actions

    def _should_fire(self, player, keys_pressed):
        raise NotImplementedError

    def reset(self):
        """Un-latch a once-only box, e.g. when restoring a test-mode backup."""
        self.triggered = False
        self.inside = False
        self._fired_this_entry = False

    # ── Serialization (matches the to_dict/from_dict pattern used by the
    #    rest of the room object types, e.g. FlyingPadWaypoint, Door) ────────

    def to_dict(self):
        return {
            'kind': self.__class__.__name__,
            'box_id': self.box_id,
            'x': self.x,
            'y': self.y,
            'width': self.width,
            'height': self.height,
            'once': self.once,
            'triggered': self.triggered,
            'conditions': self.conditions,
            'actions': self.actions,
            'always_run': self.always_run,
        }

    @classmethod
    def from_dict(cls, data):
        box_class = _KIND_REGISTRY.get(data.get('kind'), OverlapTriggerBox)
        box = box_class(
            box_id=data['box_id'],
            x=data['x'], y=data['y'],
            width=data['width'], height=data['height'],
            once=data.get('once', True),
            conditions=data.get('conditions', []),
            actions=data.get('actions', []),
            always_run=data.get('always_run', False),
        )
        box.triggered = data.get('triggered', False)
        return box


class OverlapTriggerBox(TriggerBox):
    """Fires as soon as the player overlaps the box — no button press needed.

    For once=False boxes, firing is edge-triggered on *entry*: it fires the
    frame the player transitions from outside the box to inside it, then
    stays quiet (even though the player is still overlapping) until they
    leave and come back. This is what lets a repeatable overlap box run a
    one-shot action like a dialogue line each time the player walks in,
    instead of re-firing every frame they stand there.

    If self.conditions are attached and fail on the entry frame, the box
    keeps "would_fire"-ing (and its conditions keep getting re-evaluated)
    on every subsequent frame the player remains inside — it only stops
    polling once it actually fires (see commit()) or the player leaves.
    """

    def _should_fire(self, player, keys_pressed):
        now_inside = self._overlaps(player)
        if not now_inside:
            self.inside = False
            self._fired_this_entry = False
            return False
        self.inside = True
        return not self._fired_this_entry


class KeyTriggerBox(TriggerBox):
    """Fires only while the player overlaps the box AND presses the interact
    key ("E"). `interact_key` defaults to pygame's E key but can be overridden
    to match whatever key your input config maps to "interact".

    Firing is edge-triggered on the *key press* (not "key held"): each
    press-while-overlapping is a separate firing attempt, so a once=False
    box can be triggered repeatedly by pressing the key again, but holding
    the key down doesn't spam-fire it every frame. The key state resets
    when the player leaves the box, so a held key from a previous visit
    can't count as a fresh press on the next entry.
    """

    def __init__(self, box_id, x, y, width, height, once=True, interact_key=pygame.K_e,
                 conditions=None, actions=None, always_run=False):
        super().__init__(box_id, x, y, width, height, once=once,
                          conditions=conditions, actions=actions,
                          always_run=always_run)
        self.interact_key = interact_key
        self._key_was_down = False

    def _should_fire(self, player, keys_pressed):
        now_inside = self._overlaps(player)
        self.inside = now_inside
        if not now_inside:
            self._key_was_down = False
            return False
        if keys_pressed is None:
            return False
        key_down = bool(keys_pressed[self.interact_key])
        just_pressed = key_down and not self._key_was_down
        self._key_was_down = key_down
        return just_pressed

    def to_dict(self):
        data = super().to_dict()
        data['interact_key'] = self.interact_key
        return data

    @classmethod
    def from_dict(cls, data):
        box = super().from_dict(data)
        box.interact_key = data.get('interact_key', pygame.K_e)
        return box


_KIND_REGISTRY = {
    'OverlapTriggerBox': OverlapTriggerBox,
    'KeyTriggerBox': KeyTriggerBox,
}


class TriggerBoxManager:
    """Per-room registry of trigger boxes. The object editor and Game both
    go through this rather than poking room.trigger_boxes directly."""

    def __init__(self):
        self.trigger_boxes = {}  # room_name -> list[TriggerBox]

    def get_boxes(self, room_name):
        return self.trigger_boxes.get(room_name, [])

    def add_box(self, room_name, box):
        self.trigger_boxes.setdefault(room_name, []).append(box)

    def remove_box(self, room_name, box):
        boxes = self.trigger_boxes.get(room_name)
        if boxes and box in boxes:
            boxes.remove(box)


def draw_trigger_box(screen, box, camera_x, camera_y, render_scale, dev_mode=True, selected=False):
    """Editor-overlay-only visualization of a trigger box zone. Never drawn
    during real gameplay — trigger boxes have no in-game sprite."""
    if not dev_mode:
        return

    screen_x = int(box.x * render_scale - camera_x)
    screen_y = int(box.y * render_scale - camera_y)
    w = max(1, int(box.width * render_scale))
    h = max(1, int(box.height * render_scale))
    rect = pygame.Rect(screen_x, screen_y, w, h)

    is_key = isinstance(box, KeyTriggerBox)
    base_color = (255, 200, 0) if is_key else (0, 220, 120)

    fill = pygame.Surface((w, h), pygame.SRCALPHA)
    fill.fill((*base_color, 110 if selected else 70))
    screen.blit(fill, rect.topleft)

    border_color = (255, 255, 255) if selected else base_color
    screen.draw_rect(border_color, rect, 3 if selected else 2)

    global _LABEL_FONT
    if _LABEL_FONT is None:
        _LABEL_FONT = pygame.font.Font(None, 16)
    font = _LABEL_FONT
    label = box.box_id or '<unnamed>'
    if getattr(box, 'always_run', False):
        label += ' [ALWAYS]'
    elif not box.once:
        label += ' (repeat)'
    if is_key:
        label += ' [A]'
    text_surf = font.render(label, True, (255, 255, 255))
    screen.blit(text_surf, (rect.x + 3, rect.y + 3))