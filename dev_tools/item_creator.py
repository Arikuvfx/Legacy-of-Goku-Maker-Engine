"""
item_creator.py  –  Dev-menu item browser/editor
==================================================
Sister tool to character_creator.py / entity_creator.py. Where those define
player characters and enemies/NPCs, this defines consumable *items* —
supplies, story items, and equipment (body/hands/feet/accessory) — id,
name, description, category, and effect. Every item already in
core/items.py's hardcoded ITEMS table shows up here; new items created in
this tool, and edits to existing ones, are written to
assets/items/{item_id}.json rather than touching core/items.py's source,
and merged into the live ITEMS dict on both save (immediate, this session)
and next launch (core/items.py's own JSON-overlay merge — see that file).

Wire-up (game.py), mirrors character_creator's / entity_creator's:
    from dev_tools import item_creator
    self.item_creator = item_creator.ItemCreator(SCREEN_WIDTH, SCREEN_HEIGHT)
    # in the event loop, same pattern as self.entity_creator:
    if self.item_creator.active:
        result = self.item_creator.handle_input(event)
        if result == 'back_to_dev_menu':
            # Back-arrow / ESC — the Dev Menu closed itself when it
            # launched this creator, so reopen it instead of dropping
            # all the way back into gameplay.
            self.dev_menu.open()
    ...
    self.item_creator.update(dt)
    self.item_creator.draw(self.logical_surface, dt)
    # from DevMenu, add a 'open_item_creator' result -> self.item_creator.toggle()

This tool does NOT touch item sprite art — it only shows whatever icon
already exists at item_icon_path(item_id) (assets/sprites/items/{id}.png,
.../equipment/{slot}/{id}.png for equip items, or .../story_items/{id}.png
for story items) as a preview, same "discover art, attach data" split
entity_creator.py uses for enemy/NPC sprites. Dropping in a new icon PNG
separately is still up to you; this tool is purely the data side
(name/description/category/effect).

VISUAL LANGUAGE — rebuilt from scratch on dev_tools/ui_kit.py, matching
DevMenu and RoomEditor's "modern DBZ" look (deep navy background, gold/
ki-blue accents, dark hairline-bordered cards, header/footer chrome, inline
field-row text editing with a live caret). None of the old widget classes
(TextInput/TextArea/Slider/draw_button/legacy_widgets colour constants) are
used any more — every visual here is built directly on ui_kit primitives
and this file's own drawing helpers, following the same construction
RoomEditor.py uses for its own forms (card shells, pill buttons, field
rows, sliders, dropdown lists). The underlying data model, save/load/
delete/create behaviour, category and effect-type tables, and effect
field logic are unchanged from the previous version of this file.
"""

from __future__ import annotations

import copy
import math
import os
import sys
from pathlib import Path
from typing import Optional

import pygame

import dev_tools.ui_kit as uk

from core.items import (
    ITEMS,
    CATEGORY_SUPPLIES, CATEGORY_STORY_ITEMS,
    CATEGORY_EQUIP_BODY, CATEGORY_EQUIP_HANDS,
    CATEGORY_EQUIP_FEET, CATEGORY_EQUIP_ACCESSORY,
    item_icon_path, save_item_override, revert_item_override,
    discover_custom_item_ids,
)

# ──────────────────────────────────────────────────────────────────────
#  Paths — same BASE_DIR anchoring trick as character_creator.py /
#  entity_creator.py, so icon previews resolve correctly once packaged.
# ──────────────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

# ── Category catalogue — (category value, display label, equip slot) ──
# slot is None for non-equip categories; for equip categories it's the
# fixed slot string core/items.py's equip_item()/unequip_item() key off
# of, so it isn't a free-text field in the editor — picking the category
# picks the slot.
CATEGORIES = [
    (CATEGORY_SUPPLIES,        "Supplies",         None),
    (CATEGORY_STORY_ITEMS,     "Story Items",       None),
    (CATEGORY_EQUIP_BODY,      "Equip: Body",       "body"),
    (CATEGORY_EQUIP_HANDS,     "Equip: Hands",      "hands"),
    (CATEGORY_EQUIP_FEET,      "Equip: Feet",       "feet"),
    (CATEGORY_EQUIP_ACCESSORY, "Equip: Accessory",  "accessory"),
]
CATEGORY_LABELS = {cat: label for cat, label, _ in CATEGORIES}
CATEGORY_SLOT   = {cat: slot for cat, _, slot in CATEGORIES}

# Per-category accent colour, used for the list's identity dot and the
# category picker's selected-state glow — same "colored dot = identity"
# idea RoomEditor uses (there, hashed per group; here, fixed per category
# since there are only six and they're meaningful, not arbitrary).
CATEGORY_ACCENT = {
    CATEGORY_SUPPLIES:        uk.Theme.GOLD,
    CATEGORY_STORY_ITEMS:     uk.Theme.KI_BLUE,
    CATEGORY_EQUIP_BODY:      (167, 139, 250),
    CATEGORY_EQUIP_HANDS:     (240, 146, 92),
    CATEGORY_EQUIP_FEET:      (94, 210, 148),
    CATEGORY_EQUIP_ACCESSORY: (235, 110, 150),
}

# ── Effect types (see systems/item_effects.py for how each is applied).
# 'none' isn't a real effect type item_effects.py knows about — it's this
# editor's way of representing "no effect" (story items, key items, etc.),
# and is simply omitted from the saved item's 'effect' dict entirely. ──
EFFECT_TYPES = ["heal_hp", "heal_ep", "full_restore", "revive", "buff", "equip_stat", "none"]
EFFECT_LABELS = {
    "heal_hp":      "Heal HP",
    "heal_ep":      "Heal EP",
    "full_restore": "Full Restore",
    "revive":       "Revive",
    "buff":         "Timed Buff",
    "equip_stat":   "Equip Bonus",
    "none":         "No Effect",
}

# Stat ids buff/equip_stat effects key off of — mirrors item_effects.py's
# _STAT_KEY_CANDIDATES canonical ids exactly.
STAT_IDS = ["strength", "ki_power", "vitality", "speed"]
STAT_LABELS = {"strength": "STR", "ki_power": "POW", "vitality": "END", "speed": "SPD"}


def _default_effect_for_type(etype: str, old_effect: dict) -> dict:
    """Build a fresh effect dict for *etype*, carrying over any fields
    *old_effect* already has that are still meaningful (e.g. switching
    Heal HP -> Heal EP keeps the amount; switching Timed Buff -> Equip
    Bonus keeps the stat amounts) — same "don't discard compatible data
    on a type switch" spirit as entity_creator's enemy_category toggle
    keeping shooter_style around even while it's hidden."""
    old_effect = old_effect or {}
    if etype == "heal_hp":
        return {"type": "heal_hp", "amount": old_effect.get("amount", 20)}
    if etype == "heal_ep":
        return {"type": "heal_ep", "amount": old_effect.get("amount", 20)}
    if etype == "full_restore":
        return {"type": "full_restore"}
    if etype == "revive":
        return {"type": "revive", "hp_ratio": old_effect.get("hp_ratio", 0.5)}
    if etype == "buff":
        old_stats = old_effect.get("stats", {})
        return {
            "type": "buff",
            "duration": old_effect.get("duration", 30.0),
            "stats": {sid: old_stats.get(sid, 0) for sid in STAT_IDS},
        }
    if etype == "equip_stat":
        old_stats = old_effect.get("stats", {})
        out = {
            "type": "equip_stat",
            "stats": {sid: old_stats.get(sid, 0) for sid in STAT_IDS},
        }
        if old_effect.get("exp_bonus"):
            out["exp_bonus"] = old_effect["exp_bonus"]
        return out
    return {}  # 'none' — omitted from the saved item entirely, see _do_save()


def _new_item_data(item_id: str) -> dict:
    return {
        "name": item_id.replace("_", " ").title(),
        "description": "",
        "effect_text": "",
        "category": CATEGORY_SUPPLIES,
        "effect": {"type": "heal_hp", "amount": 20},
    }


def _load_item_data(item_id: str) -> dict:
    """Return a working copy of item_id's current data (deep-copied so
    edits don't mutate the live ITEMS dict until Save), or a fresh
    skeleton if it doesn't exist yet."""
    if item_id in ITEMS:
        data = copy.deepcopy(ITEMS[item_id])
        data.setdefault("name", item_id.replace("_", " ").title())
        data.setdefault("description", "")
        data.setdefault("effect_text", "")
        data.setdefault("category", CATEGORY_SUPPLIES)
        data.setdefault("effect", {"type": "none"})
        data["effect"].setdefault("type", "none")
        return data
    return _new_item_data(item_id)


def discover_all_item_ids() -> list[str]:
    """Every item currently in the live ITEMS dict (hardcoded table +
    JSON overlay already merged by core/items.py), alphabetical."""
    return sorted(ITEMS.keys())


