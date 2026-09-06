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
        self.item_creator.handle_input(event)
    ...
    self.item_creator.update(dt)
    self.item_creator.draw(self.logical_surface, dt)
    # from DevMenu, add a 'open_item_creator' result -> self.item_creator.toggle()

This tool does NOT touch item sprite art — it only shows whatever icon
already exists at item_icon_path(item_id) (assets/sprites/items/{id}.png,
or .../equipment/{slot}/{id}.png for equip items) as a preview, same
"discover art, attach data" split entity_creator.py uses for enemy/NPC
sprites. Dropping in a new icon PNG separately is still up to you; this
tool is purely the data side (name/description/category/effect).

Reuses widgets/palette from character_creator.py rather than duplicating
them — TextInput, TextArea, Slider, draw_button, render_text_cached, and
the C_* colour constants are the same ones used everywhere else in the dev
tools, so this looks and behaves like the rest of the suite.
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Optional

import pygame

from dev_tools.character_creator import (
    TextInput, TextArea, Slider, draw_button, render_text_cached,
    C_BG, C_PANEL, C_PANEL_DARK, C_BORDER, C_ACCENT, C_ACCENT2,
    C_TEXT, C_TEXT_DIM, C_RED, C_GREEN, C_TAB_ACT, C_TAB_INACT,
    C_HOVER, C_SELECTED, C_DIALOG_BG,
)

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
    """Preview icon at item_icon_path(item_id), scaled to (size, size).
    None if no art exists yet — the editor shows a placeholder box rather
    than blocking on missing art, same as entity_creator's sprite preview."""
    path = BASE_DIR / item_icon_path(item_id)
    if not path.is_file():
        return None
    try:
        raw = pygame.image.load(str(path)).convert_alpha()
        return pygame.transform.scale(raw, (size, size))
    except Exception as e:
        print(f"Error loading item icon ({path}): {e}")
        return None


def draw_label(surf, font, text, x, y, color=C_TEXT_DIM):
    surf.blit(render_text_cached(font, text, color), (x, y))


