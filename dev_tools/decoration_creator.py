"""
decoration_creator.py  -  Dev-menu Decoration Creator
======================================================

A dedicated editor for the scenery/decorations used by the Object Editor.

Rebuilt on dev_tools/ui_kit.py (the gold / ki-blue "modern DBZ" dev-tool
look) so it matches DevMenu, CharacterCreator and EntityCreator exactly. The
old hand-rolled pygame.Surface UI (C_* palette, TextField, Slider,
DecorationList, button()/panel()/text() helpers and every old draw / hit-test
routine) is gone entirely - every widget here draws through ui_kit's
GPUScreen-safe primitives plus this file's small immediate-mode widget layer
(same hover/press easing, same hairline-card language, same scrolling panel
pattern as the other creators). Only the *functionality* is carried over -
the discovery scan, the ``decoration.json`` schema, load/save, the sequence
tools and the collision maths are unchanged.

The host game owns the event loop and simply calls ``toggle()`` /
``handle_input()`` / ``update()`` / ``draw()`` every frame::

    from dev_tools import decoration_creator
    self.decoration_creator = decoration_creator.DecorationCreator(W, H)
    self.decoration_creator.on_catalog_changed = self._refresh_decoration_catalog
    # event loop:
    if self.decoration_creator.active:
        result = self.decoration_creator.handle_input(event)   # 'back_to_dev_menu'
    # every frame:
    self.decoration_creator.update(dt)
    self.decoration_creator.draw(self.logical_surface, dt)

Features
--------
* Discovers decoration assets under ``assets/objects/decorations/``.
* Shows the complete decoration roster in a left-hand list, with a live
  animated preview underneath it.
* Preview tab: large animated preview with playback control.
* Animation tab: visual spritesheet/frame layout editor and a visual
  animation-sequence builder - click frames in the order they should play
  instead of typing a raw list.
* Collision tab: drag the collision box and resize it with corner handles
  directly over the sprite, or type exact numbers. Auto-collision mode for
  decorations where a manual box is not necessary.
* Saves each discovered decoration's settings to ``decoration.json`` beside
  its art.
* Decorations flagged ``hardcoded`` keep their hand-authored animation
  protected (collision stays editable as a sidecar override). No decoration
  is currently flagged - Tree is editable like any other.

Recommended asset layout
------------------------
assets/
  objects/
    decorations/
      tree/
        tree.png                 # existing hardcoded decoration
      bush/
        bush.png                 # automatically discovered
        decoration.json         # written by this creator

The JSON format matches ``objects/decoration_objects.py``:

{
  "label": "Bush",
  "sheet_path": "bush.png",
  "frame_w": 32,
  "frame_h": 48,
  "frame_count": 4,
  "grid_rows": 1,
  "sequence": [1, 2, 3, 2],
  "fps": 6,
  "variants": ["Bush"],
  "collision_rect": [10, 30, 12, 16]
}

For automatic collision, the ``collision_rect`` key is simply omitted. The
runtime will then use its automatic base-collision inference.
"""

from __future__ import annotations

import copy
import json
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pygame

