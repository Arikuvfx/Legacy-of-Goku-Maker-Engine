"""
entity_creator.py  –  Dev-menu enemy / NPC entity editor
==========================================================
Sister tool to character_creator.py. Where character_creator defines
*player* characters (assets/characters/{id}.json), this defines
*enemies*, *bosses*, and *NPCs* — id, stats, level, XP reward, zeni drop
table, AI type, dialogue — saved as JSON so new entities can be added
without touching enemy.py / npc.py / entity_editor.py at all.

Wire-up (game.py), mirrors character_creator's:
    from dev_tools import entity_creator
    self.entity_creator = entity_creator.EntityCreator(SCREEN_WIDTH, SCREEN_HEIGHT)
    # in the event loop, same pattern as self.character_creator:
    if self.entity_creator.active:
        self.entity_creator.handle_input(event)
    ...
    self.entity_creator.update(dt)
    self.entity_creator.draw(self.logical_surface, dt)
    # from DevMenu, add a 'open_entity_creator' result -> self.entity_creator.toggle()

Expected folder conventions
----------------------------
assets/
  sprites/
    enemy/
      {enemy_id}/                 <- one folder per enemy/boss
        variants/{variant}/...    <- optional colour/skin variants (see
                                     entity_editor._scan_npc_variants,
                                     same convention, reused here)
    npc/
      {npc_id}/
        variants/{variant}/...
  enemies/
    {enemy_id}.json                <- stats/AI/rewards, written here on Save
  npcs/
    {npc_id}.json                  <- behaviour/dialogue defaults, written here

This tool does NOT touch sprite art — it only discovers which ids exist
(same folder scan entity_editor.py already does for NPCs) and lets you
attach data to them. Dropping a new assets/sprites/enemies/{id}/ folder is
enough for a new entity to show up here as "unconfigured"; Save gives it
a config and entity_editor.py's palette picks it up automatically.

Reuses widgets/palette from character_creator.py rather than
duplicating them — TextInput, Slider, draw_button, render_text_cached,
and the C_* colour constants are the same ones used everywhere else in
the dev tools, so this looks and behaves like the rest of the suite.
"""

from __future__ import annotations

import json
import os
import sys
import copy
from pathlib import Path
from typing import Optional

import pygame

from dev_tools.character_creator import (
    TextInput, TextArea, Slider, draw_button, render_text_cached,
    C_BG, C_PANEL, C_PANEL_DARK, C_BORDER, C_ACCENT, C_ACCENT2,
    C_TEXT, C_TEXT_DIM, C_RED, C_GREEN, C_TAB_ACT, C_TAB_INACT,
    C_HOVER, C_SELECTED, C_DIALOG_BG,
)

# ──────────────────────────────────────────────────────────────────────
#  Paths — same BASE_DIR anchoring trick as character_creator.py, so this
#  tool also works correctly once packaged (see that file's long comment).
# ──────────────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

ENEMY_SPRITES_ROOT = BASE_DIR / "assets/sprites/enemies"
BOSS_SPRITES_ROOT  = ENEMY_SPRITES_ROOT / "boss"
NPC_SPRITES_ROOT    = BASE_DIR / "assets/sprites/npc"
ENEMIES_DIR         = BASE_DIR / "assets/enemies"
NPCS_DIR             = BASE_DIR / "assets/npcs"

KIND_ENEMY = "enemy"
KIND_NPC   = "npc"

# ──────────────────────────────────────────────────────────────────────
#  Default config skeletons
# ──────────────────────────────────────────────────────────────────────
DEFAULT_ENEMY_CONFIG: dict = {
    "id":            "",
    "display_name":  "",
    # Freeform prose shown in the Scouter Data description panel (see
    # ui/scouter_menu.py's _get_entity_description / _draw_data_description).
    "description":   "",
    "entity_type":   "enemy",     # "enemy" | "boss"
    "enemy_category": "melee",    # "melee" | "shooter"
    "ai_type":       "easy",      # "easy" | "advanced"
    "shooter_style": "bomb",      # "bomb" | "bullet" | "rocket" | "kiblast" — shooter only
    # Same STR/POW/END/SPD scheme character_creator.py uses for the player
    # (see that file's stats block and game.py's _stat_map) — enemies just
    # skip the KI resource (max_ki/ki_regen), since they don't spend energy,
    # they only need the damage stat.
    "stats": {
        "max_hp":    150,
        "strength":   10,   # STR — melee attack damage (enemy_category == "melee")
        "power":      10,   # POW — super/ranged attack damage: ki-blast, bomb,
                             #       bullet, rocket (enemy_category == "shooter")
        "defense":    20,   # END — mitigates incoming melee damage
        "speed":       1,
    },
    "level":      1,
    "xp_reward": 25,
    "zeni_pool": "tier1",   # core.zeni_system pool key this enemy rolls on death
    "width":  32,           # sprite/frame size (spritesheet slicing)
    "height": 32,

    # -- Boss-only fields below. Regular enemies leave these at their
    #    defaults, which reproduce the old hardcoded Enemy.__init__
    #    behaviour exactly (hitbox == frame size, no attack_range/shadow
    #    override, standard awareness/forget range). BossEnemy is the only
    #    thing that reads them. --------------------------------------
    "hitbox_width":  32,     # collision box, independent of sprite frame size
    "hitbox_height": 32,
    "attack_range":     15,  # None/omitted -> fall back to the category/shooter-style preset
    "projectile_sprite": "", # "" -> use shooter_style's default art; set to override (e.g. 'kiblast')
    "shadow_size":      "small",
    "shadow_width":     32,
    "shadow_y_offset":  0,
    "awareness_range":  100,
    "forget_range":     210,
}

