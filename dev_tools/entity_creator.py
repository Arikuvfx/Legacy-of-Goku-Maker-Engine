"""
entity_creator.py  -  Dev-menu enemy / NPC / critter entity editor
====================================================================
Sister tool to character_creator.py. Where character_creator defines
*player* characters (assets/characters/{id}.json), this defines
*enemies*, *bosses*, *NPCs*, and *critters* (ambient wildlife) - id,
stats/behavior, level, XP reward, zeni drop table, AI type, dialogue,
wander pacing - saved as JSON so new entities can be added without
touching enemy.py / npc.py / critter.py / entity_editor.py at all.

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
    critters/
      {critter_id}/                <- one folder per critter (idle.png and/or
        variants/{variant}/...        flying.png, per critter.py's profile)
  enemies/
    {enemy_id}.json                <- stats/AI/rewards, written here on Save
  npcs/
    {npc_id}.json                  <- behaviour/dialogue defaults, written here
  critters/
    {critter_id}.json              <- size/wander/behavior-profile, written here

This tool does NOT touch sprite art - it only discovers which ids exist
(same folder scan entity_editor.py already does for NPCs) and lets you
attach data to them. Dropping a new assets/sprites/enemies/{id}/ folder is
enough for a new entity to show up here as "unconfigured"; Save gives it
a config and entity_editor.py's palette picks it up automatically.

"""

from __future__ import annotations

import json
import math
import os
import sys
import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pygame

import dev_tools.ui_kit as uk

# ──────────────────────────────────────────────────────────────────────
#  Paths - same BASE_DIR anchoring trick as character_creator.py, so this
#  tool also works correctly once packaged (see that file's long comment).
# ──────────────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

ENEMY_SPRITES_ROOT   = BASE_DIR / "assets/sprites/enemies"
BOSS_SPRITES_ROOT    = ENEMY_SPRITES_ROOT / "boss"
NPC_SPRITES_ROOT      = BASE_DIR / "assets/sprites/npc"
CRITTER_SPRITES_ROOT  = BASE_DIR / "assets/sprites/critters"
ENEMIES_DIR            = BASE_DIR / "assets/enemies"
NPCS_DIR                = BASE_DIR / "assets/npcs"
CRITTERS_DIR            = BASE_DIR / "assets/critters"

KIND_ENEMY   = "enemy"
KIND_NPC     = "npc"
KIND_CRITTER = "critter"

# Display order for the kind tabs / _switch_kind cycling.
KIND_ORDER = (KIND_ENEMY, KIND_NPC, KIND_CRITTER)

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
    "shooter_style": "bomb",      # "bomb" | "bullet" | "rocket" | "kiblast" - shooter only
    # Same STR/POW/END/SPD scheme character_creator.py uses for the player
    # (see that file's stats block and game.py's _stat_map) - enemies just
    # skip the KI resource (max_ki/ki_regen), since they don't spend energy,
    # they only need the damage stat.
    "stats": {
        "max_hp":    150,
        "strength":   10,   # STR - melee attack damage (enemy_category == "melee")
        "power":      10,   # POW - super/ranged attack damage: ki-blast, bomb,
                             #       bullet, rocket (enemy_category == "shooter")
        "defense":    20,   # END - mitigates incoming melee damage
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
    "width":             32,          # sprite/frame size (spritesheet slicing)
    "height":            32,
    "speed":             1.5,
    "interaction_range": 50,
    # Ground shadow width in px - same slider/units as character_creator.py's
    # cfg["shadow_size"] (8-96, step 4), just named shadow_width here to match
    # the enemy config's existing field of the same purpose (see
    # DEFAULT_ENEMY_CONFIG above). NPC.shadow_size stays the separate legacy
    # 'small'/'big' sprite-asset toggle and isn't touched by this.
    "shadow_width":      32,
    "dialogue": {
        "dialogues":        ["Hello, traveler!"],
        "trigger_limit":    -1,      # -1 = unlimited
        "after_limit_text": "I have nothing more to say.",
        "random_order":     False,
        "give_item":        None,
    },
}

# Critters (ambient wildlife - squirrels, birds, butterflies, ...) are the
# lightest of the three kinds: no stats, no AI, no dialogue. Fields here
# are exactly Critter.__init__'s tunables (see critter.py) minus x/y/
# variant, which are set at placement time, not entity-creation time.
DEFAULT_CRITTER_CONFIG: dict = {
    "id":            "",
    "display_name":  "",
    # Freeform prose shown in the Scouter Data description panel (see
    # ui/scouter_menu.py's _get_entity_description / _draw_data_description).
    "description":   "",
    # Key into Critter.BEHAVIOR_PROFILES - picks the pacing/animation
    # profile (moving/resting state names, speed, rest/move durations,
    # flutter/jitter) this id plays. Any value not in BEHAVIOR_PROFILES
    # quietly falls back to Critter.DEFAULT_PROFILE, so a new critter id
    # can reuse an existing animal's pacing (e.g. a 'chipmunk' sprite
    # folder using the 'squirrel' profile) without new code - new profiles
    # themselves still have to be added to critter.py directly.
    "critter_type":  "squirrel",
    "width":  16,           # sprite/frame size (spritesheet slicing)
    "height": 16,
    "wander_radius": 32,    # max px from spawn point before turning back
}

# Behavior profiles Critter.BEHAVIOR_PROFILES actually defines (see
# critter.py). Kept as its own list rather than imported from critter.py
# so this dev tool doesn't need the game package importable to run -
# same standalone-ness character_creator.py's widgets/palette have.
# Any critter_type outside this list still works at runtime (it just
# falls back to Critter.DEFAULT_PROFILE), but these are the only ones
# with a purpose-built pacing/animation profile today.
CRITTER_PROFILE_OPTIONS = ("squirrel", "bird", "butterfly")


def _defaults_for(kind: str) -> dict:
    if kind == KIND_ENEMY:
        return copy.deepcopy(DEFAULT_ENEMY_CONFIG)
    if kind == KIND_CRITTER:
        return copy.deepcopy(DEFAULT_CRITTER_CONFIG)
    return copy.deepcopy(DEFAULT_NPC_CONFIG)


def _dirs_for(kind: str) -> tuple[Path, Path]:
    """Return (sprites_root, data_dir) for *kind*."""
    if kind == KIND_ENEMY:
        return ENEMY_SPRITES_ROOT, ENEMIES_DIR
    if kind == KIND_CRITTER:
        return CRITTER_SPRITES_ROOT, CRITTERS_DIR
    return NPC_SPRITES_ROOT, NPCS_DIR


# ══════════════════════════════════════════════════════════════════════
#  Filesystem scanning / persistence  (unchanged data layer)
# ══════════════════════════════════════════════════════════════════════

def discover_sprite_ids(kind: str) -> list[str]:
    """Every sub-folder of the kind's sprite root - same scan entity_editor
    already does for NPCs (see _build_npc_catalogue). An id showing up
    here doesn't mean it has a config yet; see discover_configured_ids().

    For enemies, 'boss' is excluded - it's the boss sprite subfolder
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
    shows up as "unconfigured" instead of being invisible.

    Returned in the saved custom display order for *kind* (see
    load_entity_order()/save_entity_order(), set via the reorder controls
    in EntityCreator's sidebar) - same "hand-picked order, leftovers sorted
    at the end" reconciliation character_creator.discover_characters()
    uses. Any id not yet placed in the saved order (newly added, or before
    an order was ever saved) is appended alphabetically."""
    found = set(discover_sprite_ids(kind)) | set(discover_configured_ids(kind))
    order = load_entity_order(kind)
    ordered = [eid for eid in order if eid in found]
    leftover = sorted(found - set(ordered))
    return ordered + leftover


# ── Entity list ordering (per kind) ──────────────────────────────────
# Enemies, NPCs and critters each get their own hand-picked display order
# via the reorder controls in the sidebar. All three orders live in one
# small file next to assets/ (not inside assets/enemies/ etc.) so it isn't
# mistaken for an entity config by anything scanning those folders -
# same rationale as character_creator.MENU_FILE.
ENTITY_ORDER_FILE = BASE_DIR / "assets/entity_menu.json"


