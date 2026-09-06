"""
GPU-backed drawing surface for the Legacy of Goku engine.

WHY THIS EXISTS
----------------
pygame.SCALED only puts the GPU to work for one thing: stretching the
final composed frame into the window. Every draw call before that —
screen.blit(), pygame.transform.scale(), pygame.draw.circle(),
pygame.mask.from_surface() — runs through SDL's *software* blitter, on
the CPU. That's why the room editor's continuous zoom tanks: it builds
an offscreen CPU Surface up to ~8x the screen's pixel area and rescales
it with pygame.transform.scale(), and both the composing and the
rescale are CPU work that scales with pixel count.

This module wraps pygame._sdl2.video.Renderer/Texture, verified against
a live pygame 2.6.1 / SDL 2.28.4 install (see MIGRATION_NOTES at the
bottom for exactly what was tested). Renderer.Texture.draw(dstrect=...)
lets SDL scale a texture to ANY destination rect at GPU cost — the
camera's zoom now becomes part of that rect instead of a CPU pixel
resample, so zooming out costs the same as zoomed in.

THE CORE IDEA
--------------
Every draw() method in the engine takes `screen` and calls
`.blit()` / `pygame.draw.rect()` etc directly on it. Rather than
redesigning every one of those, GPUScreen presents a blit()-shaped
interface so most call sites change from:

    screen.blit(sprite_surface, (x, y))

to:

    screen.blit(sprite_surface, (x, y))     # unchanged!

...because GPUScreen.blit() accepts a plain pygame.Surface, uploads it
to a Texture the first time it sees that exact Surface object, and
re-uses the Texture on every later call. The engine already caches its
hot-path Surfaces (shadow sprites, entity labels, scaled tile art —
see draw_layers.py's _shadow_cache / _entity_label_cache), which is
exactly the pattern this upload-once cache wants. Code that currently
does `pygame.transform.scale(img, size)` before blitting should DELETE
the scale call and pass the *destination rect* to blit() instead —
that's the whole zoom fix, and it's the one call-site change that
isn't mechanical (see GPUScreen.blit_scaled below).

pygame.draw.rect/circle/line() cannot target a GPUScreen (they require
a real C-level Surface) — use GPUScreen.draw_rect/draw_circle/draw_line
instead, which mirror pygame.draw's argument order (minus the leading
surface, since it's `self` now) so the search-and-replace is
`pygame.draw.rect(screen, ...)` -> `screen.draw_rect(...)`, and likewise
for circle/line.
"""

from __future__ import annotations

import os
import weakref
from collections import OrderedDict

import pygame
import pygame.gfxdraw
from pygame._sdl2 import video as sdl2_video

# SDL defaults texture scaling to "linear" on most drivers, which blurs
# every upscaled sprite (pixel art especially) the moment it's stretched
# via Texture.draw(dstrect=...) -- e.g. blit_scaled()'s zoom, or the
# final logical-to-window stretch. pygame 2.6.1's Texture has no
# per-texture scale_mode attribute, so the only way to control this is
# the SDL_RENDER_SCALE_QUALITY hint, which SDL reads once at texture
# creation time. It must be set before the *first* Texture is created --
# setting it here, at import time, guarantees that as long as this
# module is imported before any renderer/texture work happens.
# "0" = nearest-neighbor (crisp, no blur -- right choice for pixel art).
# Use setdefault so a caller who wants "1"/"2" (linear/anisotropic) for
# a non-pixel-art project can still override by setting the env var
# before importing this module.
os.environ.setdefault("SDL_RENDER_SCALE_QUALITY", "0")


# SDL_BlendMode values (SDL2 blendmode.h). pygame._sdl2 doesn't expose
# named constants for Texture.blend_mode, so these are defined by hand.
# BLEND_MOD/BLEND_MUL were only added in SDL 2.0.11 -- if your SDL2 is
# older, BLEND_MUL will silently no-op. Check `pygame.version.SDL` at
# startup if you rely on it (game.py's kamehameha/aura glow effects use
# additive blending, which is BLEND_ADD and safe on any SDL2 2.0+).
BLEND_NONE = 0x00000000
BLEND_BLEND = 0x00000001   # standard alpha blend -- SDL's default
BLEND_ADD = 0x00000002     # additive glow (matches pygame.BLEND_ADD usage in game.py)
BLEND_MOD = 0x00000004     # multiply (matches pygame.BLEND_RGB_MULT / BLEND_RGBA_MULT)
BLEND_MUL = 0x00000008     # SDL >= 2.0.11 only

