"""
decoration_creator.py — Dev-menu Decoration Creator
=====================================================

A dedicated editor for the scenery/decorations used by the Object Editor.

It deliberately follows the same non-blocking overlay style as the Character
Creator: the host game owns the event loop and simply calls
``toggle()`` / ``handle_input()`` / ``update()`` / ``draw()`` every frame.

Features
--------
* Discovers decoration assets under ``assets/objects/decorations/``.
* Shows the complete decoration roster in a left-hand list.
* Animated preview of the currently configured sequence.
* Visual spritesheet/frame layout editor.
* Visual animation-sequence builder: click frames in the order they should
  play instead of typing a raw list.
* Visual collision editor: drag the collision box and resize it with corner
  handles directly over the sprite.
* Auto-collision mode for decorations where a manual box is not necessary.
* Saves each discovered decoration's settings to ``decoration.json`` beside
  its art.
* The existing hardcoded ``tree`` is visible but explicitly locked so its
  hand-authored animation cannot be accidentally overwritten.

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
import os
import sys
from pathlib import Path
from typing import Optional

import pygame

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


# ---------------------------------------------------------------------------
# Palette — deliberately close to character_creator.py's dark dev-tool UI.
# ---------------------------------------------------------------------------
C_BG = (14, 14, 20)
C_PANEL = (24, 24, 34)
C_PANEL_DARK = (18, 18, 26)
C_BORDER = (50, 50, 72)
C_ACCENT = (80, 160, 255)
C_ACCENT2 = (255, 200, 55)
C_TEXT = (215, 215, 228)
C_TEXT_DIM = (110, 110, 138)
C_RED = (220, 70, 70)
C_GREEN = (70, 200, 100)
C_HOVER = (40, 40, 62)
C_SELECTED = (30, 75, 140)
C_COLLISION = (255, 80, 80)
C_COLLISION_FILL = (255, 60, 60, 55)
C_GRID = (42, 42, 58)
C_CANVAS = (12, 12, 18)

HEADER_H = 44
FOOTER_H = 52
LIST_W = 215
TAB_H = 36
PAD = 10

TAB_PREVIEW = 0
TAB_ANIMATION = 1
TAB_COLLISION = 2
TAB_NAMES = ("Preview", "Animation", "Collision")


# ---------------------------------------------------------------------------
# Tiny UI helpers
# ---------------------------------------------------------------------------
def _font(size: int, bold: bool = False):
    pygame.font.init()
    try:
        return pygame.font.SysFont("segoeui,dejavusans,arial", size, bold=bold)
    except Exception:
        return pygame.font.Font(None, size + 4)


_FONT_CACHE: dict[tuple[int, bool], pygame.font.Font] = {}
_TEXT_CACHE: dict[tuple[int, bool, str, tuple[int, int, int]], pygame.Surface] = {}


def get_font(size: int, bold: bool = False) -> pygame.font.Font:
    key = (size, bold)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = _font(size, bold)
    return _FONT_CACHE[key]


def text(surf: pygame.Surface, value: str, pos, color=C_TEXT,
         size: int = 14, bold: bool = False) -> pygame.Rect:
    key = (size, bold, str(value), tuple(color))
    img = _TEXT_CACHE.get(key)
    if img is None:
        img = get_font(size, bold).render(str(value), True, color)
        _TEXT_CACHE[key] = img
    surf.blit(img, pos)
    return pygame.Rect(pos[0], pos[1], img.get_width(), img.get_height())


def button(surf: pygame.Surface, rect: pygame.Rect, label: str,
           hover=False, active=False, disabled=False, danger=False,
           small=False) -> None:
    base = C_RED if danger else C_ACCENT
    if disabled:
        base = C_BORDER
    bg = C_PANEL_DARK if not (hover or active) else (C_HOVER if not active else C_SELECTED)
    if disabled:
        bg = C_PANEL_DARK
    surf.draw_rect(bg, rect, border_radius=5)
    surf.draw_rect(base, rect, 1, border_radius=5)
    f = get_font(12 if small else 13, bold=False)
    img = f.render(label, True, C_TEXT_DIM if disabled else (C_TEXT if hover or active else base))
    surf.blit(img, img.get_rect(center=rect.center))


def panel(surf: pygame.Surface, rect: pygame.Rect, fill=C_PANEL_DARK,
          border=C_BORDER, radius=6, width=1) -> None:
    surf.draw_rect(fill, rect, border_radius=radius)
    surf.draw_rect(border, rect, width, border_radius=radius)


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
# Text field
# ---------------------------------------------------------------------------
class TextField:
    H = 30

    def __init__(self, rect: pygame.Rect, value: str = ""):
        self.rect = rect.copy()
        self.value = str(value)
        self.active = False
        self.cursor = len(self.value)

    def set_value(self, value: str) -> None:
        self.value = str(value)
        self.cursor = min(self.cursor, len(self.value))

    def handle_event(self, event: pygame.event.Event) -> bool:
        changed = False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.active = self.rect.collidepoint(event.pos)
            if self.active:
                self.cursor = len(self.value)
            return False

        if not self.active or event.type != pygame.KEYDOWN:
            return False

        if event.key == pygame.K_BACKSPACE:
            if self.cursor > 0:
                self.value = self.value[:self.cursor - 1] + self.value[self.cursor:]
                self.cursor -= 1
                changed = True
        elif event.key == pygame.K_DELETE:
            if self.cursor < len(self.value):
                self.value = self.value[:self.cursor] + self.value[self.cursor + 1:]
                changed = True
        elif event.key == pygame.K_LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif event.key == pygame.K_RIGHT:
            self.cursor = min(len(self.value), self.cursor + 1)
        elif event.key == pygame.K_HOME:
            self.cursor = 0
        elif event.key == pygame.K_END:
            self.cursor = len(self.value)
        elif event.key in (pygame.K_RETURN, pygame.K_TAB, pygame.K_ESCAPE):
            self.active = False
        elif event.unicode and event.unicode.isprintable():
            self.value = self.value[:self.cursor] + event.unicode + self.value[self.cursor:]
            self.cursor += 1
            changed = True
        return changed

    def draw(self, surf: pygame.Surface, enabled=True) -> None:
        border = C_ACCENT if self.active and enabled else C_BORDER
        if not enabled:
            border = C_BORDER
        bg = C_PANEL_DARK if enabled else (15, 15, 22)
        surf.draw_rect(bg, self.rect, border_radius=4)
        surf.draw_rect(border, self.rect, 1, border_radius=4)
        col = C_TEXT if enabled else C_TEXT_DIM
        img = get_font(13).render(self.value, True, col)
        clip = self.rect.inflate(-8, -2)
        surf.set_clip(clip)
        surf.blit(img, (clip.x, self.rect.y + (self.rect.h - img.get_height()) // 2))
        surf.set_clip(None)
        if self.active and enabled:
            cursor_x = self.rect.x + 7 + get_font(13).size(self.value[:self.cursor])[0]
            surf.draw_line(C_TEXT,
                             (cursor_x, self.rect.y + 5),
                             (cursor_x, self.rect.bottom - 5), 1)


# ---------------------------------------------------------------------------
# Simple slider — same visual language as the Character Creator.
# ---------------------------------------------------------------------------
class Slider:
    H = 22

    def __init__(self, rect: pygame.Rect, minimum: float, maximum: float,
                 value: float, step: float = 1.0):
        self.rect = rect.copy()
        self.min = float(minimum)
        self.max = float(maximum)
        self.value = max(self.min, min(self.max, float(value)))
        self.step = float(step)
        self.dragging = False

    def _from_x(self, x: int) -> float:
        t = (x - self.rect.x) / max(1, self.rect.w)
        raw = self.min + t * (self.max - self.min)
        if self.step:
            raw = round(raw / self.step) * self.step
        return max(self.min, min(self.max, raw))

    def handle_event(self, event: pygame.event.Event, enabled=True) -> bool:
        if not enabled:
            self.dragging = False
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.dragging = True
                self.value = self._from_x(event.pos[0])
                return True
        elif event.type == pygame.MOUSEMOTION and self.dragging:
            self.value = self._from_x(event.pos[0])
            return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging = False
        return False

    def draw(self, surf: pygame.Surface, enabled=True) -> None:
        y = self.rect.centery
        track = pygame.Rect(self.rect.x, y - 3, self.rect.w, 6)
        surf.draw_rect(C_BORDER if enabled else (35, 35, 46), track, border_radius=3)
        rng = max(1e-6, self.max - self.min)
        t = (self.value - self.min) / rng
        fill = pygame.Rect(track.x, track.y, int(track.w * t), track.h)
        if fill.w:
            surf.draw_rect(C_ACCENT if enabled else C_BORDER, fill, border_radius=3)
        kx = self.rect.x + int(track.w * t)
        surf.draw_circle(C_ACCENT if enabled else C_BORDER, (kx, y), 7)


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


# ---------------------------------------------------------------------------
# Left roster panel
# ---------------------------------------------------------------------------
class DecorationList:
    ITEM_H = 46

    def __init__(self, rect: pygame.Rect):
        self.rect = rect.copy()
        self.items: list[dict] = []
        self.selected = ""
        self.scroll = 0

    def set_items(self, items: list[dict], selected: str = "") -> None:
        self.items = items
        self.selected = selected or (items[0]["id"] if items else "")
        self.scroll = 0

    def handle_event(self, event: pygame.event.Event) -> Optional[str]:
        visible = self.rect.inflate(-4, -42)
        if event.type == pygame.MOUSEWHEEL and visible.collidepoint(pygame.mouse.get_pos()):
            max_scroll = max(0, len(self.items) - max(1, visible.h // self.ITEM_H))
            self.scroll = max(0, min(max_scroll, self.scroll - event.y))

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if not visible.collidepoint(mx, my):
                return None
            for i, item in enumerate(self.items):
                row = visible.y + (i - self.scroll) * self.ITEM_H
                rr = pygame.Rect(visible.x, row, visible.w, self.ITEM_H - 4)
                if rr.collidepoint(mx, my):
                    if self.selected != item["id"]:
                        self.selected = item["id"]
                        return item["id"]
        return None

    def draw(self, surf: pygame.Surface, mouse_pos) -> None:
        panel(surf, self.rect)
        text(surf, "DECORATIONS", (self.rect.x + 12, self.rect.y + 10), C_TEXT_DIM, 12, True)
        visible = pygame.Rect(self.rect.x + 4, self.rect.y + 38,
                              self.rect.w - 8, self.rect.h - 42)
        old_clip = surf.get_clip()
        surf.set_clip(visible)
        for i, item in enumerate(self.items):
            y = visible.y + (i - self.scroll) * self.ITEM_H
            rr = pygame.Rect(visible.x, y, visible.w, self.ITEM_H - 4)
            if rr.bottom < visible.top or rr.top > visible.bottom:
                continue
            selected = item["id"] == self.selected
            hovered = rr.collidepoint(*mouse_pos)
            bg = C_SELECTED if selected else (C_HOVER if hovered else C_PANEL_DARK)
            surf.draw_rect(bg, rr, border_radius=5)
            surf.draw_rect(C_ACCENT if selected else C_BORDER, rr,
                             1, border_radius=5)

            img = item.get("thumb")
            if img is not None:
                thumb = img.copy()
                thumb_scale = min(30 / max(1, thumb.get_width()),
                                  34 / max(1, thumb.get_height()))
                tw = max(1, int(thumb.get_width() * thumb_scale))
                th = max(1, int(thumb.get_height() * thumb_scale))
                thumb = pygame.transform.scale(thumb, (tw, th))
                surf.blit(thumb, (rr.x + 8 + (34 - tw) // 2,
                                  rr.y + (rr.h - th) // 2))
            else:
                surf.draw_rect(C_GRID,
                                 pygame.Rect(rr.x + 10, rr.y + 7, 30, rr.h - 14),
                                 border_radius=3)

            text(surf, item.get("label", item["id"]), (rr.x + 50, rr.y + 8),
                 C_TEXT if selected else C_TEXT_DIM, 14)
            badge = "BUILT-IN" if item.get("hardcoded") else ""
            if badge:
                text(surf, badge, (rr.x + 50, rr.bottom - 17), C_ACCENT2, 10, True)

        surf.set_clip(old_clip)


# ---------------------------------------------------------------------------
# Main editor
# ---------------------------------------------------------------------------
class DecorationCreator:
    """Non-blocking dev-menu overlay for decoration authoring."""

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width = int(screen_width)
        self.screen_height = int(screen_height)
        self.active = False
        self.active_tab = TAB_PREVIEW

        self.font = get_font(15)
        self.font_sm = get_font(12)
        self.font_hd = get_font(20, True)

        self.list_rect = pygame.Rect(
            PAD, HEADER_H + PAD, LIST_W,
            self.screen_height - HEADER_H - FOOTER_H - PAD * 2,
        )
        self.editor_x = LIST_W + PAD * 2
        self.editor_rect = pygame.Rect(
            self.editor_x, HEADER_H + PAD + TAB_H,
            self.screen_width - self.editor_x - PAD,
            self.screen_height - HEADER_H - FOOTER_H - PAD * 2 - TAB_H,
        )
        self.preview_rect = pygame.Rect(
            PAD, self.screen_height - FOOTER_H - 215 - PAD,
            LIST_W, 205,
        )

        tab_w = max(1, self.editor_rect.w // len(TAB_NAMES))
        self.tab_rects = [
            pygame.Rect(self.editor_x + i * tab_w, HEADER_H + PAD,
                        tab_w if i < len(TAB_NAMES) - 1 else self.editor_rect.right - (self.editor_x + i * tab_w),
                        TAB_H)
            for i in range(len(TAB_NAMES))
        ]

        self.btn_save = pygame.Rect(self.screen_width - 225,
                                    self.screen_height - FOOTER_H + 10, 95, 32)
        self.btn_refresh = pygame.Rect(self.screen_width - 120,
                                       self.screen_height - FOOTER_H + 10, 105, 32)

        self.roster = DecorationList(self.list_rect)
        self.decorations: list[dict] = []
        self.selected_id: Optional[str] = None
        self.current_item: Optional[dict] = None
        self.style: dict = {}
        self.sheet: Optional[pygame.Surface] = None
        self.image_path: Optional[Path] = None

        # Config values being edited.
        self.label_input = TextField(pygame.Rect(0, 0, 220, 30))
        self.frame_w_input = TextField(pygame.Rect(0, 0, 72, 30))
        self.frame_h_input = TextField(pygame.Rect(0, 0, 72, 30))
        self.frame_count_input = TextField(pygame.Rect(0, 0, 72, 30))
        self.rows_input = TextField(pygame.Rect(0, 0, 72, 30))
        self.variant_names_input = TextField(pygame.Rect(0, 0, 300, 30))

        self.fps_slider = Slider(pygame.Rect(0, 0, 240, 22), 0, 30, 6, 0.5)

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
        self._sequence_strip_rect = None
        self._sequence_scroll_left_rect = None
        self._sequence_scroll_right_rect = None

        # Collision state — local to a sprite frame.
        self.collision_manual = False
        self.collision_rect: Optional[tuple[int, int, int, int]] = None
        self.collision_rect_input = {
            "x": TextField(pygame.Rect(0, 0, 58, 30)),
            "y": TextField(pygame.Rect(0, 0, 58, 30)),
            "w": TextField(pygame.Rect(0, 0, 58, 30)),
            "h": TextField(pygame.Rect(0, 0, 58, 30)),
        }
        self.collision_drag = None  # {mode,start_mouse,start_rect}
        self.collision_auto_rect: Optional[tuple[int, int, int, int]] = None

        self.dirty = False
        self.status_msg = ""
        self.status_col = C_TEXT_DIM
        self.status_timer = 0.0

        # Optional host callback, e.g. to refresh the Object Editor catalogue.
        self.on_catalog_changed = None

        self._refresh_roster(keep_selection=False)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def toggle(self) -> None:
        self.active = not self.active
        if self.active:
            self._refresh_roster(keep_selection=True)

    def _set_status(self, msg: str, ok=True) -> None:
        self.status_msg = msg
        self.status_col = C_GREEN if ok else C_RED
        self.status_timer = 2.5

    def _refresh_roster(self, keep_selection=True) -> None:
        previous = self.selected_id if keep_selection else None
        self.decorations = _root_items()

        for item in self.decorations:
            item["thumb"] = self._make_thumbnail(item)

        ids = [item["id"] for item in self.decorations]
        selected = previous if previous in ids else (ids[0] if ids else None)
        self.roster.set_items(self.decorations, selected or "")

        if selected:
            self._select(selected)
        else:
            self.selected_id = None
            self.current_item = None

    def _make_thumbnail(self, item: dict):
        path = item.get("image")
        if not path or not path.exists():
            return None
        try:
            sheet = pygame.image.load(str(path)).convert_alpha()
            style = item.get("style", {})
            fw = clamp_int(style.get("frame_w", sheet.get_width()), 1, sheet.get_width(), sheet.get_width())
            fh = clamp_int(style.get("frame_h", sheet.get_height()), 1, sheet.get_height(), sheet.get_height())
            row = 0
            frame = pygame.Surface((fw, fh), pygame.SRCALPHA)
            frame.blit(sheet, (0, 0), pygame.Rect(0, row * fh, fw, fh))
            return frame
        except (pygame.error, OSError):
            return None

    def _select(self, deco_id: str) -> None:
        item = next((x for x in self.decorations if x["id"] == deco_id), None)
        if not item:
            return
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
            self.sequence = [
                clamp_int(v, 1, self.frame_count, 1)
                for v in raw_seq
            ]
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
        self._sync_fields()

        saved_collision = rect_tuple(self.style.get("collision_rect"))
        self.collision_manual = saved_collision is not None
        self.collision_rect = saved_collision
        self._refresh_collision_defaults()

        self.preview_seq_index = 0
        self.anim_timer = 0.0
        self.dirty = False

    def _sync_fields(self) -> None:
        self.label_input.set_value(str(self.style.get("label", pretty_id(self.selected_id or "Decoration"))))
        self.frame_w_input.set_value(str(self.frame_w))
        self.frame_h_input.set_value(str(self.frame_h))
        self.frame_count_input.set_value(str(self.frame_count))
        self.rows_input.set_value(str(self.grid_rows))
        self.variant_names_input.set_value(", ".join(self.variant_names))
        self.fps_slider.value = self.fps
        self._sync_collision_fields()

    def _sync_collision_fields(self) -> None:
        r = self.collision_rect or self.collision_auto_rect or (0, 0, 8, 8)
        for key, value in zip(("x", "y", "w", "h"), r):
            self.collision_rect_input[key].set_value(str(int(value)))

    # ------------------------------------------------------------------
    # Config / animation helpers
    # ------------------------------------------------------------------
    def _read_numeric_fields(self) -> None:
        if not self.sheet:
            return
        sw, sh = self.sheet.get_size()

        old = (self.frame_w, self.frame_h, self.frame_count, self.grid_rows)
        self.frame_w = clamp_int(self.frame_w_input.value, 1, sw, self.frame_w)
        self.frame_h = clamp_int(self.frame_h_input.value, 1, sh, self.frame_h)
        self.frame_count = clamp_int(self.frame_count_input.value, 1,
                                     max(1, sw // self.frame_w), self.frame_count)
        self.grid_rows = clamp_int(self.rows_input.value, 1,
                                   max(1, sh // self.frame_h), self.grid_rows)

        changed_layout = old != (self.frame_w, self.frame_h, self.frame_count, self.grid_rows)
        if changed_layout:
            self.sequence = [clamp_int(n, 1, self.frame_count, 1) for n in self.sequence] or [1]
            self.variant_names = self.variant_names[:self.grid_rows]
            base = self.style.get("label", pretty_id(self.selected_id or "Decoration"))
            while len(self.variant_names) < self.grid_rows:
                self.variant_names.append(f"{base} (Variant {len(self.variant_names) + 1})")
            self._refresh_collision_defaults()

        self.fps = float(self.fps_slider.value)
        self._set_style_dirty()

    def _set_style_dirty(self) -> None:
        # Built-ins keep their animation definition locked, but their collision
        # can still be edited and saved as a sidecar override.
        if self.current_item:
            self.dirty = True

    def _collision_enabled(self) -> bool:
        """Collision editing is available for every decoration, including Tree."""
        return self.current_item is not None

    def _frames(self) -> list[pygame.Surface]:
        if self.sheet is None:
            return []
        frames = []
        sw, sh = self.sheet.get_size()
        # Current variant only. The runtime treats each row as a variant and
        # each column as a frame.
        row_y = self.selected_variant * self.frame_h
        for col in range(self.frame_count):
            x = col * self.frame_w
            if x + self.frame_w > sw or row_y + self.frame_h > sh:
                break
            frames.append(self.sheet.subsurface(
                pygame.Rect(x, row_y, self.frame_w, self.frame_h)
            ).copy())
        return frames

    def _current_frame(self) -> Optional[pygame.Surface]:
        frames = self._frames()
        if not frames:
            return None
        if not self.sequence:
            return frames[0]
        seq_n = self.sequence[self.preview_seq_index % len(self.sequence)]
        return frames[max(0, min(len(frames) - 1, seq_n - 1))]

    def _refresh_collision_defaults(self) -> None:
        frames = self._frames()
        frame = frames[0] if frames else None
        self.collision_auto_rect = _collision_from_style(self.style, frame)
        if not self.collision_manual:
            self.collision_rect = None
        self._sync_collision_fields()

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
        self._sync_collision_fields()
        self._set_style_dirty()

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
        self._set_style_dirty()

    def _append_sequence_frame(self, frame_number: int) -> None:
        if self.current_item and self.current_item.get("hardcoded"):
            return
        frame_number = max(1, min(self.frame_count, int(frame_number)))
        self.sequence.append(frame_number)
        self.preview_seq_index = max(0, len(self.sequence) - 1)
        self._set_style_dirty()

    def _clear_sequence(self) -> None:
        if self.current_item and self.current_item.get("hardcoded"):
            return
        self.sequence = [1]
        self.preview_seq_index = 0
        self._set_style_dirty()

    def _all_sequence(self) -> None:
        if self.current_item and self.current_item.get("hardcoded"):
            return
        self.sequence = list(range(1, self.frame_count + 1)) or [1]
        self.preview_seq_index = 0
        self._set_style_dirty()

    def _ping_pong_sequence(self) -> None:
        if self.current_item and self.current_item.get("hardcoded"):
            return
        n = max(1, self.frame_count)
        if n == 1:
            self.sequence = [1]
        else:
            self.sequence = list(range(1, n + 1)) + list(range(n - 1, 1, -1))
        self.preview_seq_index = 0
        self._set_style_dirty()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def save(self) -> bool:
        item = self.current_item
        if not item:
            self._set_status("No decoration selected", ok=False)
            return False

        # Tree keeps its hand-authored spritesheet/animation completely
        # protected. Collision is intentionally editable through a sidecar
        # override so the visual collision workflow still works for Tree.
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
                    # collision_size for Tree.
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
                self._set_status("Saved Tree collision")
            if callable(self.on_catalog_changed):
                try:
                    self.on_catalog_changed()
                except Exception:
                    pass
            return True

        if self.image_path is None or not self.image_path.exists():
            self._set_status("No decoration image found", ok=False)
            return False

        self._read_numeric_fields()

        raw_names = [x.strip() for x in self.variant_names_input.value.split(",")]
        raw_names = [x for x in raw_names if x]
        if not raw_names:
            raw_names = [pretty_id(self.selected_id or "Decoration")]
        self.variant_names = (raw_names[:self.grid_rows] +
                              [f"Variant {i + 1}" for i in range(len(raw_names), self.grid_rows)])[:self.grid_rows]

        self.style.update({
            "label": self.label_input.value.strip() or pretty_id(self.selected_id or "Decoration"),
            "sheet_path": relative_asset_path(self.image_path),
            "frame_w": self.frame_w,
            "frame_h": self.frame_h,
            "grid_rows": self.grid_rows,
            "frame_count": self.frame_count,
            "sequence": [clamp_int(v, 1, self.frame_count, 1) for v in self.sequence] or [1],
            "fps": round(float(self.fps_slider.value), 2),
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
        if current and current == self.selected_id:
            self._set_status(f"Saved {manifest.name}")
        if callable(self.on_catalog_changed):
            try:
                self.on_catalog_changed()
            except Exception:
                pass
        return True

    # ------------------------------------------------------------------
    # Input helpers
    # ------------------------------------------------------------------
    def _editor_enabled(self) -> bool:
        return bool(self.current_item and not self.current_item.get("hardcoded"))

    def _tab_changed(self, index: int) -> None:
        if index == TAB_ANIMATION:
            self._read_numeric_fields()
        self.active_tab = index

    def _animation_frame_hit(self, pos) -> Optional[int]:
        """Hit-test the clickable frame boxes drawn in the animation tab."""
        rect = self._animation_source_rect()
        if not rect or not self.sheet:
            return None

        # Fit the complete sheet in rect while keeping integer-ish nearest
        # scaling. The source image itself is never modified.
        sw, sh = self.sheet.get_size()
        scale = min(rect.w / max(1, sw), rect.h / max(1, sh))
        draw_w = max(1, int(sw * scale))
        draw_h = max(1, int(sh * scale))
        ox = rect.x + (rect.w - draw_w) // 2
        oy = rect.y + (rect.h - draw_h) // 2
        px, py = pos
        if not (ox <= px < ox + draw_w and oy <= py < oy + draw_h):
            return None
        sx = int((px - ox) / max(scale, 1e-6))
        sy = int((py - oy) / max(scale, 1e-6))
        col = sx // self.frame_w
        row = sy // self.frame_h
        if not (0 <= row < self.grid_rows and 0 <= col < self.frame_count):
            return None
        # Current runtime sequence numbering is frame number within the row.
        return col + 1

    # ------------------------------------------------------------------
    # Collision drag logic
    # ------------------------------------------------------------------
    def _collision_canvas(self) -> tuple[Optional[pygame.Rect], float, Optional[pygame.Surface]]:
        canvas = self._collision_source_rect()
        frame = self._current_frame()
        if canvas is None or frame is None:
            return None, 1.0, frame
        fw, fh = frame.get_size()
        scale = min(canvas.w / max(1, fw), canvas.h / max(1, fh))
        draw_w = max(1, int(fw * scale))
        draw_h = max(1, int(fh * scale))
        rect = pygame.Rect(canvas.x + (canvas.w - draw_w) // 2,
                           canvas.y + (canvas.h - draw_h) // 2,
                           draw_w, draw_h)
        return rect, scale, frame

    def _collision_rect_screen(self) -> Optional[pygame.Rect]:
        frame_rect, scale, frame = self._collision_canvas()
        if frame_rect is None or frame is None:
            return None
        local = self.collision_rect if self.collision_manual and self.collision_rect else self.collision_auto_rect
        if local is None:
            return None
        x, y, w, h = local
        return pygame.Rect(
            int(frame_rect.x + x * scale),
            int(frame_rect.y + y * scale),
            max(1, int(w * scale)),
            max(1, int(h * scale)),
        )

    def _begin_collision_drag(self, pos) -> None:
        if not self._collision_enabled():
            return
        frame_rect, scale, frame = self._collision_canvas()
        if frame_rect is None or frame is None:
            return

        current = self.collision_rect if self.collision_manual and self.collision_rect else self.collision_auto_rect
        if current is None:
            return

        screen_rect = self._collision_rect_screen()
        if screen_rect is None:
            return

        handle = 8
        handles = {
            "tl": pygame.Rect(screen_rect.left - handle // 2, screen_rect.top - handle // 2, handle, handle),
            "tr": pygame.Rect(screen_rect.right - handle // 2, screen_rect.top - handle // 2, handle, handle),
            "bl": pygame.Rect(screen_rect.left - handle // 2, screen_rect.bottom - handle // 2, handle, handle),
            "br": pygame.Rect(screen_rect.right - handle // 2, screen_rect.bottom - handle // 2, handle, handle),
        }
        mode = next((name for name, rr in handles.items() if rr.collidepoint(pos)), None)
        if mode is None and screen_rect.collidepoint(pos):
            mode = "move"
        if mode is not None:
            self.collision_drag = {
                "mode": mode,
                "mouse": pos,
                "rect": tuple(current),
                "scale": scale,
                "frame_rect": frame_rect,
            }
            self._set_manual_collision(tuple(current))

    def _update_collision_drag(self, pos) -> None:
        d = self.collision_drag
        if not d or not self.collision_rect:
            return
        frame_rect = d["frame_rect"]
        scale = d["scale"]
        fw, fh = self._current_frame().get_size() if self._current_frame() else (1, 1)
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
        self._sync_collision_fields()
        self._set_style_dirty()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def handle_input(self, event: pygame.event.Event):
        if not self.active:
            return None

        enabled = self._editor_enabled()

        # Text fields consume keyboard input first.
        fields = [
            self.label_input,
            self.frame_w_input,
            self.frame_h_input,
            self.frame_count_input,
            self.rows_input,
            self.variant_names_input,
            *self.collision_rect_input.values(),
        ]
        for field in fields:
            if field.active:
                changed = field.handle_event(event)
                if changed:
                    self.dirty = bool(self.current_item)
                    if field in self.collision_rect_input.values():
                        self._apply_collision_field_values()
                    elif field in (self.frame_w_input, self.frame_h_input,
                                   self.frame_count_input, self.rows_input):
                        self._read_numeric_fields()
                return None

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                if self.dirty:
                    self._set_status("Unsaved changes discarded", ok=False)
                self.active = False
                return "close"
            if event.key == pygame.K_SPACE and self.active_tab == TAB_PREVIEW:
                self.preview_running = not self.preview_running
                return None
            if event.key == pygame.K_s and (event.mod & pygame.KMOD_CTRL):
                self.save()
                return None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos

            # Header tabs.
            for i, rr in enumerate(self.tab_rects):
                if rr.collidepoint(mx, my):
                    self._tab_changed(i)
                    return None

            # Footer.
            if self.btn_save.collidepoint(mx, my):
                self.save()
                return None
            if self.btn_refresh.collidepoint(mx, my):
                self._refresh_roster(keep_selection=True)
                self._set_status("Decoration catalogue refreshed")
                return None

            # Left roster.
            new_sel = self.roster.handle_event(event)
            if new_sel:
                self._select(new_sel)
                return None

        # Preview tab widgets.
        if self.active_tab == TAB_PREVIEW:
            if self._handle_preview_input(event):
                return None

        # Animation tab widgets.
        if self.active_tab == TAB_ANIMATION:
            if self._handle_animation_input(event, enabled):
                return None

        # Collision tab widgets.
        if self.active_tab == TAB_COLLISION:
            if self._handle_collision_input(event, enabled):
                return None

        # Global field / slider input on visible controls.
        if self.active_tab == TAB_ANIMATION:
            changed = False
            for field in (self.label_input, self.frame_w_input, self.frame_h_input,
                          self.frame_count_input, self.rows_input, self.variant_names_input):
                changed |= field.handle_event(event)
            changed |= self.fps_slider.handle_event(event, enabled)
            if changed:
                self._read_numeric_fields()
        elif self.active_tab == TAB_COLLISION and self._collision_enabled():
            changed = False
            for field in self.collision_rect_input.values():
                changed |= field.handle_event(event)
            if changed:
                self._apply_collision_field_values()

        return None

    def _apply_collision_field_values(self) -> None:
        """Read x/y/w/h from the collision text fields and commit them."""
        try:
            r = tuple(int(self.collision_rect_input[k].value) for k in ("x", "y", "w", "h"))
        except ValueError:
            return
        if r[2] > 0 and r[3] > 0:
            self._set_manual_collision(r)

    def _handle_preview_input(self, event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            rect = getattr(self, "_preview_play_rect", None)
            if rect is not None and rect.collidepoint(event.pos):
                self.preview_running = not self.preview_running
                return True
        return False

    def _handle_animation_input(self, event, enabled) -> bool:
        if event.type == pygame.MOUSEWHEEL:
            strip = self._sequence_strip_rect
            if strip is not None and strip.collidepoint(pygame.mouse.get_pos()):
                self._scroll_sequence(-event.y)
                return True
            return False

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if not self._editor_rect_contains(mx, my):
                return False

            # Sequence-strip scroll arrows.
            if self._sequence_scroll_left_rect and self._sequence_scroll_left_rect.collidepoint(mx, my):
                self._scroll_sequence(-1)
                return True
            if self._sequence_scroll_right_rect and self._sequence_scroll_right_rect.collidepoint(mx, my):
                self._scroll_sequence(1)
                return True

            # Clickable frame boxes.
            hit = self._animation_frame_hit((mx, my))
            if hit is not None and enabled:
                self._append_sequence_frame(hit)
                return True

            # Sequence buttons.
            for rr, seq_idx in self._sequence_rects:
                if rr.collidepoint(mx, my):
                    if enabled and 0 <= seq_idx < len(self.sequence):
                        self.sequence.pop(seq_idx)
                        if not self.sequence:
                            self.sequence = [1]
                        self.preview_seq_index = min(self.preview_seq_index, len(self.sequence) - 1)
                        self._set_style_dirty()
                    return True

            for name, rr in self._animation_button_rects.items():
                if rr.collidepoint(mx, my):
                    if name == "clear":
                        self._clear_sequence()
                    elif name == "all":
                        self._all_sequence()
                    elif name == "ping":
                        self._ping_pong_sequence()
                    elif name == "reverse" and enabled:
                        self.sequence.reverse()
                        self.preview_seq_index = 0
                        self._set_style_dirty()
                    elif name == "play":
                        self.preview_running = not self.preview_running
                    return True
        return False

    def _scroll_sequence(self, delta: int) -> None:
        max_scroll = max(0, len(self.sequence) - 1)
        self.sequence_scroll = max(0, min(max_scroll, self.sequence_scroll + delta))

    def _handle_collision_input(self, event, enabled) -> bool:
        enabled = self._collision_enabled()
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if self._collision_auto_button.collidepoint(mx, my):
                self._use_auto_collision()
                return True
            if self._collision_manual_button.collidepoint(mx, my):
                current = self.collision_rect or self.collision_auto_rect
                if current:
                    self._set_manual_collision(tuple(current))
                return True
            if enabled and self._collision_canvas_rect.collidepoint(mx, my):
                self._begin_collision_drag((mx, my))
                return True

        if event.type == pygame.MOUSEMOTION and self.collision_drag:
            self._update_collision_drag(event.pos)
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.collision_drag:
            self.collision_drag = None
            return True
        return False

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------
    def update(self, dt: float) -> None:
        if not self.active:
            return
        self.status_timer = max(0.0, self.status_timer - float(dt))

        if self.preview_running and self.sequence and self.fps > 0:
            self.anim_timer += float(dt)
            step = 1.0 / max(0.01, self.fps)
            while self.anim_timer >= step:
                self.anim_timer -= step
                self.preview_seq_index = (self.preview_seq_index + 1) % len(self.sequence)

    # ------------------------------------------------------------------
    # Geometry used by draw + hit testing.
    # ------------------------------------------------------------------
    def _editor_rect_contains(self, x, y) -> bool:
        return self.editor_rect.collidepoint(x, y)

    def _animation_source_rect(self) -> pygame.Rect:
        return pygame.Rect(self.editor_rect.x + 16,
                           self.editor_rect.y + 105,
                           max(1, int(self.editor_rect.w * 0.57)),
                           max(1, self.editor_rect.h - 265))

    def _collision_source_rect(self) -> pygame.Rect:
        return pygame.Rect(self.editor_rect.x + 16,
                           self.editor_rect.y + 20,
                           max(1, int(self.editor_rect.w * 0.62)),
                           max(1, self.editor_rect.h - 42))

    # ------------------------------------------------------------------
    # Draw
    # ------------------------------------------------------------------
    def draw(self, screen: pygame.Surface, dt: float = 0.0) -> None:
        if not self.active:
            return

        sw, sh = self.screen_width, self.screen_height
        mouse = pygame.mouse.get_pos()
        enabled = self._editor_enabled()
        save_enabled = self.current_item is not None

        screen.fill(C_BG)

        # Header.
        header = pygame.Rect(0, 0, sw, HEADER_H)
        screen.draw_rect(C_PANEL, header)
        screen.draw_line(C_BORDER, (0, HEADER_H - 1), (sw, HEADER_H - 1))
        text(screen, "DECORATION CREATOR", (16, 12), C_TEXT, 20, True)
        hint = "ESC to close  •  Ctrl+S to save"
        text(screen, hint, (sw - get_font(12).size(hint)[0] - 16, 14), C_TEXT_DIM, 12)

        # Footer.
        footer = pygame.Rect(0, sh - FOOTER_H, sw, FOOTER_H)
        screen.draw_rect(C_PANEL, footer)
        screen.draw_line(C_BORDER, (0, sh - FOOTER_H), (sw, sh - FOOTER_H))
        if self.status_timer > 0:
            text(screen, self.status_msg, (LIST_W + PAD * 2, sh - FOOTER_H + 17),
                 self.status_col, 12)

        button(screen, self.btn_save, "Save  ✓",
               hover=self.btn_save.collidepoint(*mouse),
               disabled=not save_enabled)
        button(screen, self.btn_refresh, "Refresh",
               hover=self.btn_refresh.collidepoint(*mouse))

        if self.dirty:
            dot = "● unsaved"
            text(screen, dot, (self.btn_save.x - 75, self.btn_save.y + 9), C_ACCENT2, 11)

        # Left list + mini preview.
        self.roster.draw(screen, mouse)
        self._draw_sidebar_preview(screen)

        # Main panel and tabs.
        editor_outer = self.editor_rect.inflate(0, TAB_H)
        panel(screen, editor_outer, C_PANEL)
        for i, (name, rr) in enumerate(zip(TAB_NAMES, self.tab_rects)):
            active = i == self.active_tab
            bg = C_SELECTED if active else C_PANEL_DARK
            border = C_ACCENT if active else C_BORDER
            screen.draw_rect(bg, rr, border_radius=6 if i in (0, len(TAB_NAMES) - 1) else 0)
            screen.draw_rect(border, rr, 1,
                             border_radius=6 if i in (0, len(TAB_NAMES) - 1) else 0)
            img = get_font(13, active).render(name, True, C_TEXT if active else C_TEXT_DIM)
            screen.blit(img, img.get_rect(center=rr.center))

        if self.active_tab == TAB_PREVIEW:
            self._draw_preview_tab(screen, enabled)
        elif self.active_tab == TAB_ANIMATION:
            self._draw_animation_tab(screen, enabled)
        elif self.active_tab == TAB_COLLISION:
            self._draw_collision_tab(screen, self._collision_enabled())

        if self.current_item and self.current_item.get("hardcoded"):
            lock = pygame.Rect(self.editor_rect.right - 220,
                               self.editor_rect.y + 50, 200, 30)
            screen.draw_rect((38, 30, 18), lock, border_radius=5)
            screen.draw_rect(C_ACCENT2, lock, 1, border_radius=5)
            label = "BUILT-IN • ANIMATION PROTECTED"
            img = get_font(11, True).render(label, True, C_ACCENT2)
            screen.blit(img, img.get_rect(center=lock.center))

    # ------------------------------------------------------------------
    # Draw: sidebar preview
    # ------------------------------------------------------------------
    def _draw_sidebar_preview(self, screen: pygame.Surface) -> None:
        panel(screen, self.preview_rect)
        text(screen, "LIVE PREVIEW", (self.preview_rect.x + 12, self.preview_rect.y + 10),
             C_TEXT_DIM, 12, True)

        frame = self._current_frame()
        if frame is None:
            text(screen, "No image", (self.preview_rect.centerx - 30, self.preview_rect.centery),
                 C_TEXT_DIM, 12)
            return

        box = self.preview_rect.inflate(-20, -45)
        fw, fh = frame.get_size()
        scale = min(box.w / max(1, fw), box.h / max(1, fh))
        scale = max(1.0, min(scale, 5.0))
        dw = max(1, int(fw * scale))
        dh = max(1, int(fh * scale))
        img = pygame.transform.scale(frame, (dw, dh))
        screen.blit(img, img.get_rect(center=(box.centerx, box.centery + 8)))
        screen.draw_line(C_ACCENT2,
                         (box.centerx - 8, box.bottom - 2),
                         (box.centerx + 8, box.bottom - 2), 1)

    # ------------------------------------------------------------------
    # Draw: preview tab
    # ------------------------------------------------------------------
    def _draw_preview_tab(self, screen: pygame.Surface, enabled: bool) -> None:
        if not self.current_item:
            text(screen, "No decorations found under assets/objects/decorations/.",
                 (self.editor_rect.x + 20, self.editor_rect.y + 60), C_TEXT_DIM, 14)
            return

        text(screen, self.label_input.value or pretty_id(self.selected_id or "Decoration"),
             (self.editor_rect.x + 20, self.editor_rect.y + 18), C_TEXT, 18, True)
        text(screen, self.selected_id or "", (self.editor_rect.x + 20, self.editor_rect.y + 44),
             C_TEXT_DIM, 12)

        canvas = pygame.Rect(self.editor_rect.x + 16, self.editor_rect.y + 75,
                             self.editor_rect.w - 32, self.editor_rect.h - 135)
        panel(screen, canvas, C_CANVAS, C_BORDER, 6)

        frame = self._current_frame()
        if frame is None:
            text(screen, "No image / sprite sheet", (canvas.centerx - 60, canvas.centery), C_TEXT_DIM, 13)
        else:
            fw, fh = frame.get_size()
            scale = min((canvas.w - 40) / max(1, fw), (canvas.h - 40) / max(1, fh))
            scale = max(1.0, min(scale, 8.0))
            dw, dh = max(1, int(fw * scale)), max(1, int(fh * scale))
            img = pygame.transform.scale(frame, (dw, dh))
            rr = img.get_rect(center=canvas.center)
            screen.blit(img, rr)

            # Anchor marker / ground line.
            screen.draw_line(C_ACCENT2,
                             (rr.left, rr.bottom), (rr.right, rr.bottom), 1)
            screen.draw_line(C_ACCENT2,
                             (rr.centerx - 6, rr.bottom), (rr.centerx + 6, rr.bottom), 2)
            screen.draw_line(C_ACCENT2,
                             (rr.centerx, rr.bottom - 6), (rr.centerx, rr.bottom), 2)

        # Playback controls.
        play_rect = pygame.Rect(self.editor_rect.x + 20, self.editor_rect.bottom - 48, 105, 30)
        self._preview_play_rect = play_rect
        button(screen, play_rect, "Pause" if self.preview_running else "Play",
               hover=play_rect.collidepoint(*pygame.mouse.get_pos()))
        text(screen, f"Sequence: {len(self.sequence)} steps",
             (play_rect.right + 15, play_rect.y + 7), C_TEXT_DIM, 12)
        text(screen, f"FPS: {self.fps:g}",
             (play_rect.right + 120, play_rect.y + 7), C_TEXT_DIM, 12)

    # ------------------------------------------------------------------
    # Draw: animation tab
    # ------------------------------------------------------------------
    def _draw_animation_tab(self, screen: pygame.Surface, enabled: bool) -> None:
        if not self.current_item:
            return

        text(screen, "Spritesheet & Animation", (self.editor_rect.x + 16, self.editor_rect.y + 14),
             C_TEXT, 18, True)
        text(screen, "Click frames below in the order they should play.",
             (self.editor_rect.x + 16, self.editor_rect.y + 41), C_TEXT_DIM, 12)

        # Source sheet canvas.
        source = self._animation_source_rect()
        self._draw_source_sheet(screen, source, enabled)

        # Control column.
        x = source.right + 18
        y = self.editor_rect.y + 82
        w = self.editor_rect.right - x - 16
        text(screen, "CONFIGURATION", (x, y - 25), C_TEXT_DIM, 11, True)

        label_y = y
        text(screen, "Display name", (x, label_y), C_TEXT_DIM, 11)
        self.label_input.rect = pygame.Rect(x, label_y + 16, w, 30)
        self.label_input.draw(screen, enabled)
        y = self.label_input.rect.bottom + 12

        # 2x2 numeric layout.
        specs = [
            ("Frame W", self.frame_w_input),
            ("Frame H", self.frame_h_input),
            ("Frame count", self.frame_count_input),
            ("Variant rows", self.rows_input),
        ]
        for idx, (lbl, field) in enumerate(specs):
            col = idx % 2
            row = idx // 2
            fx = x + col * (w // 2 + 6)
            fy = y + row * 58
            text(screen, lbl, (fx, fy), C_TEXT_DIM, 11)
            field.rect = pygame.Rect(fx, fy + 16, max(60, w // 2 - 8), 30)
            field.draw(screen, enabled)
        y += 116

        text(screen, "Variant names", (x, y), C_TEXT_DIM, 11)
        self.variant_names_input.rect = pygame.Rect(x, y + 16, w, 30)
        self.variant_names_input.draw(screen, enabled)
        y = self.variant_names_input.rect.bottom + 18

        text(screen, "Animation speed", (x, y), C_TEXT_DIM, 11)
        self.fps_slider.rect = pygame.Rect(x, y + 18, max(120, w - 55), 22)
        self.fps_slider.draw(screen, enabled)
        text(screen, f"{self.fps_slider.value:g} fps",
             (self.fps_slider.rect.right + 8, self.fps_slider.rect.y + 2), C_TEXT, 12)

        # Sequence strip under source — scrollable when it holds more steps
        # than fit in the available width, instead of truncating with a
        # "+ N more" label (which used to collide with the action buttons).
        seq_y = source.bottom + 16
        text(screen, "PLAYBACK SEQUENCE", (source.x, seq_y), C_TEXT_DIM, 11, True)
        seq_y += 19
        self._sequence_rects = []
        cell_w = 34
        cell_h = 30
        arrow_w = 20
        strip_rect = pygame.Rect(source.x, seq_y, source.w, cell_h)

        max_cells = max(1, (strip_rect.w - 2 * (arrow_w + 4)) // (cell_w + 4))
        overflow = len(self.sequence) > max_cells
        self.sequence_scroll = max(0, min(self.sequence_scroll,
                                          max(0, len(self.sequence) - max_cells)))

        inner_x = strip_rect.x
        inner_w = strip_rect.w
        if overflow:
            inner_x += arrow_w + 4
            inner_w -= 2 * (arrow_w + 4)

        self._sequence_strip_rect = strip_rect
        self._sequence_scroll_left_rect = None
        self._sequence_scroll_right_rect = None

        if overflow:
            left_rect = pygame.Rect(strip_rect.x, seq_y, arrow_w, cell_h)
            right_rect = pygame.Rect(strip_rect.right - arrow_w, seq_y, arrow_w, cell_h)
            self._sequence_scroll_left_rect = left_rect
            self._sequence_scroll_right_rect = right_rect
            button(screen, left_rect, "<", hover=left_rect.collidepoint(*pygame.mouse.get_pos()),
                   disabled=(self.sequence_scroll <= 0), small=True)
            button(screen, right_rect, ">", hover=right_rect.collidepoint(*pygame.mouse.get_pos()),
                   disabled=(self.sequence_scroll >= len(self.sequence) - max_cells), small=True)

        prev_clip = screen.get_clip()
        screen.set_clip(pygame.Rect(inner_x, seq_y, inner_w, cell_h))
        visible = self.sequence[self.sequence_scroll:self.sequence_scroll + max_cells]
        for i, frame_no in enumerate(visible):
            seq_idx = self.sequence_scroll + i
            rr = pygame.Rect(inner_x + i * (cell_w + 4), seq_y, cell_w, cell_h)
            active = seq_idx == self.preview_seq_index
            screen.draw_rect(C_SELECTED if active else C_PANEL_DARK, rr, border_radius=4)
            screen.draw_rect(C_ACCENT if active else C_BORDER, rr, 1, border_radius=4)
            img = get_font(12, True).render(str(frame_no), True, C_TEXT)
            screen.blit(img, img.get_rect(center=rr.center))
            self._sequence_rects.append((rr, seq_idx))
        screen.set_clip(prev_clip)

        if overflow:
            text(screen, f"{self.sequence_scroll + 1}-{self.sequence_scroll + len(visible)} of {len(self.sequence)}",
                 (strip_rect.x, seq_y + cell_h + 4), C_TEXT_DIM, 10)

        # Action buttons — own row, so they never compete with the strip
        # above for width.
        button_row_y = seq_y + cell_h + (18 if overflow else 4)
        self._animation_button_rects = {}
        bx = source.x
        for name, label in (("all", "Use all"), ("ping", "Ping-pong"),
                            ("reverse", "Reverse"), ("clear", "Clear"),
                            ("play", "Play/Pause")):
            rr = pygame.Rect(bx, button_row_y, 58 if name != "ping" else 74, 30)
            self._animation_button_rects[name] = rr
            button(screen, rr, label, hover=rr.collidepoint(*pygame.mouse.get_pos()),
                   disabled=(not enabled and name != "play"), small=True)
            bx += rr.w + 4

        if not enabled:
            text(screen, "Built-in animation is protected. Collision can still be edited.",
                 (source.x, self.editor_rect.bottom - 18), C_ACCENT2, 10, True)

    def _draw_source_sheet(self, screen: pygame.Surface, rect: pygame.Rect, enabled: bool) -> None:
        panel(screen, rect, C_CANVAS, C_BORDER)
        if self.sheet is None:
            text(screen, "No sprite sheet loaded.", (rect.x + 15, rect.y + 15), C_TEXT_DIM, 13)
            return

        sw, sh = self.sheet.get_size()
        scale = min(rect.w / max(1, sw), rect.h / max(1, sh))
        scale = max(0.05, min(scale, 8.0))
        dw, dh = max(1, int(sw * scale)), max(1, int(sh * scale))
        img = pygame.transform.scale(self.sheet, (dw, dh))
        ox = rect.x + (rect.w - dw) // 2
        oy = rect.y + (rect.h - dh) // 2
        screen.blit(img, (ox, oy))

        # Grid + frame numbers.
        for row in range(self.grid_rows):
            for col in range(self.frame_count):
                fr = pygame.Rect(
                    int(ox + col * self.frame_w * scale),
                    int(oy + row * self.frame_h * scale),
                    max(1, int(self.frame_w * scale)),
                    max(1, int(self.frame_h * scale)),
                )
                border = C_ACCENT if row == self.selected_variant else C_BORDER
                screen.draw_rect(border, fr, 1)
                if fr.w >= 20 and fr.h >= 16:
                    img_n = get_font(10, True).render(str(col + 1), True, border)
                    screen.blit(img_n, (fr.x + 3, fr.y + 2))

        help_txt = "Click a frame → append it to the sequence"
        text(screen, help_txt, (rect.x + 10, rect.bottom - 20), C_TEXT_DIM, 10)

    # ------------------------------------------------------------------
    # Draw: collision tab
    # ------------------------------------------------------------------
    def _draw_collision_tab(self, screen: pygame.Surface, enabled: bool) -> None:
        if not self.current_item:
            return

        text(screen, "Collision Box", (self.editor_rect.x + 16, self.editor_rect.y + 14),
             C_TEXT, 18, True)
        text(screen,
             "Drag the box to move it. Drag a corner handle to resize it.",
             (self.editor_rect.x + 16, self.editor_rect.y + 41), C_TEXT_DIM, 12)

        canvas = self._collision_source_rect()
        self._collision_canvas_rect = canvas
        panel(screen, canvas, C_CANVAS, C_BORDER)

        frame_rect, scale, frame = self._collision_canvas()
        if frame_rect is not None and frame is not None:
            # Pixel-art nearest-neighbour preview.
            img = pygame.transform.scale(frame, frame_rect.size)
            screen.blit(img, frame_rect)

            # Sprite boundary.
            screen.draw_rect(C_BORDER, frame_rect, 1)

            # Base line + anchor.
            screen.draw_line(C_ACCENT2,
                             (frame_rect.left, frame_rect.bottom),
                             (frame_rect.right, frame_rect.bottom), 1)
            screen.draw_circle(C_ACCENT2,
                               (frame_rect.centerx, frame_rect.bottom), 3)

            cr = self._collision_rect_screen()
            if cr:
                fill = pygame.Surface(cr.size, pygame.SRCALPHA)
                fill.fill(C_COLLISION_FILL)
                screen.blit(fill, cr.topleft)
                screen.draw_rect(C_COLLISION, cr, 2)
                for cx, cy in ((cr.left, cr.top), (cr.right, cr.top),
                               (cr.left, cr.bottom), (cr.right, cr.bottom)):
                    h = pygame.Rect(cx - 4, cy - 4, 8, 8)
                    screen.draw_rect(C_COLLISION, h)
                    screen.draw_rect(C_BG, h, 1)
                mode_text = "MANUAL" if self.collision_manual else "AUTO"
                text(screen, mode_text, (cr.x + 4, cr.y + 4), C_TEXT, 10, True)

        # Right controls.
        x = canvas.right + 18
        y = self.editor_rect.y + 20
        w = self.editor_rect.right - x - 16
        text(screen, "COLLISION MODE", (x, y), C_TEXT_DIM, 11, True)
        y += 18
        self._collision_auto_button = pygame.Rect(x, y, w, 34)
        self._collision_manual_button = pygame.Rect(x, y + 42, w, 34)
        button(screen, self._collision_auto_button, "Use automatic collision",
               hover=self._collision_auto_button.collidepoint(*pygame.mouse.get_pos()),
               disabled=not enabled)
        button(screen, self._collision_manual_button, "Edit collision manually",
               hover=self._collision_manual_button.collidepoint(*pygame.mouse.get_pos()),
               disabled=not enabled)
        y += 92

        r = self.collision_rect if self.collision_manual and self.collision_rect else self.collision_auto_rect
        if r is None:
            text(screen, "No collision", (x, y), C_TEXT_DIM, 12)
        else:
            labels = (("X", "x"), ("Y", "y"), ("W", "w"), ("H", "h"))
            col_w = max(64, (w - 12) // 2)
            for i, (lbl, key) in enumerate(labels):
                col = i % 2
                row = i // 2
                fx = x + col * (col_w + 12)
                fy = y + row * 60
                text(screen, lbl, (fx, fy), C_TEXT_DIM, 11)
                field = self.collision_rect_input[key]
                field.rect = pygame.Rect(fx, fy + 16, col_w, 30)
                field.draw(screen, enabled)

        y += 140
        if self.collision_auto_rect:
            text(screen,
                 f"Auto: {self.collision_auto_rect[0]}, {self.collision_auto_rect[1]}, "
                 f"{self.collision_auto_rect[2]} × {self.collision_auto_rect[3]}",
                 (x, y), C_TEXT_DIM, 11)
        text(screen,
             "Collision coordinates are local to the frame.",
             (x, self.editor_rect.bottom - 48), C_TEXT_DIM, 10)
        text(screen,
             "Only the compact box blocks movement / beams.",
             (x, self.editor_rect.bottom - 31), C_TEXT_DIM, 10)


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
            if result == "close":
                running = False
        creator.update(dt)
        creator.draw(screen, dt)
        pygame.display.flip()


__all__ = ["DecorationCreator", "run"]