# ══════════════════════════════════════════════════════════════════════
#  Item list panel (left column) — category tabs + scrollable id list.
#  No manual reordering (unlike EntityList) — items are numerous enough
#  (100+) that alphabetical-within-category is more useful than a
#  hand-picked order, and nothing else in the game reads a saved item
#  display order the way the pause menu reads character_menu.json.
# ══════════════════════════════════════════════════════════════════════
class ItemList:
    ITEM_H = 30

    def __init__(self, rect: pygame.Rect):
        self.rect = rect
        self.ids: list[str] = []
        self.filtered: list[str] = []
        self.custom: set[str] = set()
        self.selected = ""
        self.scroll = 0
        self.category_filter = "all"  # "all" or one of the CATEGORY_* values

    def set_ids(self, ids: list[str], custom: set[str], selected: str = "") -> None:
        self.ids = ids
        self.custom = custom
        self._apply_filter()
        self.selected = selected or (self.filtered[0] if self.filtered else "")
        self.scroll = 0

    def set_filter(self, category: str) -> None:
        self.category_filter = category
        self._apply_filter()
        self.scroll = 0

    def _apply_filter(self) -> None:
        if self.category_filter == "all":
            self.filtered = list(self.ids)
        else:
            self.filtered = [iid for iid in self.ids
                              if ITEMS.get(iid, {}).get("category") == self.category_filter]

    def handle_event(self, event: pygame.event.Event) -> Optional[str]:
        if event.type == pygame.MOUSEWHEEL and self.rect.collidepoint(pygame.mouse.get_pos()):
            self.scroll = max(0, min(len(self.filtered) - 1, self.scroll - event.y))
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if not self.rect.collidepoint(mx, my):
                return None
            for i, iid in enumerate(self.filtered):
                item_y = self.rect.y + (i - self.scroll) * self.ITEM_H
                item_r = pygame.Rect(self.rect.x, item_y, self.rect.w, self.ITEM_H)
                if item_r.collidepoint(mx, my) and self.rect.collidepoint(item_r.center):
                    if self.selected != iid:
                        self.selected = iid
                        return iid
        return None

    def draw(self, surf, font_sm) -> None:
        surf.draw_rect(C_PANEL_DARK, self.rect, border_radius=6)
        surf.draw_rect(C_BORDER, self.rect, 1, border_radius=6)

        old_clip = surf.get_clip()
        surf.set_clip(self.rect)
        visible = max(1, self.rect.h // self.ITEM_H)
        mx, my = pygame.mouse.get_pos()
        for i, iid in enumerate(self.filtered[self.scroll:self.scroll + visible + 1]):
            item_y = self.rect.y + i * self.ITEM_H
            item_r = pygame.Rect(self.rect.x, item_y, self.rect.w, self.ITEM_H)
            hovered = item_r.collidepoint(mx, my)
            is_sel = (iid == self.selected)
            bg = C_SELECTED if is_sel else (C_HOVER if hovered else C_PANEL_DARK)
            surf.draw_rect(bg, item_r)

            is_custom = iid in self.custom
            col = C_ACCENT2 if is_custom else (C_TEXT if is_sel else C_TEXT_DIM)
            name = ITEMS.get(iid, {}).get("name", iid)
            label = f"{name}{' *' if is_custom else ''}"
            txt = render_text_cached(font_sm, label, col)
            surf.blit(txt, (item_r.x + 10, item_r.y + (item_r.h - txt.get_height()) // 2))
        surf.set_clip(old_clip)

        if not self.filtered:
            msg = render_text_cached(font_sm, "No items in this category", C_TEXT_DIM)
            surf.blit(msg, msg.get_rect(center=self.rect.center))

        if len(self.filtered) > visible:
            more = render_text_cached(
                font_sm, f"{len(self.filtered)} items", C_TEXT_DIM)
            surf.blit(more, (self.rect.right - more.get_width() - 6, self.rect.y - more.get_height() - 2))


# ══════════════════════════════════════════════════════════════════════
#  Editor panel — holds live widgets for whichever item is selected.
#  Rebuilt (via load()) every time the selection OR the effect type
#  changes, same lifecycle as EntityEditorPanel.
# ══════════════════════════════════════════════════════════════════════
ROW_H = 34

# Grid layout for the Category / Effect Type button rows. GRID_ROW_H is the
# vertical step between rows (button height + breathing room, was a cramped
# 30/26 = 4px gap before); SECTION_GAP is the gap left between one stacked
# section (label + its button rows) and the next.
GRID_BTN_H  = 26
GRID_ROW_H  = 38
SECTION_GAP = 26


class ItemEditorPanel:
    def __init__(self, rect: pygame.Rect, item_id: str, data: dict):
        self.rect = rect
        self.item_id = item_id
        self.data = data
        self.dirty = False
        self._icon = _load_icon(item_id)
        self._build_widgets()

    # -- shared section layout -----------------------------------------
    def _section_top(self, name: str) -> int:
        """Single source of truth for where each stacked section starts:
        Category buttons -> Effect Type buttons -> effect-specific fields
        (sliders). Previously the effect-field sliders used their own
        hardcoded offset that didn't account for the Category buttons'
        actual height, so they were drawn overlapping the Category row
        instead of below the Effect Type row. Everything that positions
        one of these sections now reads from here instead of recomputing
        the offset by hand."""
        category_y = self.rect.y + 16 + ROW_H * 2 + SECTION_GAP // 2
        effect_type_y = category_y + GRID_ROW_H * 2 + SECTION_GAP
        fx_y = effect_type_y + GRID_ROW_H * 2 + SECTION_GAP
        return {"category": category_y, "effect_type": effect_type_y, "fx": fx_y}[name]

    def _build_widgets(self) -> None:
        lx = self.rect.x + 160
        y0 = self.rect.y + 16
        w = {}

        w["name"] = TextInput(pygame.Rect(lx, y0, 260, TextInput.H), self.data["name"])
        w["effect_text"] = TextInput(pygame.Rect(lx, y0 + ROW_H, 320, TextInput.H),
                                      self.data.get("effect_text", ""))
        # Description rect.y is repositioned every frame in draw() once the
        # effect-specific field block above it knows its own height — same
        # "fixed fields first, description flows below" approach
        # EntityEditorPanel uses for its own Description field.
        w["description"] = TextArea(pygame.Rect(lx, 0, 320, TextArea.H),
                                     self.data.get("description", ""))

        self._build_effect_widgets(w, lx)
        self.widgets = w

    def _build_effect_widgets(self, w: dict, lx: int) -> None:
        """(Re)builds only the effect-specific sliders, keyed with an
        'fx_' prefix so _build_widgets()'s fixed fields (name/effect_text/
        description) never collide with them. Called both from
        _build_widgets() and whenever the effect-type cycle button is
        clicked (see _handle_buttons)."""
        for key in list(w.keys()):
            if key.startswith("fx_"):
                del w[key]

        effect = self.data["effect"]
        etype = effect.get("type", "none")
        y = self._section_top("fx")  # below the Category and Effect Type button grids

        if etype in ("heal_hp", "heal_ep"):
            w["fx_amount"] = Slider(pygame.Rect(lx, y, 220, Slider.H), 1, 3000,
                                     effect.get("amount", 20), step=5)
        elif etype == "revive":
            w["fx_hp_ratio"] = Slider(pygame.Rect(lx, y, 220, Slider.H), 0.05, 1.0,
                                       effect.get("hp_ratio", 0.5), step=0.05, fmt="{:.2f}")
        elif etype == "buff":
            w["fx_duration"] = Slider(pygame.Rect(lx, y, 220, Slider.H), 1, 120,
                                       effect.get("duration", 30.0), step=1, fmt="{:.0f}s")
            y += ROW_H
            stats = effect.get("stats", {})
            for sid in STAT_IDS:
                w[f"fx_stat_{sid}"] = Slider(pygame.Rect(lx, y, 220, Slider.H), -50, 100,
                                              stats.get(sid, 0), step=1)
                y += ROW_H
        elif etype == "equip_stat":
            stats = effect.get("stats", {})
            for sid in STAT_IDS:
                w[f"fx_stat_{sid}"] = Slider(pygame.Rect(lx, y, 220, Slider.H), -50, 100,
                                              stats.get(sid, 0), step=1)
                y += ROW_H
            w["fx_exp_bonus"] = Slider(pygame.Rect(lx, y, 220, Slider.H), 0.0, 1.0,
                                        effect.get("exp_bonus", 0.0), step=0.05, fmt="{:.2f}")
        # 'full_restore' and 'none' need no extra fields.

    def _effect_widgets_bottom(self) -> int:
        """y-coordinate just below the last effect-specific widget, so
        the category/effect-type cycle rows and Description field below
        can be positioned without overlapping — recomputed each frame
        rather than cached since it depends on the widget dict, which
        can change out from under this on an effect-type switch."""
        fx_keys = [k for k in self.widgets if k.startswith("fx_")]
        if not fx_keys:
            return self._section_top("fx")
        return max(self.widgets[k].rect.bottom for k in fx_keys) + 10

    # -- events -----------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> bool:
        changed = False
        for key, wgt in self.widgets.items():
            if wgt.handle_event(event):
                changed = True
                self._apply_widget(key, wgt)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            changed = self._handle_buttons(event.pos) or changed

        if changed:
            self.dirty = True
        return changed

    def _apply_widget(self, key: str, wgt) -> None:
        if key == "name":
            self.data["name"] = wgt.value
        elif key == "effect_text":
            self.data["effect_text"] = wgt.value
        elif key == "description":
            self.data["description"] = wgt.value
        elif key == "fx_amount":
            self.data["effect"]["amount"] = int(wgt.value)
        elif key == "fx_hp_ratio":
            self.data["effect"]["hp_ratio"] = round(wgt.value, 2)
        elif key == "fx_duration":
            self.data["effect"]["duration"] = round(wgt.value, 1)
        elif key == "fx_exp_bonus":
            self.data["effect"]["exp_bonus"] = round(wgt.value, 2)
        elif key.startswith("fx_stat_"):
            sid = key[len("fx_stat_"):]
            self.data["effect"].setdefault("stats", {})[sid] = int(wgt.value)

    def _handle_buttons(self, pos) -> bool:
        changed = False
        for rect, cat in self._category_rects():
            if rect.collidepoint(pos) and self.data["category"] != cat:
                self.data["category"] = cat
                slot = CATEGORY_SLOT[cat]
                if slot:
                    self.data["slot"] = slot
                else:
                    self.data.pop("slot", None)
                changed = True

        for rect, etype in self._effect_type_rects():
            if rect.collidepoint(pos) and self.data["effect"].get("type") != etype:
                self.data["effect"] = _default_effect_for_type(etype, self.data["effect"])
                self._build_effect_widgets(self.widgets, self.rect.x + 160)
                changed = True

        return changed

    # -- cycle-button layouts ----------------------------------------
    def _category_rects(self):
        """3 per row, 2 rows — 6 categories total."""
        lx = self.rect.x + 160
        y = self._section_top("category")
        out = []
        for i, (cat, label, _slot) in enumerate(CATEGORIES):
            col, row = i % 3, i // 3
            r = pygame.Rect(lx + col * 132, y + row * GRID_ROW_H, 124, GRID_BTN_H)
            out.append((r, cat))
        return out

    def _effect_type_rects(self):
        """4 + 3 — 7 effect types total. Positioned below the category
        buttons (2 rows) with a gap for their row label."""
        lx = self.rect.x + 160
        y = self._section_top("effect_type")
        out = []
        for i, etype in enumerate(EFFECT_TYPES):
            col, row = (i % 4, 0) if i < 4 else (i - 4, 1)
            r = pygame.Rect(lx + col * 126, y + row * GRID_ROW_H, 118, GRID_BTN_H)
            out.append((r, etype))
        return out

    # -- draw ---------------------------------------------------------
    def draw(self, surf, font, font_sm, dt) -> None:
        lx = self.rect.x + 20
        y0 = self.rect.y + 16

        draw_label(surf, font_sm, "Name", lx, y0 + 6)
        self.widgets["name"].draw(surf, font_sm, dt)

        draw_label(surf, font_sm, "Effect Text", lx, y0 + ROW_H + 6)
        self.widgets["effect_text"].draw(surf, font_sm, dt)

        self._draw_preview(surf, font_sm)
        self._draw_category_row(surf, font_sm)
        self._draw_effect_section(surf, font, font_sm, dt)

    def _draw_preview(self, surf, font_sm) -> None:
        box_w = 140
        px = self.rect.right - box_w - 20
        py = self.rect.y + 12
        draw_label(surf, font_sm, "Icon", px, py)
        py += 20
        rect = pygame.Rect(px, py, 96, 96)
        if self._icon is not None:
            surf.blit(self._icon, rect)
            surf.draw_rect(C_BORDER, rect, 1)
        else:
            surf.draw_rect(C_PANEL_DARK, rect)
            dash = 4
            xx = rect.left
            while xx < rect.right:
                surf.draw_line(C_TEXT_DIM, (xx, rect.top), (min(xx + dash, rect.right), rect.top))
                surf.draw_line(C_TEXT_DIM, (xx, rect.bottom - 1), (min(xx + dash, rect.right), rect.bottom - 1))
                xx += dash * 2
            label = render_text_cached(font_sm, "no icon", C_TEXT_DIM)
            surf.blit(label, label.get_rect(center=rect.center))
            path_txt = render_text_cached(font_sm, item_icon_path(self.item_id), C_TEXT_DIM)
            surf.blit(path_txt, (px, py + 100))

    def _draw_category_row(self, surf, font_sm) -> None:
        lx = self.rect.x + 160
        y = self._section_top("category")
        draw_label(surf, font_sm, "Category", self.rect.x + 20, y + 5)
        mx, my = pygame.mouse.get_pos()
        for rect, cat in self._category_rects():
            hovered = rect.collidepoint(mx, my)
            is_current = (self.data["category"] == cat)
            draw_button(surf, font_sm, rect, CATEGORY_LABELS[cat],
                       color=C_ACCENT if is_current else C_TEXT_DIM, hover=hovered)

    def _draw_effect_section(self, surf, font, font_sm, dt) -> None:
        lx = self.rect.x + 160
        y = self._section_top("effect_type")
        draw_label(surf, font_sm, "Effect Type", self.rect.x + 20, y + 5)
        mx, my = pygame.mouse.get_pos()
        for rect, etype in self._effect_type_rects():
            hovered = rect.collidepoint(mx, my)
            is_current = (self.data["effect"].get("type") == etype)
            draw_button(surf, font_sm, rect, EFFECT_LABELS[etype],
                       color=C_ACCENT if is_current else C_TEXT_DIM, hover=hovered)

        fx_y = self._section_top("fx")
        etype = self.data["effect"].get("type", "none")
        stat_row_labels = {
            "fx_amount":     "Amount",
            "fx_hp_ratio":   "HP Ratio",
            "fx_duration":   "Duration",
            "fx_exp_bonus":  "XP Bonus",
        }
        for key, wgt in self.widgets.items():
            if not key.startswith("fx_"):
                continue
            if key.startswith("fx_stat_"):
                sid = key[len("fx_stat_"):]
                label = STAT_LABELS[sid]
            else:
                label = stat_row_labels.get(key, key)
            draw_label(surf, font_sm, label, self.rect.x + 20, wgt.rect.y + 2)
            wgt.draw(surf, font_sm)

        if etype == "none":
            hint = render_text_cached(font_sm,
                "No mechanical effect — used for key/quest items tracked purely by inventory presence.",
                C_TEXT_DIM)
            surf.blit(hint, (lx, fx_y))
        elif etype == "full_restore":
            hint = render_text_cached(font_sm, "Fully restores HP and EP. No extra fields.", C_TEXT_DIM)
            surf.blit(hint, (lx, fx_y))

        # ── Description — flows below whatever the effect section ended up needing.
        desc_y = self._effect_widgets_bottom()
        if etype in ("none", "full_restore"):
            desc_y = max(desc_y, fx_y + 26)
        draw_label(surf, font_sm, "Description", self.rect.x + 20, desc_y + 6)
        desc_h = max(TextArea.H, self.rect.bottom - (desc_y + 26) - 16)
        self.widgets["description"].rect = pygame.Rect(lx, desc_y + 26, self.rect.w - 220, desc_h)
        self.widgets["description"].draw(surf, font_sm, dt)


# ══════════════════════════════════════════════════════════════════════
#  Top-level overlay — same lifecycle contract as CharacterCreator /
#  EntityCreator: toggle() / handle_input(event) / update(dt) /
#  draw(surface, dt), lives inside the host game's loop.
# ══════════════════════════════════════════════════════════════════════
HEADER_H = 44
FOOTER_H = 52
LIST_W   = 240
PAD      = 8


class ItemCreator:
    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.active = False

        pygame.font.init()
        try:
            self.font    = pygame.font.SysFont("segoeui,dejavusans,arial", 16)
            self.font_sm = pygame.font.SysFont("segoeui,dejavusans,arial", 13)
            self.font_hd = pygame.font.SysFont("segoeui,dejavusans,arial", 20, bold=True)
        except Exception:
            self.font = self.font_sm = self.font_hd = pygame.font.Font(None, 18)

        self._build_layout()

        self.ids: list[str] = []
        self.custom: set[str] = set()
        self.item_list = ItemList(self.list_rect)
        self.selected_id = ""
        self.data = _new_item_data("")
        self.editor: Optional[ItemEditorPanel] = None

        self.status_msg = ""
        self.status_col = C_TEXT_DIM
        self.status_timer = 0.0

        self.dialog = None  # non-blocking modal, same shape as character_creator's / entity_creator's

    # -- layout ---------------------------------------------------------
    def _build_layout(self) -> None:
        sw, sh = self.screen_width, self.screen_height
        tab_w = min(140, (sw - PAD * 2 - LIST_W - PAD) // (len(CATEGORIES) + 1))
        self.filter_tab_rects = []
        for i in range(len(CATEGORIES) + 1):  # +1 for "All"
            self.filter_tab_rects.append(
                pygame.Rect(PAD + i * (tab_w + 4), PAD, tab_w, HEADER_H - PAD * 2))

        self.list_rect = pygame.Rect(PAD, HEADER_H + PAD, LIST_W,
                                     sh - HEADER_H - FOOTER_H - PAD * 2)
        editor_x = LIST_W + PAD * 2
        self.editor_rect = pygame.Rect(editor_x, HEADER_H + PAD,
                                       sw - editor_x - PAD,
                                       sh - HEADER_H - FOOTER_H - PAD * 2)
        self.btn_save   = pygame.Rect(sw - 230, sh - FOOTER_H + 10, 100, 32)
        self.btn_delete = pygame.Rect(sw - 120, sh - FOOTER_H + 10, 100, 32)
        self.btn_new    = pygame.Rect(PAD + 4, sh - FOOTER_H + 10, LIST_W - 8, 32)

    # -- lifecycle --------------------------------------------------------
    def toggle(self) -> None:
        self.active = not self.active
        if self.active:
            self._refresh_list()

    def _refresh_list(self) -> None:
        self.ids = discover_all_item_ids()
        self.custom = discover_custom_item_ids()
        self.item_list.set_ids(self.ids, self.custom, self.selected_id)
        self.selected_id = self.item_list.selected
        if self.selected_id:
            self._load_item(self.selected_id)
        else:
            self.editor = None

    def _load_item(self, item_id: str) -> None:
        self.selected_id = item_id
        self.data = _load_item_data(item_id)
        self.editor = ItemEditorPanel(self.editor_rect, item_id, self.data)

    def _switch_item(self, item_id: str) -> None:
        self._load_item(item_id)

    def _set_status(self, msg: str, ok: bool = True) -> None:
        self.status_msg = msg
        self.status_col = C_GREEN if ok else C_RED
        self.status_timer = 3.0

    # -- save / delete / new ----------------------------------------------
    def _do_save(self) -> None:
        if not self.editor or not self.selected_id:
            return
        data = copy.deepcopy(self.editor.data)
        if data["effect"].get("type") == "none":
            data["effect"] = {}  # 'none' isn't a real item_effects.py type — omit it entirely
        save_item_override(self.selected_id, data)
        self.custom.add(self.selected_id)
        if self.selected_id not in self.ids:
            self.ids = discover_all_item_ids()
            self.item_list.set_ids(self.ids, self.custom, self.selected_id)
        self.editor.dirty = False
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
        next_selected = self.selected_id if still_exists else (self.ids[0] if self.ids else "")
        self.item_list.set_ids(self.ids, self.custom, next_selected)
        if self.item_list.selected:
            self._load_item(self.item_list.selected)
        else:
            self.editor = None
            self.selected_id = ""

    def _open_new_dialog(self) -> None:
        field = TextInput(pygame.Rect(0, 0, 320, 32), "")
        field.active = True
        self.dialog = {"field": field, "error": ""}

    def _do_create(self, new_id: str) -> None:
        new_id = new_id.strip().lower().replace(" ", "_")
        if not new_id:
            return
        if new_id in ITEMS:
            self.dialog["error"] = f"'{new_id}' already exists."
            return
        save_item_override(new_id, _new_item_data(new_id))
        self.custom.add(new_id)
        self.ids = discover_all_item_ids()
        self.item_list.set_ids(self.ids, self.custom, new_id)
        self._switch_item(new_id)
        self.dialog = None
        self._set_status(f"Created {new_id} — set its category and effect, then Save")

    # -- input --------------------------------------------------------------
    def handle_input(self, event: pygame.event.Event):
        if not self.active:
            return None

        if self.dialog is not None:
            self._handle_dialog_event(event)
            return None

        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.active = False
            return None

        if event.type == pygame.KEYDOWN and event.key == pygame.K_s \
                and (event.mod & pygame.KMOD_CTRL):
            self._do_save()
            return None

        for rect, cat in zip(self.filter_tab_rects, ["all"] + [c for c, _, _ in CATEGORIES]):
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and rect.collidepoint(event.pos):
                self.item_list.set_filter(cat)
                return None

        new_sel = self.item_list.handle_event(event)
        if new_sel:
            self._switch_item(new_sel)
            return None

        if self.editor:
            self.editor.handle_event(event)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.btn_save.collidepoint(event.pos):
                self._do_save()
            elif self.btn_delete.collidepoint(event.pos):
                self._do_delete()
            elif self.btn_new.collidepoint(event.pos):
                self._open_new_dialog()
        return None

    def _handle_dialog_event(self, event) -> None:
        d = self.dialog
        d["field"].handle_event(event)
        if event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN:
            self._do_create(d["field"].value)
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.dialog = None
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            ok_r = d.get("ok_rect")
            cancel_r = d.get("cancel_rect")
            if ok_r and ok_r.collidepoint(event.pos):
                self._do_create(d["field"].value)
            elif cancel_r and cancel_r.collidepoint(event.pos):
                self.dialog = None

    # -- frame --------------------------------------------------------------
    def update(self, dt: float) -> None:
        if not self.active:
            return
        if self.status_timer > 0:
            self.status_timer -= dt
            if self.status_timer <= 0:
                self.status_msg = ""

    def draw(self, screen: pygame.Surface, dt: float) -> None:
        if not self.active:
            return
        sw, sh = self.screen_width, self.screen_height
        overlay = pygame.Surface((sw, sh))
        overlay.fill(C_BG)
        screen.blit(overlay, (0, 0))

        labels = ["All"] + [label for _, label, _ in CATEGORIES]
        cats = ["all"] + [c for c, _, _ in CATEGORIES]
        for rect, label, cat in zip(self.filter_tab_rects, labels, cats):
            active = (self.item_list.category_filter == cat)
            bg = C_TAB_ACT if active else C_TAB_INACT
            screen.draw_rect(bg, rect, border_radius=6)
            screen.draw_rect(C_ACCENT if active else C_BORDER, rect, 1, border_radius=6)
            txt = render_text_cached(self.font_sm, label, C_TEXT if active else C_TEXT_DIM)
            # Shrink-to-fit so longer labels ("Equip: Accessory") don't
            # spill past narrow tabs on smaller screen widths.
            if txt.get_width() > rect.w - 8:
                scale = (rect.w - 8) / txt.get_width()
                txt = pygame.transform.scale(
                    txt, (max(1, int(txt.get_width() * scale)), max(1, int(txt.get_height() * scale))))
            screen.blit(txt, txt.get_rect(center=rect.center))

        self.item_list.draw(screen, self.font_sm)

        screen.draw_rect(C_PANEL, self.editor_rect, border_radius=6)
        screen.draw_rect(C_BORDER, self.editor_rect, 1, border_radius=6)
        if self.editor:
            old_clip = screen.get_clip()
            screen.set_clip(self.editor_rect)
            self.editor.draw(screen, self.font, self.font_sm, dt)
            screen.set_clip(old_clip)
        elif not self.ids:
            msg = render_text_cached(self.font_sm, "No items found in core/items.py's ITEMS table", C_TEXT_DIM)
            screen.blit(msg, msg.get_rect(center=self.editor_rect.center))

        draw_button(screen, self.font_sm, self.btn_save, "Save",
                   color=C_ACCENT2 if (self.editor and self.editor.dirty) else C_ACCENT)
        draw_button(screen, self.font_sm, self.btn_delete, "Delete", color=C_RED)
        draw_button(screen, self.font_sm, self.btn_new, "+ New Item")

        if self.status_msg:
            txt = render_text_cached(self.font_sm, self.status_msg, self.status_col)
            screen.blit(txt, (self.editor_rect.x, self.screen_height - FOOTER_H + 18))

        if self.dialog is not None:
            self._draw_dialog(screen, dt)

    def _draw_dialog(self, screen, dt) -> None:
        sw, sh = self.screen_width, self.screen_height
        dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 160))
        screen.blit(dim, (0, 0))

        box = pygame.Rect(sw // 2 - 220, sh // 2 - 70, 440, 140)
        screen.draw_rect(C_DIALOG_BG, box, border_radius=8)
        screen.draw_rect(C_ACCENT, box, 1, border_radius=8)

        prompt = "New item id (e.g. 'power_pole')"
        txt = render_text_cached(self.font_sm, prompt, C_TEXT)
        screen.blit(txt, (box.x + 20, box.y + 16))

        field = self.dialog["field"]
        field.rect = pygame.Rect(box.x + 20, box.y + 46, box.w - 40, 32)
        field.draw(screen, self.font_sm, dt)

        if self.dialog.get("error"):
            err = render_text_cached(self.font_sm, self.dialog["error"], C_RED)
            screen.blit(err, (box.x + 20, box.y + 82))

        ok_r = pygame.Rect(box.right - 200, box.bottom - 44, 90, 30)
        cancel_r = pygame.Rect(box.right - 100, box.bottom - 44, 84, 30)
        draw_button(screen, self.font_sm, ok_r, "Create", color=C_GREEN)
        draw_button(screen, self.font_sm, cancel_r, "Cancel", color=C_RED)
        self.dialog["ok_rect"] = ok_r
        self.dialog["cancel_rect"] = cancel_r