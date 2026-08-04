"""
ui/spam_qte.py — Spam-button QTE bar (bottom-middle HUD widget).

A "mash E or Q" quick-time event: the fill bar continuously retracts
(drains right-to-left) every frame, and each qualifying E/Q press pushes
it back out to the right. The QTE completes the instant the fill reaches
the right edge (progress >= 1.0) — there's no fail state; a slow/uneven
player just takes longer to fill it.

Three images, all expected in assets/ui/hud:
    spam_bar_bg.png        — the empty/background bar frame, drawn at full size.
    spam_bar.png           — the fill graphic, drawn clipped to the current
                              progress fraction (left edge anchored — it
                              grows right as progress increases, retracts
                              left as it drains).
    spam_bar_crosshair.png — a marker riding the fill's leading (right)
                              edge, tracking progress in real time.

`progress`, `fill_per_press`, and `drain_rate` are all expressed as a
0.0-1.0 fraction of the full bar, so callers don't need to know the
bar's actual pixel width:
    fill_per_press = 0.08  -> one press fills 8% of the bar
    drain_rate     = 0.15  -> the bar drains 15% of itself per second

Usage (see Game._handle_spam_qte_action / Game._update_spam_qte / Game.draw):

    bar = SpamQTEBar()
    bar.start(qte_id='wake_up_mash', fill_per_press=0.08, drain_rate=0.15)
    ...
    # every frame while bar.active:
    bar.register_press()          # once per qualifying E/Q *keydown*
    if bar.update(dt):            # True on the exact frame it fills
        ...                       # QTE finished — fire on_complete()
    bar.draw(screen, render_scale=RENDER_SCALE)
"""

import os
import pygame


_ASSET_DIR = os.path.join('assets', 'ui', 'hud')
_BG_PATH        = os.path.join(_ASSET_DIR, 'spam_bar_bg.png')
_FILL_PATH      = os.path.join(_ASSET_DIR, 'spam_bar.png')
_CROSSHAIR_PATH = os.path.join(_ASSET_DIR, 'spam_bar_crosshair.png')

_BOTTOM_MARGIN = 40  # px (pre-render_scale) from the bottom of the screen

# Extra multiplier on top of render_scale — bumps both spam_bar_bg.png and
# spam_bar.png up/down together (they have to scale in lockstep or the fill
# stops lining up with the bg frame). 1.0 = native image size.
_BAR_SCALE = 1.5

# Fill-bar-only offset, added on top of the bg bar's (x, y) — positive X
# moves it right, positive Y moves it down. Tune these two to line
# spam_bar.png up with wherever it should sit inside spam_bar_bg.png's frame.
_FILL_OFFSET_X = 4
_FILL_OFFSET_Y = 0

# Crosshair-only offset, added on top of its computed position (which
# already tracks the fill's leading/right edge, centered on it both ways).
# Nudge these if spam_bar_crosshair.png's own art isn't perfectly centered
# on its source image.
_CROSSHAIR_OFFSET_X = 0
_CROSSHAIR_OFFSET_Y = 0

_DEFAULT_FILL_PER_PRESS = 0.08
_DEFAULT_DRAIN_RATE     = 0.15