DEFAULT_NPC_CONFIG: dict = {
    "id":                "",
    "display_name":      "",
    # Freeform prose shown in the Scouter Data description panel (see
    # ui/scouter_menu.py's _get_entity_description / _draw_data_description).
    "description":       "",
    "npc_type":          "static",   # "static" | "moving"
    "speed":             1.5,
    "interaction_range": 50,
    "dialogue": {
        "dialogues":        ["Hello, traveler!"],
        "trigger_limit":    -1,      # -1 = unlimited
        "after_limit_text": "I have nothing more to say.",
        "random_order":     False,
        "give_item":        None,
    },
}


def _defaults_for(kind: str) -> dict:
    return copy.deepcopy(DEFAULT_ENEMY_CONFIG if kind == KIND_ENEMY else DEFAULT_NPC_CONFIG)


def _dirs_for(kind: str) -> tuple[Path, Path]:
    """Return (sprites_root, data_dir) for *kind*."""
    if kind == KIND_ENEMY:
        return ENEMY_SPRITES_ROOT, ENEMIES_DIR
    return NPC_SPRITES_ROOT, NPCS_DIR


# ══════════════════════════════════════════════════════════════════════
#  Filesystem scanning / persistence
# ══════════════════════════════════════════════════════════════════════

def discover_sprite_ids(kind: str) -> list[str]:
    """Every sub-folder of the kind's sprite root — same scan entity_editor
    already does for NPCs (see _build_npc_catalogue). An id showing up
    here doesn't mean it has a config yet; see discover_configured_ids().

    For enemies, 'boss' is excluded — it's the boss sprite subfolder
    (assets/sprites/enemies/boss/), not an entity id itself. Its contents
    aren't auto-listed here; an entity only shows up once it's actually
    created (named) in the editor, matching against that folder by name
    once its entity_type is set to boss."""
    root, _ = _dirs_for(kind)
    if not root.exists():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and not d.name.startswith(".") and d.name != "boss")


def discover_configured_ids(kind: str) -> list[str]:
    """Ids that already have a saved JSON config, regardless of whether
    their sprite folder still exists (keeps working if art gets moved)."""
    _, data_dir = _dirs_for(kind)
    if not data_dir.exists():
        return []
    return sorted(p.stem for p in data_dir.glob("*.json"))


def discover_all_ids(kind: str) -> list[str]:
    """Union of sprite-folder ids and configured ids, so newly-dropped art
    shows up as "unconfigured" instead of being invisible."""
    return sorted(set(discover_sprite_ids(kind)) | set(discover_configured_ids(kind)))


def scan_variants(kind: str, entity_id: str, entity_type: str = "") -> list[str]:
    """Sub-folders of {sprites_root}/{entity_id}/variants/, 'default' always
    first — same convention as entity_editor._scan_npc_variants().

    For enemies flagged as bosses (entity_type == "boss"), the sprite root
    is assets/sprites/enemies/boss/ instead of the flat enemies/ root —
    see BOSS_SPRITES_ROOT. Regular enemies are unaffected."""
    root, _ = _dirs_for(kind)
    if kind == KIND_ENEMY and entity_type == "boss":
        root = BOSS_SPRITES_ROOT
    variants_dir = root / entity_id / "variants"
    out = ["default"]
    if variants_dir.is_dir():
        out += sorted(d.name for d in variants_dir.iterdir()
                      if d.is_dir() and d.name != "default")
    return out