def _load_entity_orders() -> dict:
    if not ENTITY_ORDER_FILE.exists():
        return {}
    try:
        data = json.loads(ENTITY_ORDER_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def load_entity_order(kind: str) -> list[str]:
    """Return the saved custom display order for *kind*. May be stale
    (reference deleted ids, omit new ones) - callers reconcile against the
    real id list, same as discover_all_ids() does. Returns [] if nothing
    has been saved yet."""
    data = _load_entity_orders()
    return [str(eid) for eid in data.get(kind, [])]


def save_entity_order(kind: str, order: list[str]) -> None:
    """Persist the display order for *kind*, leaving the other kinds'
    saved orders untouched."""
    data = _load_entity_orders()
    data[kind] = order
    ENTITY_ORDER_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENTITY_ORDER_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def scan_variants(kind: str, entity_id: str, entity_type: str = "") -> list[str]:
    """Sub-folders of {sprites_root}/{entity_id}/variants/, 'default' always
    first - same convention as entity_editor._scan_npc_variants().

    For enemies flagged as bosses (entity_type == "boss"), the sprite root
    is assets/sprites/enemies/boss/ instead of the flat enemies/ root -
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
    break when the schema grows - same merge strategy character_creator's
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

            # Migrate legacy frame-size text on first load. Once a creator
            # config contains explicit width/height, those values remain
            # authoritative and sprite_size.txt is ignored.
            if "width" not in saved or "height" not in saved:
                root, _data_dir = _dirs_for(kind)
                entity_root = BOSS_SPRITES_ROOT if (
                    kind == KIND_ENEMY and cfg.get("entity_type") == "boss"
                ) else root
                legacy_folder = entity_root / entity_id
                lw, lh = _read_sprite_size(
                    legacy_folder, 16 if kind == KIND_CRITTER else 32
                )
                if "width" not in saved:
                    cfg["width"] = lw
                if "height" not in saved:
                    cfg["height"] = lh

            # -- Migrate pre-STR/POW-split saves ------------------------
            # Old schema had a single "power" stat doing double duty as
            # melee damage *and* base ki-blast/projectile damage. A save
            # from before this split has "power" but no "strength" - seed
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
    # Unconfigured sprite folders can still carry the legacy size text. Seed
    # the creator UI from it so saving the entity automatically migrates the
    # size into JSON.
    if path.exists() is False:
        root, _data_dir = _dirs_for(kind)
        entity_root = BOSS_SPRITES_ROOT if (
            kind == KIND_ENEMY and cfg.get("entity_type") == "boss"
        ) else root
        legacy_folder = entity_root / entity_id
        lw, lh = _read_sprite_size(legacy_folder, 16 if kind == KIND_CRITTER else 32)
        cfg["width"] = lw
        cfg["height"] = lh

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
#  Small helpers  (unchanged data layer)
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


def _load_preview_sprite(kind: str, entity_id: str, variant_type: str, entity_type: str = "", size: int = 96,
                       frame_w: int | None = None, frame_h: int | None = None):
    """Load the idle-down frame for a sprite-preview thumbnail.

    Tries a handful of common sheet filenames under the entity's variant
    folder (falling back to the flat, non-variant folder), sliced the same
    way entity_editor.py's palette thumbnails are (row 0 = idle-down on a
    4-directional sheet) - so what's shown here matches what shows up once
    the entity is placed in the room editor. Returns a Surface scaled to
    (size, size), or None if no matching art exists yet.

    Enemies flagged as bosses (entity_type == "boss") are searched under
    BOSS_SPRITES_ROOT (assets/sprites/enemies/boss/) instead of the flat
    enemies/ root - only when the config says so, not as a blind fallback.
    """
    if kind == KIND_ENEMY and entity_type == "boss":
        root = BOSS_SPRITES_ROOT
    elif kind == KIND_CRITTER:
        root = CRITTER_SPRITES_ROOT
    else:
        root = ENEMY_SPRITES_ROOT if kind == KIND_ENEMY else NPC_SPRITES_ROOT
    entity_dir = root / entity_id
    search_dirs = [entity_dir / "variants" / variant_type, entity_dir]
    filenames = ["idle.png", "idle_down.png", "walk_down.png", "sprite.png"]
    if kind == KIND_CRITTER:
        # Critters with no resting_state (e.g. butterflies - see
        # critter.py's BEHAVIOR_PROFILES) have no idle pose at all, so
        # fall back to flying.png - same lookup order entity_editor.py's
        # room-palette thumbnails use.
        filenames = filenames + ["flying.png"]

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
    # square - most entity sprite sheets are taller than they are wide,
    # so guessing frame_w == frame_h sliced the wrong region and either
    # threw on subsurface() (silently swallowed below, showing "no sprite
    # yet") or rendered a garbled/cropped thumbnail.
    if frame_w is None or frame_h is None:
        fallback = 16 if kind == KIND_CRITTER else 32
        legacy_w, legacy_h = _read_sprite_size(found_dir, fallback)
        frame_w = legacy_w if frame_w is None else frame_w
        frame_h = legacy_h if frame_h is None else frame_h
    frame_w = max(1, int(frame_w))
    frame_h = max(1, int(frame_h))

    try:
        sheet = pygame.image.load(str(path)).convert_alpha()
        sheet_w, sheet_h = sheet.get_size()
        # flying.png sheets (critters with no idle pose, e.g. butterflies)
        # are always 8-directional; everything else on these candidate
        # paths is the 4-dir layout - same convention entity_editor.py's
        # _load_idle_down_sprite uses for its room-palette thumbnails.
        num_rows = 8 if path.name == "flying.png" else 4
        # Clamp in case sprite_size.txt is stale/wrong for this sheet.
        frame_w = min(frame_w, sheet_w)
        frame_h = min(frame_h, sheet_h // num_rows if sheet_h >= num_rows else sheet_h)
        frame = sheet.subsurface(pygame.Rect(0, 0, frame_w, frame_h))

        # Scale to fit the (size, size) box while preserving the sprite's
        # actual aspect ratio, then pad with transparency and centre it -
        # a straight scale to (size, size) stretched/squashed anything that
        # wasn't already square (most enemy/npc/boss sheets aren't).
        scale = min(size / frame_w, size / frame_h)
        scaled_w = max(1, round(frame_w * scale))
        scaled_h = max(1, round(frame_h * scale))
        scaled = pygame.transform.scale(frame, (scaled_w, scaled_h))

        canvas = pygame.Surface((size, size), pygame.SRCALPHA)
        canvas.blit(scaled, ((size - scaled_w) // 2, (size - scaled_h) // 2))
        return canvas
    except Exception as e:
        print(f"Error loading preview sprite ({path}): {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
#  UI layer  -  built entirely on dev_tools/ui_kit.py, same visual
#  language and widget-behaviour conventions as character_creator.py
#  (immediate-mode: draw() computes hit-rects consumed by next frame's
#  events, hover/press values ease and quantise for cache-friendliness,
#  a small _TextEdit engine backs every text field and the New-ID dialog).
# ══════════════════════════════════════════════════════════════════════

_T = uk.Theme

# Same flat two-tone backdrop / bar colours DevMenu and CharacterCreator use.
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
_OFFSCREEN = (-9999, -9999)

ROSTER_ROW_H   = 46
ROSTER_ROW_GAP = 6
ANIM_FPS       = 8.0

KIND_TAB_LABELS = {KIND_ENEMY: "Enemies", KIND_NPC: "NPCs", KIND_CRITTER: "Critters"}
KIND_NEW_PROMPTS = {
    KIND_ENEMY:   "Enter an id for the new enemy (letters, digits, _ and -).",
    KIND_NPC:     "Enter an id for the new NPC (letters, digits, _ and -).",
    KIND_CRITTER: "Enter an id for the new critter (letters, digits, _ and -).",
}
KIND_ID_HINTS = {
    KIND_ENEMY:   "e.g. saibaman",
    KIND_NPC:     "e.g. blacksmith",
    KIND_CRITTER: "e.g. rabbit",
}
KIND_SPRITE_ROOT_LABELS = {
    KIND_ENEMY:   "assets/sprites/enemies/",
    KIND_NPC:     "assets/sprites/npc/",
    KIND_CRITTER: "assets/sprites/critters/",
}
# Small per-kind accent used only for the sidebar dot / preview label, so
# the three roster kinds read as distinct at a glance without touching the
# app-wide gold "selected" language used everywhere else.
KIND_ACCENT = {
    KIND_ENEMY:   (226, 110, 90),
    KIND_NPC:     _T.KI_BLUE,
    KIND_CRITTER: (140, 205, 130),
}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


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


# ── Wrapped-text spans (for multi-line fields) ──────────────────────────

_SPAN_CACHE: dict = {}


def _split_hard(font, text, s, e, width):
    out = []
    while e - s > 1 and font.width(text[s:e]) > width:
        lo, hi = 1, e - s - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.width(text[s:s + mid]) <= width:
                lo = mid
            else:
                hi = mid - 1
        out.append((s, s + lo))
        s += lo
    out.append((s, e))
    return out


def _wrap_spans(font, text, width):
    key = (id(font), text, width)
    cached = _SPAN_CACHE.get(key)
    if cached is not None:
        return cached
    raw = []
    pos = 0
    for para in text.split("\n"):
        if not para:
            raw.append((pos, pos))
        else:
            s = e = pos
            wpos = pos
            first = True
            for word in para.split(" "):
                ws, we = wpos, wpos + len(word)
                if first:
                    s, e, first = ws, we, False
                elif font.width(text[s:we]) <= width:
                    e = we
                else:
                    raw.append((s, e))
                    s, e = ws, we
                wpos = we + 1
            raw.append((s, e))
        pos += len(para) + 1
    spans = []
    for s, e in raw:
        spans.extend(_split_hard(font, text, s, e, width))
    if len(_SPAN_CACHE) > 160:
        _SPAN_CACHE.clear()
    _SPAN_CACHE[key] = spans
    return spans


def _line_of(spans, idx):
    line = 0
    for i, (s, _e) in enumerate(spans):
        if s <= idx:
            line = i
        else:
            break
    return line


# ── Text editing engine (no drawing) ────────────────────────────────────

class _TextEdit:
    """Caret / selection / clipboard logic shared by every text field and
    by the New-ID dialog. Pure state - the creator draws it and feeds it
    keys. key() returns None, 'changed', 'commit' or 'cancel'."""

    def __init__(self, value="", multiline=False, max_len=600, allowed=None):
        self.value = value
        self.cursor = len(value)
        self.anchor = None
        self.multiline = multiline
        self.max_len = max_len
        self.allowed = allowed
        self.blink = 0.0
        self.goal_x = None
        self.view = None

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
        if not self.multiline:
            text = text.replace("\r", "").replace("\n", " ")
        else:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        keep = []
        for ch in text:
            if ch == "\n" and self.multiline:
                keep.append(ch)
            elif ch.isprintable() and (self.allowed is None or self.allowed(ch)):
                keep.append(ch)
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
        self.goal_x = None
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

    def _spans(self):
        if not self.view:
            return None
        font, width = self.view
        return _wrap_spans(font, self.value, width)

    def _vertical(self, delta, shift):
        spans = self._spans()
        if not spans:
            return
        font, _w = self.view
        line = _line_of(spans, self.cursor)
        s, e = spans[line]
        if self.goal_x is None:
            self.goal_x = font.width(self.value[s:_clamp(self.cursor, s, e)])
        tgt = line + delta
        if tgt < 0:
            self._move(0, shift)
            return
        if tgt >= len(spans):
            self._move(len(self.value), shift)
            return
        ts, te = spans[tgt]
        self._move(ts + _index_at_x(font, self.value[ts:te], self.goal_x), shift)

    def key(self, event):
        mods = getattr(event, "mod", 0) | pygame.key.get_mods()
        ctrl = bool(mods & (pygame.KMOD_CTRL | pygame.KMOD_META))
        shift = bool(mods & pygame.KMOD_SHIFT)
        k = event.key
        self.blink = 0.0
        if k not in (pygame.K_UP, pygame.K_DOWN):
            self.goal_x = None

        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.multiline and not ctrl:
                return "changed" if self.insert("\n") else None
            return "commit"
        if k == pygame.K_ESCAPE:
            return "cancel"
        if k == pygame.K_TAB:
            return "commit"
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
        elif k in (pygame.K_UP, pygame.K_DOWN) and self.multiline:
            self._vertical(-1 if k == pygame.K_UP else 1, shift)
        elif k == pygame.K_HOME:
            spans = self._spans() if (self.multiline and not ctrl) else None
            self._move(spans[_line_of(spans, self.cursor)][0] if spans else 0, shift)
        elif k == pygame.K_END:
            spans = self._spans() if (self.multiline and not ctrl) else None
            self._move(spans[_line_of(spans, self.cursor)][1] if spans else len(self.value), shift)
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


def _id_char_ok(ch: str) -> bool:
    """Characters allowed in a new entity ID."""
    return ch.isascii() and (ch.isalnum() or ch in "_- ")


# ── Small vector icons (same primitives + look as character_creator.py) ─

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


def _ic_trash(surface, rect, color, width=2):
    cx, cy = rect.center
    s = min(rect.w, rect.h)
    body = pygame.Rect(0, 0, int(s * 0.58), int(s * 0.56))
    body.centerx = cx
    body.top = int(cy - s * 0.10)
    uk.draw_rect_on(surface, color, body, width, 2)
    lid = pygame.Rect(0, 0, int(s * 0.80), max(2, int(s * 0.10)))
    lid.centerx = cx
    lid.bottom = body.top + 1
    uk.draw_rect_on(surface, color, lid, width, 1)
    handle = pygame.Rect(0, 0, int(s * 0.30), max(2, int(s * 0.14)))
    handle.centerx = cx
    handle.bottom = lid.top + 2
    uk.draw_rect_on(surface, color, handle, width, 2)
    for i in (-1, 1):
        x = cx + i * s * 0.13
        uk.draw_line_on(surface, color, (x, body.top + 5), (x, body.bottom - 4), width)


def _make_chevron(direction):
    def draw(surface, rect, color, width=2):
        cx, cy = rect.center
        s = min(rect.w, rect.h) * 0.26
        if direction == "up":
            pts = [(cx - s, cy + s * 0.6), (cx, cy - s * 0.6), (cx + s, cy + s * 0.6)]
        else:
            pts = [(cx - s, cy - s * 0.6), (cx, cy + s * 0.6), (cx + s, cy - s * 0.6)]
        uk.draw_line_on(surface, color, pts[0], pts[1], width)
        uk.draw_line_on(surface, color, pts[1], pts[2], width)
    return draw


_ic_up = _make_chevron("up")
_ic_down = _make_chevron("down")


# ══════════════════════════════════════════════════════════════════════
#  EntityCreator overlay
# ══════════════════════════════════════════════════════════════════════

class EntityCreator:
    """Dev-tool overlay for browsing / editing enemy, NPC and critter
    configs. Lives inside the host game's main loop - it does not own the
    display, event polling, or the clock. Same lifecycle contract as
    CharacterCreator: toggle() / handle_input(event) / update(dt) /
    draw(surface, dt)."""

    def __init__(self, screen_width: int, screen_height: int):
        self.screen_width = int(screen_width)
        self.screen_height = int(screen_height)
        self.active = False

        pygame.font.init()
        self._init_fonts()

        # ── model ──────────────────────────────────────────────────
        self.kind = KIND_ENEMY
        self.ids: list[str] = []
        self.configured: set[str] = set()
        self.selected_id: Optional[str] = None
        self.cfg: dict = {}
        self.dirty = False
        self.preview_variants: list[dict] = []

        # ── status / dialog ────────────────────────────────────────
        self.status_msg = ""
        self.status_ok = True
        self.status_timer = 0.0
        # None, or {'kind': 'confirm'|'input', 'title', 'message', 'on_confirm', ...}
        self.dialog: Optional[dict] = None

        # ── per-frame UI plumbing (same shape as CharacterCreator's) ─
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
        self._ml_info: dict = {}
        self._ml_info_new: dict = {}
        self._tip: Optional[str] = None
        self._scroll = 0.0
        self._content_h = 0
        self._roster_scroll = 0.0
        self._close_requested = False

        self._layout()

    # ── setup ──────────────────────────────────────────────────────
    @staticmethod
    def _font_root() -> str:
        anchored = BASE_DIR / "assets" / "ui" / "fonts"
        return str(anchored) if anchored.exists() else os.path.join("assets", "ui", "fonts")

    def _init_fonts(self) -> None:
        """Same bitmap family as DevMenu / CharacterCreator: title text uses
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

        icon_path = os.path.join(str(BASE_DIR), "assets", "ui", "dev_menu", "icons", "back.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.join("assets", "ui", "dev_menu", "icons", "back.png")
        self._back_icon = self._load_png_icon(icon_path, 34)

        trash_icon_path = os.path.join(str(BASE_DIR), "assets", "ui", "dev_menu", "icons", "trash.png")
        if not os.path.exists(trash_icon_path):
            trash_icon_path = os.path.join("assets", "ui", "dev_menu", "icons", "trash.png")
        self._trash_icon = self._load_png_icon(trash_icon_path, 24)

        plus_icon_path = os.path.join(str(BASE_DIR), "assets", "ui", "dev_menu", "icons", "plus.png")
        if not os.path.exists(plus_icon_path):
            plus_icon_path = os.path.join("assets", "ui", "dev_menu", "icons", "plus.png")
        self._plus_icon = self._load_png_icon(plus_icon_path, 24)

        save_icon_path = os.path.join(str(BASE_DIR), "assets", "ui", "dev_menu", "icons", "save.png")
        if not os.path.exists(save_icon_path):
            save_icon_path = os.path.join("assets", "ui", "dev_menu", "icons", "save.png")
        self._save_icon = self._load_png_icon(save_icon_path, 26)

    @staticmethod
    def _load_png_icon(path: str, box: int) -> Optional[pygame.Surface]:
        """Same crop + integer-blow-up + point-sample path DevMenu /
        CharacterCreator use for their header icons. Returns None when the
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
        # Same proportions DevMenu / CharacterCreator use for their bars.
        self.header_h = max(86, round(h * 0.12))
        self.footer_h = max(42, round(h * 0.065))
        m = 32
        top = self.header_h + 20
        bottom = h - self.footer_h - 20
        area_h = max(240, bottom - top)

        sm, md = self.f_sm, self.f_md
        self.m_field_h = max(40, md.line_h + 20)
        self.m_slider_h = sm.cap_h + 8 + 16
        self.m_pitch = self.m_slider_h + 22
        self.m_pill_h = max(44, md.line_h + 22)

        back = max(40, round(self.header_h * 0.55))
        self.back_rect = pygame.Rect(0, 0, back, back)
        self.back_rect.left = m
        self.back_rect.centery = self.header_h // 2
        self.save_rect = pygame.Rect(0, 0, back, back)
        self.save_rect.right = w - m
        self.save_rect.centery = self.header_h // 2

        side_w = _clamp(round(w * 0.225), 250, 330)
        prev_h = _clamp(round(area_h * 0.30), 150, 220)
        self.list_rect = pygame.Rect(m, top, side_w, max(160, area_h - prev_h - 16))
        self.prev_rect = pygame.Rect(m, self.list_rect.bottom + 16, side_w, prev_h)
        self.roster_view = pygame.Rect(self.list_rect.x + 8, self.list_rect.y + 46,
                                       side_w - 16, max(40, self.list_rect.h - 46 - 62))
        self.roster_btn_y = self.list_rect.bottom - 52

        main_x = m + side_w + 24
        main_w = w - m - main_x
        self.tab_h = 46
        gap = 8

        n = len(KIND_ORDER)
        avail = main_w - gap * (n - 1)
        widths = [avail // n] * n
        widths[-1] += avail - sum(widths)
        self.kind_tab_rects = []
        tx = main_x
        for wd in widths:
            self.kind_tab_rects.append(pygame.Rect(tx, top, wd, self.tab_h))
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
            self.dialog = None
            self._drag = None
            self._blur()
            self._refresh_list()

    def _shutdown(self) -> None:
        self._blur()
        self._close_dialog()
        self._drag = None
        self.active = False
        uk.set_text_cursor(False)
        uk.set_hand_cursor(False)
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

    # ── entity list management ────────────────────────────────────
    def _refresh_list(self) -> None:
        self.ids = discover_all_ids(self.kind)
        self.configured = set(discover_configured_ids(self.kind))
        keep = self.selected_id if self.selected_id in self.ids else (self.ids[0] if self.ids else None)
        if keep:
            self._load_entity(keep)
        else:
            self._clear_selection()

    def _clear_selection(self) -> None:
        self.selected_id = None
        self.cfg = {}
        self.dirty = False
        self.preview_variants = []

    def _switch_kind(self, kind: str) -> None:
        if kind == self.kind:
            return
        self._blur()
        self.kind = kind
        self.selected_id = None
        self._scroll = 0.0
        self._roster_scroll = 0.0
        self._refresh_list()

    def _load_entity(self, entity_id: str) -> None:
        self.selected_id = entity_id
        self.cfg = load_config(self.kind, entity_id)
        self.dirty = False
        self._load_preview()

    def _switch_entity(self, entity_id: str) -> None:
        if entity_id != self.selected_id:
            self._blur()
        self._load_entity(entity_id)

    def _load_preview(self) -> None:
        """Load an idle-down thumbnail for every variant folder this entity
        has (see scan_variants()), so the panel can show what it'll
        actually look like in-game. Missing art just means a 'no sprite
        yet' box - this never blocks editing/saving stats."""
        self.preview_variants = []
        if not self.selected_id:
            return
        entity_type = self.cfg.get("entity_type", "") if self.kind == KIND_ENEMY else ""
        default_size = 16 if self.kind == KIND_CRITTER else 32
        for variant_type in scan_variants(self.kind, self.selected_id, entity_type):
            sprite = _load_preview_sprite(
                self.kind, self.selected_id, variant_type, entity_type, size=96,
                frame_w=self.cfg.get("width", default_size),
                frame_h=self.cfg.get("height", default_size),
            )
            self.preview_variants.append({
                "type": variant_type,
                "name": "Default" if variant_type == "default" else variant_type.replace("_", " ").title(),
                "sprite": sprite,
            })

    def _on_pick_entity(self, entity_id: str) -> None:
        if entity_id != self.selected_id:
            self._switch_entity(entity_id)

    def _set_status(self, msg: str, ok: bool = True) -> None:
        self.status_msg = msg
        self.status_ok = ok
        self.status_timer = 3.0

    def _mark_dirty(self) -> None:
        self.dirty = True

    # ── save / delete / new / reorder ───────────────────────────────
    def _save(self) -> None:
        if not self.selected_id:
            return
        self._blur()
        try:
            save_config(self.kind, self.cfg)
        except OSError as exc:
            self._set_status(f"Could not save: {exc.strerror or exc}", ok=False)
            return
        self.configured.add(self.selected_id)
        self.dirty = False
        self._set_status(f"Saved  {self.cfg['id']}.json")

    def _ask_delete_entity(self) -> None:
        if not self.selected_id:
            return
        eid = self.selected_id
        self._open_confirm("Delete config", f"Delete the saved config for '{eid}'? This can't be undone - "
                           "the sprite folder itself is untouched.", self._do_delete, "Delete")

    def _do_delete(self) -> None:
        eid = self.selected_id
        if not eid:
            return
        delete_config(self.kind, eid)
        self.configured.discard(eid)
        self._set_status(f"Deleted config for {eid}", ok=False)
        self._load_entity(eid)   # reloads as defaults

    def _open_new_entity(self) -> None:
        self._open_input(f"New {KIND_TAB_LABELS[self.kind][:-1]}", KIND_NEW_PROMPTS[self.kind], self._do_create)

    def _do_create(self, new_id: str) -> None:
        new_id = new_id.strip().lower().replace(" ", "_")
        if not new_id:
            return
        if new_id not in self.ids:
            self.ids.append(new_id)
            save_entity_order(self.kind, self.ids)
        self._switch_entity(new_id)
        self._ensure_roster_visible(self.ids.index(new_id))
        self._set_status(f"Created {new_id} - remember to add its sprite folder")

    def _move_selected(self, delta: int) -> None:
        if not self.selected_id or self.selected_id not in self.ids:
            return
        i = self.ids.index(self.selected_id)
        j = i + delta
        if not (0 <= j < len(self.ids)):
            return
        self.ids[i], self.ids[j] = self.ids[j], self.ids[i]
        save_entity_order(self.kind, self.ids)
        self._ensure_roster_visible(j)
        self._set_status(f"{KIND_TAB_LABELS[self.kind][:-1]} order updated")

    def _ensure_roster_visible(self, index: int) -> None:
        pitch = ROSTER_ROW_H + ROSTER_ROW_GAP
        top, bottom = index * pitch, index * pitch + ROSTER_ROW_H
        if top < self._roster_scroll:
            self._roster_scroll = top
        elif bottom > self._roster_scroll + self.roster_view.h:
            self._roster_scroll = bottom - self.roster_view.h

    # ── dialogs (non-blocking) ────────────────────────────────────
    def _open_confirm(self, title: str, message: str, on_confirm, confirm_label: str = "Confirm",
                      danger: bool = True) -> None:
        self._blur()
        self.dialog = {"kind": "confirm", "title": title, "message": message, "on_confirm": on_confirm,
                       "confirm_label": confirm_label, "danger": danger}

    def _open_input(self, title: str, message: str, on_confirm, default: str = "",
                    confirm_label: str = "Create") -> None:
        self._blur()
        edit = _TextEdit(default, multiline=False, max_len=32, allowed=_id_char_ok)
        self.dialog = {"kind": "input", "title": title, "message": message, "on_confirm": on_confirm,
                       "edit": edit, "error": "", "confirm_label": confirm_label, "danger": False,
                       "scroll": 0, "selecting": False}
        self._set_key_repeat(True)

    def _close_dialog(self) -> None:
        if self.dialog is not None:
            self.dialog = None
            if self._focus is None:
                self._set_key_repeat(False)

    def _dialog_rects(self) -> dict:
        d = self.dialog
        sw, sh = self.screen_width, self.screen_height
        W = 500 if d["kind"] == "input" else 460
        pad = 28
        lines = self.f_md.wrap(d["message"], W - pad * 2)
        line_h = self.f_md.line_h + 6
        y = pad + self.f_lg.line_h + 14
        msg_y = y
        y += len(lines) * line_h + 12
        field_y = y
        if d["kind"] == "input":
            y += self.m_field_h + 8 + self.f_sm.line_h + 6
        y += 10
        btn_y = y
        H = btn_y + self.m_pill_h + pad
        panel = pygame.Rect(0, 0, W, H)
        panel.center = (sw // 2, sh // 2)
        bw = (W - pad * 2 - 14) // 2
        return {
            "panel": panel, "lines": lines, "line_h": line_h, "pad": pad,
            "msg_y": panel.y + msg_y,
            "field": pygame.Rect(panel.x + pad, panel.y + field_y, W - pad * 2, self.m_field_h),
            "hint_y": panel.y + field_y + self.m_field_h + 8,
            "ok": pygame.Rect(panel.x + pad, panel.y + btn_y, bw, self.m_pill_h),
            "cancel": pygame.Rect(panel.right - pad - bw, panel.y + btn_y, bw, self.m_pill_h),
        }

    def _dialog_submit(self) -> None:
        d = self.dialog
        if d["kind"] == "input":
            v = d["edit"].value.strip().lower().replace(" ", "_")
            if not v:
                d["error"] = "ID cannot be empty"
                return
            cb = d["on_confirm"]
            self._close_dialog()
            cb(v)
        else:
            cb = d["on_confirm"]
            self._close_dialog()
            cb()

    def _dialog_event(self, event: pygame.event.Event) -> None:
        d = self.dialog
        r = self._dialog_rects()
        if event.type == pygame.KEYDOWN:
            if d["kind"] == "input":
                res = d["edit"].key(event)
                if res == "commit":
                    self._dialog_submit()
                elif res == "cancel":
                    self._close_dialog()
                elif res == "changed":
                    d["error"] = ""
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._dialog_submit()
            elif event.key == pygame.K_ESCAPE:
                self._close_dialog()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            if r["ok"].collidepoint(pos):
                self._dialog_submit()
            elif r["cancel"].collidepoint(pos):
                self._close_dialog()
            elif d["kind"] == "input" and r["field"].collidepoint(pos):
                edit = d["edit"]
                idx = _index_at_x(self.f_md, edit.value, pos[0] - (r["field"].x + 14) + d["scroll"])
                if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                    if edit.anchor is None:
                        edit.anchor = edit.cursor
                else:
                    edit.anchor = idx
                edit.cursor = idx
                edit.blink = 0.0
                d["selecting"] = True
        elif event.type == pygame.MOUSEMOTION and d["kind"] == "input" and d.get("selecting"):
            edit = d["edit"]
            x = _clamp(event.pos[0], r["field"].left + 14, r["field"].right - 14)
            edit.cursor = _index_at_x(self.f_md, edit.value, x - (r["field"].x + 14) + d["scroll"])
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.dialog is not None:
            d["selecting"] = False

    # ── text focus ─────────────────────────────────────────────────
    def _focus_text(self, key, get, set_, multiline=False, max_len=600, allowed=None):
        edit = _TextEdit(get() or "", multiline=multiline, max_len=max_len, allowed=allowed)
        self._focus = SimpleNamespace(key=key, edit=edit, set=set_)
        self._set_key_repeat(True)
        return edit

    def _blur(self) -> None:
        self._focus = None
        if self.dialog is None:
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

        if self.dialog is not None:
            self._dialog_event(event)
            return None

        if et == pygame.KEYDOWN:
            mods = getattr(event, "mod", 0) | pygame.key.get_mods()
            if (mods & (pygame.KMOD_CTRL | pygame.KMOD_META)) and event.key == pygame.K_s:
                self._save()
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
        return max(0, self._content_h - (self.panel_rect.h - 8))

    def _wheel(self, dy: int) -> None:
        pos = self._mouse
        if self.roster_view.collidepoint(pos):
            total = len(self.ids) * (ROSTER_ROW_H + ROSTER_ROW_GAP)
            self._roster_scroll = _clamp(self._roster_scroll - dy * (ROSTER_ROW_H + ROSTER_ROW_GAP),
                                         0, max(0, total - self.roster_view.h))
            return
        for key, (rect, total, rows) in self._ml_info.items():
            if rect.collidepoint(pos) and total > rows:
                self._tscroll[key] = _clamp(self._tscroll.get(key, 0) - dy, 0, total - rows)
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
        dt = min(dt, 1 / 20)
        self._dt = max(dt, 1 / 240)
        self._updated = True
        self._pulse += dt
        if self.status_timer > 0:
            self.status_timer -= dt
        if self._focus is not None:
            self._focus.edit.blink += dt
        if self.dialog is not None and self.dialog.get("edit") is not None:
            self.dialog["edit"].blink += dt
        # Cursor itself is resolved in draw() (_resolve_cursor), once every
        # widget for the frame has actually registered its rect — see that
        # method's docstring for why doing it here instead would mean
        # judging hover against the previous frame's (possibly stale) rects.

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
        self._ml_info_new = {}
        self._tip = None
        self._vp = None
        modal = self.dialog is not None
        self._hm = _OFFSCREEN if modal else self._mouse

        w, h = self.screen_width, self.screen_height
        uk.draw_rect_on(screen, _BG, pygame.Rect(0, 0, w, h), 0, 0)
        uk.draw_rect_on(screen, _BAND, pygame.Rect(0, self.header_h, w, h - self.header_h - self.footer_h), 0, 0)

        self._draw_sidebar(screen)
        self._draw_kind_tabs(screen)
        self._draw_content(screen)
        self._draw_header(screen)
        self._draw_footer(screen)

        self._text_rects = self._text_rects_new
        self._ml_info = self._ml_info_new
        if modal:
            self._draw_dialog(screen)

        # OS cursor, resolved dead last — after every widget this frame has
        # had a chance to register a hit/text-field rect. See _resolve_cursor.
        self._resolve_cursor()

    def _resolve_cursor(self) -> None:
        """Switch the OS cursor to an I-beam over a text field, a hand over
        anything clickable (buttons, tabs, list rows, sliders, chips...),
        or back to the plain arrow otherwise — same priority and
        once-per-frame convention as CharacterCreator._resolve_cursor (I-beam
        always wins where a field and a button happen to overlap).

        While the confirm/rename/delete dialog is open the base UI is
        blocked (see `modal` in draw()), so only the dialog's own field/
        ok/cancel rects should count — the underlying tabs and buttons are
        still sitting in self._hits with their real coordinates, but
        clicking them does nothing while the dialog is up, so hovering them
        shouldn't show a hand either.
        """
        if self.dialog is not None:
            r = self._dialog_rects()
            hovering_text_field = self.dialog["kind"] == "input" and r["field"].collidepoint(self._mouse)
            hovering_widget = (not hovering_text_field
                                and (r["ok"].collidepoint(self._mouse) or r["cancel"].collidepoint(self._mouse)))
        else:
            hovering_text_field = any(rect.collidepoint(self._mouse) for rect in self._text_rects)
            hovering_widget = (not hovering_text_field
                                and any(hit["rect"].collidepoint(self._mouse) for hit in self._hits))
        uk.set_text_cursor(hovering_text_field)
        uk.set_hand_cursor(hovering_widget)

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
        # panel/slider backgrounds do - so a label whose row straddles the
        # edge of the scroll viewport was rendering in full, with only the
        # portion inside the clip actually hidden and the rest bleeding
        # onto whatever sits outside it (e.g. the kind-tab row). Rather
        # than trust the clip, crop the source surface ourselves to
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

    def _icon_trash_png(self, screen, rect, color, width=2) -> None:
        if self._trash_icon is not None:
            uk.blit_surface(screen, self._trash_icon, self._trash_icon.get_rect(center=rect.center))
        else:
            _ic_trash(screen, rect, color, width)

    def _icon_plus_png(self, screen, rect, color, width=3) -> None:
        if self._plus_icon is not None:
            uk.blit_surface(screen, self._plus_icon, self._plus_icon.get_rect(center=rect.center))
        else:
            _ic_plus(screen, rect, color, width)

    def _icon_save_png(self, screen, rect, color, width=3) -> None:
        if self._save_icon is not None:
            uk.blit_surface(screen, self._save_icon, self._save_icon.get_rect(center=rect.center))
        else:
            _ic_check(screen, rect, color, width)

    # ══════════════════════════════════════════════════════════════
    #  Drawing: form widgets
    # ══════════════════════════════════════════════════════════════

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

    def _slider(self, screen, key, x, y, w, label, value, vmin, vmax, step, fmt, setter) -> int:
        sm = self.f_sm
        self._text_top(screen, sm, label.upper(), _T.TEXT_MUTED, x, y)
        self._text_top(screen, sm, fmt.format(value), _T.TEXT_SECONDARY, x + w, y, "r", dyn=True)
        cy = y + sm.cap_h + 16
        track = pygame.Rect(x, cy - 3, w, 6)
        dragging = self._drag is not None and self._drag.get("key") == key
        hover_zone = pygame.Rect(x - 8, cy - 12, w + 16, 24)
        t = self._anim(key, self._hov(hover_zone) or dragging)
        frac = _clamp((value - vmin) / (vmax - vmin), 0.0, 1.0) if vmax > vmin else 0.0
        uk.draw_rect_on(screen, _TRACK, track, 0, 3)
        fill = pygame.Rect(x, cy - 3, max(0, int(w * frac)), 6)
        if fill.w > 0:
            uk.draw_rect_on(screen, uk.lerp_color(_T.GOLD, _T.GOLD_BRIGHT, t), fill, 0, 3)
        tx = x + int(w * frac)
        if t > 0:
            uk.draw_soft_glow(screen, (tx, cy), 20, _T.GOLD, max_alpha=int(38 * t))
        uk.draw_circle_on(screen, uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t), (tx, cy), 8)
        uk.draw_circle_on(screen, uk.lerp_color(_T.CARD_BORDER, _T.GOLD, t), (tx, cy), 8, 2)

        def apply(pos, x=x, w=w):
            f = _clamp((pos[0] - x) / max(1, w), 0.0, 1.0)
            raw = vmin + f * (vmax - vmin)
            if step:
                raw = round(raw / step) * step
            setter(_clamp(raw, vmin, vmax))

        self._add_hit(hover_zone, key=key, down=apply, drag=apply)
        return self.m_slider_h

    def _slider_grid(self, screen, x, y, w, specs) -> int:
        """Lay sliders out row-major in as many columns as fit."""
        gap = 32
        cols = _clamp((w + gap) // (260 + gap), 1, 3)
        cw = (w - gap * (cols - 1)) // cols
        for i, sp in enumerate(specs):
            r, c = divmod(i, cols)
            self._slider(screen, sp["key"], x + c * (cw + gap), y + r * self.m_pitch, cw, sp["label"],
                         sp["get"](), sp["vmin"], sp["vmax"], sp["step"], sp["fmt"], sp["set"])
        rows = (len(specs) + cols - 1) // cols
        return rows * self.m_pitch

    def _num_setter(self, container, key, kind):
        def set_(v):
            if kind == "int":
                v = int(round(v))
            else:
                v = round(float(v), {"f1": 1, "f2": 2}[kind])
            if container.get(key) != v:
                container[key] = v
                self._mark_dirty()
        return set_

    def _segmented(self, screen, key, x, y, w, options, current, on_select) -> int:
        fh = self.m_field_h
        rect = pygame.Rect(x, y, w, fh)
        self._panel(screen, rect, _FIELD, _T.CARD_BORDER)
        seg_w = w // len(options)
        for i, (val, label) in enumerate(options):
            r = pygame.Rect(x + i * seg_w, y, seg_w if i < len(options) - 1 else w - seg_w * i, fh)
            inner = r.inflate(-8, -8)
            active = val == current
            t = self._anim((key, val), self._hov(r) and not active)
            if active:
                self._panel(screen, inner, (48, 39, 19), _T.GOLD, 1, 8)
            elif t > 0:
                uk.draw_rect_on(screen, uk.lerp_color(_FIELD, _CARD_HI, t), inner, 0, 8)
            fg = _T.GOLD_BRIGHT if active else uk.lerp_color(_T.TEXT_MUTED, _T.TEXT_PRIMARY, t)
            self._text_mid(screen, self.f_md, label, fg, r.centerx, r.centery, "c")
            self._add_hit(r, key=(key, val), down=lambda p, v=val: on_select(v))
        return fh

    def _segmented_row(self, screen, key, x, y, w, label, options, current, on_select) -> int:
        """A _segmented() preceded by its small caps field label, matching
        the label-above-field rhythm every other field on this tab uses."""
        h = self._field_label(screen, label, x, y)
        y += h
        h += self._segmented(screen, key, x, y, w, options, current, on_select)
        return h

    # -- text fields --------------------------------------------------
    def _text_field(self, screen, key, rect, get, set_, placeholder="", multiline=False,
                    max_len=600, allowed=None) -> None:
        md = self.f_md
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
        pad = 14
        blink_on = edit is not None and int(edit.blink * 2) % 2 == 0
        fg = _T.TEXT_PRIMARY if (focus or hov) else _T.TEXT_SECONDARY
        if self.dialog is None:
            self._text_rects_new.append(rect.clip(self._vp) if self._vp is not None else pygame.Rect(rect))

        if not multiline:
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
                    uk.draw_rect_on(screen, (*_T.GOLD, 70), pygame.Rect(sx, cy - md.cap_h // 2 - 4, ex - sx,
                                                                      md.line_h + 8), 0, 3)
                self._text_mid(screen, md, value, fg, inner.x - scroll, cy, dyn=True)
                if blink_on:
                    cx = inner.x - scroll + md.width(value[:edit.cursor])
                    uk.draw_rect_on(screen, _T.GOLD_BRIGHT,
                                    pygame.Rect(cx, cy - md.cap_h // 2 - 4, 2, md.line_h + 8), 0, 0)
            else:
                self._text_mid(screen, md, md.fit(value, inner.w), fg, inner.x, cy, dyn=True)
            self._pop_clip(screen, old)

            def idx_at(pos, rect=rect, key=key):
                sc = self._tscroll.get(key, 0)
                cur = self._focus.edit.value if self._focus and self._focus.key == key else value
                return _index_at_x(md, cur, pos[0] - (rect.x + pad) + sc)
        else:
            inner = pygame.Rect(rect.x + pad, rect.y + 10, rect.w - 2 * pad, rect.h - 20)
            lh = md.line_h + 6
            rows = max(1, inner.h // lh)
            spans = _wrap_spans(md, value, inner.w)
            if edit:
                edit.view = (md, inner.w)
            scroll = int(_clamp(self._tscroll.get(key, 0), 0, max(0, len(spans) - rows)))
            if edit:
                line = _line_of(spans, edit.cursor)
                if line < scroll:
                    scroll = line
                elif line >= scroll + rows:
                    scroll = line - rows + 1
                scroll = int(_clamp(scroll, 0, max(0, len(spans) - rows)))
            self._tscroll[key] = scroll
            self._ml_info_new[key] = (pygame.Rect(rect), len(spans), rows)
            old = self._push_clip(screen, inner)
            if not value and not focus:
                self._text_top(screen, md, placeholder, _T.TEXT_DIM, inner.x, inner.y)
            for i in range(scroll, min(len(spans), scroll + rows)):
                s, e = spans[i]
                ly = inner.y + (i - scroll) * lh
                if focus and edit.has_sel():
                    a, b = edit.sel_range()
                    lo, hi = max(a, s), min(b, e)
                    if lo < hi or (a <= s and b > e):
                        sx = inner.x + md.width(value[s:max(lo, s)])
                        ex = inner.x + md.width(value[s:min(max(hi, s), e)]) + (6 if b > e else 0)
                        if ex > sx:
                            uk.draw_rect_on(screen, (*_T.GOLD, 70),
                                            pygame.Rect(sx, ly - 4, ex - sx, md.line_h + 8), 0, 3)
                self._text_top(screen, md, value[s:e], fg, inner.x, ly, dyn=True)
            if focus and blink_on:
                line = _line_of(spans, edit.cursor)
                if scroll <= line < scroll + rows:
                    s, e = spans[line]
                    cx = inner.x + md.width(value[s:_clamp(edit.cursor, s, e)])
                    ly = inner.y + (line - scroll) * lh
                    uk.draw_rect_on(screen, _T.GOLD_BRIGHT, pygame.Rect(cx, ly - 4, 2, md.line_h + 8), 0, 0)
            self._pop_clip(screen, old)
            if len(spans) > rows:
                frac = scroll / max(1, len(spans) - rows)
                th = max(14, int(inner.h * rows / len(spans)))
                ty = inner.y + int((inner.h - th) * frac)
                uk.draw_rect_on(screen, _T.CHIP_BORDER, pygame.Rect(rect.right - 8, ty, 3, th), 0, 1)

            def idx_at(pos, rect=rect, key=key, inner=inner, lh=lh):
                cur = self._focus.edit.value if self._focus and self._focus.key == key else value
                sp = _wrap_spans(md, cur, inner.w)
                sc = self._tscroll.get(key, 0)
                ln = int(_clamp(sc + (pos[1] - inner.y) // lh, 0, len(sp) - 1))
                s, e = sp[ln]
                return s + _index_at_x(md, cur[s:e], pos[0] - inner.x)

        def click(pos):
            shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
            if self._focus is None or self._focus.key != key:
                self._focus_text(key, get, set_, multiline, max_len, allowed)
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

        self._add_hit(rect, key=key, down=click, drag=drag)

    def _static_field(self, screen, rect, text) -> None:
        self._panel(screen, rect, _INSET, _T.CARD_BORDER)
        self._text_mid(screen, self.f_md, text, _T.TEXT_MUTED, rect.x + 14, rect.centery, max_w=rect.w - 28)

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
            _ic_up(screen, r, uk.lerp_color(_T.TEXT_SECONDARY, _T.GOLD, t), 3)
        self._add_hit(r, key="back", down=lambda p: setattr(self, "_close_requested", True),
                      tip="Close the Entity Creator")

        cx = w // 2
        self._text_mid(screen, self.f_title, "ENTITY CREATOR", _T.TEXT_PRIMARY, cx, hh // 2, "c")

        has = bool(self.selected_id)
        self._icon_btn(screen, "save", self.save_rect, self._icon_save_png, self._save,
                       enabled=has, tip="Save this entity  (Ctrl+S)")

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

    # ── sidebar ────────────────────────────────────────────────────
    def _draw_sidebar(self, screen) -> None:
        lr = self.list_rect
        self._panel(screen, lr, _T.PANEL_BG, _T.PANEL_BORDER, 1, 12)
        self._text_top(screen, self.f_sm, KIND_TAB_LABELS[self.kind].upper(), _T.TEXT_MUTED, lr.x + 18, lr.y + 18)
        self._text_top(screen, self.f_sm, str(len(self.ids)), _T.TEXT_DIM, lr.right - 18, lr.y + 18, "r", dyn=True)

        rv = self.roster_view
        pitch = ROSTER_ROW_H + ROSTER_ROW_GAP
        total = len(self.ids) * pitch
        self._roster_scroll = _clamp(self._roster_scroll, 0, max(0, total - rv.h))
        accent = KIND_ACCENT[self.kind]
        old_vp = self._vp
        self._vp = rv
        old = self._push_clip(screen, rv)
        if not self.ids:
            root = KIND_SPRITE_ROOT_LABELS[self.kind]
            self._text_mid(screen, self.f_sm, f"No folders found in {root}", _T.TEXT_DIM, rv.centerx, rv.y + 30,
                           "c", max_w=rv.w - 20)
        for i, eid in enumerate(self.ids):
            ry = rv.y + i * pitch - int(self._roster_scroll)
            row = pygame.Rect(rv.x, ry, rv.w - (8 if total > rv.h else 0), ROSTER_ROW_H)
            if row.bottom < rv.y or row.y > rv.bottom:
                continue
            sel = eid == self.selected_id
            t = self._anim(("ent", eid), self._hov(row) and not sel)
            base = _SEL if sel else uk.lerp_color(_CARD, _CARD_HI, t)
            border = _T.GOLD if sel else uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t)
            self._panel(screen, row, base, border, 1, 9)
            unconfigured = eid not in self.configured
            dot_col = _T.TEXT_DIM if unconfigured else accent
            dot = (row.x + 22, row.centery)
            if (sel or t > 0) and not unconfigured:
                uk.draw_soft_glow(screen, dot, 16, dot_col, max_alpha=int(50 if sel else 40 * t))
            uk.draw_circle_on(screen, dot_col, dot, 7)
            if not unconfigured:
                uk.draw_circle_on(screen, uk.lerp_color(dot_col, (255, 255, 255), 0.4), dot, 7, 1)
            fg = _T.TEXT_PRIMARY if sel else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
            label = eid if not unconfigured else f"{eid}  (new)"
            label_col = fg if not unconfigured else uk.lerp_color(_T.TEXT_MUTED, _T.GOLD_BRIGHT, 0.4 + 0.6 * t)
            self._text_mid(screen, self.f_md, label, label_col, row.x + 42, row.centery, max_w=row.w - 42 - 16)
            self._add_hit(row, key=("ent", eid), down=lambda p, e=eid: self._on_pick_entity(e))
        self._pop_clip(screen, old)
        self._vp = old_vp
        if total > rv.h:
            frac = self._roster_scroll / max(1, total - rv.h)
            th = max(24, int(rv.h * rv.h / total))
            ty = rv.y + int((rv.h - th) * frac)
            uk.draw_rect_on(screen, _T.CHIP_BORDER, pygame.Rect(rv.right - 4, ty, 3, th), 0, 1)

        # action row
        by = self.roster_btn_y
        bx, gap = lr.x + 12, 8
        sq = 40
        has = bool(self.selected_id) and self.selected_id in self.ids
        idx = self.ids.index(self.selected_id) if has else -1
        self._icon_btn(screen, "e_up", pygame.Rect(bx, by, sq, sq), _ic_up, lambda: self._move_selected(-1),
                       enabled=has and idx > 0, tip="Move up in the menu")
        self._icon_btn(screen, "e_dn", pygame.Rect(bx + sq + gap, by, sq, sq), _ic_down,
                       lambda: self._move_selected(1), enabled=has and idx < len(self.ids) - 1,
                       tip="Move down in the menu")
        new_r = pygame.Rect(bx + 2 * (sq + gap), by, sq, sq)
        self._icon_btn(screen, "e_new", new_r, self._icon_plus_png, self._open_new_entity,
                       tip=f"Create a new {KIND_TAB_LABELS[self.kind][:-1].lower()}")
        trash = pygame.Rect(new_r.right + gap, by, sq, sq)
        self._icon_btn(screen, "e_del", trash, self._icon_trash_png, self._ask_delete_entity, danger=True,
                       enabled=has and self.selected_id in self.configured,
                       tip="Delete this entity's saved config")

        self._draw_preview_panel(screen)

    def _draw_preview_panel(self, screen) -> None:
        pr = self.prev_rect
        self._panel(screen, pr, _T.PANEL_BG, _T.PANEL_BORDER, 1, 12)
        self._text_top(screen, self.f_sm, "PREVIEW", _T.TEXT_MUTED, pr.x + 18, pr.y + 18)
        stage = pygame.Rect(pr.x + 12, pr.y + 44, pr.w - 24, pr.h - 44 - 12)
        uk.draw_rect_on(screen, _INSET, stage, 0, 10)
        uk.draw_rect_on(screen, _T.CARD_BORDER, stage, 1, 10)

        if not self.selected_id:
            self._text_mid(screen, self.f_sm, "NO ENTITY SELECTED", _T.TEXT_DIM, stage.centerx, stage.centery, "c")
            return

        old = self._push_clip(screen, stage)
        variants = self.preview_variants or [{"type": "default", "name": "Default", "sprite": None}]
        one = len(variants) == 1
        box = min(stage.h - 20, 132) if one else min(int(stage.h * 0.7), 104)
        mx = stage.x + 14
        my = stage.y + (stage.h - box) // 2 if one else stage.y + 12
        self._draw_thumb(screen, variants[0], mx, my, box)
        if not one:
            tx, ty = mx + box + 16, stage.y + 12
            small = 56
            for v in variants[1:]:
                if tx + small > stage.right - 8:
                    break
                self._draw_thumb(screen, v, tx, ty, small)
                tx += small + 12
        self._pop_clip(screen, old)

    def _draw_thumb(self, screen, variant, x, y, size) -> None:
        rect = pygame.Rect(x, y, size, size)
        uk.draw_rect_on(screen, _INSET, rect, 0, 8)
        sprite = variant.get("sprite")
        if sprite is not None:
            img = sprite if sprite.get_width() == size else pygame.transform.smoothscale(sprite, (size, size))
            uk.blit_surface(screen, img, img.get_rect(center=rect.center), transient=True)
        else:
            self._text_mid(screen, self.f_sm, "?", _T.TEXT_DIM, rect.centerx, rect.centery, "c")
        uk.draw_rect_on(screen, _T.CARD_BORDER, rect, 1, 8)
        label = variant.get("name", "")
        if label and size >= 56:
            self._text_top(screen, self.f_sm, self.f_sm.fit(label, size + 24), _T.TEXT_DIM, x, y + size + 4)

    # ── kind tabs ──────────────────────────────────────────────────
    def _draw_kind_tabs(self, screen) -> None:
        for i, kind in enumerate(KIND_ORDER):
            r = self.kind_tab_rects[i]
            active = kind == self.kind
            t = self._anim(("kind", kind), self._hov(r) and not active)
            base = _SEL if active else uk.lerp_color(_CARD, _CARD_HI, t)
            border = _T.GOLD if active else uk.lerp_color(_T.CARD_BORDER, _T.GOLD, 0.78 * t)
            self._panel(screen, r, base, border, 1, 10)
            if active:
                uk.draw_rect_on(screen, _T.GOLD, pygame.Rect(r.x + 14, r.bottom - 4, r.w - 28, 3), 0, 1)
            fg = _T.GOLD_BRIGHT if active else uk.lerp_color(_T.TEXT_SECONDARY, _T.TEXT_PRIMARY, t)
            label = self.f_md.fit(KIND_TAB_LABELS[kind], r.w - 28)
            x = r.centerx - self.f_md.width(label) // 2
            self._text_mid(screen, self.f_md, label, fg, x, r.centery - 1)
            self._add_hit(r, key=("kind", kind), down=lambda p, k=kind: self._switch_kind(k))

    # ── scrolling content panel ────────────────────────────────────
    def _draw_content(self, screen) -> None:
        pr = self.panel_rect
        self._panel(screen, pr, _T.PANEL_BG, _T.PANEL_BORDER, 1, 12)
        if not self.selected_id:
            self._text_mid(screen, self.f_lg, "No entity selected", _T.TEXT_MUTED, pr.centerx, pr.centery - 14, "c")
            self._text_mid(screen, self.f_md, "Pick one from the list, or create one with the + button.",
                           _T.TEXT_DIM, pr.centerx, pr.centery + 22, "c")
            return
        pad = 24
        vp = pygame.Rect(pr.x + 3, pr.y + 3, pr.w - 6, pr.h - 6)
        self._scroll = _clamp(self._scroll, 0, self._max_scroll())
        fn = {KIND_ENEMY: self._tab_enemy, KIND_NPC: self._tab_npc, KIND_CRITTER: self._tab_critter}[self.kind]
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
            th = max(30, int(track.h * (pr.h - 8) / self._content_h))
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

    # ── Display Name + ID header shared by every kind's tab ─────────
    def _identity_block(self, screen, x, y, w) -> int:
        cfg = self.cfg
        y0 = y
        y += self._caption(screen, "Identity", x, y, w)
        name_w = min(w, 420)
        y += self._field_label(screen, "Display Name", x, y)

        def set_name(v):
            if cfg.get("display_name") != v:
                cfg["display_name"] = v
                self._mark_dirty()

        self._text_field(screen, "name", pygame.Rect(x, y, name_w, self.m_field_h),
                         lambda: cfg.get("display_name", ""), set_name, "e.g. Saibaman", max_len=48)
        y += self.m_field_h + 8
        y += self._note(screen, x, y, w, f"ID: {self.selected_id}   (folder name under "
                        f"{KIND_SPRITE_ROOT_LABELS[self.kind]})")
        return y - y0

    # ══════════════════════════════════════════════════════════════
    #  Tab content: Enemy
    # ══════════════════════════════════════════════════════════════

    def _tab_enemy(self, screen, x, y, w) -> int:
        cfg = self.cfg
        stats = cfg["stats"]
        y0 = y
        y += self._identity_block(screen, x, y, w)
        y += 6

        y += self._caption(screen, "Stats", x, y, w)
        specs = [
            dict(key="e:max_hp", label="Max HP", vmin=1, vmax=2000, step=5, fmt="{:.0f}",
                 get=lambda: stats["max_hp"], set=self._num_setter(stats, "max_hp", "int")),
            dict(key="e:strength", label="STR (melee)", vmin=1, vmax=200, step=1, fmt="{:.0f}",
                 get=lambda: stats["strength"], set=self._num_setter(stats, "strength", "int")),
            dict(key="e:power", label="POW (super)", vmin=1, vmax=200, step=1, fmt="{:.0f}",
                 get=lambda: stats["power"], set=self._num_setter(stats, "power", "int")),
            dict(key="e:defense", label="END (defense)", vmin=0, vmax=200, step=1, fmt="{:.0f}",
                 get=lambda: stats["defense"], set=self._num_setter(stats, "defense", "int")),
            dict(key="e:speed", label="SPD (speed)", vmin=0.2, vmax=5.0, step=0.1, fmt="{:.1f}",
                 get=lambda: stats["speed"], set=self._num_setter(stats, "speed", "f1")),
        ]
        y += self._slider_grid(screen, x, y, w, specs)

        y += 4
        y += self._caption(screen, "Progression", x, y, w)
        specs = [
            dict(key="e:level", label="Level", vmin=1, vmax=99, step=1, fmt="{:.0f}",
                 get=lambda: cfg["level"], set=self._num_setter(cfg, "level", "int")),
            dict(key="e:xp", label="XP Reward", vmin=0, vmax=2000, step=5, fmt="{:.0f}",
                 get=lambda: cfg["xp_reward"], set=self._num_setter(cfg, "xp_reward", "int")),
        ]
        y += self._slider_grid(screen, x, y, w, specs)

        y += 4
        y += self._caption(screen, "Sprite", x, y, w)
        specs = [
            dict(key="e:width", label="Sprite Width", vmin=4, vmax=256, step=1, fmt="{:.0f}",
                 get=lambda: cfg.get("width", 32), set=self._num_setter(cfg, "width", "int")),
            dict(key="e:height", label="Sprite Height", vmin=4, vmax=256, step=1, fmt="{:.0f}",
                 get=lambda: cfg.get("height", 32), set=self._num_setter(cfg, "height", "int")),
            dict(key="e:shadow", label="Shadow Size", vmin=8, vmax=96, step=4, fmt="{:.0f}",
                 get=lambda: cfg.get("shadow_width", 32), set=self._num_setter(cfg, "shadow_width", "int")),
        ]
        y += self._slider_grid(screen, x, y, w, specs)

        y += 10
        y += self._caption(screen, "Classification", x, y, w)

        def set_entity_type(v):
            if cfg.get("entity_type") != v:
                cfg["entity_type"] = v
                self._mark_dirty()
                self._load_preview()   # boss vs regular reads a different sprite folder

        def set_category(v):
            if cfg.get("enemy_category") != v:
                cfg["enemy_category"] = v
                self._mark_dirty()

        def set_ai(v):
            if cfg.get("ai_type") != v:
                cfg["ai_type"] = v
                self._mark_dirty()

        def set_shooter_style(v):
            if cfg.get("shooter_style") != v:
                cfg["shooter_style"] = v
                self._mark_dirty()

        def set_zeni(v):
            if cfg.get("zeni_pool") != v:
                cfg["zeni_pool"] = v
                self._mark_dirty()

        y += self._segmented_row(screen, "e:type", x, y, min(w, 420), "Type",
                                  [("enemy", "Enemy"), ("boss", "Boss")], cfg.get("entity_type"), set_entity_type)
        y += 12
        y += self._segmented_row(screen, "e:cat", x, y, min(w, 420), "Category",
                                  [("melee", "Melee"), ("shooter", "Shooter")],
                                  cfg.get("enemy_category"), set_category)
        y += 12
        y += self._segmented_row(screen, "e:ai", x, y, min(w, 420), "AI",
                                  [("easy", "Easy"), ("advanced", "Advanced")], cfg.get("ai_type"), set_ai)
        y += 12
        if cfg.get("enemy_category") == "shooter":
            y += self._segmented_row(screen, "e:shooter", x, y, w, "Shooter Style",
                                      [("bomb", "Bomb"), ("bullet", "Bullet"), ("rocket", "Rocket"),
                                       ("kiblast", "Ki Blast")], cfg.get("shooter_style"), set_shooter_style)
            y += 12
        y += self._segmented_row(screen, "e:zeni", x, y, w, "Zeni Pool",
                                  [("tier1", "Tier 1"), ("tier2", "Tier 2"), ("tier3", "Tier 3"),
                                   ("tier4", "Tier 4")], cfg.get("zeni_pool"), set_zeni)

        y += 18
        desc = cfg.get("description") or ""
        y += self._caption(screen, "Description", x, y, w, right=f"{len(desc)}/600")

        def set_desc(v):
            if cfg.get("description") != v:
                cfg["description"] = v
                self._mark_dirty()

        self._text_field(screen, "e:desc", pygame.Rect(x, y, w, 110), lambda: cfg.get("description", ""),
                         set_desc, "Shown in the scouter data panel...", multiline=True, max_len=600)
        y += 110
        return y - y0

    # ══════════════════════════════════════════════════════════════
    #  Tab content: NPC
    # ══════════════════════════════════════════════════════════════

    def _tab_npc(self, screen, x, y, w) -> int:
        cfg = self.cfg
        y0 = y
        y += self._identity_block(screen, x, y, w)
        y += 6

        y += self._caption(screen, "Movement", x, y, w)
        specs = [
            dict(key="n:speed", label="Speed", vmin=0.2, vmax=5.0, step=0.1, fmt="{:.1f}",
                 get=lambda: cfg["speed"], set=self._num_setter(cfg, "speed", "f1")),
            dict(key="n:range", label="Interact Range", vmin=20, vmax=150, step=5, fmt="{:.0f}",
                 get=lambda: cfg["interaction_range"], set=self._num_setter(cfg, "interaction_range", "int")),
        ]
        y += self._slider_grid(screen, x, y, w, specs)

        y += 4
        y += self._caption(screen, "Sprite", x, y, w)
        specs = [
            dict(key="n:width", label="Sprite Width", vmin=4, vmax=256, step=1, fmt="{:.0f}",
                 get=lambda: cfg.get("width", 32), set=self._num_setter(cfg, "width", "int")),
            dict(key="n:height", label="Sprite Height", vmin=4, vmax=256, step=1, fmt="{:.0f}",
                 get=lambda: cfg.get("height", 32), set=self._num_setter(cfg, "height", "int")),
            dict(key="n:shadow", label="Shadow Size", vmin=8, vmax=96, step=4, fmt="{:.0f}",
                 get=lambda: cfg.get("shadow_width", 32), set=self._num_setter(cfg, "shadow_width", "int")),
        ]
        y += self._slider_grid(screen, x, y, w, specs)

        y += 10
        y += self._caption(screen, "Behavior", x, y, w)

        def set_npc_type(v):
            if cfg.get("npc_type") != v:
                cfg["npc_type"] = v
                self._mark_dirty()

        y += self._segmented_row(screen, "n:type", x, y, min(w, 420), "Movement Type",
                                  [("static", "Static"), ("moving", "Moving")], cfg.get("npc_type"), set_npc_type)

        y += 18
        y += self._caption(screen, "Dialogue", x, y, w)
        dlg = cfg["dialogue"]

        def get_first_line():
            return dlg["dialogues"][0] if dlg["dialogues"] else ""

        def set_first_line(v):
            if dlg["dialogues"]:
                if dlg["dialogues"][0] == v:
                    return
                dlg["dialogues"][0] = v
            else:
                dlg["dialogues"] = [v]
            self._mark_dirty()

        y += self._field_label(screen, "First Line", x, y)
        self._text_field(screen, "n:line0", pygame.Rect(x, y, w, self.m_field_h), get_first_line, set_first_line,
                         "Hello, traveler!", max_len=200)
        y += self.m_field_h + 12

        def set_after_limit(v):
            if dlg.get("after_limit_text") != v:
                dlg["after_limit_text"] = v
                self._mark_dirty()

        y += self._field_label(screen, "After Limit", x, y)
        self._text_field(screen, "n:after", pygame.Rect(x, y, w, self.m_field_h),
                         lambda: dlg.get("after_limit_text", ""), set_after_limit,
                         "I have nothing more to say.", max_len=200)
        y += self.m_field_h + 8
        y += self._note(screen, x, y, w, "Full multi-line dialogue trees are still authored per-placement in the "
                        "event/trigger editor - this sets the NPC's default first line and after-limit text, used "
                        "when it's dropped fresh in a room.")

        y += 18
        desc = cfg.get("description") or ""
        y += self._caption(screen, "Description", x, y, w, right=f"{len(desc)}/600")

        def set_desc(v):
            if cfg.get("description") != v:
                cfg["description"] = v
                self._mark_dirty()

        self._text_field(screen, "n:desc", pygame.Rect(x, y, w, 110), lambda: cfg.get("description", ""),
                         set_desc, "Shown in the scouter data panel...", multiline=True, max_len=600)
        y += 110
        return y - y0

    # ══════════════════════════════════════════════════════════════
    #  Tab content: Critter
    # ══════════════════════════════════════════════════════════════

    def _tab_critter(self, screen, x, y, w) -> int:
        cfg = self.cfg
        y0 = y
        y += self._identity_block(screen, x, y, w)
        y += 6

        y += self._caption(screen, "Sprite", x, y, w)
        specs = [
            dict(key="c:width", label="Width", vmin=4, vmax=128, step=1, fmt="{:.0f}",
                 get=lambda: cfg.get("width", 16), set=self._num_setter(cfg, "width", "int")),
            dict(key="c:height", label="Height", vmin=4, vmax=128, step=1, fmt="{:.0f}",
                 get=lambda: cfg.get("height", 16), set=self._num_setter(cfg, "height", "int")),
            dict(key="c:wander", label="Wander Radius", vmin=8, vmax=256, step=4, fmt="{:.0f}",
                 get=lambda: cfg.get("wander_radius", 32), set=self._num_setter(cfg, "wander_radius", "int")),
        ]
        y += self._slider_grid(screen, x, y, w, specs)

        y += 10
        y += self._caption(screen, "Behavior", x, y, w)

        def set_critter_type(v):
            if cfg.get("critter_type") != v:
                cfg["critter_type"] = v
                self._mark_dirty()

        options = [(opt, opt.title()) for opt in CRITTER_PROFILE_OPTIONS]
        y += self._segmented_row(screen, "c:profile", x, y, min(w, 420), "Behavior Profile",
                                  options, cfg.get("critter_type"), set_critter_type)
        y += 12
        y += self._note(screen, x, y, w, "Behavior Profile picks this id's pacing/animation from "
                        "Critter.BEHAVIOR_PROFILES (speed, rest/move timings, flutter, jitter) - reuse an existing "
                        "profile, or wire up a new one in critter.py first. Position, variant, and spawn point are "
                        "still set per-placement in the room editor.")

        y += 18
        desc = cfg.get("description") or ""
        y += self._caption(screen, "Description", x, y, w, right=f"{len(desc)}/600")

        def set_desc(v):
            if cfg.get("description") != v:
                cfg["description"] = v
                self._mark_dirty()

        self._text_field(screen, "c:desc", pygame.Rect(x, y, w, 110), lambda: cfg.get("description", ""),
                         set_desc, "Shown in the scouter data panel...", multiline=True, max_len=600)
        y += 110
        return y - y0

    # ══════════════════════════════════════════════════════════════
    #  Dialogs
    # ══════════════════════════════════════════════════════════════

    def _draw_dialog(self, screen) -> None:
        d = self.dialog
        r = self._dialog_rects()
        uk.draw_rect_on(screen, (0, 0, 0, 170), pygame.Rect(0, 0, self.screen_width, self.screen_height), 0, 0)
        accent = _T.DANGER_BRIGHT if d.get("danger") else _T.GOLD
        pn = r["panel"]
        uk.draw_panel(screen, pn, bg=_T.PANEL_BG, border=accent, border_width=2, radius=14)
        self._text_top(screen, self.f_lg, d["title"], _T.TEXT_PRIMARY, pn.x + r["pad"], pn.y + r["pad"])
        for i, line in enumerate(r["lines"]):
            self._text_top(screen, self.f_md, line, _T.TEXT_SECONDARY, pn.x + r["pad"], r["msg_y"] + i * r["line_h"])

        if d["kind"] == "input":
            edit = d["edit"]
            f = r["field"]
            self._panel(screen, f, _FIELD_HI, _T.GOLD, 2)
            md = self.f_md
            inner = pygame.Rect(f.x + 14, f.y + 2, f.w - 28, f.h - 4)
            cw = md.width(edit.value[:edit.cursor])
            sc = d["scroll"]
            if md.width(edit.value) <= inner.w - 2:
                sc = 0
            else:
                if cw - sc > inner.w - 2:
                    sc = cw - inner.w + 2
                if cw < sc:
                    sc = cw
            d["scroll"] = sc
            old = self._push_clip(screen, inner)
            if edit.has_sel():
                s, e = edit.sel_range()
                sx, ex = inner.x - sc + md.width(edit.value[:s]), inner.x - sc + md.width(edit.value[:e])
                uk.draw_rect_on(screen, (*_T.GOLD, 70),
                                pygame.Rect(sx, f.centery - md.cap_h // 2 - 4, ex - sx, md.line_h + 8), 0, 3)
            if edit.value:
                self._text_mid(screen, md, edit.value, _T.TEXT_PRIMARY, inner.x - sc, f.centery, dyn=True)
            else:
                self._text_mid(screen, md, KIND_ID_HINTS[self.kind], _T.TEXT_DIM, inner.x, f.centery)
            if int(edit.blink * 2) % 2 == 0:
                cx = inner.x - sc + cw
                uk.draw_rect_on(screen, _T.GOLD_BRIGHT,
                                pygame.Rect(cx, f.centery - md.cap_h // 2 - 4, 2, md.line_h + 8), 0, 0)
            self._pop_clip(screen, old)
            self._text_rects.append(f)
            if d["error"]:
                self._text_top(screen, self.f_sm, d["error"], _T.DANGER_BRIGHT, f.x, r["hint_y"])
            else:
                self._text_top(screen, self.f_sm, "Lowercase, spaces become underscores.  Enter to confirm.",
                               _T.TEXT_DIM, f.x, r["hint_y"])

        for key, rect, label, acc, icon in (
                ("d_ok", r["ok"], d["confirm_label"], accent, _ic_check if d["kind"] == "input" else _ic_trash),
                ("d_cancel", r["cancel"], "Cancel", _T.TEXT_SECONDARY, None)):
            hov = rect.collidepoint(self._mouse)
            t = self._anim(key, hov)
            self._panel(screen, rect, uk.lerp_color(_CARD, _CARD_HI, t),
                        uk.lerp_color(_T.CARD_BORDER, acc, 0.55 + 0.45 * t))
            fg = uk.lerp_color(_T.TEXT_SECONDARY, acc, 0.55 + 0.45 * t)
            ic = 20 if icon else 0
            tw = self.f_md.width(label)
            x = rect.centerx - (tw + (ic + 10 if icon else 0)) // 2
            if icon:
                icon(screen, pygame.Rect(x, rect.centery - 10, 20, 20), fg, 3)
                x += 30
            self._text_mid(screen, self.f_md, label, fg, x, rect.centery)