import pygame
import math
from typing import Optional, Callable
from objects.nimbus_cloud import NimbusCloud, NimbusCloudWaypoint


class NimbusCloudController:
    """
    Manages the player's nimbus cloud ride along a predefined waypoint path.

    Differs from FlyingController in exactly two ways, per design:

      1. Movement ownership — the CLOUD is what moves along the waypoints.
         The player walks onto it once, then is anchored at a fixed offset
         on top of it in their IDLE pose for the whole ride; they never
         play a "flying" animation.

      2. Camera behavior — the camera does NOT follow the ride. It is
         locked (via camera.locked, same flag the map-jump sequence uses)
         for the full duration, and only snaps — never scrolls — to a new
         static, top-anchored framing at each room boundary crossing. This
         matches the NimbusCloudPathEditor: every leg of the path is
         authored against a single static view, so playback never needs to
         scroll to keep up with it.

    Responsibilities
    ----------------
    - Lock player input while riding and restore it on landing.
    - Walk the player onto the cloud ("boarding"), then anchor them.
    - Move the cloud smoothly from waypoint to waypoint.
    - Detect boundary waypoints and orchestrate room transitions mid-ride.
    - Keep the player's world position pinned to the cloud's anchor point.

    Callbacks (set by Game after construction)
    ------------------------------------------
    on_room_transition(target_room_name, spawn_x, spawn_y)
        Called at the midpoint of a transition to swap the active room.
        The implementation is expected to also re-anchor the camera to the
        new room's top-locked framing (see game.py's
        _handle_flying_room_transition for the equivalent flying-pad hook —
        the nimbus version should additionally pin camera.y to the top of
        the room rather than centering on spawn_y).
    on_ride_complete()
        Called when the cloud arrives at the final waypoint and the player
        is released.
    """

    def __init__(self, screen_width: int, screen_height: int, camera=None):
        self.screen_width  = screen_width
        self.screen_height = screen_height
        self.camera        = camera  # Optional; can also be set via set_camera().

        # Ride state
        self.is_riding              = False
        self.state                  = None  # 'boarding' | 'traveling'
        self.current_waypoint_index = 0
        self.waypoints              = []
        self.player                 = None
        self.cloud: Optional[NimbusCloud] = None

        # Movement
        self.board_speed       = 65   # World units/sec while walking onto the cloud.
        self.fly_speed         = 140  # World units/sec while the cloud travels.
        self.arrival_threshold = 5    # Distance at which the next waypoint is targeted.

        # Room transition state (mirrors FlyingController)
        self.is_transitioning_rooms    = False
        self.transition_timer          = 0.0
        self.transition_duration       = 0.7
        self.transition_target_room    = ""
        self.transition_spawn_x        = 0
        self.transition_spawn_y        = 0
        self.boundary_waypoint_index   = 0
        self.transition_fly_direction_x = 0.0
        self.transition_fly_direction_y = 0.0

        # Callbacks
        self.on_room_transition: Optional[Callable] = None
        self.on_ride_complete: Optional[Callable]   = None
        self.transition_controller = None

        # The manager owning each room's cloud list (same one object_editor.py
        # places/removes clouds through — see NimbusCloudManager.add_cloud/
        # remove_cloud). Needed so a mid-ride room crossing can move this
        # cloud into the destination room's bucket; without it, the cloud
        # stays registered only under the room it was originally placed in,
        # so once the destination room becomes the one being drawn, the
        # cloud simply isn't in that room's list anymore — invisible, even
        # though cloud.current_room and cloud.x/y were updated correctly.
        # Optional/settable via set_nimbus_cloud_manager(); if never set,
        # the controller falls back to the old (broken) behavior rather
        # than crashing, so this stays backward compatible until game.py
        # is wired up to call the setter.
        self.nimbus_cloud_manager = None

        # Sound — nimbus rides are silent by design (no take-off/travel sfx).
        # set_sound_manager() below is kept as a harmless no-op so game.py's
        # existing wiring call doesn't need to change.
        self.sound_manager  = None

    # ── Initialisation ─────────────────────────────────────────────────────────

    def set_camera(self, camera):
        """Attach the game Camera so the controller can lock/unlock it."""
        self.camera = camera

    def set_transition_controller(self, transition_controller):
        """Attach the TransitionController so fade effects can be triggered
        during room transitions."""
        self.transition_controller = transition_controller

    def set_nimbus_cloud_manager(self, nimbus_cloud_manager):
        """Attach the NimbusCloudManager so a mid-ride room crossing can
        relocate the cloud into the destination room's bucket — see the
        attribute comment in __init__ for why this is necessary."""
        self.nimbus_cloud_manager = nimbus_cloud_manager

    def set_sound_manager(self, sound_manager):
        """Kept for compatibility with game.py's wiring call — nimbus rides
        don't play any sfx, so this is currently unused."""
        self.sound_manager = sound_manager

    # ── Public API ────────────────────────────────────────────────────────────

    def start_ride(self, player, cloud: NimbusCloud):
        """
        Begin the nimbus cloud ride for *player* along the path defined by
        *cloud*. Cancels in-progress attack actions and locks player input.
        The player first walks onto the cloud (boarding); the actual travel
        along waypoints only starts once they're anchored.
        """
        if self.is_riding or cloud.is_occupied:
            return

        self.is_riding = True
        self.state      = 'boarding'
        self.player     = player
        self.cloud      = cloud
        cloud.is_occupied = True

        current_room   = getattr(cloud, 'current_room', '')
        forward_waypoints = cloud.get_path_for_flight(current_room)

        # A shuttle cloud (not a dedicated is_return_pad) can be boarded
        # from either end: its authored origin, or wherever it parked after
        # its last ride (typically the destination). Figure out which end
        # it's currently sitting at and travel toward the OTHER end —
        # forward through the path if it's at the origin, backward if it's
        # at the destination — so every ride glides the full visible route
        # instead of jumping to one end first.
        if not cloud.is_return_pad and forward_waypoints:
            # Find the room the path's final boundary crossing leads to (if
            # the path crosses a room at all).
            last_boundary_target_room = None
            for wp in forward_waypoints:
                if wp.is_boundary and wp.target_room:
                    last_boundary_target_room = wp.target_room

            origin_room = (
                getattr(cloud, 'source_room', '') or
                getattr(cloud, 'origin_room', '') or
                ''
            )

            if last_boundary_target_room:
                # Cross-room path — coordinate distance is meaningless across
                # rooms. Prefer current_room vs last boundary target; after a
                # save/load current_room can be stale, so also treat "not in
                # origin_room" as at-destination.
                if current_room == last_boundary_target_room:
                    at_destination = True
                elif origin_room and current_room and current_room != origin_room:
                    at_destination = True
                elif origin_room and current_room == origin_room:
                    at_destination = False
                else:
                    at_destination = False
            else:
                # Same-room path — everything's in one coordinate space, so
                # the original distance-to-endpoint heuristic is valid.
                last_wp = forward_waypoints[-1]
                dist_to_origin = math.hypot(cloud.x - cloud.origin_x, cloud.y - cloud.origin_y)
                dist_to_dest    = math.hypot(cloud.x - last_wp.x, cloud.y - last_wp.y)
                at_destination = dist_to_dest < dist_to_origin

            if at_destination:
                # Riding the shuttle back from its far end. get_reversed_path
                # needs origin_room to set each boundary's return target_room.
                self.waypoints = cloud.get_reversed_path(current_room)
            else:
                self.waypoints = forward_waypoints
        else:
            self.waypoints = forward_waypoints

        self.current_waypoint_index = 0
        self.is_transitioning_rooms = False
        self.transition_timer       = 0.0

        # Lock player input for the duration of the ride. Reuses the same
        # is_flying gate FlyingController uses so any existing input-locking
        # checks elsewhere in Player/Game apply here too.
        if hasattr(player, 'is_flying'):
            player.is_flying = True

        self.player.is_attacking     = False
        self.player.is_charging_beam = False
        self.player.is_firing_beam   = False
        self.player.pending_blast    = None
        self.player.is_q_pressed     = False
        if hasattr(self.player, 'current_beam') and self.player.current_beam:
            self.player.current_beam = None

        # Camera goes static immediately — even the walk-on is watched from
        # a fixed frame, matching how the path was authored in the editor.
        if self.camera is not None:
            self.camera.locked = True

    def cancel_ride(self):
        """Immediately abort the ride and restore player control."""
        if self.is_riding:
            self._complete_ride()

    def is_active(self) -> bool:
        """Return True while a ride is in progress (boarding, traveling, or
        mid-room-transition)."""
        return self.is_riding

    def get_transition_alpha(self) -> int:
        """Return the current fade overlay opacity (0–255) for the transition."""
        if not self.is_transitioning_rooms:
            return 0
        progress = self.transition_timer / self.transition_duration
        if progress < 0.5:
            return int(255 * (progress * 2))
        else:
            return int(255 * (2 - progress * 2))

    # ── Per-frame update ──────────────────────────────────────────────────────

    def update(self, dt: float):
        """Advance the controller one simulation step."""
        if not self.is_riding or not self.player or not self.cloud:
            return

        if self.state == 'boarding':
            self._update_boarding(dt)
            return

        if self.is_transitioning_rooms:
            self._update_room_transition(dt)
            return

        if self.current_waypoint_index >= len(self.waypoints):
            self._complete_ride()
            return

        target_wp = self.waypoints[self.current_waypoint_index]
        dx        = target_wp.x - self.cloud.x
        dy        = target_wp.y - self.cloud.y
        distance  = math.sqrt(dx ** 2 + dy ** 2)

        if distance < self.arrival_threshold:
            if target_wp.is_boundary and target_wp.target_room:
                if distance > 0:
                    self.transition_fly_direction_x = dx / distance
                    self.transition_fly_direction_y = dy / distance
                else:
                    self._derive_direction_from_previous(target_wp)
                self._start_room_transition(target_wp)
                return

            self.current_waypoint_index += 1
            return

        if distance > 0:
            dx /= distance
            dy /= distance
            move_dist = self.fly_speed * dt
            self.cloud.x += dx * move_dist
            self.cloud.y += dy * move_dist
            self._sync_player_to_cloud()

    def _update_boarding(self, dt: float):
        """Walk the player toward the cloud's rider anchor; once close,
        snap them onto it and hold the idle pose for the rest of the ride."""
        anchor_x = self.cloud.x + self.cloud.rider_offset_x
        anchor_y = self.cloud.y + self.cloud.rider_offset_y

        dx       = anchor_x - self.player.x
        dy       = anchor_y - self.player.y
        distance = math.sqrt(dx ** 2 + dy ** 2)

        if distance < self.arrival_threshold:
            self.player.x = anchor_x
            self.player.y = anchor_y
            self._anchor_idle(self.player.direction if hasattr(self.player, 'direction') else 'down')
            self.state = 'traveling'
            return

        dx /= distance
        dy /= distance
        move_dist = self.board_speed * dt
        self.player.x += dx * move_dist
        self.player.y += dy * move_dist
        self._update_walk_direction(dx, dy)

    def _sync_player_to_cloud(self):
        """Pin the player's world position to the cloud's current anchor point."""
        self.player.x = self.cloud.x + self.cloud.rider_offset_x
        self.player.y = self.cloud.y + self.cloud.rider_offset_y

    # ── Room-transition helpers (mirrors FlyingController, moves the cloud) ────

    def _derive_direction_from_previous(self, target_wp: NimbusCloudWaypoint):
        if self.current_waypoint_index > 0:
            prev_wp = self.waypoints[self.current_waypoint_index - 1]
            dx   = target_wp.x - prev_wp.x
            dy   = target_wp.y - prev_wp.y
            dist = math.sqrt(dx ** 2 + dy ** 2)
            if dist > 0:
                self.transition_fly_direction_x = dx / dist
                self.transition_fly_direction_y = dy / dist
                return
        self.transition_fly_direction_x = 1.0
        self.transition_fly_direction_y = 0.0

    def _start_room_transition(self, boundary_wp: NimbusCloudWaypoint):
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
        Phase 1 — Fade out: the cloud keeps moving in the stored direction,
                  player stays pinned to it.
        Phase 2 — Room swap: on_room_transition fires; cloud + player
                  teleport to the spawn point. The callback is responsible
                  for re-anchoring the camera to the new room's static,
                  top-locked frame.
        Phase 3 — Fade in: the cloud resumes moving toward the next
                  waypoint in the new room.
        """
        self.transition_timer += dt
        half = self.transition_duration / 2

        if self.transition_timer < half:
            move_dist = self.fly_speed * dt
            self.cloud.x += self.transition_fly_direction_x * move_dist
            self.cloud.y += self.transition_fly_direction_y * move_dist
            self._sync_player_to_cloud()

        elif self.transition_timer >= half and not hasattr(self, '_room_changed'):
            if self.on_room_transition:
                self.on_room_transition(
                    self.transition_target_room,
                    self.transition_spawn_x,
                    self.transition_spawn_y
                )

            # Move the cloud object itself into the destination room's
            # manager bucket. Without this it stays registered only under
            # the room it started the ride in — cloud.current_room and
            # cloud.x/y below get updated correctly, but the room-draw
            # loop looks the cloud up by which room's bucket it's IN, not
            # by its current_room field, so it would silently vanish the
            # moment the destination room becomes the one being drawn.
            if self.nimbus_cloud_manager is not None:
                source_room = self.cloud.current_room
                self.nimbus_cloud_manager.remove_cloud(source_room, self.cloud)
                self.nimbus_cloud_manager.add_cloud(self.transition_target_room, self.cloud)

            self.cloud.x = self.transition_spawn_x
            self.cloud.y = self.transition_spawn_y
            self.cloud.current_room = self.transition_target_room
            self._sync_player_to_cloud()

            self.current_waypoint_index = self.boundary_waypoint_index + 1
            self._room_changed = True

        elif self.transition_timer >= half:
            if self.current_waypoint_index < len(self.waypoints):
                target_wp = self.waypoints[self.current_waypoint_index]
                dx        = target_wp.x - self.cloud.x
                dy        = target_wp.y - self.cloud.y
                distance  = math.sqrt(dx ** 2 + dy ** 2)

                if distance > 0:
                    dx /= distance
                    dy /= distance
                    self.cloud.x += dx * self.fly_speed * dt
                    self.cloud.y += dy * self.fly_speed * dt
                    self._sync_player_to_cloud()

        if self.transition_timer >= self.transition_duration:
            self.is_transitioning_rooms = False
            self.transition_timer       = 0.0
            if hasattr(self, '_room_changed'):
                delattr(self, '_room_changed')

    # ── Player direction / animation ──────────────────────────────────────────

    def _update_walk_direction(self, dx: float, dy: float):
        """Set the player's facing + walking animation while boarding.
        Only used during the walk-onto-the-cloud phase — once anchored the
        player holds a fixed idle pose for the rest of the ride.

        Collapses to the dominant axis (down/up/left/right) rather than an
        8-way angle bucket. The player's 'walk' animation is only loaded
        4-directionally (see CharacterSpriteLoader.load_character —
        use_8_directions=False), so a diagonal direction like 'down_right'
        has no matching 'walk_down_right' key; set_animation() silently
        no-ops on a missing key, which left the player frozen on whatever
        animation was already playing (idle) for any approach angle that
        wasn't near-perfectly axis-aligned. This matches how Player.move()
        already resolves facing on diagonal input.
        """
        if abs(dx) >= abs(dy):
            self.player.direction = 'right' if dx > 0 else 'left'
        else:
            self.player.direction = 'down' if dy > 0 else 'up'

        if hasattr(self.player, 'sprite'):
            try:
                self.player.sprite.set_animation('walk', self.player.direction)
                self.player.current_animation_state = 'walk'
            except Exception:
                pass

    def _anchor_idle(self, direction: str):
        """Snap the player into the idle pose they'll hold for the whole ride."""
        landing_direction = direction
        if direction in ('up_right', 'up_left'):
            landing_direction = 'up'
        elif direction in ('down_right', 'down_left'):
            landing_direction = 'down'

        self.player.direction = landing_direction
        if hasattr(self.player, 'sprite'):
            self.player.sprite.set_animation('idle', landing_direction)
            self.player.current_animation_state = 'idle'

    # ── Ride completion ─────────────────────────────────────────────────────────

    def _complete_ride(self):
        """End the ride, restore player control, release the cloud."""
        if not self.player:
            return

        if hasattr(self.player, 'is_flying'):
            self.player.is_flying = False

        # Player is already in idle pose from _anchor_idle; nothing further
        # to change animation-wise on landing.

        if self.cloud:
            self.cloud.is_occupied = False

        if self.camera is not None:
            self.camera.locked = False

        if self.on_ride_complete:
            self.on_ride_complete()

        self.is_riding              = False
        self.state                  = None
        self.player                 = None
        self.cloud                  = None
        self.waypoints              = []
        self.current_waypoint_index = 0
        self.is_transitioning_rooms = False
        self.transition_timer       = 0.0

    # ── Draw ──────────────────────────────────────────────────────────────────

    def draw(self, screen: pygame.Surface, camera, render_scale: int = 2):
        """Reserved for future ride-visual effects (currently a no-op, same
        as FlyingController.draw)."""
        pass