def load_config(kind: str, entity_id: str) -> dict:
    """Load {entity_id}.json, merging in any keys missing from an older
    save (new fields added to DEFAULT_*_CONFIG later) so old files don't
    break when the schema grows — same merge strategy character_creator's
    load_config() uses for "stats"/"attacks"."""
    _, data_dir = _dirs_for(kind)
    path = data_dir / f"{entity_id}.json"
    cfg = _defaults_for(kind)
    cfg["id"] = entity_id
    cfg["display_name"] = entity_id.replace("_", " ").title()
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k in ("stats", "dialogue") and isinstance(v, dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v

            # -- Migrate pre-STR/POW-split saves ------------------------
            # Old schema had a single "power" stat doing double duty as
            # melee damage *and* base ki-blast/projectile damage. A save
            # from before this split has "power" but no "strength" — seed
            # both new stats from the old value so damage doesn't silently
            # change until it's re-tuned in the editor.
            if kind == KIND_ENEMY:
                legacy_stats = saved.get("stats", {})
                if isinstance(legacy_stats, dict) and "power" in legacy_stats \
                        and "strength" not in legacy_stats:
                    legacy_power = legacy_stats["power"]
                    cfg["stats"]["strength"] = legacy_power
                    cfg["stats"]["power"] = legacy_power
        except Exception as e:
            print(f"Error loading {path}: {e}")
    return cfg


def save_config(kind: str, cfg: dict) -> None:
    _, data_dir = _dirs_for(kind)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{cfg['id']}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def delete_config(kind: str, entity_id: str) -> None:
    _, data_dir = _dirs_for(kind)
    path = data_dir / f"{entity_id}.json"
    if path.exists():
        path.unlink()


# ══════════════════════════════════════════════════════════════════════
#  Small helpers
# ══════════════════════════════════════════════════════════════════════

def _read_sprite_size(folder: Path, default: int = 32) -> tuple[int, int]:
    """Read frame dimensions from {folder}/sprite_size.txt (format: '48x48').
    Same convention/format as character_creator._read_sprite_size(). Falls
    back to (default, default) if missing/unreadable."""
    p = folder / "sprite_size.txt"
    if p.exists():
        try:
            w, h = p.read_text().strip().lower().split("x")
            return int(w), int(h)
        except Exception:
            pass
    return default, default


def _load_preview_sprite(kind: str, entity_id: str, variant_type: str, entity_type: str = "", size: int = 96):
    """Load the idle-down frame for a sprite-preview thumbnail.

    Tries a handful of common sheet filenames under the entity's variant
    folder (falling back to the flat, non-variant folder), sliced the same
    way entity_editor.py's palette thumbnails are (row 0 = idle-down on a
    4-directional sheet) — so what's shown here matches what shows up once
    the entity is placed in the room editor. Returns a Surface scaled to
    (size, size), or None if no matching art exists yet.

    Enemies flagged as bosses (entity_type == "boss") are searched under
    BOSS_SPRITES_ROOT (assets/sprites/enemies/boss/) instead of the flat
    enemies/ root — only when the config says so, not as a blind fallback.
    """
    if kind == KIND_ENEMY and entity_type == "boss":
        root = BOSS_SPRITES_ROOT
    else:
        root = ENEMY_SPRITES_ROOT if kind == KIND_ENEMY else NPC_SPRITES_ROOT
    entity_dir = root / entity_id
    search_dirs = [entity_dir / "variants" / variant_type, entity_dir]
    filenames = ["idle.png", "idle_down.png", "walk_down.png", "sprite.png"]

    path = None
    found_dir = None
    for d in search_dirs:
        for fname in filenames:
            candidate = d / fname
            if candidate.is_file():
                path = candidate
                found_dir = d
                break
        if path:
            break
    if path is None:
        return None

    # Frame size is read from sprite_size.txt (same 'WxH' convention as
    # character_creator.py's player sprites) rather than assumed to be
    # square — most entity sprite sheets are taller than they are wide,
    # so guessing frame_w == frame_h sliced the wrong region and either
    # threw on subsurface() (silently swallowed below, showing "no sprite
    # yet") or rendered a garbled/cropped thumbnail.
    frame_w, frame_h = _read_sprite_size(found_dir)

    try:
        sheet = pygame.image.load(str(path)).convert_alpha()
        sheet_w, sheet_h = sheet.get_size()
        # Clamp in case sprite_size.txt is stale/wrong for this sheet.
        frame_w = min(frame_w, sheet_w)
        frame_h = min(frame_h, sheet_h // 4 if sheet_h >= 4 else sheet_h)
        frame = sheet.subsurface(pygame.Rect(0, 0, frame_w, frame_h))
        return pygame.transform.scale(frame, (size, size))
    except Exception as e:
        print(f"Error loading preview sprite ({path}): {e}")
        return None


def draw_label(surf, font, text, x, y, color=C_TEXT_DIM):
    surf.blit(render_text_cached(font, text, color), (x, y))


def _cycle_button(surf, font, rect, label, hovered=False):
    draw_button(surf, font, rect, label, hover=hovered)


# ══════════════════════════════════════════════════════════════════════
#  Entity list panel (left column) — same interaction pattern as
#  character_creator.CharacterList, minus manual reordering (not needed
#  here; enemies/NPCs don't have a menu order to preserve).
# ══════════════════════════════════════════════════════════════════════
class EntityList:
    ITEM_H = 32

    def __init__(self, rect: pygame.Rect):
        self.rect = rect
        self.ids: list[str] = []
        self.configured: set[str] = set()
        self.selected = ""
        self.scroll = 0

    def set_ids(self, ids: list[str], configured: set[str], selected: str = "") -> None:
        self.ids = ids
        self.configured = configured
        self.selected = selected or (ids[0] if ids else "")

    def handle_event(self, event: pygame.event.Event) -> Optional[str]:
        if event.type == pygame.MOUSEWHEEL and self.rect.collidepoint(pygame.mouse.get_pos()):
            self.scroll = max(0, min(len(self.ids) - 1, self.scroll - event.y))
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if not self.rect.collidepoint(mx, my):
                return None
            for i, eid in enumerate(self.ids):
                item_y = self.rect.y + (i - self.scroll) * self.ITEM_H
                item_r = pygame.Rect(self.rect.x, item_y, self.rect.w, self.ITEM_H)
                if item_r.collidepoint(mx, my) and self.rect.collidepoint(item_r.center):
                    if self.selected != eid:
                        self.selected = eid
                        return eid
        return None

    def draw(self, surf, font, font_sm) -> None:
        pygame.draw.rect(surf, C_PANEL_DARK, self.rect, border_radius=6)
        pygame.draw.rect(surf, C_BORDER, self.rect, 1, border_radius=6)

        old_clip = surf.get_clip()
        surf.set_clip(self.rect)
        visible = max(1, self.rect.h // self.ITEM_H)
        mx, my = pygame.mouse.get_pos()
        for i, eid in enumerate(self.ids[self.scroll:self.scroll + visible + 1]):
            real_i = self.scroll + i
            item_y = self.rect.y + i * self.ITEM_H
            item_r = pygame.Rect(self.rect.x, item_y, self.rect.w, self.ITEM_H)
            hovered = item_r.collidepoint(mx, my)
            is_sel = (eid == self.selected)
            bg = C_SELECTED if is_sel else (C_HOVER if hovered else C_PANEL_DARK)
            pygame.draw.rect(surf, bg, item_r)

            unconfigured = eid not in self.configured
            col = C_ACCENT2 if unconfigured else (C_TEXT if is_sel else C_TEXT_DIM)
            label = eid if not unconfigured else f"{eid} (new)"
            txt = render_text_cached(font_sm, label, col)
            surf.blit(txt, (item_r.x + 10, item_r.y + (item_r.h - txt.get_height()) // 2))
        surf.set_clip(old_clip)

        if not self.ids:
            msg = render_text_cached(font_sm, "No sprite folders found", C_TEXT_DIM)
            surf.blit(msg, msg.get_rect(center=self.rect.center))


# ══════════════════════════════════════════════════════════════════════
#  Tab content — a handful of free functions rather than a class per tab;
#  each returns the list of live widgets it drew (for hit-testing) and
#  mutates cfg in place when a widget changes, mirroring
#  CharacterEditor's per-tab draw methods but flattened since this schema
#  is much smaller than the player one.
# ══════════════════════════════════════════════════════════════════════
ROW_H = 34


class EntityEditorPanel:
    """Holds live widgets for whichever entity is selected and draws /
    handles whichever tab is active. Rebuilt (via load()) every time the
    selection changes, same lifecycle as CharacterEditor."""

    def __init__(self, rect: pygame.Rect, kind: str, entity_id: str, cfg: dict):
        self.rect = rect
        self.kind = kind
        self.entity_id = entity_id
        self.cfg = cfg
        self.dirty = False
        self._build_widgets()
        self._load_preview()

    def _load_preview(self) -> None:
        """Load an idle-down thumbnail for every variant folder this entity
        has (see scan_variants()), so the panel can show what it'll actually
        look like in-game. Missing art just means a 'no sprite yet' box —
        this never blocks editing/saving stats.

        For enemies, entity_type ('enemy' vs 'boss') decides whether we
        look in the flat enemies/ root or the boss/ subfolder — see
        BOSS_SPRITES_ROOT."""
        entity_type = self.cfg.get('entity_type', '') if self.kind == KIND_ENEMY else ''
        self.preview_variants = []
        for variant_type in scan_variants(self.kind, self.entity_id, entity_type):
            sprite = _load_preview_sprite(self.kind, self.entity_id, variant_type, entity_type, size=96)
            self.preview_variants.append({
                'type': variant_type,
                'name': 'Default' if variant_type == 'default' else variant_type.replace('_', ' ').title(),
                'sprite': sprite,
            })

    def _build_widgets(self) -> None:
        lx = self.rect.x + 160
        y0 = self.rect.y + 16
        w = {}

        w["name"] = TextInput(pygame.Rect(lx, y0, 260, TextInput.H), self.cfg["display_name"])

        if self.kind == KIND_ENEMY:
            stats = self.cfg["stats"]
            y = y0 + 44
            w["max_hp"]   = Slider(pygame.Rect(lx, y, 220, Slider.H), 1, 2000, stats["max_hp"],   step=5); y += ROW_H
            w["strength"] = Slider(pygame.Rect(lx, y, 220, Slider.H), 1, 200,  stats["strength"], step=1); y += ROW_H
            w["power"]    = Slider(pygame.Rect(lx, y, 220, Slider.H), 1, 200,  stats["power"],    step=1); y += ROW_H
            w["defense"]  = Slider(pygame.Rect(lx, y, 220, Slider.H), 0, 200,  stats["defense"],  step=1); y += ROW_H
            w["speed"]    = Slider(pygame.Rect(lx, y, 220, Slider.H), 0.2, 5, stats["speed"], step=0.1, fmt="{:.1f}"); y += ROW_H

            y += 10
            w["level"]     = Slider(pygame.Rect(lx, y, 220, Slider.H), 1, 99, self.cfg["level"], step=1); y += ROW_H
            w["xp_reward"] = Slider(pygame.Rect(lx, y, 220, Slider.H), 0, 2000, self.cfg["xp_reward"], step=5); y += ROW_H

            # Description sits below the entity_type/category/ai/zeni cycle
            # buttons — _draw_enemy() repositions this rect each frame once
            # it knows exactly how many cycle-button rows are showing
            # (shooter_style only appears for enemy_category == "shooter").
            w["description"] = TextArea(pygame.Rect(lx, 0, 320, TextArea.H), self.cfg.get("description", ""))
        else:
            y = y0 + 44
            w["speed"] = Slider(pygame.Rect(lx, y, 220, Slider.H), 0.2, 5, self.cfg["speed"], step=0.1, fmt="{:.1f}"); y += ROW_H
            w["interaction_range"] = Slider(pygame.Rect(lx, y, 220, Slider.H), 20, 150, self.cfg["interaction_range"], step=5); y += ROW_H

            y += 10
            dlg = self.cfg["dialogue"]
            w["dialogue_0"] = TextInput(pygame.Rect(lx, y, 320, TextInput.H),
                                        dlg["dialogues"][0] if dlg["dialogues"] else "")
            y += ROW_H + 10
            w["after_limit"] = TextInput(pygame.Rect(lx, y, 320, TextInput.H), dlg["after_limit_text"])
            y += ROW_H + 10

            # Description — rect repositioned each frame in _draw_npc()
            # once the fixed dialogue-fields block above it is drawn.
            w["description"] = TextArea(pygame.Rect(lx, y, 320, 80), self.cfg.get("description", ""))

        self.widgets = w

    # -- events -----------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> bool:
        """Returns True if a field changed (marks dirty)."""
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
            self.cfg["display_name"] = wgt.value
        elif key == "description":
            self.cfg["description"] = wgt.value
        elif key in ("max_hp", "strength", "power", "defense", "speed") and self.kind == KIND_ENEMY:
            self.cfg["stats"][key] = int(wgt.value) if key != "speed" else round(wgt.value, 2)
        elif key == "level":
            self.cfg["level"] = int(wgt.value)
        elif key == "xp_reward":
            self.cfg["xp_reward"] = int(wgt.value)
        elif key == "speed" and self.kind == KIND_NPC:
            self.cfg["speed"] = round(wgt.value, 2)
        elif key == "interaction_range":
            self.cfg["interaction_range"] = int(wgt.value)
        elif key == "dialogue_0":
            dlg = self.cfg["dialogue"]
            if dlg["dialogues"]:
                dlg["dialogues"][0] = wgt.value
            else:
                dlg["dialogues"] = [wgt.value]
        elif key == "after_limit":
            self.cfg["dialogue"]["after_limit_text"] = wgt.value

    def _handle_buttons(self, pos) -> bool:
        changed = False
        if self.kind == KIND_ENEMY:
            for rect, val, field in self._cycle_rects_enemy():
                if rect.collidepoint(pos):
                    self.cfg[field] = val
                    changed = True
                    if field == "entity_type":
                        # Boss vs regular enemy reads from a different
                        # sprite folder — reload thumbnails immediately.
                        self._load_preview()
        else:
            for rect, val, field in self._cycle_rects_npc():
                if rect.collidepoint(pos):
                    self.cfg[field] = val
                    changed = True
        return changed

    # -- cycle-button layouts (entity_type / enemy_category / ai_type /
    #    shooter_style / zeni_pool for enemies; npc_type for NPCs) -----
    def _cycle_rects_enemy(self):
        lx = self.rect.x + 160
        y = self.rect.y + 16 + 44 + ROW_H * 4 + 10 + ROW_H * 2 + 20
        out = []
        rows = [
            ("entity_type",    ["enemy", "boss"]),
            ("enemy_category", ["melee", "shooter"]),
            ("ai_type",        ["easy", "advanced"]),
        ]
        if self.cfg.get("enemy_category") == "shooter":
            rows.append(("shooter_style", ["bomb", "bullet", "rocket", "kiblast"]))
        rows.append(("zeni_pool", ["tier1", "tier2", "tier3", "tier4"]))

        for field, options in rows:
            current = self.cfg.get(field)
            bx = lx
            for opt in options:
                r = pygame.Rect(bx, y, 80, 26)
                out.append((r, opt, field))
                bx += 84
            y += 34
        return out

    def _cycle_rects_npc(self):
        lx = self.rect.x + 160
        y = self.rect.y + 16 + 44 + ROW_H * 2 + 10 + (ROW_H + 10) + ROW_H + 20
        out = []
        for opt in ("static", "moving"):
            r = pygame.Rect(lx, y, 80, 26)
            out.append((r, opt, "npc_type"))
            lx += 84
        return out

    # -- draw ---------------------------------------------------------
    def draw(self, surf, font, font_sm, dt) -> None:
        lx = self.rect.x + 20
        y0 = self.rect.y + 16

        draw_label(surf, font_sm, "Display Name", lx, y0 + 6)
        self.widgets["name"].draw(surf, font_sm, dt)

        self._draw_preview(surf, font, font_sm)

        if self.kind == KIND_ENEMY:
            self._draw_enemy(surf, font, font_sm, y0)
        else:
            self._draw_npc(surf, font, font_sm, y0)

    def _draw_preview(self, surf, font, font_sm) -> None:
        """Sprite preview, pinned to the top-right corner of the panel —
        the idle-down frame for each variant folder found under
        assets/sprites/{enemy,npc}/{id}/, at up to 96x96. Falls back to a
        dashed 'no sprite yet' box (still keyed to entity_type colour) so
        a missing/misnamed art folder is obvious at a glance instead of
        silently showing nothing."""
        box_w = 220
        px = self.rect.right - box_w - 20
        py = self.rect.y + 12

        draw_label(surf, font_sm, "Preview", px, py)
        py += 20

        variants = self.preview_variants or [{'type': 'default', 'name': 'Default', 'sprite': None}]

        # Large thumbnail for the first variant
        main = variants[0]
        self._draw_thumb(surf, font_sm, main, px, py, 96)

        # Smaller thumbnails for any additional variants, wrapped 2 per row
        if len(variants) > 1:
            tx, ty = px + 104, py
            for i, v in enumerate(variants[1:]):
                self._draw_thumb(surf, font_sm, v, tx, ty, 56)
                ty += 68
                if ty > py + 96:
                    break  # panel's short on room past this point — rest still exist on disk

    def _draw_thumb(self, surf, font_sm, variant, x, y, size) -> None:
        rect = pygame.Rect(x, y, size, size)
        sprite = variant.get('sprite')
        if sprite is not None:
            surf.blit(sprite, rect)
            pygame.draw.rect(surf, C_BORDER, rect, 1)
        else:
            # dashed placeholder — no art found for this variant yet
            pygame.draw.rect(surf, C_PANEL_DARK, rect)
            dash = 4
            xx = rect.left
            while xx < rect.right:
                pygame.draw.line(surf, C_TEXT_DIM,
                                  (xx, rect.top), (min(xx + dash, rect.right), rect.top))
                pygame.draw.line(surf, C_TEXT_DIM,
                                  (xx, rect.bottom - 1), (min(xx + dash, rect.right), rect.bottom - 1))
                xx += dash * 2
            label = render_text_cached(font_sm, "?", C_TEXT_DIM)
            surf.blit(label, label.get_rect(center=rect.center))

        name_txt = render_text_cached(font_sm, variant.get('name', ''), C_TEXT_DIM)
        surf.blit(name_txt, (x, y + size + 2))

    def _draw_enemy(self, surf, font, font_sm, y0) -> None:
        lx = self.rect.x + 20
        labels = ["Max HP", "STR (Melee)", "POW (Super)", "END (Defense)", "SPD (Speed)"]
        y = y0 + 44
        for label, key in zip(labels, ("max_hp", "strength", "power", "defense", "speed")):
            draw_label(surf, font_sm, label, lx, y + 2)
            self.widgets[key].draw(surf, font_sm)
            y += ROW_H

        y += 10
        draw_label(surf, font_sm, "Level", lx, y + 2)
        self.widgets["level"].draw(surf, font_sm); y += ROW_H
        draw_label(surf, font_sm, "XP Reward", lx, y + 2)
        self.widgets["xp_reward"].draw(surf, font_sm); y += ROW_H

        y += 20
        for rect, val, field in self._cycle_rects_enemy():
            hovered = rect.collidepoint(pygame.mouse.get_pos())
            is_current = (self.cfg.get(field) == val)
            draw_button(surf, font_sm, rect, val,
                       color=C_ACCENT if is_current else C_TEXT_DIM, hover=hovered)
        # Row labels drawn once per field, left of the first option in each row
        field_rows = []
        seen = []
        for rect, val, field in self._cycle_rects_enemy():
            if field not in seen:
                seen.append(field)
                field_rows.append((field, rect.y))
        titles = {
            "entity_type": "Type", "enemy_category": "Category", "ai_type": "AI",
            "shooter_style": "Shooter Style", "zeni_pool": "Zeni Pool",
        }
        for field, ry in field_rows:
            draw_label(surf, font_sm, titles.get(field, field), lx, ry + 5)

        # ── Description — below however many cycle-button rows ended up
        # showing (varies with enemy_category, see _cycle_rects_enemy). ──
        desc_y = max(ry for _, ry in field_rows) + 34 + 14
        draw_label(surf, font_sm, "Description", lx, desc_y + 6)
        desc_h = max(TextArea.H, self.rect.bottom - (desc_y + 26) - 16)
        self.widgets["description"].rect = pygame.Rect(lx, desc_y + 26, self.rect.w - 220, desc_h)
        self.widgets["description"].draw(surf, font_sm, 0)

    def _draw_npc(self, surf, font, font_sm, y0) -> None:
        lx = self.rect.x + 20
        y = y0 + 44
        draw_label(surf, font_sm, "Speed", lx, y + 2)
        self.widgets["speed"].draw(surf, font_sm); y += ROW_H
        draw_label(surf, font_sm, "Interact Range", lx, y + 2)
        self.widgets["interaction_range"].draw(surf, font_sm); y += ROW_H

        y += 10
        for rect, val, field in self._cycle_rects_npc():
            hovered = rect.collidepoint(pygame.mouse.get_pos())
            is_current = (self.cfg.get(field) == val)
            draw_button(surf, font_sm, rect, val,
                       color=C_ACCENT if is_current else C_TEXT_DIM, hover=hovered)
        draw_label(surf, font_sm, "Behavior", lx, y + 5)
        y += 44

        y += 10
        draw_label(surf, font_sm, "First Line", lx, y + 6)
        self.widgets["dialogue_0"].draw(surf, font_sm, 0)
        y += ROW_H + 10
        draw_label(surf, font_sm, "After Limit", lx, y + 6)
        self.widgets["after_limit"].draw(surf, font_sm, 0)
        y += ROW_H + 10

        draw_label(surf, font_sm, "Description", lx, y + 6)
        desc_h = max(60, self.rect.bottom - (y + 26) - 44)   # -44 leaves room for the hint below
        self.widgets["description"].rect = pygame.Rect(lx, y + 26, self.rect.w - 220, desc_h)
        self.widgets["description"].draw(surf, font_sm, 0)
        y += 26 + desc_h + 6

        hint = render_text_cached(
            font_sm,
            "Full multi-line dialogue trees are still authored per-placement "
            "in the event/trigger editor — this sets the NPC's default first "
            "line and give_item, used when it's dropped fresh in a room.",
            C_TEXT_DIM,
        )
        surf.blit(hint, (lx, self.rect.bottom - 40))


# ══════════════════════════════════════════════════════════════════════
#  Top-level overlay — same lifecycle contract as CharacterCreator:
#  toggle() / handle_input(event) / update(dt) / draw(surface, dt),
#  lives inside the host game's loop, doesn't own the display/clock.
# ══════════════════════════════════════════════════════════════════════
HEADER_H  = 44
FOOTER_H  = 52
LIST_W    = 220
PAD       = 8

KIND_TAB_W = 100


class EntityCreator:
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

        self.kind = KIND_ENEMY
        self.ids: list[str] = []
        self.configured: set[str] = set()
        self.entity_list = EntityList(self.list_rect)
        self.selected_id = ""
        self.cfg = _defaults_for(self.kind)
        self.editor: Optional[EntityEditorPanel] = None

        self.status_msg = ""
        self.status_col = C_TEXT_DIM
        self.status_timer = 0.0

        self.dialog = None  # non-blocking modal, same shape as character_creator's

    # -- layout ---------------------------------------------------------
    def _build_layout(self) -> None:
        sw, sh = self.screen_width, self.screen_height
        self.kind_tab_rects = [
            pygame.Rect(PAD, PAD, KIND_TAB_W, HEADER_H - PAD * 2),
            pygame.Rect(PAD + KIND_TAB_W + 4, PAD, KIND_TAB_W, HEADER_H - PAD * 2),
        ]
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
        self.ids = discover_all_ids(self.kind)
        self.configured = set(discover_configured_ids(self.kind))
        self.entity_list.set_ids(self.ids, self.configured, self.selected_id)
        self.selected_id = self.entity_list.selected
        if self.selected_id:
            self._load_entity(self.selected_id)
        else:
            self.editor = None

    def _switch_kind(self, kind: str) -> None:
        if kind == self.kind:
            return
        if self.editor:
            self._flush()
        self.kind = kind
        self.selected_id = ""
        self._refresh_list()

    def _load_entity(self, entity_id: str) -> None:
        self.selected_id = entity_id
        self.cfg = load_config(self.kind, entity_id)
        self.editor = EntityEditorPanel(self.editor_rect, self.kind, entity_id, self.cfg)

    def _switch_entity(self, entity_id: str) -> None:
        if self.editor:
            self._flush()
        self._load_entity(entity_id)

    def _flush(self) -> None:
        """No-op placeholder mirroring CharacterEditor.flush() — all our
        widgets already write straight into self.cfg on change, so there's
        nothing buffered to commit. Kept as a hook in case a future tab
        (e.g. multi-line dialogue list) needs debounced writes."""
        pass

    def _set_status(self, msg: str, ok: bool = True) -> None:
        self.status_msg = msg
        self.status_col = C_GREEN if ok else C_RED
        self.status_timer = 3.0

    # -- save / delete / new ----------------------------------------------
    def _do_save(self) -> None:
        if not self.editor or not self.selected_id:
            return
        save_config(self.kind, self.cfg)
        self.configured.add(self.selected_id)
        self.editor.dirty = False
        self._set_status(f"Saved {self.selected_id}")

    def _do_delete(self) -> None:
        if not self.selected_id:
            return
        delete_config(self.kind, self.selected_id)
        self.configured.discard(self.selected_id)
        self._set_status(f"Deleted config for {self.selected_id}", ok=False)
        self._load_entity(self.selected_id)  # reloads as defaults

    def _open_new_dialog(self) -> None:
        field = TextInput(pygame.Rect(0, 0, 320, 32), "")
        field.active = True
        self.dialog = {"field": field, "error": ""}

    def _do_create(self, new_id: str) -> None:
        new_id = new_id.strip().lower().replace(" ", "_")
        if not new_id:
            return
        if new_id not in self.ids:
            self.ids.append(new_id)
            self.ids.sort()
            self.entity_list.set_ids(self.ids, self.configured, new_id)
        self._switch_entity(new_id)
        self.dialog = None
        self._set_status(f"Created {new_id} — remember to add its sprite folder")

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

        for i, rect in enumerate(self.kind_tab_rects):
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and rect.collidepoint(event.pos):
                self._switch_kind(KIND_ENEMY if i == 0 else KIND_NPC)
                return None

        new_sel = self.entity_list.handle_event(event)
        if new_sel:
            self._switch_entity(new_sel)
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
            # Rects are stamped onto the dialog dict by _draw_dialog() each
            # frame (same "draw computes hit-rects for next event" pattern
            # used by the popup lists in entity_editor.py), so they're only
            # valid once the dialog has been drawn at least once.
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

        for i, rect in enumerate(self.kind_tab_rects):
            label = "Enemies" if i == 0 else "NPCs"
            active = (self.kind == (KIND_ENEMY if i == 0 else KIND_NPC))
            bg = C_TAB_ACT if active else C_TAB_INACT
            pygame.draw.rect(screen, bg, rect, border_radius=6)
            pygame.draw.rect(screen, C_ACCENT if active else C_BORDER, rect, 1, border_radius=6)
            txt = render_text_cached(self.font_sm, label, C_TEXT if active else C_TEXT_DIM)
            screen.blit(txt, txt.get_rect(center=rect.center))

        self.entity_list.draw(screen, self.font, self.font_sm)

        pygame.draw.rect(screen, C_PANEL, self.editor_rect, border_radius=6)
        pygame.draw.rect(screen, C_BORDER, self.editor_rect, 1, border_radius=6)
        if self.editor:
            old_clip = screen.get_clip()
            screen.set_clip(self.editor_rect)
            self.editor.draw(screen, self.font, self.font_sm, dt)
            screen.set_clip(old_clip)
        elif not self.ids:
            root = "assets/sprites/enemies/" if self.kind == KIND_ENEMY else "assets/sprites/npc/"
            msg = render_text_cached(self.font_sm, f"No folders found in {root}", C_TEXT_DIM)
            screen.blit(msg, msg.get_rect(center=self.editor_rect.center))

        draw_button(screen, self.font_sm, self.btn_save, "Save",
                   color=C_ACCENT2 if (self.editor and self.editor.dirty) else C_ACCENT)
        draw_button(screen, self.font_sm, self.btn_delete, "Delete", color=C_RED)
        draw_button(screen, self.font_sm, self.btn_new, "+ New Entity")

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
        pygame.draw.rect(screen, C_DIALOG_BG, box, border_radius=8)
        pygame.draw.rect(screen, C_ACCENT, box, 1, border_radius=8)

        prompt = "New enemy id (folder-safe, e.g. 'saibaman')" if self.kind == KIND_ENEMY \
            else "New NPC id (folder-safe, e.g. 'blacksmith')"
        txt = render_text_cached(self.font_sm, prompt, C_TEXT)
        screen.blit(txt, (box.x + 20, box.y + 16))

        field = self.dialog["field"]
        field.rect = pygame.Rect(box.x + 20, box.y + 46, box.w - 40, 32)
        field.draw(screen, self.font_sm, dt)

        ok_r = pygame.Rect(box.right - 200, box.bottom - 44, 90, 30)
        cancel_r = pygame.Rect(box.right - 100, box.bottom - 44, 84, 30)
        draw_button(screen, self.font_sm, ok_r, "Create", color=C_GREEN)
        draw_button(screen, self.font_sm, cancel_r, "Cancel", color=C_RED)
        self.dialog["ok_rect"] = ok_r
        self.dialog["cancel_rect"] = cancel_r