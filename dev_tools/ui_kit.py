"""
ui_kit.py — shared UI building blocks for the engine's dev-tool overlays.

Everything here renders straight onto whatever pygame surface you pass in —
no separate windows, no external UI toolkit. Built so DevMenu is the first
consumer, but every class here is generic enough to reuse in the room
editor, sprite editor, etc. as they get their own passes.

Design language ("modern DBZ"):
    - deep navy/space background, gold (saiyan) accent, soft ki-blue accent
    - flat-ish cards with a hairline border that lights up gold on hover
    - small, purposeful motion (hover lift, icon bob, glow pulse) instead
      of the old per-row selection-box swap
"""

import pygame
import math
import os


# =============================================================================
# Bitmap menu font
# =============================================================================

class BitmapFont:
    """Bitmap font assembled from the menu glyph PNGs."""

    _SPECIAL = {
        " ": None, ".": "period.png", ",": "comma.png",
        "!": "exclamation.png", "?": "question.png",
        ":": "colon.png", "-": "dash.png", "+": "plus.png",
        "/": "slash.png", "_": "underscore.png",
        "(": "left_paren.png", ")": "right_paren.png",
        "'": "apostrophe.png",
    }

    def __init__(self, root='assets\\ui\\fonts', letter_spacing=1):
        root = os.path.normpath(root)
        self.uppercase_dir = os.path.join(root, 'uppercase_menu')
        self.lowercase_dir = os.path.join(root, 'lowercase_menu')
        self.numbers_dir = os.path.join(root, 'numbers')
        self.letter_spacing = int(letter_spacing)
        # Per-glyph baseline corrections in native bitmap pixels. Descender
        # glyphs need to sit slightly lower so their main body aligns with
        # letters such as a/e/o instead of being pulled upward by bottom-align.
        # Tweak these values here when a source glyph has different padding.
        self.glyph_y_offsets = {
            'g': 2, 'j': 2, 'p': 2, 'q': 2, 'y': 2,
        }
        self._glyphs = {}
        self._space_width_cache = None
        self._ref_native_h = None  # see _reference_native_height()
        # Cache of fully-assembled text surfaces, keyed by (text, color,
        # height). render() was rebuilding every string from its glyphs —
        # copy + BLEND_RGBA_MULT tint per character, then a full-surface
        # transform.scale — from scratch on *every single call*, including
        # every label redrawn every frame in an immediate-mode UI. Room
        # Editor in particular draws a lot of text per frame (list rows,
        # form fields, values), so that cost multiplied badly there. Most
        # on-screen text is static frame-to-frame, so caching the result
        # turns repeat draws into a dict lookup. Capped so highly dynamic
        # text (live-typed fields, per-frame counters) can't grow this
        # without bound.
        self._render_cache = {}
        self._RENDER_CACHE_LIMIT = 2000

    def _filename(self, ch):
        special = self._SPECIAL.get(ch, '__missing__')
        if special != '__missing__':
            return special
        if ch.isalpha() or ch.isdigit():
            return f'{ch}.png'
        return None

    def _glyph(self, ch):
        if ch in self._glyphs:
            return self._glyphs[ch]

        filename = self._filename(ch)
        if filename is None:
            self._glyphs[ch] = None
            return None

        folder = self.numbers_dir if ch.isdigit() else (
            self.uppercase_dir if ch.isupper() else self.lowercase_dir)
        path = os.path.join(folder, filename)
        try:
            glyph = pygame.image.load(path).convert_alpha()
        except (FileNotFoundError, pygame.error):
            glyph = None
        self._glyphs[ch] = glyph
        return glyph

    def _space_width(self):
        if self._space_width_cache is not None:
            return self._space_width_cache
        for ch in ('A', 'a', '0'):
            glyph = self._glyph(ch)
            if glyph is not None:
                self._space_width_cache = max(1, round(glyph.get_height() * 0.35))
                return self._space_width_cache
        self._space_width_cache = 4
        return self._space_width_cache

    def _render_native(self, text, color):
        parts = []
        max_h = 1

        for ch in text:
            if ch == ' ':
                parts.append((None, self._space_width(), 0))
                continue

            glyph = self._glyph(ch)
            if glyph is None:
                continue

            glyph = glyph.copy()
            glyph.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
            parts.append((glyph, glyph.get_width(), self.glyph_y_offsets.get(ch, 0)))
            max_h = max(max_h, glyph.get_height())

        if not parts:
            return pygame.Surface((1, 1), pygame.SRCALPHA), max_h

        width = sum(w for _, w, _ in parts) + self.letter_spacing * max(0, len(parts) - 1)

        # Leave enough transparent space below the normal baseline for
        # descender offsets. Previously the canvas stayed at max_h, so moving
        # g/j/p/q/y downward clipped their bottom pixels.
        max_positive_offset = max(
            0, max((offset for _, _, offset in parts), default=0)
        )
        out = pygame.Surface(
            (max(1, width), max_h + max_positive_offset),
            pygame.SRCALPHA
        )

        x = 0
        for glyph, glyph_w, y_offset in parts:
            if glyph is not None:
                out.blit(
                    glyph,
                    (x, max_h - glyph.get_height() + y_offset)
                )
            x += glyph_w + self.letter_spacing
        return out, max_h

    def render(self, text, antialias=False, color=(255, 255, 255), height=None):
        """Render bitmap text without fractional pixel scaling.

        The source glyphs are pixel art, so the final text is only enlarged by
        an integer factor (1x, 2x, 3x, ...). Fractional scaling is what causes
        some strokes to become 1px wide and others 2px wide.
        """
        color = tuple(color)
        cache_key = (text, color, height)
        cached = self._render_cache.get(cache_key)
        if cached is not None:
            return cached

        surf = self._render_uncached(text, color, height)

        if len(self._render_cache) >= self._RENDER_CACHE_LIMIT:
            # Simple reset rather than an LRU — this only fires for
            # pathological cases (e.g. a per-frame timer string), and a
            # full clear is far cheaper than bookkeeping eviction order for
            # what's normally a small, stable set of strings.
            self._render_cache.clear()
        self._render_cache[cache_key] = surf
        return surf

    def _reference_native_height(self):
        """Fixed glyph height used to derive the integer up-scale for a
        requested render `height` — this MUST be the same reference for
        every string rendered by this font, or two strings drawn at the
        *same* `height` end up at different actual pixel scales whenever
        their particular characters happen to come from source glyph PNGs
        of different native heights (e.g. the numbers/ sheet vs the
        lowercase/ sheet aren't guaranteed to be exported at the same
        pixel height). That was the previous bug here: scale was derived
        from *this string's own* tallest glyph (see _render_uncached's
        old comment about glyph_h), so e.g. a digit-containing id like
        "actor_0" and a plain word like "sound" — both rendered at the
        same requested `height` — could round to different integer
        scales and come out visibly different sizes.

        Uses a capital letter as the reference (present in effectively
        every real UI string set); falls back to a digit or lowercase
        letter for font instances that only ever render numbers/lowercase.
        Cached — the source glyphs never change size at runtime.
        """
        if self._ref_native_h is not None:
            return self._ref_native_h
        for ch in ('A', '0', 'a'):
            glyph = self._glyph(ch)
            if glyph is not None:
                self._ref_native_h = glyph.get_height()
                return self._ref_native_h
        self._ref_native_h = 1
        return self._ref_native_h

    def _render_uncached(self, text, color, height):
        out, glyph_h = self._render_native(text, color)

        if height is None or height <= 0:
            return out

        # Scale is derived from a fixed font-wide reference height (see
        # _reference_native_height), NOT from glyph_h/out.get_height() —
        # both of those vary per string (this string's tallest glyph, or
        # the descender-padded canvas height respectively) and using
        # either one means different strings requested at the same
        # `height` can round to different integer scales and render at
        # visibly different sizes.
        native_h = self._reference_native_height()
        scale = max(1, int(round(height / native_h)))
        if scale == 1:
            return out

        return pygame.transform.scale(
            out,
            (out.get_width() * scale, out.get_height() * scale),
        )

    def size(self, text, height=None):
        return self.render(text, height=height).get_size()