def _load_icon(item_id: str, size: int = 96) -> Optional[pygame.Surface]:
    """Preview icon at item_icon_path(item_id), scaled to fit inside a
    (size, size) box. None if no art exists yet — the editor shows a
    placeholder box rather than blocking on missing art, same as
    entity_creator's sprite preview.

    Item art is small pixel-art sprites, so this follows the same
    crop-to-content + aspect-preserving + nearest-neighbour-style scale
    _load_dev_menu_icon() uses for the UI's own icons, instead of
    smoothscale directly to (size, size): smoothscale's bilinear
    filtering blurs pixel art on upscale, and stretching straight to a
    square box distorts any icon that isn't already square."""
    path = BASE_DIR / item_icon_path(item_id)
    if not path.is_file():
        return None
    try:
        raw = pygame.image.load(str(path)).convert_alpha()
    except Exception as e:
        print(f"Error loading item icon ({path}): {e}")
        return None

    content_rect = raw.get_bounding_rect(min_alpha=1)
    if content_rect.width <= 0 or content_rect.height <= 0:
        content_rect = raw.get_rect()
    raw = raw.subsurface(content_rect).copy()

    iw, ih = raw.get_size()
    scale = min(size / max(1, iw), size / max(1, ih))
    nw = max(1, round(iw * scale))
    nh = max(1, round(ih * scale))
    if scale >= 1.0:
        # Crisp integer-ish upscale: blow up with nearest-neighbour scale
        # first, then settle to the exact target size, instead of a
        # single smoothscale that would soften pixel-art edges.
        prescale = max(1, math.ceil(scale) * 2)
        big = pygame.transform.scale(raw, (iw * prescale, ih * prescale))
        scaled = pygame.transform.scale(big, (nw, nh))
    else:
        scaled = pygame.transform.smoothscale(raw, (nw, nh))

    canvas = pygame.Surface((size, size), pygame.SRCALPHA)
    canvas.blit(scaled, ((size - nw) // 2, (size - nh) // 2))
    return canvas


# =============================================================================
# Small vector icon glyphs — same fn(surface, rect, color) shape as
# ui_kit's own DEV_MENU_ICON_DRAWERS / RoomEditor's _draw_plus_icon family,
# so these read as part of the same icon set rather than a one-off.
# =============================================================================

def _draw_plus_icon(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.34
    uk.draw_line_on(surface, color, (cx - s, cy), (cx + s, cy), width)
    uk.draw_line_on(surface, color, (cx, cy - s), (cx, cy + s), width)


def _draw_x_icon(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.28
    uk.draw_line_on(surface, color, (cx - s, cy - s), (cx + s, cy + s), width)
    uk.draw_line_on(surface, color, (cx - s, cy + s), (cx + s, cy - s), width)


def _draw_check_icon(surface, rect, color, width=3):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.32
    uk.draw_line_on(surface, color, (cx - s, cy), (cx - s * 0.15, cy + s * 0.8), width)
    uk.draw_line_on(surface, color, (cx - s * 0.15, cy + s * 0.8), (cx + s, cy - s * 0.7), width)


def _draw_trash_icon(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    body = pygame.Rect(0, 0, s * 0.46, s * 0.48)
    body.centerx = cx
    body.top = int(cy - s * 0.08)
    uk.draw_rect_on(surface, color, body, width, 2)
    lid = pygame.Rect(0, 0, s * 0.62, s * 0.09)
    lid.centerx = cx
    lid.bottom = body.top + 1
    uk.draw_rect_on(surface, color, lid, width, 1)
    handle = pygame.Rect(0, 0, s * 0.22, s * 0.12)
    handle.centerx = cx
    handle.bottom = lid.top + 2
    uk.draw_rect_on(surface, color, handle, width, 2)
    for i in (-1, 0, 1):
        x = cx + i * s * 0.13
        uk.draw_line_on(surface, color, (x, body.top + 5), (x, body.bottom - 4), width)


def _draw_star_icon(surface, rect, color):
    """Small filled 4-point sparkle — marks an item as a custom / user
    override in the list (in place of the old trailing ' *' in the label)."""
    cx, cy = rect.center
    r = min(rect.w, rect.h) * 0.5
    pts = [(cx, cy - r), (cx + r * 0.28, cy - r * 0.28), (cx + r, cy),
           (cx + r * 0.28, cy + r * 0.28), (cx, cy + r), (cx - r * 0.28, cy + r * 0.28),
           (cx - r, cy), (cx - r * 0.28, cy - r * 0.28)]
    pygame.draw.polygon(surface, color, pts)


def _draw_chevron_down(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h) * 0.22
    uk.draw_line_on(surface, color, (cx - s, cy - s * 0.4), (cx, cy + s * 0.6), width)
    uk.draw_line_on(surface, color, (cx, cy + s * 0.6), (cx + s, cy - s * 0.4), width)


class _BitmapFontView:
    """Adapts a BitmapFont to the plain pygame.font.Font call shape —
    render(text, antialias, color) / size(text) — at one fixed pixel
    height. Same adapter DevMenu.py / RoomEditor.py use for their own
    bitmap fonts, so every `self.font_x.render(text, True, color)` call
    site below behaves like a normal pygame Font."""

    def __init__(self, bitmap_font, height):
        self._font = bitmap_font
        self._height = height

    def render(self, text, antialias=True, color=(255, 255, 255)):
        return self._font.render(text, color=color, height=self._height)

    def size(self, text):
        return self._font.size(text, height=self._height)


# =============================================================================
# Layout constants
# =============================================================================
LIST_W = 320
CARD_H = 60
GRID_BTN_H = 30
GRID_ROW_H = 40
SLIDER_H = 40
FIELD_H = 40
SECTION_GAP = 22

# Per-field max input length — description gets a lot more room than a
# short name or one-line effect blurb.
_FIELD_MAX_LEN = {
    "name": 40,
    "effect_text": 64,
    "description": 600,
    "new_item_id": 40,
}


class ItemCreator:
    """In-game item browser/editor. Same toggle()/handle_input(event)/
    update(dt)/draw(surface, dt) lifecycle as CharacterCreator/EntityCreator/
    RoomEditor, so game.py's wiring doesn't change."""

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width = int(screen_width)
        self.screen_height = int(screen_height)
        self.active = False
        self._logical_mouse_pos = (screen_width // 2, screen_height // 2)

        # Same bitmap-font family / split as DevMenu and RoomEditor: title
        # text uses the plain uppercase/lowercase glyph set, everything
        # else uses the menu glyph set.
        self._menu_font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self._title_bitmap_font = uk.BitmapFont('assets\\ui\\fonts', letter_spacing=1)
        self._title_bitmap_font.uppercase_dir = os.path.join('assets', 'ui', 'fonts', 'uppercase')
        self._title_bitmap_font.lowercase_dir = os.path.join('assets', 'ui', 'fonts', 'lowercase')

        self.font_title = _BitmapFontView(self._title_bitmap_font, 30)
        self.font_large = _BitmapFontView(self._menu_font, 18)
        self.font_medium = _BitmapFontView(self._menu_font, 15)
        self.font_small = _BitmapFontView(self._menu_font, 12)
        self.font_tiny = _BitmapFontView(self._menu_font, 11)

        self._back_icon = self._load_dev_menu_icon('back', 34)
        self._save_icon = self._load_dev_menu_icon('save', 26)
        self._trash_icon = self._load_dev_menu_icon('trash', 26)
        self._plus_icon = self._load_dev_menu_icon('plus', 22)

        # ── item data ──────────────────────────────────────────────────
        self.ids: list[str] = []
        self.custom: set[str] = set()
        self.category_filter = "all"  # "all" or one of the CATEGORY_* values
        self.list_scroll = 0
        self._list_scroll_dragging = False
        self._list_scrollbar_track = None
        self._list_scrollbar_thumb = None
        self._list_scrollbar_max_scroll = 0
        self.selected_id = ""
        self.data: dict = _new_item_data("")
        self.editor_dirty = False
        self._icon: Optional[pygame.Surface] = None

        # ── status line (bottom of action row) ───────────────────────────
        self.status_msg = ""
        self.status_col = uk.Theme.TEXT_MUTED
        self.status_timer = 0.0

        # ── category filter dropdown (list panel) ─────────────────────────
        self._filter_dropdown_open = False
        self._filter_field_rect = pygame.Rect(0, 0, 0, 0)
        self._filter_option_rects: dict = {}

        # ── inline text editing (name / effect_text / description) ───────
        self.editing_field: Optional[str] = None
        self.text_input = ""
        self.cursor_pos = 0
        self.selection_anchor = None
        self.cursor_blink = 0.0
        self._text_drag = False
        self._text_max_len = 40
        self._active_edit_rect: Optional[pygame.Rect] = None
        self._active_edit_text_x: Optional[int] = None
        self._active_edit_font = None
        self.text_field_rects: list[pygame.Rect] = []

        # description word-wrap state — rebuilt every draw(), read by
        # both drawing and the text-edit engine (click mapping, scrolling)
        self._desc_rect = pygame.Rect(0, 0, 0, 0)
        self._desc_lines: list[tuple[str, int]] = [("", 0)]
        self._desc_scroll = 0
        self._desc_line_h = 18

        # ── category / effect-type pickers (editor panel) ────────────────
        self._category_rects: dict = {}
        self._effect_type_rects: dict = {}

        # ── effect sliders ────────────────────────────────────────────────
        self._slider_rects: dict = {}
        self._slider_drag_key: Optional[str] = None

        # ── new-item dialog ────────────────────────────────────────────────
        self._new_item_dialog_open = False
        self._dialog_error = ""
        self._dialog_field_rect = pygame.Rect(0, 0, 0, 0)

        # ── clickable rects, rebuilt every draw() ─────────────────────────
        self.clickable_rects: list[dict] = []

        # ── hover/press anim buckets (index-based, mirrors RoomEditor) ────
        self.hover_anim = [0.0] * 64
        self.hover_index = -1
        self._last_input = 'mouse'

        # Field-row rects, populated by the editor panel's first draw() —
        # defaulted here so handle_input never sees a missing attribute if
        # a click somehow lands before the first frame is drawn.
        self._name_field_rect = pygame.Rect(0, 0, 0, 0)
        self._effect_text_field_rect = pygame.Rect(0, 0, 0, 0)

        self._back_hovered = False
        self._back_hover_anim = 0.0
        self._new_btn_hovered = False
        self._new_btn_hover_anim = 0.0
        self._save_btn_hovered = False
        self._save_btn_hover_anim = 0.0
        self._delete_btn_hovered = False
        self._delete_btn_hover_anim = 0.0

        self._layout()

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _load_dev_menu_icon(icon_key, box_size):
        """Same shared dev-menu PNG icon loader (crop + point-sample scale)
        DevMenu._load_icon / RoomEditor._load_dev_menu_icon use, so the
        back arrow here matches theirs pixel-for-pixel."""
        path = os.path.join('assets', 'ui', 'dev_menu', 'icons', f'{icon_key}.png')
        try:
            raw = pygame.image.load(path).convert_alpha()
        except (FileNotFoundError, pygame.error):
            return pygame.Surface((box_size, box_size), pygame.SRCALPHA)

        content_rect = raw.get_bounding_rect(min_alpha=1)
        if content_rect.width <= 0 or content_rect.height <= 0:
            content_rect = raw.get_rect()
        raw = raw.subsurface(content_rect).copy()

        iw, ih = raw.get_size()
        scale = min(box_size / max(1, iw), box_size / max(1, ih))
        nw = max(1, round(iw * scale))
        nh = max(1, round(ih * scale))
        if scale >= 1.0:
            prescale = max(1, math.ceil(scale) * 2)
            big = pygame.transform.scale(raw, (iw * prescale, ih * prescale))
            scaled = pygame.transform.scale(big, (nw, nh))
        else:
            scaled = pygame.transform.scale(raw, (nw, nh))

        canvas = pygame.Surface((box_size, box_size), pygame.SRCALPHA)
        canvas.blit(scaled, ((box_size - nw) // 2, (box_size - nh) // 2))
        return canvas

    def _layout(self):
        sw, sh = self.screen_width, self.screen_height
        # Same header/footer formula as DevMenu/RoomEditor.
        self.header_h = max(86, round(sh * 0.12))
        self.footer_h = max(42, round(sh * 0.065))
        self.margin_x = max(24, round(sw * 0.03))

        back_size = max(40, round(self.header_h * 0.55))
        self._back_rect = pygame.Rect(0, 0, back_size, back_size)
        self._back_rect.left = self.margin_x
        self._back_rect.centery = self.header_h // 2

        self._title_surf = self.font_title.render("ITEM CREATOR", True, uk.Theme.TEXT_PRIMARY)

        # Save / Delete icon buttons live in the header bar itself, right
        # side, matching the back button's square size.
        self._delete_btn_rect = pygame.Rect(0, 0, back_size, back_size)
        self._delete_btn_rect.right = sw - self.margin_x
        self._delete_btn_rect.centery = self.header_h // 2
        self._save_btn_rect = pygame.Rect(0, 0, back_size, back_size)
        self._save_btn_rect.right = self._delete_btn_rect.left - 12
        self._save_btn_rect.centery = self.header_h // 2

        gap = 20
        content_top = self.header_h + gap
        content_bottom = sh - self.footer_h - gap
        content_h = max(100, content_bottom - content_top)

        list_w = min(LIST_W, max(240, int(sw * 0.24)))
        self.list_rect = pygame.Rect(self.margin_x, content_top, list_w, content_h)
        editor_x = self.list_rect.right + gap
        self.editor_rect = pygame.Rect(editor_x, content_top, sw - editor_x - self.margin_x, content_h)

        # Category filter field row, top of the list panel.
        self._filter_field_rect = pygame.Rect(self.list_rect.x, self.list_rect.y, self.list_rect.w, FIELD_H)

        # "New Item": a square plus-icon button when collapsed; when
        # expanded (adding an item) this same row becomes an inline text
        # field with confirm/cancel icon buttons — see _draw_new_item_row.
        self._new_item_row_rect = pygame.Rect(
            self.list_rect.x, self._filter_field_rect.bottom + 10, self.list_rect.w, 40)
        self._new_item_btn_rect = pygame.Rect(
            self.list_rect.x, self._new_item_row_rect.y, self._new_item_row_rect.h, self._new_item_row_rect.h)

        # Scrollable item card area fills the rest of the list panel.
        self.item_list_rect = pygame.Rect(
            self.list_rect.x, self._new_item_row_rect.bottom + 12,
            self.list_rect.w, self.list_rect.bottom - (self._new_item_row_rect.bottom + 12))

        self._icon_box = pygame.Rect(0, 0, 96, 96)

    def _resize_to(self, w, h):
        w, h = int(w), int(h)
        if (w, h) == (self.screen_width, self.screen_height):
            return
        self.screen_width, self.screen_height = w, h
        self._layout()

    # ------------------------------------------------------------------ lifecycle
    def toggle(self) -> None:
        self.active = not self.active
        if self.active:
            pygame.key.set_repeat(400, 50)
            self._refresh_list()
            self._back_hovered = False
            self._back_hover_anim = 0.0
            self._filter_dropdown_open = False
            self._new_item_dialog_open = False
            self.editing_field = None
        else:
            pygame.key.set_repeat(0, 0)

    def _refresh_list(self) -> None:
        self.ids = discover_all_item_ids()
        self.custom = discover_custom_item_ids()
        if self.selected_id not in self.ids:
            self.selected_id = self._filtered_ids()[0] if self._filtered_ids() else ""
        if self.selected_id:
            self._load_item(self.selected_id)
        else:
            self.data = _new_item_data("")
            self._icon = None

    def _filtered_ids(self) -> list[str]:
        if self.category_filter == "all":
            return list(self.ids)
        return [iid for iid in self.ids if ITEMS.get(iid, {}).get("category") == self.category_filter]

    def _load_item(self, item_id: str) -> None:
        self.selected_id = item_id
        self.data = _load_item_data(item_id)
        self._icon = _load_icon(item_id, self._icon_box.w)
        self.editor_dirty = False
        self.editing_field = None
        self._desc_scroll = 0

    def _switch_item(self, item_id: str) -> None:
        if item_id == self.selected_id:
            return
        self._load_item(item_id)

    def _set_status(self, msg: str, ok: bool = True) -> None:
        self.status_msg = msg
        self.status_col = uk.Theme.GOLD_BRIGHT if ok else uk.Theme.DANGER_BRIGHT
        self.status_timer = 3.0

    # ------------------------------------------------------------------ save / delete / new
    def _do_save(self) -> None:
        if not self.selected_id:
            return
        data = copy.deepcopy(self.data)
        if data["effect"].get("type") == "none":
            data["effect"] = {}  # 'none' isn't a real item_effects.py type — omit it entirely
        save_item_override(self.selected_id, data)
        self.custom.add(self.selected_id)
        if self.selected_id not in self.ids:
            self.ids = discover_all_item_ids()
        self.editor_dirty = False
        self._set_status(f"Saved {data['name']}")

    def _do_delete(self) -> None:
        if not self.selected_id:
            return
        revert_item_override(self.selected_id)
        self.custom.discard(self.selected_id)
        self.ids = discover_all_item_ids()
        still_exists = self.selected_id in ITEMS
        self._set_status(
            f"Reverted {self.selected_id} to built-in" if still_exists
            else f"Deleted {self.selected_id}",
            ok=still_exists,
        )
        filtered = self._filtered_ids()
        next_selected = self.selected_id if still_exists else (filtered[0] if filtered else "")
        if next_selected:
            self._load_item(next_selected)
        else:
            self.selected_id = ""
            self.data = _new_item_data("")
            self._icon = None

    def _open_new_item_dialog(self) -> None:
        self._new_item_dialog_open = True
        self._dialog_error = ""
        self._begin_text_edit("new_item_id", "")

    def _close_new_item_dialog(self) -> None:
        self._new_item_dialog_open = False
        self._dialog_error = ""
        if self.editing_field == "new_item_id":
            self.editing_field = None
            self.text_input = ""
            self.cursor_pos = 0
            self.selection_anchor = None

    def _do_create(self, raw_id: str) -> bool:
        new_id = raw_id.strip().lower().replace(" ", "_")
        if not new_id:
            self._dialog_error = "Enter an item id."
            self._set_status(self._dialog_error, ok=False)
            return False
        if new_id in ITEMS:
            self._dialog_error = f"'{new_id}' already exists."
            self._set_status(self._dialog_error, ok=False)
            return False
        save_item_override(new_id, _new_item_data(new_id))
        self.custom.add(new_id)
        self.ids = discover_all_item_ids()
        self.category_filter = "all"
        self._switch_item(new_id)
        self._set_status(f"Created {new_id} — set its category and effect, then Save")
        return True

    # ------------------------------------------------------------------ text-edit engine
    # Generalized version of RoomEditor's editing_field/text_input/cursor
    # state machine — same field/commit/cancel/selection/clipboard
    # behaviour, extended here to also support one multi-line field
    # (description) via _desc_lines word-wrap.
    def _begin_text_edit(self, field: str, text: str) -> None:
        self.editing_field = field
        self.text_input = text
        self.cursor_pos = len(text)
        self.selection_anchor = None
        self.cursor_blink = 0.0
        self._text_max_len = _FIELD_MAX_LEN.get(field, 40)

    def _has_text_selection(self) -> bool:
        return self.selection_anchor is not None and self.selection_anchor != self.cursor_pos

    def _text_selection_range(self):
        a, b = self.selection_anchor, self.cursor_pos
        return (a, b) if a <= b else (b, a)

    def _delete_text_selection(self) -> bool:
        if not self._has_text_selection():
            return False
        s, e = self._text_selection_range()
        self.text_input = self.text_input[:s] + self.text_input[e:]
        self.cursor_pos = s
        self.selection_anchor = None
        return True

    def _insert_into_text_input(self, s: str) -> None:
        allow_newline = self.editing_field == "description"
        s = "".join(ch for ch in s if ch.isprintable() or (allow_newline and ch == "\n"))
        if not s:
            return
        if self._has_text_selection():
            self._delete_text_selection()
        space = self._text_max_len - len(self.text_input)
        if space <= 0:
            return
        s = s[:space]
        self.text_input = self.text_input[:self.cursor_pos] + s + self.text_input[self.cursor_pos:]
        self.cursor_pos += len(s)

    def _text_index_from_x(self, x: int) -> int:
        """Single-line variant — used for name/effect_text/new_item_id."""
        if self._active_edit_font is None or self._active_edit_text_x is None or not self.text_input:
            return 0
        font = self._active_edit_font
        widths = [0]
        for i in range(1, len(self.text_input) + 1):
            widths.append(font.size(self.text_input[:i])[0])
        rel_x = x - self._active_edit_text_x
        best_i, best_d = 0, abs(widths[0] - rel_x)
        for i, w in enumerate(widths):
            d = abs(w - rel_x)
            if d < best_d:
                best_i, best_d = i, d
        return best_i

    def _desc_wrap_width(self) -> int:
        """Text-wrap width for the description box, net of its 10px left/
        right padding — the single source of truth so drawing, click
        mapping, and vertical-arrow navigation all wrap identically."""
        return max(10, self._desc_rect.w - 20)

    def _desc_index_from_pos(self, x: int, y: int) -> int:
        """Multi-line variant for the description box: pick the visual
        line under y (accounting for scroll), then the nearest character
        boundary in that line under x."""
        lines = self._desc_lines
        if not lines:
            return 0
        rel_row = (y - self._desc_rect.y - 6) // max(1, self._desc_line_h) + self._desc_scroll
        row = max(0, min(len(lines) - 1, int(rel_row)))
        line_text, line_start = lines[row]
        font = self.font_medium
        widths = [0]
        for i in range(1, len(line_text) + 1):
            widths.append(font.size(line_text[:i])[0])
        rel_x = x - (self._desc_rect.x + 10)
        best_i, best_d = 0, abs(widths[0] - rel_x)
        for i, w in enumerate(widths):
            d = abs(w - rel_x)
            if d < best_d:
                best_i, best_d = i, d
        return line_start + best_i

    @staticmethod
    def _wrap_lines(font, text: str, width: int) -> list[tuple[str, int]]:
        """Word-wrap `text` (which may contain manual '\\n' breaks) to fit
        `width`. Returns [(visual_line_text, raw_start_index), ...] so the
        caret / click-to-index mapping can translate back to a position in
        the original (unwrapped) string."""
        lines: list[tuple[str, int]] = []
        offset = 0
        paragraphs = text.split("\n")
        for para in paragraphs:
            if para == "":
                lines.append(("", offset))
            else:
                words = para.split(" ")
                word_starts = []
                pos = offset
                for w in words:
                    word_starts.append(pos)
                    pos += len(w) + 1
                cur_words: list[str] = []
                cur_start = word_starts[0]
                for wi, w in enumerate(words):
                    trial = cur_words + [w]
                    if cur_words and font.size(" ".join(trial))[0] > width:
                        lines.append((" ".join(cur_words), cur_start))
                        cur_words = [w]
                        cur_start = word_starts[wi]
                    else:
                        cur_words = trial
                lines.append((" ".join(cur_words), cur_start))
            offset += len(para) + 1
        return lines

    def _finish_text_input(self) -> None:
        field = self.editing_field
        if field is None:
            return
        text = self.text_input

        if field == "name":
            self.data["name"] = text
            self.editor_dirty = True
        elif field == "effect_text":
            self.data["effect_text"] = text
            self.editor_dirty = True
        elif field == "description":
            self.data["description"] = text
            self.editor_dirty = True
        elif field == "new_item_id":
            if self._do_create(text):
                self._close_new_item_dialog()
            return  # keep the dialog (and its field) open on failure

        self.editing_field = None
        self.text_input = ""
        self.cursor_pos = 0
        self.selection_anchor = None

    def _cancel_text_input(self) -> None:
        if self.editing_field == "new_item_id":
            self._close_new_item_dialog()
            return
        self.editing_field = None
        self.text_input = ""
        self.cursor_pos = 0
        self.selection_anchor = None

    def _handle_text_edit_event(self, event) -> bool:
        """Returns True if the event was consumed by the text-edit engine."""
        if self.editing_field is None:
            return False

        if event.type == pygame.KEYDOWN:
            mods = pygame.key.get_mods()
            ctrl = bool(mods & (pygame.KMOD_CTRL | pygame.KMOD_META))
            shift = bool(mods & pygame.KMOD_SHIFT)
            multiline = self.editing_field == "description"

            if event.key == pygame.K_RETURN:
                if multiline:
                    self._insert_into_text_input("\n")
                else:
                    self._finish_text_input()
            elif event.key == pygame.K_ESCAPE:
                self._cancel_text_input()
            elif event.key == pygame.K_TAB:
                self._finish_text_input()
            elif ctrl and event.key == pygame.K_a:
                self.selection_anchor = 0
                self.cursor_pos = len(self.text_input)
            elif ctrl and event.key in (pygame.K_c, pygame.K_x):
                if self._has_text_selection():
                    s, e = self._text_selection_range()
                    uk.clipboard_set_text(self.text_input[s:e])
                    if event.key == pygame.K_x:
                        self._delete_text_selection()
            elif ctrl and event.key == pygame.K_v:
                self._insert_into_text_input(uk.clipboard_get_text())
            elif event.key == pygame.K_LEFT:
                if shift:
                    if self.selection_anchor is None:
                        self.selection_anchor = self.cursor_pos
                    self.cursor_pos = max(0, self.cursor_pos - 1)
                elif self._has_text_selection():
                    self.cursor_pos = self._text_selection_range()[0]
                    self.selection_anchor = None
                else:
                    self.cursor_pos = max(0, self.cursor_pos - 1)
            elif event.key == pygame.K_RIGHT:
                if shift:
                    if self.selection_anchor is None:
                        self.selection_anchor = self.cursor_pos
                    self.cursor_pos = min(len(self.text_input), self.cursor_pos + 1)
                elif self._has_text_selection():
                    self.cursor_pos = self._text_selection_range()[1]
                    self.selection_anchor = None
                else:
                    self.cursor_pos = min(len(self.text_input), self.cursor_pos + 1)
            elif event.key in (pygame.K_UP, pygame.K_DOWN) and multiline:
                self._move_desc_cursor_vertical(-1 if event.key == pygame.K_UP else 1, shift)
            elif event.key == pygame.K_HOME:
                if shift and self.selection_anchor is None:
                    self.selection_anchor = self.cursor_pos
                elif not shift:
                    self.selection_anchor = None
                self.cursor_pos = 0
            elif event.key == pygame.K_END:
                if shift and self.selection_anchor is None:
                    self.selection_anchor = self.cursor_pos
                elif not shift:
                    self.selection_anchor = None
                self.cursor_pos = len(self.text_input)
            elif event.key == pygame.K_BACKSPACE:
                if not self._delete_text_selection() and self.cursor_pos > 0:
                    self.text_input = self.text_input[:self.cursor_pos - 1] + self.text_input[self.cursor_pos:]
                    self.cursor_pos -= 1
            elif event.key == pygame.K_DELETE:
                if not self._delete_text_selection() and self.cursor_pos < len(self.text_input):
                    self.text_input = self.text_input[:self.cursor_pos] + self.text_input[self.cursor_pos + 1:]
            else:
                if event.unicode and (event.unicode.isprintable() or (multiline and event.unicode == "\n")):
                    self._insert_into_text_input(event.unicode)
            self.cursor_blink = 0.0
            return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._active_edit_rect is not None and self._active_edit_rect.collidepoint(event.pos):
                idx = (self._desc_index_from_pos(*event.pos) if self.editing_field == "description"
                       else self._text_index_from_x(event.pos[0]))
                if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                    if self.selection_anchor is None:
                        self.selection_anchor = self.cursor_pos
                else:
                    self.selection_anchor = idx
                self.cursor_pos = idx
                self._text_drag = True
                self.cursor_blink = 0.0
                return True
            self._finish_text_input()
            return False  # let the click fall through to normal handling

        if event.type == pygame.MOUSEMOTION:
            if self._text_drag and self._active_edit_rect is not None:
                x = max(self._active_edit_rect.left, min(event.pos[0], self._active_edit_rect.right))
                y = max(self._active_edit_rect.top, min(event.pos[1], self._active_edit_rect.bottom - 1))
                idx = (self._desc_index_from_pos(x, y) if self.editing_field == "description"
                       else self._text_index_from_x(x))
                self.cursor_pos = idx
                self.cursor_blink = 0.0
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._text_drag = False
            return True

        if event.type == pygame.MOUSEWHEEL and self.editing_field == "description":
            self._desc_scroll = max(0, self._desc_scroll - event.y)
            return True

        return True

    def _move_desc_cursor_vertical(self, direction: int, shift: bool) -> None:
        lines = self._wrap_lines(self.font_medium, self.text_input, self._desc_wrap_width())
        starts = [s for _, s in lines]
        row = 0
        for i, s in enumerate(starts):
            if s <= self.cursor_pos:
                row = i
        col_x = self.font_medium.size(self.text_input[lines[row][1]:self.cursor_pos])[0]
        new_row = max(0, min(len(lines) - 1, row + direction))
        line_text, line_start = lines[new_row]
        widths = [0]
        for i in range(1, len(line_text) + 1):
            widths.append(self.font_medium.size(line_text[:i])[0])
        best_i, best_d = 0, abs(widths[0] - col_x)
        for i, w in enumerate(widths):
            d = abs(w - col_x)
            if d < best_d:
                best_i, best_d = i, d
        new_pos = line_start + best_i
        if shift:
            if self.selection_anchor is None:
                self.selection_anchor = self.cursor_pos
        else:
            self.selection_anchor = None
        self.cursor_pos = new_pos

    # ------------------------------------------------------------------ effect sliders
    def _slider_specs(self) -> list[dict]:
        """Effect-specific numeric fields for the currently selected effect
        type — same set of fields _build_effect_widgets used to build,
        just described declaratively instead of as widget objects."""
        effect = self.data["effect"]
        etype = effect.get("type", "none")
        specs: list[dict] = []

        def stat_specs():
            stats = effect.setdefault("stats", {})
            out = []
            for sid in STAT_IDS:
                out.append({
                    "key": f"stat_{sid}", "label": STAT_LABELS[sid],
                    "min": -50, "max": 100, "step": 1, "as_int": True,
                    "get": lambda stats=stats, sid=sid: stats.get(sid, 0),
                    "set": lambda v, stats=stats, sid=sid: stats.__setitem__(sid, int(v)),
                })
            return out

        if etype in ("heal_hp", "heal_ep"):
            specs.append({
                "key": "amount", "label": "Amount", "min": 1, "max": 3000, "step": 5, "as_int": True,
                "get": lambda: effect.get("amount", 20),
                "set": lambda v: effect.__setitem__("amount", int(v)),
            })
        elif etype == "revive":
            specs.append({
                "key": "hp_ratio", "label": "HP Ratio", "min": 0.05, "max": 1.0, "step": 0.05,
                "as_int": False, "fmt": "{:.2f}",
                "get": lambda: effect.get("hp_ratio", 0.5),
                "set": lambda v: effect.__setitem__("hp_ratio", round(v, 2)),
            })
        elif etype == "buff":
            specs.append({
                "key": "duration", "label": "Duration", "min": 1, "max": 120, "step": 1,
                "as_int": True, "fmt": "{:.0f}s",
                "get": lambda: effect.get("duration", 30.0),
                "set": lambda v: effect.__setitem__("duration", round(v, 1)),
            })
            specs.extend(stat_specs())
        elif etype == "equip_stat":
            specs.extend(stat_specs())
            specs.append({
                "key": "exp_bonus", "label": "XP Bonus", "min": 0.0, "max": 1.0, "step": 0.05,
                "as_int": False, "fmt": "{:.2f}",
                "get": lambda: effect.get("exp_bonus", 0.0),
                "set": lambda v: effect.__setitem__("exp_bonus", round(v, 2)),
            })
        return specs

    def _apply_slider_drag(self, key: str, mouse_x: int) -> None:
        rect = self._slider_rects.get(key)
        if rect is None:
            return
        spec = next((s for s in self._slider_specs() if s["key"] == key), None)
        if spec is None:
            return
        t = max(0.0, min(1.0, (mouse_x - rect.x) / max(1, rect.w)))
        value = spec["min"] + t * (spec["max"] - spec["min"])
        step = spec.get("step", 1)
        if step:
            value = round(value / step) * step
        value = max(spec["min"], min(spec["max"], value))
        spec["set"](value)
        self.editor_dirty = True

    # ------------------------------------------------------------------ input
    def handle_input(self, event):
        if not self.active:
            return None

        if hasattr(event, 'pos'):
            self._logical_mouse_pos = tuple(event.pos)

        # -- New-item inline row: fully captures input while expanded -------
        if self._new_item_dialog_open:
            if self._handle_text_edit_event(event):
                return None
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if not self._dialog_field_rect.collidepoint(event.pos):
                    # Clicking anywhere outside the field closes it, same
                    # as any other inline field committing on outside click.
                    self._close_new_item_dialog()
            return None

        # -- Inline field editing (name / effect_text / description) --------
        if self.editing_field is not None:
            consumed = self._handle_text_edit_event(event)
            if consumed:
                return None
            # fall through — the click that just committed the field may
            # also hit a button/row below

        # -- Category filter dropdown ----------------------------------------
        if self._filter_dropdown_open:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for cat, rect in self._filter_option_rects.items():
                    if rect.collidepoint(event.pos):
                        self.category_filter = cat
                        self._filter_dropdown_open = False
                        filtered = self._filtered_ids()
                        if filtered and self.selected_id not in filtered:
                            self._switch_item(filtered[0])
                        self.list_scroll = 0
                        return None
                self._filter_dropdown_open = False
                return None
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self._filter_dropdown_open = False
                return None

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.active = False
            return 'back_to_dev_menu'

        if event.type == pygame.KEYDOWN and event.key == pygame.K_s and (event.mod & pygame.KMOD_CTRL):
            self._do_save()
            return None

        if event.type == pygame.MOUSEWHEEL:
            if self.item_list_rect.collidepoint(self._logical_mouse_pos):
                self.list_scroll = max(0, self.list_scroll - event.y)
                return None

        if event.type == pygame.MOUSEMOTION:
            self._last_input = 'mouse'
            if self._list_scroll_dragging:
                self._scrub_list_scroll(event.pos[1])
                return None
            self._back_hovered = self._back_rect.collidepoint(event.pos)
            self._new_btn_hovered = self._new_item_btn_rect.collidepoint(event.pos)
            self._save_btn_hovered = self._save_btn_rect.collidepoint(event.pos)
            self._delete_btn_hovered = self._delete_btn_rect.collidepoint(event.pos)
            self._set_hover_from_pos(event.pos)
            return None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._back_rect.collidepoint(event.pos):
                self.active = False
                return 'back_to_dev_menu'
            if self._filter_field_rect.collidepoint(event.pos):
                self._filter_dropdown_open = not self._filter_dropdown_open
                return None
            if self._new_item_btn_rect.collidepoint(event.pos):
                self._open_new_item_dialog()
                return None
            if self._save_btn_rect.collidepoint(event.pos):
                self._do_save()
                return None
            if self._delete_btn_rect.collidepoint(event.pos):
                self._do_delete()
                return None

            # scrollbar thumb / track — click-to-jump, then drag
            if self._list_scrollbar_track is not None:
                hit = self._list_scrollbar_track.inflate(12, 0)
                if hit.collidepoint(event.pos) or (
                        self._list_scrollbar_thumb and self._list_scrollbar_thumb.collidepoint(event.pos)):
                    self._list_scroll_dragging = True
                    self._scrub_list_scroll(event.pos[1])
                    return None

            # item list rows
            filtered = self._filtered_ids()
            if self.item_list_rect.collidepoint(event.pos):
                visible = max(1, self.item_list_rect.h // CARD_H)
                for i, iid in enumerate(filtered[self.list_scroll:self.list_scroll + visible + 1]):
                    row_y = self.item_list_rect.y + i * CARD_H
                    row_rect = pygame.Rect(self.item_list_rect.x, row_y, self.item_list_rect.w, CARD_H - 8)
                    if row_rect.collidepoint(event.pos):
                        self._switch_item(iid)
                        return None

            # name / effect_text field rows
            if self._name_field_rect.collidepoint(event.pos):
                self._begin_text_edit("name", self.data.get("name", ""))
                return None
            if self._effect_text_field_rect.collidepoint(event.pos):
                self._begin_text_edit("effect_text", self.data.get("effect_text", ""))
                return None
            if self._desc_rect.collidepoint(event.pos):
                self._begin_text_edit("description", self.data.get("description", ""))
                idx = self._desc_index_from_pos(*event.pos)
                self.cursor_pos = idx
                return None

            # category picker
            for cat, rect in self._category_rects.items():
                if rect.collidepoint(event.pos) and self.data["category"] != cat:
                    self.data["category"] = cat
                    slot = CATEGORY_SLOT[cat]
                    if slot:
                        self.data["slot"] = slot
                    else:
                        self.data.pop("slot", None)
                    self.editor_dirty = True
                    return None

            # effect type picker
            for etype, rect in self._effect_type_rects.items():
                if rect.collidepoint(event.pos) and self.data["effect"].get("type") != etype:
                    self.data["effect"] = _default_effect_for_type(etype, self.data["effect"])
                    self.editor_dirty = True
                    return None

            # sliders — click-to-set, then drag
            for key, rect in self._slider_rects.items():
                hit = rect.inflate(0, 16)
                if hit.collidepoint(event.pos):
                    self._slider_drag_key = key
                    self._apply_slider_drag(key, event.pos[0])
                    return None

            return None

        if event.type == pygame.MOUSEMOTION and self._slider_drag_key is not None:
            self._apply_slider_drag(self._slider_drag_key, event.pos[0])
            return None

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._slider_drag_key = None
            self._list_scroll_dragging = False
            return None

        return None

    def _set_hover_from_pos(self, pos):
        self.hover_index = -1
        filtered = self._filtered_ids()
        if not self.item_list_rect.collidepoint(pos):
            return
        visible = max(1, self.item_list_rect.h // CARD_H)
        for i, iid in enumerate(filtered[self.list_scroll:self.list_scroll + visible + 1]):
            row_y = self.item_list_rect.y + i * CARD_H
            row_rect = pygame.Rect(self.item_list_rect.x, row_y, self.item_list_rect.w, CARD_H - 8)
            if row_rect.collidepoint(pos):
                self.hover_index = self.list_scroll + i
                return

    # ------------------------------------------------------------------ frame
    def update(self, dt: float) -> None:
        if not self.active:
            uk.set_text_cursor(False)
            uk.set_hand_cursor(False)
            return

        dt = min(dt, 1 / 20)
        self.cursor_blink += dt

        target_back = 1.0 if self._back_hovered else 0.0
        self._back_hover_anim += (target_back - self._back_hover_anim) * min(1.0, dt * 12.0)
        target_new = 1.0 if self._new_btn_hovered else 0.0
        self._new_btn_hover_anim += (target_new - self._new_btn_hover_anim) * min(1.0, dt * 12.0)
        target_save = 1.0 if self._save_btn_hovered else 0.0
        self._save_btn_hover_anim += (target_save - self._save_btn_hover_anim) * min(1.0, dt * 12.0)
        target_delete = 1.0 if self._delete_btn_hovered else 0.0
        self._delete_btn_hover_anim += (target_delete - self._delete_btn_hover_anim) * min(1.0, dt * 12.0)

        filtered = self._filtered_ids()
        for i in range(len(filtered)):
            if i >= len(self.hover_anim):
                break
            target = 1.0 if i == self.hover_index else 0.0
            self.hover_anim[i] += (target - self.hover_anim[i]) * min(1.0, dt * 12.0)

        if self.status_timer > 0:
            self.status_timer -= dt
            if self.status_timer <= 0:
                self.status_msg = ""

        hovering_text_field = any(r.collidepoint(self._logical_mouse_pos) for r in self.text_field_rects)
        uk.set_text_cursor(hovering_text_field)
        hovering_widget = (not hovering_text_field
                            and any(c["rect"].collidepoint(self._logical_mouse_pos) for c in self.clickable_rects))
        uk.set_hand_cursor(hovering_widget)

    # ------------------------------------------------------------------ drawing helpers (shared card/panel primitives)
    def _item_anim(self, index: int) -> float:
        if 0 <= index < len(self.hover_anim):
            return self.hover_anim[index]
        return 1.0 if index == self.hover_index else 0.0

    def _draw_background(self, screen):
        w, h = self.screen_width, self.screen_height
        uk.draw_rect_on(screen, (8, 11, 17), pygame.Rect(0, 0, w, h), 0, 0)
        uk.draw_rect_on(screen, (10, 13, 20),
                         pygame.Rect(0, self.header_h, w, h - self.header_h - self.footer_h), 0, 0)

    def _draw_header(self, screen):
        w = self.screen_width
        uk.draw_rect_on(screen, (12, 15, 23), pygame.Rect(0, 0, w, self.header_h), 0, 0)
        uk.draw_rect_on(screen, (43, 49, 63), pygame.Rect(0, self.header_h - 1, w, 1), 0, 0)

        title_rect = self._title_surf.get_rect(centerx=w // 2, centery=self.header_h // 2)
        uk.blit_surface(screen, self._title_surf, title_rect, transient=False)

        self._draw_back_button(screen)

        save_accent = uk.Theme.GOLD_BRIGHT if self.editor_dirty else uk.Theme.GOLD
        t_save = max(self._save_btn_hover_anim, 0.35 if self.editor_dirty else 0.0)
        self._draw_icon_square_btn(screen, self._save_btn_rect, t_save, self._icon_save_png, save_accent)
        self.clickable_rects.append({"rect": self._save_btn_rect})

        t_del = self._delete_btn_hover_anim
        self._draw_icon_square_btn(screen, self._delete_btn_rect, t_del, self._icon_trash_png,
                                    uk.Theme.DANGER_BRIGHT, danger=True)
        self.clickable_rects.append({"rect": self._delete_btn_rect})

        if self.status_msg:
            status_surf = self.font_small.render(self.status_msg, True, self.status_col)
            status_rect = status_surf.get_rect(right=self._save_btn_rect.left - 16, centery=self.header_h // 2)
            uk.blit_surface(screen, status_surf, status_rect, transient=True)

    def _draw_back_button(self, screen):
        accent = uk.Theme.GOLD
        t = round(self._back_hover_anim * 20) / 20.0
        base = uk.lerp_color((22, 26, 35), (28, 33, 44), t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, accent, t * 0.78)
        uk.draw_panel(screen, self._back_rect, bg=(*base, 255), border=border,
                      border_width=1, radius=8, shadow=False)
        if t > 0.01:
            uk.draw_soft_glow(screen, self._back_rect.center, 22, accent, max_alpha=int(25 * t))
        icon_rect = self._back_icon.get_rect(center=self._back_rect.center)
        uk.blit_surface(screen, self._back_icon, icon_rect, transient=False)
        self.clickable_rects.append({"rect": self._back_rect})

    def _icon_save_png(self, screen, rect, color):
        if self._save_icon is not None:
            uk.blit_surface(screen, self._save_icon, self._save_icon.get_rect(center=rect.center))
        else:
            _draw_check_icon(screen, rect, color)

    def _icon_trash_png(self, screen, rect, color):
        if self._trash_icon is not None:
            uk.blit_surface(screen, self._trash_icon, self._trash_icon.get_rect(center=rect.center))
        else:
            _draw_trash_icon(screen, rect, color)

    def _icon_plus_png(self, screen, rect, color):
        if self._plus_icon is not None:
            uk.blit_surface(screen, self._plus_icon, self._plus_icon.get_rect(center=rect.center))
        else:
            _draw_plus_icon(screen, rect, color)

    def _draw_icon_square_btn(self, screen, rect, t, icon_fn, accent, danger=False):
        """Square, icon-only button (no label) — same shape as the back
        button, used for Save / Delete in the header bar."""
        t = round(max(0.0, min(1.0, t)) * 20) / 20.0
        dim_base = (32, 22, 22) if danger else (28, 33, 44)
        base = uk.lerp_color((22, 26, 35), dim_base, t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, accent, t)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border, border_width=1 + round(t),
                      radius=8, shadow=False)
        if t > 0.01:
            uk.draw_soft_glow(screen, rect.center, rect.w // 2 + 4, accent, max_alpha=int(28 * t))
        icon_col = uk.lerp_color(uk.Theme.TEXT_SECONDARY, accent, t)
        icon_size = max(16, round(rect.w * 0.4))
        icon_rect = pygame.Rect(0, 0, icon_size, icon_size)
        icon_rect.center = rect.center
        icon_fn(screen, icon_rect, icon_col)

    def _draw_footer(self, screen):
        w, h = self.screen_width, self.screen_height
        y = h - self.footer_h
        uk.draw_rect_on(screen, (12, 15, 23), pygame.Rect(0, y, w, self.footer_h), 0, 0)
        uk.draw_rect_on(screen, (43, 49, 63), pygame.Rect(0, y, w, 1), 0, 0)

    def _draw_card_shell(self, screen, rect, t, accent=None):
        accent = accent or uk.Theme.GOLD
        t = round(max(0.0, min(1.0, t)) * 20) / 20.0
        lift = int(round(2 * t))
        draw_rect = rect.move(0, -lift)
        base = uk.lerp_color((22, 26, 35), (28, 33, 44), t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, accent, t * 0.78)
        uk.draw_panel(screen, draw_rect, bg=(*base, 255), border=border, border_width=1, radius=10, shadow=False)
        return draw_rect

    def _draw_pill_button(self, screen, rect, t, label, accent, icon_fn=None, danger=False):
        col = uk.Theme.DANGER_BRIGHT if danger else accent
        dim_base = (32, 22, 22) if danger else (28, 33, 44)
        base = uk.lerp_color((22, 26, 35), dim_base, t)
        border = uk.lerp_color(uk.Theme.CARD_BORDER, col, t)
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border, border_width=1 + round(t), radius=10, shadow=False)
        if t > 0.01:
            uk.draw_soft_glow(screen, rect.center, max(rect.w, rect.h) // 2, col, max_alpha=int(30 * t))
        label_color = uk.lerp_color(uk.Theme.TEXT_SECONDARY, col, t)
        if icon_fn is not None:
            icon_rect = pygame.Rect(0, 0, 16, 16)
            icon_rect.midleft = (rect.x + 14, rect.centery)
            icon_fn(screen, icon_rect, label_color)
            label_surf = self.font_medium.render(label, True, label_color)
            uk.blit_surface(screen, label_surf, (icon_rect.right + 8, rect.centery - label_surf.get_height() // 2),
                             transient=True)
        else:
            label_surf = self.font_medium.render(label, True, label_color)
            uk.blit_surface(screen, label_surf, label_surf.get_rect(center=rect.center), transient=True)

    def _draw_text_caret(self, screen, x, y, height, color=None):
        if int(self.cursor_blink * 2) % 2 != 0:
            return
        color = color or uk.Theme.TEXT_PRIMARY
        uk.draw_rect_on(screen, color, pygame.Rect(int(x), int(y), 2, int(height)), 0, 0)

    def _draw_live_text_field(self, screen, font, text_rect, click_rect):
        self._active_edit_rect = click_rect
        self._active_edit_text_x = text_rect.x
        self._active_edit_font = font

        if self._has_text_selection():
            s, e = self._text_selection_range()
            sx = text_rect.x + (font.size(self.text_input[:s])[0] if s else 0)
            ex = text_rect.x + (font.size(self.text_input[:e])[0] if e else 0)
            sel_rect = pygame.Rect(sx, text_rect.y, max(1, ex - sx), text_rect.height)
            uk.draw_rect_on(screen, (*uk.Theme.KI_BLUE, 90), sel_rect, 0, 0)

        caret_w = font.size(self.text_input[:self.cursor_pos])[0] if self.cursor_pos else 0
        self._draw_text_caret(screen, text_rect.x + caret_w, text_rect.y, text_rect.height)

    def _draw_field_row(self, screen, rect, value_text, field_id, placeholder=""):
        editing = self.editing_field == field_id
        t = 1.0 if editing else 0.0
        accent = uk.Theme.GOLD
        base = uk.lerp_color((20, 23, 32), (27, 31, 42), t)
        border = accent if editing else uk.Theme.CARD_BORDER
        border_width = 2 if editing else 1
        uk.draw_panel(screen, rect, bg=(*base, 255), border=border, border_width=border_width, radius=8, shadow=False)
        self.text_field_rects.append(rect)

        if editing:
            shown, color = self.text_input, uk.Theme.TEXT_PRIMARY
        elif value_text:
            shown, color = value_text, uk.Theme.TEXT_SECONDARY
        else:
            shown, color = placeholder, uk.Theme.TEXT_DIM

        val_surf = self.font_medium.render(shown, True, color)
        val_rect = val_surf.get_rect(x=rect.x + 12, centery=rect.centery)
        uk.blit_surface(screen, val_surf, val_rect, transient=True)

        if editing:
            # render("") from the bitmap font comes back near-zero-height,
            # which collapsed the caret into a dot instead of a bar when
            # the field is empty. Use a fixed line height (from a
            # reference glyph) instead of trusting val_rect's height,
            # which is only meaningful when there's text to measure.
            line_h = self.font_medium.size("Ag")[1]
            caret_rect = pygame.Rect(val_rect.x, 0, val_rect.w, line_h)
            caret_rect.centery = rect.centery
            self._draw_live_text_field(screen, self.font_medium, caret_rect, rect)

    def _draw_slider(self, screen, x, y, width, spec):
        key = spec["key"]
        value = spec["get"]()
        fmt = spec.get("fmt", "{:.0f}")
        display = fmt.format(value)

        label_surf = self.font_small.render(spec["label"].upper(), True, uk.Theme.TEXT_MUTED)
        uk.blit_surface(screen, label_surf, (x, y), transient=True)
        val_surf = self.font_small.render(display, True, uk.Theme.TEXT_SECONDARY)
        uk.blit_surface(screen, val_surf, (x + width - val_surf.get_width(), y), transient=True)

        track_y = y + label_surf.get_height() + 6
        track = pygame.Rect(x, track_y, width, 6)
        uk.draw_rect_on(screen, uk.Theme.CARD_BG[:3], track, 0, 3)

        t = max(0.0, min(1.0, (value - spec["min"]) / max(1e-9, spec["max"] - spec["min"])))
        fill_w = max(0, min(width, int(t * width)))
        if fill_w:
            uk.draw_rect_on(screen, uk.Theme.GOLD, pygame.Rect(x, track_y, fill_w, 6), 0, 3)

        thumb_x = x + int(t * width)
        thumb_cy = track_y + 3
        mx, my = self._logical_mouse_pos
        dragging = self._slider_drag_key == key
        hovered = abs(mx - thumb_x) <= 10 and abs(my - thumb_cy) <= 10
        thumb_color = uk.Theme.GOLD_BRIGHT if (dragging or hovered) else uk.Theme.TEXT_PRIMARY
        uk.draw_circle_on(screen, thumb_color, (thumb_x, thumb_cy), 7)
        uk.draw_circle_on(screen, uk.Theme.CARD_BORDER, (thumb_x, thumb_cy), 7, 1)

        self._slider_rects[key] = track
        self.clickable_rects.append({"rect": track.inflate(0, 16)})

    # ------------------------------------------------------------------ list panel
    def _draw_filter_dropdown_field(self, screen):
        rect = self._filter_field_rect
        open_ = self._filter_dropdown_open
        accent = uk.Theme.GOLD
        border = accent if open_ else uk.Theme.CARD_BORDER
        uk.draw_panel(screen, rect, bg=(*uk.Theme.CARD_BG[:3], 255), border=border,
                      border_width=2 if open_ else 1, radius=8, shadow=False)
        self.clickable_rects.append({"rect": rect})

        label = "All Categories" if self.category_filter == "all" else CATEGORY_LABELS[self.category_filter]
        if self.category_filter != "all":
            dot_c = pygame.Rect(0, 0, 10, 10)
            dot_c.midleft = (rect.x + 12, rect.centery)
            uk.draw_circle_on(screen, CATEGORY_ACCENT[self.category_filter], dot_c.center, 5)
            text_x = rect.x + 28
        else:
            text_x = rect.x + 12
        label_surf = self.font_medium.render(label, True, uk.Theme.TEXT_PRIMARY)
        uk.blit_surface(screen, label_surf, label_surf.get_rect(x=text_x, centery=rect.centery), transient=True)

        chev = pygame.Rect(0, 0, 16, 16)
        chev.midright = (rect.right - 12, rect.centery)
        _draw_chevron_down(screen, chev, uk.Theme.TEXT_MUTED)

    def _draw_filter_dropdown_list(self, screen):
        rect = self._filter_field_rect
        options = ["all"] + [c for c, _, _ in CATEGORIES]
        item_h = 32
        list_h = item_h * len(options)
        list_rect = pygame.Rect(rect.x, rect.bottom + 6, rect.w, list_h)
        if list_rect.bottom > self.item_list_rect.bottom:
            list_rect.height = max(item_h, self.item_list_rect.bottom - list_rect.y)

        uk.draw_panel(screen, list_rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.GOLD, border_width=1, radius=8)

        mouse_pos = self._logical_mouse_pos
        self._filter_option_rects = {}
        for i, opt in enumerate(options):
            item_rect = pygame.Rect(list_rect.x, list_rect.y + i * item_h, list_rect.w, item_h)
            if item_rect.bottom > list_rect.bottom:
                break
            self._filter_option_rects[opt] = item_rect
            self.clickable_rects.append({"rect": item_rect})
            is_current = (opt == self.category_filter)
            hovered = item_rect.collidepoint(mouse_pos)
            if is_current or hovered:
                row_bg = uk.Theme.CARD_BG_SELECTED[:3] if is_current else uk.Theme.CARD_BG_HOVER[:3]
                uk.draw_rect_on(screen, row_bg, item_rect.inflate(-6, -2), 0, 6)
            label = "All Categories" if opt == "all" else CATEGORY_LABELS[opt]
            text_color = uk.Theme.GOLD_BRIGHT if is_current else uk.Theme.TEXT_SECONDARY
            text_surf = self.font_small.render(label, True, text_color)
            uk.blit_surface(screen, text_surf,
                             (item_rect.x + 12, item_rect.y + (item_h - text_surf.get_height()) // 2),
                             transient=True)
            if is_current:
                chk = pygame.Rect(0, 0, 12, 12)
                chk.midright = (item_rect.right - 10, item_rect.centery)
                _draw_check_icon(screen, chk, uk.Theme.GOLD_BRIGHT)

    def _draw_item_list(self, screen):
        rect = self.item_list_rect
        filtered = self._filtered_ids()
        old_clip = screen.get_clip()
        screen.set_clip(rect)

        visible = max(1, rect.h // CARD_H)
        mouse_pos = self._logical_mouse_pos
        for i, iid in enumerate(filtered[self.list_scroll:self.list_scroll + visible + 1]):
            idx = self.list_scroll + i
            row_y = rect.y + i * CARD_H
            row_rect = pygame.Rect(rect.x, row_y, rect.w, CARD_H - 8)
            if row_rect.bottom > rect.bottom:
                break
            self.clickable_rects.append({"rect": row_rect})

            is_sel = (iid == self.selected_id)
            t = self._item_anim(idx) if not is_sel else 1.0
            item_data = ITEMS.get(iid, {})
            accent = CATEGORY_ACCENT.get(item_data.get("category"), uk.Theme.GOLD)

            base = uk.Theme.CARD_BG_SELECTED[:3] if is_sel else uk.lerp_color(
                uk.Theme.CARD_BG[:3], uk.Theme.CARD_BG_HOVER[:3], t)
            border = accent if is_sel else uk.lerp_color(uk.Theme.CARD_BORDER, accent, t)
            uk.draw_panel(screen, row_rect, bg=(*base, 255), border=border,
                          border_width=1 + (1 if is_sel else 0), radius=8, shadow=False)

            dot_center = (row_rect.x + 22, row_rect.centery)
            uk.draw_circle_on(screen, accent, dot_center, 6)

            name = item_data.get("name", iid)
            name_color = uk.Theme.TEXT_PRIMARY if (is_sel or t > 0.2) else uk.Theme.TEXT_SECONDARY
            name_surf = self.font_medium.render(name, True, name_color)
            name_rect = name_surf.get_rect(x=row_rect.x + 40, y=row_rect.y + 8)
            uk.blit_surface(screen, name_surf, name_rect, transient=True)

            sub_surf = self.font_tiny.render(iid, True, uk.Theme.TEXT_DIM)
            uk.blit_surface(screen, sub_surf, (row_rect.x + 40, name_rect.bottom + 2), transient=True)

            if iid in self.custom:
                star_rect = pygame.Rect(0, 0, 14, 14)
                star_rect.midright = (row_rect.right - 12, row_rect.centery)
                _draw_star_icon(screen, star_rect, uk.Theme.GOLD_BRIGHT)

        screen.set_clip(old_clip)

        total_rows = len(filtered)
        max_scroll = max(0, total_rows - visible)
        if self.list_scroll > max_scroll:
            self.list_scroll = max_scroll

        if not filtered:
            msg = self.font_small.render("No items in this category", True, uk.Theme.TEXT_DIM)
            uk.blit_surface(screen, msg, msg.get_rect(center=rect.center), transient=True)
            self._list_scrollbar_track = None
            self._list_scrollbar_thumb = None
            self._list_scrollbar_max_scroll = 0
        elif max_scroll > 0:
            # Draggable scrollbar — grab and drag the thumb up/down instead
            # of only being able to use the scroll wheel.
            track = pygame.Rect(rect.right - 6, rect.y, 5, rect.h)
            total_content_h = total_rows * CARD_H
            th = max(24, int(rect.h * rect.h / max(1, total_content_h)))
            frac = self.list_scroll / max_scroll
            ty = rect.y + int((rect.h - th) * frac)
            thumb = pygame.Rect(track.x, ty, track.w, th)
            self._list_scrollbar_track = track
            self._list_scrollbar_thumb = thumb
            self._list_scrollbar_max_scroll = max_scroll
            self.clickable_rects.append({"rect": track.inflate(12, 0)})

            grabbed = self._list_scroll_dragging
            hovered = grabbed or thumb.collidepoint(self._logical_mouse_pos)
            uk.draw_rect_on(screen, (24, 28, 38), track, 0, 2)
            thumb_col = uk.Theme.GOLD_BRIGHT if hovered else uk.Theme.TEXT_DIM
            uk.draw_rect_on(screen, thumb_col, thumb, 0, 2)
        else:
            self._list_scrollbar_track = None
            self._list_scrollbar_thumb = None
            self._list_scrollbar_max_scroll = 0

    def _scrub_list_scroll(self, pos_y: int) -> None:
        track, thumb = self._list_scrollbar_track, self._list_scrollbar_thumb
        if track is None or thumb is None or self._list_scrollbar_max_scroll <= 0:
            return
        frac = (pos_y - track.y - thumb.h / 2) / max(1, track.h - thumb.h)
        frac = max(0.0, min(1.0, frac))
        self.list_scroll = round(frac * self._list_scrollbar_max_scroll)

    def _draw_list_panel(self, screen):
        uk.draw_panel(screen, self.list_rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.PANEL_BORDER,
                      border_width=1, radius=12, shadow=False)
        self._draw_filter_dropdown_field(screen)

        self._draw_new_item_row(screen)

        self._draw_item_list(screen)

        if self._filter_dropdown_open:
            self._draw_filter_dropdown_list(screen)

    # ------------------------------------------------------------------ editor panel
    def _draw_icon_preview(self, screen, panel_rect, pad):
        box = pygame.Rect(0, 0, 96, 96)
        box.topright = (panel_rect.right - pad, panel_rect.y + pad)
        label = self.font_small.render("Icon", True, uk.Theme.TEXT_MUTED)
        uk.blit_surface(screen, label, (box.x, box.y - label.get_height() - 4), transient=True)

        if self._icon is not None:
            uk.draw_panel(screen, box, bg=(*uk.Theme.CARD_BG[:3], 255), border=uk.Theme.CARD_BORDER,
                          border_width=1, radius=8, shadow=False)
            uk.blit_surface(screen, self._icon, box, transient=False)
        else:
            uk.draw_panel(screen, box, bg=(*uk.Theme.CARD_BG[:3], 255), border=uk.Theme.CARD_BORDER,
                          border_width=1, radius=8, shadow=False)
            dash = 4
            xx = box.left + 6
            while xx < box.right - 6:
                uk.draw_line_on(screen, uk.Theme.TEXT_DIM, (xx, box.top + 6),
                                (min(xx + dash, box.right - 6), box.top + 6), 1)
                uk.draw_line_on(screen, uk.Theme.TEXT_DIM, (xx, box.bottom - 6),
                                (min(xx + dash, box.right - 6), box.bottom - 6), 1)
                xx += dash * 2
            no_icon = self.font_tiny.render("no icon", True, uk.Theme.TEXT_DIM)
            uk.blit_surface(screen, no_icon, no_icon.get_rect(center=box.center), transient=True)
        return box

    def _draw_section_label(self, screen, text, x, y):
        surf = self.font_small.render(text.upper(), True, uk.Theme.TEXT_MUTED)
        uk.blit_surface(screen, surf, (x, y), transient=True)
        return surf.get_height()

    def _draw_category_picker(self, screen, x, y, width):
        h = self._draw_section_label(screen, "Category", x, y)
        y += h + 8
        cols = 3
        col_w = (width - (cols - 1) * 10) // cols
        self._category_rects = {}
        mouse_pos = self._logical_mouse_pos
        for i, (cat, label, _slot) in enumerate(CATEGORIES):
            col, row = i % cols, i // cols
            rect = pygame.Rect(x + col * (col_w + 10), y + row * GRID_ROW_H, col_w, GRID_BTN_H)
            self._category_rects[cat] = rect
            self.clickable_rects.append({"rect": rect})
            is_current = (self.data["category"] == cat)
            t = 1.0 if is_current else (0.5 if rect.collidepoint(mouse_pos) else 0.0)
            accent = CATEGORY_ACCENT[cat]
            self._draw_pill_button(screen, rect, t, label, accent)
        rows = math.ceil(len(CATEGORIES) / cols)
        return y + rows * GRID_ROW_H

    def _draw_effect_type_picker(self, screen, x, y, width):
        h = self._draw_section_label(screen, "Effect Type", x, y)
        y += h + 8
        cols = 4
        col_w = (width - (cols - 1) * 10) // cols
        self._effect_type_rects = {}
        mouse_pos = self._logical_mouse_pos
        for i, etype in enumerate(EFFECT_TYPES):
            col, row = (i % cols, 0) if i < cols else (i - cols, 1)
            rect = pygame.Rect(x + col * (col_w + 10), y + row * GRID_ROW_H, col_w, GRID_BTN_H)
            self._effect_type_rects[etype] = rect
            self.clickable_rects.append({"rect": rect})
            is_current = (self.data["effect"].get("type") == etype)
            t = 1.0 if is_current else (0.5 if rect.collidepoint(mouse_pos) else 0.0)
            self._draw_pill_button(screen, rect, t, EFFECT_LABELS[etype], uk.Theme.KI_BLUE)
        rows = 2
        return y + rows * GRID_ROW_H

    def _draw_effect_fields(self, screen, x, y, width):
        specs = self._slider_specs()
        self._slider_rects = {}
        etype = self.data["effect"].get("type", "none")

        if not specs:
            hint_text = {
                "none": "No mechanical effect — used for key/quest items tracked purely by inventory presence.",
                "full_restore": "Fully restores HP and EP. No extra fields.",
            }.get(etype, "")
            if hint_text:
                hint = self.font_small.render(hint_text, True, uk.Theme.TEXT_DIM)
                uk.blit_surface(screen, hint, (x, y), transient=True)
                y += hint.get_height() + 10
            return y

        # Stack stat sliders two-per-row where possible to save vertical
        # space, same footprint the old Slider-widget layout used.
        col_w = (width - 24) // 2
        i = 0
        while i < len(specs):
            spec = specs[i]
            if spec["key"].startswith("stat_") and i + 1 < len(specs) and specs[i + 1]["key"].startswith("stat_"):
                self._draw_slider(screen, x, y, col_w, spec)
                self._draw_slider(screen, x + col_w + 24, y, col_w, specs[i + 1])
                i += 2
            else:
                self._draw_slider(screen, x, y, width, spec)
                i += 1
            y += SLIDER_H
        return y + 6

    def _draw_description_field(self, screen, x, y, width, bottom):
        h = self._draw_section_label(screen, "Description", x, y)
        y += h + 6
        rect = pygame.Rect(x, y, width, max(60, bottom - y))
        self._desc_rect = rect

        editing = self.editing_field == "description"
        border = uk.Theme.GOLD if editing else uk.Theme.CARD_BORDER
        uk.draw_panel(screen, rect, bg=(*uk.Theme.CARD_BG[:3], 255), border=border,
                      border_width=2 if editing else 1, radius=8, shadow=False)
        self.text_field_rects.append(rect)

        text = self.text_input if editing else self.data.get("description", "")
        self._desc_line_h = self.font_medium.size("Ag")[1] + 4
        lines = self._wrap_lines(self.font_medium, text, self._desc_wrap_width()) if text else [("", 0)]
        self._desc_lines = lines

        visible_lines = max(1, rect.h // self._desc_line_h)
        max_scroll = max(0, len(lines) - visible_lines)
        self._desc_scroll = max(0, min(self._desc_scroll, max_scroll))

        old_clip = screen.get_clip()
        screen.set_clip(rect)
        placeholder = not text
        if placeholder:
            ph = self.font_medium.render("Item description...", True, uk.Theme.TEXT_DIM)
            uk.blit_surface(screen, ph, (rect.x + 10, rect.y + 8), transient=True)
        else:
            for row, (line_text, _start) in enumerate(lines[self._desc_scroll:self._desc_scroll + visible_lines + 1]):
                line_surf = self.font_medium.render(line_text, True, uk.Theme.TEXT_PRIMARY)
                line_rect = line_surf.get_rect(x=rect.x + 10, y=rect.y + 6 + row * self._desc_line_h)
                uk.blit_surface(screen, line_surf, line_rect, transient=True)

        if editing:
            self._active_edit_rect = rect
            self._active_edit_font = self.font_medium
            # Selection highlight, drawn per visual line it spans.
            if self._has_text_selection():
                s, e = self._text_selection_range()
                for row, (line_text, line_start) in enumerate(lines):
                    line_end = line_start + len(line_text)
                    if line_end < s or line_start > e:
                        continue
                    vis_row = row - self._desc_scroll
                    if vis_row < 0 or vis_row > visible_lines:
                        continue
                    ls, le = max(s, line_start), min(e, line_end)
                    sx = rect.x + 10 + (self.font_medium.size(line_text[:ls - line_start])[0] if ls > line_start else 0)
                    ex = rect.x + 10 + self.font_medium.size(line_text[:le - line_start])[0]
                    sel_rect = pygame.Rect(sx, rect.y + 6 + vis_row * self._desc_line_h,
                                           max(1, ex - sx), self._desc_line_h)
                    uk.draw_rect_on(screen, (*uk.Theme.KI_BLUE, 90), sel_rect, 0, 0)

            # Caret on whichever visual line currently contains cursor_pos.
            caret_row = 0
            for row, (_line_text, line_start) in enumerate(lines):
                if line_start <= self.cursor_pos:
                    caret_row = row
            line_text, line_start = lines[caret_row]
            vis_row = caret_row - self._desc_scroll
            if 0 <= vis_row <= visible_lines:
                caret_w = self.font_medium.size(line_text[:self.cursor_pos - line_start])[0]
                self._draw_text_caret(screen, rect.x + 10 + caret_w, rect.y + 6 + vis_row * self._desc_line_h,
                                      self._desc_line_h - 2)
        screen.set_clip(old_clip)

    def _draw_editor_panel(self, screen):
        rect = self.editor_rect
        uk.draw_panel(screen, rect, bg=uk.Theme.PANEL_BG, border=uk.Theme.PANEL_BORDER,
                      border_width=1, radius=12, shadow=False)

        if not self.selected_id:
            msg = self.font_medium.render("Select or create an item to begin editing", True, uk.Theme.TEXT_DIM)
            uk.blit_surface(screen, msg, msg.get_rect(center=rect.center), transient=True)
            return

        pad = 20
        x = rect.x + pad
        y = rect.y + pad
        icon_box = self._draw_icon_preview(screen, rect, pad)
        field_width = icon_box.left - x - 16

        h = self._draw_section_label(screen, "Name", x, y)
        y += h + 4
        self._name_field_rect = pygame.Rect(x, y, field_width, FIELD_H)
        self._draw_field_row(screen, self._name_field_rect, self.data.get("name", ""), "name", "Item name")
        id_surf = self.font_tiny.render(f"id: {self.selected_id}", True, uk.Theme.TEXT_DIM)
        uk.blit_surface(screen, id_surf, (x, self._name_field_rect.bottom + 4), transient=True)
        y = max(self._name_field_rect.bottom, icon_box.bottom) + SECTION_GAP - 4

        full_width = rect.w - pad * 2
        y = self._draw_category_picker(screen, x, y, full_width) + SECTION_GAP
        y = self._draw_effect_type_picker(screen, x, y, full_width) + SECTION_GAP
        y = self._draw_effect_fields(screen, x, y, full_width) + 6

        eh = self._draw_section_label(screen, "Effect Text", x, y)
        y += eh + 4
        self._effect_text_field_rect = pygame.Rect(x, y, full_width, FIELD_H)
        self._draw_field_row(screen, self._effect_text_field_rect, self.data.get("effect_text", ""),
                             "effect_text", "e.g. \"Heals 50 HP\"")
        y = self._effect_text_field_rect.bottom + SECTION_GAP

        self._draw_description_field(screen, x, y, full_width, rect.bottom - pad)

    # ------------------------------------------------------------------ action row / new-item row / top-level draw
    def _draw_new_item_row(self, screen):
        row = self._new_item_row_rect
        if not self._new_item_dialog_open:
            # Collapsed: just the plus-icon square button.
            t = self._new_btn_hover_anim
            self._draw_icon_square_btn(screen, self._new_item_btn_rect, t, self._icon_plus_png, uk.Theme.GOLD)
            self.clickable_rects.append({"rect": self._new_item_btn_rect})
            return

        # Expanded: the row becomes an inline text field right where the
        # button was — no confirm/cancel buttons. Enter saves, clicking
        # away cancels (handled in handle_input).
        field_rect = pygame.Rect(row.x, row.y, row.w, row.h)

        self._draw_field_row(screen, field_rect, "", "new_item_id", "item_id")

        self._dialog_field_rect = field_rect

    # ------------------------------------------------------------------ top-level draw

    def draw(self, screen, dt: float = 0.0) -> None:
        if not self.active:
            return

        self.clickable_rects = []
        self.text_field_rects = []
        self._active_edit_rect = None
        self._active_edit_text_x = None
        self._active_edit_font = None

        self._draw_background(screen)
        self._draw_header(screen)
        self._draw_list_panel(screen)
        self._draw_editor_panel(screen)
        self._draw_footer(screen)