class SpamQTEBar:
    """Bottom-middle mash-to-fill QTE bar. Game owns a single reusable
    instance — call start() to (re)arm it for whichever 'spam_qte' event
    action just fired, then update()/draw() it every frame while
    self.active is True. It clears itself (self.active = False) the
    frame it completes."""

    def __init__(self):
        self._bg_image        = None
        self._fill_image      = None
        self._crosshair_image = None
        self._load_images()

        self.active            = False
        self.qte_id            = None
        self.progress          = 0.0   # 0.0 (empty/left) .. 1.0 (full/right = complete)
        self.fill_per_press    = _DEFAULT_FILL_PER_PRESS
        self.drain_rate        = _DEFAULT_DRAIN_RATE
        self._pending_presses  = 0     # queued register_press() calls, consumed on next update()

    def _load_images(self):
        try:
            self._bg_image = pygame.image.load(_BG_PATH).convert_alpha()
        except Exception as e:
            print(f"SpamQTEBar: could not load {_BG_PATH}: {e}")
        try:
            self._fill_image = pygame.image.load(_FILL_PATH).convert_alpha()
        except Exception as e:
            print(f"SpamQTEBar: could not load {_FILL_PATH}: {e}")
        try:
            self._crosshair_image = pygame.image.load(_CROSSHAIR_PATH).convert_alpha()
        except Exception as e:
            print(f"SpamQTEBar: could not load {_CROSSHAIR_PATH}: {e}")

    def start(self, qte_id=None, fill_per_press=None, drain_rate=None, start_progress=0.0):
        """(Re)arm the bar and make it active/visible. A falsy
        fill_per_press/drain_rate (0, None, '' coerced to 0 by the event
        editor) falls back to a sane default instead of leaving the bar
        stuck (0 drain) or impossible to fill in reasonable time (0 fill)."""
        self.qte_id           = qte_id
        self.fill_per_press   = fill_per_press if fill_per_press else _DEFAULT_FILL_PER_PRESS
        self.drain_rate       = drain_rate if drain_rate else _DEFAULT_DRAIN_RATE
        self.progress          = max(0.0, min(1.0, start_progress or 0.0))
        self._pending_presses = 0
        self.active            = True

    def register_press(self):
        """Call once per qualifying E/Q *keydown* while self.active — queued
        and applied on the next update() rather than immediately, so a
        press landing between frames is never lost or double-counted."""
        if self.active:
            self._pending_presses += 1

    def update(self, dt):
        """Advance the continuous drain, apply any queued presses, and
        clamp to [0, 1]. Returns True on the exact frame the bar first
        reaches full — the caller fires its stored on_complete() then."""
        if not self.active:
            return False

        self.progress -= self.drain_rate * dt
        if self._pending_presses:
            self.progress += self.fill_per_press * self._pending_presses
            self._pending_presses = 0
        self.progress = max(0.0, min(1.0, self.progress))

        if self.progress >= 1.0:
            self.active = False
            return True
        return False

    def stop(self):
        """Hide the bar without completing it (e.g. a dev-tool bail-out /
        test-mode exit mid-QTE). Does NOT fire on_complete — callers that
        need the sequence to keep going should call the stored
        on_complete themselves after this."""
        self.active           = False
        self._pending_presses = 0

    def draw(self, screen, render_scale=1.0):
        if not self.active or self._bg_image is None:
            return

        total_scale = render_scale * _BAR_SCALE

        bg_w, bg_h = self._bg_image.get_size()
        scaled_w   = max(1, int(bg_w * total_scale))
        scaled_h   = max(1, int(bg_h * total_scale))

        screen_w, screen_h = screen.get_size()
        x = (screen_w - scaled_w) // 2
        y = screen_h - scaled_h - int(_BOTTOM_MARGIN * render_scale) + 100

        bg_scaled = pygame.transform.scale(self._bg_image, (scaled_w, scaled_h))
        screen.blit(bg_scaled, (x, y))

        if self._fill_image is not None and self.progress > 0.0:
            fill_w, fill_h = self._fill_image.get_size()
            visible_w = max(1, int(fill_w * self.progress))
            clip      = pygame.Rect(0, 0, visible_w, fill_h)
            fill_crop = self._fill_image.subsurface(clip)

            fill_scaled_w = max(1, int(visible_w * total_scale))
            fill_scaled_h = max(1, int(fill_h * total_scale))
            fill_scaled   = pygame.transform.scale(fill_crop, (fill_scaled_w, fill_scaled_h))
            fill_x        = x + int(_FILL_OFFSET_X * total_scale)
            fill_y        = y + int(_FILL_OFFSET_Y * total_scale)
            screen.blit(fill_scaled, (fill_x, fill_y))

        if self._crosshair_image is not None and self._fill_image is not None:
            # Leading edge of the fill, in the *fill image's own* pixel
            # space — deliberately not the clamped/blitted visible_w above
            # (that's floored to >= 1px so a sliver of fill always shows;
            # using it here would make the crosshair pop to a minimum
            # offset right as progress leaves 0 instead of starting flush
            # with the fill's left edge).
            fill_w, fill_h = self._fill_image.get_size()
            edge_w = fill_w * self.progress

            ch_w, ch_h = self._crosshair_image.get_size()
            ch_scaled_w = max(1, int(ch_w * total_scale))
            ch_scaled_h = max(1, int(ch_h * total_scale))
            ch_scaled   = pygame.transform.scale(self._crosshair_image, (ch_scaled_w, ch_scaled_h))

            fill_x = x + int(_FILL_OFFSET_X * total_scale)
            fill_y = y + int(_FILL_OFFSET_Y * total_scale)
            edge_x = fill_x + int(edge_w * total_scale)
            edge_y = fill_y + int(fill_h * total_scale) // 2

            ch_x = edge_x - ch_scaled_w // 2 + int(_CROSSHAIR_OFFSET_X * total_scale)
            ch_y = edge_y - ch_scaled_h // 2 + int(_CROSSHAIR_OFFSET_Y * total_scale)
            screen.blit(ch_scaled, (ch_x, ch_y))