# =============================================================================
# Theme
# =============================================================================

_scrap_ready = None  # None = not yet attempted, True/False = attempted, worked or not


def _ensure_scrap():
    """Lazily init pygame's OS-clipboard bridge (pygame.scrap). It needs a
    display mode to already be set, which is why this isn't done at import
    time, and it isn't available on every platform/build, hence the guard."""
    global _scrap_ready
    if _scrap_ready is None:
        try:
            pygame.scrap.init()
            _scrap_ready = True
        except Exception:
            _scrap_ready = False
    return _scrap_ready


def clipboard_get_text():
    """Best-effort read of the system clipboard as text. Returns "" if the
    clipboard is empty, holds non-text data, or clipboard access failed."""
    if not _ensure_scrap():
        return ""
    try:
        data = pygame.scrap.get(pygame.SCRAP_TEXT)
    except Exception:
        return ""
    if not data:
        return ""
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="ignore")
    # SDL clipboard text often comes back with a trailing NUL.
    return data.split("\x00", 1)[0]


def clipboard_set_text(text):
    """Best-effort write of `text` to the system clipboard. No-op if
    clipboard access isn't available."""
    if not _ensure_scrap():
        return
    try:
        pygame.scrap.put(pygame.SCRAP_TEXT, text.encode("utf-8"))
    except Exception:
        pass


_active_cursor_kind = None  # None | 'ibeam' | 'resize' | 'resize_ns' | 'move'

_CURSOR_KIND_TO_SYSTEM_CURSOR = {
    None:        pygame.SYSTEM_CURSOR_ARROW,
    'ibeam':     pygame.SYSTEM_CURSOR_IBEAM,
    'resize':    pygame.SYSTEM_CURSOR_SIZEWE,
    'resize_ns': pygame.SYSTEM_CURSOR_SIZENS,
    'move':      pygame.SYSTEM_CURSOR_SIZEALL,
    'hand':      pygame.SYSTEM_CURSOR_HAND,
}

# A drop back to the plain arrow (kind=None) only commits once it's been
# requested continuously for this long. Wall-clock, not a per-call/per-frame
# counter — see below for why that distinction matters.
#
# Why this exists: every one of these setters is a "last write for the
# frame wins" no-op-guarded flag on ONE shared global — by design, so any
# number of editors/widgets can each call in with their own hover check
# without hand-wiring coordination between them (see each setter's
# docstring). That design assumes whichever call happens last in a frame
# is reliable. It isn't always: a single frame where a widget's hit-test
# momentarily comes up empty (e.g. a clip-rect edge case during a panel
# animation), or a second, independently-timed source touching this same
# global (e.g. DevMenu's dev-menu grid is a separate Dear PyGui window
# with its own event loop, not pygame's — see
# Game._handle_dev_menu_action), can slip in an errant "nothing's
# hovered" request for a brief moment. Applied immediately, that snaps
# the cursor to the arrow before the next real hover check restores it —
# a visible blink even though the mouse never left the widget.
#
# Time-based rather than a call counter specifically because a single
# caller's one hover check can itself make more than one setter call in
# the same frame (e.g. CutsceneEditor._resolve_cursor calls both
# set_text_cursor and set_hand_cursor every frame) — a counter would get
# bumped multiple times per real frame and the grace period would shrink
# unpredictably depending on how many setters a given caller happens to
# use. Measuring elapsed time from the first "clear" request instead
# means repeat calls within the same instant don't compound.
#
# Only the *downgrade to nothing* is debounced, not every change: picking
# up a MORE specific cursor (hand, ibeam, ...) still applies the instant
# it's requested, so hovering onto a widget still feels immediate. Only
# giving one back up needs the grace period, since that's the direction a
# stray gap pushes things.
_CURSOR_CLEAR_GRACE_MS = 100
_pending_clear_since = None  # ms timestamp of the first still-unresolved clear request


def _apply_cursor_kind(kind):
    """Shared no-op-guarded setter behind set_text_cursor/set_resize_cursor/
    set_move_cursor below, so the hover states share one source of truth
    instead of each tracking (and potentially clobbering) their own 'is it
    already set' flag. A request to clear back to the arrow (kind=None) is
    debounced by _CURSOR_CLEAR_GRACE_MS — see the module comment above it
    for why."""
    global _active_cursor_kind, _pending_clear_since
    if kind == _active_cursor_kind:
        _pending_clear_since = None
        return
    if kind is not None:
        _active_cursor_kind = kind
        _pending_clear_since = None
        pygame.mouse.set_cursor(_CURSOR_KIND_TO_SYSTEM_CURSOR[kind])
        return
    # kind is None and it differs from the currently-active kind: don't
    # commit yet, wait for this to be asked for continuously first.
    now = pygame.time.get_ticks()
    if _pending_clear_since is None:
        _pending_clear_since = now
    elif now - _pending_clear_since >= _CURSOR_CLEAR_GRACE_MS:
        _active_cursor_kind = None
        _pending_clear_since = None
        pygame.mouse.set_cursor(_CURSOR_KIND_TO_SYSTEM_CURSOR[None])


def set_text_cursor(active):
    """Switch the OS mouse cursor to an I-beam when `active` (mouse is over
    a text field), back to the normal arrow otherwise. No-ops when the
    cursor is already in that state, so every editor can call this once a
    frame — with whatever hover check it already has — without worrying
    about redundant pygame.mouse.set_cursor calls stomping each other."""
    _apply_cursor_kind('ibeam' if active else None)


def set_resize_cursor(active):
    """Switch the OS mouse cursor to a left-right resize arrow when `active`
    (mouse is over a draggable panel splitter), back to the normal arrow
    otherwise. Same no-op-guarded pattern as set_text_cursor — call this
    once a frame with whatever hover/drag check you already have. If a
    frame needs to check both text-field and splitter hover, resolve which
    one wins before calling in, since whichever of these two calls happens
    last in a frame determines the cursor. See set_resize_cursor_ns for the
    up-down variant (a panel's bottom-edge height grip)."""
    _apply_cursor_kind('resize' if active else None)


def set_resize_cursor_ns(active):
    """Switch the OS mouse cursor to an up-down resize arrow when `active`
    (mouse is over a draggable height grip), back to the normal arrow
    otherwise. Same no-op-guarded pattern as set_resize_cursor, just for a
    vertical (height) splitter instead of a horizontal (width) one."""
    _apply_cursor_kind('resize_ns' if active else None)


def set_move_cursor(active):
    """Switch the OS mouse cursor to a 4-way move arrow when `active`
    (mouse is over a draggable dialog title bar), back to the normal arrow
    otherwise. Same no-op-guarded pattern as set_text_cursor/
    set_resize_cursor — call once a frame; whichever of these three calls
    happens last in a frame wins."""
    _apply_cursor_kind('move' if active else None)


def set_hand_cursor(active):
    """Switch the OS mouse cursor to the pointing-hand (index finger out,
    other fingers curled) when `active` (mouse is over something clickable
    — a button, card, list item, tab), back to the normal arrow otherwise.
    Same no-op-guarded pattern as set_text_cursor/set_resize_cursor/
    set_move_cursor. Most callers won't need to call this directly — see
    register_hoverable/update_hover_cursor below for the generic,
    per-widget-opt-in version every ui_kit consumer gets automatically."""
    _apply_cursor_kind('hand' if active else None)


# ---------------------------------------------------------------------------
# Generic hover-cursor registry
# ---------------------------------------------------------------------------
# Rather than every editor hand-wiring set_hand_cursor(...) around each of
# its own ad-hoc hover checks, any widget can call register_hoverable(rect)
# while it draws itself. Once per frame — after everything's been drawn —
# the owning editor calls update_hover_cursor(mouse_pos) to resolve and
# apply the cursor for the whole frame, then clear the list for next time.
# Built-in ui_kit widgets (IconButton, IconGridMenu) already register
# themselves, so anything built purely from those gets the hand cursor for
# free; custom hand-rolled buttons elsewhere just need one
# `uk.register_hoverable(rect)` call added where they're drawn.
_hoverable_rects = []


