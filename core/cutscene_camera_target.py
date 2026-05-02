"""
core/cutscene_camera_target.py

A lightweight object that the Camera can follow during a cutscene.
Replaces the player as the camera target so Camera.update() works unchanged —
just feed it this object instead of self.player.

Multiple simultaneous pan_to actions are blended additively: each pan
contributes its own eased delta on top of a shared base position, so
overlapping pans combine smoothly instead of the second cancelling the first.
"""


class CutsceneCameraTarget:
    """Dummy camera target with x/y that supports multiple simultaneous pans."""

    def __init__(self, x=0.0, y=0.0):
        self.x       = float(x)
        self.y       = float(y)
        self._base_x = float(x)  # settled position — updated as tweens complete
        self._base_y = float(y)
        self._tweens: list = []   # all active pan tweens running in parallel
        self._tween  = None       # most-recently added tween (for external callers)

    # ── Control API ───────────────────────────────────────────────────────────

    def pan_to(self, target_x, target_y, duration=1.0, start_x=None, start_y=None):
        """Pan to (target_x, target_y) over duration seconds.

        If start_x/start_y are given, the camera hard-resets to that position
        and clears all existing pans before starting — handy for scripting an
        explicit camera reset at the top of a cutscene.

        Otherwise the pan is additive: it layers a delta on top of any pans
        already running, so two simultaneous pans just move in both directions.
        """
        if duration <= 0:
            self.snap_to(target_x, target_y)
            return

        if start_x is not None or start_y is not None:
            # Explicit start — hard-reset position and wipe all existing pans.
            from_x = float(start_x) if start_x is not None else self.x
            from_y = float(start_y) if start_y is not None else self.y
            self._base_x = from_x
            self._base_y = from_y
            self._tweens = []
            self.x = from_x
            self.y = from_y

        # Store a delta rather than an absolute target so multiple overlapping
        # pans can each contribute independently to the final camera position.
        tween = {
            'delta_x':   float(target_x) - self.x,
            'delta_y':   float(target_y) - self.y,
            'duration':  float(duration),
            'elapsed':   0.0,
            'fire_time': None,  # filled in by seek() for scrub resolution
        }
        self._tweens.append(tween)
        self._tween = tween

    def snap_to(self, x, y):
        """Instantly jump to (x, y), cancelling all active pans."""
        self.x       = float(x)
        self.y       = float(y)
        self._base_x = float(x)
        self._base_y = float(y)
        self._tweens = []
        self._tween  = None

    def stop(self):
        """Cancel all in-progress pans, leaving the camera where it is."""
        self._tweens = []
        self._tween  = None

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self, dt):
        if not self._tweens:
            return

        still_running = []
        for tw in self._tweens:
            tw['elapsed'] += dt
            progress = min(1.0, tw['elapsed'] / tw['duration']) if tw['duration'] > 0 else 1.0

            if progress >= 1.0:
                # Tween finished — bake its full delta into the base so future
                # pans layer correctly on top of where this one landed.
                self._base_x += tw['delta_x']
                self._base_y += tw['delta_y']
            else:
                still_running.append(tw)

        self._tweens = still_running
        self._tween  = still_running[-1] if still_running else None
        self._recompute_xy()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _recompute_xy(self):
        """Rebuild x/y from the settled base plus every active tween's contribution.

        Each tween uses a smoothstep ease (3t² - 2t³) so pans accelerate in
        and decelerate out rather than moving at a constant rate.
        """
        x, y = self._base_x, self._base_y
        for tw in self._tweens:
            progress = min(1.0, tw['elapsed'] / tw['duration']) if tw['duration'] > 0 else 1.0
            eased = progress * progress * (3.0 - 2.0 * progress)  # smoothstep
            x += tw['delta_x'] * eased
            y += tw['delta_y'] * eased
        self.x = x
        self.y = y