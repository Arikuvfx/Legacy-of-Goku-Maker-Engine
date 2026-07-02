import pygame
import math
from typing import Optional, Callable
from objects.flying_pad import FlyingPad, FlyingPadWaypoint


class FlyingController:
    """
    Manages the player's flying sequence along a predefined waypoint path.

    Responsibilities
    ----------------
    - Lock player input while flying and restore it on landing.
    - Move the player smoothly from waypoint to waypoint.
    - Detect boundary waypoints and orchestrate room transitions mid-flight.
    - Drive the directional sprite animation while the player is airborne.

    Callbacks (set by Game after construction)
    ------------------------------------------
    on_room_transition(target_room_name, spawn_x, spawn_y)
        Called at the midpoint of a transition to swap the active room.
    on_flight_complete()
        Called when the player arrives at the final waypoint and lands.
    """

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width  = screen_width
        self.screen_height = screen_height

        # Flight state
        self.is_flying             = False
        self.current_waypoint_index = 0
        self.waypoints             = []
        self.player                = None

        # Movement
        self.fly_speed          = 160  # World units per second.
        self.arrival_threshold  = 5    # Distance at which the next waypoint is targeted.

        # Room transition state
        self.is_transitioning_rooms    = False
        self.transition_timer          = 0.0
        self.transition_duration       = 0.7   # Should match the walk-transition duration.
        self.transition_target_room    = ""
        self.transition_spawn_x        = 0
        self.transition_spawn_y        = 0
        self.boundary_waypoint_index   = 0

        # Direction the player was flying when the transition started,
        # used to keep them moving through the fade-out phase.
        self.transition_fly_direction_x = 0.0
        self.transition_fly_direction_y = 0.0

        # Callbacks
        self.on_room_transition: Optional[Callable] = None
        self.on_flight_complete: Optional[Callable] = None
        self.transition_controller = None  # Reference to TransitionController.

        # Sound — 'flyoff' plays once as the player takes off; once it
        # finishes, 'aura' loops for the remainder of the flight and is
        # stopped on landing/cancel. See set_sound_manager().
        self.sound_manager = None
        self._flyoff_channel = None   # Channel the one-shot take-off sfx is on.
        self._aura_looping   = False  # Whether the looping aura sfx is active.

        # Flying sprite (currently unused visually but available for effects)
        self.flying_sprite = None
        self._load_flying_sprite()

    # ── Initialisation ─────────────────────────────────────────────────────────

    def _load_flying_sprite(self):
        """
        Load the flying overlay sprite.  Creates a simple diamond fallback
        surface if the asset cannot be found.
        """
        try:
            self.flying_sprite = pygame.image.load('assets/sprites/flying.png').convert_alpha()
        except Exception:
            self.flying_sprite = pygame.Surface((32, 32), pygame.SRCALPHA)
            self.flying_sprite.fill((255, 255, 0))
            points = [(16, 5), (27, 16), (16, 27), (5, 16)]
            pygame.draw.polygon(self.flying_sprite, (255, 255, 255), points)

    def set_transition_controller(self, transition_controller):
        """
        Attach the TransitionController so fade effects can be triggered
        during room transitions.

        Args:
            transition_controller: Active TransitionController instance.
        """
        self.transition_controller = transition_controller

    def set_sound_manager(self, sound_manager):
        """
        Attach the SoundManager so take-off/flying sfx can be played.

        Args:
            sound_manager: Active SoundManager instance.
        """
        self.sound_manager = sound_manager

    # ── Public API ────────────────────────────────────────────────────────────

    def start_flight(self, player, flying_pad: FlyingPad):
        """
        Begin the flying sequence for *player* along the path defined by
        *flying_pad*.

        Cancels any in-progress attack actions (melee, beam, blast) and
        locks player input for the duration of the flight.

        Args:
            player:     The Player instance to move.
            flying_pad: The FlyingPad that triggered the flight.
        """
        if self.is_flying:
            return

        self.is_flying  = True
        self.player     = player

        current_room    = getattr(flying_pad, 'current_room', '')
        self.waypoints  = flying_pad.get_path_for_flight(current_room)

        self.current_waypoint_index = 0
        self.is_transitioning_rooms = False
        self.transition_timer       = 0.0

        # Lock player input for the duration of the flight.
        if hasattr(player, 'is_flying'):
            player.is_flying = True

        # Cancel any in-progress player actions.
        self.player.is_attacking       = False
        self.player.is_charging_beam   = False
        self.player.is_firing_beam     = False
        self.player.pending_blast      = None
        self.player.is_q_pressed       = False
        if hasattr(self.player, 'current_beam') and self.player.current_beam:
            self.player.current_beam = None

        # Play the take-off sfx. Once it finishes (polled in update()),
        # the looping aura sfx picks up for the rest of the flight.
        self._flyoff_channel = None
        self._aura_looping   = False
        if self.sound_manager:
            self._flyoff_channel = self.sound_manager.play_sfx('flyoff')
            # If the sfx couldn't be played (missing/disabled), fall back to
            # starting the aura loop immediately so the flight isn't silent.
            if self._flyoff_channel is None:
                self.sound_manager.play_looping_sfx('aura')
                self._aura_looping = True

    def cancel_flight(self):
        """Immediately abort the flying sequence and restore player control."""
        if self.is_flying:
            self._complete_flight()

    def is_active(self) -> bool:
        """Return True while a flight sequence is in progress."""
        return self.is_flying

    def get_transition_alpha(self) -> int:
        """
        Return the current fade overlay opacity (0–255) for the transition.

        Ramps up to 255 in the first half (fade to black) and back to 0 in
        the second half (fade from black).
        """
        if not self.is_transitioning_rooms:
            return 0

        progress = self.transition_timer / self.transition_duration

        if progress < 0.5:
            return int(255 * (progress * 2))
        else:
            return int(255 * (2 - progress * 2))

    # ── Per-frame update ──────────────────────────────────────────────────────

    def update(self, dt: float):
        """
        Advance the flying controller one simulation step.

        Args:
            dt: Delta time in seconds.
        """
        if not self.is_flying or not self.player:
            return

        # Once the one-shot take-off sfx finishes playing, hand off to the
        # looping aura sfx for the remainder of the flight.
        if self._flyoff_channel is not None and not self._flyoff_channel.get_busy():
            self._flyoff_channel = None
            if self.sound_manager and not self._aura_looping:
                self.sound_manager.play_looping_sfx('aura')
                self._aura_looping = True

        if self.is_transitioning_rooms:
            self._update_room_transition(dt)
            return

        if self.current_waypoint_index >= len(self.waypoints):
            self._complete_flight()
            return

        target_wp = self.waypoints[self.current_waypoint_index]
        dx        = target_wp.x - self.player.x
        dy        = target_wp.y - self.player.y
        distance  = math.sqrt(dx ** 2 + dy ** 2)

        if distance < self.arrival_threshold:
            # Reached the current waypoint — decide what to do next.
            if target_wp.is_boundary and target_wp.target_room:
                # Store the approach direction before snapping to the waypoint.
                if distance > 0:
                    self.transition_fly_direction_x = dx / distance
                    self.transition_fly_direction_y = dy / distance
                else:
                    self._derive_direction_from_previous(target_wp)

                self._start_room_transition(target_wp)
                return

            # Regular waypoint — advance to the next one.
            self.current_waypoint_index += 1
            return

        # Move the player toward the current waypoint.
        if distance > 0:
            dx /= distance
            dy /= distance
            self._update_player_direction(dx, dy)
            move_dist  = self.fly_speed * dt
            self.player.x += dx * move_dist
            self.player.y += dy * move_dist

    # ── Room-transition helpers ────────────────────────────────────────────────

    def _derive_direction_from_previous(self, target_wp: FlyingPadWaypoint):
        """
        Calculate the approach direction to *target_wp* using the preceding
        waypoint when the player has already arrived exactly on top of it.
        """
        if self.current_waypoint_index > 0:
            prev_wp = self.waypoints[self.current_waypoint_index - 1]
            dx   = target_wp.x - prev_wp.x
            dy   = target_wp.y - prev_wp.y
            dist = math.sqrt(dx ** 2 + dy ** 2)
            if dist > 0:
                self.transition_fly_direction_x = dx / dist
                self.transition_fly_direction_y = dy / dist
                return
        # Final fallback: continue to the right.
        self.transition_fly_direction_x = 1.0
        self.transition_fly_direction_y = 0.0

    def _start_room_transition(self, boundary_wp: FlyingPadWaypoint):
        """
        Initiate the room-transition sequence at a boundary waypoint.

        Starts the visual fade-out/in via the TransitionController (if
        attached) and records the target room and spawn position.

        Args:
            boundary_wp: The boundary FlyingPadWaypoint that was reached.
        """
        self.is_transitioning_rooms  = True
        self.transition_timer        = 0.0
        self.transition_target_room  = boundary_wp.target_room
        self.boundary_waypoint_index = self.current_waypoint_index
        self.transition_spawn_x      = boundary_wp.spawn_x
        self.transition_spawn_y      = boundary_wp.spawn_y

        if self.transition_controller:
            self.transition_controller.start_flying_transition(self.transition_duration)

    def _update_room_transition(self, dt: float):
        """
        Drive the three-phase room-transition sequence.

        Phase 1 — Fade out (first half):
            Player keeps flying in the stored direction.
        Phase 2 — Room swap (at the halfway point):
            on_room_transition callback fires; player teleports to spawn.
        Phase 3 — Fade in (second half):
            Player resumes flying toward the next waypoint.

        Args:
            dt: Delta time in seconds.
        """
        self.transition_timer += dt
        half = self.transition_duration / 2

        if self.transition_timer < half:
            # Phase 1: continue flying in the pre-transition direction.
            move_dist  = self.fly_speed * dt
            self.player.x += self.transition_fly_direction_x * move_dist
            self.player.y += self.transition_fly_direction_y * move_dist
            self._update_player_direction(
                self.transition_fly_direction_x,
                self.transition_fly_direction_y
            )

        elif self.transition_timer >= half and not hasattr(self, '_room_changed'):
            # Phase 2: swap the active room and teleport the player.
            if self.on_room_transition:
                self.on_room_transition(
                    self.transition_target_room,
                    self.transition_spawn_x,
                    self.transition_spawn_y
                )

            self.player.x = self.transition_spawn_x
            self.player.y = self.transition_spawn_y

            self.current_waypoint_index = self.boundary_waypoint_index + 1
            self._room_changed = True  # Guard so this block runs only once.

        elif self.transition_timer >= half:
            # Phase 3: fly toward the next waypoint in the new room.
            if self.current_waypoint_index < len(self.waypoints):
                target_wp = self.waypoints[self.current_waypoint_index]
                dx        = target_wp.x - self.player.x
                dy        = target_wp.y - self.player.y
                distance  = math.sqrt(dx ** 2 + dy ** 2)

                if distance > 0:
                    dx /= distance
                    dy /= distance
                    self.player.x += dx * self.fly_speed * dt
                    self.player.y += dy * self.fly_speed * dt
                    self._update_player_direction(dx, dy)

        # Finish the transition once the full duration has elapsed.
        if self.transition_timer >= self.transition_duration:
            self.is_transitioning_rooms = False
            self.transition_timer       = 0.0
            if hasattr(self, '_room_changed'):
                delattr(self, '_room_changed')

    # ── Player direction / animation ──────────────────────────────────────────

    def _update_player_direction(self, dx: float, dy: float):
        """
        Set the player's facing direction and sprite animation based on the
        current normalised movement vector.

        Maps the movement angle to one of eight named directions and switches
        the sprite to its 'flying' animation state (falls back to 'idle' if
        no flying animation exists).

        Args:
            dx: Normalised X component of the movement direction.
            dy: Normalised Y component of the movement direction.
        """
        angle = math.degrees(math.atan2(dy, dx))
        if angle < 0:
            angle += 360

        if   angle >= 337.5 or angle < 22.5:  self.player.direction = 'right'
        elif 22.5  <= angle < 67.5:            self.player.direction = 'down_right'
        elif 67.5  <= angle < 112.5:           self.player.direction = 'down'
        elif 112.5 <= angle < 157.5:           self.player.direction = 'down_left'
        elif 157.5 <= angle < 202.5:           self.player.direction = 'left'
        elif 202.5 <= angle < 247.5:           self.player.direction = 'up_left'
        elif 247.5 <= angle < 292.5:           self.player.direction = 'up'
        else:                                  self.player.direction = 'up_right'

        if hasattr(self.player, 'sprite'):
            try:
                self.player.sprite.set_animation('flying', self.player.direction)
                self.player.current_animation_state = 'flying'
            except Exception:
                self.player.sprite.set_animation('idle', self.player.direction)

    # ── Flight completion ─────────────────────────────────────────────────────

    def _complete_flight(self):
        """
        End the flying sequence, restore player control, and trigger the
        on_flight_complete callback.

        Diagonal flying directions are mapped to their nearest cardinal idle
        directions so the landing animation looks natural.
        """
        if not self.player:
            return

        # Stop any take-off/aura sfx that's still going — but leave it alone
        # if the player is currently transformed, since the transformation
        # system owns the aura loop in that case and will stop it on its own
        # detransform check.
        if self.sound_manager:
            player_is_transformed = False
            if hasattr(self.player, 'is_transformed'):
                try:
                    player_is_transformed = self.player.is_transformed()
                except Exception:
                    player_is_transformed = False
            if not player_is_transformed:
                self.sound_manager.stop_looping_sfx('aura')
        self._flyoff_channel = None
        self._aura_looping   = False

        if hasattr(self.player, 'is_flying'):
            self.player.is_flying = False

        if hasattr(self.player, 'sprite'):
            landing_direction = self.player.direction
            if self.player.direction in ('up_right', 'up_left'):
                landing_direction = 'up'
            elif self.player.direction in ('down_right', 'down_left'):
                landing_direction = 'down'

            self.player.direction = landing_direction
            self.player.sprite.set_animation('idle', landing_direction)
            self.player.current_animation_state = 'idle'

        if self.on_flight_complete:
            self.on_flight_complete()

        # Reset all state.
        self.is_flying              = False
        self.player                 = None
        self.waypoints              = []
        self.current_waypoint_index = 0
        self.is_transitioning_rooms = False
        self.transition_timer       = 0.0

    # ── Draw ──────────────────────────────────────────────────────────────────

    def draw(self, screen: pygame.Surface, camera, render_scale: int = 2):
        """
        Draw any flying-related visual effects (flight trail, particles, etc.)

        Currently a no-op; reserved for future use.
        """
        pass