def register_hoverable(rect):
    """Mark `rect` as clickable for this frame, so update_hover_cursor()
    shows the hand cursor when the mouse is over it. Call this every frame
    from inside a widget's draw() (only while it's actually clickable —
    skip it for disabled items), not just once at setup; the list is
    cleared every frame by update_hover_cursor(). No-ops on a falsy rect,
    so it's safe to call with `rect if condition else None`."""
    if rect is not None:
        _hoverable_rects.append(rect)


def update_hover_cursor(mouse_pos):
    """Call once per frame, after every widget has drawn (and therefore
    called register_hoverable), to resolve and apply the frame's final
    cursor. A more specific cursor set this frame via set_text_cursor/
    set_resize_cursor/set_move_cursor takes priority over the generic hand
    — so call this LAST, after those. Clears the hoverable list for the
    next frame regardless of outcome."""
    global _hoverable_rects
    is_hovering = any(r.collidepoint(mouse_pos) for r in _hoverable_rects)
    _hoverable_rects = []
    if _active_cursor_kind in ('ibeam', 'resize', 'resize_ns', 'move'):
        # An owner such as WorldMapEditor.update() (see its
        # _owned_cursor_kind guard) only asks to release one of these
        # kinds ONCE, on the frame its own hover check changes away from
        # it — that one call just starts _apply_cursor_kind's
        # _CURSOR_CLEAR_GRACE_MS timer, it doesn't commit the clear. If
        # nothing calls _apply_cursor_kind(None) again to check whether
        # the grace period has since elapsed, that timer is frozen
        # forever and _active_cursor_kind never actually clears — so we'd
        # keep bailing out here on every single frame after, and the
        # shared cursor would be stuck on move/resize/ibeam permanently
        # even once nothing is hovering it, instead of just for one
        # grace-period's worth of frames. This function runs every frame
        # regardless of which editor is doing the edge-triggering above,
        # so re-poll the pending clear here if one is already in flight
        # (never start one ourselves — only commit a release someone else
        # already asked for).
        if _pending_clear_since is not None:
            _apply_cursor_kind(None)
        return
    _apply_cursor_kind('hand' if is_hovering else None)


class Theme:
    BG_TOP = (9, 11, 18)
    BG_BOTTOM = (5, 7, 12)

    GOLD = (244, 190, 76)
    GOLD_BRIGHT = (255, 220, 145)
    KI_BLUE = (93, 171, 238)

    TEXT_PRIMARY = (236, 238, 244)
    TEXT_MUTED = (142, 148, 164)
    TEXT_SECONDARY = (190, 194, 204)
    TEXT_DIM = (92, 98, 115)

    CARD_BG = (19, 22, 31, 255)
    CARD_BG_HOVER = (27, 31, 42, 255)
    CARD_BG_SELECTED = (31, 36, 49, 255)
    CARD_BORDER = (49, 54, 68)

    PANEL_BG = (14, 17, 25, 250)
    PANEL_BORDER = (43, 48, 61)
    CHIP_BG = (26, 30, 40, 255)
    CHIP_BORDER = (55, 61, 76)

    DANGER = (230, 88, 88)
    DANGER_BRIGHT = (255, 120, 120)

    RADIUS_CARD = 10
    RADIUS_PANEL = 12
    BORDER_HAIRLINE = 1
    _font_cache = {}

    @classmethod
    def font(cls, path, size):
        key = (path, int(size))
        f = cls._font_cache.get(key)
        if f is None:
            f = pygame.font.Font(path, int(size))
            cls._font_cache[key] = f
        return f


def ease_out(t):
    """Standard easeOutCubic, t in [0,1]."""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_color(c1, c2, t):
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(min(len(c1), len(c2))))


# =============================================================================
# GPUScreen compatibility
# =============================================================================
# The engine's draw target (game.py's self.logical_surface) is core/gpu_renderer.py's
# GPUScreen wrapper around an SDL2 Renderer, not a plain pygame.Surface — it has no
# pixel buffer, so pygame.draw.* / set_at can't touch it directly. GPUScreen exposes
# its own draw_rect/draw_line/draw_circle/blit/blit_transient instead (same argument
# order as pygame.draw, minus the leading surface arg). Every function below that
# draws onto a passed-in `surface` goes through these dispatch helpers so it works
# whether that surface is a real pygame.Surface (e.g. in a headless/test context) or
# a GPUScreen — same convention the rest of the engine's UI code already migrated to.

def _is_real_surface(target):
    return isinstance(target, pygame.Surface)


def draw_line_on(target, color, start, end, width=1):
    if _is_real_surface(target):
        pygame.draw.line(target, color, start, end, width)
    else:
        target.draw_line(color, start, end, width)


# =============================================================================
# Supersampled shapes — real anti-aliasing on GPUScreen
# =============================================================================
# GPUScreen's draw_rect/draw_circle go straight to SDL_RenderGeometry, which
# (like pygame.draw) has no MSAA/AA of its own — curved edges (rounded-rect
# corners, circles) come out visibly stair-stepped. That jaggedness, not the
# color choices, was the actual gap between this and DPG's native widgets.
#
# Fix: draw the shape at N times the target resolution onto a throwaway
# pygame.Surface (plain CPU-side surface, so pygame.draw's rasterizer is
# available regardless of what `target` is), then pygame.transform.smoothscale
# it back down. Downsampling averages each output pixel over ~N*N source
# pixels, which is exactly what supersampled anti-aliasing is. The result is
# a normal Surface, so it always goes through the existing blit_surface()
# path — GPUScreen or real Surface, no special-casing needed at the call site.
#
# Every shape is cached by its exact (size, color, radius, width) — the same
# handful of card/panel/button sizes and a bounded range of lerped hover
# colors recur every frame, so this costs a build once per distinct value,
# not per frame (same tradeoff the existing shadow/vignette caches already
# make elsewhere in this file).

_SUPERSAMPLE = 8  # Render rounded geometry at 8x, then downsample for smooth
                  # anti-aliased curves and more consistent-looking borders.

_rrect_cache = {}
_circle_cache = {}


def _rgba(color):
    return tuple(color) if len(color) == 4 else (color[0], color[1], color[2], 255)