import dev_tools.ui_kit as uk
from objects.decoration_objects import (
    DECORATION_STYLES,
    discover_decoration_styles,
    reload_decoration_styles,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    # This file belongs in dev_tools/, so project root is one level above it.
    BASE_DIR = Path(__file__).resolve().parent.parent

DECORATIONS_DIR = BASE_DIR / "assets" / "objects" / "decorations"
IMAGE_EXTENSIONS = {".png", ".webp", ".jpg", ".jpeg"}

TAB_PREVIEW = 0
TAB_ANIMATION = 1
TAB_COLLISION = 2
TAB_NAMES = ("Preview", "Animation", "Collision")


# ---------------------------------------------------------------------------
# Small data helpers (unchanged)
# ---------------------------------------------------------------------------
def clamp_int(value, lo, hi, default):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def clamp_float(value, lo, hi, default):
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def pretty_id(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip().title()


def rect_tuple(value) -> Optional[tuple[int, int, int, int]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x, y, w, h = [int(round(float(v))) for v in value]
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def relative_asset_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(BASE_DIR.resolve()).as_posix()
    except ValueError:
        return str(path).replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Decoration discovery / catalog helpers
# ---------------------------------------------------------------------------
def _root_items() -> list[dict]:
    """Build the editor roster, preserving the hardcoded tree first."""
    merged = discover_decoration_styles()
    merged.update(DECORATION_STYLES)

    ids = list(merged.keys())
    if "tree" in ids:
        ids.remove("tree")
        ids.insert(0, "tree")

    result = []
    for deco_id in ids:
        style = DECORATION_STYLES.get(deco_id, merged.get(deco_id, {}))
        # Tree used to be force-locked here so its hand-authored animation
        # couldn't be overwritten. That protection is removed: Tree is now
        # editable like any other decoration.
        hardcoded = False

        folder = DECORATIONS_DIR / deco_id
        image_path = None
        manifest_path = None

        if folder.is_dir():
            if (folder / "decoration.json").exists():
                manifest_path = folder / "decoration.json"
            configured = style.get("sheet_path")
            if configured:
                candidate = BASE_DIR / str(configured)
                if candidate.is_file():
                    image_path = candidate
            preferred = folder / f"{deco_id}.png"
            if image_path is None and preferred.is_file():
                image_path = preferred
            if image_path is None:
                files = sorted(p for p in folder.iterdir()
                               if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)
                if files:
                    image_path = files[0]
        else:
            # Root-level compatibility: rock.png + optional rock.json.
            for ext in IMAGE_EXTENSIONS:
                candidate = DECORATIONS_DIR / f"{deco_id}{ext}"
                if candidate.is_file():
                    image_path = candidate
                    break
            root_manifest = DECORATIONS_DIR / f"{deco_id}.json"
            if root_manifest.exists():
                manifest_path = root_manifest

        if image_path is None:
            configured = style.get("sheet_path")
            if configured:
                candidate = BASE_DIR / str(configured)
                if candidate.is_file():
                    image_path = candidate

        result.append({
            "id": deco_id,
            "label": style.get("label", pretty_id(deco_id)),
            "style": copy.deepcopy(style),
            "hardcoded": hardcoded,
            "folder": folder,
            "image": image_path,
            "manifest": manifest_path,
        })

    return result


def _safe_json(path: Optional[Path]) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _infer_collision(frame: Optional[pygame.Surface]) -> Optional[tuple[int, int, int, int]]:
    """Same general philosophy as runtime auto collision: compact base box."""
    if frame is None:
        return None
    fw, fh = frame.get_size()
    start_y = min(fh - 1, max(0, int(fh * 0.65)))
    try:
        region = frame.subsurface((0, start_y, fw, max(1, fh - start_y)))
        bbox = region.get_bounding_rect(min_alpha=32)
    except (pygame.error, ValueError):
        bbox = pygame.Rect(0, 0, 0, 0)

    if bbox.width <= 0 or bbox.height <= 0:
        w = max(4, int(fw * 0.22))
        h = max(4, int(fh * 0.14))
        return max(0, (fw - w) // 2), max(0, fh - h - 2), w, h

    width = min(bbox.width, max(4, int(fw * 0.45)))
    height = min(bbox.height, max(4, int(fh * 0.30)))
    cx = bbox.x + bbox.width / 2.0
    x = int(round(cx - width / 2.0))
    y = start_y + bbox.y
    x = max(0, min(x, fw - width))
    y = max(0, min(y, fh - height))
    return x, y, width, height


def _collision_from_style(style: dict, frame: Optional[pygame.Surface]) -> Optional[tuple[int, int, int, int]]:
    """Return the runtime collision box in frame-local coordinates.

    ``collision_rect`` is the explicit form. ``collision_size`` mirrors
    Decoration's legacy base-anchor behavior: centered horizontally and
    placed 10px above the sprite's bottom anchor. Otherwise use alpha-based
    inference for newly discovered decorations.
    """
    if frame is None:
        return None

    explicit = rect_tuple(style.get("collision_rect"))
    if explicit is not None:
        fw, fh = frame.get_size()
        x, y, w, h = explicit
        w = max(1, min(w, fw))
        h = max(1, min(h, fh))
        x = max(0, min(x, fw - w))
        y = max(0, min(y, fh - h))
        return x, y, w, h

    size = style.get("collision_size")
    if isinstance(size, (list, tuple)) and len(size) == 2:
        try:
            fw, fh = frame.get_size()
            w = max(1, min(int(size[0]), fw))
            h = max(1, min(int(size[1]), fh))
            x = max(0, (fw - w) // 2)
            y = max(0, min(fh - h, fh - 10 - h))
            return x, y, w, h
        except (TypeError, ValueError):
            pass

    return _infer_collision(frame)


# ══════════════════════════════════════════════════════════════════════
#  UI layer  -  built entirely on dev_tools/ui_kit.py, same visual
#  language and widget-behaviour conventions as character_creator.py and
#  entity_creator.py (immediate-mode: draw() computes hit-rects consumed by
#  the next frame's events, hover/press values ease and quantise for
#  cache-friendliness, a small _TextEdit engine backs every text field).
#
#  NOTE: the bitmap menu font only has letters, digits and  . , ! ? : - + /
#  _ ( ) '  - any other character renders as '?', so every UI string below
#  sticks to that set (e.g. "x" rather than a multiplication sign).
# ══════════════════════════════════════════════════════════════════════

_T = uk.Theme

# Same flat two-tone backdrop / bar colours DevMenu and the other creators use.
_BG        = (8, 11, 17)
_BAND      = (10, 13, 20)
_BAR       = (12, 15, 23)
_HAIR      = (43, 49, 63)
_CARD      = (22, 26, 35)
_CARD_HI   = (28, 33, 44)
_FIELD     = (20, 23, 32)
_FIELD_HI  = (27, 31, 42)
_INSET     = (11, 14, 21)
_TRACK     = (34, 39, 53)
_SEL       = (31, 36, 49)
_GRID      = (72, 80, 102)          # spritesheet cell lines
_GROUND    = (120, 96, 44)          # dim-gold ground / anchor line

SEQ_CELL = 44                       # sequence-strip cell size
SEQ_GAP = 6
SEQ_ARROW = 34


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _fit_scale(box_w, box_h, w, h, cap):
    """Scale that fits (w, h) in the box. Prefers a whole-number up-scale
    (crisp pixel art, one sprite pixel == a whole number of screen pixels),
    but falls back to the exact fractional fit when rounding down would waste
    a lot of space (e.g. 1.8x must not become 1x)."""
    s = min(box_w / max(1, w), box_h / max(1, h))
    if s >= 1.0:
        s = min(s, float(cap))
        k = float(int(s))
        return k if k >= 0.7 * s else s
    return max(0.05, s)


def _digit_ok(ch: str) -> bool:
    return ch in "0123456789"


# ── Bitmap font wrapper (same shape as character_creator._Font) ────────

class _Font:
    """One BitmapFont pinned to one pixel height, plus the text metrics the
    layout code needs. See character_creator.py's identical class for the
    full rationale (glyph fallback to '?', baseline-based positioning,
    arithmetic width instead of per-prefix rendering)."""

    def __init__(self, bitmap, height):
        self.bm = bitmap
        self.height = int(height)
        self._ok = {}
        self._adv = {}
        self._spacing = None
        native = max(1, bitmap.size("A")[1])
        self.scale = max(1, int(round(self.height / native)))
        self.cap_h = max(1, bitmap.size("A", height=self.height)[1])
        offs = getattr(bitmap, "glyph_y_offsets", None) or {}
        self.desc_h = max(offs.values(), default=0) * self.scale
        self.line_h = self.cap_h + self.desc_h

    def _has(self, ch):
        ok = self._ok.get(ch)
        if ok is None:
            try:
                ok = ch == " " or self.bm._glyph(ch) is not None
            except AttributeError:
                ok = True
            self._ok[ch] = ok
        return ok

    def disp(self, text):
        for ch in text:
            if not self._has(ch):
                return "".join(c if self._has(c) else "?" for c in text)
        return text

    def _advance(self, ch):
        a = self._adv.get(ch)
        if a is None:
            a = self.bm.size(ch, height=self.height)[0]
            self._adv[ch] = a
        return a

    def _sp(self):
        if self._spacing is None:
            aa = self.bm.size("AA", height=self.height)[0]
            self._spacing = max(0, aa - 2 * self._advance("A"))
        return self._spacing

    def width(self, text):
        if not text:
            return 0
        d = self.disp(text)
        return sum(self._advance(c) for c in d) + self._sp() * (len(d) - 1)

    def render(self, text, color):
        d = self.disp(text)
        surf = self.bm.render(d, color=tuple(color), height=self.height)
        offs = getattr(self.bm, "glyph_y_offsets", None) or {}
        desc = 0
        for c in set(d):
            o = offs.get(c, 0)
            if o > desc:
                desc = o
        return surf, desc * self.scale

    def fit(self, text, max_w):
        if self.width(text) <= max_w:
            return text
        ell = "..."
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.width(text[:mid].rstrip() + ell) <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return (text[:lo].rstrip() + ell) if lo else ell

    def wrap(self, text, max_w):
        lines = []
        for para in text.split("\n"):
            cur = ""
            for word in para.split(" "):
                trial = f"{cur} {word}" if cur else word
                if cur and self.width(trial) > max_w:
                    lines.append(cur)
                    cur = word
                else:
                    cur = trial
            lines.append(cur)
        return lines


# ── Text editing engine (no drawing) ────────────────────────────────────

def _index_at_x(font, line_text, x):
    if x <= 0 or not line_text:
        return 0
    shown = font.disp(line_text)
    sp = font._sp()
    best_i, best_d = 0, x
    acc = 0
    for i, ch in enumerate(shown, 1):
        acc += font._advance(ch) + (sp if i > 1 else 0)
        d = abs(acc - x)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


class _TextEdit:
    """Caret / selection / clipboard logic shared by every text field. Pure
    state - the creator draws it and feeds it keys. key() returns None,
    'changed', 'commit' or 'cancel'. Single-line only (no field in this
    tool needs more)."""

    def __init__(self, value="", max_len=600, allowed=None):
        self.value = value
        self.cursor = len(value)
        self.anchor = None
        self.max_len = max_len
        self.allowed = allowed
        self.blink = 0.0

    def has_sel(self):
        return self.anchor is not None and self.anchor != self.cursor

    def sel_range(self):
        a, b = self.anchor, self.cursor
        return (a, b) if a <= b else (b, a)

    def _del_sel(self):
        if not self.has_sel():
            return False
        s, e = self.sel_range()
        self.value = self.value[:s] + self.value[e:]
        self.cursor = s
        self.anchor = None
        return True

    def insert(self, text):
        text = text.replace("\r", "").replace("\n", " ")
        keep = [ch for ch in text
                if ch.isprintable() and (self.allowed is None or self.allowed(ch))]
        text = "".join(keep)
        if not text:
            return False
        changed = self._del_sel()
        room = self.max_len - len(self.value)
        if room <= 0:
            return changed
        text = text[:room]
        self.value = self.value[:self.cursor] + text + self.value[self.cursor:]
        self.cursor += len(text)
        return True

    def _move(self, idx, shift):
        idx = _clamp(idx, 0, len(self.value))
        if shift:
            if self.anchor is None:
                self.anchor = self.cursor
        else:
            self.anchor = None
        self.cursor = idx

    def _word_left(self, i):
        v = self.value
        while i > 0 and v[i - 1] == " ":
            i -= 1
        while i > 0 and v[i - 1] != " ":
            i -= 1
        return i

    def _word_right(self, i):
        v, n = self.value, len(self.value)
        while i < n and v[i] == " ":
            i += 1
        while i < n and v[i] != " ":
            i += 1
        return i

    def key(self, event):
        mods = getattr(event, "mod", 0) | pygame.key.get_mods()
        ctrl = bool(mods & (pygame.KMOD_CTRL | pygame.KMOD_META))
        shift = bool(mods & pygame.KMOD_SHIFT)
        k = event.key
        self.blink = 0.0

        if k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            return "commit"
        if k == pygame.K_ESCAPE:
            return "cancel"
        if ctrl and k == pygame.K_a:
            self.anchor, self.cursor = 0, len(self.value)
            return None
        if ctrl and k in (pygame.K_c, pygame.K_x):
            if self.has_sel():
                s, e = self.sel_range()
                uk.clipboard_set_text(self.value[s:e])
                if k == pygame.K_x:
                    self._del_sel()
                    return "changed"
            return None
        if ctrl and k == pygame.K_v:
            return "changed" if self.insert(uk.clipboard_get_text()) else None

        if k == pygame.K_LEFT:
            if not shift and self.has_sel():
                self.cursor = self.sel_range()[0]
                self.anchor = None
            else:
                self._move(self._word_left(self.cursor) if ctrl else self.cursor - 1, shift)
        elif k == pygame.K_RIGHT:
            if not shift and self.has_sel():
                self.cursor = self.sel_range()[1]
                self.anchor = None
            else:
                self._move(self._word_right(self.cursor) if ctrl else self.cursor + 1, shift)
        elif k == pygame.K_HOME:
            self._move(0, shift)
        elif k == pygame.K_END:
            self._move(len(self.value), shift)
        elif k == pygame.K_BACKSPACE:
            if self._del_sel():
                return "changed"
            if self.cursor > 0:
                start = self._word_left(self.cursor) if ctrl else self.cursor - 1
                self.value = self.value[:start] + self.value[self.cursor:]
                self.cursor = start
                return "changed"
        elif k == pygame.K_DELETE:
            if self._del_sel():
                return "changed"
            if self.cursor < len(self.value):
                end = self._word_right(self.cursor) if ctrl else self.cursor + 1
                self.value = self.value[:self.cursor] + self.value[end:]
                return "changed"
        elif event.unicode and not ctrl:
            return "changed" if self.insert(event.unicode) else None
        return None


# ── Small vector icons (same primitives + look as the other creators) ───

def _ic_check(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    uk.draw_line_on(surface, color, (cx - s, cy), (cx - s * 0.15, cy + s * 0.8), width)
    uk.draw_line_on(surface, color, (cx - s * 0.15, cy + s * 0.8), (cx + s, cy - s * 0.7), width)


def _ic_plus(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.34
    uk.draw_line_on(surface, color, (cx - s, cy), (cx + s, cy), width)
    uk.draw_line_on(surface, color, (cx, cy - s), (cx, cy + s), width)


def _ic_minus(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.34
    uk.draw_line_on(surface, color, (cx - s, cy), (cx + s, cy), width)


def _make_chevron(direction):
    def draw(surface, rect, color, width=2):
        cx, cy = rect.center
        s = min(rect.w, rect.h) * 0.26
        if direction == "up":
            pts = [(cx - s, cy + s * 0.6), (cx, cy - s * 0.6), (cx + s, cy + s * 0.6)]
        elif direction == "down":
            pts = [(cx - s, cy - s * 0.6), (cx, cy + s * 0.6), (cx + s, cy - s * 0.6)]
        elif direction == "left":
            pts = [(cx + s * 0.6, cy - s), (cx - s * 0.6, cy), (cx + s * 0.6, cy + s)]
        else:
            pts = [(cx - s * 0.6, cy - s), (cx + s * 0.6, cy), (cx - s * 0.6, cy + s)]
        uk.draw_line_on(surface, color, pts[0], pts[1], width)
        uk.draw_line_on(surface, color, pts[1], pts[2], width)
    return draw


_ic_left = _make_chevron("left")
_ic_right = _make_chevron("right")


def _ic_refresh(surface, rect, color, width=3):
    """Open circular arrow: an arc that leaves a gap at the top-right, with an
    arrow head on the leading end."""
    cx, cy = rect.center
    r = min(rect.w, rect.h) * 0.30
    a0, a1 = math.radians(-35), math.radians(-35 + 290)
    steps = 16
    pts = []
    for i in range(steps + 1):
        a = a0 + (a1 - a0) * i / steps
        pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    for p, q in zip(pts, pts[1:]):
        uk.draw_line_on(surface, color, p, q, width)
    ex, ey = pts[-1]
    tang = a1 + math.pi / 2                       # travel direction at the end
    hl = r * 0.62
    for off in (math.radians(150), math.radians(-150)):
        uk.draw_line_on(surface, color, (ex, ey),
                        (ex + math.cos(tang + off) * hl, ey + math.sin(tang + off) * hl), width)


_POLY_CACHE: dict = {}


def _poly_icon(kind, size, color):
    """Filled play / pause glyph, drawn supersampled once per (kind, size,
    colour) and reused - same approach ui_kit takes for rounded shapes."""
    color = tuple(color)
    key = (kind, size, color)
    surf = _POLY_CACHE.get(key)
    if surf is not None:
        return surf
    ss = 6
    hi = pygame.Surface((size * ss, size * ss), pygame.SRCALPHA)
    c = (*color[:3], 255)
    if kind == "play":
        pts = [(size * 0.28, size * 0.16), (size * 0.28, size * 0.84), (size * 0.84, size * 0.5)]
        pygame.draw.polygon(hi, c, [(x * ss, y * ss) for x, y in pts])
    else:
        for x0 in (0.24, 0.56):
            pygame.draw.rect(hi, c, pygame.Rect(int(size * x0 * ss), int(size * 0.16 * ss),
                                                int(size * 0.20 * ss), int(size * 0.68 * ss)),
                             border_radius=int(size * 0.04 * ss))
    surf = pygame.transform.smoothscale(hi, (size, size))
    if len(_POLY_CACHE) > 200:
        _POLY_CACHE.clear()
    _POLY_CACHE[key] = surf
    return surf


def _make_poly_icon(kind):
    def draw(surface, rect, color, width=3):
        size = max(8, min(rect.w, rect.h))
        surf = _poly_icon(kind, size, color)
        uk.blit_surface(surface, surf, surf.get_rect(center=rect.center).topleft)
    return draw


_ic_play = _make_poly_icon("play")
_ic_pause = _make_poly_icon("pause")


# ══════════════════════════════════════════════════════════════════════
#  DecorationCreator overlay
# ══════════════════════════════════════════════════════════════════════

class DecorationCreator:
    """Non-blocking dev-menu overlay for decoration authoring."""

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width = int(screen_width)
        self.screen_height = int(screen_height)
        self.active = False
        self.active_tab = TAB_PREVIEW

        pygame.font.init()
        self._init_fonts()

        # ── model ──────────────────────────────────────────────────
        self.decorations: list[dict] = []
        self.selected_id: Optional[str] = None
        self.current_item: Optional[dict] = None
        self.style: dict = {}
        self.sheet: Optional[pygame.Surface] = None
        self.image_path: Optional[Path] = None
        self._sheet_serial = 0

        # Config values being edited.
        self.label_text = ""
        self.variant_names_text = ""
        self.frame_w = 1
        self.frame_h = 1
        self.frame_count = 1
        self.grid_rows = 1
        self.fps = 0.0
        self.sequence: list[int] = [1]
        self.variant_names: list[str] = ["Variant 1"]
        self.selected_variant = 0

        # Preview playback.
        self.anim_timer = 0.0
        self.preview_seq_index = 0
        self.preview_running = True

        # Scroll offset (index of first visible cell) for the playback
        # sequence strip in the Animation tab, when it holds more steps
        # than fit on screen at once.
        self.sequence_scroll = 0

        # Collision state - local to a sprite frame.
        self.collision_manual = False
        self.collision_rect: Optional[tuple[int, int, int, int]] = None
        self.collision_auto_rect: Optional[tuple[int, int, int, int]] = None
        self.collision_drag = None  # {mode, mouse, rect, scale, frame_rect}

        self.dirty = False

        # ── status ─────────────────────────────────────────────────
        self.status_msg = ""
        self.status_ok = True
        self.status_timer = 0.0

        # Optional host callback, e.g. to refresh the Object Editor catalogue.
        self.on_catalog_changed = None

        # ── frame / scaled-surface caches ──────────────────────────
        self._frames_key = None
        self._frames_cache: list[pygame.Surface] = []
        self._scaled_cache: dict = {}

        # ── per-frame UI plumbing (same shape as EntityCreator's) ───
        self._mouse = (screen_width // 2, screen_height // 2)
        self._hm = self._mouse
        self._dt = 1 / 60
        self._pulse = 0.0
        self._updated = False
        self._hits: list[dict] = []
        self._vp: Optional[pygame.Rect] = None
        self._drag: Optional[dict] = None
        self._focus = None
        self._repeat_on = False
        self._hv: dict = {}
        self._tscroll: dict = {}
        self._text_rects: list[pygame.Rect] = []
        self._text_rects_new: list[pygame.Rect] = []
        self._tip: Optional[str] = None
        self._scroll = 0.0
        self._content_h = 0
        self._content_avail = 0
        self._roster_scroll = 0.0
        self._close_requested = False
        self._seq_strip_rect: Optional[pygame.Rect] = None
        self._col_geom = None            # (frame_rect, scale) of the collision stage
        self._cursor_hint: Optional[str] = None

        self._layout()
        self._refresh_roster(keep_selection=False)

    # ── setup ──────────────────────────────────────────────────────
    @staticmethod
    def _font_root() -> str:
        anchored = BASE_DIR / "assets" / "ui" / "fonts"
        return str(anchored) if anchored.exists() else os.path.join("assets", "ui", "fonts")

    @staticmethod
    def _icon_path(name: str) -> str:
        path = os.path.join(str(BASE_DIR), "assets", "ui", "dev_menu", "icons", name)
        if not os.path.exists(path):
            path = os.path.join("assets", "ui", "dev_menu", "icons", name)
        return path

    def _init_fonts(self) -> None:
        """Same bitmap family as DevMenu / the other creators: title text uses
        the plain uppercase/lowercase glyph folders, everything else the
        menu glyph set."""
        root = self._font_root()
        menu = uk.BitmapFont(root, letter_spacing=1)
        title = uk.BitmapFont(root, letter_spacing=1)
        title.uppercase_dir = os.path.join(root, "uppercase")
        title.lowercase_dir = os.path.join(root, "lowercase")
        self.f_title = _Font(title, 30)
        self.f_lg = _Font(menu, 20)
        self.f_md = _Font(menu, 16)
        self.f_sm = _Font(menu, 12)

        self._back_icon = self._load_png_icon(self._icon_path("back.png"), 34)
        self._save_icon = self._load_png_icon(self._icon_path("save.png"), 26)

    @staticmethod
    def _load_png_icon(path: str, box: int) -> Optional[pygame.Surface]:
        """Same crop + integer-blow-up + point-sample path DevMenu / the
        other creators use for their header icons. Returns None when the
        file is missing (caller falls back to a vector icon)."""
        try:
            raw = pygame.image.load(path).convert_alpha()
        except (FileNotFoundError, pygame.error):
            return None
        rect = raw.get_bounding_rect(min_alpha=1)
        if rect.w <= 0 or rect.h <= 0:
            rect = raw.get_rect()
        raw = raw.subsurface(rect).copy()
        iw, ih = raw.get_size()
        scale = min(box / max(1, iw), box / max(1, ih))
        nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
        if scale >= 1.0:
            pre = max(1, math.ceil(scale) * 2)
            scaled = pygame.transform.scale(pygame.transform.scale(raw, (iw * pre, ih * pre)), (nw, nh))
        else:
            scaled = pygame.transform.scale(raw, (nw, nh))
        canvas = pygame.Surface((box, box), pygame.SRCALPHA)
        canvas.blit(scaled, ((box - nw) // 2, (box - nh) // 2))
        return canvas

    def _layout(self) -> None:
        w, h = self.screen_width, self.screen_height
        # Same proportions DevMenu / the other creators use for their bars.
        self.header_h = max(86, round(h * 0.12))
        self.footer_h = max(42, round(h * 0.065))
        m = 32
        top = self.header_h + 20
        bottom = h - self.footer_h - 20
        area_h = max(240, bottom - top)

        sm, md = self.f_sm, self.f_md
        self.m_field_h = max(40, md.line_h + 20)
        self.m_slider_h = sm.cap_h + 8 + 16
        self.m_btn_h = max(40, md.line_h + 16)
        self.row_h = max(56, sm.cap_h + md.cap_h + 28)
        self.row_gap = 6

        back = max(40, round(self.header_h * 0.55))
        self.back_rect = pygame.Rect(0, 0, back, back)
        self.back_rect.left = m
        self.back_rect.centery = self.header_h // 2
        self.save_rect = pygame.Rect(0, 0, back, back)
        self.save_rect.right = w - m
        self.save_rect.centery = self.header_h // 2
        self.refresh_rect = pygame.Rect(0, 0, back, back)
        self.refresh_rect.right = self.save_rect.left - 12
        self.refresh_rect.centery = self.header_h // 2

        side_w = _clamp(round(w * 0.225), 250, 330)
        prev_h = _clamp(round(area_h * 0.34), 170, 250)
        self.list_rect = pygame.Rect(m, top, side_w, max(160, area_h - prev_h - 16))
        self.prev_rect = pygame.Rect(m, self.list_rect.bottom + 16, side_w, prev_h)
        self.roster_view = pygame.Rect(self.list_rect.x + 8, self.list_rect.y + 46,
                                       side_w - 16, max(40, self.list_rect.h - 46 - 10))

        main_x = m + side_w + 24
        main_w = w - m - main_x
        self.tab_h = 46
        gap = 8
        n = len(TAB_NAMES)
        avail = main_w - gap * (n - 1)
        widths = [avail // n] * n
        widths[-1] += avail - sum(widths)
        self.tab_rects = []
        tx = main_x
        for wd in widths:
            self.tab_rects.append(pygame.Rect(tx, top, wd, self.tab_h))
            tx += wd + gap
        self.panel_rect = pygame.Rect(main_x, top + self.tab_h + 12, main_w, area_h - self.tab_h - 12)

    def resize(self, width: int, height: int) -> None:
        """Re-layout for a new draw-target size (e.g. native-resolution mode)."""
        width, height = int(width), int(height)
        if (width, height) != (self.screen_width, self.screen_height):
            self.screen_width, self.screen_height = width, height
            self._layout()

    # ── lifecycle ──────────────────────────────────────────────────
    def toggle(self) -> None:
        if self.active:
            self._shutdown()
        else:
            self.active = True
            self._mouse = tuple(pygame.mouse.get_pos())
            self._drag = None
            self._blur()
            self._refresh_roster(keep_selection=True)

    def _shutdown(self) -> None:
        self._blur()
        self._drag = None
        self.collision_drag = None
        self.active = False
        uk.set_text_cursor(False)
        self._set_key_repeat(False)

    def _close(self) -> str:
        self._shutdown()
        return "back_to_dev_menu"

    def _set_key_repeat(self, on: bool) -> None:
        if on and not self._repeat_on:
            pygame.key.set_repeat(400, 50)
            self._repeat_on = True
        elif not on and self._repeat_on:
            pygame.key.set_repeat(0, 0)
            self._repeat_on = False

    def _set_status(self, msg: str, ok: bool = True) -> None:
        self.status_msg = msg
        self.status_ok = ok
        self.status_timer = 3.0

    # ── roster ─────────────────────────────────────────────────────
    def _refresh_roster(self, keep_selection=True) -> None:
        previous = self.selected_id if keep_selection else None
        self.decorations = _root_items()

        for item in self.decorations:
            item["thumb"] = self._make_thumbnail(item)

        ids = [item["id"] for item in self.decorations]
        selected = previous if previous in ids else (ids[0] if ids else None)

        if selected:
            self._select(selected)
            self._ensure_selected_visible()
        else:
            self.selected_id = None
            self.current_item = None
            self.sheet = None
            self._frames_key = None
        self._roster_scroll = _clamp(self._roster_scroll, 0, self._roster_max_scroll())

    def _roster_pitch(self) -> int:
        return self.row_h + self.row_gap

    def _roster_max_scroll(self) -> float:
        return max(0, len(self.decorations) * self._roster_pitch() - self.roster_view.h)

    def _ensure_selected_visible(self) -> None:
        ids = [d["id"] for d in self.decorations]
        if self.selected_id not in ids:
            return
        pitch = self._roster_pitch()
        i = ids.index(self.selected_id)
        top, bottom = i * pitch, i * pitch + self.row_h
        if top < self._roster_scroll:
            self._roster_scroll = top
        elif bottom > self._roster_scroll + self.roster_view.h:
            self._roster_scroll = bottom - self.roster_view.h

    def _make_thumbnail(self, item: dict):
        path = item.get("image")
        if not path or not path.exists():
            return None
        try:
            sheet = pygame.image.load(str(path)).convert_alpha()
            style = item.get("style", {})
            fw = clamp_int(style.get("frame_w", sheet.get_width()), 1, sheet.get_width(), sheet.get_width())
            fh = clamp_int(style.get("frame_h", sheet.get_height()), 1, sheet.get_height(), sheet.get_height())
            frame = pygame.Surface((fw, fh), pygame.SRCALPHA)
            frame.blit(sheet, (0, 0), pygame.Rect(0, 0, fw, fh))
            return self._fit_thumb(frame, 40)
        except (pygame.error, OSError):
            return None

    @staticmethod
    def _fit_thumb(frame: pygame.Surface, box: int) -> pygame.Surface:
        """Fit a frame into a (box, box) transparent canvas, centred, using
        whole-number up-scaling so pixel art stays crisp."""
        fw, fh = frame.get_size()
        scale = _fit_scale(box, box, fw, fh, 8)
        nw, nh = max(1, round(fw * scale)), max(1, round(fh * scale))
        scaled = pygame.transform.scale(frame, (nw, nh))
        canvas = pygame.Surface((box, box), pygame.SRCALPHA)
        canvas.blit(scaled, ((box - nw) // 2, (box - nh) // 2))
        return canvas

    def _select(self, deco_id: str) -> None:
        item = next((x for x in self.decorations if x["id"] == deco_id), None)
        if not item:
            return
        changed = deco_id != self.selected_id
        if changed:
            self._blur()
            self.selected_variant = 0
            self.sequence_scroll = 0
            self._scroll = 0.0
        self.selected_id = deco_id
        self.current_item = item
        self.style = copy.deepcopy(item.get("style", {}))
        metadata = _safe_json(item.get("manifest"))
        # The manifest is authoritative for non-hardcoded decorations.
        if metadata and not item.get("hardcoded"):
            self.style.update(metadata)

        self.image_path = item.get("image")
        self.sheet = None
        if self.image_path and self.image_path.exists():
            try:
                self.sheet = pygame.image.load(str(self.image_path)).convert_alpha()
            except (pygame.error, OSError):
                self.sheet = None
        self._sheet_serial += 1

        if self.sheet is not None:
            sw, sh = self.sheet.get_size()
        else:
            sw, sh = 32, 32

        self.frame_w = clamp_int(self.style.get("frame_w", sw), 1, sw, sw)
        self.frame_h = clamp_int(self.style.get("frame_h", sh), 1, sh, sh)
        self.frame_count = clamp_int(self.style.get("frame_count", 1), 1,
                                     max(1, sw // self.frame_w), 1)
        self.grid_rows = clamp_int(self.style.get("grid_rows", 1), 1,
                                   max(1, sh // self.frame_h), 1)
        self.fps = clamp_float(self.style.get("fps", 0), 0, 30, 0)

        raw_seq = self.style.get("sequence", [1])
        if isinstance(raw_seq, list) and raw_seq:
            self.sequence = [clamp_int(v, 1, self.frame_count, 1) for v in raw_seq]
        else:
            self.sequence = [1]

        raw_variants = self.style.get("variants")
        if isinstance(raw_variants, list) and raw_variants:
            self.variant_names = [str(v) for v in raw_variants[:self.grid_rows]]
        else:
            base = self.style.get("label", pretty_id(deco_id))
            self.variant_names = [base] + [f"{base} (Variant {i + 1})"
                                           for i in range(1, self.grid_rows)]
        while len(self.variant_names) < self.grid_rows:
            self.variant_names.append(f"Variant {len(self.variant_names) + 1}")

        self.selected_variant = min(self.selected_variant, self.grid_rows - 1)
        self.label_text = str(self.style.get("label", pretty_id(self.selected_id or "Decoration")))
        self.variant_names_text = ", ".join(self.variant_names)

        saved_collision = rect_tuple(self.style.get("collision_rect"))
        self.collision_manual = saved_collision is not None
        self.collision_rect = saved_collision
        self.collision_drag = None
        self._refresh_collision_defaults()

        self.preview_seq_index = 0
        self.anim_timer = 0.0
        self.dirty = False

    def _on_pick(self, deco_id: str) -> None:
        if deco_id != self.selected_id:
            self._select(deco_id)

    def _refresh_clicked(self) -> None:
        self._refresh_roster(keep_selection=True)
        self._set_status("Decoration catalogue refreshed")

    def _set_tab(self, index: int) -> None:
        if index == self.active_tab:
            return
        self._blur()
        if index == TAB_ANIMATION:
            self._apply_layout()
        self.active_tab = index
        self._scroll = 0.0

    # ── config / animation helpers ─────────────────────────────────
    def _mark_dirty(self) -> None:
        # Built-ins keep their animation definition locked, but their collision
        # can still be edited and saved as a sidecar override.
        if self.current_item:
            self.dirty = True

    def _editor_enabled(self) -> bool:
        return bool(self.current_item and not self.current_item.get("hardcoded"))

    def _collision_enabled(self) -> bool:
        """Collision editing is available for every decoration, including Tree."""
        return self.current_item is not None

    def _apply_layout(self, **wanted) -> None:
        """Clamp (and optionally change) frame_w / frame_h / frame_count /
        grid_rows against the sheet, then fix up everything that depends on
        them. Same clamping order as the original numeric-field reader."""
        if not self.sheet:
            return
        sw, sh = self.sheet.get_size()

        old = (self.frame_w, self.frame_h, self.frame_count, self.grid_rows)
        self.frame_w = clamp_int(wanted.get("frame_w", self.frame_w), 1, sw, self.frame_w)
        self.frame_h = clamp_int(wanted.get("frame_h", self.frame_h), 1, sh, self.frame_h)
        self.frame_count = clamp_int(wanted.get("frame_count", self.frame_count), 1,
                                     max(1, sw // self.frame_w), self.frame_count)
        self.grid_rows = clamp_int(wanted.get("grid_rows", self.grid_rows), 1,
                                   max(1, sh // self.frame_h), self.grid_rows)

        if old != (self.frame_w, self.frame_h, self.frame_count, self.grid_rows):
            self.sequence = [clamp_int(n, 1, self.frame_count, 1) for n in self.sequence] or [1]
            self.preview_seq_index = min(self.preview_seq_index, len(self.sequence) - 1)
            self.selected_variant = min(self.selected_variant, self.grid_rows - 1)
            self._refresh_collision_defaults()
            self._mark_dirty()

    def _variant_labels(self) -> list[str]:
        """Variant names as they will be saved: the comma-separated text,
        trimmed / padded to one name per sheet row."""
        raw = [x.strip() for x in self.variant_names_text.split(",")]
        raw = [x for x in raw if x]
        if not raw:
            raw = [pretty_id(self.selected_id or "Decoration")]
        return (raw[:self.grid_rows] +
                [f"Variant {i + 1}" for i in range(len(raw), self.grid_rows)])[:self.grid_rows]

    def _frames(self) -> list[pygame.Surface]:
        """Frames of the current variant row (the runtime treats each row as
        a variant and each column as a frame). Cached until the sheet, row or
        grid changes."""
        key = (self._sheet_serial, self.selected_variant, self.frame_w, self.frame_h, self.frame_count)
        if key == self._frames_key:
            return self._frames_cache
        frames: list[pygame.Surface] = []
        if self.sheet is not None:
            sw, sh = self.sheet.get_size()
            row_y = self.selected_variant * self.frame_h
            for col in range(self.frame_count):
                x = col * self.frame_w
                if x + self.frame_w > sw or row_y + self.frame_h > sh:
                    break
                frames.append(self.sheet.subsurface(
                    pygame.Rect(x, row_y, self.frame_w, self.frame_h)
                ).copy())
        self._frames_key = key
        self._frames_cache = frames
        self._scaled_cache = {}
        return frames

    def _current_frame(self) -> Optional[pygame.Surface]:
        frames = self._frames()
        if not frames:
            return None
        if not self.sequence:
            return frames[0]
        seq_n = self.sequence[self.preview_seq_index % len(self.sequence)]
        return frames[max(0, min(len(frames) - 1, seq_n - 1))]

    def _scaled(self, surf: pygame.Surface, w: int, h: int) -> pygame.Surface:
        """Nearest-neighbour scaled copy, cached (sprites are re-drawn every
        frame; re-scaling them every frame would be wasted work)."""
        if surf.get_size() == (w, h):
            return surf
        key = (id(surf), w, h)
        out = self._scaled_cache.get(key)
        if out is None:
            if len(self._scaled_cache) > 96:
                self._scaled_cache.clear()
            out = pygame.transform.scale(surf, (w, h))
            self._scaled_cache[key] = out
        return out

    def _toggle_play(self) -> None:
        self.preview_running = not self.preview_running

    def _set_variant(self, index: int) -> None:
        index = _clamp(int(index), 0, max(0, self.grid_rows - 1))
        if index != self.selected_variant:
            self.selected_variant = index
            self._refresh_collision_defaults()

    # ── sequence editing ───────────────────────────────────────────
    def _append_sequence_frame(self, frame_number: int) -> None:
        if self.current_item and self.current_item.get("hardcoded"):
            return
        frame_number = max(1, min(self.frame_count, int(frame_number)))
        self.sequence.append(frame_number)
        self.preview_seq_index = max(0, len(self.sequence) - 1)
        self._mark_dirty()

    def _remove_sequence_step(self, index: int) -> None:
        if not self._editor_enabled() or not (0 <= index < len(self.sequence)):
            return
        self.sequence.pop(index)
        if not self.sequence:
            self.sequence = [1]
        self.preview_seq_index = min(self.preview_seq_index, len(self.sequence) - 1)
        self._mark_dirty()

    def _clear_sequence(self) -> None:
        if self.current_item and self.current_item.get("hardcoded"):
            return
        self.sequence = [1]
        self.preview_seq_index = 0
        self._mark_dirty()

    def _all_sequence(self) -> None:
        if self.current_item and self.current_item.get("hardcoded"):
            return
        self.sequence = list(range(1, self.frame_count + 1)) or [1]
        self.preview_seq_index = 0
        self._mark_dirty()

    def _ping_pong_sequence(self) -> None:
        if self.current_item and self.current_item.get("hardcoded"):
            return
        n = max(1, self.frame_count)
        if n == 1:
            self.sequence = [1]
        else:
            self.sequence = list(range(1, n + 1)) + list(range(n - 1, 1, -1))
        self.preview_seq_index = 0
        self._mark_dirty()

    def _reverse_sequence(self) -> None:
        if not self._editor_enabled():
            return
        self.sequence.reverse()
        self.preview_seq_index = 0
        self._mark_dirty()

    def _scroll_sequence(self, delta: int) -> None:
        max_scroll = max(0, len(self.sequence) - 1)
        self.sequence_scroll = max(0, min(max_scroll, self.sequence_scroll + delta))

    # ── collision editing ──────────────────────────────────────────
    def _refresh_collision_defaults(self) -> None:
        frames = self._frames()
        frame = frames[0] if frames else None
        self.collision_auto_rect = _collision_from_style(self.style, frame)
        if not self.collision_manual:
            self.collision_rect = None

    def _current_collision(self) -> Optional[tuple[int, int, int, int]]:
        if self.collision_manual and self.collision_rect:
            return self.collision_rect
        return self.collision_auto_rect

    def _set_manual_collision(self, rect: tuple[int, int, int, int]) -> None:
        frame = self._current_frame()
        if frame is not None:
            fw, fh = frame.get_size()
            x, y, w, h = rect
            w = max(1, min(w, fw))
            h = max(1, min(h, fh))
            x = max(0, min(x, fw - w))
            y = max(0, min(y, fh - h))
            rect = (x, y, w, h)
        self.collision_manual = True
        self.collision_rect = rect
        self._mark_dirty()

    def _use_auto_collision(self) -> None:
        if not self.current_item:
            return
        self.collision_manual = False
        self.collision_rect = None
        # Drop a previously loaded manual override from the in-memory style
        # so the canvas immediately shows the real fallback collision (for
        # Tree, that means its original hardcoded collision_size).
        self.style.pop("collision_rect", None)
        self._refresh_collision_defaults()
        self._mark_dirty()

    def _use_manual_collision(self) -> None:
        current = self._current_collision()
        if current:
            self._set_manual_collision(tuple(current))

    def _set_collision_component(self, index: int, value) -> None:
        """Stepper / typed-number edit of one of x, y, w, h."""
        cur = list(self._current_collision() or (0, 0, 8, 8))
        cur[index] = int(value)
        if cur[2] > 0 and cur[3] > 0:
            self._set_manual_collision(tuple(cur))

    def _rect_to_screen(self, local, frame_rect: pygame.Rect, scale: float) -> pygame.Rect:
        x, y, w, h = local
        return pygame.Rect(
            int(round(frame_rect.x + x * scale)),
            int(round(frame_rect.y + y * scale)),
            max(1, int(round(w * scale))),
            max(1, int(round(h * scale))),
        )

    def _collision_down(self, pos) -> None:
        if not self._collision_enabled() or self._col_geom is None:
            return
        frame_rect, scale = self._col_geom
        current = self._current_collision()
        if current is None:
            return
        screen_rect = self._rect_to_screen(current, frame_rect, scale)

        handle = 16
        corners = {
            "tl": screen_rect.topleft, "tr": screen_rect.topright,
            "bl": screen_rect.bottomleft, "br": screen_rect.bottomright,
        }
        mode = None
        for name, (cx, cy) in corners.items():
            if pygame.Rect(cx - handle // 2, cy - handle // 2, handle, handle).collidepoint(pos):
                mode = name
                break
        if mode is None and screen_rect.collidepoint(pos):
            mode = "move"
        if mode is not None:
            self.collision_drag = {
                "mode": mode,
                "mouse": tuple(pos),
                "rect": tuple(current),
                "scale": scale,
                "frame_rect": frame_rect,
            }
            self._set_manual_collision(tuple(current))

    def _collision_move(self, pos) -> None:
        d = self.collision_drag
        if not d or not self.collision_rect:
            return
        scale = d["scale"]
        frame = self._current_frame()
        fw, fh = frame.get_size() if frame else (1, 1)
        dx = int(round((pos[0] - d["mouse"][0]) / max(scale, 1e-6)))
        dy = int(round((pos[1] - d["mouse"][1]) / max(scale, 1e-6)))
        x, y, w, h = d["rect"]
        mode = d["mode"]

        if mode == "move":
            nx = max(0, min(fw - w, x + dx))
            ny = max(0, min(fh - h, y + dy))
            new = (nx, ny, w, h)
        else:
            left, top, right, bottom = x, y, x + w, y + h
            if "l" in mode:
                left = max(0, min(right - 1, x + dx))
            if "r" in mode:
                right = max(left + 1, min(fw, x + w + dx))
            if "t" in mode:
                top = max(0, min(bottom - 1, y + dy))
            if "b" in mode:
                bottom = max(top + 1, min(fh, y + h + dy))
            new = (left, top, right - left, bottom - top)

        self.collision_rect = tuple(map(int, new))
        self._mark_dirty()

    def _collision_up(self, pos) -> None:
        self.collision_drag = None

    # ── save ───────────────────────────────────────────────────────
    def save(self) -> bool:
        item = self.current_item
        if not item:
            self._set_status("No decoration selected", ok=False)
            return False

        # Hardcoded decorations keep their hand-authored spritesheet/animation
        # completely protected. Collision is intentionally editable through a
        # sidecar override so the visual collision workflow still works.
        if item.get("hardcoded"):
            folder = item.get("folder")
            if not folder or not folder.is_dir():
                self._set_status("Built-in decoration folder missing", ok=False)
                return False
            manifest = folder / "decoration.json"
            try:
                data = _safe_json(manifest) if manifest.exists() else {}
                if self.collision_manual and self.collision_rect:
                    data["collision_rect"] = list(map(int, self.collision_rect))
                    data.pop("collision_size", None)
                else:
                    # Removing the override restores the original hardcoded
                    # collision_size.
                    data.pop("collision_rect", None)
                    data.pop("collision_size", None)

                if data:
                    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
                elif manifest.exists():
                    manifest.unlink()
            except OSError as exc:
                self._set_status(f"Save failed: {exc}", ok=False)
                return False

            self.dirty = False
            try:
                reload_decoration_styles()
            except Exception as exc:
                self._set_status(f"Saved, reload failed: {exc}", ok=False)
                return False

            current = self.selected_id
            self._refresh_roster(keep_selection=True)
            if current and current == self.selected_id:
                self._set_status(f"Saved {item.get('label', current)} collision")
            if callable(self.on_catalog_changed):
                try:
                    self.on_catalog_changed()
                except Exception:
                    pass
            return True

        if self.image_path is None or not self.image_path.exists():
            self._set_status("No decoration image found", ok=False)
            return False

        self._apply_layout()
        self.variant_names = self._variant_labels()

        self.style.update({
            "label": self.label_text.strip() or pretty_id(self.selected_id or "Decoration"),
            "sheet_path": relative_asset_path(self.image_path),
            "frame_w": self.frame_w,
            "frame_h": self.frame_h,
            "grid_rows": self.grid_rows,
            "frame_count": self.frame_count,
            "sequence": [clamp_int(v, 1, self.frame_count, 1) for v in self.sequence] or [1],
            "fps": round(float(self.fps), 2),
            "variants": self.variant_names,
        })

        if self.collision_manual and self.collision_rect:
            self.style["collision_rect"] = list(map(int, self.collision_rect))
        else:
            self.style.pop("collision_rect", None)

        folder = item.get("folder")
        if not folder.is_dir():
            # This is a root-level PNG compatibility entry. The creator writes
            # a sibling <id>.json because no folder exists to host a manifest.
            manifest = DECORATIONS_DIR / f"{self.selected_id}.json"
        else:
            folder.mkdir(parents=True, exist_ok=True)
            manifest = folder / "decoration.json"

        # Do not persist editor-only discovery bookkeeping.
        save_data = {
            k: v for k, v in self.style.items()
            if k not in {"auto_discovered"}
        }

        try:
            manifest.write_text(json.dumps(save_data, indent=2), encoding="utf-8")
        except OSError as exc:
            self._set_status(f"Save failed: {exc}", ok=False)
            return False

        self.dirty = False
        self._set_status(f"Saved {manifest.name}")

        # Refresh the runtime catalogue while preserving the dictionary object
        # imported by ObjectEditor. The hardcoded tree is restored first, so it
        # can never be overwritten by the manifest scanner.
        try:
            reload_decoration_styles()
        except Exception as exc:
            self._set_status(f"Saved, reload failed: {exc}", ok=False)

        # Refresh our own live style from disk and optionally tell the host to
        # rebuild its Object Editor decoration palette immediately.
        current = self.selected_id
        self._refresh_roster(keep_selection=True)
        if current and current == self.selected_id and self.status_ok:
            self._set_status(f"Saved {manifest.name}")
        if callable(self.on_catalog_changed):
            try:
                self.on_catalog_changed()
            except Exception:
                pass
        return True

    # ── text focus ─────────────────────────────────────────────────
    def _focus_text(self, key, get, set_, max_len=600, allowed=None):
        edit = _TextEdit(get() or "", max_len=max_len, allowed=allowed)
        self._focus = SimpleNamespace(key=key, edit=edit, set=set_)
        self._set_key_repeat(True)
        return edit

    def _blur(self) -> None:
        self._focus = None
        self._set_key_repeat(False)

    # ── input ──────────────────────────────────────────────────────
    def handle_input(self, event: pygame.event.Event):
        """Returns 'back_to_dev_menu' when the overlay was just closed (via
        the header Back button or ESC), else None."""
        if not self.active:
            return None
        et = event.type
        if et in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and hasattr(event, "pos"):
            self._mouse = tuple(event.pos)

        if et == pygame.KEYDOWN:
            mods = getattr(event, "mod", 0) | pygame.key.get_mods()
            if (mods & (pygame.KMOD_CTRL | pygame.KMOD_META)) and event.key == pygame.K_s:
                self.save()
                return None
            if self._focus is not None:
                res = self._focus.edit.key(event)
                if res == "changed":
                    self._focus.set(self._focus.edit.value)
                elif res in ("commit", "cancel"):
                    self._blur()
                return None
            if event.key == pygame.K_ESCAPE:
                return self._close()
            if event.key == pygame.K_SPACE and self.active_tab == TAB_PREVIEW:
                self._toggle_play()
        elif et == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._mouse_down(event.pos)
            if self._close_requested:
                self._close_requested = False
                return self._close()
        elif et == pygame.MOUSEMOTION:
            if self._drag is not None and self._drag.get("drag"):
                self._drag["drag"](event.pos)
        elif et == pygame.MOUSEBUTTONUP and event.button == 1:
            d, self._drag = self._drag, None
            if d is not None and d.get("up"):
                d["up"](event.pos)
        elif et == pygame.MOUSEWHEEL:
            self._wheel(event.y)
        return None

    def _hit_at(self, pos):
        for hit in reversed(self._hits):
            if hit["rect"].collidepoint(pos):
                return hit
        return None

    def _mouse_down(self, pos) -> None:
        hit = self._hit_at(pos)
        if self._focus is not None and (hit is None or hit["key"] != self._focus.key):
            self._blur()
        if hit is None:
            return
        if hit["down"]:
            hit["down"](pos)
        if hit["drag"] or hit["up"]:
            self._drag = hit

    def _max_scroll(self) -> float:
        return max(0, self._content_h - self.panel_rect.h)

    def _wheel(self, dy: int) -> None:
        pos = self._mouse
        if self.roster_view.collidepoint(pos):
            self._roster_scroll = _clamp(self._roster_scroll - dy * self._roster_pitch(),
                                         0, self._roster_max_scroll())
            return
        strip = self._seq_strip_rect
        if (strip is not None and self.active_tab == TAB_ANIMATION
                and strip.collidepoint(pos) and self.panel_rect.collidepoint(pos)):
            self._scroll_sequence(-dy)
            return
        if self.panel_rect.collidepoint(pos):
            self._scroll = _clamp(self._scroll - dy * 64, 0, self._max_scroll())

    # ── update ─────────────────────────────────────────────────────
    def update(self, dt: float, mouse_pos=None) -> None:
        if not self.active:
            uk.set_text_cursor(False)
            uk.set_hand_cursor(False)
            return
        if mouse_pos is not None:
            self._mouse = tuple(mouse_pos)
        raw_dt = float(dt)
        dt = min(dt, 1 / 20)
        self._dt = max(dt, 1 / 240)
        self._updated = True
        self._pulse += dt
        if self.status_timer > 0:
            self.status_timer -= dt
        if self._focus is not None:
            self._focus.edit.blink += dt

        if self.preview_running and self.sequence and self.fps > 0:
            self.anim_timer += raw_dt
            step = 1.0 / max(0.01, self.fps)
            while self.anim_timer >= step:
                self.anim_timer -= step
                self.preview_seq_index = (self.preview_seq_index + 1) % len(self.sequence)

        self._update_cursor()

    def _update_cursor(self) -> None:
        """One OS cursor per frame: I-beam over text, move / resize over the
        collision box, hand over anything else clickable, arrow otherwise
        (ui_kit's last-call-wins helpers)."""
        if any(r.collidepoint(self._mouse) for r in self._text_rects):
            uk.set_text_cursor(True)
            uk.set_hand_cursor(False)
            return
        uk.set_text_cursor(False)
        d = self.collision_drag
        kind = ("move" if d["mode"] == "move" else "resize") if d else self._cursor_hint
        if kind == "move":
            uk.set_move_cursor(True)
            uk.set_hand_cursor(False)
            return
        if kind == "resize":
            uk.set_resize_cursor(True)
            uk.set_hand_cursor(False)
            return
        # Anything else registered as a hit this frame is a real button/tab/
        # row/scrollbar etc — except "c:canvas", the whole-stage hit behind
        # the collision box, which only responds under the handles/box
        # already covered above; hovering the rest of that stage doesn't
        # actually do anything, so it stays out of the hand-cursor check.
        hovering_widget = any(
            hit["rect"].collidepoint(self._mouse)
            for hit in self._hits
            if hit.get("key") != "c:canvas"
        )
        uk.set_hand_cursor(hovering_widget)

    # ══════════════════════════════════════════════════════════════
    #  Drawing: plumbing
    # ══════════════════════════════════════════════════════════════

    def draw(self, screen, dt: float = 0.0) -> None:
        """`screen` may be the engine's GPUScreen or a plain pygame.Surface.
        `dt` is only used if the host never calls update() this frame."""
        if not self.active:
            return
        if not self._updated and dt > 0:
            self.update(dt)
        self._updated = False

        self._hits = []
        self._text_rects_new = []
        self._tip = None
        self._vp = None
        self._seq_strip_rect = None
        self._col_geom = None
        self._cursor_hint = None
        self._hm = self._mouse

        w, h = self.screen_width, self.screen_height
        uk.draw_rect_on(screen, _BG, pygame.Rect(0, 0, w, h), 0, 0)
        uk.draw_rect_on(screen, _BAND, pygame.Rect(0, self.header_h, w, h - self.header_h - self.footer_h), 0, 0)

        self._draw_sidebar(screen)
        self._draw_tabs(screen)
        self._draw_content(screen)
        self._draw_header(screen)
        self._draw_footer(screen)

        self._text_rects = self._text_rects_new

    # -- hover / hit plumbing -----------------------------------------
    def _anim(self, key, on: bool) -> float:
        v = self._hv.get(key, 0.0)
        target = 1.0 if on else 0.0
        v += (target - v) * min(1.0, self._dt * 14.0)
        if abs(target - v) < 0.01:
            v = target
        self._hv[key] = v
        return round(v * 20) / 20

    def _hov(self, rect) -> bool:
        if not pygame.Rect(rect).collidepoint(self._hm):
            return False
        return self._vp is None or self._vp.collidepoint(self._hm)

    def _add_hit(self, rect, key=None, down=None, drag=None, up=None, tip=None):
        r = pygame.Rect(rect)
        if self._vp is not None:
            r = r.clip(self._vp)
        if r.w <= 0 or r.h <= 0:
            return None
        hit = {"rect": r, "key": key, "down": down, "drag": drag, "up": up, "tip": tip}
        self._hits.append(hit)
        if tip and r.collidepoint(self._hm):
            self._tip = tip
        return hit

    # -- clipping -----------------------------------------------------
    @staticmethod
    def _push_clip(screen, rect):
        old = screen.get_clip()
        r = pygame.Rect(rect)
        if old is not None:
            try:
                r = r.clip(pygame.Rect(old))
            except Exception:
                pass
        screen.set_clip(r)
        return old

    @staticmethod
    def _pop_clip(screen, old) -> None:
        screen.set_clip(old)

    def _blit_clip(self, screen, surf, pos, transient=True) -> None:
        """Blit a sprite, cropping the source to the scroll viewport
        ourselves - transient blits don't reliably honour the clip rect (see
        _put), and a sprite stage that straddles the viewport edge would
        otherwise bleed onto the tab row."""
        x, y = int(pos[0]), int(pos[1])
        tw, th = surf.get_size()
        if self._vp is not None:
            row = pygame.Rect(x, y, tw, th)
            vis = row.clip(self._vp)
            if vis.w <= 0 or vis.h <= 0:
                return
            if vis != row:
                area = pygame.Rect(vis.x - x, vis.y - y, vis.w, vis.h)
                uk.blit_surface(screen, surf, vis.topleft, area=area, transient=transient)
                return
        uk.blit_surface(screen, surf, (x, y), transient=transient)

    def _put(self, screen, font, text, color, x, base_y, anchor="l", dyn=False) -> int:
        if not text:
            return 0
        surf, desc = font.render(text, color)
        tw, th = surf.get_size()
        if anchor == "c":
            x -= tw // 2
        elif anchor == "r":
            x -= tw
        x = int(x)
        y = int(base_y - (th - desc))
        # Text draws through the "transient" (uncached) blit path, which on
        # some render backends doesn't honour the active clip rect the way
        # panel/slider backgrounds do - crop the source surface ourselves to
        # whatever part of the row actually falls inside the viewport.
        if self._vp is not None:
            row = pygame.Rect(x, y, tw, th)
            visible = row.clip(self._vp)
            if visible.w <= 0 or visible.h <= 0:
                return tw
            if visible != row:
                area = pygame.Rect(visible.x - x, visible.y - y, visible.w, visible.h)
                uk.blit_surface(screen, surf, visible.topleft, area=area, transient=dyn)
                return tw
        uk.blit_surface(screen, surf, (x, y), transient=dyn)
        return tw

    def _text_top(self, screen, font, text, color, x, y, anchor="l", dyn=False) -> int:
        return self._put(screen, font, text, color, x, y + font.cap_h, anchor, dyn)

    def _text_mid(self, screen, font, text, color, x, cy, anchor="l", dyn=False, max_w=None) -> int:
        if max_w is not None:
            text = font.fit(text, max_w)
        return self._put(screen, font, text, color, x, cy + (font.cap_h + 1) // 2, anchor, dyn)

    # -- primitives ---------------------------------------------------
    @staticmethod
    def _panel(screen, rect, bg, border, bw=1, radius=10) -> None:
        if len(bg) == 3:
            bg = (*bg, 255)
        uk.draw_panel(screen, rect, bg=bg, border=border, border_width=bw, radius=radius, shadow=False)

    def _stage(self, screen, rect) -> None:
        """Inset canvas well used for every sprite / sheet view."""
        uk.draw_rect_on(screen, _INSET, rect, 0, 10)
        uk.draw_rect_on(screen, _T.CARD_BORDER, rect, 1, 10)

    def _icon_btn(self, screen, key, rect, icon, on_click, danger=False, enabled=True, tip=None) -> None:
        accent = _T.DANGER_BRIGHT if danger else _T.GOLD
        hov = enabled and self._hov(rect)
        t = self._anim(key, hov)
        if enabled:
            self._panel(screen, rect, uk.lerp_color(_CARD, _CARD_HI, t),
                        uk.lerp_color(_T.CARD_BORDER, accent, 0.78 * t))
            fg = uk.lerp_color(_T.TEXT_SECONDARY, accent, t)
        else:
            self._panel(screen, rect, _CARD, _T.CARD_BORDER)
            fg = _T.TEXT_DIM
        icon(screen, pygame.Rect(0, 0, 28, 28).move(rect.centerx - 14, rect.centery - 14), fg, 3)
        if enabled and on_click:
            self._add_hit(rect, key=key, down=lambda p, cb=on_click: cb(), tip=tip)

    def _icon_save_png(self, screen, rect, color, width=3) -> None:
        if self._save_icon is not None:
            uk.blit_surface(screen, self._save_icon, self._save_icon.get_rect(center=rect.center))
        else:
            _ic_check(screen, rect, color, width)

    def _pill(self, screen, key, rect, label, accent, icon=None, danger=False, enabled=True,
              on_click=None, tip=None) -> None:
        if danger:
            accent = _T.DANGER_BRIGHT
        hov = enabled and self._hov(rect)
        t = self._anim(key, hov)
        if enabled:
            self._panel(screen, rect, uk.lerp_color(_CARD, _CARD_HI, t),
                        uk.lerp_color(_T.CARD_BORDER, accent, 0.5 + 0.5 * t))
            fg = uk.lerp_color(_T.TEXT_SECONDARY, accent, 0.55 + 0.45 * t)
            if t > 0:
                uk.draw_soft_glow(screen, rect.center, int(rect.w * 0.55), accent, max_alpha=int(26 * t))
        else:
            self._panel(screen, rect, _CARD, _T.CARD_BORDER)
            fg = _T.TEXT_DIM
        font = self.f_md
        ic = 20 if icon else 0
        gap = 10 if icon and label else 0
        if icon and label and font.width(label) + 24 + ic + gap > rect.w:
            icon, ic, gap = None, 0, 0    # too narrow for icon + label: the label matters more
        text = font.fit(label, rect.w - 24 - ic - gap) if label else ""
        tw = font.width(text)
        x = rect.centerx - (ic + gap + tw) // 2
        if icon:
            icon(screen, pygame.Rect(x, rect.centery - ic // 2, ic, ic), fg, 3)
        if text:
            self._text_mid(screen, font, text, fg, x + ic + gap, rect.centery)
        if enabled and on_click:
            self._add_hit(rect, key=key, down=lambda p, cb=on_click: cb(), tip=tip)

    def _field_label(self, screen, text, x, y) -> int:
        self._text_top(screen, self.f_sm, text.upper(), _T.TEXT_MUTED, x, y)
        return self.f_sm.cap_h + 8

    def _caption(self, screen, text, x, y, w, right=None) -> int:
        sm = self.f_sm
        label = text.upper()
        self._text_top(screen, sm, label, _T.TEXT_MUTED, x, y)
        x0 = x + sm.width(label) + 14
        x1 = x + w
        if right:
            rw = self._text_top(screen, sm, right, _T.TEXT_DIM, x + w, y, "r", dyn=True)
            x1 -= rw + 14
        if x1 > x0:
            uk.draw_line_on(screen, _HAIR, (x0, y + sm.cap_h // 2), (x1, y + sm.cap_h // 2), 1)
        return sm.cap_h + 18

    def _note(self, screen, x, y, w, text, color=None, font=None) -> int:
        font = font or self.f_sm
        color = color or _T.TEXT_DIM
        lh = font.line_h + 4
        lines = font.wrap(text, w)
        for i, line in enumerate(lines):
            self._text_top(screen, font, line, color, x, y + i * lh)
        return len(lines) * lh

    def _stat(self, screen, x, cy, label, value) -> int:
        """'LABEL  value' readout on one baseline; returns the width used."""
        sm = self.f_sm
        a = self._text_mid(screen, sm, label, _T.TEXT_MUTED, x, cy)
        b = self._text_mid(screen, sm, value, _T.TEXT_SECONDARY, x + a + 10, cy, dyn=True)
        return a + 10 + b

    # -- sliders / steppers / segmented -------------------------------
    def _slider(self, screen, key, x, y, w, label, value, vmin, vmax, step, fmt, setter, enabled=True) -> int:
        sm = self.f_sm
        self._text_top(screen, sm, label.upper(), _T.TEXT_MUTED, x, y)
        self._text_top(screen, sm, fmt.format(value), _T.TEXT_SECONDARY if enabled else _T.TEXT_DIM,
                       x + w, y, "r", dyn=True)
        cy = y + sm.cap_h + 16
        track = pygame.Rect(x, cy - 3, w, 6)
        dragging = enabled and self._drag is not None and self._drag.get("key") == key
        hover_zone = pygame.Rect(x - 8, cy - 12, w + 16, 24)
        t = self._anim(key, enabled and (self._hov(hover_zone) or dragging))
        frac = _clamp((value - vmin) / (vmax - vmin), 0.0, 1.0) if vmax > vmin else 0.0
        uk.draw_rect_on(screen, _TRACK, track, 0, 3)
        fill = pygame.Rect(x, cy - 3, max(0, int(w * frac)), 6)
        accent = _T.GOLD if enabled else _T.CHIP_BORDER
        if fill.w > 0:
            uk.draw_rect_on(screen, uk.lerp_color(accent, _T.GOLD_BRIGHT, t) if enabled else accent, fill, 0, 3)
        tx = x + int(w * frac)
        if t > 0:
            uk.draw_soft_glow(screen, (tx, cy), 20, _T.GOLD, max_alpha=int(38 * t))
        uk.draw_circle_on(screen, uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t) if enabled else _T.TEXT_DIM,
                          (tx, cy), 8)
        uk.draw_circle_on(screen, uk.lerp_color(_T.CARD_BORDER, _T.GOLD, t) if enabled else _T.CARD_BORDER,
                          (tx, cy), 8, 2)

        if enabled:
            def apply(pos, x=x, w=w):
                f = _clamp((pos[0] - x) / max(1, w), 0.0, 1.0)
                raw = vmin + f * (vmax - vmin)
                if step:
                    raw = round(raw / step) * step
                setter(_clamp(raw, vmin, vmax))

            self._add_hit(hover_zone, key=key, down=apply, drag=apply)
        return self.m_slider_h

    def _stepper(self, screen, key, x, y, w, label, get, set_, vmin, vmax, enabled=True) -> int:
        """[ - ] [ typed number ] [ + ]   (Shift = step by 10)."""
        y0 = y
        y += self._field_label(screen, label, x, y)
        fh = self.m_field_h
        minus = pygame.Rect(x, y, fh, fh)
        plus = pygame.Rect(x + w - fh, y, fh, fh)
        field = pygame.Rect(minus.right + 6, y, max(20, w - 2 * fh - 12), fh)

        def bump(sign):
            step = 10 if (pygame.key.get_mods() & pygame.KMOD_SHIFT) else 1
            set_(_clamp(get() + sign * step, vmin, vmax))

        v = get()
        self._icon_btn(screen, (key, "-"), minus, _ic_minus, lambda: bump(-1),
                       enabled=enabled and v > vmin, tip=f"Decrease {label.lower()}  (Shift = 10)")
        self._icon_btn(screen, (key, "+"), plus, _ic_plus, lambda: bump(1),
                       enabled=enabled and v < vmax, tip=f"Increase {label.lower()}  (Shift = 10)")

        def typed(text):
            # Typed values are only capped at the top; each setter applies its
            # own lower-bound rule exactly as the original numeric fields did
            # (layout fields clamp up to 1, a collision W/H of 0 is ignored).
            s = text.strip()
            if s.isdigit():
                set_(min(int(s), vmax))

        self._text_field(screen, key, field, lambda: str(get()), typed, "0", max_len=5,
                         allowed=_digit_ok, enabled=enabled)
        return fh + (y - y0)

    def _stepper_grid(self, screen, x, y, w, specs, enabled=True) -> int:
        gap = 16
        cols = 2 if w >= 300 else 1
        cw = (w - gap * (cols - 1)) // cols
        pitch = self.f_sm.cap_h + 8 + self.m_field_h + 8
        for i, sp in enumerate(specs):
            r, c = divmod(i, cols)
            self._stepper(screen, sp["key"], x + c * (cw + gap), y + r * pitch, cw, sp["label"],
                          sp["get"], sp["set"], sp["vmin"], sp["vmax"], enabled)
        return ((len(specs) + cols - 1) // cols) * pitch

    def _segmented(self, screen, key, x, y, w, options, current, on_select, enabled=True) -> int:
        fh = self.m_field_h
        rect = pygame.Rect(x, y, w, fh)
        self._panel(screen, rect, _FIELD, _T.CARD_BORDER)
        seg_w = w // len(options)
        for i, (val, label) in enumerate(options):
            r = pygame.Rect(x + i * seg_w, y, seg_w if i < len(options) - 1 else w - seg_w * i, fh)
            inner = r.inflate(-8, -8)
            active = val == current
            t = self._anim((key, val), enabled and self._hov(r) and not active)
            if active:
                self._panel(screen, inner, (48, 39, 19) if enabled else _CARD_HI,
                            _T.GOLD if enabled else _T.CHIP_BORDER, 1, 8)
            elif t > 0:
                uk.draw_rect_on(screen, uk.lerp_color(_FIELD, _CARD_HI, t), inner, 0, 8)
            if not enabled:
                fg = _T.TEXT_DIM
            else:
                fg = _T.GOLD_BRIGHT if active else uk.lerp_color(_T.TEXT_MUTED, _T.TEXT_PRIMARY, t)
            self._text_mid(screen, self.f_md, label, fg, r.centerx, r.centery, "c", max_w=r.w - 12)
            if enabled:
                self._add_hit(r, key=(key, val), down=lambda p, v=val: on_select(v))
        return fh

    # -- text fields --------------------------------------------------
    def _text_field(self, screen, key, rect, get, set_, placeholder="", max_len=600, allowed=None,
                    enabled=True, tip=None) -> None:
        md = self.f_md
        pad = 14
        if not enabled:
            self._panel(screen, rect, _INSET, _T.CARD_BORDER)
            self._text_mid(screen, md, get() or "", _T.TEXT_DIM, rect.x + pad, rect.centery,
                           dyn=True, max_w=rect.w - 2 * pad)
            return

        focus = self._focus if (self._focus is not None and self._focus.key == key) else None
        edit = focus.edit if focus else None
        hov = self._hov(rect)
        t = self._anim(key, hov and not focus)
        bg = uk.lerp_color(_FIELD, _FIELD_HI, t if not focus else 1.0)
        if focus:
            self._panel(screen, rect, bg, _T.GOLD, 2)
        else:
            self._panel(screen, rect, bg, uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.6 * t))
        value = edit.value if edit else (get() or "")
        blink_on = edit is not None and int(edit.blink * 2) % 2 == 0
        fg = _T.TEXT_PRIMARY if (focus or hov) else _T.TEXT_SECONDARY
        self._text_rects_new.append(rect.clip(self._vp) if self._vp is not None else pygame.Rect(rect))

        inner = pygame.Rect(rect.x + pad, rect.y + 2, rect.w - 2 * pad, rect.h - 4)
        cy = rect.centery
        scroll = self._tscroll.get(key, 0)
        if edit:
            cw = md.width(value[:edit.cursor])
            if md.width(value) <= inner.w - 2:
                scroll = 0
            else:
                if cw - scroll > inner.w - 2:
                    scroll = cw - inner.w + 2
                if cw < scroll:
                    scroll = cw
                scroll = max(0, scroll)
            self._tscroll[key] = scroll
        old = self._push_clip(screen, inner)
        if not value and not focus:
            self._text_mid(screen, md, placeholder, _T.TEXT_DIM, inner.x, cy)
        elif focus:
            if edit.has_sel():
                s, e = edit.sel_range()
                sx = inner.x - scroll + md.width(value[:s])
                ex = inner.x - scroll + md.width(value[:e])
                uk.draw_rect_on(screen, (*_T.GOLD, 70),
                                pygame.Rect(sx, cy - md.cap_h // 2 - 4, ex - sx, md.line_h + 8), 0, 3)
            self._text_mid(screen, md, value, fg, inner.x - scroll, cy, dyn=True)
            if blink_on:
                cx = inner.x - scroll + md.width(value[:edit.cursor])
                uk.draw_rect_on(screen, _T.GOLD_BRIGHT,
                                pygame.Rect(cx, cy - md.cap_h // 2 - 4, 2, md.line_h + 8), 0, 0)
        else:
            self._text_mid(screen, md, md.fit(value, inner.w), fg, inner.x, cy, dyn=True)
        self._pop_clip(screen, old)

        def idx_at(pos, rect=rect, key=key, value=value):
            sc = self._tscroll.get(key, 0)
            cur = self._focus.edit.value if self._focus and self._focus.key == key else value
            return _index_at_x(md, cur, pos[0] - (rect.x + pad) + sc)

        def click(pos):
            shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
            if self._focus is None or self._focus.key != key:
                self._focus_text(key, get, set_, max_len, allowed)
                shift = False
            ed = self._focus.edit
            idx = idx_at(pos)
            if shift and ed.anchor is None:
                ed.anchor = ed.cursor
            elif not shift:
                ed.anchor = idx
            ed.cursor = idx
            ed.blink = 0.0

        def drag(pos):
            if self._focus is not None and self._focus.key == key:
                self._focus.edit.cursor = idx_at(pos)
                self._focus.edit.blink = 0.0

        self._add_hit(rect, key=key, down=click, drag=drag, tip=tip)

    # ══════════════════════════════════════════════════════════════
    #  Drawing: header / footer
    # ══════════════════════════════════════════════════════════════

    def _draw_header(self, screen) -> None:
        w, hh = self.screen_width, self.header_h
        uk.draw_rect_on(screen, _BAR, pygame.Rect(0, 0, w, hh), 0, 0)
        uk.draw_line_on(screen, _HAIR, (0, hh - 1), (w, hh - 1), 1)

        r = self.back_rect
        t = self._anim("back", self._hov(r))
        self._panel(screen, r, uk.lerp_color(_CARD, _CARD_HI, t), uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t))
        if t > 0:
            uk.draw_soft_glow(screen, r.center, int(r.w * 0.8), _T.GOLD, max_alpha=int(28 * t))
        if self._back_icon is not None:
            uk.blit_surface(screen, self._back_icon, self._back_icon.get_rect(center=r.center))
        else:
            _ic_left(screen, r, uk.lerp_color(_T.TEXT_SECONDARY, _T.GOLD, t), 3)
        self._add_hit(r, key="back", down=lambda p: setattr(self, "_close_requested", True),
                      tip="Back to the Dev Menu")

        self._text_mid(screen, self.f_title, "DECORATION CREATOR", _T.TEXT_PRIMARY, w // 2, hh // 2, "c")

        has = self.current_item is not None
        self._icon_btn(screen, "refresh", self.refresh_rect, _ic_refresh, self._refresh_clicked,
                       tip="Rescan assets/objects/decorations/")
        self._icon_btn(screen, "save", self.save_rect, self._icon_save_png, self.save,
                       enabled=has, tip="Save this decoration  (Ctrl+S)")

        if self.dirty and has:
            label = "UNSAVED"
            tw = self.f_sm.width(label)
            chip = pygame.Rect(0, 0, tw + 42, 32)
            chip.right = self.refresh_rect.left - 14
            chip.centery = self.save_rect.centery
            self._panel(screen, chip, (36, 30, 16), (110, 88, 40), 1, 16)
            pulse = 0.5 + 0.5 * math.sin(self._pulse * 4.0)
            dot = (chip.x + 17, chip.centery)
            uk.draw_soft_glow(screen, dot, 12, _T.GOLD, max_alpha=int(30 + 50 * pulse))
            uk.draw_circle_on(screen, _T.GOLD, dot, 4)
            self._text_mid(screen, self.f_sm, label, _T.GOLD_BRIGHT, chip.x + 30, chip.centery)

    def _draw_footer(self, screen) -> None:
        w, h = self.screen_width, self.screen_height
        fy = h - self.footer_h
        uk.draw_rect_on(screen, _BAR, pygame.Rect(0, fy, w, self.footer_h), 0, 0)
        uk.draw_line_on(screen, _HAIR, (0, fy), (w, fy), 1)
        cy = fy + self.footer_h // 2
        x = 32
        if self.status_timer > 0 and self.status_msg:
            col = _T.KI_BLUE if self.status_ok else _T.DANGER_BRIGHT
            uk.draw_circle_on(screen, col, (x + 4, cy), 4)
            self._text_mid(screen, self.f_sm, self.status_msg, col, x + 18, cy, dyn=True, max_w=w // 2)
        elif self._tip:
            self._text_mid(screen, self.f_sm, self._tip, _T.TEXT_MUTED, x, cy, dyn=True, max_w=w // 2)

    # ══════════════════════════════════════════════════════════════
    #  Drawing: sidebar (roster + live preview)
    # ══════════════════════════════════════════════════════════════

    def _draw_sidebar(self, screen) -> None:
        lr = self.list_rect
        self._panel(screen, lr, _T.PANEL_BG, _T.PANEL_BORDER, 1, 12)
        self._text_top(screen, self.f_sm, "DECORATIONS", _T.TEXT_MUTED, lr.x + 18, lr.y + 18)
        self._text_top(screen, self.f_sm, str(len(self.decorations)), _T.TEXT_DIM, lr.right - 18, lr.y + 18,
                       "r", dyn=True)

        rv = self.roster_view
        pitch = self._roster_pitch()
        total = len(self.decorations) * pitch
        self._roster_scroll = _clamp(self._roster_scroll, 0, max(0, total - rv.h))
        old_vp = self._vp
        self._vp = rv
        old = self._push_clip(screen, rv)
        if not self.decorations:
            self._note(screen, rv.x + 8, rv.y + 14, rv.w - 16,
                       "No decorations found in assets/objects/decorations/")
        for i, item in enumerate(self.decorations):
            ry = rv.y + i * pitch - int(self._roster_scroll)
            row = pygame.Rect(rv.x, ry, rv.w - (8 if total > rv.h else 0), self.row_h)
            if row.bottom < rv.y or row.y > rv.bottom:
                continue
            deco_id = item["id"]
            sel = deco_id == self.selected_id
            t = self._anim(("deco", deco_id), self._hov(row) and not sel)
            base = _SEL if sel else uk.lerp_color(_CARD, _CARD_HI, t)
            border = _T.GOLD if sel else uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t)
            self._panel(screen, row, base, border, 1, 9)

            box = pygame.Rect(row.x + 8, row.centery - 20, 40, 40)
            uk.draw_rect_on(screen, _INSET, box, 0, 8)
            thumb = item.get("thumb")
            if thumb is not None:
                self._blit_clip(screen, thumb, box.topleft)
            else:
                self._text_mid(screen, self.f_sm, "?", _T.TEXT_DIM, box.centerx, box.centery, "c")
            uk.draw_rect_on(screen, uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.5) if sel else _T.CARD_BORDER,
                            box, 1, 8)

            md, sm = self.f_md, self.f_sm
            tx = box.right + 12
            max_w = row.right - tx - 10
            block = md.cap_h + 6 + sm.cap_h
            ty = row.centery - block // 2
            fg = _T.TEXT_PRIMARY if sel else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
            label = str(item.get("label", deco_id))
            self._text_top(screen, md, md.fit(label, max_w), fg, tx, ty)
            if item.get("hardcoded"):
                self._text_top(screen, sm, "BUILT-IN", _T.GOLD, tx, ty + md.cap_h + 6)
            else:
                self._text_top(screen, sm, sm.fit(deco_id, max_w), _T.TEXT_DIM, tx, ty + md.cap_h + 6)
            self._add_hit(row, key=("deco", deco_id), down=lambda p, d=deco_id: self._on_pick(d))
        self._pop_clip(screen, old)
        self._vp = old_vp
        if total > rv.h:
            frac = self._roster_scroll / max(1, total - rv.h)
            th = max(24, int(rv.h * rv.h / total))
            ty = rv.y + int((rv.h - th) * frac)
            uk.draw_rect_on(screen, _T.CHIP_BORDER, pygame.Rect(rv.right - 4, ty, 3, th), 0, 1)

        self._draw_live_preview(screen)

    def _draw_live_preview(self, screen) -> None:
        pr = self.prev_rect
        self._panel(screen, pr, _T.PANEL_BG, _T.PANEL_BORDER, 1, 12)
        self._text_top(screen, self.f_sm, "LIVE PREVIEW", _T.TEXT_MUTED, pr.x + 18, pr.y + 18)
        if self.current_item is not None:
            state = f"{self.fps:g} FPS" if self.preview_running else "PAUSED"
            self._text_top(screen, self.f_sm, state, _T.TEXT_DIM, pr.right - 18, pr.y + 18, "r", dyn=True)
        stage = pygame.Rect(pr.x + 12, pr.y + 44, pr.w - 24, pr.h - 44 - 12)
        self._stage(screen, stage)

        frame = self._current_frame()
        if frame is None:
            msg = "NO DECORATION SELECTED" if self.current_item is None else "NO IMAGE"
            self._text_mid(screen, self.f_sm, msg, _T.TEXT_DIM, stage.centerx, stage.centery, "c")
            return
        old = self._push_clip(screen, stage)
        fw, fh = frame.get_size()
        base_y = stage.bottom - 22
        scale = _fit_scale(stage.w - 28, base_y - stage.y - 14, fw, fh, 6)
        dw, dh = max(1, int(round(fw * scale))), max(1, int(round(fh * scale)))
        dest = pygame.Rect(0, 0, dw, dh)
        dest.midbottom = (stage.centerx, base_y)
        uk.draw_line_on(screen, _GROUND, (stage.x + 14, base_y), (stage.right - 14, base_y), 1)
        self._blit_clip(screen, self._scaled(frame, dw, dh), dest.topleft)
        uk.draw_line_on(screen, _T.GOLD, (dest.centerx - 7, base_y), (dest.centerx + 7, base_y), 2)
        self._pop_clip(screen, old)

    # ══════════════════════════════════════════════════════════════
    #  Drawing: tabs + scrolling content panel
    # ══════════════════════════════════════════════════════════════

    def _draw_tabs(self, screen) -> None:
        for i, name in enumerate(TAB_NAMES):
            r = self.tab_rects[i]
            active = i == self.active_tab
            t = self._anim(("tab", i), self._hov(r) and not active)
            base = _SEL if active else uk.lerp_color(_CARD, _CARD_HI, t)
            border = _T.GOLD if active else uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t)
            self._panel(screen, r, base, border, 1, 10)
            if active:
                uk.draw_rect_on(screen, _T.GOLD, pygame.Rect(r.x + 14, r.bottom - 4, r.w - 28, 3), 0, 1)
            fg = _T.GOLD_BRIGHT if active else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
            label = self.f_md.fit(name, r.w - 28)
            x = r.centerx - self.f_md.width(label) // 2
            self._text_mid(screen, self.f_md, label, fg, x, r.centery - 1)
            self._add_hit(r, key=("tab", i), down=lambda p, k=i: self._set_tab(k))

    def _draw_content(self, screen) -> None:
        pr = self.panel_rect
        self._panel(screen, pr, _T.PANEL_BG, _T.PANEL_BORDER, 1, 12)
        if self.current_item is None:
            self._text_mid(screen, self.f_lg, "No decoration selected", _T.TEXT_MUTED, pr.centerx, pr.centery - 14, "c")
            self._text_mid(screen, self.f_md, "Add art under assets/objects/decorations/, then press Refresh.",
                           _T.TEXT_DIM, pr.centerx, pr.centery + 22, "c", max_w=pr.w - 40)
            return
        pad = 24
        vp = pygame.Rect(pr.x + 3, pr.y + 3, pr.w - 6, pr.h - 6)
        self._content_avail = pr.h - pad * 2
        self._scroll = _clamp(self._scroll, 0, self._max_scroll())
        fn = {TAB_PREVIEW: self._tab_preview, TAB_ANIMATION: self._tab_animation,
              TAB_COLLISION: self._tab_collision}[self.active_tab]
        old_vp = self._vp
        self._vp = vp
        old = self._push_clip(screen, vp)
        x = pr.x + pad
        w = pr.w - pad * 2 - 10
        y = pr.y + pad - int(self._scroll)
        used = fn(screen, x, y, w)
        self._pop_clip(screen, old)
        self._vp = old_vp
        self._content_h = used + pad * 2

        max_scroll = self._max_scroll()
        if max_scroll > 0:
            track = pygame.Rect(pr.right - 12, pr.y + 14, 5, pr.h - 28)
            th = max(30, int(track.h * pr.h / self._content_h))
            frac = self._scroll / max_scroll
            thumb = pygame.Rect(track.x, track.y + int((track.h - th) * frac), track.w, th)
            grab = self._drag is not None and self._drag.get("key") == "scrollbar"
            t = self._anim("scrollbar", self._hov(track.inflate(10, 0)) or grab)
            uk.draw_rect_on(screen, (24, 28, 38), track, 0, 2)
            uk.draw_rect_on(screen, uk.lerp_color(_T.CHIP_BORDER, _T.GOLD, t), thumb, 0, 2)

            def scrub(pos, track=track, th=th, ms=max_scroll):
                f = _clamp((pos[1] - track.y - th / 2) / max(1, track.h - th), 0.0, 1.0)
                self._scroll = f * ms

            self._add_hit(track.inflate(12, 0), key="scrollbar", down=scrub, drag=scrub)

    # ══════════════════════════════════════════════════════════════
    #  Tab: Preview
    # ══════════════════════════════════════════════════════════════

    def _tab_preview(self, screen, x, y, w) -> int:
        y0 = y
        lg, sm = self.f_lg, self.f_sm
        avail = self._content_avail

        label = self.label_text.strip() or pretty_id(self.selected_id or "Decoration")
        chip_w = 0
        if self.current_item and self.current_item.get("hardcoded"):
            chip = pygame.Rect(0, 0, sm.width("BUILT-IN") + 28, 30)
            chip.topright = (x + w, y - 2)
            self._panel(screen, chip, (36, 30, 16), (110, 88, 40), 1, 15)
            self._text_mid(screen, sm, "BUILT-IN", _T.GOLD_BRIGHT, chip.centerx, chip.centery, "c")
            chip_w = chip.w + 12
        self._text_top(screen, lg, lg.fit(label, w - chip_w), _T.TEXT_PRIMARY, x, y, dyn=True)
        y += lg.cap_h + 10

        sub = f"ID: {self.selected_id}"
        if self.grid_rows > 1:
            names = self._variant_labels()
            sub += f"     VARIANT {self.selected_variant + 1}: {names[min(self.selected_variant, len(names) - 1)]}"
        elif self.image_path:
            sub += f"     {relative_asset_path(self.image_path)}"
        self._text_top(screen, sm, sm.fit(sub, w), _T.TEXT_DIM, x, y, dyn=True)
        y += sm.cap_h + 16

        ctrl_h = max(self.m_btn_h, self.m_field_h)
        stage_h = max(120, avail - (y - y0) - ctrl_h - 16)
        stage = pygame.Rect(x, y, w, stage_h)
        self._draw_big_preview(screen, stage)
        y += stage_h + 16

        # playback row
        running = self.preview_running
        play = pygame.Rect(x, y, 132, ctrl_h)
        self._pill(screen, "pv_play", play, "Pause" if running else "Play", _T.GOLD,
                   _ic_pause if running else _ic_play, on_click=self._toggle_play,
                   tip="Play / pause the preview  (Space)")
        cy = y + ctrl_h // 2
        sx = play.right + 24
        n_steps = len(self.sequence)
        sx += self._stat(screen, sx, cy, "SEQUENCE", f"{n_steps} STEP" + ("" if n_steps == 1 else "S")) + 24
        sx += self._stat(screen, sx, cy, "SPEED", f"{self.fps:g} FPS")

        if self.grid_rows > 1:
            rows = self.grid_rows
            seg_w = min(52 * rows, max(0, x + w - sx - 24))
            if seg_w >= 40 * rows:
                seg = pygame.Rect(x + w - seg_w, y + (ctrl_h - self.m_field_h) // 2, seg_w, self.m_field_h)
                self._segmented(screen, "pv_variant", seg.x, seg.y, seg.w,
                                [(i, str(i + 1)) for i in range(rows)],
                                self.selected_variant, self._set_variant)
                lbl_w = sm.width("VARIANT")
                if seg.x - lbl_w - 14 > sx:
                    self._text_mid(screen, sm, "VARIANT", _T.TEXT_MUTED, seg.x - 14, cy, "r")
        return y + ctrl_h - y0

    def _draw_big_preview(self, screen, stage: pygame.Rect) -> None:
        self._stage(screen, stage)
        frame = self._current_frame()
        if frame is None:
            self._text_mid(screen, self.f_md, "No image / sprite sheet", _T.TEXT_DIM, stage.centerx, stage.centery, "c")
            return
        old = self._push_clip(screen, stage)
        fw, fh = frame.get_size()
        base_y = stage.bottom - 34
        scale = _fit_scale(stage.w - 48, base_y - stage.y - 24, fw, fh, 12)
        dw, dh = max(1, int(round(fw * scale))), max(1, int(round(fh * scale)))
        dest = pygame.Rect(0, 0, dw, dh)
        dest.midbottom = (stage.centerx, base_y)
        # Ground line + anchor: the sprite's bottom-centre is its world anchor.
        uk.draw_line_on(screen, _GROUND, (min(dest.left - 30, stage.centerx - 90), base_y),
                        (max(dest.right + 30, stage.centerx + 90), base_y), 1)
        self._blit_clip(screen, self._scaled(frame, dw, dh), dest.topleft)
        uk.draw_line_on(screen, _T.GOLD, (dest.centerx - 8, base_y), (dest.centerx + 8, base_y), 2)
        uk.draw_line_on(screen, _T.GOLD, (dest.centerx, base_y - 8), (dest.centerx, base_y), 2)
        self._pop_clip(screen, old)

    # ══════════════════════════════════════════════════════════════
    #  Tab: Animation
    # ══════════════════════════════════════════════════════════════

    def _tab_animation(self, screen, x, y, w) -> int:
        gap = 28
        if w >= 560:
            lw = int(w * 0.58)
            rw = w - lw - gap
            left = self._anim_left(screen, x, y, lw, self._content_avail)
            right = self._anim_right(screen, x + lw + gap, y, rw)
            return max(left, right)
        left = self._anim_left(screen, x, y, w, self._content_avail)
        right = self._anim_right(screen, x, y + left + 24, w)
        return left + 24 + right

    def _anim_left(self, screen, x, y, w, avail) -> int:
        y0 = y
        en = self._editor_enabled()
        sm = self.f_sm
        cap = sm.cap_h + 18
        tool_h = self._seq_tools_height(w)

        right_txt = ""
        if self.sheet is not None:
            sw, sh = self.sheet.get_size()
            right_txt = f"{sw} x {sh} PX"
        y += self._caption(screen, "Spritesheet", x, y, w, right=right_txt)
        sheet_h = max(130, avail - cap - (14 + cap + SEQ_CELL + 14 + tool_h))
        self._draw_sheet(screen, pygame.Rect(x, y, w, sheet_h), en)
        y += sheet_h + 14

        geom = self._strip_geom(w)
        overflow, _off, _iw, max_cells = geom
        total = len(self.sequence)
        if overflow:
            first = self.sequence_scroll + 1
            info = f"{first}-{min(total, self.sequence_scroll + max_cells)} OF {total}"
        else:
            info = f"{total} STEP" + ("" if total == 1 else "S")
        y += self._caption(screen, "Playback Sequence", x, y, w, right=info)
        self._draw_sequence_strip(screen, x, y, w, en, geom)
        y += SEQ_CELL + 14
        y += self._draw_seq_tools(screen, x, y, w, en)
        return y - y0

    def _draw_sheet(self, screen, rect: pygame.Rect, enabled: bool) -> None:
        self._stage(screen, rect)
        if self.sheet is None:
            self._text_mid(screen, self.f_sm, "NO SPRITE SHEET LOADED", _T.TEXT_DIM, rect.centerx, rect.centery, "c")
            return
        sm = self.f_sm
        hint_h = sm.cap_h + 14
        area = pygame.Rect(rect.x + 12, rect.y + 12, rect.w - 24, max(20, rect.h - 24 - hint_h))
        sw, sh = self.sheet.get_size()
        scale = _fit_scale(area.w, area.h, sw, sh, 8)
        dw, dh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
        ox = area.x + (area.w - dw) // 2
        oy = area.y + (area.h - dh) // 2
        old = self._push_clip(screen, rect.inflate(-2, -2))
        self._blit_clip(screen, self._scaled(self.sheet, dw, dh), (ox, oy))

        playing = None
        if self.sequence:
            playing = self.sequence[self.preview_seq_index % len(self.sequence)]
        for row in range(self.grid_rows):
            for col in range(self.frame_count):
                x0 = ox + int(round(col * self.frame_w * scale))
                x1 = ox + int(round((col + 1) * self.frame_w * scale))
                y0 = oy + int(round(row * self.frame_h * scale))
                y1 = oy + int(round((row + 1) * self.frame_h * scale))
                fr = pygame.Rect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
                on_row = row == self.selected_variant
                t = self._anim(("cell", row, col), enabled and self._hov(fr))
                if t > 0:
                    uk.draw_rect_on(screen, (*_T.GOLD, int(46 * t)), fr, 0, 0)
                is_playing = on_row and playing == col + 1
                if is_playing:
                    color, bw = _T.GOLD, 2
                else:
                    color = uk.lerp_color(_T.KI_BLUE if on_row else _GRID, _T.GOLD, t)
                    bw = 1
                uk.draw_rect_on(screen, color, fr, bw, 0)
                label = str(col + 1)
                if fr.w >= sm.width(label) + 12 and fr.h >= sm.cap_h + 12:
                    self._text_top(screen, sm, label, color, fr.x + 5, fr.y + 5)
                if enabled:
                    self._add_hit(fr, key=("cell", row, col),
                                  down=lambda p, n=col + 1: self._append_sequence_frame(n),
                                  tip=f"Click to add frame {col + 1} to the sequence")
        self._pop_clip(screen, old)
        if enabled:
            for hint in ("CLICK A FRAME TO ADD IT TO THE SEQUENCE", "CLICK A FRAME TO ADD IT", "CLICK A FRAME"):
                if sm.width(hint) <= rect.w - 24:
                    self._text_top(screen, sm, hint, _T.TEXT_DIM, rect.centerx, rect.bottom - hint_h + 4, "c")
                    break

    def _strip_geom(self, w: int):
        """(overflow, inner_offset, inner_width, cells_visible) for the
        sequence strip, and clamp its scroll to match."""
        total = len(self.sequence)
        per = max(1, (w + SEQ_GAP) // (SEQ_CELL + SEQ_GAP))
        if total <= per:
            geom = (False, 0, w, per)
        else:
            off = SEQ_ARROW + SEQ_GAP
            inner_w = w - 2 * off
            geom = (True, off, inner_w, max(1, (inner_w + SEQ_GAP) // (SEQ_CELL + SEQ_GAP)))
        self.sequence_scroll = _clamp(self.sequence_scroll, 0, max(0, total - geom[3]))
        return geom

    def _draw_sequence_strip(self, screen, x, y, w, enabled, geom) -> None:
        overflow, off, _iw, max_cells = geom
        total = len(self.sequence)
        self._seq_strip_rect = pygame.Rect(x, y, w, SEQ_CELL)
        if overflow:
            left = pygame.Rect(x, y, SEQ_ARROW, SEQ_CELL)
            right = pygame.Rect(x + w - SEQ_ARROW, y, SEQ_ARROW, SEQ_CELL)
            self._icon_btn(screen, "seq_l", left, _ic_left, lambda: self._scroll_sequence(-1),
                           enabled=self.sequence_scroll > 0, tip="Scroll the sequence left")
            self._icon_btn(screen, "seq_r", right, _ic_right, lambda: self._scroll_sequence(1),
                           enabled=self.sequence_scroll < total - max_cells, tip="Scroll the sequence right")
        visible = self.sequence[self.sequence_scroll:self.sequence_scroll + max_cells]
        for i, frame_no in enumerate(visible):
            idx = self.sequence_scroll + i
            r = pygame.Rect(x + off + i * (SEQ_CELL + SEQ_GAP), y, SEQ_CELL, SEQ_CELL)
            active = idx == self.preview_seq_index
            t = self._anim(("seq", idx), enabled and self._hov(r))
            base = _SEL if active else uk.lerp_color(_CARD, _CARD_HI, t)
            if active:
                border = _T.GOLD
            else:
                border = uk.lerp_color(_T.CARD_BORDER, _T.DANGER_BRIGHT, 0.7 * t)
            self._panel(screen, r, base, border, 1, 9)
            fg = _T.GOLD_BRIGHT if active else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
            self._text_mid(screen, self.f_md, str(frame_no), fg, r.centerx, r.centery, "c", dyn=True)
            if enabled:
                self._add_hit(r, key=("seq", idx), down=lambda p, k=idx: self._remove_sequence_step(k),
                              tip="Click to remove this step")

    def _seq_tool_specs(self, enabled):
        running = self.preview_running
        return [
            ("t_all", "Use All", None, False, enabled, self._all_sequence, "Play every frame in order"),
            ("t_ping", "Ping-Pong", None, False, enabled, self._ping_pong_sequence, "Forward, then back again"),
            ("t_rev", "Reverse", None, False, enabled, self._reverse_sequence, "Reverse the sequence order"),
            ("t_clr", "Clear", None, True, enabled, self._clear_sequence, "Reset the sequence to frame 1"),
            ("t_play", "Pause" if running else "Play", _ic_pause if running else _ic_play, False, True,
             self._toggle_play, "Play / pause the preview"),
        ]

    def _seq_tool_rows(self, w, specs):
        """Flow the tool pills into rows: each pill gets its label's natural
        width, wraps when the row is full, then the leftover is shared out so
        every row spans the column. Returns [[(spec_index, x_off, width)]]."""
        gap = 8
        naturals = [self.f_md.width(sp[1]) + 36 + (30 if sp[2] else 0) for sp in specs]
        rows, cur, used = [], [], 0
        for i, nw in enumerate(naturals):
            need = nw + (gap if cur else 0)
            if cur and used + need > w:
                rows.append(cur)
                cur, used, need = [], 0, nw
            cur.append(i)
            used += need
        rows.append(cur)
        out = []
        for r_i, row in enumerate(rows):
            stretch = len(rows) == 1 or r_i < len(rows) - 1
            spare = w - (sum(naturals[i] for i in row) + gap * (len(row) - 1))
            extra = max(0, spare // len(row)) if stretch else 0
            x_off, items = 0, []
            for n, i in enumerate(row):
                pw = naturals[i] + extra
                if stretch and n == len(row) - 1:
                    pw = w - x_off
                items.append((i, x_off, pw))
                x_off += pw + gap
            out.append(items)
        return out

    def _seq_tools_height(self, w) -> int:
        rows = len(self._seq_tool_rows(w, self._seq_tool_specs(True)))
        return rows * self.m_btn_h + (rows - 1) * 8

    def _draw_seq_tools(self, screen, x, y, w, enabled) -> int:
        specs = self._seq_tool_specs(enabled)
        h = self.m_btn_h
        rows = self._seq_tool_rows(w, specs)
        for r_i, items in enumerate(rows):
            ry = y + r_i * (h + 8)
            for i, x_off, pw in items:
                key, label, icon, danger, en, cb, tip = specs[i]
                self._pill(screen, key, pygame.Rect(x + x_off, ry, pw, h), label, _T.GOLD, icon,
                           danger=danger, enabled=en, on_click=cb, tip=tip)
        return len(rows) * h + (len(rows) - 1) * 8

    def _anim_right(self, screen, x, y, w) -> int:
        y0 = y
        en = self._editor_enabled()
        has_sheet = self.sheet is not None
        fh = self.m_field_h

        if not en:
            y += self._note(screen, x, y, w, "Built-in animation is protected. Collision can still be edited.",
                            _T.GOLD_BRIGHT) + 12

        y += self._caption(screen, "Identity", x, y, w)
        y += self._field_label(screen, "Display Name", x, y)

        def set_label(v):
            if self.label_text != v:
                self.label_text = v
                self._mark_dirty()

        self._text_field(screen, "a:label", pygame.Rect(x, y, w, fh), lambda: self.label_text, set_label,
                         "e.g. Bush", max_len=48, enabled=en)
        y += fh + 10

        y += self._caption(screen, "Sheet Layout", x, y, w)
        sw, sh = self.sheet.get_size() if has_sheet else (1, 1)
        specs = [
            dict(key="a:fw", label="Frame Width", get=lambda: self.frame_w,
                 set=lambda v: self._apply_layout(frame_w=v), vmin=1, vmax=sw),
            dict(key="a:fh", label="Frame Height", get=lambda: self.frame_h,
                 set=lambda v: self._apply_layout(frame_h=v), vmin=1, vmax=sh),
            dict(key="a:fc", label="Frame Count", get=lambda: self.frame_count,
                 set=lambda v: self._apply_layout(frame_count=v), vmin=1, vmax=max(1, sw // self.frame_w)),
            dict(key="a:rows", label="Variant Rows", get=lambda: self.grid_rows,
                 set=lambda v: self._apply_layout(grid_rows=v), vmin=1, vmax=max(1, sh // self.frame_h)),
        ]
        y += self._stepper_grid(screen, x, y, w, specs, enabled=en and has_sheet)

        y += self._field_label(screen, "Variant Names", x, y)

        def set_names(v):
            if self.variant_names_text != v:
                self.variant_names_text = v
                self._mark_dirty()

        self._text_field(screen, "a:variants", pygame.Rect(x, y, w, fh), lambda: self.variant_names_text,
                         set_names, "e.g. Green, Autumn", max_len=200, enabled=en,
                         tip="Comma-separated, one name per sheet row")
        y += fh + 12

        y += self._caption(screen, "Timing", x, y, w)

        def set_fps(v):
            v = float(v)
            if self.fps != v:
                self.fps = v
                self._mark_dirty()

        y += self._slider(screen, "a:fps", x, y, w, "Animation Speed", self.fps, 0, 30, 0.5, "{:g} fps",
                          set_fps, enabled=en)
        return y - y0

    # ══════════════════════════════════════════════════════════════
    #  Tab: Collision
    # ══════════════════════════════════════════════════════════════

    def _tab_collision(self, screen, x, y, w) -> int:
        gap = 28
        stacked = w < 560
        lw = w if stacked else int(w * 0.54)
        rw = w if stacked else w - lw - gap
        en = self._collision_enabled()

        y0 = y
        mode = "MANUAL" if self.collision_manual else "AUTO"
        y += self._caption(screen, "Collision Box", x, y, lw, right=mode)
        stage_h = max(160, self._content_avail - (y - y0))
        self._draw_collision_stage(screen, pygame.Rect(x, y, lw, stage_h), en)
        left_used = (y - y0) + stage_h

        if stacked:
            ry = y0 + left_used + 24
            rx = x
        else:
            ry = y0
            rx = x + lw + gap
        r0 = ry

        ry += self._caption(screen, "Mode", rx, ry, rw)
        self._segmented(screen, "c:mode", rx, ry, rw,
                        [("auto", "Automatic"), ("manual", "Manual")],
                        "manual" if self.collision_manual else "auto",
                        lambda v: self._use_auto_collision() if v == "auto" else self._use_manual_collision(),
                        enabled=en)
        ry += self.m_field_h + 18

        ry += self._caption(screen, "Box  (frame pixels)", rx, ry, rw)
        cur = self._current_collision()
        frame = self._current_frame()
        fw, fh = frame.get_size() if frame is not None else (1, 1)
        cx, cy, cw, ch = cur if cur else (0, 0, 1, 1)
        specs = [
            dict(key="c:x", label="X", get=lambda: cx, set=lambda v: self._set_collision_component(0, v),
                 vmin=0, vmax=max(0, fw - cw)),
            dict(key="c:y", label="Y", get=lambda: cy, set=lambda v: self._set_collision_component(1, v),
                 vmin=0, vmax=max(0, fh - ch)),
            dict(key="c:w", label="Width", get=lambda: cw, set=lambda v: self._set_collision_component(2, v),
                 vmin=1, vmax=max(1, fw)),
            dict(key="c:h", label="Height", get=lambda: ch, set=lambda v: self._set_collision_component(3, v),
                 vmin=1, vmax=max(1, fh)),
        ]
        ry += self._stepper_grid(screen, rx, ry, rw, specs, enabled=en and cur is not None)

        if cur is None:
            ry += self._note(screen, rx, ry, rw, "No collision box available for this decoration.") + 6
        if self.collision_auto_rect:
            a = self.collision_auto_rect
            ry += self._note(screen, rx, ry, rw, f"Auto box: {a[0]}, {a[1]}, {a[2]} x {a[3]}",
                             _T.TEXT_MUTED) + 6
        ry += self._note(screen, rx, ry, rw, "Drag the box to move it, or a corner handle to resize it. "
                         "Only this compact box blocks movement and beams.") + 6
        return max(left_used, ry - r0) if not stacked else left_used + 24 + (ry - r0)

    def _draw_collision_stage(self, screen, stage: pygame.Rect, enabled: bool) -> None:
        self._stage(screen, stage)
        frame = self._current_frame()
        if frame is None:
            self._text_mid(screen, self.f_md, "No image / sprite sheet", _T.TEXT_DIM, stage.centerx, stage.centery, "c")
            return

        old = self._push_clip(screen, stage.inflate(-2, -2))
        fw, fh = frame.get_size()
        fit = stage.inflate(-64, -64)
        scale = _fit_scale(fit.w, fit.h, fw, fh, 24)
        dw, dh = max(1, int(round(fw * scale))), max(1, int(round(fh * scale)))
        frame_rect = pygame.Rect(0, 0, dw, dh)
        frame_rect.center = stage.center
        self._blit_clip(screen, self._scaled(frame, dw, dh), frame_rect.topleft)

        # Sprite boundary, base line and anchor.
        uk.draw_rect_on(screen, _GRID, frame_rect, 1, 0)
        uk.draw_line_on(screen, _GROUND, (frame_rect.left - 14, frame_rect.bottom),
                        (frame_rect.right + 14, frame_rect.bottom), 1)
        uk.draw_circle_on(screen, _T.GOLD, (frame_rect.centerx, frame_rect.bottom), 4)

        self._col_geom = (frame_rect, scale)
        cur = self._current_collision()
        if cur is not None:
            cr = self._rect_to_screen(cur, frame_rect, scale)
            uk.draw_rect_on(screen, (*_T.DANGER, 62), cr, 0, 0)
            uk.draw_rect_on(screen, _T.DANGER_BRIGHT, cr, 2, 0)

            hover_corner = None
            dragging = self.collision_drag is not None
            corners = {"tl": cr.topleft, "tr": cr.topright, "bl": cr.bottomleft, "br": cr.bottomright}
            if enabled and not dragging and self._hov(stage):
                for name, (px, py) in corners.items():
                    if pygame.Rect(px - 8, py - 8, 16, 16).collidepoint(self._hm):
                        hover_corner = name
                        break
                if hover_corner:
                    self._cursor_hint = "resize"
                elif cr.collidepoint(self._hm):
                    self._cursor_hint = "move"
            for name, (px, py) in corners.items():
                hot = name == hover_corner or (dragging and self.collision_drag["mode"] == name)
                handle = pygame.Rect(0, 0, 12 if hot else 10, 12 if hot else 10)
                handle.center = (px, py)
                if hot:
                    uk.draw_soft_glow(screen, (px, py), 18, _T.DANGER_BRIGHT, max_alpha=60)
                uk.draw_rect_on(screen, _T.DANGER_BRIGHT if not hot else (255, 190, 190), handle, 0, 2)
                uk.draw_rect_on(screen, _BG, handle, 1, 2)

            tag = "MANUAL" if self.collision_manual else "AUTO"
            sm = self.f_sm
            tag_y = cr.top - sm.cap_h - 8
            if tag_y < stage.y + 6:
                tag_y = cr.top + 6
            self._text_top(screen, sm, tag, _T.DANGER_BRIGHT, cr.x + 2, tag_y)

        if enabled:
            self._add_hit(stage, key="c:canvas", down=self._collision_down,
                          drag=self._collision_move, up=self._collision_up)
        self._pop_clip(screen, old)


# ---------------------------------------------------------------------------
# Optional standalone entry point
# ---------------------------------------------------------------------------
def run(screen: pygame.Surface, clock: pygame.time.Clock) -> None:
    """Convenience mode for testing the creator outside the host game loop."""
    creator = DecorationCreator(*screen.get_size())
    creator.toggle()
    running = True
    while running and creator.active:
        dt = clock.tick(60) / 1000.0
        for event in pygame.event.get():
            result = creator.handle_input(event)
            if result in ("close", "back_to_dev_menu"):
                running = False
        creator.update(dt)
        creator.draw(screen, dt)
        pygame.display.flip()