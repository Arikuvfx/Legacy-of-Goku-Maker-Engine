import pygame
from typing import Optional, List, Tuple
from config.settings import RENDER_SCALE
from objects.nimbus_cloud import NimbusCloud, NimbusCloudWaypoint
from dev_tools.room_editor.room_editor_tools.flying_pad_path_editor import FlyingPadPathEditor


class _LockedCamera:
    """Tiny read-only camera stand-in — just needs .x/.y, same trick
    object_editor.py's own _make_camera() proxy uses elsewhere."""
    __slots__ = ('x', 'y')

    def __init__(self, x, y):
        self.x = x
        self.y = y


class NimbusCloudPathEditor(FlyingPadPathEditor):
    """
    Editor for configuring nimbus cloud waypoint paths.

    Reuses FlyingPadPathEditor's entire waypoint-placement UI (boundary
    config dialog, spawn placement, return-cloud checkbox, etc.) as-is —
    the only thing that differs is the camera:

    FlyingPadPathEditor draws against whatever live camera the room editor
    hands it, so the view can be freely panned while placing points.
    Nimbus paths are meant to be authored — and later played back — against
    a single STATIC frame per room-leg, since at runtime the camera won't
    scroll to follow the cloud. So:

      - When the editor opens, the current view is captured once as
        `locked_camera_x/y` and used for every draw call from then on
        (via a small camera proxy handed down to the parent's draw/update),
        regardless of what live camera the caller passes in.
      - Waypoint placement is clamped to the INTERSECTION of the room
        bounds and that locked viewport — you simply cannot click
        somewhere off-screen, so a leg's path is naturally limited to
        "as far as you can see from here".
      - The moment a room boundary is crossed (spawn point placed in the
        destination room), the locked frame is recomputed to a fresh
        top-anchored view of the new room — "entry is always locked to
        the cam's top" — and stays fixed there for that room's leg, same
        as the first room.

    Integration note for the caller (room editor):
    While `self.active` is True, the room editor's own free-pan camera
    controls should be disabled — this class ignores whatever camera it's
    handed for its own drawing, but if the underlying view keeps panning
    underneath it, the tiles on screen and the path overlay will disagree.
    Call `get_locked_camera_position()` each frame and force the room
    editor's live camera to that value while this editor is active.
    """

    def __init__(self, screen_width: int, screen_height: int):
        super().__init__(screen_width, screen_height)
        self.locked_camera_x = 0
        self.locked_camera_y = 0
        self.render_scale    = RENDER_SCALE
        # The very first leg's locked frame, as captured in open(). Kept
        # around (separately from locked_camera_x/y, which gets overwritten
        # every time a new leg starts) so that cancelling out of
        # spawn_placement — which bounces the room transition back to
        # initial_room_name — has a frame to restore to instead of leaving
        # the locked view pointing at whatever leg was being authored when
        # the cancel happened.
        self._initial_locked_camera_x = 0
        self._initial_locked_camera_y = 0

        # Set the instant a new leg's spawn_placement begins, holding the
        # provisional anchor x to snap to. NOT acted on immediately — see
        # update() for why the actual _snap_camera_to_top_anchor call has
        # to wait until then.
        self._pending_leg_snap_x = None

    # ── Locked-camera helpers ────────────────────────────────────────────────

    def get_locked_camera_position(self) -> Tuple[int, int]:
        """Current static frame the path is being authored/played against."""
        return (self.locked_camera_x, self.locked_camera_y)

    def _viewport_world_bounds(self) -> Tuple[int, int, int, int]:
        """The locked frame's visible area, in world (unscaled) units."""
        left   = self.locked_camera_x / self.render_scale
        top    = self.locked_camera_y / self.render_scale
        right  = (self.locked_camera_x + self.screen_width)  / self.render_scale
        bottom = (self.locked_camera_y + self.screen_height) / self.render_scale
        return left, top, right, bottom

    def _clamp_to_locked_viewport(self, x: int, y: int) -> Tuple[int, int]:
        """Clamp world coordinates to the intersection of the room bounds and
        the current locked camera's visible area — this is what actually
        confines a leg's path to 'whatever can be seen right there'."""
        room_x, room_y = self._clamp_to_room_bounds(x, y)
        left, top, right, bottom = self._viewport_world_bounds()
        clamped_x = max(left, min(room_x, right))
        clamped_y = max(top,  min(room_y, bottom))
        return int(clamped_x), int(clamped_y)

    def _is_at_room_boundary(self, x: int, y: int) -> bool:
        """Same purpose as the parent's check, but measured against the
        locked viewport's edge rather than the room's true physical edge.

        The camera never pans while authoring a nimbus leg (see class
        docstring), so the room's real edge can sit well outside what's
        currently on screen — clicking it is simply impossible, which is
        why "click at the room edge" was silently never registering a
        transition. Since every coordinate this editor ever sees is already
        clamped to the locked viewport (via _clamp_to_locked_viewport), the
        edge of THAT viewport is the actual reachable limit for this leg —
        so that's what should count as "at boundary" here.
        """
        left, top, right, bottom = self._viewport_world_bounds()
        boundary_threshold = 10
        effective_left   = max(0, left)
        effective_top    = max(0, top)
        effective_right  = min(self.room_width, right)
        effective_bottom = min(self.room_height, bottom)
        return (
                x <= effective_left + boundary_threshold or
                x >= effective_right - boundary_threshold or
                y <= effective_top + boundary_threshold or
                y >= effective_bottom - boundary_threshold
        )

    def _snap_camera_to_top_anchor(self, spawn_x: int, spawn_y: int):
        """Recompute the locked frame for a freshly-entered room — centered
        horizontally on the spawn point, pinned to the top of the room.
        Mirrors the runtime camera snap in Game._handle_nimbus_room_transition
        so editing and playback always agree."""
        cam_x = (spawn_x * self.render_scale) - self.screen_width // 2
        cam_x = max(0, min(cam_x, self.room_width * self.render_scale - self.screen_width))
        cam_y = 0  # Top of the room.

        self.locked_camera_x = int(cam_x)
        self.locked_camera_y = int(cam_y)

    # ── Overrides ────────────────────────────────────────────────────────────

    def open(self, nimbus_cloud: NimbusCloud, room_name: str, available_rooms: List[str],
              room_width: int = 2400, room_height: int = 1800,
              camera_x: int = 0, camera_y: int = 0, render_scale: int = None):
        """Open the path editor for a nimbus cloud.

        camera_x/camera_y: the live editor camera position at the moment of
        opening — captured once as the locked frame for the first leg
        ("whatever can be seen right there" when the cloud was placed).
        """
        super().open(nimbus_cloud, room_name, available_rooms, room_width, room_height)
        self.render_scale    = render_scale if render_scale is not None else RENDER_SCALE
        self.locked_camera_x = int(camera_x)
        self.locked_camera_y = int(camera_y)
        self._initial_locked_camera_x = int(camera_x)
        self._initial_locked_camera_y = int(camera_y)

    def close(self):
        super().close()
        self.locked_camera_x = 0
        self.locked_camera_y = 0

    def handle_input(self, event, mouse_world_x: int, mouse_world_y: int,
                      world_width: int, world_height: int) -> Optional[str]:
        """Same as FlyingPadPathEditor.handle_input, except the mouse position
        handed to it is first clamped to the locked viewport, and a fresh
        top-anchored frame is captured the instant a new room-leg begins."""
        if not self.active:
            return None

        # Confine placement/clicks to what's actually visible in the locked
        # frame — this is what limits a leg's path to on-screen territory.
        clamped_x, clamped_y = self._clamp_to_locked_viewport(mouse_world_x, mouse_world_y)

        mode_before = self.editing_mode
        result = super().handle_input(event, clamped_x, clamped_y, world_width, world_height)

        # The instant a boundary is confirmed, the parent flips straight to
        # 'spawn_placement' and returns 'transition:{target_room}' in the
        # SAME call (see FlyingPadPathEditor's boundary_config confirm-
        # button handler) — the very next click needs to land in the
        # destination room. If we wait until AFTER that click to re-lock
        # the camera (the old approach), every click made while in
        # spawn_placement is still being clamped and drawn against the
        # PREVIOUS room's locked frame, so nothing the user clicks in the
        # new room can ever register — this was why spawn placement
        # silently never worked.
        #
        # We don't know the exact spawn spot yet (that's what the user is
        # about to choose), so record a provisional anchor x — the just-
        # confirmed boundary waypoint's exit x — rather than snapping right
        # here. The caller (room editor) only updates self.room_width/
        # room_height for the destination room in response to the
        # 'transition:' result we're about to return, i.e. AFTER this
        # method has already returned — computing the snap now would clamp
        # against the WRONG (previous) room's dimensions whenever the two
        # rooms differ in size. update() below applies it on the next
        # tick, by which point the caller has already synced room_width/
        # room_height to the destination room.
        if mode_before != 'spawn_placement' and self.editing_mode == 'spawn_placement':
            exit_wp = self.pending_boundary_waypoint
            self._pending_leg_snap_x = exit_wp.x if exit_wp else self.room_width // 2

        # Detect "just finished placing a spawn point in a new room" —
        # refine the provisional snap above to the exact committed position.
        if mode_before == 'spawn_placement' and self.editing_mode == 'placing' and self.active:
            spawn_point = self._get_spawn_point_for_current_room()
            if spawn_point:
                self._snap_camera_to_top_anchor(spawn_point[0], spawn_point[1])
            elif result and result.startswith('transition:'):
                # No committed spawn point but we still left spawn_placement
                # via a transition — this is the ESC-cancel path, which
                # bounces the room transition back to initial_room_name.
                # Restore the first leg's frame rather than leaving the
                # locked view pointed at the room we just backed out of.
                self.locked_camera_x = self._initial_locked_camera_x
                self.locked_camera_y = self._initial_locked_camera_y
            # Either branch resolves whatever leg-entry snap was pending —
            # clear it so a stale provisional anchor never gets applied
            # later in update().
            self._pending_leg_snap_x = None

        return result

    def update(self, dt: float, mouse_world_x: int, mouse_world_y: int):
        """Same as FlyingPadPathEditor.update, with the mouse clamped to the
        locked viewport so hover/preview stay confined to it too."""
        if not self.active:
            return

        # Apply a leg-entry snap that was deferred from handle_input(). By
        # now the caller (room editor) has already synced self.room_width/
        # room_height to the destination room in response to the
        # 'transition:' result — see the long comment in handle_input()
        # for why computing this any earlier clamps against the wrong
        # room's dimensions.
        if self._pending_leg_snap_x is not None:
            self._snap_camera_to_top_anchor(self._pending_leg_snap_x, 0)
            self._pending_leg_snap_x = None

        clamped_x, clamped_y = self._clamp_to_locked_viewport(mouse_world_x, mouse_world_y)
        super().update(dt, clamped_x, clamped_y)

    def draw(self, screen: pygame.Surface, camera, render_scale: int = 2):
        """Draw against the locked frame, not whatever live camera is passed
        in — that's what keeps the view pinned while a leg is being built."""
        if not self.active:
            return
        locked_camera = _LockedCamera(self.locked_camera_x, self.locked_camera_y)
        super().draw(screen, locked_camera, self.render_scale)

        # Small on-screen reminder that the view is intentionally frozen —
        # helps avoid confusion if the room editor's own camera controls
        # haven't been fully disabled by the integration yet (see class
        # docstring).
        font = pygame.font.Font(None, 18)
        label = font.render("View locked — Nimbus Cloud leg in progress", True, (170, 220, 255))
        bg = label.get_rect()
        bg.topright = (self.screen_width - 20, self.screen_height - 30)
        bg_surf = pygame.Surface((bg.width + 12, bg.height + 8), pygame.SRCALPHA)
        bg_surf.fill((0, 0, 0, 180))
        screen.blit(bg_surf, (bg.x - 6, bg.y - 4))
        screen.blit(label, bg)