def _rounded_rect_surface(w, h, radius, fill, border_color, border_width, ss=_SUPERSAMPLE):
    """Cached vector-style rounded rectangle.

    The border is constructed as an outer rounded shape plus an inset inner
    shape instead of using pygame's stroked rounded rectangle. This keeps the
    border visually consistent around corners, where a stroked 1px/2px line
    can otherwise look thinner than the straight edges. Everything is drawn
    at high resolution and then downsampled for clean anti-aliasing.
    """
    w, h = max(1, int(w)), max(1, int(h))
    radius = max(0, int(radius))
    border_width = max(0, int(border_width))
    key = (w, h, radius, fill, border_color, border_width, ss)
    surf = _rrect_cache.get(key)
    if surf is not None:
        return surf

    hi = pygame.Surface((w * ss, h * ss), pygame.SRCALPHA)
    hi_rect = hi.get_rect()
    outer_radius = min(radius, min(w, h) // 2)

    if border_color is not None and border_width > 0:
        # Build the border from two filled vector shapes. This avoids the
        # uneven corner thickness produced by a stroked rounded rectangle.
        pygame.draw.rect(
            hi,
            border_color,
            hi_rect,
            border_radius=outer_radius * ss,
        )

        inset = min(border_width, min(w, h) // 2)
        inner = pygame.Rect(
            inset * ss,
            inset * ss,
            max(1, (w - inset * 2) * ss),
            max(1, (h - inset * 2) * ss),
        )
        inner_radius = max(0, min(
            outer_radius - inset,
            min(inner.width // ss, inner.height // ss) // 2
        )) * ss

        # If there is no fill, clear the center to transparent. If there is
        # a fill, draw it directly into the inset area.
        center_color = fill if fill is not None else (0, 0, 0, 0)
        pygame.draw.rect(
            hi,
            center_color,
            inner,
            border_radius=inner_radius,
        )
    elif fill is not None:
        pygame.draw.rect(
            hi,
            fill,
            hi_rect,
            border_radius=outer_radius * ss,
        )

    surf = pygame.transform.smoothscale(hi, (w, h))
    _rrect_cache[key] = surf
    return surf


def _circle_surface(radius, color, width, ss=_SUPERSAMPLE):
    radius = max(1, int(radius))
    key = (radius, color, width, ss)
    surf = _circle_cache.get(key)
    if surf is not None:
        return surf

    d = radius * 2
    hi = pygame.Surface((d * ss, d * ss), pygame.SRCALPHA)
    pygame.draw.circle(hi, color, (d * ss // 2, d * ss // 2), radius * ss, width * ss)
    surf = pygame.transform.smoothscale(hi, (d, d))
    _circle_cache[key] = surf
    return surf


def draw_circle_on(target, color, center, radius, width=0):
    if radius <= 0:
        return
    surf = _circle_surface(radius, _rgba(color), width)
    pos = (center[0] - surf.get_width() // 2, center[1] - surf.get_height() // 2)
    blit_surface(target, surf, pos, transient=False)


def draw_rect_on(target, color, rect, width=0, border_radius=0):
    rect = pygame.Rect(rect)
    if rect.w <= 0 or rect.h <= 0:
        return

    if border_radius <= 0:
        # Axis-aligned straight edges land on the pixel grid regardless of
        # resolution — nothing to anti-alias, so skip the Surface build/cache
        # entirely and go straight through the cheap native path.
        if _is_real_surface(target):
            pygame.draw.rect(target, color, rect, width, border_radius=0)
        else:
            target.draw_rect(color, rect, width, border_radius=0)
        return

    color = _rgba(color)
    if width > 0:
        surf = _rounded_rect_surface(rect.w, rect.h, border_radius, None, color, width)
    else:
        surf = _rounded_rect_surface(rect.w, rect.h, border_radius, color, None, 0)
    blit_surface(target, surf, rect.topleft, transient=False)


def blit_surface(target, source, dest, area=None, special_flags=0, transient=False):
    """Blit `source` (a plain pygame.Surface, e.g. a font.render() result)
    onto `target`, which may be a real pygame.Surface or GPUScreen.

    transient=True: `source` is rebuilt fresh every call (a re-rendered
    label, a per-frame animated panel) — routes through GPUScreen's
    uncached blit_transient so a brand-new object every frame doesn't pile
    up dead cache entries. transient=False (default): `source` is the SAME
    object reused across frames (a loaded icon, the background image) —
    uses the cached blit() so it uploads once and is reused.

    special_flags (e.g. pygame.BLEND_RGBA_ADD) always forces the cached
    blit() path, since GPUScreen.blit_transient has no blend-mode support.
    """
    if _is_real_surface(target):
        target.blit(source, dest, area, special_flags)
        return
    if special_flags:
        target.blit(source, dest, area=area, special_flags=special_flags)
    elif transient:
        target.blit_transient(source, dest, area=area)
    else:
        target.blit(source, dest, area=area)


# =============================================================================
# Backdrop
# =============================================================================

_gradient_cache = {}


def draw_vertical_gradient(surface, rect, top_color, bottom_color):
    """Vertical gradient fill. Rendered once per distinct (size, colors) and
    cached — this runs every frame the dev menu is open, and re-building a
    full-screen Surface line-by-line every frame would be wasteful either
    way (real Surface or GPUScreen)."""
    rect = pygame.Rect(rect)
    if rect.w <= 0 or rect.h <= 0:
        return
    key = (rect.w, rect.h, tuple(top_color), tuple(bottom_color))
    grad = _gradient_cache.get(key)
    if grad is None:
        grad = pygame.Surface((rect.w, rect.h))
        for i in range(rect.h):
            t = i / max(1, rect.h - 1)
            pygame.draw.line(grad, lerp_color(top_color, bottom_color, t), (0, i), (rect.w, i))
        _gradient_cache[key] = grad
    blit_surface(surface, grad, rect.topleft, transient=False)


_vignette_cache = {}


def draw_vignette(surface, size, strength=110):
    """Soft radial darkening toward the edges, cached per-size. This is
    what separates a flat single-color fill from the layered, "expensive"
    -looking backgrounds of tools like Premiere/Unreal — without it the
    backdrop reads as a plain rectangle of color no matter how good the
    gradient is."""
    key = (size, strength)
    vg = _vignette_cache.get(key)
    if vg is None:
        w, h = size
        vg = pygame.Surface((w, h), pygame.SRCALPHA)
        cx, cy = w / 2, h / 2
        max_r = math.hypot(cx, cy)
        # Coarse radial bands (cheap) rather than per-pixel distance —
        # plenty smooth at this alpha range and costs nothing to cache.
        bands = 28
        for i in range(bands, 0, -1):
            t = i / bands
            r = int(max_r * t)
            a = int(strength * (t ** 2.2))
            if a <= 0:
                continue
            pygame.draw.circle(vg, (0, 0, 0, a), (int(cx), int(cy)), r, width=max(2, int(max_r / bands) + 1))
        _vignette_cache[key] = vg
    blit_surface(surface, vg, (0, 0), transient=False)


_shadow_cache = {}

# pygame-ce (and pygame >= 2.5) ship a real box/gaussian blur. Where it's
# available we draw one crisp (supersampled, so still AA'd) rounded rect and
# blur it for a genuine soft shadow; where it isn't, fall back to the old
# layered-outline approximation so this still runs on older pygame.
_HAS_GAUSSIAN_BLUR = hasattr(pygame.transform, 'gaussian_blur')


def draw_soft_shadow(surface, rect, radius, spread=14, alpha=90, offset=(0, 6)):
    """Soft drop-shadow for a rounded rect, cached per (size, radius,
    spread, alpha). Reads as "lifted off the background" instead of the
    hard 2px outline the UI had before DPG."""
    rect = pygame.Rect(rect)
    key = (rect.w, rect.h, radius, spread, alpha)
    shadow = _shadow_cache.get(key)
    if shadow is None:
        pad = spread + 4
        w, h = rect.w + pad * 2, rect.h + pad * 2
        if _HAS_GAUSSIAN_BLUR:
            base = pygame.Surface((w, h), pygame.SRCALPHA)
            shape = _rounded_rect_surface(rect.w, rect.h, radius, (0, 0, 0, alpha), None, 0)
            base.blit(shape, (pad, pad))
            shadow = pygame.transform.gaussian_blur(base, spread // 2)
        else:
            shadow = pygame.Surface((w, h), pygame.SRCALPHA)
            layers = 6
            for i in range(layers, 0, -1):
                t = i / layers
                grow = int(spread * t)
                a = int(alpha * (1 - t) ** 1.6)
                if a <= 0:
                    continue
                r = pygame.Rect(pad - grow, pad - grow, rect.w + grow * 2, rect.h + grow * 2)
                pygame.draw.rect(shadow, (0, 0, 0, a), r, border_radius=radius + grow // 2)
        _shadow_cache[key] = shadow
    pos = (rect.x - (shadow.get_width() - rect.w) // 2 + offset[0],
           rect.y - (shadow.get_height() - rect.h) // 2 + offset[1])
    blit_surface(surface, shadow, pos, transient=False)


_icon_glow_cache = {}


def draw_soft_glow(surface, center, radius, color, max_alpha=70):
    """Feathered radial glow, cached per (radius, color, max_alpha). A
    single flat-alpha circle reads as a solid coin of color once
    additive-blended; this falls off toward the edge instead, which is
    what actually looks like "light" rather than "sticker"."""
    key = (radius, tuple(color), max_alpha)
    glow = _icon_glow_cache.get(key)
    if glow is None:
        if _HAS_GAUSSIAN_BLUR:
            core_r = max(1, radius // 3)
            base = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            core = _circle_surface(core_r, (*color, min(255, max_alpha * 2)), 0)
            base.blit(core, (radius - core_r, radius - core_r))
            glow = pygame.transform.gaussian_blur(base, radius // 2)
        else:
            glow = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            bands = 14
            for i in range(bands, 0, -1):
                t = i / bands
                r = int(radius * t)
                a = int(max_alpha * (1 - t) ** 1.8)
                if a <= 0:
                    continue
                pygame.draw.circle(glow, (*color, a), (radius, radius), r)
        _icon_glow_cache[key] = glow
    pos = (center[0] - glow.get_width() // 2, center[1] - glow.get_height() // 2)
    blit_surface(surface, glow, pos, special_flags=pygame.BLEND_RGBA_ADD)


def render_text_glow(surface, font, text, pos, color, glow_color, glow_alpha=140, radius=3):
    """Crisp text with a soft halo behind it instead of a hard offset
    black shadow. Approximates a blur by stamping the same glyph a few
    times at small offsets with low alpha before drawing the sharp copy
    on top — cheap, and reads as "soft glow" rather than "muddy outline"."""
    glow_surf = font.render(text, True, glow_color)
    glow_surf.set_alpha(glow_alpha)
    offsets = [(-radius, 0), (radius, 0), (0, -radius), (0, radius),
               (-radius, -radius), (radius, radius), (-radius, radius), (radius, -radius)]
    for dx, dy in offsets:
        blit_surface(surface, glow_surf, (pos[0] + dx, pos[1] + dy), transient=True)
    main_surf = font.render(text, True, color)
    blit_surface(surface, main_surf, pos, transient=True)
    return main_surf


class KiParticles:
    """Lightweight ambient particles drifting upward — cheap charm layer
    behind menu content. Purely decorative; no interaction."""

    def __init__(self, width, height, count=26):
        self.width = width
        self.height = height
        self.particles = []
        import random
        for _ in range(count):
            self.particles.append(self._spawn(random, y=random.uniform(0, height)))

    def _spawn(self, random_mod, y=None):
        import random
        r = random_mod if random_mod else random
        color = Theme.GOLD if r.random() < 0.6 else Theme.KI_BLUE
        size = r.uniform(1.2, 2.6)
        alpha = r.uniform(35, 85)  # dimmer — ambient detail, not a visible FX layer
        return {
            'x': r.uniform(0, self.width),
            'y': self.height + 10 if y is None else y,
            'speed': r.uniform(12, 34),
            'sway_amp': r.uniform(6, 22),
            'sway_speed': r.uniform(0.5, 1.4),
            'phase': r.uniform(0, math.tau),
            # Baked once per spawn, not rebuilt per frame: color/size/alpha
            # are fixed for a particle's whole lifetime, so the same Surface
            # object gets reused across frames — that's what lets
            # blit_surface's cached path actually hit instead of re-
            # uploading a texture every frame for every particle.
            'glow_surf': self._make_glow_surf(color, size, alpha),
        }

    @staticmethod
    def _make_glow_surf(color, size, alpha):
        s = max(1, int(size * 3))
        surf = pygame.Surface((s * 2, s * 2), pygame.SRCALPHA)
        pygame.draw.circle(surf, (*color, int(alpha)), (s, s), s)
        return surf

    def update(self, dt):
        import random
        for p in self.particles:
            p['y'] -= p['speed'] * dt
            p['phase'] += p['sway_speed'] * dt
            if p['y'] < -10:
                new = self._spawn(random, y=self.height + 10)
                p.update(new)

    def draw(self, surface):
        for p in self.particles:
            sway = math.sin(p['phase']) * p['sway_amp']
            x = int(p['x'] + sway)
            y = int(p['y'])
            glow = p['glow_surf']
            gw, gh = glow.get_size()
            blit_surface(surface, glow, (x - gw // 2, y - gh // 2),
                         special_flags=pygame.BLEND_RGBA_ADD)


# =============================================================================
# Panels / buttons
# =============================================================================

def draw_panel(surface, rect, bg=None, border=None, border_width=None, radius=None, shadow=True):
    bg = bg if bg is not None else Theme.PANEL_BG
    border = border if border is not None else Theme.PANEL_BORDER
    radius = radius if radius is not None else Theme.RADIUS_PANEL
    border_width = Theme.BORDER_HAIRLINE if border_width is None else border_width
    rect = pygame.Rect(rect)

    if shadow:
        draw_soft_shadow(surface, rect, radius)

    draw_rect_on(surface, bg, rect, 0, radius)
    if border_width > 0:
        draw_rect_on(surface, border, rect, border_width, radius)
    # 1px inner highlight along the top edge — the "glass bevel" that
    # separates a lit panel from a flat one without adding any glow.
    hl = pygame.Rect(rect.x + radius, rect.y + border_width, max(0, rect.w - radius * 2), 1)
    if hl.w > 0:
        draw_rect_on(surface, (255, 255, 255, 16), hl, 0, 0)


class IconButton:
    """Small circular/rounded corner button — used for the dev menu's
    gear (settings) and close (X) affordances, top-right of the header."""

    def __init__(self, rect, draw_icon_fn, tooltip=None, danger=False):
        self.rect = pygame.Rect(rect)
        self.draw_icon_fn = draw_icon_fn  # fn(surface, rect, color) -> None
        self.tooltip = tooltip
        self.danger = danger
        self.hover = False
        self.hover_anim = 0.0

    def handle_event(self, event):
        if event.type == pygame.MOUSEMOTION:
            self.hover = self.rect.collidepoint(event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                return True
        return False

    def update(self, dt):
        target = 1.0 if self.hover else 0.0
        self.hover_anim += (target - self.hover_anim) * min(1.0, dt * 10)

    def draw(self, surface):
        register_hoverable(self.rect)
        base_bg = (22, 25, 42)
        hover_bg = (58, 28, 28) if self.danger else (42, 47, 76)
        bg = lerp_color(base_bg, hover_bg, self.hover_anim)
        border_col = Theme.DANGER_BRIGHT if self.danger else Theme.GOLD
        border = lerp_color(Theme.CARD_BORDER, border_col, self.hover_anim)
        border_w = 1 + round(self.hover_anim)  # hairline at rest, 2px only when active

        draw_rect_on(surface, (*bg, 235), self.rect, 0, 10)
        draw_rect_on(surface, border, self.rect, border_w, 10)

        icon_color = lerp_color(Theme.TEXT_MUTED, (255, 255, 255), self.hover_anim)
        self.draw_icon_fn(surface, self.rect, icon_color)


def draw_gear_icon(surface, rect, color):
    cx, cy = rect.center
    r_outer, r_inner = 9, 4
    for i in range(8):
        a = i * (math.pi / 4)
        x1 = cx + math.cos(a) * r_outer
        y1 = cy + math.sin(a) * r_outer
        draw_line_on(surface, color, (cx, cy), (x1, y1), 3)
    draw_circle_on(surface, color, (cx, cy), r_outer - 2, 2)
    draw_circle_on(surface, color, (cx, cy), r_inner)


def draw_close_icon(surface, rect, color):
    cx, cy = rect.center
    s = 6
    draw_line_on(surface, color, (cx - s, cy - s), (cx + s, cy + s), 3)
    draw_line_on(surface, color, (cx - s, cy + s), (cx + s, cy - s), 3)


def draw_back_icon(surface, rect, color):
    cx, cy = rect.center
    s = 7
    draw_line_on(surface, color, (cx + s, cy - s), (cx - s, cy), 3)
    draw_line_on(surface, color, (cx - s, cy), (cx + s, cy + s), 3)


# =============================================================================
# Dev-menu card icon set — vector line glyphs, not bitmaps
# =============================================================================
# Deliberately drawn with the same primitives as the gear/close/back icons
# above (draw_line_on / draw_rect_on / draw_circle_on) rather than loaded
# from PNG. A bitmap icon — however well drawn — reads as a different
# material than a crisp gradient card with hairline borders; that material
# mismatch is what makes a UI look "unprofessional" even when the layout is
# solid. Every editor with a clean app-launcher grid (Unity Hub, Unreal's
# project browser, RPG Maker's toolbox) uses flat single-color line icons
# for exactly this reason: they scale, anti-alias, and re-color for free.

def draw_room_icon(surface, rect, color, width=3):
    """Simple house glyph — Room Editor."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    half = s * 0.30
    eave_y = cy - s * 0.02
    draw_line_on(surface, color, (cx - half, eave_y), (cx, cy - s * 0.30), width)
    draw_line_on(surface, color, (cx, cy - s * 0.30), (cx + half, eave_y), width)
    body = pygame.Rect(0, 0, half * 1.72, s * 0.40)
    body.centerx = cx
    body.top = int(eave_y)
    draw_rect_on(surface, color, body, width, 3)
    door = pygame.Rect(0, 0, s * 0.15, s * 0.22)
    door.centerx = cx
    door.bottom = body.bottom
    draw_rect_on(surface, color, door, max(1, width - 1), 2)


def draw_map_icon(surface, rect, color, width=3):
    """Folded map with a location marker — World Map Editor."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    box = pygame.Rect(0, 0, s * 0.62, s * 0.46)
    box.center = (cx, cy)
    draw_rect_on(surface, color, box, width, 4)
    third = box.w / 3
    draw_line_on(surface, color, (box.x + third, box.y + 4), (box.x + third, box.bottom - 4), 1)
    draw_line_on(surface, color, (box.x + 2 * third, box.y + 4), (box.x + 2 * third, box.bottom - 4), 1)
    mx, my = box.x + third * 1.5, box.y + box.h * 0.42
    m = s * 0.06
    draw_line_on(surface, color, (mx - m, my - m), (mx + m, my + m), 2)
    draw_line_on(surface, color, (mx - m, my + m), (mx + m, my - m), 2)


def draw_sprite_icon(surface, rect, color, width=3):
    """Image frame with mountain + sun — Sprite Editor."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    box = pygame.Rect(0, 0, s * 0.62, s * 0.48)
    box.center = (cx, cy)
    draw_rect_on(surface, color, box, width, 4)
    sun = (int(box.x + box.w * 0.26), int(box.y + box.h * 0.30))
    draw_circle_on(surface, color, sun, max(2, int(s * 0.065)), max(1, width - 1))
    p1 = (box.x + 4, box.bottom - 5)
    p2 = (box.x + box.w * 0.44, box.y + box.h * 0.40)
    p3 = (box.x + box.w * 0.64, box.bottom - box.h * 0.32)
    p4 = (box.right - 4, box.bottom - 5)
    for a, b in ((p1, p2), (p2, p3), (p3, p4)):
        draw_line_on(surface, color, a, b, max(1, width - 1))


def draw_cutscene_icon(surface, rect, color, width=3):
    """Clapperboard — Cutscene Editor."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    body = pygame.Rect(0, 0, s * 0.58, s * 0.38)
    body.centerx = cx
    body.top = int(cy - s * 0.02)
    draw_rect_on(surface, color, body, width, 3)
    top = pygame.Rect(body.x, body.y - s * 0.15, body.w, s * 0.15)
    draw_rect_on(surface, color, top, width, 3)
    for i in range(4):
        t = i / 3
        x = top.x + t * top.w
        draw_line_on(surface, color, (x, top.y + 1), (x - top.h * 0.55, top.bottom - 1), 2)


def draw_character_icon(surface, rect, color, width=3):
    """Head + shoulders — Character Creator."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    head_r = s * 0.15
    head_c = (cx, int(cy - s * 0.16))
    draw_circle_on(surface, color, head_c, int(head_r), width)
    shoulder = pygame.Rect(0, 0, s * 0.5, s * 0.28)
    shoulder.centerx = cx
    shoulder.top = int(head_c[1] + head_r * 1.1)
    draw_rect_on(surface, color, shoulder, width, shoulder.h // 2)


def draw_entity_icon(surface, rect, color, width=3):
    """Three linked nodes — Entity Creator (a generic game-object/actor,
    distinct from the single head-and-shoulders Character glyph)."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    r = s * 0.08
    top = (cx, cy - s * 0.26)
    left = (cx - s * 0.24, cy + s * 0.18)
    right = (cx + s * 0.24, cy + s * 0.18)
    draw_line_on(surface, color, top, left, width)
    draw_line_on(surface, color, top, right, width)
    draw_line_on(surface, color, left, right, width)
    for p in (top, left, right):
        draw_circle_on(surface, color, p, r, 0)


def draw_attack_icon(surface, rect, color, width=3):
    """Crossed-blade glyph — Attack Creator."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    draw_line_on(surface, color, (cx - s * 0.24, cy + s * 0.24), (cx + s * 0.22, cy - s * 0.22), width)
    gx, gy = cx - s * 0.02, cy + s * 0.02
    draw_line_on(surface, color, (gx - s * 0.13, gy + s * 0.09), (gx + s * 0.09, gy - s * 0.13), width)
    hx, hy = cx - s * 0.28, cy + s * 0.28
    draw_line_on(surface, color, (hx, hy), (hx - s * 0.09, hy + s * 0.09), width)


def draw_item_icon(surface, rect, color, width=3):
    """Isometric cube outline — Item Creator."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    r = s * 0.28
    pts = [(cx + math.cos(math.pi / 6 + i * math.pi / 3) * r,
            cy + math.sin(math.pi / 6 + i * math.pi / 3) * r) for i in range(6)]
    for i in range(6):
        draw_line_on(surface, color, pts[i], pts[(i + 1) % 6], width)
    for i in (0, 2, 4):
        draw_line_on(surface, color, (cx, cy), pts[i], max(1, width - 1))


def draw_decoration_icon(surface, rect, color, width=3):
    """4-point sparkle — Decoration Creator."""
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    r = s * 0.27
    pts = [(cx, cy - r), (cx + r * 0.3, cy - r * 0.3), (cx + r, cy), (cx + r * 0.3, cy + r * 0.3),
           (cx, cy + r), (cx - r * 0.3, cy + r * 0.3), (cx - r, cy), (cx - r * 0.3, cy - r * 0.3)]
    for i in range(len(pts)):
        draw_line_on(surface, color, pts[i], pts[(i + 1) % len(pts)], max(1, width - 1))


DEV_MENU_ICON_DRAWERS = {
    'room': draw_room_icon,
    'map': draw_map_icon,
    'sprite': draw_sprite_icon,
    'cutscene': draw_cutscene_icon,
    'character': draw_character_icon,
    'entity': draw_entity_icon,
    'attack': draw_attack_icon,
    'item': draw_item_icon,
    'decoration': draw_decoration_icon,
    'config': draw_gear_icon,
    'close': draw_close_icon,
    'back': draw_back_icon,
}


def render_icon_surface(draw_fn, size, color):
    """Bake a vector icon drawer into a cached-friendly pygame.Surface, the
    same shape (SRCALPHA square) a loaded PNG icon used to be — so callers
    (IconGridMenu) don't need to know whether an icon came from disk or
    from code."""
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    draw_fn(surf, pygame.Rect(0, 0, size, size), color)
    return surf


# =============================================================================
# Icon grid menu
# =============================================================================

class GridItem:
    def __init__(self, item_id, label, icon=None, danger=False, sublabel=None, accent=None, shortcut=None):
        self.id = item_id
        self.label = label
        self.icon = icon
        self.danger = danger
        self.accent = tuple(accent) if accent else Theme.GOLD
        self.shortcut = shortcut
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.hover_anim = 0.0
        self.focus_anim = 0.0


class IconGridMenu:
    """Keyboard and mouse navigable tool grid."""

    def __init__(self, items, columns=3, card_size=(196, 150), gap=(26, 26),
                 font_label=None, font_label_hi=None, icon_size=48,
                 font_path=None, font_label_size=17, font_label_hi_size=20):
        self.columns = columns
        self.card_w, self.card_h = card_size
        self.gap_x, self.gap_y = gap
        self.font_label = font_label
        self.font_label_hi = font_label_hi or font_label
        self.icon_size = icon_size
        self.font_path = font_path
        self.font_label_size = font_label_size
        self.font_label_hi_size = font_label_hi_size
        self._label_fit_cache = {}
        self.items = []
        self.set_items(items)
        self.selected_index = -1
        self.hover_index = -1
        self._area = pygame.Rect(0, 0, 0, 0)

    def set_items(self, items):
        self.items = []
        for it in items:
            if isinstance(it, GridItem):
                self.items.append(it)
            else:
                self.items.append(GridItem(
                    it['id'], it['label'], it.get('icon'),
                    it.get('danger', False), it.get('sublabel'),
                    it.get('accent'), it.get('shortcut')
                ))
        self.selected_index = -1
        self.hover_index = -1

    def set_card_geometry(self, card_size, gap):
        self.card_w, self.card_h = card_size
        self.gap_x, self.gap_y = gap
        self._label_fit_cache.clear()

    def layout(self, area_rect):
        self._area = pygame.Rect(area_rect)
        n = len(self.items)
        if n == 0:
            return
        cols = min(self.columns, n)
        rows = math.ceil(n / cols)
        grid_w = cols * self.card_w + (cols - 1) * self.gap_x
        grid_h = rows * self.card_h + (rows - 1) * self.gap_y
        origin_x = self._area.centerx - grid_w // 2
        origin_y = self._area.centery - grid_h // 2
        for i, item in enumerate(self.items):
            row, col = divmod(i, cols)
            items_in_row = min(cols, n - row * cols)
            row_w = items_in_row * self.card_w + (items_in_row - 1) * self.gap_x
            row_origin_x = self._area.centerx - row_w // 2
            x = row_origin_x + col * (self.card_w + self.gap_x)
            y = origin_y + row * (self.card_h + self.gap_y)
            item.rect = pygame.Rect(x, y, self.card_w, self.card_h)
        self._cols = cols
        self._rows = rows

    def _set_hover_from_pos(self, pos):
        self.hover_index = -1
        for i, item in enumerate(self.items):
            if item.rect.collidepoint(pos):
                self.hover_index = i
                break

    def handle_event(self, event):
        if not self.items:
            return None
        if event.type == pygame.MOUSEMOTION:
            self._set_hover_from_pos(event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._set_hover_from_pos(event.pos)
            if self.hover_index >= 0:
                self.selected_index = self.hover_index
                return self.items[self.selected_index].id
        if event.type == pygame.KEYDOWN:
            cols = getattr(self, '_cols', self.columns)
            n = len(self.items)
            # Number row shortcuts, 1..9.
            if pygame.K_1 <= event.key <= pygame.K_9:
                index = event.key - pygame.K_1
                if index < n:
                    self.selected_index = index
                    return self.items[index].id
            if self.selected_index == -1:
                if event.key in (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT,
                                 pygame.K_RIGHT, pygame.K_w, pygame.K_a,
                                 pygame.K_s, pygame.K_d):
                    self.selected_index = 0
                    return None
            if self.selected_index < 0:
                return None
            if event.key in (pygame.K_RIGHT, pygame.K_d):
                self.selected_index = min(self.selected_index + 1, n - 1)
            elif event.key in (pygame.K_LEFT, pygame.K_a):
                self.selected_index = max(self.selected_index - 1, 0)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                target = self.selected_index + cols
                self.selected_index = target if target < n else self.selected_index
            elif event.key in (pygame.K_UP, pygame.K_w):
                target = self.selected_index - cols
                self.selected_index = target if target >= 0 else self.selected_index
            elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                return self.items[self.selected_index].id
        return None

    def update(self, dt):
        for i, item in enumerate(self.items):
            target = 1.0 if (i == self.selected_index or i == self.hover_index) else 0.0
            item.hover_anim += (target - item.hover_anim) * min(1.0, dt * 10.0)
            focus = 1.0 if i == self.selected_index else 0.0
            item.focus_anim += (focus - item.focus_anim) * min(1.0, dt * 12.0)

    def draw(self, surface):
        for item in self.items:
            register_hoverable(item.rect)
            self._draw_card(surface, item)

    def _fit_label(self, text, base_size, max_w, hi):
        font = self.font_label_hi if hi else self.font_label
        if isinstance(font, BitmapFont):
            key = (text, int(base_size), int(max_w), bool(hi))
            cached = self._label_fit_cache.get(key)
            if cached is not None:
                return cached
            height = max(8, int(base_size))
            while height > 8 and font.size(text, height=height)[0] > max_w:
                height -= 1
            self._label_fit_cache[key] = height
            return height
        return font

    def _draw_card(self, surface, item):
        t = max(item.hover_anim, item.focus_anim)
        rect = item.rect.copy()
        accent = item.accent if not item.danger else Theme.DANGER_BRIGHT
        bg = lerp_color(Theme.CARD_BG[:3], Theme.CARD_BG_HOVER[:3], t)
        if item.focus_anim > 0.01:
            bg = lerp_color(bg, Theme.CARD_BG_SELECTED[:3], item.focus_anim)
        border = lerp_color(Theme.CARD_BORDER, accent, t)

        draw_rect_on(surface, (*bg, 255), rect, 0, Theme.RADIUS_CARD)
        draw_rect_on(surface, border, rect, 1, Theme.RADIUS_CARD)

        if item.shortcut:
            badge = pygame.Rect(rect.right - 30, rect.y + 10, 20, 20)
            draw_rect_on(surface, (*Theme.CHIP_BG[:3], 255), badge, 0, 6)
            draw_rect_on(surface, Theme.CHIP_BORDER, badge, 1, 6)
            if isinstance(self.font_label, BitmapFont):
                st = self.font_label.render(
                    item.shortcut, color=Theme.TEXT_MUTED,
                    height=max(8, self.font_label_size - 3)
                )
            else:
                sf = Theme.font(self.font_path, max(10, self.font_label_size - 3))
                st = sf.render(item.shortcut, True, Theme.TEXT_MUTED)
            blit_surface(surface, st, st.get_rect(center=badge.center), transient=True)

        # Cards without an icon (e.g. the CONFIGURATION rows) get no glow
        # circle and a vertically centered label instead of the lower
        # label slot reserved under the icon.
        if item.icon is not None:
            icon_center = (rect.centerx, rect.y + int(rect.h * 0.40))
            draw_circle_on(surface, (*accent, 18), icon_center, max(25, int(self.icon_size * .68)))
            icon_rect = item.icon.get_rect(center=icon_center)
            blit_surface(surface, item.icon, icon_rect, transient=False)
            label_y = rect.y + int(rect.h * .77)
        else:
            label_y = rect.centery

        font = self._fit_label(item.label, self.font_label_size + int(t), rect.w - 24, t > .5)
        text_color = Theme.TEXT_PRIMARY if t > .2 else Theme.TEXT_SECONDARY
        if isinstance(self.font_label, BitmapFont):
            text = self.font_label.render(item.label, color=text_color, height=font)
        else:
            text = font.render(item.label, True, text_color)
        blit_surface(surface, text, text.get_rect(center=(rect.centerx, label_y)),
                     transient=True)


# =============================================================================
# Modal text input (extracted so any editor can reuse it)
# =============================================================================

class ModalTextInput:
    def __init__(self, font_prompt, font_input, font_hint, width=600, height=120):
        self.font_prompt = font_prompt
        self.font_input = font_input
        self.font_hint = font_hint
        self.width = width
        self.height = height
        self.active = False
        self.text = ""
        self.field = None
        self.cursor_blink = 0.0
        self.cursor_pos = 0
        self.selection_anchor = None  # None = no selection; else other end of it
        self._dragging = False
        self.max_len = 24
        self.input_rect = None

    def open(self, field):
        self.active = True
        self.text = ""
        self.field = field
        self.cursor_blink = 0.0
        self.cursor_pos = 0
        self.selection_anchor = None
        self._dragging = False

    def close(self):
        self.active = False
        self.text = ""
        self.field = None
        self.cursor_pos = 0
        self.selection_anchor = None
        self._dragging = False
        self.input_rect = None

    def _has_selection(self):
        return self.selection_anchor is not None and self.selection_anchor != self.cursor_pos

    def _selection_range(self):
        a, b = self.selection_anchor, self.cursor_pos
        return (a, b) if a <= b else (b, a)

    def _delete_selection(self):
        """Removes the selected text (if any), leaving the cursor at the
        start of where it was. Returns True if anything was deleted."""
        if not self._has_selection():
            return False
        s, e = self._selection_range()
        self.text = self.text[:s] + self.text[e:]
        self.cursor_pos = s
        self.selection_anchor = None
        return True

    def _insert_text(self, s):
        """Types `s` in at the cursor, replacing the selection if any and
        respecting the field's max length. Used for both single keystrokes
        and pasted text. Filters/length-checks happen before touching the
        selection, so an empty or all-non-printable `s` (e.g. a stray
        modifier-key keystroke, or pasting an empty clipboard) leaves any
        existing selection untouched instead of silently deleting it."""
        s = "".join(ch for ch in s if ch.isprintable())
        if not s:
            return
        if self._has_selection():
            self._delete_selection()
        space = self.max_len - len(self.text)
        if space <= 0:
            return
        s = s[:space]
        self.text = self.text[:self.cursor_pos] + s + self.text[self.cursor_pos:]
        self.cursor_pos += len(s)
        self.cursor_blink = 0.0

    def wants_ibeam(self, mouse_pos):
        """True when `mouse_pos` is over the live input box, i.e. the
        cursor should show as a text I-beam rather than the arrow."""
        return (self.active and self.input_rect is not None
                and self.input_rect.collidepoint(mouse_pos))

    def _index_from_x(self, x):
        """Map an absolute mouse x-coordinate to the character index whose
        caret sits closest to it, so a click lands the cursor between the
        two nearest letters rather than always at the end of the text."""
        if self.input_rect is None or not self.text:
            return 0
        widths = [0]
        for i in range(1, len(self.text) + 1):
            widths.append(self.font_input.size(self.text[:i])[0])
        text_left = self.input_rect.centerx - widths[-1] // 2
        rel_x = x - text_left
        best_i, best_d = 0, abs(widths[0] - rel_x)
        for i, w in enumerate(widths):
            d = abs(w - rel_x)
            if d < best_d:
                best_i, best_d = i, d
        return best_i

    def handle_event(self, event):
        """Returns ('commit', text) / ('cancel', None) / None."""
        if not self.active:
            return None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.input_rect is not None and self.input_rect.collidepoint(event.pos):
                idx = self._index_from_x(event.pos[0])
                if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                    if self.selection_anchor is None:
                        self.selection_anchor = self.cursor_pos
                else:
                    self.selection_anchor = idx
                self.cursor_pos = idx
                self._dragging = True
                self.cursor_blink = 0.0
            return None

        if event.type == pygame.MOUSEMOTION:
            if self._dragging and self.input_rect is not None:
                # Clamp to the box so dragging past an edge still selects
                # to that edge, same as a normal desktop textbox.
                x = max(self.input_rect.left, min(event.pos[0], self.input_rect.right))
                self.cursor_pos = self._index_from_x(x)
                self.cursor_blink = 0.0
            return None

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._dragging = False
            return None

        if event.type != pygame.KEYDOWN:
            return None

        mods = pygame.key.get_mods()
        ctrl = bool(mods & (pygame.KMOD_CTRL | pygame.KMOD_META))
        shift = bool(mods & pygame.KMOD_SHIFT)

        if event.key == pygame.K_RETURN:
            text = self.text
            self.close()
            return ('commit', text)
        elif event.key == pygame.K_ESCAPE:
            self.close()
            return ('cancel', None)
        elif ctrl and event.key == pygame.K_a:
            self.selection_anchor = 0
            self.cursor_pos = len(self.text)
            self.cursor_blink = 0.0
        elif ctrl and event.key in (pygame.K_c, pygame.K_x):
            if self._has_selection():
                s, e = self._selection_range()
                clipboard_set_text(self.text[s:e])
                if event.key == pygame.K_x:
                    self._delete_selection()
                    self.cursor_blink = 0.0
        elif ctrl and event.key == pygame.K_v:
            self._insert_text(clipboard_get_text())
        elif event.key == pygame.K_LEFT:
            if shift:
                if self.selection_anchor is None:
                    self.selection_anchor = self.cursor_pos
                self.cursor_pos = max(0, self.cursor_pos - 1)
            elif self._has_selection():
                self.cursor_pos = self._selection_range()[0]
                self.selection_anchor = None
            else:
                self.cursor_pos = max(0, self.cursor_pos - 1)
            self.cursor_blink = 0.0
        elif event.key == pygame.K_RIGHT:
            if shift:
                if self.selection_anchor is None:
                    self.selection_anchor = self.cursor_pos
                self.cursor_pos = min(len(self.text), self.cursor_pos + 1)
            elif self._has_selection():
                self.cursor_pos = self._selection_range()[1]
                self.selection_anchor = None
            else:
                self.cursor_pos = min(len(self.text), self.cursor_pos + 1)
            self.cursor_blink = 0.0
        elif event.key == pygame.K_HOME:
            if shift and self.selection_anchor is None:
                self.selection_anchor = self.cursor_pos
            elif not shift:
                self.selection_anchor = None
            self.cursor_pos = 0
            self.cursor_blink = 0.0
        elif event.key == pygame.K_END:
            if shift and self.selection_anchor is None:
                self.selection_anchor = self.cursor_pos
            elif not shift:
                self.selection_anchor = None
            self.cursor_pos = len(self.text)
            self.cursor_blink = 0.0
        elif event.key == pygame.K_BACKSPACE:
            if not self._delete_selection() and self.cursor_pos > 0:
                self.text = self.text[:self.cursor_pos - 1] + self.text[self.cursor_pos:]
                self.cursor_pos -= 1
            self.cursor_blink = 0.0
        elif event.key == pygame.K_DELETE:
            if not self._delete_selection() and self.cursor_pos < len(self.text):
                self.text = self.text[:self.cursor_pos] + self.text[self.cursor_pos + 1:]
            self.cursor_blink = 0.0
        elif event.unicode and event.unicode.isprintable():
            self._insert_text(event.unicode)
        return None

    def update(self, dt):
        if self.active:
            self.cursor_blink += dt

    def draw(self, surface, screen_w, screen_h):
        if not self.active:
            return
        full_rect = pygame.Rect(0, 0, screen_w, screen_h)
        draw_rect_on(surface, (0, 0, 0, 160), full_rect, 0, 0)

        rect = pygame.Rect(0, 0, self.width, self.height)
        rect.center = (screen_w // 2, screen_h // 2)
        draw_panel(surface, rect, bg=Theme.PANEL_BG, border=Theme.GOLD, border_width=2)

        prompt = self.font_prompt.render("Enter new value:", True, Theme.TEXT_MUTED)
        blit_surface(surface, prompt, prompt.get_rect(centerx=rect.centerx, y=rect.y + 16), transient=True)

        input_rect = pygame.Rect(rect.x + 20, rect.y + 48, rect.w - 40, 40)
        self.input_rect = input_rect
        draw_panel(surface, input_rect, bg=(32, 36, 58, 255), border=(70, 76, 106),
                   border_width=1, radius=8, shadow=False)

        input_surf = self.font_input.render(self.text, True, Theme.TEXT_PRIMARY)
        text_rect = input_surf.get_rect(center=input_rect.center)

        if self._has_selection():
            s, e = self._selection_range()
            sx = text_rect.x + (self.font_input.size(self.text[:s])[0] if s else 0)
            ex = text_rect.x + (self.font_input.size(self.text[:e])[0] if e else 0)
            sel_rect = pygame.Rect(sx, text_rect.y, max(1, ex - sx), text_rect.height)
            draw_rect_on(surface, (*Theme.KI_BLUE, 90), sel_rect, 0, 0)

        blit_surface(surface, input_surf, text_rect, transient=True)

        if int(self.cursor_blink * 2) % 2 == 0:
            caret_w = self.font_input.size(self.text[:self.cursor_pos])[0] if self.cursor_pos else 0
            caret_x = text_rect.x + caret_w
            pygame.draw.line(
                surface, Theme.TEXT_PRIMARY,
                (caret_x, text_rect.y), (caret_x, text_rect.y + text_rect.height), 2
            )

        hint = self.font_hint.render(
            "ENTER to confirm · ESC to cancel · CTRL+C/V to copy/paste", True, Theme.TEXT_DIM
        )
        blit_surface(surface, hint, hint.get_rect(centerx=rect.centerx, y=rect.bottom - 26), transient=True)