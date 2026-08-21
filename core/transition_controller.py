"""
transition_controller.py

Manages smooth screen fade/walk transitions between rooms for both
walking (3-phase: walk-out → room-change → fade-in) and flying
(single fade driven by FlyingController) travel modes.
"""
import pygame


class TransitionController:
    """Handles smooth screen transitions between rooms (both walking and flying)"""

    def __init__(self, screen_width, screen_height):
        self.screen_width = screen_width
        self.screen_height = screen_height

        # Transition state
        self.transitioning = False
        self.transition_type = None  # 'walk' or 'flying'
        self.transition_phase = None  # 'walk_out', 'change_room', 'fade_in' OR 'flying_transition'
        self.transition_timer = 0.0
        self.walk_fade_duration = 0.7  # Duration of walking animation AND fade
        self.flying_fade_duration = 0.7  # Duration of flying fade transition

        # Track if we've initialized the current phase
        self.phase_initialized = False

        # Transition data
        self.target_room_name = None
        self.spawn_x = 0
        self.spawn_y = 0
        self.entry_direction = 'down'
        self.exit_direction = 'up'
        self.completion_callback = None

        # Player reference
        self.player = None

        # Flying transition state
        self.flying_transition_duration = 0.7

        # Plain fade state — a standalone fade with no room swap or player
        # movement, driven by the 'screen_fade' event action (see
        # start_plain_fade()). Kept separate from the walk/flying fields
        # above since those always imply a room change.
        self.plain_fade_direction = None  # 'in' | 'out'
        self.plain_fade_duration = 0.5
        self.plain_fade_callback = None
        self.plain_fade_hold = 0.0  # seconds to hold at the starting alpha before the fade itself begins

    def is_transitioning(self):
        """Return True if a transition is currently in progress."""
        return self.transitioning

    def start_transition(self, player, transition, completion_callback):
        """Begin a walking room transition.

        Args:
            player: The player object to animate during the transition.
            transition: A RoomTransition instance with target_room, spawn coords,
                        entry/exit directions, and dimensions.
            completion_callback: Called at the room-change phase with
                                 (target_room_name, spawn_x, spawn_y).
        """
        if self.transitioning:
            return

        self.transitioning = True
        self.transition_type = 'walk'
        self.transition_phase = 'walk_out'
        self.transition_timer = 0.0
        self.phase_initialized = False
        self.player = player

        # Store transition configuration
        self.target_room_name = transition.target_room
        self.spawn_x = transition.spawn_x
        self.spawn_y = transition.spawn_y
        self.entry_direction = transition.entry_direction
        self.exit_direction = transition.exit_direction
        self.completion_callback = completion_callback

        # Store the transition object size
        self.transition_width = transition.width
        self.transition_height = transition.height

        # Use spawn dimensions if available, otherwise fall back to transition dimensions
        spawn_width = getattr(transition, 'spawn_width', transition.width)
        spawn_height = getattr(transition, 'spawn_height', transition.height)

        # Calculate relative entry position within the transition object
        if transition.width > 0:
            self.relative_entry_x = (player.x - transition.x) / transition.width
        else:
            self.relative_entry_x = 0.5

        if transition.height > 0:
            self.relative_entry_y = (player.y - transition.y) / transition.height
        else:
            self.relative_entry_y = 0.5

        # Clamp relative position between 0 and 1
        self.relative_entry_x = max(0.0, min(1.0, self.relative_entry_x))
        self.relative_entry_y = max(0.0, min(1.0, self.relative_entry_y))

        # Store spawn dimensions for later use in change_room phase
        self.spawn_width = spawn_width
        self.spawn_height = spawn_height

        # Cancel any ongoing player actions
        self.player.is_attacking = False
        self.player.is_charging_beam = False
        self.player.is_firing_beam = False
        self.player.pending_blast = None
        self.player.is_q_pressed = False
        if self.player.current_beam:
            self.player.current_beam = None

    def start_flying_transition(self, duration):
        """Begin a flying room transition (movement handled by FlyingController).

        Args:
            duration: Length of the fade in seconds.
        """
        if self.transitioning:
            return

        self.transitioning = True
        self.transition_type = 'flying'
        self.transition_phase = 'flying_transition'
        self.transition_timer = 0.0
        self.flying_fade_duration = duration

    def start_plain_fade(self, direction, duration=0.5, on_complete=None, hold=0.0):
        """Begin a standalone fade with no room swap or player movement —
        this is what backs the 'screen_fade' event action.

        Args:
            direction:   'out' fades the screen from clear to black,
                         'in' fades it from black to clear.
            duration:    Length of the fade itself, in seconds.
            on_complete: Called with no args once the fade finishes — lets
                         EventRunner's blocking-action contract (see
                         event_actions.py) resume the sequence.
            hold:        Seconds to hold at the starting alpha (full black
                         for 'in', fully clear for 'out') BEFORE the fade
                         itself starts counting down. Lets a caller pin the
                         screen at full black for a beat — e.g. the pause
                         menu closing — without stretching the fade's own
                         motion into a slow crossfade. Defaults to 0 (no
                         hold), so existing callers are unaffected.

        If a transition is already in progress, this is a no-op except for
        immediately firing on_complete — otherwise a blocking screen_fade
        action queued behind an active room transition would stall the
        event sequence forever waiting for a callback that never comes.
        """
        if self.transitioning:
            if on_complete:
                on_complete()
            return

        self.transitioning = True
        self.transition_type = 'plain_fade'
        self.transition_phase = None
        self.transition_timer = 0.0
        self.plain_fade_direction = direction
        self.plain_fade_duration = max(0.0001, duration)
        self.plain_fade_callback = on_complete
        self.plain_fade_hold = max(0.0, hold)

    def update(self, dt, player):
        """Advance the transition state machine.

        Args:
            dt: Seconds elapsed since last frame.
            player: The player object (used to track is_transitioning flag).
        """
        if not self.transitioning:
            return

        self.transition_timer += dt

        # Plain fade: hold at the starting alpha for plain_fade_hold seconds
        # (if any), then track the fade itself and fire the completion
        # callback once it finishes — no room swap, no player movement.
        if self.transition_type == 'plain_fade':
            total = self.plain_fade_hold + self.plain_fade_duration
            if self.transition_timer >= total:
                self.transitioning = False
                self.transition_type = None
                callback = self.plain_fade_callback
                self.plain_fade_callback = None
                if callback:
                    callback()
            return

        # Flying transitions: just track the timer; movement is external
        if self.transition_type == 'flying':
            if self.transition_timer >= self.flying_fade_duration:
                self.transitioning = False
                self.transition_type = None
                self.transition_phase = None
                self.transition_timer = 0.0
            return

        # Walking — Phase 1: walk out with simultaneous fade to black
        if self.transition_phase == 'walk_out':
            if not self.phase_initialized:
                self.player.is_running = False
                self.player.direction = self.exit_direction
                self.player.sprite.set_animation('walk', self.exit_direction)
                self.player.current_animation_state = 'walk'
                self.phase_initialized = True
                player.is_transitioning = True

            walk_speed = 1.0
            if self.exit_direction == 'up':
                self.player.y -= walk_speed
            elif self.exit_direction == 'down':
                self.player.y += walk_speed
            elif self.exit_direction == 'left':
                self.player.x -= walk_speed
            elif self.exit_direction == 'right':
                self.player.x += walk_speed

            if self.transition_timer >= self.walk_fade_duration:
                self.transition_phase = 'change_room'
                self.transition_timer = 0.0
                self.phase_initialized = False

        # Walking — Phase 2: swap rooms while screen is fully black
        elif self.transition_phase == 'change_room':
            # Calculate exact spawn position from relative entry offset
            spawn_x = self.spawn_x + self.relative_entry_x * self.transition_width
            spawn_y = self.spawn_y + self.relative_entry_y * self.transition_height

            if self.completion_callback:
                self.completion_callback(self.target_room_name, spawn_x, spawn_y)

            self.player.x = spawn_x
            self.player.y = spawn_y

            self.player.direction = self.entry_direction
            self.player.sprite.set_animation('walk', self.entry_direction)
            self.player.current_animation_state = 'walk'

            # Reset animation frame counter
            if hasattr(self.player.sprite, 'frame_index'):
                self.player.sprite.frame_index = 0
            elif hasattr(self.player.sprite, 'current_frame'):
                self.player.sprite.current_frame = 0

            self.transition_phase = 'fade_in'
            self.transition_timer = 0.0
            self.phase_initialized = False

        # Walking — Phase 3: walk in with simultaneous fade from black
        elif self.transition_phase == 'fade_in':
            if not self.phase_initialized:
                self.player.direction = self.entry_direction
                self.player.sprite.set_animation('walk', self.entry_direction)
                self.player.current_animation_state = 'walk'
                self.phase_initialized = True
                player.is_transitioning = True

            walk_speed = 1.0
            if self.entry_direction == 'up':
                self.player.y -= walk_speed
            elif self.entry_direction == 'down':
                self.player.y += walk_speed
            elif self.entry_direction == 'left':
                self.player.x -= walk_speed
            elif self.entry_direction == 'right':
                self.player.x += walk_speed

            if self.transition_timer >= self.walk_fade_duration:
                self.transitioning = False
                self.transition_type = None
                self.transition_phase = None
                self.phase_initialized = False
                self.player.sprite.set_animation('idle', self.entry_direction)
                self.player.current_animation_state = 'idle'
                player.is_transitioning = False

    def draw(self, screen):
        """Draw the black fade overlay on top of the game world.

        Args:
            screen: The pygame Surface to draw onto.
        """
        if not self.transitioning:
            return

        alpha = 0

        if self.transition_type == 'plain_fade':
            if self.transition_timer < self.plain_fade_hold:
                # Still holding at the starting alpha — full black for 'in'
                # (screen was already black when the hold began), fully
                # clear for 'out' (nothing drawn yet).
                alpha = 255 if self.plain_fade_direction == 'in' else 0
            else:
                progress = min(1.0, (self.transition_timer - self.plain_fade_hold)
                                     / self.plain_fade_duration)
                if self.plain_fade_direction == 'out':
                    alpha = int(255 * progress)
                else:  # 'in'
                    alpha = int(255 * (1.0 - progress))

        elif self.transition_type == 'flying':
            # Fade to black in the first half, fade back in the second half
            progress = self.transition_timer / self.flying_fade_duration
            if progress < 0.5:
                alpha = int(255 * (progress * 2))
            else:
                alpha = int(255 * (2 - progress * 2))

        elif self.transition_type == 'walk':
            if self.transition_phase == 'walk_out':
                progress = self.transition_timer / self.walk_fade_duration
                alpha = int(255 * progress)
            elif self.transition_phase == 'change_room':
                alpha = 255
            elif self.transition_phase == 'fade_in':
                progress = self.transition_timer / self.walk_fade_duration
                alpha = int(255 * (1.0 - progress))

        if alpha > 0:
            overlay = pygame.Surface((self.screen_width, self.screen_height))
            overlay.fill((0, 0, 0))
            overlay.set_alpha(alpha)
            screen.blit(overlay, (0, 0))