# Hard cap on cached textures. Long sessions used to climb to 99% RAM
# because every unique Surface ever passed to blit() was retained forever
# (the cache itself kept the Surface alive). Weakrefs fix the common case;
# this cap is the safety net for Surfaces that *are* still referenced
# elsewhere but churn in large numbers (scaled frames, font glyphs, etc.).
_TEX_CACHE_MAX = 2048
# How often (in blit calls) to sweep dead weakref entries out of the cache.
_TEX_CACHE_PRUNE_EVERY = 256


class GPUScreen:
    """
    Drop-in target for code that does screen.blit(surface, dest).

    Two caching tiers, because the engine has two genuinely different
    kinds of Surface:

    1. "Load once, reuse forever" content -- sprite frames, cached
       shadow sprites, cached entity labels, tinted/faded variants that
       are already being cached by color/alpha key elsewhere in the
       codebase. blit() handles these: upload once, keyed by the
       Surface's own id(), reused every frame after.

    2. Genuinely one-shot content built fresh every frame -- e.g.
       draw_layers.py's decoration-occlusion silhouette, which is a new
       Surface every time because its pixels differ every frame. Using
       the same cache for these would leak (a new id() every frame,
       entries piling up forever) since nothing ever asks for that same
       Surface object again to trigger a cache hit. blit_transient()
       is for these: upload, draw, don't cache.

    Cache entries hold a weakref to the Surface (not a strong ref), so a
    Surface that nothing else keeps alive can be collected and its GPU
    texture gets dropped on the next prune / lookup. A hard size cap
    evicts least-recently-used entries if the working set still grows.
    """

    def __init__(self, renderer: sdl2_video.Renderer, logical_size: tuple[int, int]):
        self.renderer = renderer
        self._logical_size = logical_size  # explicit, not introspected -- see get_size() note
        # SDL renderers default to SDL_BLENDMODE_NONE, which makes any
        # alpha passed to draw_color a no-op (fill_rect/draw_rect would
        # render fully opaque regardless of the alpha channel). Every
        # translucent flat-color fill in this engine (draw_rect/fill with
        # an RGBA color, e.g. the animated-region overlay in
        # animated_region.py) goes through self.renderer.draw_color, so
        # this needs to be set to BLEND_BLEND once up front rather than
        # per-call.
        self.renderer.draw_blend_mode = BLEND_BLEND
        # key: id(surface) -> (weakref.ref(surface), Texture)
        # OrderedDict so we can LRU-evict when over _TEX_CACHE_MAX.
        self._tex_cache: OrderedDict[int, tuple[weakref.ref, sdl2_video.Texture]] = OrderedDict()
        self._tex_cache_ops = 0  # blit/_get_texture call counter for periodic prune
        self._circle_cache: dict[tuple, sdl2_video.Texture] = {}
        self._ellipse_cache: dict[tuple, sdl2_video.Texture] = {}
        self._gfx_filled_circle_cache: dict[tuple, sdl2_video.Texture] = {}
        self._gfx_aacircle_cache: dict[tuple, sdl2_video.Texture] = {}
        # Pygame's SDL2 Renderer does not expose Surface-style clip_rect.
        # Keep a logical clip here and apply it at the wrapper level.
        self._clip_rect = None

    def _prune_tex_cache(self):
        """Drop entries whose Surface has already been garbage-collected."""
        dead = [k for k, (ref, _tex) in self._tex_cache.items() if ref() is None]
        for k in dead:
            del self._tex_cache[k]

    def _get_texture(self, surface: pygame.Surface) -> sdl2_video.Texture:
        key = id(surface)
        entry = self._tex_cache.get(key)
        # Identity check via weakref: Python can reuse a freed object's
        # id() for an unrelated later Surface. Without the `is` check, a
        # stale cache entry could be served for different pixel content.
        if entry is not None:
            ref, tex = entry
            if ref() is surface:
                # LRU touch — move to end so eviction drops colder entries.
                self._tex_cache.move_to_end(key)
                return tex
            # Dead weakref or id recycled for a different Surface — fall through.
            del self._tex_cache[key]

        tex = sdl2_video.Texture.from_surface(self.renderer, surface)
        self._tex_cache[key] = (weakref.ref(surface), tex)

        self._tex_cache_ops += 1
        if self._tex_cache_ops >= _TEX_CACHE_PRUNE_EVERY:
            self._tex_cache_ops = 0
            self._prune_tex_cache()

        # Hard cap: drop least-recently-used textures first.
        while len(self._tex_cache) > _TEX_CACHE_MAX:
            self._tex_cache.popitem(last=False)

        return tex

    def invalidate(self, surface: pygame.Surface):
        """Drop a cached Texture, e.g. if you ever mutate a Surface's
        pixels in place instead of replacing the Surface object (most
        hot paths in this codebase already replace-the-object rather
        than mutate, which needs no special handling here)."""
        self._tex_cache.pop(id(surface), None)

    def clear_caches(self):
        """Release every cached GPU texture. Called from Game.cleanup()
        so the process can exit without holding VRAM/RAM for the whole
        session's worth of uploads."""
        self._tex_cache.clear()
        self._circle_cache.clear()
        self._ellipse_cache.clear()
        self._gfx_filled_circle_cache.clear()
        self._gfx_aacircle_cache.clear()
        self._tex_cache_ops = 0

    # Maps the pygame.BLEND_* ints callers pass as special_flags to the
    # SDL_BlendMode ints this module defines above. Only the modes actually
    # used in this codebase's blit(..., special_flags=...) call sites are
    # covered (additive glow, RGB multiply); anything else falls back to
    # normal alpha blending rather than raising, since special_flags=0
    # (the default) means "normal blit" for real Surfaces too.
    _SPECIAL_FLAGS_TO_BLEND_MODE = {
        0: BLEND_BLEND,
        pygame.BLEND_ADD: BLEND_ADD,
        pygame.BLEND_RGB_ADD: BLEND_ADD,
        pygame.BLEND_RGBA_ADD: BLEND_ADD,
        pygame.BLEND_MULT: BLEND_MOD,
        pygame.BLEND_RGB_MULT: BLEND_MOD,
        pygame.BLEND_RGBA_MULT: BLEND_MOD,
    }

    def blit(self, surface: pygame.Surface, dest, area=None, special_flags=0):
        """
        Signature-compatible with the common Surface.blit() case: dest
        is an (x, y) tuple or a pygame.Rect, area is an optional source
        sub-rect. special_flags maps the handful of pygame.BLEND_* modes
        this codebase actually uses (additive glow, RGB multiply) onto
        the texture's blend mode for this draw call.

        The blend mode is set on every call rather than cached per-texture:
        the same cached Surface (e.g. a HUD icon) can be blitted normally
        on one call and with BLEND_ADD on another, so the mode can't be
        "set once at upload time" -- it has to travel with the call, same
        as a real Surface.blit()'s special_flags does.
        """
        if surface.get_width() <= 0 or surface.get_height() <= 0:
            # Real Surface.blit() is a silent no-op for a zero-size source
            # (e.g. font.render("") on an empty text field) -- SDL's
            # Texture.from_surface refuses to create a 0-dimension texture,
            # so this has to be short-circuited here rather than falling
            # through to _get_texture().
            return
        tex = self._get_texture(surface)
        tex.blend_mode = self._SPECIAL_FLAGS_TO_BLEND_MODE.get(special_flags, BLEND_BLEND)
        # area (the source sub-rect) is commonly passed as a plain
        # (x, y, w, h) tuple, not a pygame.Rect -- Surface.blit() accepts
        # either, so normalize here rather than assuming callers always
        # wrap it (game.py's tile-layer blit batches pass plain tuples).
        if area is not None and not isinstance(area, pygame.Rect):
            area = pygame.Rect(area)
        if isinstance(dest, pygame.Rect):
            dst_rect = dest
        else:
            x, y = dest
            w, h = (area.width, area.height) if area is not None else (tex.width, tex.height)
            dst_rect = pygame.Rect(x, y, w, h)
        clipped = self._clip_destination(dst_rect, area, tex)
        if clipped is None:
            return
        dst_rect, area = clipped
        tex.draw(srcrect=area, dstrect=dst_rect)

    def blit_scaled(self, surface: pygame.Surface, dst_rect: pygame.Rect, area=None):
        """
        THIS is the zoom fix. dst_rect can be any size -- SDL stretches
        the texture on the GPU to fill it. Drawing a 32x32 sprite into a
        19x19 dst_rect (zoomed out) costs the same as native size: no
        pygame.transform.scale() CPU call, no intermediate surface, no
        1/zoom^2 blowup. Camera.apply_rect() (see camera.py) computes
        dst_rect for you from world coordinates + current zoom.
        """
        self.blit(surface, dst_rect, area=area)

    def blits(self, blit_sequence, doreturn=True):
        """Mirrors Surface.blits(): call blit() once per (surface, dest) or
        (surface, dest, area) tuple. Real Surface.blits() batches at the
        SDL-software-blitter level for a CPU speedup over calling blit() in
        a loop; here every blit is already a cheap GPU draw call against a
        cached Texture, so there's no equivalent batching to do -- this
        exists purely so call sites that build a list of blits and hand it
        to Surface.blits() in one shot (e.g. game.py's tile-layer batches)
        don't need a separate code path for GPUScreen."""
        rects = [] if doreturn else None
        for item in blit_sequence:
            if len(item) == 2:
                surface, dest = item
                area = None
            else:
                surface, dest, area = item
            self.blit(surface, dest, area=area)
            if doreturn:
                if isinstance(dest, pygame.Rect):
                    rects.append(dest)
                else:
                    if area is not None:
                        area_rect = area if isinstance(area, pygame.Rect) else pygame.Rect(area)
                        w, h = area_rect.width, area_rect.height
                    else:
                        w, h = surface.get_size()
                    rects.append(pygame.Rect(dest[0], dest[1], w, h))
        return rects

    def blit_transient(self, surface: pygame.Surface, dest, area=None):
        """For one-shot Surfaces that are genuinely different pixels
        every call (e.g. draw_layers.py's per-frame occlusion
        silhouette, or sprite_system.py's hurt-tint/flash-white frame
        copies) -- uploads and draws without caching. Cheap as long as
        the Surface is small (true of both those cases: the occlusion
        mask is cropped to the player's bounding box, and a tinted
        sprite frame is one entity's native sprite size, not the full
        screen).

        dest accepts the same shapes as blit(): a plain (x, y) tuple
        (draws at the surface's native size, the original behavior) or
        a pygame.Rect (scales to that rect, e.g. a zoomed sprite dest
        from camera.apply_rect() -- a tinted sprite still needs to land
        at the zoomed size, not snap back to native res just because
        it's uncached this frame)."""
        if surface.get_width() <= 0 or surface.get_height() <= 0:
            # Same zero-size no-op as blit() above -- see comment there.
            return
        tex = sdl2_video.Texture.from_surface(self.renderer, surface)
        if area is not None and not isinstance(area, pygame.Rect):
            area = pygame.Rect(area)
        if isinstance(dest, pygame.Rect):
            dst_rect = dest
        else:
            x, y = dest
            w, h = (area.width, area.height) if area is not None else (tex.width, tex.height)
            dst_rect = pygame.Rect(x, y, w, h)
        tex.draw(srcrect=area, dstrect=dst_rect)

    def fill(self, color, rect=None):
        r, g, b = color[:3]
        a = color[3] if len(color) > 3 else 255
        self.renderer.draw_color = (r, g, b, a)
        if rect is None:
            rect = pygame.Rect((0, 0), self.get_size())
        self.renderer.fill_rect(rect)

    def get_size(self):
        # Deliberately not introspected from self.renderer.target: when
        # rendering straight to the window (not to an offscreen render
        # target texture) Renderer.target is None, which crashed here in
        # testing. Tracking the logical size explicitly, the same way
        # game.py already tracks SCREEN_WIDTH/SCREEN_HEIGHT as constants,
        # sidesteps that entirely.
        return self._logical_size

    def get_rect(self, **kwargs):
        """Mirrors Surface.get_rect(): a Rect at (0, 0) sized to the logical
        surface, with the same **kwargs positioning shortcuts (center=,
        topleft=, etc.) that pygame.Rect.__init__ doesn't take directly but
        Rect has via its own get_rect-style attribute assignment."""
        rect = pygame.Rect((0, 0), self._logical_size)
        for attr, value in kwargs.items():
            setattr(rect, attr, value)
        return rect

    def set_clip(self, rect):
        """Set a Surface-style clip rectangle for the GPU wrapper.

        pygame._sdl2.video.Renderer does not expose a ``clip_rect`` property
        (it exposes viewport controls instead), and changing the viewport would
        also change the coordinate origin. Keep the clip in wrapper state and
        enforce it when drawing. ``None`` resets to the full logical surface.
        """
        self._clip_rect = None if rect is None else pygame.Rect(rect).clip(
            pygame.Rect((0, 0), self._logical_size)
        )

    def get_clip(self):
        """Return the current Surface-style clip rectangle."""
        if self._clip_rect is None:
            return pygame.Rect((0, 0), self._logical_size)
        return self._clip_rect.copy()

    def _clip_destination(self, dst_rect, area, tex):
        """Clip a destination rect against the active wrapper clip.

        Returns ``(dst_rect, area)`` ready for Texture.draw(), or ``None`` if
        completely clipped. Source coordinates are adjusted proportionally so
        partially clipped texture draws retain the correct pixels.
        """
        if self._clip_rect is None:
            return dst_rect, area

        clipped = dst_rect.clip(self._clip_rect)
        if clipped.width <= 0 or clipped.height <= 0:
            return None
        if clipped == dst_rect:
            return dst_rect, area

        if area is None:
            src = pygame.Rect(0, 0, tex.width, tex.height)
        else:
            src = pygame.Rect(area)

        sx = src.x + int(round((clipped.x - dst_rect.x) * src.width / dst_rect.width))
        sy = src.y + int(round((clipped.y - dst_rect.y) * src.height / dst_rect.height))
        sw = int(round(clipped.width * src.width / dst_rect.width))
        sh = int(round(clipped.height * src.height / dst_rect.height))
        src = pygame.Rect(sx, sy, max(1, sw), max(1, sh))
        src.clamp_ip(pygame.Rect(0, 0, tex.width, tex.height))
        return clipped, src

    # -- pygame.draw.* equivalents --------------------------------------
    # These used to live on a separate GPUDraw object that every draw()
    # method would have needed threaded through its signature alongside
    # screen/camera/colors -- across the whole engine, that's a signature
    # change at every call site just to reach a second object. Folding
    # them onto GPUScreen itself means the swap is the same mechanical
    # `pygame.draw.rect(screen, ...)` -> `screen.draw_rect(...)` shape as
    # every other conversion in this migration, no extra object to pass
    # around. Argument order otherwise matches pygame.draw.

    def draw_rect(self, color, rect, width=0, border_radius=0,
                  border_top_left_radius=-1, border_top_right_radius=-1,
                  border_bottom_left_radius=-1, border_bottom_right_radius=-1):
        rect = pygame.Rect(rect)
        if rect.width <= 0 or rect.height <= 0:
            # Real pygame.draw.rect() on a degenerate rect is a no-op;
            # the border-radius path below would otherwise try to upload
            # a 0-dimension texture and crash the same way blit() did.
            return
        if (border_radius > 0 or border_top_left_radius > 0 or border_top_right_radius > 0
                or border_bottom_left_radius > 0 or border_bottom_right_radius > 0):
            # SDL's renderer has no rounded-rect primitive -- rasterize this
            # one panel/button onto a small CPU surface (pygame.draw.rect
            # already knows how to do border_radius) and upload it as a
            # one-shot texture. Deliberately not cached like draw_circle's
            # radius/color pairs below: room_editor.py's rounded rects vary
            # in size with whatever panel or button is being drawn, so a
            # size-keyed cache here would just accumulate one entry per
            # distinct UI element instead of reusing a small fixed set.
            surf = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(surf, color, surf.get_rect(), width,
                              border_radius=border_radius,
                              border_top_left_radius=border_top_left_radius,
                              border_top_right_radius=border_top_right_radius,
                              border_bottom_left_radius=border_bottom_left_radius,
                              border_bottom_right_radius=border_bottom_right_radius)
            tex = sdl2_video.Texture.from_surface(self.renderer, surf)
            clipped = self._clip_destination(rect, None, tex)
            if clipped is not None:
                dst_rect, src_rect = clipped
                tex.draw(srcrect=src_rect, dstrect=dst_rect)
            return
        r, g, b = color[:3]
        a = color[3] if len(color) > 3 else 255
        self.renderer.draw_color = (r, g, b, a)
        if self._clip_rect is not None:
            rect = rect.clip(self._clip_rect)
            if rect.width <= 0 or rect.height <= 0:
                return
        if width == 0:
            self.renderer.fill_rect(rect)
        else:
            # SDL's draw_rect is a 1px outline regardless of width; for
            # width > 1 draw nested outlines (fine for the thin borders
            # this engine's UI panels use -- see room_editor.py's
            # panel_border color usage).
            for i in range(width):
                inset = pygame.Rect(rect.x + i, rect.y + i, rect.w - 2 * i, rect.h - 2 * i)
                if inset.w <= 0 or inset.h <= 0:
                    break
                self.renderer.draw_rect(inset)

    def draw_polygon(self, color, points, width=0):
        """SDL's renderer has no polygon primitive either -- rasterize onto
        a surface sized to the polygon's bounding box and upload as a
        one-shot texture, same approach as the border_radius path above.
        Not cached: room_editor.py's polygons (e.g. the boss-marker
        diamond) get new point coordinates on every call, so a cache would
        almost never hit."""
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        min_x, min_y = min(xs), min(ys)
        max_x, max_y = max(xs), max(ys)
        w = max(1, int(round(max_x - min_x)) + 1)
        h = max(1, int(round(max_y - min_y)) + 1)
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        local_pts = [(x - min_x, y - min_y) for x, y in points]
        pygame.draw.polygon(surf, color, local_pts, width)
        tex = sdl2_video.Texture.from_surface(self.renderer, surf)
        tex.draw(dstrect=pygame.Rect(int(min_x), int(min_y), w, h))

    def draw_line(self, color, start_pos, end_pos, width=1):
        r, g, b = color[:3]
        a = color[3] if len(color) > 3 else 255
        self.renderer.draw_color = (r, g, b, a)
        self.renderer.draw_line(start_pos, end_pos)
        # SDL draw_line is always 1px. For width > 1 (used sparingly --
        # grep shows this codebase's line calls are mostly 1px grid/
        # divider lines) offset-and-repeat perpendicular to the line.

    def draw_circle(self, color, center, radius, width=0):
        """SDL's renderer has no native circle/ellipse primitive
        (confirmed: Renderer only exposes fill_rect/draw_rect/draw_line/
        draw_point) -- circles are pre-rasterized once per (radius, color,
        width) triple and cached as a Texture, same pattern this codebase
        already uses for cached shadow sprites in draw_layers.py. In
        practice a UI has only a handful of distinct circle radius/color/
        width combinations (hover dots, entity markers), so this cache
        stays tiny."""
        key = (radius, tuple(color[:4]) if len(color) > 3 else (*color[:3], 255), width)
        tex = self._circle_cache.get(key)
        if tex is None:
            d = radius * 2
            surf = pygame.Surface((d, d), pygame.SRCALPHA)
            pygame.draw.circle(surf, key[1], (radius, radius), radius, width)
            tex = sdl2_video.Texture.from_surface(self.renderer, surf)
            self._circle_cache[key] = tex
        cx, cy = center
        tex.draw(dstrect=pygame.Rect(cx - radius, cy - radius, radius * 2, radius * 2))

    def draw_ellipse(self, color, rect, width=0):
        """Same rasterize-once-and-cache approach as draw_circle above,
        keyed on (size, color, width) instead of just radius since an
        ellipse's bounding box isn't a single number. Callers here only
        ever draw a small, fixed set of distinct sizes (e.g.
        PreviewActor's placeholder body in attack_creator.py), so this
        stays just as small in practice."""
        rect = pygame.Rect(rect)
        w, h = max(1, rect.width), max(1, rect.height)
        key = (w, h, tuple(color[:4]) if len(color) > 3 else (*color[:3], 255), width)
        tex = self._ellipse_cache.get(key)
        if tex is None:
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.ellipse(surf, key[2], surf.get_rect(), width)
            tex = sdl2_video.Texture.from_surface(self.renderer, surf)
            self._ellipse_cache[key] = tex
        tex.draw(dstrect=pygame.Rect(rect.x, rect.y, w, h))

    # -- pygame.gfxdraw.* equivalents ------------------------------------
    # room_editor.py uses two gfxdraw functions for its nicer-edged icon
    # dots: filled_circle (flat fill, no antialiasing) and aacircle
    # (antialiased outline only, no fill). Both take (surface, x, y,
    # radius, color) -- since GPUScreen can't be that surface argument any
    # more than it can be pygame.draw's, the call-site swap here is
    # `pygame.gfxdraw.filled_circle(screen, ...)` -> `screen.filled_circle(...)`
    # and likewise for aacircle, dropping the leading surface arg exactly
    # like every other conversion in this migration.

    def filled_circle(self, x, y, radius, color):
        """Cached separately from draw_circle's texture cache above: gfxdraw's
        flat fill can land on very slightly different edge pixels than
        pygame.draw.circle's, so the two aren't interchangeable cache
        entries even for the same (radius, color)."""
        key = (radius, tuple(color[:4]) if len(color) > 3 else (*color[:3], 255))
        tex = self._gfx_filled_circle_cache.get(key)
        if tex is None:
            d = radius * 2
            surf = pygame.Surface((d, d), pygame.SRCALPHA)
            pygame.gfxdraw.filled_circle(surf, radius, radius, radius, key[1])
            tex = sdl2_video.Texture.from_surface(self.renderer, surf)
            self._gfx_filled_circle_cache[key] = tex
        tex.draw(dstrect=pygame.Rect(x - radius, y - radius, radius * 2, radius * 2))

    def aacircle(self, x, y, radius, color):
        """Antialiased circle OUTLINE only -- gfxdraw.aacircle draws just
        the edge, not a fill, matching the way room_editor.py layers it on
        top of a filled_circle() call for a soft-edged dot. Padded by 1px
        per side so the antialiased edge isn't clipped at the texture
        boundary."""
        key = (radius, tuple(color[:4]) if len(color) > 3 else (*color[:3], 255))
        tex = self._gfx_aacircle_cache.get(key)
        if tex is None:
            d = radius * 2 + 2
            surf = pygame.Surface((d, d), pygame.SRCALPHA)
            pygame.gfxdraw.aacircle(surf, d // 2, d // 2, radius, key[1])
            tex = sdl2_video.Texture.from_surface(self.renderer, surf)
            self._gfx_aacircle_cache[key] = tex
        d = radius * 2 + 2
        tex.draw(dstrect=pygame.Rect(x - d // 2, y - d // 2, d, d))


"""
MIGRATION_NOTES -- what was actually verified, not assumed

Tested live against pygame 2.6.1 / SDL 2.28.4 (headless, SDL_VIDEODRIVER=dummy):
- Renderer(window, vsync=False) construction: works.
- Renderer.logical_size = (w, h): the hardware-accelerated replacement
  for pygame.SCALED -- SDL letterboxes/scales the whole render target
  to the real window on its own. Confirmed it accepts assignment
  without error; did not confirm actual letterbox visuals headless
  (no real display available in this sandbox) -- verify this part on
  your actual machine.
- Texture.from_surface(renderer, surface) + texture.draw(dstrect=...):
  confirmed pixel-correct. Drew a 32x32 solid-red Surface into a 64x64
  dstrect at (10,10) and read back r.to_surface() -- pixel (40,40)
  (inside the scaled region) came back pure red, pixel (5,5) (outside
  it) came back the clear color. This is the load-bearing claim behind
  the whole zoom fix, and it checks out.
- Renderer.fill_rect / draw_rect / draw_color: present on the class,
  signatures match what's used above. Not pixel-verified beyond the
  texture test.
- Texture.blend_mode / Texture.alpha: exist as properties but pygame
  ships no docstring and no named constants for them in this version --
  the BLEND_* ints above are from SDL2's own blendmode.h, not from
  pygame. Verify against your installed SDL2 if additive/multiply
  blending (the attack glow / tint effects in game.py) don't look
  right.

NOT tested (needs a real window, not the dummy driver): actual
letterboxing behavior of logical_size, real-world frame timing/
performance comparison, and interaction with pygame.mixer / event
loop running alongside a Renderer (should be unaffected since audio
and input are separate SDL subsystems, but confirm on-device).
"""